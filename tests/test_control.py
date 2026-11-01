#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_control.py
#  Description: Tests for the race controls and their journal.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: pytest
#
#########################################################

"""Tests for the race controls and their journal."""

#
# SYSTEM IMPORTS
#

import json
import logging
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

#
# PROJECT IMPORTS
#
from start_camera.config import (
    Config,
    ConfigError,
    ControlConfig,
    SequenceConfig,
    SequencesConfig,
    load_config,
)
from start_camera.control import RaceControl
from start_camera.overlays.factory import build_compositor
from start_camera.race_clock import Phase, RaceClockError

#
#  GLOBAL VARIABLES
#

T0 = datetime(2026, 11, 1, 12, 0).timestamp()
MINUTE = 60.0
DAY = 24 * 3600.0

#
# FUNCTIONS
#


class Clock:
    """A clock that a test can set."""

    def __init__(self, now: float = T0) -> None:
        """Set the time."""
        self.now = now

    def __call__(self) -> float:
        """Return the time."""
        return self.now


def sequences(*names: str, **options: object) -> SequencesConfig:
    """Return sequences named after their class flags."""
    items = [SequenceConfig(name=name, class_flag=name) for name in names]
    return SequencesConfig(items=items, **options)


@pytest.fixture
def journal(tmp_path: Path) -> Path:
    """Return a journal path."""
    return tmp_path / "journal.jsonl"


def records(path: Path) -> list[dict[str, Any]]:
    """Return a journal's records."""
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def started(journal: Path, clock: Clock, *names: str) -> RaceControl:
    """Return a control with the first warning at the current time."""
    control = RaceControl(sequences(*names), journal, clock)
    control.start_now()
    return control


def test_start_now_uses_the_current_time(journal: Path) -> None:
    clock = Clock()
    control = started(journal, clock, "A")
    assert control.state(T0).upcoming.start == T0 + 5 * MINUTE


@pytest.mark.parametrize(
    ("command", "check"),
    [
        (lambda c: c.postpone(), lambda s: s.phase is Phase.POSTPONED),
        (lambda c: c.abandon(), lambda s: s.phase is Phase.ABANDONED),
        (lambda c: c.shift(5), lambda s: s.upcoming.start == T0 + 10 * MINUTE),
        (lambda c: c.skip(), lambda s: s.upcoming.name == "B"),
    ],
)
def test_commands_before_a_start_change_the_state(
    journal: Path, command: Callable[..., None], check: Callable[..., bool]
) -> None:
    control = started(journal, Clock(), "A", "B")
    command(control)
    assert check(control.state(T0))


def test_resume_ends_a_postponement(journal: Path) -> None:
    clock = Clock()
    control = started(journal, clock, "A")
    control.postpone()
    clock.now += MINUTE
    control.resume()
    assert control.state(clock.now).phase is Phase.WAITING


def test_recalls_after_a_start(journal: Path) -> None:
    clock = Clock()
    control = started(journal, clock, "A")
    clock.now += 5 * MINUTE
    control.individual_recall()
    assert control.state(clock.now).flags == ("X",)
    control.clear_individual_recall()
    assert control.state(clock.now).flags == ()
    control.general_recall()
    assert "First Substitute" in control.state(clock.now).flags


@pytest.mark.parametrize(
    "command",
    [
        lambda c: c.resume(),
        lambda c: c.general_recall(),
        lambda c: c.individual_recall(),
        lambda c: c.start_now(),
        lambda c: c.shift(-10),
    ],
)
def test_invalid_commands_are_refused_and_recorded(
    journal: Path, command: Callable[..., None], caplog: pytest.LogCaptureFixture
) -> None:
    control = started(journal, Clock(), "A")
    with pytest.raises(RaceClockError):
        command(control)
    assert records(journal)[-1]["accepted"] is False
    assert "Refused" in caplog.text


def test_refused_command_changes_nothing(journal: Path) -> None:
    control = started(journal, Clock(), "A")
    before = control.state(T0)
    with pytest.raises(RaceClockError):
        control.shift(-10)
    assert control.state(T0) == before


