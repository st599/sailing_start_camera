#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_sequence_editor.py
#  Description: Tests for the race templates and the sequence editor.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, pytest, pytest-qt, PySide6
#
#########################################################

"""Tests for the race templates and the sequence editor."""

#
# SYSTEM IMPORTS
#

import json
from datetime import datetime, time
from pathlib import Path

import numpy as np
import pytest

#
# PROJECT IMPORTS
#
from start_camera.config import (
    Config,
    ConfigError,
    ControlConfig,
    FlagAction,
    FlagEvent,
    FlagsOverlayConfig,
    GeneralRecallMode,
    Secrets,
    SequenceConfig,
    SequencesConfig,
    StreamConfig,
    load_config,
)
from start_camera.gui.sequence_model import build_timeline, check_sequences
from start_camera.gui.settings_model import ConfigDocument
from start_camera.ingest import Frame
from start_camera.overlays.compositor import OverlayContext
from start_camera.overlays.flag_artwork import load_flag
from start_camera.overlays.flags import FlagsOverlay
from start_camera.race_clock import Phase, RaceClock
from start_camera.race_templates import load_library, save_user_template

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QTime  # noqa: E402

from start_camera.gui.main_window import MainWindow  # noqa: E402
from start_camera.gui.sequence_editor import SequenceEditorDialog  # noqa: E402
from start_camera.gui.session import GuiSession  # noqa: E402

#
#  GLOBAL VARIABLES
#

MORNING_BLUE = [
    ("10:25", "1", True),
    ("10:30", "1", False),
    ("10:30", "R", True),
    ("10:35", "R", False),
]
MORNING_WHITE = [
    ("11:25", "O", True),
    ("11:30", "O", False),
    ("11:30", "8", True),
    ("11:30", "Solo", True),
    ("11:35", "8", False),
    ("11:35", "Solo", False),
    ("11:35", "E", True),
    ("11:35", "G", True),
    ("11:40", "E", False),
    ("11:40", "G", False),
    ("11:40", "R", True),
    ("11:45", "R", False),
]
AFTERNOON_BLUE = [
    ("13:55", "1", True),
    ("14:00", "1", False),
    ("14:00", "R", True),
    ("14:05", "R", False),
]
AFTERNOON_TIMES = {
    "11:25": "14:55",
    "11:30": "15:00",
    "11:35": "15:05",
    "11:40": "15:10",
    "11:45": "15:15",
}
AFTERNOON_WHITE = [
    (AFTERNOON_TIMES[moment], flag, up) for moment, flag, up in MORNING_WHITE
]
TEMPLATES = {
    "Summer Blue, morning": MORNING_BLUE,
    "Summer White, morning": MORNING_WHITE,
    "Summer Blue and White, morning": MORNING_BLUE + MORNING_WHITE,
    "Summer Blue, afternoon": AFTERNOON_BLUE,
    "Summer White, afternoon": AFTERNOON_WHITE,
    "Summer Blue and White, afternoon": AFTERNOON_BLUE + AFTERNOON_WHITE,
}

#
# FUNCTIONS
#


def template(name: str) -> SequencesConfig:
    """Return a built-in template by name."""
    library = load_library(user=Path("/nonexistent"))
    return next(item.sequences for item in library.templates if item.name == name)


def class_flag_rows(config: SequencesConfig) -> list[tuple[str, str, bool]]:
    """Return when each class flag moves, as the Series Notice lists it."""
    preparatory = {"P"}
    return [
        (row.time[:5], row.flag, row.up)
        for row in build_timeline(config)
        if row.flag not in preparatory
    ]


@pytest.mark.parametrize("name", TEMPLATES)
def test_template_matches_the_series_notice(name: str) -> None:
    assert class_flag_rows(template(name)) == TEMPLATES[name]


@pytest.mark.parametrize("name", TEMPLATES)
def test_templates_have_nothing_wrong_with_them(name: str) -> None:
    problems = check_sequences(template(name))
    assert problems.errors == []
    assert problems.warnings == []


