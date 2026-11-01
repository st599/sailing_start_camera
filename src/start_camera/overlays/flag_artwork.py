#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: flag_artwork.py
#  Description: Draw signal flags, and load custom flag images.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, opencv
#
#########################################################

"""Draw signal flags, and load custom flag images.

The international code flags are drawn here rather than copied from anywhere, so they
come under this project's licence and any club can use them. Clubs can add their own
flags as image files, and a flag with no artwork gets a plain panel with its name, so a
signal can never silently disappear.
"""

#
# SYSTEM IMPORTS
#

import logging
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

RED = (40, 30, 210)
BLUE = (140, 50, 20)
YELLOW = (0, 210, 250)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
OUTLINE = (120, 120, 120)
OVERSAMPLE = 4
FLAG_ASPECT = 1.5
PENNANT_ASPECT = 2.0
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
SWALLOW_NOTCH = ((1.0, 0.0), (0.75, 0.5), (1.0, 1.0))
PENNANT_CUTS = (
    ((0.0, 0.0), (1.0, 0.36), (1.0, 0.0)),
    ((0.0, 1.0), (1.0, 0.64), (1.0, 1.0)),
)

Colour = tuple[int, int, int]
Image = NDArray[np.uint8]

#
# FUNCTIONS
#


class _Canvas:
    """A flag being drawn, with positions given as fractions of its size."""

    def __init__(self, width: int, height: int) -> None:
        """Make an empty canvas."""
        self.width = width
        self.height = height
        self.image = np.zeros((height, width, 3), dtype=np.uint8)

    def rect(
        self, left: float, top: float, right: float, bottom: float, colour: Colour
    ) -> None:
        """Fill a rectangle."""
        self.image[
            round(top * self.height) : round(bottom * self.height),
            round(left * self.width) : round(right * self.width),
        ] = colour

    def poly(self, points: tuple[tuple[float, float], ...], colour: Colour) -> None:
        """Fill a polygon."""
        path = np.array(
            [(x * self.width, y * self.height) for x, y in points], dtype=np.int32
        )
        cv2.fillPoly(self.image, [path], colour)

    def circle(self, radius: float, colour: Colour) -> None:
        """Fill a circle in the centre, sized as a fraction of the height."""
        centre = (self.width // 2, self.height // 2)
        cv2.circle(self.image, centre, round(radius * self.height), colour, -1)


def _horizontal(*bands: tuple[Colour, float]) -> Callable[[_Canvas], None]:
    """Return a drawing of horizontal bands, from the top, with relative heights."""

    def draw(canvas: _Canvas) -> None:
        total = sum(size for _, size in bands)
        top = 0.0
        for colour, size in bands:
            canvas.rect(0, top, 1, top + size / total, colour)
            top += size / total

    return draw


def _vertical(*colours: Colour) -> Callable[[_Canvas], None]:
    """Return a drawing of equal vertical bands, from the hoist."""

    def draw(canvas: _Canvas) -> None:
        for index, colour in enumerate(colours):
            canvas.rect(index / len(colours), 0, (index + 1) / len(colours), 1, colour)

    return draw


def _quarters(
    hoist_top: Colour, fly_top: Colour, hoist_bottom: Colour, fly_bottom: Colour
) -> Callable[[_Canvas], None]:
    """Return a drawing of four quarters."""

    def draw(canvas: _Canvas) -> None:
        canvas.rect(0, 0, 0.5, 0.5, hoist_top)
        canvas.rect(0.5, 0, 1, 0.5, fly_top)
        canvas.rect(0, 0.5, 0.5, 1, hoist_bottom)
        canvas.rect(0.5, 0.5, 1, 1, fly_bottom)

    return draw


def _square(outer: Colour, inner: Colour) -> Callable[[_Canvas], None]:
    """Return a drawing of a central square on a background."""

    def draw(canvas: _Canvas) -> None:
        canvas.rect(0, 0, 1, 1, outer)
        canvas.rect(0.3, 0.3, 0.7, 0.7, inner)

    return draw


def _cross(field: Colour, cross: Colour) -> Callable[[_Canvas], None]:
    """Return a drawing of an upright cross."""

    def draw(canvas: _Canvas) -> None:
        canvas.rect(0, 0, 1, 1, field)
        canvas.rect(0.4, 0, 0.6, 1, cross)
        canvas.rect(0, 0.36, 1, 0.64, cross)

    return draw


def _saltire(field: Colour, cross: Colour) -> Callable[[_Canvas], None]:
    """Return a drawing of a diagonal cross."""

    def draw(canvas: _Canvas) -> None:
        canvas.rect(0, 0, 1, 1, field)
        thickness = canvas.height // 5
        size = (canvas.width - 1, canvas.height - 1)
        cv2.line(canvas.image, (0, 0), size, cross, thickness)
        cv2.line(canvas.image, (0, size[1]), (size[0], 0), cross, thickness)

    return draw


def _disc(field: Colour, disc: Colour) -> Callable[[_Canvas], None]:
    """Return a drawing of a central circle."""

    def draw(canvas: _Canvas) -> None:
        canvas.rect(0, 0, 1, 1, field)
        canvas.circle(0.28, disc)

    return draw


def _draw_d(canvas: _Canvas) -> None:
    _horizontal((YELLOW, 1), (BLUE, 2), (YELLOW, 1))(canvas)


def _draw_f(canvas: _Canvas) -> None:
    canvas.rect(0, 0, 1, 1, WHITE)
    canvas.poly(((0.5, 0.0), (1.0, 0.5), (0.5, 1.0), (0.0, 0.5)), RED)


def _draw_n(canvas: _Canvas) -> None:
    for row in range(4):
        for column in range(4):
            colour = BLUE if (row + column) % 2 == 0 else WHITE
            canvas.rect(
                column / 4, row / 4, (column + 1) / 4, (row + 1) / 4, colour
            )


def _draw_o(canvas: _Canvas) -> None:
    canvas.rect(0, 0, 1, 1, YELLOW)
    canvas.poly(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0)), RED)


