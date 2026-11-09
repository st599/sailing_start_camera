#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_events.py
#  Description: Tests for events and the watcher that makes them.
#  Date: 2026-11-09
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: pytest
#
#########################################################

"""Tests for events and the watcher that makes them."""

#
# SYSTEM IMPORTS
#

import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

#
# PROJECT IMPORTS
#
from start_camera.config import SequenceConfig, SequencesConfig
from start_camera.control import RaceControl
from start_camera.events import Event, EventHub, EventKind
from start_camera.watcher import SystemState, Watcher

#
#  GLOBAL VARIABLES
#

T0 = datetime(2026, 11, 9, 12, 0).timestamp()
MINUTE = 60.0
WAIT_S = 5.0

#
# FUNCTIONS
#


class Clock:
    """A clock that a test can set."""

    def __init__(self) -> None:
        """Start at T0."""
        self.now = T0

    def __call__(self) -> float:
        """Return the time."""
        return self.now


def sequences(*names: str) -> SequencesConfig:
    """Return sequences named after their class flags."""
    return SequencesConfig(
        items=[SequenceConfig(name=name, class_flag=name) for name in names]
    )


class Setup:
    """Race controls, a watcher and its events, on a settable clock."""

    def __init__(self, tmp_path: Path, *names: str) -> None:
        """Make them."""
        self.clock = Clock()
        self.system = SystemState(False, False, True)
        self.control = RaceControl(
            sequences(*names), tmp_path / "journal.jsonl", self.clock
        )
        self.hub = EventHub()
        self.watcher = Watcher(self.control, self.hub, self.clock, lambda: self.system)

    def at(self, moment: float) -> list[Event]:
        """Check at a moment, returning the new events."""
        seen = len(self.hub.since(0))
        self.clock.now = moment
        self.watcher.check()
        return self.hub.since(0)[seen:]


def summary(events: list[Event]) -> list[tuple[str, object]]:
    """Return each event's kind and its main detail."""
    return [
        (event.kind.value, event.details.get("flag", event.details.get("phase")))
        for event in events
    ]


def test_every_flag_change_has_one_event_at_its_time(tmp_path: Path) -> None:
    setup = Setup(tmp_path, "A", "B")
    setup.control.start_now()
    for seconds in range(0, 12 * 60, 7):
        setup.at(T0 + seconds)
    events = setup.hub.since(0)
    flags = [e for e in events if e.kind in (EventKind.FLAG_UP, EventKind.FLAG_DOWN)]
    expected = [
        ("flag_up", "A", 0),
        ("flag_up", "P", 1),
        ("flag_down", "P", 4),
        ("flag_down", "A", 5),
        ("flag_up", "B", 5),
        ("flag_up", "P", 6),
        ("flag_down", "P", 9),
        ("flag_down", "B", 10),
    ]
    assert [(e.kind.value, e.details["flag"]) for e in flags] == [
        (kind, flag) for kind, flag, _ in expected
    ]
    for event, (_, _, minutes) in zip(flags, expected, strict=True):
        assert event.time == pytest.approx(T0 + minutes * MINUTE, abs=0.002)
    starts = [e for e in events if e.kind is EventKind.START]
    assert [(e.details["name"], e.time) for e in starts] == [
        ("A", pytest.approx(T0 + 5 * MINUTE, abs=0.002)),
        ("B", pytest.approx(T0 + 10 * MINUTE, abs=0.002)),
    ]


def test_numbers_increase_by_one(tmp_path: Path) -> None:
    setup = Setup(tmp_path, "A")
    setup.control.start_now()
    setup.at(T0 + 6 * MINUTE)
    numbers = [event.number for event in setup.hub.since(0)]
    assert numbers == list(range(1, len(numbers) + 1))
    assert setup.hub.since(numbers[-2]) == setup.hub.since(0)[-1:]


def test_a_command_is_a_schedule_event_naming_its_source(tmp_path: Path) -> None:
    setup = Setup(tmp_path, "A")
    setup.control.with_source("phone").start_now()
    setup.control.with_source("keys").shift(2)
    schedule = [e for e in setup.hub.since(0) if e.kind is EventKind.SCHEDULE]
    assert [(e.details["command"], e.details["source"]) for e in schedule] == [
        ("start_now", "phone"),
        ("shift", "keys"),
    ]
    assert schedule[1].details["arguments"] == {"minutes": 2}
    assert schedule[1].details["upcoming"]["start"] == T0 + 7 * MINUTE


def test_a_command_comes_before_what_it_changes(tmp_path: Path) -> None:
    setup = Setup(tmp_path, "A")
    setup.control.start_now()
    setup.at(T0 + 2 * MINUTE)
    seen = len(setup.hub.since(0))
    setup.clock.now = T0 + 3 * MINUTE
    setup.control.postpone()
    events = setup.hub.since(0)[seen:]
    assert events[0].kind is EventKind.SCHEDULE
    assert events[0].details["command"] == "postpone"
    assert ("flag_up", "AP") in summary(events)
    assert ("phase", "postponed") in summary(events)
    assert all(event.time == T0 + 3 * MINUTE for event in events)


