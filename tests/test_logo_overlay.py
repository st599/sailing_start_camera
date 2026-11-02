#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_logo_overlay.py
#  Description: Tests for the logo overlay.
#  Date: 2026-11-02
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, opencv, pytest
#
#########################################################

"""Tests for the logo overlay."""

#
# SYSTEM IMPORTS
#

import logging
from pathlib import Path

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

#
# PROJECT IMPORTS
#
from start_camera import cli
from start_camera.config import (
    Config,
    Corner,
    DateTimeOverlayConfig,
    LogoOverlayConfig,
    OverlaysConfig,
    save_config,
)
from start_camera.ingest import Frame
from start_camera.overlays.compositor import Compositor, OverlayContext
from start_camera.overlays.factory import build_compositor
from start_camera.overlays.logo import LogoOverlay

#
#  GLOBAL VARIABLES
#

WIDTH = 1280
HEIGHT = 720

#
# FUNCTIONS
#


@pytest.fixture
def logo(tmp_path: Path) -> Path:
    """Return a 200x100 logo: a red square on the left, transparent on the right."""
    image = np.zeros((100, 200, 4), dtype=np.uint8)
    image[:, :100] = (0, 0, 255, 255)
    path = tmp_path / "logo.png"
    cv2.imwrite(str(path), image)
    return path


def context(width: int = WIDTH, height: int = HEIGHT) -> OverlayContext:
    """Return a drawing context."""
    return OverlayContext(0.0, width, height, True)


def draw(path: Path, **options: object) -> object:
    """Draw the logo with some settings."""
    return LogoOverlay(LogoOverlayConfig(enabled=True, path=str(path), **options)).draw(
        context()
    )


def test_default_is_the_bottom_left_at_twelve_percent(logo: Path) -> None:
    layer = draw(logo)
    height, width = layer.image.shape[:2]
    assert height == round(0.12 * HEIGHT)
    assert width == round(200 * height / 100)
    assert layer.x == 16
    assert layer.y + height == HEIGHT - 16


@pytest.mark.parametrize(
    ("corner", "right", "bottom"),
    [
        (Corner.TOP_LEFT, False, False),
        (Corner.TOP_RIGHT, True, False),
        (Corner.BOTTOM_LEFT, False, True),
        (Corner.BOTTOM_RIGHT, True, True),
    ],
)
def test_any_corner(logo: Path, corner: Corner, right: bool, bottom: bool) -> None:
    layer = draw(logo, position=corner)
    assert (layer.x > WIDTH / 2) == right
    assert (layer.y > HEIGHT / 2) == bottom


@pytest.mark.parametrize("size", [(640, 360), (1280, 720), (1920, 1080)])
def test_size_follows_the_video(logo: Path, size: tuple[int, int]) -> None:
    overlay = LogoOverlay(LogoOverlayConfig(enabled=True, path=str(logo)))
    layer = overlay.draw(context(*size))
    assert layer.image.shape[0] == round(0.12 * size[1])


def test_height_is_configurable(logo: Path) -> None:
    assert draw(logo, height=0.25).image.shape[0] == 180


def test_transparency_shows_the_picture_through(logo: Path) -> None:
    overlay = LogoOverlay(LogoOverlayConfig(enabled=True, path=str(logo)))
    frame = Frame(np.full((HEIGHT, WIDTH, 3), 50, np.uint8), 0.0, 1, True)
    image = Compositor([overlay], clock=lambda: 0.0).render(frame)
    layer = overlay.draw(context())
    middle = layer.y + layer.image.shape[0] // 2
    assert image[middle, layer.x + 10].tolist() == [0, 0, 255]
    assert image[middle, layer.x + layer.image.shape[1] - 10].tolist() == [50, 50, 50]


def test_opacity(logo: Path) -> None:
    layer = draw(logo, opacity=0.5)
    assert layer.image[:, :, 3].max() == 127


def test_missing_file_draws_nothing_and_warns_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    overlay = LogoOverlay(
        LogoOverlayConfig(enabled=True, path=str(tmp_path / "missing.png"))
    )
    assert overlay.draw(context()) is None
    assert overlay.draw(context()) is None
    assert caplog.text.count("can't be shown") == 1


def test_unreadable_file_draws_nothing(tmp_path: Path) -> None:
    path = tmp_path / "logo.png"
    path.write_text("not an image", encoding="utf-8")
    assert draw(path) is None


def test_folder_instead_of_a_file_draws_nothing(tmp_path: Path) -> None:
    assert draw(tmp_path) is None


def test_enabled_logo_needs_a_file() -> None:
    with pytest.raises(ValidationError, match="needs an image file"):
        LogoOverlayConfig(enabled=True)


def test_height_is_limited() -> None:
    with pytest.raises(ValidationError):
        LogoOverlayConfig(height=0.6)


def test_off_by_default() -> None:
    assert not Config().overlays.logo.enabled


def kinds(config: Config) -> list[type]:
    """Return the types of overlay a configuration gives."""
    compositor = build_compositor(config, clock=lambda: 0.0)
    return [type(item.overlay) for item in compositor._overlays]


def test_factory_adds_the_logo_beneath_the_text(logo: Path) -> None:
    config = Config(
        overlays=OverlaysConfig(logo=LogoOverlayConfig(enabled=True, path=str(logo)))
    )
    assert kinds(config)[0] is LogoOverlay
    assert LogoOverlay not in kinds(Config())


def test_sharing_a_corner_is_warned_about(
    logo: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = Config(
        overlays=OverlaysConfig(
            logo=LogoOverlayConfig(enabled=True, path=str(logo)),
            datetime=DateTimeOverlayConfig(position=Corner.BOTTOM_LEFT),
        )
    )
    build_compositor(config, clock=lambda: 0.0)
    assert "share the bottom_left corner" in caplog.text


def test_the_default_corner_is_free(
    logo: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = Config(
        overlays=OverlaysConfig(logo=LogoOverlayConfig(enabled=True, path=str(logo)))
    )
    with caplog.at_level(logging.WARNING):
        build_compositor(config, clock=lambda: 0.0)
    assert "share" not in caplog.text


def test_check_config_warns_about_a_missing_logo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "c.json"
    save_config(
        Config(
            overlays=OverlaysConfig(
                logo=LogoOverlayConfig(enabled=True, path=str(tmp_path / "gone.png"))
            )
        ),
        path,
    )
    arguments = ["startcam", "--check-config", "--config", str(path)]
    monkeypatch.setattr("sys.argv", arguments)
    assert cli.main() == 0
    assert "doesn't exist" in capsys.readouterr().err
