#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_api.py
#  Description: Tests for the stable Python interface.
#  Date: 2026-11-09
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, pytest
#
#########################################################

"""Tests for the stable Python interface."""

#
# SYSTEM IMPORTS
#

import importlib.util
import logging
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

#
# PROJECT IMPORTS
#
from start_camera import api
from start_camera.api import (
    CameraSystem,
    ClockNotSynchronisedError,
    Event,
    EventKind,
    Phase,
    RaceClockError,
    SequencesError,
    SystemStatus,
)
from start_camera.config import (
    Config,
    ConfigError,
    ControlConfig,
    Secrets,
    SequenceConfig,
    SequencesConfig,
    StreamConfig,
    load_config,
    save_config,
)
from start_camera.control_page import _PageState
from start_camera.ingest import Frame
from start_camera.sequence_model import build_timeline
from start_camera.terminal_keys import KeyHandler

#
#  GLOBAL VARIABLES
#

T0 = datetime(2026, 11, 9, 12, 0).timestamp()
ROOT = Path(__file__).parent.parent

#
# FUNCTIONS
#


class FakeSource:
    """Stand in for the camera."""

    instances: list["FakeSource"] = []

    def __init__(self, config: Config, secrets: Secrets) -> None:
        """Make a frame to the configured size."""
        stream = config.stream
        image = np.full((stream.height, stream.width, 3), 90, np.uint8)
        self.frame = Frame(image, T0, 1, True)
        self.started = self.stopped = 0
        FakeSource.instances.append(self)

    def start(self) -> None:
        """Note the call."""
        self.started += 1

    def stop(self) -> None:
        """Note the call."""
        self.stopped += 1

    def latest(self) -> Frame:
        """Return the frame."""
        return self.frame


class FakeStreaming:
    """Stand in for the stream."""

    instances: list["FakeStreaming"] = []

    def __init__(self, *_: object) -> None:
        """Note the instance."""
        self.started = self.stopped = 0
        self.recorder = None
        FakeStreaming.instances.append(self)

    def start(self) -> None:
        """Note the call."""
        self.started += 1

    def stop(self) -> None:
        """Note the call."""
        self.stopped += 1

    def use_sequences(self, sequences: SequencesConfig) -> None:
        """Note the sequences."""
        self.sequences = sequences


