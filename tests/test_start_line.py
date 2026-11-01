#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_start_line.py
#  Description: Tests for fitting and drawing the start line.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, pytest
#
#########################################################

"""Tests for fitting and drawing the start line."""

#
# SYSTEM IMPORTS
#

import numpy as np
import pytest
from pydantic import ValidationError

#
# PROJECT IMPORTS
#
from start_camera.config import Config, LineFit, LinePoint, StartLineConfig
from start_camera.ingest import Frame
from start_camera.overlays import compositor
from start_camera.overlays.compositor import (
    Compositor,
    Layer,
    OverlayContext,
    _prepare,
)
from start_camera.overlays.factory import build_compositor
from start_camera.overlays.start_line import StartLineOverlay, fit_line

#
#  GLOBAL VARIABLES
#

ALL_FITS = [LineFit.STRAIGHT, LineFit.POLYNOMIAL, LineFit.SPLINE]

#
# FUNCTIONS
#


def points(*pairs: tuple[float, float]) -> list[LinePoint]:
    """Return line points."""
    return [LinePoint(x=x, y=y) for x, y in pairs]


def distance_to_line(point: LinePoint, line: np.ndarray) -> float:
    """Return how far a point is from the nearest part of a line."""
    segment_start, segment_end = line[:-1], line[1:]
    step = segment_end - segment_start
    position = np.array((point.x, point.y))
    lengths = (step**2).sum(axis=1)
    along = np.clip(((position - segment_start) * step).sum(axis=1) / lengths, 0, 1)
    nearest = segment_start + along[:, np.newaxis] * step
    return float(np.hypot(*(nearest - position).T).min())


def draw(config: StartLineConfig, width: int = 1280, height: int = 720) -> Layer | None:
    """Draw a line on a frame of a given size."""
    context = OverlayContext(0.0, width, height, True)
    return StartLineOverlay(config).draw(context)


def alpha_image(layer: Layer, width: int = 1280, height: int = 720) -> np.ndarray:
    """Return a layer's alpha channel in its place on the whole frame."""
    alpha = np.zeros((height, width), dtype=np.uint8)
    rows, columns = layer.image.shape[:2]
    alpha[layer.y : layer.y + rows, layer.x : layer.x + columns] = layer.image[:, :, 3]
    return alpha


def test_straight_fit_through_points_on_a_line() -> None:
    line = fit_line(points((0.1, 0.2), (0.5, 0.4), (0.9, 0.6)), LineFit.STRAIGHT)
    assert line == pytest.approx(np.array([(0.1, 0.2), (0.9, 0.6)]))


def test_straight_fit_averages_out_a_stray_point() -> None:
    line = fit_line(points((0.1, 0.5), (0.5, 0.52), (0.9, 0.5)), LineFit.STRAIGHT)
    assert line[:, 1] == pytest.approx(0.5067, abs=0.002)


def test_spline_passes_exactly_through_every_point() -> None:
    given = points((0.05, 0.6), (0.3, 0.52), (0.5, 0.5), (0.7, 0.53), (0.95, 0.62))
    line = fit_line(given, LineFit.SPLINE)
    assert all(distance_to_line(point, line) < 0.0005 for point in given)


def test_polynomial_follows_a_curve_closely() -> None:
    xs = np.linspace(0.05, 0.95, 7)
    given = points(*[(x, 0.5 + 0.4 * (x - 0.5) ** 2) for x in xs])
    line = fit_line(given, LineFit.POLYNOMIAL, degree=2)
    assert all(distance_to_line(point, line) < 0.001 for point in given)


def test_polynomial_evens_out_errors_instead_of_touching_every_point() -> None:
    given = points((0.1, 0.5), (0.3, 0.52), (0.5, 0.49), (0.7, 0.52), (0.9, 0.5))
    line = fit_line(given, LineFit.POLYNOMIAL, degree=2)
    assert distance_to_line(given[2], line) > 0.005


def test_polynomial_with_few_points_reduces_its_degree() -> None:
    line = fit_line(points((0.1, 0.1), (0.9, 0.9)), LineFit.POLYNOMIAL, degree=5)
    assert line[0] == pytest.approx((0.1, 0.1))
    assert line[-1] == pytest.approx((0.9, 0.9))


