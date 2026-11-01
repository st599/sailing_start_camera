#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: sequence_editor.py
#  Description: The sequence and flag timing editor.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: PySide6, pydantic
#
#########################################################

"""The sequence and flag timing editor."""

#
# SYSTEM IMPORTS
#

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

#
# PROJECT IMPORTS
#
from start_camera.config import (
    MAX_STARTS,
    ConfigError,
    FlagAction,
    GeneralRecallMode,
    PreparatoryFlag,
    SequenceConfig,
    SequencesConfig,
    default_flag_events,
)
from start_camera.gui.sequence_model import build_timeline, check_sequences
from start_camera.race_templates import (
    USER_DIRECTORY,
    TemplateLibrary,
    load_library,
    save_user_template,
)

#
#  GLOBAL VARIABLES
#

ERROR_STYLE = "color: #c00000; font-weight: bold;"
WARNING_STYLE = "color: #8a5a00;"
EVENT_COLUMNS = ("Minutes before", "Flag", "Action")
NEW_CLASS_FLAG = "A"
NEW_CLASS_NAME = "New class"

#
# FUNCTIONS
#


def _new_item(name: str, flag: str) -> dict[str, Any]:
    """Return a new sequence with the standard signals."""
    return SequenceConfig(name=name, class_flag=flag).model_dump(mode="json")


