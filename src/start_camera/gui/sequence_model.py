#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: sequence_model.py
#  Description: The timeline and checks for the sequence editor.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: None
#
#########################################################

"""The timeline and checks for the sequence editor, free of the GUI library."""

#
# SYSTEM IMPORTS
#

from dataclasses import dataclass, field
from datetime import datetime

#
# PROJECT IMPORTS
#
from start_camera.config import (
    CLASS_FLAG,
    PREPARATORY_FLAG,
    FlagAction,
    SequenceConfig,
    SequencesConfig,
)
from start_camera.race_clock import (
    COMBINED_FLAG_SEPARATOR,
    MINUTE_S,
    warning_lead_s,
)

#
# FUNCTIONS
#


@dataclass(frozen=True)
class TimelineRow:
    """A flag going up or down."""

    seconds: float
    time: str
    sequence: str
    flag: str
    up: bool
    is_start: bool

    def describe(self) -> str:
        """Return the row as a line of text."""
        action = "up" if self.up else "down"
        start = " (start)" if self.is_start else ""
        return f"{self.time}  {self.sequence}: {self.flag} {action}{start}"


@dataclass
class SequenceProblems:
    """What's wrong with some sequences."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _start_times(config: SequencesConfig) -> list[float]:
    """Return each start's time in seconds, from the first start."""
    starts: list[float] = []
    for item in config.items:
        gap = item.minutes_after_previous * MINUTE_S
        starts.append(starts[-1] + gap if starts else 0.0)
    return starts


def _format_time(seconds: float, config: SequencesConfig) -> str:
    """Return a time of day, or a time from the first start if that isn't scheduled."""
    if config.first_start is not None:
        base = datetime.combine(datetime(2000, 1, 2).date(), config.first_start)
        moment = datetime.fromtimestamp(base.timestamp() + seconds)
        return moment.strftime("%H:%M:%S")
    sign = "-" if seconds < 0 else "+"
    minutes, remainder = divmod(round(abs(seconds)), 60)
    return f"T{sign}{minutes:02d}:{remainder:02d}"


def _flags_of(item: SequenceConfig, flag: str) -> list[str]:
    """Return the real flags that an event's flag stands for."""
    if flag == CLASS_FLAG:
        name = item.class_flag
    elif flag == PREPARATORY_FLAG:
        name = item.preparatory_flag.value
    else:
        name = flag
    return name.split(COMBINED_FLAG_SEPARATOR)


def build_timeline(config: SequencesConfig) -> list[TimelineRow]:
    """Return what flies when, in order, with flags coming down before others go up."""
    rows = []
    for item, start in zip(config.items, _start_times(config), strict=True):
        for event in item.events:
            seconds = start - event.minutes_before_start * MINUTE_S
            up = event.action is FlagAction.UP
            for flag in _flags_of(item, event.flag):
                rows.append(
                    TimelineRow(
                        seconds,
                        _format_time(seconds, config),
                        item.name,
                        flag,
                        up,
                        event.minutes_before_start == 0 and not up,
                    )
                )
    return sorted(rows, key=lambda row: (row.seconds, row.up))


def _check_events(item: SequenceConfig, problems: SequenceProblems) -> None:
    """Check that each flag in a sequence goes up, then comes down."""
    events = sorted(item.events, key=lambda event: -event.minutes_before_start)
    seen: set[tuple[float, str]] = set()
    flying: dict[str, bool] = {}
    for event in events:
        moment = (event.minutes_before_start, event.flag)
        if moment in seen:
            problems.errors.append(
                f"{item.name}: {event.flag} has two events at "
                f"{event.minutes_before_start:g} minutes"
            )
        seen.add(moment)
        up = event.action is FlagAction.UP
        was_up = flying.get(event.flag, False)
        if up and was_up:
            problems.errors.append(f"{item.name}: {event.flag} goes up twice")
        if not up and not was_up:
            problems.errors.append(
                f"{item.name}: {event.flag} comes down without having gone up"
            )
        flying[event.flag] = up
    for flag, up in flying.items():
        if up:
            problems.warnings.append(
                f"{item.name}: {flag} doesn't come down, so it flies until the start"
            )


def _check_overlaps(config: SequencesConfig, problems: SequenceProblems) -> None:
    """Warn where a sequence begins before the previous class has started."""
    for previous, item in zip(config.items, config.items[1:], strict=False):
        lead_minutes = warning_lead_s(item) / MINUTE_S
        if item.minutes_after_previous < lead_minutes:
            problems.warnings.append(
                f"{item.name}'s sequence begins before {previous.name} starts, so "
                "their flags overlap"
            )


def check_sequences(config: SequencesConfig) -> SequenceProblems:
    """Return what's impossible in the sequences, and what's merely worth checking."""
    problems = SequenceProblems()
    for item in config.items:
        _check_events(item, problems)
    _check_overlaps(config, problems)
    return problems