def test_every_command_is_recorded_with_its_time(
    journal: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clock = Clock()
    with caplog.at_level(logging.INFO):
        control = started(journal, clock, "A")
        clock.now += 30
        control.shift(1)
    session, first, second = records(journal)
    assert session["record"] == "session"
    assert session["date"] == "2026-11-01"
    assert first["command"] == "start_now"
    assert first["time"] == T0
    assert first["local_time"] == "2026-11-01T12:00:00"
    assert second["command"] == "shift"
    assert second["arguments"] == {"minutes": 1}
    assert second["time"] == T0 + 30
    assert "Race command: shift" in caplog.text


def test_commands_are_forced_to_disk(
    journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced = []
    monkeypatch.setattr("os.fsync", lambda descriptor: synced.append(descriptor))
    started(journal, Clock(), "A")
    assert len(synced) == 1


def test_no_journal_is_written_until_a_command(journal: Path) -> None:
    RaceControl(sequences("A"), journal, Clock())
    assert not journal.exists()


def test_concurrent_commands_leave_the_state_consistent(journal: Path) -> None:
    clock = Clock()
    control = started(journal, clock, "A", "B")
    start = control.state(T0).upcoming.start
    errors = []

    def worker() -> None:
        try:
            for _ in range(50):
                control.shift(1)
                control.state(T0)
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert control.state(T0).upcoming.start == start + 400 * MINUTE
    commands = [item for item in records(journal) if item["record"] == "command"]
    assert len(commands) == 401


def restart(journal: Path, clock: Clock, *names: str, **options: object) -> RaceControl:
    """Return a new control, as after an application restart."""
    return RaceControl(sequences(*names, **options), journal, clock)


def test_restart_resumes_the_sequence_at_the_correct_time(journal: Path) -> None:
    clock = Clock()
    started(journal, clock, "A", "B")
    clock.now += 2 * MINUTE
    resumed = restart(journal, clock, "A", "B")
    state = resumed.state(clock.now)
    assert state.phase is Phase.IN_SEQUENCE
    assert state.upcoming.start == T0 + 5 * MINUTE
    assert state.flags == ("A", "P")


def test_restart_keeps_a_delay(journal: Path) -> None:
    clock = Clock()
    control = started(journal, clock, "A")
    clock.now += MINUTE
    control.shift(5)
    assert restart(journal, clock, "A").state(clock.now).upcoming.start == T0 + 600


def test_restart_keeps_a_postponement_through_a_passing_start(journal: Path) -> None:
    clock = Clock()
    started(journal, clock, "A").postpone()
    clock.now += 60 * MINUTE
    resumed = restart(journal, clock, "A")
    assert resumed.state(clock.now).phase is Phase.POSTPONED
    resumed.resume()
    assert resumed.state(clock.now).upcoming.start == clock.now + 6 * MINUTE


def test_restart_keeps_a_general_recall(journal: Path) -> None:
    clock = Clock()
    control = started(journal, clock, "A", "B")
    clock.now += 5.5 * MINUTE
    control.general_recall()
    original = control.state(clock.now)
    assert restart(journal, clock, "A", "B").state(clock.now) == original


def test_start_passing_during_a_restart_counts_as_having_happened(
    journal: Path,
) -> None:
    clock = Clock()
    started(journal, clock, "A", "B")
    clock.now += 6 * MINUTE
    state = restart(journal, clock, "A", "B").state(clock.now)
    assert state.latest.name == "A"
    assert state.upcoming.name == "B"


def test_refused_commands_are_not_replayed(journal: Path) -> None:
    clock = Clock()
    control = started(journal, clock, "A")
    with pytest.raises(RaceClockError):
        control.resume()
    assert restart(journal, clock, "A").state(T0).phase is Phase.IN_SEQUENCE


def test_commands_after_a_restart_continue_the_same_session(journal: Path) -> None:
    clock = Clock()
    started(journal, clock, "A")
    restart(journal, clock, "A").shift(1)
    kinds = [item["record"] for item in records(journal)]
    assert kinds == ["session", "command", "command"]
    assert restart(journal, clock, "A").state(T0).upcoming.start == T0 + 6 * MINUTE


def test_a_new_day_starts_afresh(journal: Path) -> None:
    clock = Clock()
    started(journal, clock, "A").postpone()
    clock.now += DAY
    resumed = restart(journal, clock, "A")
    assert resumed.state(clock.now).phase is Phase.IDLE
    resumed.start_now()
    assert records(journal)[-2]["date"] == "2026-11-02"
    assert restart(journal, clock, "A").state(clock.now).phase is Phase.IN_SEQUENCE


def test_changed_sequences_start_afresh(journal: Path) -> None:
    clock = Clock()
    started(journal, clock, "A").postpone()
    assert restart(journal, clock, "A", "B").state(T0).phase is Phase.IDLE


def test_only_the_latest_matching_session_counts(journal: Path) -> None:
    clock = Clock()
    started(journal, clock, "A")
    restart(journal, clock, "B").start_now()
    resumed = restart(journal, clock, "A")
    assert resumed.state(T0).phase is Phase.IDLE


def test_damaged_line_from_a_crash_is_skipped(
    journal: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clock = Clock()
    started(journal, clock, "A")
    with journal.open("a", encoding="utf-8") as file:
        file.write('{"record": "comm')
    resumed = restart(journal, clock, "A")
    assert resumed.state(T0).phase is Phase.IN_SEQUENCE
    assert "damaged line" in caplog.text


def test_unknown_command_in_the_journal_is_skipped(
    journal: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clock = Clock()
    started(journal, clock, "A")
    with journal.open("a", encoding="utf-8") as file:
        file.write(
            '{"record": "command", "accepted": true, "time": 1, "command": "explode"}\n'
        )
    assert restart(journal, clock, "A").state(T0).phase is Phase.IN_SEQUENCE
    assert "Couldn't replay explode" in caplog.text


def test_unreadable_journal_starts_afresh(journal: Path) -> None:
    journal.write_bytes(b"\xff\xfe")
    assert restart(journal, Clock(), "A").state(T0).phase is Phase.IDLE


def test_unwritable_journal_doesnt_stop_race_control(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    missing = tmp_path / "missing" / "journal.jsonl"
    control = RaceControl(sequences("A"), missing, Clock())
    control.start_now()
    assert control.state(T0).phase is Phase.IN_SEQUENCE
    assert "Can't write the race journal" in caplog.text


def test_delay_steps_default_to_the_clubs_choice() -> None:
    assert ControlConfig().delay_steps == [1, 5, 10, -1]


def test_a_delay_step_of_zero_is_rejected() -> None:
    with pytest.raises(ValidationError, match="can't be zero"):
        ControlConfig(delay_steps=[0])


def test_bad_delay_step_in_a_file_is_named(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text(
        '{"version": 1, "control": {"delay_steps": [0]}}', encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="control.delay_steps"):
        load_config(path)


def test_overlays_show_the_controls_state(journal: Path) -> None:
    clock = Clock()
    config = Config(sequences=sequences("A"))
    control = RaceControl(config.sequences, journal, clock)
    compositor = build_compositor(config, clock=clock, race_clock=control)
    overlay = compositor._overlays[1].overlay
    assert overlay._race_clock is control