def test_templates_run_on_the_race_clock() -> None:
    config = template("Summer White, morning")
    clock = RaceClock(config, datetime(2026, 5, 3, 9, 0).timestamp())
    at = lambda h, m: datetime(2026, 5, 3, h, m).timestamp()  # noqa: E731
    assert clock.state(at(9, 0)).phase is Phase.WAITING
    assert clock.state(at(11, 25)).flags == ("O",)
    assert clock.state(at(11, 26)).flags == ("O", "P")
    assert clock.state(at(11, 30)).flags == ("8", "Solo")
    assert clock.state(at(11, 30)).latest.name == "Merlin Rocket"
    assert clock.state(at(11, 35)).flags == ("E", "G")
    assert clock.state(at(11, 35)).upcoming.name == "Enterprise and Handicap"
    assert clock.state(at(11, 40)).flags == ("R",)
    assert clock.state(at(11, 45)).latest.name == "N18"


def test_combined_day_runs_with_a_gap_between_series() -> None:
    config = template("Summer Blue and White, morning")
    clock = RaceClock(config, datetime(2026, 5, 3, 9, 0).timestamp())
    at = lambda h, m: datetime(2026, 5, 3, h, m).timestamp()  # noqa: E731
    assert clock.state(at(10, 35)).latest.name == "N18"
    assert clock.state(at(11, 0)).flags == ()
    assert clock.state(at(11, 0)).phase is Phase.WAITING
    assert clock.state(at(11, 25)).flags == ("O",)


def test_thursday_n18_template() -> None:
    config = template("Thursday N18 signals")
    assert config.first_start is None
    rows = build_timeline(config)
    assert [(row.time, row.flag, row.up) for row in rows] == [
        ("T-01:00", "R", True),
        ("T+00:00", "R", False),
    ]


def test_all_class_flags_from_the_sailing_instructions_are_listed() -> None:
    classes = {
        entry.name: entry.flag
        for entry in load_library(user=Path("/nonexistent")).classes
    }
    assert classes == {
        "Merlin Rocket": "O",
        "ILCA (Laser)": "8",
        "N18": "R",
        "Enterprise": "E",
        "Firefly": "F",
        "Handicap": "G",
        "Solo": "Solo",
        "Topper": "T",
        "All classes": "1",
        "A-Rater": "A",
    }


def flags_overlay(config: SequencesConfig, tmp_path: Path) -> FlagsOverlay:
    """Return a flags overlay for sequences."""
    clock = RaceClock(config, datetime(2026, 5, 3, 9, 0).timestamp())
    settings = FlagsOverlayConfig(custom_directory=str(tmp_path))
    return FlagsOverlay(settings, clock, config.items)


def test_combined_start_shows_each_flag_with_its_own_label(tmp_path: Path) -> None:
    overlay = flags_overlay(template("Summer White, morning"), tmp_path)
    moment = datetime(2026, 5, 3, 11, 30).timestamp()
    assert overlay.state(OverlayContext(moment, 1280, 720, True)) == ("8", "Solo")
    assert overlay._labels["8"] == "8 ILCA (Laser)"
    assert overlay._labels["Solo"] == "Solo"
    assert overlay._labels["E"] == "E Enterprise"
    assert overlay._labels["G"] == "G Handicap"
    assert overlay.draw(OverlayContext(moment, 1280, 720, True)) is not None


def test_each_template_flag_has_artwork_or_a_panel(tmp_path: Path) -> None:
    for flag in ("1", "R", "O", "8", "E", "G"):
        image = load_flag(flag, 40, tmp_path)
        assert image.shape[0] == 40


def test_combined_flags_can_be_used_in_any_event() -> None:
    item = SequenceConfig(
        name="Both",
        class_flag="A+B",
        events=[
            FlagEvent(minutes_before_start=2, flag="class", action=FlagAction.UP),
            FlagEvent(minutes_before_start=1, flag="A", action=FlagAction.DOWN),
            FlagEvent(minutes_before_start=0, flag="B", action=FlagAction.DOWN),
        ],
    )
    clock = RaceClock(SequencesConfig(items=[item]), 1000.0)
    clock.start_now(1000.0)
    assert clock.state(1000.0).flags == ("A", "B")
    assert clock.state(1060.0).flags == ("B",)