def _draw_w(canvas: _Canvas) -> None:
    canvas.rect(0, 0, 1, 1, BLUE)
    canvas.rect(0.16, 0.16, 0.84, 0.84, WHITE)
    canvas.rect(0.36, 0.36, 0.64, 0.64, RED)


def _draw_y(canvas: _Canvas) -> None:
    canvas.rect(0, 0, 1, 1, YELLOW)
    for index in range(-5, 5, 2):
        left = index / 5
        canvas.poly(
            (
                (left, 1.0),
                (left + 0.2, 1.0),
                (left + 1.2, 0.0),
                (left + 1.0, 0.0),
            ),
            RED,
        )


def _draw_z(canvas: _Canvas) -> None:
    centre = (0.5, 0.5)
    canvas.poly(((0.0, 0.0), (1.0, 0.0), centre), YELLOW)
    canvas.poly(((1.0, 0.0), (1.0, 1.0), centre), BLUE)
    canvas.poly(((0.0, 1.0), (1.0, 1.0), centre), RED)
    canvas.poly(((0.0, 0.0), (0.0, 1.0), centre), BLACK)


FLAGS: dict[str, Callable[[_Canvas], None]] = {
    "A": _vertical(WHITE, BLUE),
    "B": _vertical(RED),
    "C": _horizontal(
        (BLUE, 1), (WHITE, 1), (RED, 2), (WHITE, 1), (BLUE, 1)
    ),
    "D": _draw_d,
    "E": _horizontal((BLUE, 1), (RED, 1)),
    "F": _draw_f,
    "G": _vertical(YELLOW, BLUE, YELLOW, BLUE, YELLOW, BLUE),
    "H": _vertical(WHITE, RED),
    "I": _disc(YELLOW, BLACK),
    "J": _horizontal((BLUE, 1), (WHITE, 1), (BLUE, 1)),
    "K": _vertical(YELLOW, BLUE),
    "L": _quarters(YELLOW, BLACK, BLACK, YELLOW),
    "M": _saltire(BLUE, WHITE),
    "N": _draw_n,
    "O": _draw_o,
    "P": _square(BLUE, WHITE),
    "Q": _vertical(YELLOW),
    "R": _cross(RED, YELLOW),
    "S": _square(WHITE, BLUE),
    "T": _vertical(RED, WHITE, BLUE),
    "U": _quarters(RED, WHITE, WHITE, RED),
    "V": _saltire(WHITE, RED),
    "W": _draw_w,
    "X": _cross(WHITE, BLUE),
    "Y": _draw_y,
    "Z": _draw_z,
    "BLACK": _vertical(BLACK),
}
SWALLOWTAILS = {"A", "B"}
PENNANTS: dict[str, Callable[[_Canvas], None]] = {
    "0": _vertical(YELLOW, RED, YELLOW),
    "1": _disc(WHITE, RED),
    "2": _disc(BLUE, WHITE),
    "3": _vertical(RED, WHITE, BLUE),
    "4": _cross(RED, WHITE),
    "5": _vertical(YELLOW, BLUE),
    "6": _horizontal((BLACK, 1), (WHITE, 1)),
    "7": _horizontal((YELLOW, 1), (RED, 1)),
    "8": _cross(WHITE, RED),
    "9": _quarters(WHITE, BLACK, RED, YELLOW),
    "AP": _vertical(RED, WHITE, RED, WHITE, RED, WHITE),
    "FIRST SUBSTITUTE": _horizontal((BLUE, 1), (YELLOW, 2), (BLUE, 1)),
}


