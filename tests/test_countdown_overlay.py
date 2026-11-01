#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_countdown_overlay.py
#  Description: Tests for the countdown overlay.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, pytest
#
#########################################################

"""Tests for the countdown overlay, using a fake clock."""

#
# SYSTEM IMPORTS
#

from datetime import datetime, time

import numpy as np
import pytest

#
# PROJECT IMPORTS
#
from start_camera.config import (
    Config,
    Corner,
    CountdownOverlayConfig,
    OverlaysConfig,
    SequenceConfig,
    SequencesConfig,
)
from start_camera.ingest import Frame
from start_camera.overlays.compositor import Compositor, OverlayContext
from start_camera.overlays.countdown import (
    CountdownOverlay,
    format_count_up,
    format_countdown,
)
from start_camera.overlays.factory import build_compositor
from start_camera.race_clock import RaceClock

#
#  GLOBAL VARIABLES
#

T0 = datetime(2026, 11, 1, 12, 0).timestamp()
WIDTH = 1280
HEIGHT = 720
DOWN_COLOUR = "#ffffff"
UP_COLOUR = "#80ff80"
START = T0 + 300

#
# FUNCTIONS
#


def sequences(*names: str, **options: object) -> SequencesConfig:
    """Return sequences named after their class flags."""
    items = [SequenceConfig(name=name, class_flag=name) for name in names]
    return SequencesConfig(items=items, **options)


def race(*names: str) -> RaceClock:
    """Return a race clock with the first warning at T0 and the first start 5 min on."""
    clock = RaceClock(sequences(*names), T0)
    clock.start_now(T0)
    return clock


def overlay(clock: RaceClock, **options: object) -> CountdownOverlay:
    """Return an overlay for a race clock."""
    return CountdownOverlay(CountdownOverlayConfig(**options), clock)


def context(now: float) -> OverlayContext:
    """Return a context at a given time."""
    return OverlayContext(now, WIDTH, HEIGHT, True)


def shown(clock: RaceClock, now: float, **options: object) -> str | None:
    """Return the text on display at a given time."""
    display = overlay(clock, **options).state(context(now))
    return None if display is None else display.text


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (300, "-05:00"),
        (299.99, "-05:00"),
        (299, "-04:59"),
        (61, "-01:01"),
        (60, "-01:00"),
        (1, "-00:01"),
        (0.01, "-00:01"),
        (6000, "-100:00"),
    ],
)
def test_countdown_format(seconds: float, text: str) -> None:
    assert format_countdown(seconds) == text


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0, "+00:00"),
        (0.99, "+00:00"),
        (1, "+00:01"),
        (59.99, "+00:59"),
        (60, "+01:00"),
        (5400, "+90:00"),
    ],
)
def test_count_up_format(seconds: float, text: str) -> None:
    assert format_count_up(seconds) == text


def test_every_second_shows_once_through_the_start() -> None:
    clock = race("A")
    texts = []
    for step in range(-200, 201):
        text = shown(clock, START + step * 0.05)
        if not texts or texts[-1] != text:
            texts.append(text)
    expected = [f"A -00:{n:02d}" for n in range(10, 0, -1)]
    expected += [f"A +00:{n:02d}" for n in range(0, 11)]
    assert texts == expected


@pytest.mark.parametrize("offset", [0.25, 0.5, 0.999])
def test_start_between_seconds_is_still_counted_correctly(offset: float) -> None:
    clock = RaceClock(sequences("A"), T0)
    clock.start_now(T0 + offset)
    start = T0 + offset + 300
    assert shown(clock, start - 0.01) == "A -00:01"
    assert shown(clock, start) == "A +00:00"
    assert shown(clock, start + 0.99) == "A +00:00"
    assert shown(clock, start + 1) == "A +00:01"


def test_counting_down_and_up_look_different() -> None:
    clock = race("A")
    down = overlay(clock).state(context(START - 1))
    up = overlay(clock).state(context(START))
    assert "-" in down.text
    assert "+" in up.text
    assert down.colour == DOWN_COLOUR
    assert up.colour == UP_COLOUR


def test_colours_are_configurable() -> None:
    clock = race("A")
    options = {"colour": "#010203", "count_up_colour": "#040506"}
    assert overlay(clock, **options).state(context(T0)).colour == "#010203"
    assert overlay(clock, **options).state(context(START)).colour == "#040506"


def test_rolling_start_goes_straight_to_the_next_countdown() -> None:
    clock = race("A", "B")
    assert shown(clock, START - 0.01) == "A -00:01"
    assert shown(clock, START) == "B -05:00"


def test_count_up_runs_after_the_last_start() -> None:
    clock = race("A", "B")
    assert shown(clock, START + 300) == "B +00:00"


def test_gap_between_classes_counts_up_then_counts_down() -> None:
    items = [
        SequenceConfig(name="A", class_flag="A"),
        SequenceConfig(name="B", class_flag="B", minutes_after_previous=8),
    ]
    clock = RaceClock(SequencesConfig(items=items), T0)
    clock.start_now(T0)
    assert shown(clock, START + 179) == "A +02:59"
    assert shown(clock, START + 180) == "B -05:00"