def test_user_template_is_saved_and_reused(tmp_path: Path) -> None:
    config = template("Summer Blue, morning")
    save_user_template("My day", config, tmp_path)
    library = load_library(user=tmp_path)
    found = [item for item in library.templates if item.name == "My day"]
    assert found[0].sequences == config


def test_saving_again_replaces_a_template_of_that_name(tmp_path: Path) -> None:
    save_user_template("My day", template("Summer Blue, morning"), tmp_path)
    save_user_template("My day", template("Summer White, morning"), tmp_path)
    found = [
        item for item in load_library(user=tmp_path).templates if item.name == "My day"
    ]
    assert len(found) == 1
    assert len(found[0].sequences.items) == 4


def test_a_clubs_own_file_is_added(tmp_path: Path) -> None:
    (tmp_path / "other.json").write_text(
        json.dumps(
            {
                "club": "Another club",
                "classes": [{"name": "Optimist", "flag": "Q"}],
                "templates": [],
            }
        ),
        encoding="utf-8",
    )
    classes = load_library(user=tmp_path).classes
    assert classes[-1].name == "Optimist"


def test_bad_template_file_is_skipped_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    library = load_library(user=tmp_path)
    assert library.templates
    assert "Skipping the template file bad.json" in caplog.text


def test_invalid_user_file_isnt_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "my_templates.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ConfigError, match="isn't valid"):
        save_user_template("My day", template("Summer Blue, morning"), tmp_path)
    assert path.read_text(encoding="utf-8") == "{"


def test_timeline_without_a_scheduled_time_is_relative() -> None:
    config = SequencesConfig(items=[SequenceConfig(name="A", class_flag="A")])
    rows = build_timeline(config)
    assert rows[0].time == "T-05:00"
    assert rows[-1].time == "T+00:00"
    assert rows[-1].is_start
    assert "A: A down (start)" in rows[-1].describe()


def test_timeline_puts_flags_coming_down_first() -> None:
    rows = build_timeline(template("Summer White, morning"))
    at_start = [row for row in rows if row.time == "11:30:00" and row.flag != "P"]
    assert [row.up for row in at_start] == [False, True, True]


def events(*items: tuple[float, str, FlagAction]) -> list[FlagEvent]:
    """Return events."""
    return [
        FlagEvent(minutes_before_start=m, flag=f, action=a) for m, f, a in items
    ]


UP, DOWN = FlagAction.UP, FlagAction.DOWN


@pytest.mark.parametrize(
    ("items", "message"),
    [
        (((1, "class", UP), (2, "class", DOWN)), "comes down without having gone up"),
        (((3, "class", UP), (2, "class", UP), (0, "class", DOWN)), "goes up twice"),
        (((2, "class", UP), (2, "class", DOWN)), "two events at 2 minutes"),
        (((0, "X", DOWN),), "without having gone up"),
    ],
)
def test_impossible_timings_are_explained(
    items: tuple[tuple[float, str, FlagAction], ...], message: str
) -> None:
    config = SequencesConfig(
        items=[SequenceConfig(name="A", class_flag="A", events=events(*items))]
    )
    assert any(message in error for error in check_sequences(config).errors)


def test_flag_that_never_comes_down_is_a_warning() -> None:
    config = SequencesConfig(
        items=[
            SequenceConfig(name="A", class_flag="A", events=events((2, "class", UP)))
        ]
    )
    problems = check_sequences(config)
    assert problems.errors == []
    assert "doesn't come down" in problems.warnings[0]


def test_overlap_is_a_warning() -> None:
    config = SequencesConfig(
        items=[
            SequenceConfig(name="A", class_flag="A"),
            SequenceConfig(name="B", class_flag="B", minutes_after_previous=2),
        ]
    )
    problems = check_sequences(config)
    assert problems.errors == []
    assert "overlap" in problems.warnings[0]


def test_rolling_start_isnt_an_overlap() -> None:
    assert check_sequences(template("Summer Blue, morning")).warnings == []


