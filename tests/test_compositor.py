#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_compositor.py
#  Description: Tests for combining overlays with frames.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, pytest
#
#########################################################

"""Tests for combining overlays with frames, checked at known pixels."""

#
# SYSTEM IMPORTS
#

from collections.abc import Hashable

import numpy as np
import pytest
from numpy.typing import NDArray

#
# PROJECT IMPORTS
#
from start_camera.config import Corner
from start_camera.ingest import Frame
from start_camera.overlays.compositor import (
    Compositor,
    Layer,
    OverlayContext,
    corner_position,
    crop_to_content,
)

#
#  GLOBAL VARIABLES
#

WIDTH = 40
HEIGHT = 20

#
# FUNCTIONS
#


def make_frame(value: int = 100, *, live: bool = True) -> Frame:
    """Return a read-only frame of one colour."""
    image = np.full((HEIGHT, WIDTH, 3), value, dtype=np.uint8)
    image.flags.writeable = False
    return Frame(image, 0.0, 1, live)


def patch(
    colour: tuple[int, int, int], alpha: int, width: int = 4, height: int = 2
) -> NDArray[np.uint8]:
    """Return a BGRA image of one colour."""
    return np.full((height, width, 4), (*colour, alpha), dtype=np.uint8)


class FixedOverlay:
    """An overlay that always draws the same layer, counting its drawings."""

    def __init__(self, layer: Layer | None, state: Hashable = 0) -> None:
        """Hold the layer."""
        self.layer = layer
        self.current_state = state
        self.draws = 0

    def state(self, context: OverlayContext) -> Hashable:
        """Return the state set by the test."""
        return self.current_state

    def draw(self, context: OverlayContext) -> Layer | None:
        """Return the layer."""
        self.draws += 1
        return self.layer


def render_one(layer: Layer, value: int = 100) -> NDArray[np.uint8]:
    """Return a frame with one layer on it."""
    return Compositor([FixedOverlay(layer)]).render(make_frame(value))


def test_no_overlays_return_the_frame_without_copying() -> None:
    frame = make_frame()
    assert Compositor([]).render(frame) is frame.image


def test_opaque_layer_replaces_pixels_only_where_it_is() -> None:
    image = render_one(Layer(patch((10, 20, 30), 255), 5, 3))
    assert image[3, 5].tolist() == [10, 20, 30]
    assert image[4, 8].tolist() == [10, 20, 30]
    assert image[2, 5].tolist() == [100, 100, 100]
    assert image[3, 9].tolist() == [100, 100, 100]
    assert image[5, 5].tolist() == [100, 100, 100]


def test_transparent_layer_leaves_pixels_unchanged() -> None:
    assert render_one(Layer(patch((10, 20, 30), 0), 0, 0))[0, 0].tolist() == [
        100,
        100,
        100,
    ]


def test_translucent_layer_blends() -> None:
    pixel = render_one(Layer(patch((200, 0, 100), 128), 0, 0))[0, 0]
    assert pixel.tolist() == pytest.approx([149, 50, 100], abs=1)


@pytest.mark.parametrize("value", [0, 1, 127, 128, 254, 255])
@pytest.mark.parametrize("colour", [0, 1, 128, 255])
def test_blend_extremes_are_exact(value: int, colour: int) -> None:
    assert render_one(Layer(patch((colour,) * 3, 255), 0, 0), value)[0, 0, 0] == colour
    assert render_one(Layer(patch((colour,) * 3, 0), 0, 0), value)[0, 0, 0] == value


def test_later_overlays_are_on_top() -> None:
    bottom = FixedOverlay(Layer(patch((1, 1, 1), 255), 0, 0))
    top = FixedOverlay(Layer(patch((2, 2, 2), 255), 0, 0))
    assert Compositor([bottom, top]).render(make_frame())[0, 0, 0] == 2


def test_original_frame_is_not_changed() -> None:
    frame = make_frame()
    Compositor([FixedOverlay(Layer(patch((1, 2, 3), 255), 0, 0))]).render(frame)
    assert frame.image[0, 0].tolist() == [100, 100, 100]


@pytest.mark.parametrize(
    ("x", "y", "pixel"),
    [(-2, -1, (0, 0)), (WIDTH - 2, HEIGHT - 1, (HEIGHT - 1, WIDTH - 1))],
)
def test_layer_is_clipped_at_the_edges(x: int, y: int, pixel: tuple[int, int]) -> None:
    image = render_one(Layer(patch((1, 2, 3), 255), x, y))
    assert image[pixel].tolist() == [1, 2, 3]