def _cut_out(
    image: Image, cuts: tuple[tuple[tuple[float, float], ...], ...]
) -> None:
    """Make areas of a BGRA image transparent."""
    height, width = image.shape[:2]
    for cut in cuts:
        path = np.array([(x * width, y * height) for x, y in cut], dtype=np.int32)
        cv2.fillPoly(image, [path], (0, 0, 0, 0))


def draw_standard_flag(name: str, height: int) -> Image | None:
    """Return a standard flag as a BGRA image, or None if there's no such flag.

    Args:
        name: The flag's name, in any case.
        height: The image's height in pixels.
    """
    key = name.upper()
    painter = FLAGS.get(key) or PENNANTS.get(key)
    if painter is None:
        return None
    pennant = key in PENNANTS
    aspect = PENNANT_ASPECT if pennant else FLAG_ASPECT
    width = max(1, round(height * aspect))
    canvas = _Canvas(width * OVERSAMPLE, height * OVERSAMPLE)
    painter(canvas)
    image = np.dstack(
        (canvas.image, np.full(canvas.image.shape[:2], 255, dtype=np.uint8))
    )
    if key in SWALLOWTAILS:
        _cut_out(image, (SWALLOW_NOTCH,))
    if pennant:
        _cut_out(image, PENNANT_CUTS)
    small = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
    return small.astype(np.uint8)


def draw_named_panel(name: str, height: int) -> Image:
    """Return a plain panel showing a flag's name, for a flag with no artwork."""
    font_scale = height / 40
    thickness = max(1, round(height / 20))
    (text_width, text_height), _ = cv2.getTextSize(
        name, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
    )
    width = max(round(height * FLAG_ASPECT), text_width + height // 2)
    image = np.full((height, width, 4), (*WHITE, 255), dtype=np.uint8)
    cv2.rectangle(image, (0, 0), (width - 1, height - 1), (*OUTLINE, 255), 2)
    origin = ((width - text_width) // 2, (height + text_height) // 2)
    cv2.putText(
        image,
        name,
        origin,
        cv2.FONT_HERSHEY_SIMPLEX,
        font_scale,
        (*BLACK, 255),
        thickness,
        cv2.LINE_AA,
    )
    # Smooth text also blends the alpha channel, which would leave holes in the panel.
    image[:, :, 3] = 255
    return image


def _find_image(directory: Path, name: str) -> Path | None:
    """Return the image file for a flag. Names are only compared, never joined."""
    if not directory.is_dir():
        return None
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() in IMAGE_SUFFIXES and path.stem.lower() == name.lower():
            return path
    return None


def _load_custom_flag(path: Path, height: int) -> Image | None:
    """Return a custom flag's image at a given height, if it can be read."""
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        logger.warning("Can't read the flag image %s", path)
        return None
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGRA)
    elif image.shape[2] == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    if image.dtype != np.uint8:
        image = (image / 257).astype(np.uint8)
    width = max(1, round(image.shape[1] * height / image.shape[0]))
    return cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)


def load_flag(name: str, height: int, directory: Path) -> Image:
    """Return a flag's image: a custom image, a standard flag or a panel with its name.

    A custom image replaces a standard flag of the same name.

    Args:
        name: The flag's name.
        height: The image's height in pixels.
        directory: The folder of custom flag images, which needn't exist.
    """
    path = _find_image(directory, name)
    image = _load_custom_flag(path, height) if path is not None else None
    if image is None:
        image = draw_standard_flag(name, height)
    if image is None:
        logger.warning("No artwork for flag %s, so showing its name", name)
        image = draw_named_panel(name, height)
    return image
