#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: line_editor.py
#  Description: The start line editor.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: PySide6, numpy
#
#########################################################

"""The start line editor.

The preview is drawn by the same overlay that draws the stream's line, so what's shown
here is what viewers will see.
"""

#
# SYSTEM IMPORTS
#

from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray
from pydantic import ValidationError
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

#
# PROJECT IMPORTS
#
from start_camera.config import LineFit, LinePoint, StartLineConfig
from start_camera.gui.line_editor_model import LinePoints, ViewTransform
from start_camera.ingest import Frame
from start_camera.overlays.compositor import Compositor
from start_camera.overlays.start_line import StartLineOverlay

#
#  GLOBAL VARIABLES
#

HANDLE_RADIUS = 7
GRAB_DISTANCE = 12
ZOOM_STEP = 1.25
SIDE_PANEL_WIDTH = 260
HELP_TEXT = (
    "Click the two ends of the line, then any points between them. Drag a point to "
    "move it. Right-click a point, or select it and press Delete, to remove it. Scroll "
    "to zoom, and drag with the middle button to move around."
)
ERROR_STYLE = "color: #c00000; font-weight: bold;"

#
# FUNCTIONS
#


class LineCanvas(QWidget):
    """The frozen picture, with the points and the fitted line."""

    points_changed = Signal()

    def __init__(self, points: LinePoints) -> None:
        """Start with no picture."""
        super().__init__()
        self.points = points
        self.selected: int | None = None
        self.view: ViewTransform | None = None
        self._image: QImage | None = None
        self._frame: NDArray[np.uint8] | None = None
        self._dragging = False
        self._pan_from: QPointF | None = None
        self.setMinimumSize(400, 225)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def show_picture(self, frame: NDArray[np.uint8]) -> None:
        """Show a BGR picture, keeping the view if the picture's size hasn't changed."""
        self._frame = frame
        height, width = frame.shape[:2]
        self._image = QImage(
            frame.data, width, height, width * 3, QImage.Format.Format_BGR888
        )
        if self.view is None or (self.view.image_width, self.view.image_height) != (
            width,
            height,
        ):
            self.view = ViewTransform(self.width(), self.height(), width, height)
        self.update()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - the name Qt requires
        """Keep the view the same size as the widget."""
        if self.view is not None:
            self.view.view_width = self.width()
            self.view.view_height = self.height()
        super().resizeEvent(event)

    def zoom_by(self, factor: float) -> None:
        """Zoom around the middle of the view."""
        if self.view is not None:
            self.view.zoom_at(factor, self.width() / 2, self.height() / 2)
            self.update()

    def reset_view(self) -> None:
        """Show the whole picture."""
        if self.view is not None:
            self.view.reset()
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - the name Qt requires
        """Draw the picture and the points."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if self._image is not None and self.view is not None:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(QRectF(*self.view.picture_rect()), self._image)
            self._draw_points(painter)
        painter.end()

    def _draw_points(self, painter: QPainter) -> None:
        """Draw a numbered handle on each point."""
        for index, point in enumerate(self.points.points):
            x, y = self.view.to_view(point)
            colour = QColor("#ffd400") if index == self.selected else QColor("#ffffff")
            painter.setPen(QPen(QColor("#000000"), 2))
            painter.setBrush(colour)
            painter.drawEllipse(QPointF(x, y), HANDLE_RADIUS, HANDLE_RADIUS)
            label = QPointF(x + HANDLE_RADIUS + 3, y - HANDLE_RADIUS)
            painter.drawText(label, str(index + 1))

    def _point_at(self, x: float, y: float) -> int | None:
        """Return the point under a place in the view, the nearest if several are."""
        best = None
        best_distance = float(GRAB_DISTANCE)
        for index, point in enumerate(self.points.points):
            view_x, view_y = self.view.to_view(point)
            distance = np.hypot(view_x - x, view_y - y)
            if distance <= best_distance:
                best, best_distance = index, distance
        return best

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - the name Qt requires
        """Add, select, remove or start moving a point, or start moving the view."""
        if self.view is None:
            return
        self.setFocus()
        position = event.position()
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_from = position
            return
        index = self._point_at(position.x(), position.y())
        if event.button() == Qt.MouseButton.RightButton:
            if index is not None:
                self._remove(index)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if index is None:
            if not self.view.is_on_picture(position.x(), position.y()):
                return
            index = self.points.add(self.view.to_picture(position.x(), position.y()))
            self.points_changed.emit()
        self.selected = index
        self._dragging = True
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - the name Qt requires
        """Move the point or the view being dragged."""
        position = event.position()
        if self._pan_from is not None:
            self.view.pan_by_view_pixels(
                position.x() - self._pan_from.x(), position.y() - self._pan_from.y()
            )
            self._pan_from = position
            self.update()
        elif self._dragging and self.selected is not None:
            self.points.move(
                self.selected, self.view.to_picture(position.x(), position.y())
            )
            self.points_changed.emit()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - the name Qt requires
        """End a drag."""
        self._dragging = False
        self._pan_from = None

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - the name Qt requires
        """Zoom around the pointer."""
        if self.view is None:
            return
        factor = ZOOM_STEP if event.angleDelta().y() > 0 else 1 / ZOOM_STEP
        self.view.zoom_at(factor, event.position().x(), event.position().y())
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - the name Qt requires
        """Remove the selected point with Delete or Backspace."""
        keys = (Qt.Key.Key_Delete, Qt.Key.Key_Backspace)
        if event.key() in keys and self.selected is not None:
            self._remove(self.selected)
        else:
            super().keyPressEvent(event)

    def _remove(self, index: int) -> None:
        """Remove a point."""
        self.points.remove(index)
        self.selected = None
        self._dragging = False
        self.points_changed.emit()


class LineEditorDialog(QDialog):
    """Place the start line on a frozen picture from the camera."""

    def __init__(
        self,
        config: StartLineConfig,
        camera_frame: Callable[[], Frame],
        parent: QWidget | None = None,
    ) -> None:
        """Build the editor.

        Args:
            config: The line to start from, which isn't changed.
            camera_frame: Provides the camera's newest frame, without overlays.
            parent: The parent window.
        """
        super().__init__(parent)
        self._camera_frame = camera_frame
        self._frame: Frame | None = None
        self.points = LinePoints([(point.x, point.y) for point in config.points])
        self.result_config: StartLineConfig | None = None
        self.setWindowTitle("Start line")
        self.resize(1100, 700)
        self._build(config)
        self.new_picture()

    def _build(self, config: StartLineConfig) -> None:
        """Lay out the editor."""
        self.canvas = LineCanvas(self.points)
        self.canvas.points_changed.connect(self.refresh_preview)
        help_label = QLabel(HELP_TEXT)
        help_label.setWordWrap(True)
        self.signal_label = QLabel()
        self.error_label = QLabel()
        self.error_label.setStyleSheet(ERROR_STYLE)
        self.error_label.setWordWrap(True)
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept_line)
        buttons.rejected.connect(self.reject)
        # The controls go to the side, as a larger picture makes for more accurate clicks.
        side = QVBoxLayout()
        side.addLayout(self._build_settings(config))
        side.addLayout(self._build_tools())
        side.addWidget(self.error_label)
        panel = QWidget()
        panel.setLayout(side)
        panel.setFixedWidth(SIDE_PANEL_WIDTH)
        middle = QHBoxLayout()
        middle.addWidget(self.canvas, stretch=1)
        middle.addWidget(panel)
        layout = QVBoxLayout(self)
        layout.addWidget(help_label)
        layout.addLayout(middle, stretch=1)
        layout.addWidget(self.signal_label)
        layout.addWidget(buttons)

    def _build_settings(self, config: StartLineConfig) -> QFormLayout:
        """Return the line's settings."""
        self.enabled_box = QCheckBox()
        self.enabled_box.setChecked(config.enabled or not config.points)
        self.fit_box = QComboBox()
        self.fit_box.addItems([fit.value for fit in LineFit])
        # A new line goes through what's clicked, which is straight for just two points.
        fit = config.fit if config.points else LineFit.SPLINE
        self.fit_box.setCurrentText(fit.value)
        self.degree_box = QSpinBox()
        self.degree_box.setRange(2, 5)
        self.degree_box.setValue(config.polynomial_degree)
        self.width_box = QSpinBox()
        self.width_box.setRange(1, 20)
        self.width_box.setValue(config.width)
        self.opacity_box = QDoubleSpinBox()
        self.opacity_box.setRange(0.05, 1.0)
        self.opacity_box.setSingleStep(0.05)
        self.opacity_box.setValue(config.opacity)
        self.colour_edit = QLineEdit(config.colour)
        self.fit_box.currentTextChanged.connect(self._fit_changed)
        self.degree_box.valueChanged.connect(self.refresh_preview)
        self.width_box.valueChanged.connect(self.refresh_preview)
        self.opacity_box.valueChanged.connect(self.refresh_preview)
        self.colour_edit.textChanged.connect(self.refresh_preview)
        form = QFormLayout()
        form.addRow("Show the line", self.enabled_box)
        form.addRow("Fit", self.fit_box)
        form.addRow("Polynomial degree", self.degree_box)
        form.addRow("Width", self.width_box)
        form.addRow("Opacity", self.opacity_box)
        form.addRow("Colour", self.colour_edit)
        self.degree_box.setEnabled(fit is LineFit.POLYNOMIAL)
        return form

    def _build_tools(self) -> QVBoxLayout:
        """Return the buttons for the picture and the points."""
        layout = QVBoxLayout()
        for text, action in (
            ("New picture", self.new_picture),
            ("Zoom in", lambda: self.canvas.zoom_by(ZOOM_STEP)),
            ("Zoom out", lambda: self.canvas.zoom_by(1 / ZOOM_STEP)),
            ("Whole picture", self.canvas.reset_view),
            ("Clear points", self._clear),
        ):
            button = QPushButton(text)
            # Enter in a field mustn't press these instead of OK.
            button.setAutoDefault(False)
            button.clicked.connect(lambda _checked=False, run=action: run())
            layout.addWidget(button)
        layout.addStretch(1)
        return layout

    def new_picture(self) -> None:
        """Freeze a new picture from the camera."""
        self._frame = self._camera_frame()
        live = self._frame.live
        message = "" if live else "No picture from the camera at the moment."
        self.signal_label.setText(message)
        self.refresh_preview()

    def _fit_changed(self) -> None:
        """Enable the degree only where it applies, and show the new fit."""
        is_polynomial = self.fit_box.currentText() == LineFit.POLYNOMIAL.value
        self.degree_box.setEnabled(is_polynomial)
        self.refresh_preview()

    def _clear(self) -> None:
        """Remove all the points."""
        self.points.clear()
        self.canvas.selected = None
        self.refresh_preview()

    def _settings(self, *, enabled: bool) -> StartLineConfig:
        """Return the line as it's set.

        Raises:
            ValidationError: If the settings aren't valid.
        """
        return StartLineConfig(
            enabled=enabled,
            points=[LinePoint(x=x, y=y) for x, y in self.points.points],
            fit=LineFit(self.fit_box.currentText()),
            polynomial_degree=self.degree_box.value(),
            colour=self.colour_edit.text().strip(),
            width=self.width_box.value(),
            opacity=self.opacity_box.value(),
        )

    def refresh_preview(self) -> None:
        """Draw the line on the frozen picture, as the stream would."""
        try:
            line = self._settings(enabled=False)
        except ValidationError:
            self.error_label.setText("The colour must look like #ff0000")
            return
        self.error_label.setText("")
        compositor = Compositor([StartLineOverlay(line)])
        self.canvas.show_picture(compositor.render(self._frame))

    def accept_line(self) -> None:
        """Close the editor if the line is valid, otherwise explain what's wrong."""
        try:
            self.result_config = self._settings(enabled=self.enabled_box.isChecked())
        except ValidationError as error:
            messages = [
                item["msg"].removeprefix("Value error, ").removeprefix("points: ")
                for item in error.errors(include_input=False, include_url=False)
            ]
            self.error_label.setText("\n".join(messages))
            return
        self.accept()