@pytest.mark.parametrize("fit", ALL_FITS)
def test_vertical_line_is_handled(fit: LineFit) -> None:
    given = points((0.5, 0.1), (0.5, 0.5), (0.5, 0.9))
    line = fit_line(given, fit)
    assert line[:, 0] == pytest.approx(0.5)
    assert line[0, 1] == pytest.approx(0.1)
    assert line[-1, 1] == pytest.approx(0.9)


@pytest.mark.parametrize("fit", ALL_FITS)
def test_curve_can_double_back_in_x(fit: LineFit) -> None:
    given = points((0.2, 0.1), (0.6, 0.5), (0.2, 0.9))
    assert fit_line(given, fit) is not None


@pytest.mark.parametrize("fit", ALL_FITS)
def test_line_stops_at_its_first_and_last_points(fit: LineFit) -> None:
    given = points((0.2, 0.5), (0.5, 0.48), (0.8, 0.5))
    line = fit_line(given, fit)
    assert line[:, 0].min() == pytest.approx(0.2, abs=0.01)
    assert line[:, 0].max() == pytest.approx(0.8, abs=0.01)


@pytest.mark.parametrize("fit", ALL_FITS)
def test_line_runs_in_the_order_given(fit: LineFit) -> None:
    line = fit_line(points((0.8, 0.5), (0.5, 0.5), (0.2, 0.5)), fit)
    assert line[0, 0] > line[-1, 0]


@pytest.mark.parametrize("fit", ALL_FITS)
def test_repeated_points_are_handled(fit: LineFit) -> None:
    given = points((0.1, 0.5), (0.1, 0.5), (0.5, 0.5), (0.5, 0.5), (0.9, 0.5))
    line = fit_line(given, fit)
    assert line[0, 0] == pytest.approx(0.1)
    assert line[-1, 0] == pytest.approx(0.9)


@pytest.mark.parametrize("fit", ALL_FITS)
@pytest.mark.parametrize(
    "given", [[], points((0.5, 0.5)), points((0.5, 0.5), (0.5, 0.5))]
)
def test_too_few_points_gives_no_line(fit: LineFit, given: list[LinePoint]) -> None:
    assert fit_line(given, fit) is None


def test_enabled_line_of_repeated_points_is_rejected() -> None:
    with pytest.raises(ValidationError, match="2 different points, got 1"):
        StartLineConfig(enabled=True, points=points((0.5, 0.5), (0.5, 0.5)))


def test_line_is_drawn_between_its_ends_only() -> None:
    config = StartLineConfig(points=points((0.25, 0.5), (0.75, 0.5)))
    alpha = alpha_image(draw(config))
    assert alpha[:, 640].max() > 0
    assert abs(int(alpha[:, 640].argmax()) - 360) <= 1
    assert alpha[:, :310].max() == 0
    assert alpha[:, 970:].max() == 0


def test_vertical_line_is_drawn() -> None:
    config = StartLineConfig(points=points((0.5, 0.2), (0.5, 0.8)))
    alpha = alpha_image(draw(config))
    assert alpha[360].max() > 0
    assert abs(int(alpha[360].argmax()) - 640) <= 1


@pytest.mark.parametrize("size", [(640, 360), (1280, 720), (1920, 1080)])
def test_line_is_in_the_same_place_at_any_resolution(size: tuple[int, int]) -> None:
    width, height = size
    given = points((0.1, 0.3), (0.5, 0.5), (0.9, 0.4))
    config = StartLineConfig(points=given, fit=LineFit.SPLINE)
    alpha = alpha_image(draw(config, width, height), width, height)
    for point in given:
        x, y = round(point.x * (width - 1)), round(point.y * (height - 1))
        assert alpha[y - 2 : y + 3, x - 2 : x + 3].max() > 0


def test_colour_opacity_and_smooth_edges() -> None:
    config = StartLineConfig(
        points=points((0.1, 0.1), (0.9, 0.6)), colour="#102030", opacity=0.5, width=3
    )
    image = draw(config).image
    visible = image[image[:, :, 3] > 0]
    assert visible[0, :3].tolist() == [0x30, 0x20, 0x10]
    assert image[:, :, 3].max() == pytest.approx(127, abs=1)
    assert len(np.unique(visible[:, 3])) > 2


