#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: start_line.py
#  Description: Fit and draw the start line.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, opencv
#
#########################################################

"""Fit and draw the start line.

Curves are fitted against the distance along the line, not against x, so a line can run
in any direction, including straight up the frame. The line stops at its first and last
points, because a fitted curve can bend away from the true line beyond them.
"""

#
# SYSTEM IMPORTS
#

from collections.abc import Sequence

import cv2
import numpy as np
from numpy.polynomial import Polynomial
from numpy.typing import NDArray

#
# PROJECT IMPORTS
#
from start_camera.config import LineFit, LinePoint, StartLineConfig
from start_camera.overlays.compositor import Layer, OverlayContext, crop_to_content
from start_camera.overlays.text import parse_colour

#
#  GLOBAL VARIABLES
#

CURVE_SAMPLES = 200
SUBPIXEL_BITS = 4

#
# FUNCTIONS
#


def _as_array(points: Sequence[LinePoint]) -> NDArray[np.float64]:
    """Return points as an array, without repeats of the point before."""
    array = np.array([(point.x, point.y) for point in points], dtype=np.float64)
    if len(array) < 2:
        return array
    keep = np.concatenate(([True], (np.diff(array, axis=0) != 0).any(axis=1)))
    return array[keep]


def _distances(points: NDArray[np.float64]) -> NDArray[np.float64]:
    """Return how far along the line each point is, from 0 to 1."""
    steps = np.hypot(*np.diff(points, axis=0).T)
    along = np.concatenate(([0.0], np.cumsum(steps)))
    return along / along[-1]


def _fit_straight(points: NDArray[np.float64]) -> NDArray[np.float64]:
    """Return the ends of the straight line closest to the points in any direction."""
    centre = points.mean(axis=0)
    _, _, axes = np.linalg.svd(points - centre)
    direction = axes[0]
    ends = (points[[0, -1]] - centre) @ direction
    return centre + ends[:, np.newaxis] * direction


def _fit_polynomial(points: NDArray[np.float64], degree: int) -> NDArray[np.float64]:
    """Return a smooth curve closest to the points, without having to touch them."""
    along = _distances(points)
    degree = min(degree, len(points) - 1)
    samples = np.linspace(0.0, 1.0, CURVE_SAMPLES)
    return np.column_stack(
        [
            Polynomial.fit(along, points[:, axis], degree, domain=[0, 1])(samples)
            for axis in (0, 1)
        ]
    )


def _natural_spline_curvature(
    along: NDArray[np.float64], values: NDArray[np.float64]
) -> NDArray[np.float64]:
    """Return a natural cubic spline's second derivatives at its points."""
    count = len(along)
    steps = np.diff(along)
    matrix = np.zeros((count, count))
    target = np.zeros(count)
    matrix[0, 0] = matrix[-1, -1] = 1.0
    slopes = np.diff(values) / steps
    for row in range(1, count - 1):
        matrix[row, row - 1] = steps[row - 1]
        matrix[row, row] = 2 * (steps[row - 1] + steps[row])
        matrix[row, row + 1] = steps[row]
        target[row] = 6 * (slopes[row] - slopes[row - 1])
    return np.linalg.solve(matrix, target)


def _fit_spline(points: NDArray[np.float64]) -> NDArray[np.float64]:
    """Return a smooth curve that passes exactly through every point."""
    along = _distances(points)
    samples = np.linspace(0.0, 1.0, CURVE_SAMPLES)
    spans = np.searchsorted(along, samples, side="right") - 1
    spans = np.clip(spans, 0, len(along) - 2)
    steps = np.diff(along)[spans]
    before = (along[spans + 1] - samples) / steps
    after = (samples - along[spans]) / steps
    curve = []
    for axis in (0, 1):
        values = points[:, axis]
        curvature = _natural_spline_curvature(along, values)
        curve.append(
            before * values[spans]
            + after * values[spans + 1]
            + ((before**3 - before) * curvature[spans]
               + (after**3 - after) * curvature[spans + 1]) * steps**2 / 6
        )
    return np.column_stack(curve)


def fit_line(
    points: Sequence[LinePoint], fit: LineFit, degree: int = 2
) -> NDArray[np.float64] | None:
    """Return the fitted line as a series of points, in fractions of the frame.

    Args:
        points: The points, in order along the line.
        fit: The type of fit.
        degree: The polynomial's degree, reduced if there are too few points for it.

    Returns:
        The line, or None if there aren't two different points to draw between.
    """
    array = _as_array(points)
    if len(array) < 2:
        return None
    if fit is LineFit.STRAIGHT:
        return _fit_straight(array)
    if fit is LineFit.POLYNOMIAL:
        return _fit_polynomial(array, degree)
    return _fit_spline(array)


class StartLineOverlay:
    """Draw the fitted start line."""

    def __init__(self, config: StartLineConfig) -> None:
        """Fit the line, which doesn't change while running."""
        self._config = config
        self._line = fit_line(config.points, config.fit, config.polynomial_degree)

    def state(self, context: OverlayContext) -> int:
        """Never change. The compositor redraws by itself if the frame size changes."""
        return 0

    def draw(self, context: OverlayContext) -> Layer | None:
        """Draw the line, trimmed to the area it covers."""
        if self._line is None:
            return None
        mask = np.zeros((context.height, context.width), dtype=np.uint8)
        size = np.array((context.width - 1, context.height - 1))
        path = np.round(self._line * size * (1 << SUBPIXEL_BITS)).astype(np.int32)
        cv2.polylines(
            mask,
            [path],
            isClosed=False,
            color=255,
            thickness=self._config.width,
            lineType=cv2.LINE_AA,
            shift=SUBPIXEL_BITS,
        )
        image = np.zeros((context.height, context.width, 4), dtype=np.uint8)
        image[:, :, :3] = parse_colour(self._config.colour)
        image[:, :, 3] = (mask * self._config.opacity).astype(np.uint8)
        return crop_to_content(image)
