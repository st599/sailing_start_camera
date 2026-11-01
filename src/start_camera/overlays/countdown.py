#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: countdown.py
#  Description: The countdown and count-up overlay.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: None
#
#########################################################

"""The countdown and count-up overlay."""

#
# SYSTEM IMPORTS
#

import math
import time
from dataclasses import dataclass

#
# PROJECT IMPORTS
#
from start_camera.config import CountdownOverlayConfig
from start_camera.overlays.compositor import Layer, OverlayContext, corner_position
from start_camera.overlays.text import draw_text_panel
from start_camera.race_clock import Phase, RaceClock, RaceState

#
#  GLOBAL VARIABLES
#

READY_TEXT = "READY"
POSTPONED_TEXT = "POSTPONED"
ABANDONED_TEXT = "ABANDONED"
MINUTE_S = 60

#
# FUNCTIONS
#


@dataclass(frozen=True)
class _Display:
    """What the overlay shows."""

    text: str
    colour: str


def format_countdown(seconds_to_start: float) -> str:
    """Return the time before a start, as "-MM:SS".

    Rounding up means the display reaches 00:00 only at the start itself.
    """
    minutes, seconds = divmod(math.ceil(seconds_to_start), MINUTE_S)
    return f"-{minutes:02d}:{seconds:02d}"


def format_count_up(seconds_since_start: float) -> str:
    """Return the time since a start, as "+MM:SS".

    Rounding down means 00:00 shows for the whole first second after the start.
    """
    minutes, seconds = divmod(math.floor(seconds_since_start), MINUTE_S)
    return f"+{minutes:02d}:{seconds:02d}"


class CountdownOverlay:
    """Show the time to the next start, or the time since the last one."""

    def __init__(self, config: CountdownOverlayConfig, race_clock: RaceClock) -> None:
        """Hold the overlay's settings and the race clock.

        Args:
            config: The overlay's settings.
            race_clock: Provides the state of the start sequences.
        """
        self._config = config
        self._race_clock = race_clock

    def state(self, context: OverlayContext) -> _Display | None:
        """Return what to show, which changes exactly when the display needs to."""
        return self._display(self._race_clock.state(context.now), context.now)

    def draw(self, context: OverlayContext) -> Layer | None:
        """Draw the text in the configured corner, if there's anything to show."""
        display = self.state(context)
        if display is None:
            return None
        image = draw_text_panel(
            display.text, context.height, self._config.scale, display.colour
        )
        height, width = image.shape[:2]
        x, y = corner_position(self._config.position, context, width, height)
        return Layer(image, x, y)

    def _display(self, race: RaceState, now: float) -> _Display | None:
        """Choose what to show in a race state."""
        if race.phase is Phase.ABANDONED:
            return _Display(ABANDONED_TEXT, self._config.colour)
        if race.phase is Phase.POSTPONED:
            return _Display(POSTPONED_TEXT, self._config.colour)
        if race.phase is Phase.IN_SEQUENCE:
            countdown = format_countdown(race.upcoming.start - now)
            return _Display(f"{race.upcoming.name} {countdown}", self._config.colour)
        recent = self._recent_count_up(race, now)
        if recent is not None:
            return recent
        if race.phase is Phase.WAITING:
            at = time.strftime("%H:%M:%S", time.localtime(race.upcoming.start))
            return _Display(f"{race.upcoming.name} {at}", self._config.colour)
        if race.phase is Phase.IDLE:
            return _Display(READY_TEXT, self._config.colour)
        return None

    def _recent_count_up(self, race: RaceState, now: float) -> _Display | None:
        """Return the count-up from the last start, if it hasn't run for too long."""
        if race.latest is None:
            return None
        elapsed = now - race.latest.start
        if elapsed >= self._config.count_up_minutes * MINUTE_S:
            return None
        text = f"{race.latest.name} {format_count_up(elapsed)}"
        return _Display(text, self._config.count_up_colour)
