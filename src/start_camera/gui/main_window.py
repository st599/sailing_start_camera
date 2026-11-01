#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: main_window.py
#  Description: The GUI's main window.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: PySide6, numpy
#
#########################################################

"""The GUI's main window: the video, the race controls and the stream."""

#
# SYSTEM IMPORTS
#

import logging
import threading
from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QObject, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QImage, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

#
# PROJECT IMPORTS
#
from start_camera.gui.session import GuiSession
from start_camera.media_server import MediaServerError
from start_camera.race_clock import INDIVIDUAL_RECALL_FLAG, RaceClockError

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

PANEL_WIDTH = 340
BUTTON_HEIGHT = 44
MAX_LOG_LINES = 500
MILLISECONDS = 1000
STYLE = """
#time { font-size: 28px; font-weight: bold; padding: 8px;
  border: 2px solid palette(text); border-radius: 6px; }
#flags { font-weight: bold; }
#message { color: #c00000; font-weight: bold; }
QPushButton { font-weight: bold; }
"""

#
# FUNCTIONS
#


class VideoView(QWidget):
    """Show frames, scaled to fit while keeping their shape."""

    def __init__(self) -> None:
        """Start with nothing to show."""
        super().__init__()
        self._image: QImage | None = None
        self._frame: NDArray[np.uint8] | None = None
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def show_frame(self, frame: NDArray[np.uint8]) -> None:
        """Show a BGR frame."""
        # The image uses the frame's memory, so keep the frame while it's on show.
        self._frame = frame
        height, width = frame.shape[:2]
        self._image = QImage(
            frame.data, width, height, width * 3, QImage.Format.Format_BGR888
        )
        self.update()

    def picture_area(self) -> QRect:
        """Return where the picture goes, in the middle of the widget."""
        if self._image is None:
            return QRect()
        size = self._image.size().scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio
        )
        left = (self.width() - size.width()) // 2
        top = (self.height() - size.height()) // 2
        return QRect(QPoint(left, top), size)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - the name Qt requires
        """Draw the picture on a black background."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if self._image is not None:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(self.picture_area(), self._image)
        painter.end()


class _LogSignal(QObject):
    """Carry log messages safely to the GUI thread."""

    message = Signal(str)


class _WindowLogHandler(logging.Handler):
    """Pass log messages to the window, from any thread."""

    def __init__(self) -> None:
        """Set up the signal and format."""
        super().__init__(logging.INFO)
        self.signals = _LogSignal()
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        """Send a record to the window."""
        self.signals.message.emit(self.format(record))


class MainWindow(QMainWindow):
    """The main window."""

    _stream_finished = Signal(str)

    def __init__(self, session: GuiSession) -> None:
        """Build the window.

        Args:
            session: What the window shows and controls.
        """
        super().__init__()
        self._session = session
        self._busy = False
        self._worker: threading.Thread | None = None
        self._log_handler = _WindowLogHandler()
        self.setWindowTitle("Sailing start camera")
        self.resize(1200, 720)
        self.setMinimumSize(QSize(900, 500))
        self.setStyleSheet(STYLE)
        self._build()
        self._log_handler.signals.message.connect(self._add_log_line)
        logging.getLogger().addHandler(self._log_handler)
        self._stream_finished.connect(self._on_stream_finished)
        self._timer = QTimer(self)
        self._timer.setInterval(MILLISECONDS // session.config.stream.fps)
        self._timer.timeout.connect(self.refresh)

    def start(self) -> None:
        """Start the camera and the display."""
        self._session.start()
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        """Update the picture and the status."""
        self.video.show_frame(self._session.preview())
        summary = self._session.summary()
        self.time_label.setText(summary.display or "-")
        flags = ", ".join(summary.flags)
        self.flags_label.setText(f"Flags: {flags}" if flags else "No flags")
        recall_flying = INDIVIDUAL_RECALL_FLAG in summary.flags
        self.recall_button.setText("Clear X" if recall_flying else "Individual recall")
        self.camera_label.setText(
            "Camera: live" if self._session.camera_is_live() else "Camera: no signal"
        )
        self._show_stream_state()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - the name Qt requires
        """Stop everything, checking first if that would end a running stream."""
        question = "This will stop the stream. Quit?"
        if self._session.is_streaming and not self._confirm(question):
            event.ignore()
            return
        self._timer.stop()
        logging.getLogger().removeHandler(self._log_handler)
        if self._worker is not None:
            self._worker.join()
        self._session.close()
        event.accept()

    def _build(self) -> None:
        """Lay out the window."""
        self.video = VideoView()
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(MAX_LOG_LINES)
        self.log_view.setFixedHeight(90)
        left = QVBoxLayout()
        left.addWidget(self.video, stretch=1)
        left.addWidget(self.log_view)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(PANEL_WIDTH)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self._build_panel())
        layout = QHBoxLayout()
        layout.addLayout(left, stretch=1)
        layout.addWidget(scroll)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

    def _build_panel(self) -> QWidget:
        """Return the controls."""
        self.time_label = _label("time", center=True)
        self.flags_label = _label("flags", center=True)
        self.message_label = _label("message", center=True)
        layout = QVBoxLayout()
        for label in (self.time_label, self.flags_label, self.message_label):
            layout.addWidget(label)
        layout.addLayout(self._build_race_buttons())
        layout.addLayout(self._build_delay_buttons())
        layout.addLayout(self._build_stream_controls())
        layout.addStretch(1)
        panel = QWidget()
        panel.setLayout(layout)
        return panel

    def _build_race_buttons(self) -> QGridLayout:
        """Return the buttons for the race commands."""
        control = self._session.control
        self.recall_button = _button("Individual recall", self._toggle_recall)
        buttons = [
            _button("Start now", lambda: self._send(control.start_now)),
            _button(
                "Next start",
                lambda: self._send_confirmed("Skip the next start?", control.skip),
            ),
            _button("Postpone", lambda: self._send(control.postpone)),
            _button("Resume", lambda: self._send(control.resume)),
            self.recall_button,
            _button(
                "General recall",
                lambda: self._send_confirmed(
                    "General recall?", control.general_recall
                ),
            ),
            _button(
                "Abandon",
                lambda: self._send_confirmed("Abandon the racing?", control.abandon),
            ),
        ]
        grid = QGridLayout()
        for index, button in enumerate(buttons):
            grid.addWidget(button, index // 2, index % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        return grid

    def _build_delay_buttons(self) -> QHBoxLayout:
        """Return a button for each delay step."""
        row = QHBoxLayout()
        for minutes in self._session.config.control.delay_steps:
            row.addWidget(
                _button(f"{minutes:+g} min", self._delay_action(minutes))
            )
        return row

    def _delay_action(self, minutes: float) -> Callable[[], None]:
        """Return what a delay button does."""
        return lambda: self._send(lambda: self._session.control.shift(minutes))

    def _build_stream_controls(self) -> QVBoxLayout:
        """Return the stream's button and status."""
        self.stream_button = _button("Start streaming", self._toggle_stream)
        self.camera_label = _label("camera")
        self.stream_label = _label("stream")
        self.addresses_label = _label("addresses")
        self.addresses_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout = QVBoxLayout()
        for widget in (
            self.stream_button,
            self.camera_label,
            self.stream_label,
            self.addresses_label,
        ):
            layout.addWidget(widget)
        return layout

    def _confirm(self, question: str) -> bool:
        """Return whether the user agrees."""
        reply = QMessageBox.question(self, "Please confirm", question)
        return reply == QMessageBox.StandardButton.Yes

    def _send(self, command: Callable[[], None]) -> None:
        """Run a race command, showing the reason if it's refused."""
        self.message_label.setText("")
        try:
            command()
        except RaceClockError as error:
            self.message_label.setText(str(error))
        self.refresh()

    def _send_confirmed(self, question: str, command: Callable[[], None]) -> None:
        """Run a disruptive race command once it's confirmed."""
        if self._confirm(question):
            self._send(command)

    def _toggle_recall(self) -> None:
        """Raise the individual recall flag, or take it down if it's flying."""
        control = self._session.control
        if INDIVIDUAL_RECALL_FLAG in self._session.summary().flags:
            self._send(control.clear_individual_recall)
        else:
            self._send(control.individual_recall)

    def _toggle_stream(self) -> None:
        """Start or stop the stream in the background, as this can take a while."""
        if self._busy:
            return
        stopping = self._session.is_streaming
        if stopping and not self._confirm("Stop the stream for all viewers?"):
            return
        self._busy = True
        self._show_stream_state()
        work = self._session.stop_stream if stopping else self._session.start_stream
        self._worker = threading.Thread(
            target=self._run_stream_work, args=(work,), daemon=True, name="gui-stream"
        )
        self._worker.start()

    def _run_stream_work(self, work: Callable[[], None]) -> None:
        """Start or stop the stream, and report back to the GUI thread."""
        error = ""
        try:
            work()
        except (MediaServerError, OSError) as problem:
            logger.error("Can't start the stream: %s", problem)
            error = str(problem)
        self._stream_finished.emit(error)

    def _on_stream_finished(self, error: str) -> None:
        """Show the result of starting or stopping the stream."""
        self._busy = False
        self.message_label.setText(error)
        self._show_stream_state()

    def _show_stream_state(self) -> None:
        """Update the stream's button and status."""
        streaming = self._session.is_streaming
        self.stream_button.setEnabled(not self._busy)
        self.stream_button.setText("Stop streaming" if streaming else "Start streaming")
        if self._busy:
            status = "Stream: please wait..."
        else:
            status = "Stream: ON" if streaming else "Stream: off"
        self.stream_label.setText(status)
        addresses = ""
        if streaming:
            addresses = "\n".join(
                f"{name}:\n{url}"
                for name, url in self._session.viewer_addresses().items()
            )
        self.addresses_label.setText(addresses)

    def _add_log_line(self, line: str) -> None:
        """Add a line to the log view."""
        self.log_view.appendPlainText(line)


def _label(name: str, *, center: bool = False) -> QLabel:
    """Return a named label that wraps its text."""
    label = QLabel()
    label.setObjectName(name)
    label.setWordWrap(True)
    if center:
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


def _button(text: str, action: Callable[[], None]) -> QPushButton:
    """Return a button that does something when pressed."""
    button = QPushButton(text)
    button.setMinimumHeight(BUTTON_HEIGHT)
    # Share the panel's width, rather than insisting on the width of the text.
    button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    button.clicked.connect(lambda _checked=False: action())
    return button