def make_dialog(
    qtbot: object, tmp_path: Path, config: SequencesConfig | None = None
) -> SequenceEditorDialog:
    """Return an editor."""
    dialog = SequenceEditorDialog(
        config or SequencesConfig(),
        library=load_library(user=tmp_path),
        user_templates=tmp_path,
    )
    qtbot.addWidget(dialog)
    return dialog


def choose_template(dialog: SequenceEditorDialog, name: str) -> None:
    """Use a template."""
    dialog.template_box.setCurrentText(name)
    dialog.use_template()


def result(dialog: SequenceEditorDialog) -> SequencesConfig:
    """Press OK and return the result."""
    dialog.accept_sequences()
    assert dialog.result_config is not None, dialog.error_label.text()
    return dialog.result_config


def test_editor_shows_existing_sequences_unchanged(
    qtbot: object, tmp_path: Path
) -> None:
    config = template("Summer White, morning")
    assert result(make_dialog(qtbot, tmp_path, config)) == config


@pytest.mark.parametrize("name", TEMPLATES)
def test_template_passes_through_the_editor_unchanged(
    qtbot: object, tmp_path: Path, name: str
) -> None:
    dialog = make_dialog(qtbot, tmp_path)
    choose_template(dialog, name)
    assert class_flag_rows(result(dialog)) == TEMPLATES[name]


def test_timeline_shown_is_the_sequences_timeline(
    qtbot: object, tmp_path: Path
) -> None:
    dialog = make_dialog(qtbot, tmp_path)
    choose_template(dialog, "Summer Blue, morning")
    shown = dialog.timeline_view.toPlainText().splitlines()
    assert shown == [row.describe() for row in build_timeline(result(dialog))]
    assert shown[0].startswith("10:25:00  All classes: 1 up")


def test_sequence_runs_as_the_timeline_says(qtbot: object, tmp_path: Path) -> None:
    dialog = make_dialog(qtbot, tmp_path)
    choose_template(dialog, "Summer White, afternoon")
    config = result(dialog)
    clock = RaceClock(config, datetime(2026, 5, 10, 9, 0).timestamp())
    for row in build_timeline(config):
        moment = datetime.combine(
            datetime(2026, 5, 10).date(), time.fromisoformat(row.time)
        ).timestamp()
        assert (row.flag in clock.state(moment).flags) == row.up, row.describe()


def test_scheduled_or_manual(qtbot: object, tmp_path: Path) -> None:
    dialog = make_dialog(qtbot, tmp_path)
    assert not dialog.scheduled_box.isChecked()
    assert result(dialog).first_start is None
    dialog.scheduled_box.setChecked(True)
    dialog.time_edit.setTime(QTime(13, 45, 0))
    assert result(dialog).first_start == time(13, 45)


def test_day_settings_are_kept(qtbot: object, tmp_path: Path) -> None:
    dialog = make_dialog(qtbot, tmp_path)
    dialog.recall_box.setCurrentText("end_of_queue")
    dialog.recall_minutes_edit.setText("2")
    dialog.day_flags_edit.setText("Y, L")
    config = result(dialog)
    assert config.general_recall is GeneralRecallMode.END_OF_QUEUE
    assert config.recall_flag_minutes == 2
    assert config.day_flags == ["Y", "L"]


def test_all_day_flags_appear_in_the_timeline(qtbot: object, tmp_path: Path) -> None:
    dialog = make_dialog(qtbot, tmp_path)
    dialog.day_flags_edit.setText("Y")
    assert "All day: Y" in dialog.timeline_view.toPlainText()


def test_add_from_the_class_list(qtbot: object, tmp_path: Path) -> None:
    dialog = make_dialog(qtbot, tmp_path)
    dialog.class_box.setCurrentText("Merlin Rocket (O)")
    dialog.add_sequence()
    item = result(dialog).items[0]
    assert (item.name, item.class_flag) == ("Merlin Rocket", "O")
    assert len(item.events) == 4