@pytest.fixture(autouse=True)
def fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the camera and the stream for every test here."""
    FakeSource.instances = []
    FakeStreaming.instances = []
    monkeypatch.setattr(api, "FrameSource", FakeSource)
    monkeypatch.setattr(api, "Streaming", FakeStreaming)


def make_config(tmp_path: Path) -> Config:
    """Return a configuration with one class."""
    return Config(
        stream=StreamConfig(width=64, height=36),
        control=ControlConfig(journal_path=str(tmp_path / "journal.jsonl")),
        sequences=SequencesConfig(items=[SequenceConfig(name="Solo", class_flag="S")]),
    )


def system(
    tmp_path: Path,
    now: list[float] | None = None,
    synchronised: bool | None = True,
    config: Config | None = None,
) -> CameraSystem:
    """Return a system on a settable clock, synchronised with NTP unless asked."""
    clock = now or [T0]
    return CameraSystem(
        config or make_config(tmp_path),
        Secrets(),
        clock=lambda: clock[0],
        clock_synchronised=lambda: synchronised,
    )


def test_stable_names_are_all_there() -> None:
    for name in api.__all__:
        assert hasattr(api, name), name


def test_with_block_starts_and_stops_everything(tmp_path: Path) -> None:
    with system(tmp_path) as running:
        running.start_stream()
        assert FakeSource.instances[0].started == 1
    assert FakeSource.instances[0].stopped == 1
    assert FakeStreaming.instances[0].stopped == 1


def test_status_snapshot(tmp_path: Path) -> None:
    with system(tmp_path) as running:
        status = running.status()
        assert isinstance(status, SystemStatus)
        assert status.time == T0
        assert status.phase is Phase.IDLE
        assert status.display == "READY"
        assert status.flags == ()
        assert status.camera_live is True
        assert status.streaming is False
        assert status.recording is False


def test_race_commands(tmp_path: Path) -> None:
    now = [T0]
    with system(tmp_path, now) as running:
        running.start_now()
        status = running.status()
        assert status.phase is Phase.IN_SEQUENCE
        assert status.upcoming.name == "Solo"
        assert status.upcoming.start == T0 + 300
        assert status.flags == ("S",)
        running.shift(1)
        assert running.status().upcoming.start == T0 + 360
        running.postpone()
        assert running.status().phase is Phase.POSTPONED
        running.resume()
        assert running.status().phase is Phase.WAITING
        now[0] = running.status().upcoming.start
        running.individual_recall()
        assert "X" in running.status().flags
        running.clear_individual_recall()
        running.general_recall()
        assert "First Substitute" in running.status().flags
        running.skip()
        running.abandon()
        assert running.status().phase is Phase.ABANDONED


def test_refused_command_raises_with_a_reason(tmp_path: Path) -> None:
    with system(tmp_path) as running, pytest.raises(
        RaceClockError, match="isn't postponed"
    ):
        running.resume()


def test_commands_are_journalled(tmp_path: Path) -> None:
    with system(tmp_path) as running:
        running.start_now()
    assert "start_now" in (tmp_path / "journal.jsonl").read_text(encoding="utf-8")


def test_racing_resumes_after_a_restart(tmp_path: Path) -> None:
    with system(tmp_path) as running:
        running.start_now()
    with system(tmp_path) as again:
        assert again.status().phase is Phase.IN_SEQUENCE


def test_from_files(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    save_config(make_config(tmp_path), path)
    loaded = CameraSystem.from_files(path)
    assert loaded.config.sequences.items[0].name == "Solo"
    assert isinstance(CameraSystem.from_files(str(path)), CameraSystem)


def test_from_files_reports_a_bad_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        CameraSystem.from_files(tmp_path / "missing.json")


def test_preview_has_the_overlays(tmp_path: Path) -> None:
    with system(tmp_path) as running:
        image = running.preview()
    assert image.shape == (36, 64, 3)
    assert not np.array_equal(image, FakeSource.instances[0].frame.image)


def test_recording_needs_the_stream(tmp_path: Path) -> None:
    with system(tmp_path) as running, pytest.raises(
        api.RecordingError, match="Start the stream"
    ):
        running.start_recording()


def test_interface_doesnt_need_the_gui_library() -> None:
    code = "import sys, start_camera.api; sys.exit('PySide6' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_example_runs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "config.json"
    save_config(make_config(tmp_path), path)
    spec = importlib.util.spec_from_file_location(
        "example", ROOT / "examples" / "python_api_example.py"
    )
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    with caplog.at_level(logging.INFO):
        example.run(str(path), None, seconds=0)
    assert "Countdown: Solo -05:00" in caplog.text
    assert "Refused, as expected" in caplog.text
    assert FakeStreaming.instances[0].stopped == 1


def test_guide_lists_every_stable_name_and_method() -> None:
    guide = (ROOT / "docs" / "python_api.md").read_text(encoding="utf-8")
    public = [
        name
        for name in dir(CameraSystem)
        if not name.startswith("_") and callable(getattr(CameraSystem, name))
    ]
    for name in public:
        assert f"{name}(" in guide, name
    for name in ("SystemStatus", "RaceClockError", "ConfigError", "RecordingError"):
        assert name in guide


def test_countdown_reaches_zero_at_the_set_start(tmp_path: Path) -> None:
    now = [T0]
    start = datetime.fromtimestamp(T0 + 600, UTC)
    with system(tmp_path, now) as running:
        running.set_start(start, "Solo")
        assert running.status().upcoming.start == T0 + 600
        now[0] = T0 + 600 - 1 / 25
        assert running.status().display == "Solo -00:01"
        now[0] = T0 + 600
        assert running.status().display == "Solo +00:00"


def test_set_start_takes_seconds_since_the_epoch(tmp_path: Path) -> None:
    with system(tmp_path) as running:
        running.set_start(T0 + 600)
        assert running.status().upcoming.start == T0 + 600


def test_set_start_needs_a_time_zone(tmp_path: Path) -> None:
    with system(tmp_path) as running, pytest.raises(ValueError, match="time zone"):
        running.set_start(datetime.fromtimestamp(T0 + 600))


def test_set_start_refuses_a_past_start(tmp_path: Path) -> None:
    with system(tmp_path) as running, pytest.raises(RaceClockError, match="passed"):
        running.set_start(T0 - 1)


@pytest.mark.parametrize("synchronised", [False, None])
def test_set_start_needs_ntp(tmp_path: Path, synchronised: bool | None) -> None:
    with system(tmp_path, synchronised=synchronised) as running:
        with pytest.raises(ClockNotSynchronisedError, match="NTP"):
            running.set_start(T0 + 600)
        assert running.status().upcoming is None


def test_ntp_requirement_can_be_turned_off(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.control.require_ntp = False
    with system(tmp_path, synchronised=False, config=config) as running:
        running.set_start(T0 + 600)
        assert running.status().upcoming.start == T0 + 600


def test_clock_difference_is_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    sent = datetime.fromtimestamp(T0, UTC) + timedelta(seconds=2)
    with system(tmp_path) as running, caplog.at_level(logging.WARNING):
        running.set_start(T0 + 600, sent_at=sent)
        status = running.clock_status()
    assert status.time == T0
    assert status.synchronised is True
    assert status.last_difference == pytest.approx(-2)
    assert "2.00 seconds behind" in caplog.text


def test_small_clock_difference_isnt_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with system(tmp_path) as running, caplog.at_level(logging.WARNING):
        running.set_start(T0 + 600, sent_at=T0 - 0.2)
    assert "seconds" not in caplog.text


def test_suite_commands_are_journalled_as_the_suites(tmp_path: Path) -> None:
    with system(tmp_path) as running:
        running.set_start(T0 + 600)
        running.shift(1)
    with system(tmp_path) as again:
        assert again.status().upcoming.start == T0 + 660
    journal = (tmp_path / "journal.jsonl").read_text("utf-8")
    assert journal.count('"source": "suite"') == 2


DAY = {
    "items": [
        {"name": "Lasers", "class_flag": "L"},
        {"name": "Toppers", "class_flag": "T", "preparatory_flag": "U"},
    ]
}


def test_sequences_from_the_suite_run_as_the_timeline_shows(tmp_path: Path) -> None:
    now = [T0]
    with system(tmp_path, now) as running:
        assert running.set_sequences(DAY) == []
        running.start_now()
        first_start = running.status().upcoming.start
        rows = build_timeline(running.config.sequences)
        for moment in sorted({row.seconds for row in rows}):
            flying: set[str] = set()
            for row in rows:
                if row.seconds <= moment:
                    (flying.add if row.up else flying.discard)(row.flag)
            now[0] = first_start + moment
            assert set(running.status().flags) == flying, moment


def test_new_sequences_change_the_video_straight_away(tmp_path: Path) -> None:
    with system(tmp_path) as running:
        running.start_stream()
        running.set_sequences(SequencesConfig(items=[SequenceConfig(**DAY["items"][0])]))
        running.start_now()
        assert running.status().display == "Lasers -05:00"
        assert running.summary().flags == ("L",)
        assert FakeStreaming.instances[0].sequences.items[0].name == "Lasers"
        assert FakeStreaming.instances[0].started == 1


def test_invalid_sequences_are_refused_with_each_problem(tmp_path: Path) -> None:
    bad = {"items": [{"name": "", "class_flag": "L", "minutes_after_previous": 0}]}
    with system(tmp_path) as running:
        with pytest.raises(SequencesError) as caught:
            running.set_sequences(bad)
        assert len(caught.value.problems) == 2
        assert "items.0.name" in str(caught.value)
        assert "items.0.minutes_after_previous" in str(caught.value)
        assert running.config.sequences.items[0].name == "Solo"


def test_impossible_flags_are_refused(tmp_path: Path) -> None:
    events = [{"minutes_before_start": 1, "flag": "P", "action": "down"}]
    bad = {"items": [{"name": "A", "class_flag": "A", "events": events}]}
    with system(tmp_path) as running, pytest.raises(
        SequencesError, match="P comes down without having gone up"
    ):
        running.set_sequences(bad)


def test_warnings_are_returned(tmp_path: Path) -> None:
    day = {"items": [dict(item, minutes_after_previous=2) for item in DAY["items"]]}
    with system(tmp_path) as running:
        warnings = running.set_sequences(day)
    assert warnings == [
        "Toppers's sequence begins before Lasers starts, so their flags overlap"
    ]


def test_sequences_cant_change_while_one_is_under_way(tmp_path: Path) -> None:
    with system(tmp_path) as running:
        running.start_now()
        with pytest.raises(RaceClockError, match="in sequence"):
            running.set_sequences(DAY)
        assert running.status().upcoming.name == "Solo"
        running.set_sequences(DAY, force=True)
        assert running.status().phase is Phase.IDLE
        assert running.status().upcoming is None


def test_sending_the_same_sequences_changes_nothing(tmp_path: Path) -> None:
    with system(tmp_path) as running:
        running.set_sequences(DAY)
        running.start_now()
        running.set_sequences(DAY)
        assert running.status().phase is Phase.IN_SEQUENCE


def test_sequences_can_be_saved(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    save_config(make_config(tmp_path), path)
    with CameraSystem.from_files(path) as running:
        running.set_sequences(DAY, save=True)
    assert load_config(path).sequences.items[1].name == "Toppers"


def test_saving_needs_a_file(tmp_path: Path) -> None:
    with system(tmp_path) as running:
        with pytest.raises(ConfigError, match="no configuration file"):
            running.set_sequences(DAY, save=True)
        assert running.config.sequences.items[0].name == "Lasers"


def test_racing_with_saved_sequences_resumes_after_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    save_config(make_config(tmp_path), path)
    with CameraSystem.from_files(path) as running:
        running.set_sequences(DAY, save=True)
        running.start_now()
        start = running.status().upcoming.start
    with CameraSystem.from_files(path) as again:
        assert again.status().upcoming.start == start
    journal = (tmp_path / "journal.jsonl").read_text("utf-8")
    assert '"record": "session"' in journal and '"source": "suite"' in journal


def schedule_sources(running: CameraSystem) -> list[tuple[str, str]]:
    """Return each schedule event's command and source."""
    return [
        (event.details["command"], event.details["source"])
        for event in running.events_since()
        if event.kind is EventKind.SCHEDULE
    ]


