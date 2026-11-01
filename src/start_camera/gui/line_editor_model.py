#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: line_editor_model.py
#  Description: The start line editor's points and view, free of the GUI library.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: None
#
#########################################################

"""The start line editor's points and view, free of the GUI library."""

#
# SYSTEM IMPORTS
#

import math
from dataclasses import dataclass

#
#  GLOBAL VARIABLES
#

MAX_ZOOM = 16.0
COORDINATE_PLACES = 5

Point = tuple[float, float]

#
# FUNCTIONS
#


def _distance(first: Point, second: Point) -> float:
    """Return the distance between two points."""
    return math.hypot(first[0] - second[0], first[1] - second[1])


class LinePoints:
    """The line's points, in order along the line, as fractions of the frame."""

    def __init__(self, points: list[Point] | None = None) -> None:
        """Hold the points."""
        self.points = list(points or [])

    def add(self, point: Point) -> int:
        """Add a point where it adds the least length to the line, and return its index.

        The ends are usually placed first, so a point placed between them belongs in the
        middle, not at the end of the list, and one beyond an end extends the line.
        """
        point = _tidy(point)
        count = len(self.points)
        if count < 2:
            self.points.append(point)
            return count
        best_index = count
        best_cost = _distance(self.points[-1], point)
        before_first = _distance(self.points[0], point)
        if before_first < best_cost:
            best_index, best_cost = 0, before_first
        for index in range(1, count):
            first, second = self.points[index - 1], self.points[index]
            cost = (
                _distance(first, point)
                + _distance(point, second)
                - _distance(first, second)
            )
            if cost < best_cost:
                best_index, best_cost = index, cost
        self.points.insert(best_index, point)
        return best_index

    def move(self, index: int, point: Point) -> None:
        """Move a point."""
        self.points[index] = _tidy(point)

    def remove(self, index: int) -> None:
        """Remove a point."""
        del self.points[index]

    def clear(self) -> None:
        """Remove all the points."""
        self.points.clear()


def _tidy(point: Point) -> Point:
    """Keep a point on the frame, without meaningless digits in the file."""
    return (
        round(min(max(point[0], 0.0), 1.0), COORDINATE_PLACES),
        round(min(max(point[1], 0.0), 1.0), COORDINATE_PLACES),
    )


@dataclass
class ViewTransform:
    """Convert between the widget's pixels and fractions of the picture, with zoom."""

    view_width: int
    view_height: int
    image_width: int
    image_height: int
    zoom: float = 1.0
    centre_x: float = 0.5
    centre_y: float = 0.5

    @property
    def _scale(self) -> float:
        """Return view pixels per image pixel."""
        fit = min(
            self.view_width / self.image_width, self.view_height / self.image_height
        )
        return fit * self.zoom

    @property
    def _origin(self) -> tuple[float, float]:
        """Return where the picture's top left corner is in the view."""
        return (
            self.view_width / 2 - self.centre_x * self.image_width * self._scale,
            self.view_height / 2 - self.centre_y * self.image_height * self._scale,
        )

    def to_view(self, point: Point) -> Point:
        """Return where a point is in the view."""
        left, top = self._origin
        scale = self._scale
        return (
            left + point[0] * self.image_width * scale,
            top + point[1] * self.image_height * scale,
        )

    def to_picture(self, x: float, y: float) -> Point:
        """Return the fraction of the picture at a place in the view."""
        left, top = self._origin
        scale = self._scale
        return (
            (x - left) / (self.image_width * scale),
            (y - top) / (self.image_height * scale),
        )

    def is_on_picture(self, x: float, y: float) -> bool:
        """Return whether a place in the view is on the picture."""
        fraction_x, fraction_y = self.to_picture(x, y)
        return 0.0 <= fraction_x <= 1.0 and 0.0 <= fraction_y <= 1.0

    def picture_rect(self) -> tuple[float, float, float, float]:
        """Return the picture's left, top, width and height in the view."""
        left, top = self._origin
        scale = self._scale
        return left, top, self.image_width * scale, self.image_height * scale

    def zoom_at(self, factor: float, x: float, y: float) -> None:
        """Zoom, keeping the part of the picture at a place in the view where it is."""
        fixed = self.to_picture(x, y)
        self.zoom = min(max(self.zoom * factor, 1.0), MAX_ZOOM)
        moved = self.to_picture(x, y)
        self.pan(fixed[0] - moved[0], fixed[1] - moved[1])

    def pan(self, across: float, down: float) -> None:
        """Move the view by fractions of the picture, without leaving the picture."""
        self.centre_x = self._limit(self.centre_x + across)
        self.centre_y = self._limit(self.centre_y + down)

    def pan_by_view_pixels(self, x: float, y: float) -> None:
        """Move the view by a drag's distance, so the picture follows the pointer."""
        scale = self._scale
        self.pan(-x / (self.image_width * scale), -y / (self.image_height * scale))

    def reset(self) -> None:
        """Show the whole picture."""
        self.zoom, self.centre_x, self.centre_y = 1.0, 0.5, 0.5

    def _limit(self, centre: float) -> float:
        """Keep the view's centre where the view stays on the picture."""
        margin = 0.5 / self.zoom
        return min(max(centre, margin), 1.0 - margin)
