#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: logo.py
#  Description: A fixed image, such as the club's logo.
#  Date: 2026-11-02
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, opencv
#
#########################################################

"""A fixed image, such as the club's logo."""

#
# SYSTEM IMPORTS
#

import logging
from pathlib import Path

import numpy as np

#
# PROJECT IMPORTS
#
from start_camera.config import LogoOverlayConfig
from start_camera.overlays.compositor import Layer, OverlayContext, corner_position
from start_camera.overlays.flag_artwork import load_image

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

#
# FUNCTIONS
#


class LogoOverlay:
    """Show an image file in a corner, sized to the video."""

    def __init__(self, config: LogoOverlayConfig) -> None:
        """Hold the overlay's settings. The image is read when it's first drawn."""
        self._config = config
        self._warned = False

    def state(self, context: OverlayContext) -> int:
        """Never change. The compositor redraws by itself if the frame size changes."""
        return 0

    def draw(self, context: OverlayContext) -> Layer | None:
        """Draw the image, or nothing if it can't be read."""
        height = max(1, round(self._config.height * context.height))
        image = load_image(Path(self._config.path), height)
        if image is None:
            if not self._warned:
                logger.warning("The logo %s can't be shown", self._config.path)
                self._warned = True
            return None
        if self._config.opacity < 1:
            image = image.copy()
            image[:, :, 3] = (image[:, :, 3] * self._config.opacity).astype(np.uint8)
        width = image.shape[1]
        x, y = corner_position(self._config.position, context, width, height)
        return Layer(image, x, y)
