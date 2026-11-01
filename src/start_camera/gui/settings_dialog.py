#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: settings_dialog.py
#  Description: The settings form, including the camera scan.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: PySide6
#
#########################################################

"""The settings form, including the camera scan."""

#
# SYSTEM IMPORTS
#

import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

#
# PROJECT IMPORTS
#
from start_camera.config import Config, Secrets
from start_camera.discovery import (
    Candidate,
    DiscoveryError,
    StreamStatus,
    discover,
    select_camera,
)
from start_camera.gui.settings_model import (
    FIELDS,
    GENERAL_ERRORS,
    Field,
    FieldKind,
    apply_fields,
    read_fields,
)

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

ERROR_STYLE = "color: #c00000; font-weight: bold;"
HOST_FIELD = "camera.host"
MAC_FIELD = "camera.mac"

#
# FUNCTIONS
#


def describe_candidate(candidate: Candidate) -> str:
    """Return a line describing a device found by a scan."""
    identity = candidate.model or "unknown model"
    status = candidate.status.value
    camera = "camera" if candidate.identified else "not identified"
    return f"{candidate.host} - {identity} - {camera} - {status}"


def order_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Return the devices with the likeliest camera first."""

    def rank(item: Candidate) -> tuple[bool, bool]:
        readable = item.status is StreamStatus.READABLE
        return (not item.identified, not readable)

    return sorted(candidates, key=rank)


class SettingsDialog(QDialog):
    """Edit the camera, stream and overlay settings."""

    _scan_finished = Signal(list, str)

    def __init__(
        self,
        config: Config,
        secrets: Secrets,
        parent: QWidget | None = None,
        *,
        scan: Callable[[Config, Secrets], list[Candidate]] = discover,
    ) -> None:
        """Build the form.

        Args:
            config: The configuration to start from, which isn't changed.
            secrets: The credentials, used only to test cameras found by a scan.
            parent: The parent window.
            scan: Finds the devices on the network.
        """
        super().__init__(parent)
        self._config = config
        self._secrets = secrets
        self._scan = scan
        self._editors: dict[str, QWidget] = {}
        self._errors: dict[str, QLabel] = {}
        self._candidates: list[Candidate] = []
        self._tab_order: list[str] = []
        self.result_config: Config | None = None
        self.setWindowTitle("Settings")
        self.resize(560, 520)
        self._build()
        self._show_values(read_fields(config))
        self._scan_finished.connect(self._show_scan)

    def _build(self) -> None:
        """Lay out the form."""
        self._tabs = QTabWidget()
        forms: dict[str, QFormLayout] = {}
        for field in FIELDS:
            if field.tab not in forms:
                forms[field.tab] = QFormLayout()
                self._tabs.addTab(_scrollable(forms[field.tab]), field.tab)
                self._tab_order.append(field.tab)
            self._add_field(forms[field.tab], field)
        self._add_scan(forms["Camera"])
        self.general_error = _error_label()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept_values)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs)
        layout.addWidget(self.general_error)
        layout.addWidget(buttons)

    def _add_field(self, form: QFormLayout, field: Field) -> None:
        """Add a field, with room for its error directly beneath it."""
        editor = _make_editor(field)
        error = _error_label()
        self._editors[field.path] = editor
        self._errors[field.path] = error
        form.addRow(field.label, editor)
        form.addRow("", error)

    def _add_scan(self, form: QFormLayout) -> None:
        """Add the scan's button and results to the camera's form."""
        self.scan_button = QPushButton("Scan network")
        self.scan_button.clicked.connect(lambda _checked=False: self._start_scan())
        self.scan_status = QLabel()
        self.scan_status.setWordWrap(True)
        self.scan_results = QListWidget()
        self.scan_results.setMaximumHeight(110)
        self.use_button = QPushButton("Use selected camera")
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(lambda _checked=False: self.use_selected())
        for widget in (
            self.scan_button,
            self.scan_status,
            self.scan_results,
            self.use_button,
        ):
            form.addRow(widget)

    def values(self) -> dict[str, str | bool]:
        """Return what's on the form."""
        return {path: _read_editor(editor) for path, editor in self._editors.items()}

    def _show_values(self, values: dict[str, str | bool]) -> None:
        """Put values on the form."""
        for path, value in values.items():
            _write_editor(self._editors[path], value)

    def set_value(self, path: str, value: str | bool) -> None:
        """Change one value on the form."""
        _write_editor(self._editors[path], value)

    def error_for(self, path: str) -> str:
        """Return the error showing next to a field."""
        return "" if self._errors[path].isHidden() else self._errors[path].text()

    def accept_values(self) -> None:
        """Close the form if its values are valid, otherwise explain what's wrong."""
        config, errors = apply_fields(self._config, self.values())
        for path, label in self._errors.items():
            _show_error(label, errors.get(path, ""))
        _show_error(self.general_error, errors.get(GENERAL_ERRORS, ""))
        if config is not None:
            self.result_config = config
            self.accept()
        else:
            self._show_first_problem(errors)

    def _show_first_problem(self, errors: dict[str, str]) -> None:
        """Switch to the first tab with an error, so that the error can be seen."""
        for field in FIELDS:
            if field.path in errors:
                self._tabs.setCurrentIndex(self._tab_order.index(field.tab))
                return

    def _start_scan(self) -> None:
        """Scan in the background, as it takes several seconds."""
        self.scan_button.setEnabled(False)
        self.use_button.setEnabled(False)
        self.scan_results.clear()
        self.scan_status.setText("Scanning...")
        config, _ = apply_fields(self._config, self.values())
        threading.Thread(
            target=self._run_scan,
            args=(config or self._config,),
            daemon=True,
            name="gui-scan",
        ).start()

    def _run_scan(self, config: Config) -> None:
        """Find the devices, and report back to the GUI thread."""
        try:
            found, problem = self._scan(config, self._secrets), ""
        except (DiscoveryError, OSError, ValueError) as error:
            logger.warning("The scan failed: %s", error)
            found, problem = [], str(error)
        try:
            self._scan_finished.emit(found, problem)
        except RuntimeError:
            logger.debug("The settings window closed during the scan")

    def _show_scan(self, found: list[Candidate], problem: str) -> None:
        """Show what the scan found, selecting the likeliest camera."""
        self.scan_button.setEnabled(True)
        self._candidates = order_candidates(found)
        for candidate in self._candidates:
            self.scan_results.addItem(QListWidgetItem(describe_candidate(candidate)))
        self.scan_status.setText(problem or self._scan_summary(found))
        if self._candidates:
            self.scan_results.setCurrentRow(0)
            self.use_button.setEnabled(True)

    def _scan_summary(self, found: list[Candidate]) -> str:
        """Return a sentence about the scan's result."""
        try:
            camera = select_camera(found, self._config.camera.mac)
        except DiscoveryError as error:
            return str(error)
        return f"Camera found at {camera.host}"

    def use_selected(self) -> None:
        """Put the selected device's address on the form."""
        row = self.scan_results.currentRow()
        if row < 0:
            return
        candidate = self._candidates[row]
        self.set_value(HOST_FIELD, candidate.host)
        self.set_value(MAC_FIELD, candidate.mac or "")


