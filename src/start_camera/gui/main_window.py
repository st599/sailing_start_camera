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
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QObject, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QImage,
    QKeySequence,
    QPainter,
    QPaintEvent,
)
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
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
from start_camera.config import Config, ConfigError
from start_camera.gui.line_editor import LineEditorDialog
from start_camera.gui.sequence_editor import SequenceEditorDialog
from start_camera.gui.session import GuiSession
from start_camera.gui.settings_dialog import SettingsDialog
from start_camera.gui.settings_model import ConfigDocument
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
FILE_FILTER = "Configuration (*.json);;All files (*)"
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

    def __init__(
        self,
        session: GuiSession,
        document: ConfigDocument | None = None,
        *,
        make_session: Callable[[Config], GuiSession] | None = None,
    ) -> None:
        """Build the window.

        Args:
            session: What the window shows and controls.
            document: The configuration file being edited.
            make_session: Makes a new session when the configuration changes.
        """
        super().__init__()
        self._session = session
        self._document = document or ConfigDocument(session.config)
        self._make_session = make_session or (
            lambda config: GuiSession(config, self._session.secrets)
        )
        self._busy = False
        self._worker: threading.Thread | None = None
        self._log_handler = _WindowLogHandler()
        self._show_title()
        self.resize(1200, 720)
        self.setMinimumSize(QSize(900, 500))
        self.setStyleSheet(STYLE)
        self._build()
        self._build_menu()
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
        if not self._settle_unsaved_changes():
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
        self.recall_button = _button("Individual recall", self._toggle_recall)
        buttons = [
            _button("Start now", lambda: self._send_command("start_now")),
            _button(
                "Next start",
                lambda: self._send_confirmed("Skip the next start?", "skip"),
            ),
            _button("Postpone", lambda: self._send_command("postpone")),
            _button("Resume", lambda: self._send_command("resume")),
            self.recall_button,
            _button(
                "General recall",
                lambda: self._send_confirmed("General recall?", "general_recall"),
            ),
            _button(
                "Abandon",
                lambda: self._send_confirmed("Abandon the racing?", "abandon"),
            ),
        ]
        grid = QGridLayout()
        for index, button in enumerate(buttons):
            grid.addWidget(button, index // 2, index % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        return grid

    def _build_delay_buttons(self) -> QHBoxLayout:
        """Return the row for the delay buttons."""
        self._delay_row = QHBoxLayout()
        self._show_delay_buttons()
        return self._delay_row

    def _show_delay_buttons(self) -> None:
        """Show a button for each delay step in the configuration."""
        while (item := self._delay_row.takeAt(0)) is not None:
            item.widget().deleteLater()
        for minutes in self._session.config.control.delay_steps:
            self._delay_row.addWidget(
                _button(f"{minutes:+g} min", self._delay_action(minutes))
            )

    def _delay_action(self, minutes: float) -> Callable[[], None]:
        """Return what a delay button does."""
        return lambda: self._send(lambda: self._session.control.shift(minutes))

    def _build_menu(self) -> None:
        """Add the menu."""
        items = (
            ("&Open...", QKeySequence.StandardKey.Open, self._open_file),
            ("&Save", QKeySequence.StandardKey.Save, self._save_file),
            ("Save &As...", QKeySequence.StandardKey.SaveAs, self._save_file_as),
            ("Se&ttings...", None, self._open_settings),
            ("Start &line...", None, self._open_line_editor),
            ("Start se&quences...", None, self._open_sequence_editor),
        )
        menu = self.menuBar().addMenu("&File")
        for text, shortcut, action in items:
            entry = QAction(text, self)
            if shortcut is not None:
                entry.setShortcut(shortcut)
            entry.triggered.connect(lambda _checked=False, run=action: run())
            menu.addAction(entry)

    def _show_title(self) -> None:
        """Show the file's name, and whether it has unsaved changes."""
        self.setWindowTitle(f"{self._document.name} - Sailing start camera")

    def _config_can_change(self) -> bool:
        """Refuse to change the configuration while the stream depends on it."""
        if self._busy or self._session.is_streaming:
            self._show_problem("Stop the stream before changing the configuration.")
            return False
        return True

    def _open_settings(self) -> None:
        """Edit the settings, using the new ones straight away."""
        if not self._config_can_change():
            return
        dialog = self._settings_dialog()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if self._document.update(dialog.result_config):
            self._use_config(self._document.config)
        self._show_title()

    def _open_line_editor(self) -> None:
        """Edit the start line, using the new one straight away."""
        if not self._config_can_change():
            return
        dialog = self._line_editor()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        config = self._document.config.model_copy(
            update={"start_line": dialog.result_config}
        )
        if self._document.update(config):
            self._use_config(config)
        self._show_title()

    def _open_sequence_editor(self) -> None:
        """Edit the start sequences, using the new ones straight away."""
        if not self._config_can_change():
            return
        dialog = self._sequence_editor()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        config = self._document.config.model_copy(
            update={"sequences": dialog.result_config}
        )
        if self._document.update(config):
            self._use_config(config)
        self._show_title()

    def _sequence_editor(self) -> SequenceEditorDialog:
        """Return the sequence editor."""
        return SequenceEditorDialog(self._document.config.sequences, self)

    def _line_editor(self) -> LineEditorDialog:
        """Return the start line editor."""
        return LineEditorDialog(
            self._document.config.start_line, self._session.camera_frame, self
        )

    def _settings_dialog(self) -> SettingsDialog:
        """Return the settings form."""
        return SettingsDialog(self._document.config, self._session.secrets, self)

    def _open_file(self) -> None:
        """Open a configuration file."""
        if not self._config_can_change() or not self._settle_unsaved_changes():
            return
        name, _ = QFileDialog.getOpenFileName(
            self, "Open configuration", "", FILE_FILTER
        )
        if not name:
            return
        try:
            self._document = ConfigDocument.open(Path(name))
        except ConfigError as error:
            self._show_problem(str(error))
            return
        self._use_config(self._document.config)
        self._show_title()

    def _save_file(self) -> bool:
        """Save the configuration, and return whether it was saved."""
        if self._document.path is None:
            return self._save_file_as()
        return self._save_to(None)

    def _save_file_as(self) -> bool:
        """Save the configuration to a chosen file, and return whether it was saved."""
        name, _ = QFileDialog.getSaveFileName(
            self, "Save configuration", "config.json", FILE_FILTER
        )
        return self._save_to(Path(name)) if name else False

    def _save_to(self, path: Path | None) -> bool:
        """Save to a file, explaining any problem."""
        try:
            self._document.save(path)
        except ConfigError as error:
            self._show_problem(str(error))
            return False
        self._show_title()
        return True

    def _settle_unsaved_changes(self) -> bool:
        """Offer to save any changes, and return whether it's fine to carry on."""
        if not self._document.modified:
            return True
        answer = self._ask_to_save()
        if answer == QMessageBox.StandardButton.Save:
            return self._save_file()
        return answer == QMessageBox.StandardButton.Discard

    def _ask_to_save(self) -> QMessageBox.StandardButton:
        """Return what the user wants to do with unsaved changes."""
        return QMessageBox.question(
            self,
            "Unsaved changes",
            "The configuration has unsaved changes. Save them?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )

    def _show_problem(self, message: str) -> None:
        """Tell the user about a problem."""
        QMessageBox.warning(self, "Sailing start camera", message)

    def _use_config(self, config: Config) -> None:
        """Replace the session with one for a new configuration."""
        self._timer.stop()
        self._session.close()
        self._session = self._make_session(config)
        self._timer.setInterval(MILLISECONDS // config.stream.fps)
        self._show_delay_buttons()
        self.start()

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

    def _send_command(self, name: str) -> None:
        """Run a race command by name, on the current session's controls."""
        self._send(getattr(self._session.control, name))

    def _send_confirmed(self, question: str, name: str) -> None:
        """Run a disruptive race command once it's confirmed."""
        if self._confirm(question):
            self._send_command(name)

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
