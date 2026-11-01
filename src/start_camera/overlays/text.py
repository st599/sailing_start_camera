#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: text.py
#  Description: Draw text that stays readable on any background.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, opencv
#
#########################################################

"""Draw text that stays readable on any background."""

#
# SYSTEM IMPORTS
#

import cv2
import numpy as np
from numpy.typing import NDArray

#
#  GLOBAL VARIABLES
#

FONT = cv2.FONT_HERSHEY_SIMPLEX
PANEL_COLOUR = (0, 0, 0)
PANEL_ALPHA = 170
REFERENCE_HEIGHT = 720
REFERENCE_FONT_SCALE = 0.9
REFERENCE_PADDING = 10

#
# FUNCTIONS
#


def parse_colour(colour: str) -> tuple[int, int, int]:
    """Return a "#rrggbb" colour as (blue, green, red)."""
    return int(colour[5:7], 16), int(colour[3:5], 16), int(colour[1:3], 16)


def draw_text_panel(
    text: str, frame_height: int, scale: float, colour: str
) -> NDArray[np.uint8]:
    """Return a BGRA image of text on a dark translucent panel.

    The dark panel keeps the text readable against bright water and sky. Its size
    follows the frame's height, so the text looks the same at any resolution.

    Args:
        text: The text to draw.
        frame_height: The height of the video.
        scale: A size multiplier.
        colour: The text colour as "#rrggbb".
    """
    size = frame_height / REFERENCE_HEIGHT * scale
    font_scale = REFERENCE_FONT_SCALE * size
    thickness = max(1, round(2 * size))
    padding = max(1, round(REFERENCE_PADDING * size))
    (text_width, text_height), baseline = cv2.getTextSize(
        text, FONT, font_scale, thickness
    )
    height = text_height + baseline + 2 * padding
    width = text_width + 2 * padding
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.putText(
        mask,
        text,
        (padding, padding + text_height),
        FONT,
        font_scale,
        255,
        thickness,
        cv2.LINE_AA,
    )
    coverage = mask[:, :, np.newaxis] / 255
    colours = (
        np.array(parse_colour(colour)) * coverage
        + np.array(PANEL_COLOUR) * (1 - coverage)
    )
    alpha = np.maximum(mask, PANEL_ALPHA)
    return np.dstack((colours.astype(np.uint8), alpha))