def test_add_a_new_class(qtbot: object, tmp_path: Path) -> None:
    dialog = make_dialog(qtbot, tmp_path)
    dialog.add_sequence()
    dialog.name_edit.setText("Wayfarer")
    dialog.flag_edit.setText("W")
    item = result(dialog).items[0]
    assert (item.name, item.class_flag) == ("Wayfarer", "W")
    assert dialog.sequence_list.item(0).text() == "Wayfarer"


def test_up_to_ten_starts(qtbot: object, tmp_path: Path) -> None:
    dialog = make_dialog(qtbot, tmp_path)
    for _ in range(12):
        dialog.add_sequence()
    assert len(dialog._items) == 10
    assert not dialog.add_button.isEnabled()
    assert len(result(dialog).items) == 10


def test_remove_and_reorder(qtbot: object, tmp_path: Path) -> None:
    dialog = make_dialog(qtbot, tmp_path)
    choose_template(dialog, "Summer White, morning")
    dialog.sequence_list.setCurrentRow(3)
    dialog.move_sequence(-1)
    names = [item["name"] for item in dialog._items]
    assert names[2] == "N18"
    assert dialog._current == 2
    dialog.move_sequence(-10)
    dialog.sequence_list.setCurrentRow(0)
    dialog.remove_sequence()
    assert [item.name for item in result(dialog).items] == [
        "ILCA and Solo",
        "N18",
        "Enterprise and Handicap",
    ]


def test_removing_everything_is_allowed(qtbot: object, tmp_path: Path) -> None:
    dialog = make_dialog(qtbot, tmp_path)
    dialog.add_sequence()
    dialog.remove_sequence()
    dialog.remove_sequence()
    assert result(dialog).items == []
    assert not dialog.details.isEnabled()


def test_moving_with_nothing_selected_is_safe(qtbot: object, tmp_path: Path) -> None:
    dialog = make_dialog(qtbot, tmp_path)
    dialog.move_sequence(1)
    assert dialog._items == []


def test_edits_survive_changing_the_selection(qtbot: object, tmp_path: Path) -> None:
    dialog = make_dialog(qtbot, tmp_path)
    choose_template(dialog, "Summer Blue, morning")
    dialog.interval_edit.setEnabled(True)
    dialog.name_edit.setText("Everyone")
    dialog.sequence_list.setCurrentRow(1)
    dialog.interval_edit.setText("7")
    dialog.sequence_list.setCurrentRow(0)
    assert dialog.name_edit.text() == "Everyone"
    config = result(dialog)
    assert config.items[0].name == "Everyone"
    assert config.items[1].minutes_after_previous == 7


def test_editing_an_event_updates_the_timeline(qtbot: object, tmp_path: Path) -> None:
    dialog = make_dialog(qtbot, tmp_path)
    dialog.add_sequence()
    dialog.events_table.item(0, 0).setText("6")
    assert "T-06:00" in dialog.timeline_view.toPlainText()
    assert result(dialog).items[0].events[0].minutes_before_start == 6


def test_add_and_remove_events(qtbot: object, tmp_path: Path) -> None:
    dialog = make_dialog(qtbot, tmp_path)
    dialog.add_sequence()
    dialog.events_table.setCurrentCell(1, 0)
    dialog.remove_event()
    assert dialog.events_table.rowCount() == 3
    dialog.add_event()
    assert dialog.events_table.rowCount() == 4


def test_standard_signals_can_be_restored(qtbot: object, tmp_path: Path) -> None:
    dialog = make_dialog(qtbot, tmp_path)
    choose_template(dialog, "Thursday N18 signals")
    assert dialog.events_table.rowCount() == 2
    dialog.use_standard_events()
    assert dialog.events_table.rowCount() == 4
    assert len(result(dialog).items[0].events) == 4


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda d: d.events_table.item(0, 0).setText("soon"), "enter a number"),
        (lambda d: d.events_table.item(0, 0).setText("-1"), "greater than or equal"),
        (lambda d: d.events_table.item(0, 0).setText("0"), "two events"),
        (lambda d: d.name_edit.setText(""), "name"),
        (lambda d: d.flag_edit.setText(""), "class flag"),
        (lambda d: d.recall_minutes_edit.setText("x"), "enter a number"),
    ],
)
def test_invalid_timings_are_explained_before_saving(
    qtbot: object, tmp_path: Path, edit: object, message: str
) -> None:
    dialog = make_dialog(qtbot, tmp_path)
    dialog.add_sequence()
    edit(dialog)
    dialog.accept_sequences()
    assert dialog.result_config is None
    assert message in dialog.error_label.text()


