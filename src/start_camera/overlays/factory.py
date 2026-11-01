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
from start_camera.overlays.date_time import DateTimeOverlay

#
# FUNCTIONS
#


def build_compositor(
    config: Config, clock: Callable[[], float] = time.time
) -> Compositor:
    """Return a compositor for the enabled overlays.

    All overlays share one clock, set back by the camera's delay, so that they agree
    with each other and describe the moment the picture was captured.

    Args:
        config: The configuration.
        clock: Provides the current time.
    """
    latency_s = config.camera.capture_latency_ms / 1000
    overlays: list[Overlay] = []
    if config.overlays.datetime.enabled:
        overlays.append(DateTimeOverlay(config.overlays.datetime))
    return Compositor(overlays, clock=lambda: clock() - latency_s)
