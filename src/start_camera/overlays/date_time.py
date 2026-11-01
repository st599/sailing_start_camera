#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: date_time.py
#  Description: The date and time overlay.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: None
#
#########################################################

"""The date and time overlay."""

#
# SYSTEM IMPORTS
#

import time

#
# PROJECT IMPORTS
#
from start_camera.config import DateTimeOverlayConfig
from start_camera.overlays.compositor import Layer, OverlayContext, corner_position
from start_camera.overlays.text import draw_text_panel

#
# FUNCTIONS
#


class DateTimeOverlay:
    """Show the local date and time."""

    def __init__(self, config: DateTimeOverlayConfig) -> None:
        """Hold the overlay's settings."""
        self._config = config

    def state(self, context: OverlayContext) -> str:
        """Return the text, which changes exactly when the display needs to."""
        return time.strftime(self._config.format, time.localtime(context.now))

    def draw(self, context: OverlayContext) -> Layer:
        """Draw the text in the configured corner."""
        image = draw_text_panel(
            self.state(context),
            context.height,
            self._config.scale,
            self._config.colour,
        )
        height, width = image.shape[:2]
        x, y = corner_position(self._config.position, context, width, height)
        return Layer(image, x, y)