class SequenceEditorDialog(QDialog):
    """Edit the day's start sequences, starting from a template if wanted."""

    def __init__(
        self,
        config: SequencesConfig,
        parent: QWidget | None = None,
        *,
        library: TemplateLibrary | None = None,
        user_templates: Path = USER_DIRECTORY,
    ) -> None:
        """Build the editor.

        Args:
            config: The sequences to start from, which aren't changed.
            parent: The parent window.
            library: The available templates. Defaults to loading them.
            user_templates: Where new templates are saved.
        """
        super().__init__(parent)
        self._library = library if library is not None else load_library()
        self._user_templates = user_templates
        self._items: list[dict[str, Any]] = []
        self._current = -1
        self._loading = False
        self.result_config: SequencesConfig | None = None
        self.setWindowTitle("Start sequences")
        self.resize(1000, 680)
        self._build()
        self.show_sequences(config)

    def _build(self) -> None:
        """Lay out the editor."""
        columns = QHBoxLayout()
        columns.addLayout(self._build_list(), stretch=1)
        columns.addWidget(self._build_details(), stretch=2)
        columns.addLayout(self._build_timeline(), stretch=2)
        self.error_label = _message_label(ERROR_STYLE)
        self.warning_label = _message_label(WARNING_STYLE)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept_sequences)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(self._build_day())
        layout.addLayout(columns, stretch=1)
        layout.addWidget(self.error_label)
        layout.addWidget(self.warning_label)
        layout.addWidget(buttons)

    def _build_day(self) -> QFormLayout:
        """Return the settings for the whole day."""
        self.template_box = QComboBox()
        for template in self._library.templates:
            self.template_box.addItem(template.name)
        use = _button("Use template", self.use_template)
        save = _button("Save as template...", self._ask_to_save_template)
        row = QHBoxLayout()
        row.addWidget(self.template_box, stretch=1)
        row.addWidget(use)
        row.addWidget(save)
        self.scheduled_box = QCheckBox("Scheduled, at")
        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm:ss")
        self.recall_box = QComboBox()
        self.recall_box.addItems([mode.value for mode in GeneralRecallMode])
        self.recall_minutes_edit = QLineEdit()
        self.day_flags_edit = QLineEdit()
        self.day_flags_edit.setPlaceholderText("Flags that fly all day, such as Y")
        timing = QHBoxLayout()
        timing.addWidget(self.scheduled_box)
        timing.addWidget(self.time_edit)
        timing.addStretch(1)
        self.scheduled_box.toggled.connect(self._day_changed)
        self.time_edit.timeChanged.connect(self._day_changed)
        self.recall_box.currentTextChanged.connect(self._day_changed)
        self.recall_minutes_edit.textChanged.connect(self._day_changed)
        self.day_flags_edit.textChanged.connect(self._day_changed)
        form = QFormLayout()
        form.addRow("Template", row)
        form.addRow("First start", timing)
        form.addRow("After a general recall", self.recall_box)
        form.addRow("First Substitute flies for (minutes)", self.recall_minutes_edit)
        form.addRow("All-day flags", self.day_flags_edit)
        return form

    def _build_list(self) -> QVBoxLayout:
        """Return the list of sequences and its buttons."""
        self.sequence_list = QListWidget()
        self.sequence_list.currentRowChanged.connect(self._select)
        self.class_box = QComboBox()
        self.class_box.addItem("New class")
        for entry in self._library.classes:
            self.class_box.addItem(f"{entry.name} ({entry.flag})")
        self.add_button = _button("Add", self.add_sequence)
        self.remove_button = _button("Remove", self.remove_sequence)
        up = _button("Move up", lambda: self.move_sequence(-1))
        down = _button("Move down", lambda: self.move_sequence(1))
        moves = QHBoxLayout()
        moves.addWidget(up)
        moves.addWidget(down)
        layout = QVBoxLayout()
        layout.addWidget(QLabel(f"Starts, in order (up to {MAX_STARTS})"))
        layout.addWidget(self.sequence_list, stretch=1)
        layout.addWidget(self.class_box)
        layout.addWidget(self.add_button)
        layout.addWidget(self.remove_button)
        layout.addLayout(moves)
        return layout

    def _build_details(self) -> QGroupBox:
        """Return the selected sequence's settings and events."""
        self.name_edit = QLineEdit()
        self.flag_edit = QLineEdit()
        self.flag_edit.setToolTip('For a combined start, join flags with +: "8+Solo"')
        self.preparatory_box = QComboBox()
        self.preparatory_box.addItems([flag.value for flag in PreparatoryFlag])
        self.interval_edit = QLineEdit()
        self.events_table = QTableWidget(0, len(EVENT_COLUMNS))
        self.events_table.setHorizontalHeaderLabels(EVENT_COLUMNS)
        self.events_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.events_table.verticalHeader().setVisible(False)
        self.events_table.cellChanged.connect(self._details_changed)
        for edit in (self.name_edit, self.flag_edit, self.interval_edit):
            edit.textChanged.connect(self._details_changed)
        self.preparatory_box.currentTextChanged.connect(self._details_changed)
        form = QFormLayout()
        form.addRow("Name", self.name_edit)
        form.addRow("Class flag", self.flag_edit)
        form.addRow("Preparatory flag", self.preparatory_box)
        form.addRow("Minutes after previous", self.interval_edit)
        buttons = QHBoxLayout()
        buttons.addWidget(_button("Add event", self.add_event))
        buttons.addWidget(_button("Remove event", self.remove_event))
        buttons.addWidget(_button("Standard signals", self.use_standard_events))
        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.events_table, stretch=1)
        layout.addLayout(buttons)
        self.details = QGroupBox("Selected start")
        self.details.setLayout(layout)
        return self.details

    def _build_timeline(self) -> QVBoxLayout:
        """Return the preview of what flies when."""
        self.timeline_view = QPlainTextEdit()
        self.timeline_view.setReadOnly(True)
        layout = QVBoxLayout()
        layout.addWidget(QLabel("What flies when"))
        layout.addWidget(self.timeline_view, stretch=1)
        return layout

    def show_sequences(self, config: SequencesConfig) -> None:
        """Put a set of sequences in the editor."""
        data = config.model_dump(mode="json")
        self._loading = True
        self._items = data["items"]
        self._current = -1
        self.scheduled_box.setChecked(config.first_start is not None)
        if config.first_start is not None:
            start = config.first_start
            self.time_edit.setTime(QTime(start.hour, start.minute, start.second))
        self.recall_box.setCurrentText(config.general_recall.value)
        self.recall_minutes_edit.setText(f"{config.recall_flag_minutes:g}")
        self.day_flags_edit.setText(", ".join(config.day_flags))
        self._loading = False
        self._refresh_list(0 if self._items else -1)

    def use_template(self) -> None:
        """Replace the sequences with the chosen template."""
        index = self.template_box.currentIndex()
        if index >= 0:
            self.show_sequences(self._library.templates[index].sequences)

    def add_sequence(self) -> None:
        """Add a start, for the class chosen in the list."""
        if len(self._items) >= MAX_STARTS:
            self.error_label.setText(f"There can be at most {MAX_STARTS} starts")
            return
        self._store_details()
        index = self.class_box.currentIndex() - 1
        if index >= 0:
            entry = self._library.classes[index]
            item = _new_item(entry.name, entry.flag)
        else:
            item = _new_item(NEW_CLASS_NAME, NEW_CLASS_FLAG)
        self._items.append(item)
        self._refresh_list(len(self._items) - 1)

    def remove_sequence(self) -> None:
        """Remove the selected start."""
        if self._current < 0:
            return
        del self._items[self._current]
        self._current = -1
        self._refresh_list(min(self.sequence_list.currentRow(), len(self._items) - 1))

    def move_sequence(self, step: int) -> None:
        """Move the selected start earlier or later."""
        target = self._current + step
        if self._current < 0 or not 0 <= target < len(self._items):
            return
        self._store_details()
        items = self._items
        items[self._current], items[target] = items[target], items[self._current]
        self._current = -1
        self._refresh_list(target)

    def add_event(self) -> None:
        """Add a blank event to the selected start."""
        if self._current >= 0:
            self._add_event_row("0", "class", FlagAction.UP.value)
            self._details_changed()

    def remove_event(self) -> None:
        """Remove the selected event."""
        row = self.events_table.currentRow()
        if row >= 0:
            self.events_table.removeRow(row)
            self._details_changed()

    def use_standard_events(self) -> None:
        """Give the selected start the standard five minute signals."""
        if self._current < 0:
            return
        events = [event.model_dump(mode="json") for event in default_flag_events()]
        self._items[self._current]["events"] = events
        self._show_details()
        self.refresh()

    def _select(self, row: int) -> None:
        """Show the selected start."""
        if self._loading:
            return
        self._store_details()
        self._current = row
        self._show_details()
        self.refresh()

    def _refresh_list(self, select: int) -> None:
        """Show the list of starts, with one selected."""
        self._loading = True
        self.sequence_list.clear()
        for item in self._items:
            self.sequence_list.addItem(item["name"])
        self.sequence_list.setCurrentRow(select)
        self._current = select
        self._loading = False
        self._show_details()
        self.refresh()

    def _show_details(self) -> None:
        """Show the selected start's settings and events."""
        self._loading = True
        self.details.setEnabled(self._current >= 0)
        self.events_table.setRowCount(0)
        if self._current >= 0:
            item = self._items[self._current]
            self.name_edit.setText(item["name"])
            self.flag_edit.setText(item["class_flag"])
            self.preparatory_box.setCurrentText(item["preparatory_flag"])
            self.interval_edit.setText(_text(item["minutes_after_previous"]))
            self.interval_edit.setEnabled(self._current > 0)
            for event in item["events"]:
                self._add_event_row(
                    _text(event["minutes_before_start"]), event["flag"], event["action"]
                )
        self._loading = False

    def _add_event_row(self, minutes: str, flag: str, action: str) -> None:
        """Add a row to the events table, without reacting until it's complete."""
        was_loading = self._loading
        self._loading = True
        row = self.events_table.rowCount()
        self.events_table.insertRow(row)
        self.events_table.setItem(row, 0, QTableWidgetItem(minutes))
        self.events_table.setItem(row, 1, QTableWidgetItem(flag))
        box = QComboBox()
        box.addItems([item.value for item in FlagAction])
        box.setCurrentText(action)
        box.currentTextChanged.connect(self._details_changed)
        self.events_table.setCellWidget(row, 2, box)
        self._loading = was_loading

    def _store_details(self) -> None:
        """Keep what's on the form for the selected start."""
        if self._current < 0 or self._current >= len(self._items):
            return
        item = self._items[self._current]
        item["name"] = self.name_edit.text().strip()
        item["class_flag"] = self.flag_edit.text().strip()
        item["preparatory_flag"] = self.preparatory_box.currentText()
        item["minutes_after_previous"] = self.interval_edit.text().strip()
        item["events"] = [
            {
                "minutes_before_start": _cell(self.events_table, row, 0),
                "flag": _cell(self.events_table, row, 1),
                "action": self.events_table.cellWidget(row, 2).currentText(),
            }
            for row in range(self.events_table.rowCount())
        ]
        list_item = self.sequence_list.item(self._current)
        if list_item is not None:
            list_item.setText(item["name"])

    def _details_changed(self) -> None:
        """Update after an edit to the selected start."""
        if not self._loading:
            self._store_details()
            self.refresh()

    def _day_changed(self) -> None:
        """Update after an edit to the day's settings."""
        self.time_edit.setEnabled(self.scheduled_box.isChecked())
        if not self._loading:
            self.refresh()

    def _build_config(self) -> SequencesConfig:
        """Return the sequences as they're set.

        Raises:
            ValueError: With a message for the user, if they aren't valid.
        """
        items = [_parsed_item(item) for item in self._items]
        first_start = None
        if self.scheduled_box.isChecked():
            first_start = self.time_edit.time().toString("HH:mm:ss")
        data = {
            "first_start": first_start,
            "general_recall": self.recall_box.currentText(),
            "recall_flag_minutes": _number(
                self.recall_minutes_edit.text(), "First Substitute minutes"
            ),
            "day_flags": _split_flags(self.day_flags_edit.text()),
            "items": items,
        }
        try:
            return SequencesConfig.model_validate_json(json.dumps(data))
        except ValidationError as error:
            raise ValueError(_describe_errors(error, items)) from error

    def refresh(self) -> None:
        """Show the timeline and any problems for the sequences as they're set."""
        self.add_button.setEnabled(len(self._items) < MAX_STARTS)
        try:
            config = self._build_config()
        except ValueError as error:
            self.error_label.setText(str(error))
            self.warning_label.setText("")
            self.timeline_view.setPlainText("")
            return
        problems = check_sequences(config)
        self.error_label.setText("\n".join(problems.errors))
        self.warning_label.setText("\n".join(problems.warnings))
        lines = [row.describe() for row in build_timeline(config)]
        for flag in config.day_flags:
            lines.insert(0, f"All day: {flag}")
        self.timeline_view.setPlainText("\n".join(lines))

    def accept_sequences(self) -> None:
        """Close the editor if the sequences are valid."""
        self._store_details()
        self.refresh()
        if self.error_label.text():
            return
        self.result_config = self._build_config()
        self.accept()

    def _ask_to_save_template(self) -> None:
        """Save the sequences as a template, for reuse on another day."""
        name, chosen = QInputDialog.getText(self, "Save as template", "Template name:")
        if chosen and name.strip():
            self.save_template(name.strip())

    def save_template(self, name: str) -> None:
        """Save the sequences as a template, if they're valid."""
        self._store_details()
        self.refresh()
        if self.error_label.text():
            return
        try:
            save_user_template(name, self._build_config(), self._user_templates)
        except ConfigError as error:
            QMessageBox.warning(self, "Start sequences", str(error))
            return
        self._library = load_library(user=self._user_templates)
        self.template_box.clear()
        for template in self._library.templates:
            self.template_box.addItem(template.name)
        self.template_box.setCurrentText(name)


