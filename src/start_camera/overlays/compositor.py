#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: compositor.py
#  Description: Combine overlays with video frames.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy
#
#########################################################

"""Combine overlays with video frames.

Overlays are drawn only when their content changes, and blending only touches the area
they cover, to leave as much processor time as possible for encoding.
"""

#
# SYSTEM IMPORTS
#

import logging
import time
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

#
# PROJECT IMPORTS
#
from start_camera.config import Corner
from start_camera.ingest import Frame

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

DEFAULT_MARGIN = 16

#
# FUNCTIONS
#


@dataclass(frozen=True)
class OverlayContext:
    """What an overlay may draw from."""

    now: float
    width: int
    height: int
    live: bool


@dataclass(frozen=True)
class Layer:
    """An image in BGRA format, placed with its top left corner at (x, y)."""

    image: NDArray[np.uint8]
    x: int
    y: int


class Overlay(Protocol):
    """Something drawn on the video."""

    def state(self, context: OverlayContext) -> Hashable:
        """Return a value that changes only when the overlay needs drawing again."""
        ...

    def draw(self, context: OverlayContext) -> Layer | None:
        """Return the overlay's image, or None if there's nothing to show."""
        ...


def corner_position(
    corner: Corner,
    context: OverlayContext,
    width: int,
    height: int,
    margin: int = DEFAULT_MARGIN,
) -> tuple[int, int]:
    """Return where to put an image of a given size in a corner of the frame."""
    left = corner in (Corner.TOP_LEFT, Corner.BOTTOM_LEFT)
    top = corner in (Corner.TOP_LEFT, Corner.TOP_RIGHT)
    x = margin if left else context.width - width - margin
    y = margin if top else context.height - height - margin
    return x, y


def crop_to_content(image: NDArray[np.uint8], x: int = 0, y: int = 0) -> Layer | None:
    """Return a layer trimmed to its visible pixels, or None if there aren't any."""
    visible = image[:, :, 3] > 0
    rows = np.flatnonzero(visible.any(axis=1))
    columns = np.flatnonzero(visible.any(axis=0))
    if rows.size == 0:
        return None
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    left, right = int(columns[0]), int(columns[-1]) + 1
    return Layer(image[top:bottom, left:right], x + left, y + top)


@dataclass(frozen=True)
class _PreparedLayer:
    """A layer clipped to the frame and ready for repeated blending."""

    rows: slice
    columns: slice
    premultiplied: NDArray[np.uint16]
    inverse_alpha: NDArray[np.uint16]


def _prepare(layer: Layer, width: int, height: int) -> _PreparedLayer | None:
    """Clip a layer to the frame, or return None if none of it is on the frame."""
    layer_height, layer_width = layer.image.shape[:2]
    left, top = max(layer.x, 0), max(layer.y, 0)
    right = min(layer.x + layer_width, width)
    bottom = min(layer.y + layer_height, height)
    if left >= right or top >= bottom:
        return None
    clipped = layer.image[
        top - layer.y : bottom - layer.y, left - layer.x : right - layer.x
    ].astype(np.uint16)
    alpha = clipped[:, :, 3:]
    return _PreparedLayer(
        slice(top, bottom),
        slice(left, right),
        clipped[:, :, :3] * alpha,
        255 - alpha,
    )


class _CachedOverlay:
    """An overlay and its most recent drawing."""

    def __init__(self, overlay: Overlay) -> None:
        """Hold the overlay, which hasn't been drawn yet."""
        self.overlay = overlay
        self.has_drawn = False
        self.state: Hashable = None
        self.size: tuple[int, int] | None = None
        self.prepared: _PreparedLayer | None = None
        self.failing = False

    def update(self, context: OverlayContext) -> None:
        """Draw the overlay again if its content has changed.

        A failing overlay is left out, as a drawing problem must not stop the stream.
        """
        size = (context.width, context.height)
        try:
            state = self.overlay.state(context)
            if self.has_drawn and state == self.state and size == self.size:
                return
            layer = self.overlay.draw(context)
            self.prepared = layer and _prepare(layer, *size)
        except Exception:
            if not self.failing:
                logger.exception("Overlay %s failed", type(self.overlay).__name__)
            self.failing = True
            self.prepared = None
            self.has_drawn = False
            return
        self.failing = False
        self.has_drawn = True
        self.state = state
        self.size = size


class Compositor:
    """Draw overlays on frames, in order, with later overlays on top."""

    def __init__(
        self,
        overlays: Iterable[Overlay],
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Prepare the overlays.

        Args:
            overlays: The overlays, from bottom to top.
            clock: Provides the current time.
        """
        self._overlays = [_CachedOverlay(overlay) for overlay in overlays]
        self._clock = clock

    def render(self, frame: Frame) -> NDArray[np.uint8]:
        """Return the frame's image with the overlays on it.

        The frame itself isn't changed, and may be returned if nothing covers it.
        """
        height, width = frame.image.shape[:2]
        context = OverlayContext(self._clock(), width, height, frame.live)
        for cached in self._overlays:
            cached.update(context)
        prepared = [cached.prepared for cached in self._overlays if cached.prepared]
        if not prepared:
            return frame.image
        image = frame.image.copy()
        for layer in prepared:
            region = image[layer.rows, layer.columns]
            # (x + 255) >> 8 divides by 255 exactly for these values, without a slow
            # integer division.
            region[:] = (
                (layer.premultiplied + region * layer.inverse_alpha + 255) >> 8
            ).astype(np.uint8)
        return image