def test_events_reach_a_listener(tmp_path: Path) -> None:
    received: list[Event] = []
    got_start = threading.Event()

    def listener(event: Event) -> None:
        received.append(event)
        if event.kind is EventKind.FLAG_UP:
            got_start.set()

    with system(tmp_path) as running:
        unsubscribe = running.subscribe(listener)
        running.start_now()
        assert got_start.wait(5)
        unsubscribe()
    assert received[0].kind is EventKind.SCHEDULE
    assert received[0].details["source"] == "suite"
    assert received[1].details["flag"] == "S"


def test_changes_at_the_start_box_name_their_source(tmp_path: Path) -> None:
    with system(tmp_path) as running:
        keys = KeyHandler(running.control.with_source("keys"), lambda: None)
        keys.handle("s")
        page = _PageState(running.config, running.control, None, lambda: T0)
        page.run_command({"command": "shift", "minutes": 1})
        running.set_sequences(DAY, force=True)
        assert schedule_sources(running) == [("start_now", "keys"), ("shift", "phone")]
        kinds = {event.kind: event for event in running.events_since()}
        assert kinds[EventKind.SEQUENCES].details["source"] == "suite"


def test_stream_events(tmp_path: Path) -> None:
    with system(tmp_path) as running:
        running.start_stream()
        running._watcher.check()
        running.stop_stream()
        running._watcher.check()
        kinds = [event.kind for event in running.events_since()]
    assert kinds == [EventKind.STREAM_STARTED, EventKind.STREAM_STOPPED]


def test_events_since_skips_those_already_seen(tmp_path: Path) -> None:
    with system(tmp_path) as running:
        running.start_now()
        first = running.events_since()
        running.shift(1)
        assert running.events_since(first[-1].number)[0].number == len(first) + 1