def _text(value: object) -> str:
    """Return a stored number as tidy text."""
    return f"{value:g}" if isinstance(value, int | float) else str(value)


def _cell(table: QTableWidget, row: int, column: int) -> str:
    """Return the text of a table cell."""
    item = table.item(row, column)
    return item.text().strip() if item is not None else ""


def _number(text: object, label: str) -> float:
    """Return a number from a form.

    Raises:
        ValueError: With a message for the user, if it isn't a number.
    """
    try:
        return float(str(text).strip())
    except ValueError:
        raise ValueError(f"{label}: enter a number") from None


def _split_flags(text: str) -> list[str]:
    """Return the flags in a comma-separated list."""
    return [flag.strip() for flag in text.split(",") if flag.strip()]


def _parsed_item(item: dict[str, Any]) -> dict[str, Any]:
    """Return a sequence with its numbers converted from what's on the form."""
    name = item["name"] or "A start"
    return {
        **item,
        "minutes_after_previous": _number(
            item["minutes_after_previous"], f"{name}, minutes after previous"
        ),
        "events": [
            {
                **event,
                "minutes_before_start": _number(
                    event["minutes_before_start"], f"{name}, event minutes"
                ),
            }
            for event in item["events"]
        ],
    }


def _describe_errors(error: ValidationError, items: list[dict[str, Any]]) -> str:
    """Return validation errors in terms of the starts' names."""
    lines = []
    for problem in error.errors(include_input=False, include_url=False):
        location = list(problem["loc"])
        prefix = ""
        if len(location) >= 2 and location[0] == "items":
            start = items[location[1]]["name"] or f"Start {location[1] + 1}"
            prefix = f"{start}: "
            location = location[2:]
        place = " ".join(str(part).replace("_", " ") for part in location)
        lines.append(f"{prefix}{place}: {problem['msg']}")
    return "\n".join(lines)


def _message_label(style: str) -> QLabel:
    """Return a label for messages."""
    label = QLabel()
    label.setStyleSheet(style)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def _button(text: str, action: object) -> QPushButton:
    """Return a button that doesn't take over Enter from OK."""
    button = QPushButton(text)
    button.setAutoDefault(False)
    button.clicked.connect(lambda _checked=False: action())
    return button
