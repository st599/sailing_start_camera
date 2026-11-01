#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_date_time_overlay.py
#  Description: Tests for the date and time overlay.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, pytest
#
#########################################################

"""Tests for the date and time overlay, using a fake clock."""

#
# SYSTEM IMPORTS
#

import logging
import os
import subprocess
import time
from collections.abc import Iterator

import numpy as np
import pytest

#
# PROJECT IMPORTS
#
from start_camera.clock_check import (
    clock_is_synchronised,
    warn_if_clock_unsynchronised,
)
from start_camera.config import (
    CameraConfig,
    Config,
    Corner,
    DateTimeOverlayConfig,
    OverlaysConfig,
)
from start_camera.ingest import Frame
from start_camera.overlays.compositor import Compositor, OverlayContext
from start_camera.overlays.date_time import DateTimeOverlay
from start_camera.overlays.factory import build_compositor
from start_camera.overlays.text import PANEL_ALPHA, draw_text_panel, parse_colour

#
#  GLOBAL VARIABLES
#

WIDTH = 1280
HEIGHT = 720
# 2026-11-01 13:05:09 UTC
MOMENT = 1793538309.0

#
# FUNCTIONS
#


@pytest.fixture(autouse=True)
def utc() -> Iterator[None]:
    """Use UTC as the local time zone."""
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    yield
    if previous is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = previous
    time.tzset()


def make_frame(value: int = 255) -> Frame:
    """Return a frame of one shade."""
    image = np.full((HEIGHT, WIDTH, 3), value, dtype=np.uint8)
    return Frame(image, 0.0, 1, True)


def context(now: float = MOMENT) -> OverlayContext:
    """Return a context at a given time."""
    return OverlayContext(now, WIDTH, HEIGHT, True)


def changed_area(image: np.ndarray, value: int = 255) -> tuple[int, int, int, int]:
    """Return the left, top, right and bottom of the pixels that differ from a shade."""
    rows, columns = np.nonzero((image != value).any(axis=2))
    return columns.min(), rows.min(), columns.max(), rows.max()


def test_default_format_is_time_then_date() -> None:
    overlay = DateTimeOverlay(DateTimeOverlayConfig())
    assert overlay.state(context()) == "13:05:09 01/11/26"


def test_format_is_configurable() -> None:
    overlay = DateTimeOverlay(DateTimeOverlayConfig(format="%H:%M"))
    assert overlay.state(context()) == "13:05"


def test_local_time_zone_is_used() -> None:
    os.environ["TZ"] = "Etc/GMT-2"
    time.tzset()
    overlay = DateTimeOverlay(DateTimeOverlayConfig())
    assert overlay.state(context()).startswith("15:05:09")


def test_display_changes_each_second_and_not_within_one() -> None:
    overlay = DateTimeOverlay(DateTimeOverlayConfig())
    assert overlay.state(context(MOMENT)) == overlay.state(context(MOMENT + 0.99))
    assert overlay.state(context(MOMENT)) != overlay.state(context(MOMENT + 1.0))


def test_appears_in_the_top_left_by_default() -> None:
    compositor = Compositor([DateTimeOverlay(DateTimeOverlayConfig())])
    left, top, right, bottom = changed_area(compositor.render(make_frame()))
    assert left < 40
    assert top < 40
    assert right < WIDTH / 2
    assert bottom < HEIGHT / 4


@pytest.mark.parametrize(
    ("corner", "right_side", "low"),
    [
        (Corner.TOP_RIGHT, True, False),
        (Corner.BOTTOM_LEFT, False, True),
        (Corner.BOTTOM_RIGHT, True, True),
    ],
)
def test_position_is_configurable(corner: Corner, right_side: bool, low: bool) -> None:
    overlay = DateTimeOverlay(DateTimeOverlayConfig(position=corner))
    left, top, right, bottom = changed_area(Compositor([overlay]).render(make_frame()))
    assert (right > WIDTH / 2 and left > WIDTH / 2) == right_side
    assert (bottom > HEIGHT / 2 and top > HEIGHT / 2) == low


def test_size_is_configurable() -> None:
    small = DateTimeOverlay(DateTimeOverlayConfig()).draw(context()).image
    large = DateTimeOverlay(DateTimeOverlayConfig(scale=2)).draw(context()).image
    assert large.shape[1] > small.shape[1] * 1.8


