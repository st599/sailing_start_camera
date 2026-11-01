#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: flags.py
#  Description: The start sequence flags overlay.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy
#
#########################################################

"""The start sequence flags overlay."""

#
# SYSTEM IMPORTS
#

from pathlib import Path

import numpy as np
from numpy.typing import NDArray

#
# PROJECT IMPORTS
#
from start_camera.config import (
    Corner,
    CountdownOverlayConfig,
    FlagsOverlayConfig,
    SequenceConfig,
)
from start_camera.overlays.compositor import (
    DEFAULT_MARGIN,
    Layer,
    OverlayContext,
    corner_position,
)
from start_camera.overlays.flag_artwork import load_flag
from start_camera.overlays.text import (
    PANEL_ALPHA,
    REFERENCE_HEIGHT,
    draw_text_panel,
)
from start_camera.race_clock import (
    ABANDONMENT_FLAG,
    GENERAL_RECALL_FLAG,
    INDIVIDUAL_RECALL_FLAG,
    POSTPONEMENT_FLAG,
    RaceClock,
)

#
#  GLOBAL VARIABLES
#

REFERENCE_FLAG_HEIGHT = 44
REFERENCE_GAP = 6
LABEL_SCALE = 0.7
LABEL_COLOUR = "#ffffff"
COUNTDOWN_SAMPLE = "00:00"
COMBINED_FLAG_SEPARATOR = "+"
SIGNAL_LABELS = {
    POSTPONEMENT_FLAG: "AP Postponed",
    GENERAL_RECALL_FLAG: "General recall",
    INDIVIDUAL_RECALL_FLAG: "X Individual recall",
    ABANDONMENT_FLAG: "N Abandoned",
    "Y": "Y Lifejackets",
}

#
# FUNCTIONS
#


def _class_labels(sequences: list[SequenceConfig]) -> dict[str, str]:
    """Return the label for each class flag and preparatory flag."""
    labels = {}
    for sequence in sequences:
        preparatory = sequence.preparatory_flag.value
        for part in preparatory.split(COMBINED_FLAG_SEPARATOR):
            labels[part] = "Black flag" if part == "black" else f"{part} Preparatory"
    for sequence in sequences:
        flag, name = sequence.class_flag, sequence.name
        labels[flag] = name if flag.lower() == name.lower() else f"{flag} {name}"
    return labels


def _paste(canvas: NDArray[np.uint8], image: NDArray[np.uint8], x: int, y: int) -> None:
    """Draw a BGRA image on to a BGRA canvas, respecting its transparency."""
    height, width = image.shape[:2]
    region = canvas[y : y + height, x : x + width]
    alpha = image[:, :, 3:] / 255
    blended = image[:, :, :3] * alpha + region[:, :, :3] * (1 - alpha)
    region[:, :, :3] = blended.astype(np.uint8)
    region[:, :, 3] = np.maximum(region[:, :, 3], image[:, :, 3])


class FlagsOverlay:
    """Show the flags currently flying, in a vertical stack."""

    def __init__(
        self,
        config: FlagsOverlayConfig,
        race_clock: RaceClock,
        sequences: list[SequenceConfig],
        countdown: CountdownOverlayConfig | None = None,
    ) -> None:
        """Hold the overlay's settings.

        Args:
            config: The overlay's settings.
            race_clock: Provides the flags to show.
            sequences: The sequences, which name the class flags.
            countdown: The countdown's settings if it's shown, to stack beneath it.
        """
        self._config = config
        self._race_clock = race_clock
        self._countdown = countdown
        self._directory = Path(config.custom_directory)
        self._labels = SIGNAL_LABELS | _class_labels(sequences)
        self._images: dict[tuple[str, int], NDArray[np.uint8]] = {}

    def state(self, context: OverlayContext) -> tuple[str, ...]:
        """Return the flags flying, with combined signals as separate flags."""
        flags = self._race_clock.state(context.now).flags
        separate = [
            part for flag in flags for part in flag.split(COMBINED_FLAG_SEPARATOR)
        ]
        return tuple(dict.fromkeys(separate))

    def draw(self, context: OverlayContext) -> Layer | None:
        """Draw the stack, or nothing if no flags are flying."""
        flags = self.state(context)
        if not flags:
            return None
        image = self._draw_stack(flags, context.height)
        height, width = image.shape[:2]
        x, y = corner_position(self._config.position, context, width, height)
        return Layer(image, x, y + self._top_offset(context))

    def _size(self, frame_height: int) -> float:
        """Return the size multiplier for a frame."""
        return frame_height / REFERENCE_HEIGHT * self._config.scale

    def _flag_image(self, flag: str, height: int) -> NDArray[np.uint8]:
        """Return a flag's image, loading it only once."""
        key = (flag, height)
        if key not in self._images:
            self._images[key] = load_flag(flag, height, self._directory)
        return self._images[key]

    def _row(self, flag: str, frame_height: int) -> list[NDArray[np.uint8]]:
        """Return the images in a flag's row: the flag, and perhaps its label."""
        size = self._size(frame_height)
        height = max(1, round(REFERENCE_FLAG_HEIGHT * size))
        row = [self._flag_image(flag, height)]
        if self._config.show_labels:
            label = self._labels.get(flag, flag)
            scale = self._config.scale * LABEL_SCALE
            row.append(draw_text_panel(label, frame_height, scale, LABEL_COLOUR))
        return row

    def _draw_stack(
        self, flags: tuple[str, ...], frame_height: int
    ) -> NDArray[np.uint8]:
        """Return the rows on one dark translucent panel."""
        gap = max(1, round(REFERENCE_GAP * self._size(frame_height)))
        rows = [self._row(flag, frame_height) for flag in flags]
        heights = [max(item.shape[0] for item in row) for row in rows]
        widths = [
            sum(item.shape[1] for item in row) + gap * (len(row) - 1) for row in rows
        ]
        canvas = np.zeros(
            (sum(heights) + gap * (len(rows) + 1), max(widths) + 2 * gap, 4),
            dtype=np.uint8,
        )
        canvas[:, :, 3] = PANEL_ALPHA
        y = gap
        for row, height in zip(rows, heights, strict=True):
            x = gap
            for item in row:
                _paste(canvas, item, x, y + (height - item.shape[0]) // 2)
                x += item.shape[1] + gap
            y += height + gap
        return canvas

    def _top_offset(self, context: OverlayContext) -> int:
        """Return how far down to start, to clear a countdown in the same corner."""
        same_corner = (
            self._countdown is not None
            and self._countdown.position is self._config.position
        )
        if not same_corner or self._config.position not in (
            Corner.TOP_LEFT,
            Corner.TOP_RIGHT,
        ):
            return 0
        panel = draw_text_panel(
            COUNTDOWN_SAMPLE, context.height, self._countdown.scale, LABEL_COLOUR
        )
        return panel.shape[0] + DEFAULT_MARGIN // 2
