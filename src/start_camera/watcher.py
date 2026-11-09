#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: watcher.py
#  Description: Turns changes in the racing and the system into events.
#  Date: 2026-11-09
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: None
#
#########################################################

"""Turns changes in the racing and the system into events.

The race clock works out its flags from the time, so nothing happens when a flag goes
up. The watcher checks often, and when something has changed it narrows down when, so
each event has the moment it happened rather than when it was noticed. Commands are
watched as they're made, so a flag that came down just before a command still gets
its event, and every change from a command follows the event for the command.
"""

#
# SYSTEM IMPORTS
#

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

#
# PROJECT IMPORTS
#
from start_camera.control import RaceControl
from start_camera.events import EventHub, EventKind
from start_camera.race_clock import RaceState, ScheduledStart

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

CHECK_INTERVAL_S = 0.05
PRECISION_S = 0.001
MAX_CHANGES_PER_CHECK = 100
SCHEDULE_COMMANDS_KIND = {"set_sequences": EventKind.SEQUENCES}

#
# FUNCTIONS
#


@dataclass(frozen=True)
class SystemState:
    """Whether the stream and recording are running, and the camera is live."""

    streaming: bool
    recording: bool
    camera_live: bool


SYSTEM_EVENTS = {
    "streaming": (EventKind.STREAM_STARTED, EventKind.STREAM_STOPPED),
    "recording": (EventKind.RECORDING_STARTED, EventKind.RECORDING_STOPPED),
    "camera_live": (EventKind.CAMERA_REGAINED, EventKind.CAMERA_LOST),
}


def _start_details(start: ScheduledStart | None) -> dict[str, object] | None:
    """Return a start as event details."""
    if start is None:
        return None
    return {"name": start.name, "class_flag": start.class_flag, "start": start.start}


def _key(state: RaceState) -> tuple[object, ...]:
    """Return what an event is made for when it changes."""
    return (state.phase, state.flags, state.latest)


class Watcher:
    """Publishes an event for each change in the racing and the system."""

    def __init__(
        self,
        control: RaceControl,
        hub: EventHub,
        clock: Callable[[], float],
        system_state: Callable[[], SystemState],
    ) -> None:
        """Note the racing and the system as they are now, without any events.

        Args:
            control: The race controls to watch.
            hub: Where to publish events.
            clock: Provides the current time.
            system_state: Returns the system's state.
        """
        self._control = control
        self._hub = hub
        self._clock = clock
        self._system_state = system_state
        self._lock = threading.Lock()
        self._time = clock()
        self._race = control.state(self._time)
        self._system = system_state()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        control.observe(self)

    def start(self) -> None:
        """Check for changes on a thread of its own."""
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="event-watcher", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop checking."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def check(self) -> None:
        """Publish events for anything that has changed."""
        now = self._clock()
        with self._control.locked():
            self._catch_up(now)
        self._check_system(now)

    def before_change(self, now: float) -> None:
        """Publish what changed before a command, with the racing as it was."""
        self._catch_up(now)

    def after_change(
        self, command: str, source: str, arguments: dict[str, object], now: float
    ) -> None:
        """Publish a command, then what it changed straight away."""
        with self._lock:
            state = self._control.state(now)
            kind = SCHEDULE_COMMANDS_KIND.get(command, EventKind.SCHEDULE)
            self._hub.publish(
                kind,
                now,
                command=command,
                source=source,
                arguments=dict(arguments),
                upcoming=_start_details(state.upcoming),
            )
            self._publish_changes(self._race, state, now, now)
            self._race, self._time = state, now

    def _run(self) -> None:
        """Check until stopped."""
        while not self._stop.wait(CHECK_INTERVAL_S):
            try:
                self.check()
            except Exception:
                logger.exception("Couldn't check for events")

    def _catch_up(self, now: float) -> None:
        """Publish each change in the racing up to a moment, at the time it happened.

        The caller holds the race controls' lock, so the racing can't change meanwhile.
        """
        with self._lock:
            if now < self._time:
                # The clock went back: start again from here, without events.
                self._race, self._time = self._control.state(now), now
                return
            for _ in range(MAX_CHANGES_PER_CHECK):
                state = self._control.state(now)
                if _key(state) == _key(self._race):
                    break
                moment = self._first_change(now)
                changed = self._control.state(moment)
                self._publish_changes(self._race, changed, self._time, moment)
                self._race, self._time = changed, moment
            self._race, self._time = self._control.state(now), now

    def _first_change(self, now: float) -> float:
        """Return about when the racing first changed after the last check."""
        before, after = self._time, now
        key = _key(self._race)
        while after - before > PRECISION_S:
            middle = (before + after) / 2
            if _key(self._control.state(middle)) == key:
                before = middle
            else:
                after = middle
        return after

    def _publish_changes(
        self, old: RaceState, new: RaceState, since: float, now: float
    ) -> None:
        """Publish events for the differences between two states."""
        for flag in old.flags:
            if flag not in new.flags:
                self._hub.publish(EventKind.FLAG_DOWN, now, flag=flag)
        if new.latest != old.latest and new.latest is not None:
            if since < new.latest.start <= now:
                self._hub.publish(EventKind.START, now, **_start_details(new.latest))
        for flag in new.flags:
            if flag not in old.flags:
                self._hub.publish(EventKind.FLAG_UP, now, flag=flag)
        if new.phase != old.phase:
            self._hub.publish(EventKind.PHASE, now, phase=new.phase.value)

    def _check_system(self, now: float) -> None:
        """Publish changes in the stream, recording and camera."""
        state = self._system_state()
        with self._lock:
            old, self._system = self._system, state
        for name, (on, off) in SYSTEM_EVENTS.items():
            value = getattr(state, name)
            if value != getattr(old, name):
                self._hub.publish(on if value else off, now)