def test_changes_before_a_command_keep_their_time(tmp_path: Path) -> None:
    setup = Setup(tmp_path, "A")
    setup.control.start_now()
    setup.watcher.check()
    setup.clock.now = T0 + 2 * MINUTE
    setup.control.shift(5)
    events = setup.hub.since(0)
    up = [e for e in events if e.kind is EventKind.FLAG_UP and e.details["flag"] == "P"]
    assert up[0].time == pytest.approx(T0 + MINUTE, abs=0.002)
    schedule = [e for e in events if e.kind is EventKind.SCHEDULE]
    assert events.index(up[0]) < events.index(schedule[-1])


def test_new_sequences_are_an_event(tmp_path: Path) -> None:
    setup = Setup(tmp_path, "A")
    setup.control.replace_sequences(sequences("B"), "suite")
    (event,) = setup.hub.since(0)
    assert event.kind is EventKind.SEQUENCES
    assert event.details["source"] == "suite"


def test_system_changes(tmp_path: Path) -> None:
    setup = Setup(tmp_path, "A")
    setup.system = SystemState(True, True, False)
    assert [e.kind for e in setup.at(T0)] == [
        EventKind.STREAM_STARTED,
        EventKind.RECORDING_STARTED,
        EventKind.CAMERA_LOST,
    ]
    setup.system = SystemState(False, False, True)
    assert [e.kind for e in setup.at(T0 + 1)] == [
        EventKind.STREAM_STOPPED,
        EventKind.RECORDING_STOPPED,
        EventKind.CAMERA_REGAINED,
    ]


def test_clock_going_back_makes_no_events(tmp_path: Path) -> None:
    setup = Setup(tmp_path, "A")
    setup.control.start_now()
    setup.at(T0 + 2 * MINUTE)
    assert setup.at(T0 - MINUTE) == []


def test_a_slow_listener_holds_nothing_up() -> None:
    hub = EventHub()
    release = threading.Event()
    fast: list[Event] = []
    done = threading.Event()

    def slow(_: Event) -> None:
        release.wait(WAIT_S)

    def quick(event: Event) -> None:
        fast.append(event)
        if len(fast) == 50:
            done.set()

    hub.subscribe(slow)
    hub.subscribe(quick)
    began = time.monotonic()
    for number in range(50):
        hub.publish(EventKind.PHASE, T0 + number)
    assert time.monotonic() - began < 0.5
    assert done.wait(WAIT_S)
    release.set()
    hub.close()


def test_a_listener_far_behind_misses_events(caplog: pytest.LogCaptureFixture) -> None:
    hub = EventHub()
    release = threading.Event()
    hub.subscribe(lambda _: release.wait(WAIT_S))
    for number in range(1100):
        hub.publish(EventKind.PHASE, T0 + number)
    release.set()
    hub.close()
    assert "too slow" in caplog.text


def test_a_failing_listener_doesnt_stop_delivery(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hub = EventHub()
    received: list[Event] = []
    done = threading.Event()

    def flaky(event: Event) -> None:
        if event.number == 1:
            raise ValueError("broken")
        received.append(event)
        done.set()

    hub.subscribe(flaky)
    hub.publish(EventKind.PHASE, T0)
    hub.publish(EventKind.PHASE, T0 + 1)
    assert done.wait(WAIT_S)
    assert [event.number for event in received] == [2]
    assert "An event listener failed" in caplog.text
    hub.close()


def test_unsubscribing_stops_the_calls() -> None:
    hub = EventHub()
    received: list[Event] = []
    unsubscribe = hub.subscribe(received.append)
    unsubscribe()
    hub.publish(EventKind.PHASE, T0)
    time.sleep(0.05)
    assert received == []


def test_history_is_limited() -> None:
    hub = EventHub()
    for number in range(1500):
        hub.publish(EventKind.PHASE, T0 + number)
    events = hub.since(0)
    assert len(events) == 1000
    assert events[0].number == 501


def test_details_cant_be_changed() -> None:
    event = EventHub().publish(EventKind.FLAG_UP, T0, flag="A")
    with pytest.raises(TypeError):
        event.details["flag"] = "B"  # type: ignore[index]


def test_the_watcher_checks_by_itself(tmp_path: Path) -> None:
    setup = Setup(tmp_path, "A")
    noticed = threading.Event()
    setup.hub.subscribe(lambda event: noticed.set())
    setup.watcher.start()
    try:
        setup.system = SystemState(True, False, True)
        assert noticed.wait(WAIT_S)
    finally:
        setup.watcher.stop()
    assert setup.hub.since(0)[0].kind is EventKind.STREAM_STARTED