def test_count_up_clears_after_ninety_minutes() -> None:
    clock = race("A")
    assert shown(clock, START + 90 * 60 - 1) == "A +89:59"
    assert shown(clock, START + 90 * 60) is None
    assert shown(clock, START + 90 * 600) is None


def test_count_up_time_is_configurable() -> None:
    clock = race("A")
    assert shown(clock, START + 59, count_up_minutes=1) == "A +00:59"
    assert shown(clock, START + 60, count_up_minutes=1) is None


def test_count_up_can_be_turned_off() -> None:
    assert shown(race("A"), START, count_up_minutes=0) is None


def test_waiting_for_the_race_officer_shows_ready() -> None:
    assert shown(RaceClock(sequences("A"), T0), T0) == "READY"


def test_scheduled_start_shows_its_time_before_the_sequence() -> None:
    config = sequences("A", first_start=time(13, 0))
    clock = RaceClock(config, T0)
    first = datetime(2026, 11, 1, 13, 0).timestamp()
    assert shown(clock, T0) == "A 13:00:00"
    assert shown(clock, first - 301) == "A 13:00:00"
    assert shown(clock, first - 300) == "A -05:00"


def test_postponed_is_shown() -> None:
    clock = race("A")
    clock.postpone(T0 + 10)
    assert shown(clock, T0 + 10) == "POSTPONED"


def test_postponed_replaces_the_count_up() -> None:
    clock = race("A", "B")
    clock.postpone(START + 10)
    assert shown(clock, START + 10) == "POSTPONED"


def test_abandoned_is_shown() -> None:
    clock = race("A")
    clock.abandon(T0 + 10)
    assert shown(clock, T0 + 10) == "ABANDONED"


def test_delay_changes_the_countdown_straight_away() -> None:
    clock = race("A")
    clock.shift(T0 + 240, 2)
    assert shown(clock, T0 + 240) == "A -03:00"


def test_delay_past_the_warning_shows_the_new_start_until_the_warning() -> None:
    clock = race("A")
    clock.shift(T0 + 60, 2)
    assert shown(clock, T0 + 60) == "A 12:07:00"
    assert shown(clock, T0 + 120) == "A -05:00"


def test_general_recall_counts_down_to_the_new_start() -> None:
    clock = race("A")
    clock.general_recall(START + 30)
    # The new warning is 2 minutes after the recall.
    assert shown(clock, START + 150) == "A -05:00"


def changed_area(image: np.ndarray) -> tuple[int, int, int, int]:
    """Return the left, top, right and bottom of the pixels that differ from white."""
    rows, columns = np.nonzero((image != 255).any(axis=2))
    return columns.min(), rows.min(), columns.max(), rows.max()


def white_frame() -> Frame:
    """Return a white frame."""
    return Frame(np.full((HEIGHT, WIDTH, 3), 255, dtype=np.uint8), 0.0, 1, True)


def test_appears_in_the_top_right_by_default() -> None:
    compositor = Compositor([overlay(race("A"))], clock=lambda: T0)
    left, top, right, bottom = changed_area(compositor.render(white_frame()))
    assert left > WIDTH / 2
    assert right > WIDTH - 40
    assert top < 40
    assert bottom < HEIGHT / 4


def test_position_and_size_are_configurable() -> None:
    clock = race("A")
    small = overlay(clock).draw(context(T0))
    large = overlay(clock, scale=2, position=Corner.BOTTOM_LEFT).draw(context(T0))
    assert large.image.shape[1] > small.image.shape[1] * 1.8
    assert large.x < 40
    assert large.y > HEIGHT / 2


def test_nothing_to_show_draws_nothing() -> None:
    assert overlay(race("A")).draw(context(START + 90 * 60)) is None


def test_redraws_only_when_the_display_changes() -> None:
    now = [T0 + 0.1]
    draws = []

    class Counting(CountdownOverlay):
        def draw(self, context: OverlayContext) -> object:
            draws.append(context.now)
            return super().draw(context)

    instance = Counting(CountdownOverlayConfig(), race("A"))
    compositor = Compositor([instance], clock=lambda: now[0])
    for step in range(10):
        now[0] = T0 + 0.1 + step * 0.05
        compositor.render(white_frame())
    assert len(draws) == 1
    now[0] = T0 + 1.1
    compositor.render(white_frame())
    assert len(draws) == 2


def test_factory_adds_the_overlay_when_there_are_sequences() -> None:
    config = Config(sequences=sequences("A"))
    assert len(build_compositor(config, clock=lambda: T0)._overlays) == 2


def test_factory_leaves_it_out_with_no_sequences() -> None:
    assert len(build_compositor(Config(), clock=lambda: T0)._overlays) == 1


def test_factory_leaves_it_out_when_disabled() -> None:
    config = Config(
        sequences=sequences("A"),
        overlays=OverlaysConfig(countdown=CountdownOverlayConfig(enabled=False)),
    )
    assert len(build_compositor(config, clock=lambda: T0)._overlays) == 1


def test_factory_uses_the_race_clock_it_is_given() -> None:
    clock = race("A")
    compositor = build_compositor(
        Config(sequences=sequences("A")), clock=lambda: T0, race_clock=clock
    )
    assert compositor._overlays[1].overlay._race_clock is clock