def test_size_follows_the_resolution() -> None:
    overlay = DateTimeOverlay(DateTimeOverlayConfig())
    small = overlay.draw(OverlayContext(MOMENT, 640, 360, True)).image
    large = overlay.draw(context()).image
    assert small.shape[1] / 640 == pytest.approx(large.shape[1] / WIDTH, abs=0.05)


def test_redraws_only_when_the_second_changes() -> None:
    now = [MOMENT]
    calls = []

    class Counting(DateTimeOverlay):
        def draw(self, context: OverlayContext) -> object:
            calls.append(context.now)
            return super().draw(context)

    compositor = Compositor(
        [Counting(DateTimeOverlayConfig())], clock=lambda: now[0]
    )
    for step in range(20):
        now[0] = MOMENT + step * 0.05
        compositor.render(make_frame())
    assert len(calls) == 1
    now[0] = MOMENT + 1
    compositor.render(make_frame())
    assert len(calls) == 2


@pytest.mark.parametrize("background", [0, 255])
def test_text_stands_out_on_any_background(background: int) -> None:
    compositor = Compositor([DateTimeOverlay(DateTimeOverlayConfig())])
    image = compositor.render(make_frame(background))
    left, top, right, bottom = 16, 16, 150, 40
    region = image[top:bottom, left:right]
    assert region.max() >= 250
    assert region.min() <= 90
    assert right > left and bottom > top


def test_text_panel_has_translucent_background_and_solid_text() -> None:
    panel = draw_text_panel("12:34", 720, 1.0, "#ffffff")
    assert panel.shape[2] == 4
    assert panel[:, :, 3].min() == PANEL_ALPHA
    assert panel[:, :, 3].max() == 255
    assert panel[0, 0, :3].tolist() == [0, 0, 0]


def test_text_uses_the_configured_colour() -> None:
    panel = draw_text_panel("12:34", 720, 1.0, "#ff8000")
    brightest = panel[panel[:, :, 3] == 255][0]
    assert brightest[:3].tolist() == [0, 128, 255]


def test_colour_is_parsed_as_blue_green_red() -> None:
    assert parse_colour("#102030") == (0x30, 0x20, 0x10)


def test_disabled_overlay_is_left_out() -> None:
    config = Config(
        overlays=OverlaysConfig(datetime=DateTimeOverlayConfig(enabled=False))
    )
    frame = make_frame()
    assert build_compositor(config).render(frame) is frame.image


def test_enabled_overlay_is_drawn_by_default() -> None:
    frame = make_frame()
    assert build_compositor(Config()).render(frame) is not frame.image


def test_clock_is_set_back_by_the_camera_delay() -> None:
    config = Config(camera=CameraConfig(capture_latency_ms=1500))
    seen = []

    class Recording(DateTimeOverlay):
        def state(self, context: OverlayContext) -> str:
            seen.append(context.now)
            return super().state(context)

    compositor = build_compositor(config, clock=lambda: MOMENT)
    compositor._overlays[0].overlay = Recording(config.overlays.datetime)
    compositor.render(make_frame())
    assert set(seen) == {MOMENT - 1.5}


@pytest.mark.parametrize(
    ("output", "expected"),
    [("yes\n", True), ("no\n", False), ("", None), ("maybe\n", None)],
)
def test_clock_state_is_read(
    monkeypatch: pytest.MonkeyPatch, output: str, expected: bool | None
) -> None:
    reply = subprocess.CompletedProcess([], 0, output, "")
    monkeypatch.setattr(subprocess, "run", lambda *_, **__: reply)
    assert clock_is_synchronised() is expected


def test_missing_timedatectl_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*_: object, **__: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)
    assert clock_is_synchronised() is None


@pytest.mark.parametrize(
    ("state", "warns"), [(True, False), (False, True), (None, False)]
)
def test_warning_only_when_known_to_be_unsynchronised(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    state: bool | None,
    warns: bool,
) -> None:
    monkeypatch.setattr(
        "start_camera.clock_check.clock_is_synchronised", lambda: state
    )
    with caplog.at_level(logging.INFO):
        warn_if_clock_unsynchronised()
    assert ("isn't synchronised" in caplog.text) == warns