def test_width_is_configurable() -> None:
    def thickness(width: int) -> int:
        config = StartLineConfig(points=points((0.1, 0.5), (0.9, 0.5)), width=width)
        return int((alpha_image(draw(config))[:, 640] > 0).sum())

    assert thickness(8) > thickness(2)


def test_only_the_area_of_the_line_is_kept() -> None:
    layer = draw(StartLineConfig(points=points((0.25, 0.5), (0.75, 0.5))))
    assert layer.image.shape[0] < 20
    assert layer.image.shape[1] < 700


def test_too_few_points_draws_nothing() -> None:
    assert draw(StartLineConfig()) is None


def test_line_is_only_drawn_once() -> None:
    fits = []

    class Counting(StartLineOverlay):
        def draw(self, context: OverlayContext) -> Layer | None:
            fits.append(1)
            return super().draw(context)

    config = StartLineConfig(points=points((0.1, 0.5), (0.9, 0.5)))
    compositor = Compositor([Counting(config)], clock=lambda: 0.0)
    frame = Frame(np.zeros((720, 1280, 3), dtype=np.uint8), 0.0, 1, True)
    for _ in range(5):
        compositor.render(frame)
    assert len(fits) == 1


def test_diagonal_line_blends_the_same_as_a_dense_layer() -> None:
    size = (200, 200, 4)
    image = np.zeros(size, dtype=np.uint8)
    for index in range(200):
        image[index, index] = (10, 20, 30, 128)
    frame = Frame(np.full((200, 200, 3), 100, dtype=np.uint8), 0.0, 1, True)

    class Fixed:
        def state(self, context: OverlayContext) -> int:
            return 0

        def draw(self, context: OverlayContext) -> Layer:
            return Layer(image, 0, 0)

    result = Compositor([Fixed()], clock=lambda: 0.0).render(frame)
    expected = (
        (np.array((10, 20, 30)) * 128 + np.array(100) * 127 + 255) >> 8
    ).tolist()
    assert result[50, 50].tolist() == expected
    assert result[50, 51].tolist() == [100, 100, 100]
    assert _prepare(Layer(image, 0, 0), 200, 200).pixels is not None


def test_sparse_and_dense_blending_give_the_same_picture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = StartLineConfig(
        points=points((0.0, 0.0), (0.5, 0.45), (1.0, 1.0)), fit=LineFit.SPLINE
    )
    background = np.random.default_rng(1).integers(0, 255, (720, 1280, 3))
    frame = Frame(background.astype(np.uint8), 0.0, 1, True)

    def render() -> np.ndarray:
        overlay = StartLineOverlay(config)
        return Compositor([overlay], clock=lambda: 0.0).render(frame)

    sparse = render()
    monkeypatch.setattr(compositor, "SPARSE_COVERAGE", 0.0)
    assert np.array_equal(sparse, render())


def test_sparse_layer_is_clipped_at_the_edges() -> None:
    image = np.zeros((100, 100, 4), dtype=np.uint8)
    image[50, 50] = (1, 2, 3, 255)

    class Fixed:
        def state(self, context: OverlayContext) -> int:
            return 0

        def draw(self, context: OverlayContext) -> Layer:
            return Layer(image, -40, -40)

    frame = Frame(np.zeros((20, 20, 3), dtype=np.uint8), 0.0, 1, True)
    result = Compositor([Fixed()], clock=lambda: 0.0).render(frame)
    assert result[10, 10].tolist() == [1, 2, 3]


def test_factory_puts_the_line_beneath_the_text() -> None:
    config = Config(
        start_line=StartLineConfig(
            enabled=True, points=points((0.0, 0.0), (1.0, 1.0))
        )
    )
    overlays = build_compositor(config, clock=lambda: 0.0)._overlays
    assert isinstance(overlays[0].overlay, StartLineOverlay)


def test_factory_leaves_out_a_disabled_line() -> None:
    assert len(build_compositor(Config(), clock=lambda: 0.0)._overlays) == 1