@pytest.mark.parametrize("position", [(WIDTH, 0), (0, HEIGHT), (-4, 0), (0, -2)])
def test_layer_off_the_frame_is_ignored(position: tuple[int, int]) -> None:
    frame = make_frame()
    layer = Layer(patch((1, 2, 3), 255), *position)
    assert Compositor([FixedOverlay(layer)]).render(frame) is frame.image


def test_overlay_returning_nothing_is_skipped() -> None:
    frame = make_frame()
    assert Compositor([FixedOverlay(None)]).render(frame) is frame.image


def test_overlay_is_drawn_only_when_its_state_changes() -> None:
    overlay = FixedOverlay(Layer(patch((1, 2, 3), 255), 0, 0))
    compositor = Compositor([overlay])
    for _ in range(5):
        compositor.render(make_frame())
    assert overlay.draws == 1
    overlay.current_state = 1
    compositor.render(make_frame())
    assert overlay.draws == 2


def test_overlay_with_none_as_first_state_is_drawn() -> None:
    overlay = FixedOverlay(Layer(patch((1, 2, 3), 255), 0, 0), state=None)
    assert Compositor([overlay]).render(make_frame())[0, 0, 0] == 1
    assert overlay.draws == 1


def test_cached_drawing_goes_on_each_new_frame() -> None:
    compositor = Compositor([FixedOverlay(Layer(patch((1, 2, 3), 255), 0, 0))])
    compositor.render(make_frame(100))
    image = compositor.render(make_frame(50))
    assert image[0, 0].tolist() == [1, 2, 3]
    assert image[10, 10].tolist() == [50, 50, 50]


def test_overlay_is_drawn_again_when_the_frame_size_changes() -> None:
    overlay = FixedOverlay(Layer(patch((1, 2, 3), 255), 0, 0))
    compositor = Compositor([overlay])
    compositor.render(make_frame())
    image = np.zeros((10, 10, 3), dtype=np.uint8)
    compositor.render(Frame(image, 0.0, 1, True))
    assert overlay.draws == 2


def test_failing_overlay_is_left_out_and_logged_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Broken(FixedOverlay):
        def draw(self, context: OverlayContext) -> Layer | None:
            raise RuntimeError("drawing failed")

    good = FixedOverlay(Layer(patch((1, 2, 3), 255), 0, 0))
    compositor = Compositor([Broken(None), good])
    for _ in range(3):
        image = compositor.render(make_frame())
    assert image[0, 0].tolist() == [1, 2, 3]
    assert caplog.text.count("Overlay Broken failed") == 1


def test_overlay_recovers_after_failing() -> None:
    class Flaky(FixedOverlay):
        fail = True

        def draw(self, context: OverlayContext) -> Layer | None:
            if self.fail:
                raise RuntimeError("drawing failed")
            return super().draw(context)

    overlay = Flaky(Layer(patch((1, 2, 3), 255), 0, 0))
    compositor = Compositor([overlay])
    compositor.render(make_frame())
    overlay.fail = False
    assert compositor.render(make_frame())[0, 0, 0] == 1


def test_overlay_sees_the_time_and_frame() -> None:
    seen: list[OverlayContext] = []

    class Recording(FixedOverlay):
        def state(self, context: OverlayContext) -> Hashable:
            seen.append(context)
            return 0

    Compositor([Recording(None)], clock=lambda: 12.5).render(make_frame(live=False))
    assert seen == [OverlayContext(12.5, WIDTH, HEIGHT, False)]


@pytest.mark.parametrize(
    ("corner", "expected"),
    [
        (Corner.TOP_LEFT, (2, 2)),
        (Corner.TOP_RIGHT, (34, 2)),
        (Corner.BOTTOM_LEFT, (2, 15)),
        (Corner.BOTTOM_RIGHT, (34, 15)),
    ],
)
def test_corner_position(corner: Corner, expected: tuple[int, int]) -> None:
    context = OverlayContext(0.0, WIDTH, HEIGHT, True)
    assert corner_position(corner, context, 4, 3, margin=2) == expected


def test_crop_finds_the_visible_area() -> None:
    image = np.zeros((10, 10, 4), dtype=np.uint8)
    image[2:4, 5:8] = (1, 2, 3, 255)
    layer = crop_to_content(image, x=100, y=200)
    assert layer is not None
    assert (layer.x, layer.y) == (105, 202)
    assert layer.image.shape == (2, 3, 4)


def test_crop_of_nothing_is_none() -> None:
    assert crop_to_content(np.zeros((4, 4, 4), dtype=np.uint8)) is None
