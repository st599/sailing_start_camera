#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: factory.py
#  Description: Build the overlays from the configuration.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: None
#
#########################################################

"""Build the overlays from the configuration."""

#
# SYSTEM IMPORTS
#

import time
from collections.abc import Callable

#
# PROJECT IMPORTS
#
from start_camera.config import Config
from start_camera.overlays.compositor import Compositor, Overlay
from start_camera.overlays.countdown import CountdownOverlay
from start_camera.overlays.date_time import DateTimeOverlay
from start_camera.overlays.flags import FlagsOverlay
from start_camera.overlays.start_line import StartLineOverlay
from start_camera.race_clock import RaceClock

#
# FUNCTIONS
#


def build_compositor(
    config: Config,
    clock: Callable[[], float] = time.time,
    race_clock: RaceClock | None = None,
) -> Compositor:
    """Return a compositor for the enabled overlays.

    All overlays share one clock, set back by the camera's delay, so that they agree
    with each other and describe the moment the picture was captured.

    Args:
        config: The configuration.
        clock: Provides the current time.
        race_clock: The race clock to show. Defaults to a new one for the configuration.
    """
    latency_s = config.camera.capture_latency_ms / 1000
    overlays: list[Overlay] = []
    if config.start_line.enabled:
        overlays.append(StartLineOverlay(config.start_line))
    if config.overlays.datetime.enabled:
        overlays.append(DateTimeOverlay(config.overlays.datetime))
    sequences = config.sequences
    if sequences.items or sequences.day_flags:
        race_clock = race_clock or RaceClock(sequences, clock())
    countdown = config.overlays.countdown if sequences.items else None
    if countdown is not None and countdown.enabled:
        overlays.append(CountdownOverlay(countdown, race_clock))
    else:
        countdown = None
    if race_clock is not None and config.overlays.flags.enabled:
        overlays.append(
            FlagsOverlay(config.overlays.flags, race_clock, sequences.items, countdown)
        )
    return Compositor(overlays, clock=lambda: clock() - latency_s)