def _scrollable(form: QFormLayout) -> QScrollArea:
    """Return a form that scrolls if it doesn't fit."""
    inner = QWidget()
    inner.setLayout(form)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(inner)
    return area


def _error_label() -> QLabel:
    """Return a hidden label for an error."""
    label = QLabel()
    label.setStyleSheet(ERROR_STYLE)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.hide()
    return label


def _show_error(label: QLabel, message: str) -> None:
    """Show an error, or hide the label if there isn't one."""
    label.setText(message)
    label.setVisible(bool(message))


def _make_editor(field: Field) -> QWidget:
    """Return the widget for editing a field."""
    if field.kind is FieldKind.CHECK:
        return QCheckBox()
    if field.kind is FieldKind.CHOICE:
        editor = QComboBox()
        editor.addItems(field.choices)
        return editor
    return QLineEdit()


def _read_editor(editor: QWidget) -> str | bool:
    """Return an editor's value."""
    if isinstance(editor, QCheckBox):
        return editor.isChecked()
    if isinstance(editor, QComboBox):
        return editor.currentText()
    return editor.text()


def _write_editor(editor: QWidget, value: str | bool) -> None:
    """Set an editor's value."""
    if isinstance(editor, QCheckBox):
        editor.setChecked(bool(value))
    elif isinstance(editor, QComboBox):
        editor.setCurrentText(str(value))
    else:
        editor.setText(str(value))
