#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: race_clock.py
#  Description: Work out the state of the start sequences.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: None
#
#########################################################

"""Work out the state of the start sequences.

This is pure logic. Every method takes the time as an argument, and the state is worked
out from that time and the stored start times, so the result is always well defined,
even if the clock jumps.
"""

#
# SYSTEM IMPORTS
#

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

#
# PROJECT IMPORTS
#
from start_camera.config import (
    CLASS_FLAG,
    PREPARATORY_FLAG,
    FlagAction,
    GeneralRecallMode,
    SequenceConfig,
    SequencesConfig,
)

#
#  GLOBAL VARIABLES
#

POSTPONEMENT_FLAG = "AP"
GENERAL_RECALL_FLAG = "First Substitute"
INDIVIDUAL_RECALL_FLAG = "X"
ABANDONMENT_FLAG = "N"
MINUTE_S = 60.0
RESUME_TO_WARNING_S = MINUTE_S
RECALL_FLAG_TO_WARNING_S = MINUTE_S
INDIVIDUAL_RECALL_MAX_S = 4 * MINUTE_S
INDIVIDUAL_RECALL_BEFORE_NEXT_S = MINUTE_S

#
# FUNCTIONS
#


class RaceClockError(Exception):
    """A command can't be carried out in the current state."""


class Phase(Enum):
    """The overall state of the start sequences."""

    IDLE = "idle"
    WAITING = "waiting"
    IN_SEQUENCE = "in sequence"
    FINISHED = "finished"
    POSTPONED = "postponed"
    ABANDONED = "abandoned"


@dataclass(frozen=True)
class ScheduledStart:
    """A class and when it starts, as seconds since the epoch."""

    name: str
    class_flag: str
    start: float


@dataclass(frozen=True)
class RaceState:
    """The state of the start sequences at a moment."""

    phase: Phase
    flags: tuple[str, ...]
    upcoming: ScheduledStart | None
    latest: ScheduledStart | None


@dataclass
class _Entry:
    """A sequence in the queue. A start of None means it hasn't been scheduled."""

    sequence: SequenceConfig
    start: float | None = None


def warning_lead_s(sequence: SequenceConfig) -> float:
    """Return how long before its start a sequence's first event is."""
    return max(event.minutes_before_start for event in sequence.events) * MINUTE_S


def _flag_name(sequence: SequenceConfig, flag: str) -> str:
    """Return the real name of a flag in a sequence's events."""
    if flag == CLASS_FLAG:
        return sequence.class_flag
    if flag == PREPARATORY_FLAG:
        return sequence.preparatory_flag.value
    return flag


def _sequence_flags(entry: _Entry, now: float) -> list[str]:
    """Return the flags flying for an entry. Nothing flies once it has started."""
    if entry.start is None or now >= entry.start:
        return []
    flying: dict[str, bool] = {}
    events = sorted(
        entry.sequence.events, key=lambda event: -event.minutes_before_start
    )
    for event in events:
        if now >= entry.start - event.minutes_before_start * MINUTE_S:
            name = _flag_name(entry.sequence, event.flag)
            flying[name] = event.action is FlagAction.UP
    return [name for name, up in flying.items() if up]


def first_start_time(config: SequencesConfig, today: date) -> float | None:
    """Return when the first start is on a given day, if one is scheduled."""
    if config.first_start is None:
        return None
    return datetime.combine(today, config.first_start).timestamp()