def test_warning_doesnt_stop_saving(qtbot: object, tmp_path: Path) -> None:
    dialog = make_dialog(qtbot, tmp_path)
    dialog.add_sequence()
    dialog.add_sequence()
    dialog.interval_edit.setText("2")
    assert "overlap" in dialog.warning_label.text()
    assert dialog.error_label.text() == ""
    assert len(result(dialog).items) == 2


def test_saved_template_can_be_chosen_again(qtbot: object, tmp_path: Path) -> None:
    dialog = make_dialog(qtbot, tmp_path)
    choose_template(dialog, "Summer Blue, morning")
    dialog.save_template("Regatta")
    assert dialog.template_box.currentText() == "Regatta"
    again = make_dialog(qtbot, tmp_path)
    choose_template(again, "Regatta")
    assert [item["name"] for item in again._items] == ["All classes", "N18"]


def test_invalid_sequences_cant_be_saved_as_a_template(
    qtbot: object, tmp_path: Path
) -> None:
    dialog = make_dialog(qtbot, tmp_path)
    dialog.add_sequence()
    dialog.flag_edit.setText("")
    dialog.save_template("Broken")
    assert not (tmp_path / "my_templates.json").exists()


class FakeSource:
    """Stand in for the camera."""

    def __init__(self, config: Config, secrets: Secrets) -> None:
        """Make a frame."""
        stream = config.stream
        image = np.full((stream.height, stream.width, 3), 90, np.uint8)
        self.frame = Frame(image, 0.0, 1, True)

    def start(self) -> None:
        """Do nothing."""

    def stop(self) -> None:
        """Do nothing."""

    def latest(self) -> Frame:
        """Return the frame."""
        return self.frame


class FakeStreaming:
    """Stand in for the stream."""

    def __init__(self, *_: object) -> None:
        """Do nothing."""

    def start(self) -> None:
        """Do nothing."""

    def stop(self) -> None:
        """Do nothing."""


def make_window(qtbot: object, tmp_path: Path) -> MainWindow:
    """Return a started window."""

    def make_session(config: Config) -> GuiSession:
        return GuiSession(
            config, Secrets(), make_source=FakeSource, make_streaming=FakeStreaming
        )

    config = Config(
        stream=StreamConfig(width=64, height=36),
        control=ControlConfig(journal_path=str(tmp_path / "j.jsonl")),
    )
    window = MainWindow(
        make_session(config), ConfigDocument(config), make_session=make_session
    )
    qtbot.addWidget(window)
    window.start()
    window._timer.stop()
    return window


def test_new_sequences_are_used_and_can_be_saved(
    qtbot: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window(qtbot, tmp_path)
    sequences = template("Summer Blue, afternoon")

    class Answer:
        result_config = sequences

        def exec(self) -> int:
            return 1

    monkeypatch.setattr(MainWindow, "_sequence_editor", lambda self: Answer())
    window._open_sequence_editor()
    window._timer.stop()
    assert window._session.config.sequences == sequences
    assert window._document.modified
    path = tmp_path / "c.json"
    window._document.save(path)
    assert load_config(path).sequences == sequences


def test_sequences_cant_change_while_streaming(
    qtbot: object, tmp_path: Path
) -> None:
    window = make_window(qtbot, tmp_path)
    problems: list[str] = []
    window._show_problem = problems.append
    window._session.start_stream()
    window._open_sequence_editor()
    assert "Stop the stream" in problems[0]
    window._session.stop_stream()


def test_real_editor_opens_from_the_window(qtbot: object, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path)
    dialog = window._sequence_editor()
    qtbot.addWidget(dialog)
    assert dialog.template_box.count() >= 7