class RaceClock:
    """The queue of starts, and the commands that change it."""

    def __init__(self, config: SequencesConfig, now: float) -> None:
        """Set up the day's sequences.

        Args:
            config: The sequences.
            now: The current time, which also decides which day first_start is on.
        """
        self._config = config
        self._entries = [_Entry(sequence) for sequence in config.items]
        self._postponed = False
        self._abandoned = False
        self._general_recall_until = 0.0
        self._individual_recall_until = 0.0
        first = first_start_time(config, datetime.fromtimestamp(now).date())
        if first is not None:
            self._schedule_from(0, first)

    def state(self, now: float) -> RaceState:
        """Return the state at a moment."""
        upcoming = self._pending(now)
        return RaceState(
            phase=self._phase(now, upcoming),
            flags=self._flags(now),
            upcoming=None if self._held else self._as_start(upcoming),
            latest=self._as_start(self._latest(now)),
        )

    def start_now(self, now: float) -> None:
        """Bring the next start forward so its warning signal is now.

        After an abandonment, this runs every sequence again from the first.
        """
        if self._postponed:
            raise RaceClockError("The start is postponed; resume it instead")
        if self._abandoned:
            self._abandoned = False
            self._entries = [_Entry(item) for item in self._config.items]
        entry = self._require_pending(now)
        lead = warning_lead_s(entry.sequence)
        if entry.start is not None and entry.start - lead <= now:
            raise RaceClockError(f"The sequence for {entry.sequence.name} is running")
        self._schedule_from(self._entries.index(entry), now + lead)

    def shift(self, now: float, minutes: float) -> None:
        """Move every start that hasn't happened by a number of minutes."""
        self._require_running()
        entry = self._require_pending(now)
        if entry.start is None:
            raise RaceClockError("The first start hasn't been set")
        if entry.start + minutes * MINUTE_S <= now:
            raise RaceClockError("That would put the next start in the past")
        for item in self._entries[self._entries.index(entry) :]:
            item.start += minutes * MINUTE_S

    def postpone(self, now: float) -> None:
        """Hold all starts that haven't happened, and fly the postponement flag."""
        self._require_running()
        self._require_pending(now)
        self._clear_pending_times(now)
        self._postponed = True

    def resume(self, now: float) -> None:
        """End a postponement. The next warning signal is one minute later."""
        if not self._postponed:
            raise RaceClockError("The start isn't postponed")
        self._postponed = False
        entry = self._require_pending(now)
        start = now + RESUME_TO_WARNING_S + warning_lead_s(entry.sequence)
        self._schedule_from(self._entries.index(entry), start)

    def general_recall(self, now: float) -> None:
        """Recall the most recent class, which then starts again."""
        self._require_running()
        entry = self._require_latest(now)
        self._general_recall_until = now + self._config.recall_flag_minutes * MINUTE_S
        self._individual_recall_until = 0.0
        earliest = (
            self._general_recall_until
            + RECALL_FLAG_TO_WARNING_S
            + warning_lead_s(entry.sequence)
        )
        if self._config.general_recall is GeneralRecallMode.DELAY_FOLLOWING:
            self._schedule_from(self._entries.index(entry), earliest)
        else:
            self._move_to_end(entry, earliest)

    def individual_recall(self, now: float) -> None:
        """Fly the individual recall flag for the most recent class."""
        self._require_running()
        entry = self._require_latest(now)
        until = entry.start + INDIVIDUAL_RECALL_MAX_S
        upcoming = self._pending(now)
        if upcoming is not None and upcoming.start is not None:
            until = min(until, upcoming.start - INDIVIDUAL_RECALL_BEFORE_NEXT_S)
        self._individual_recall_until = until

    def clear_individual_recall(self) -> None:
        """Take down the individual recall flag."""
        self._individual_recall_until = 0.0

    def skip(self, now: float) -> None:
        """Remove the next start. The others stay at the same times."""
        self._require_running()
        self._entries.remove(self._require_pending(now))

    def abandon(self, now: float) -> None:
        """Stop all starts and fly the abandonment flag."""
        if self._abandoned:
            raise RaceClockError("The racing is already abandoned")
        self._clear_pending_times(now)
        self._abandoned = True
        self._postponed = False
        self._general_recall_until = 0.0
        self._individual_recall_until = 0.0

    @property
    def _held(self) -> bool:
        """Return whether starts are being held."""
        return self._postponed or self._abandoned

    def _phase(self, now: float, upcoming: _Entry | None) -> Phase:
        """Return the overall state."""
        if self._abandoned:
            return Phase.ABANDONED
        if self._postponed:
            return Phase.POSTPONED
        if not self._entries:
            return Phase.IDLE
        if upcoming is None:
            return Phase.FINISHED
        if upcoming.start is None:
            return Phase.IDLE
        if now >= upcoming.start - warning_lead_s(upcoming.sequence):
            return Phase.IN_SEQUENCE
        return Phase.WAITING

    def _flags(self, now: float) -> tuple[str, ...]:
        """Return all the flags flying, without repeats."""
        flags: list[str] = []
        if self._abandoned:
            flags.append(ABANDONMENT_FLAG)
        elif self._postponed:
            flags.append(POSTPONEMENT_FLAG)
        else:
            for entry in self._entries:
                flags.extend(_sequence_flags(entry, now))
        if now < self._general_recall_until:
            flags.append(GENERAL_RECALL_FLAG)
        if now < self._individual_recall_until:
            flags.append(INDIVIDUAL_RECALL_FLAG)
        return tuple(dict.fromkeys(flags))

    def _pending(self, now: float) -> _Entry | None:
        """Return the next entry that hasn't started."""
        for entry in self._entries:
            if entry.start is None or entry.start > now:
                return entry
        return None

    def _latest(self, now: float) -> _Entry | None:
        """Return the entry that started most recently."""
        started = [
            entry
            for entry in self._entries
            if entry.start is not None and entry.start <= now
        ]
        return max(started, key=lambda entry: entry.start, default=None)

    def _as_start(self, entry: _Entry | None) -> ScheduledStart | None:
        """Return an entry's public description, if it has a time."""
        if entry is None or entry.start is None:
            return None
        return ScheduledStart(
            entry.sequence.name, entry.sequence.class_flag, entry.start
        )

    def _clear_pending_times(self, now: float) -> None:
        """Forget the times of held starts, so they can't appear to have happened."""
        for entry in self._entries:
            if entry.start is not None and entry.start > now:
                entry.start = None

    def _schedule_from(self, index: int, start: float) -> None:
        """Set a start time, with later entries following at their normal spacing."""
        for position, entry in enumerate(self._entries[index:]):
            if position:
                start += entry.sequence.minutes_after_previous * MINUTE_S
            entry.start = start

    def _move_to_end(self, entry: _Entry, earliest: float) -> None:
        """Send an entry to the back of the queue."""
        self._entries.remove(entry)
        last = max(
            (item.start for item in self._entries if item.start is not None),
            default=None,
        )
        entry.start = earliest
        if last is not None:
            spaced = last + entry.sequence.minutes_after_previous * MINUTE_S
            entry.start = max(earliest, spaced)
        self._entries.append(entry)

    def _require_running(self) -> None:
        """Refuse a command during a postponement or after an abandonment."""
        if self._postponed:
            raise RaceClockError("The start is postponed")
        if self._abandoned:
            raise RaceClockError("The racing is abandoned")

    def _require_pending(self, now: float) -> _Entry:
        """Return the next entry, or refuse if there isn't one."""
        entry = self._pending(now)
        if entry is None:
            raise RaceClockError("There are no more starts")
        return entry

    def _require_latest(self, now: float) -> _Entry:
        """Return the most recent start, or refuse if there isn't one."""
        entry = self._latest(now)
        if entry is None:
            raise RaceClockError("No class has started")
        return entry
