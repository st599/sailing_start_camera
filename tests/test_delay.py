#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_delay.py
#  Description: Tests for the camera delay measurement.
#  Date: 2026-10-31
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, pytest
#
#########################################################

"""Tests for the camera delay measurement."""

#
# SYSTEM IMPORTS
#

import json
import urllib.request
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

#
# PROJECT IMPORTS
#
from start_camera import delay as delay_module
from start_camera.config import Config, Secrets, load_config, save_config
from start_camera.delay import (
    FLASH_LENGTH_S,
    FLASH_PERIOD_S,
    FRAME_HEIGHT,
    FRAME_WIDTH,
    DelayError,
    build_rtsp_url,
    estimate_delay,
    start_page_server,
)

#
#  GLOBAL VARIABLES
#

START_TIME = 1_000_000.3
FPS = 20

#
# FUNCTIONS
#


def scene(
    delay_s: float, duration_s: float = 45.0, flash: int = 200
) -> tuple[NDArray[np.float64], NDArray[np.uint8]]:
    """Return frames of a noisy scene with a patch flashing after a delay."""
    rng = np.random.default_rng(1)
    times = START_TIME + np.arange(0, duration_s, 1 / FPS)
    frames = rng.integers(40, 46, (len(times), FRAME_HEIGHT, FRAME_WIDTH)).astype(
        np.uint8
    )
    lit = (times - delay_s) % FLASH_PERIOD_S < FLASH_LENGTH_S
    frames[lit, 20:40, 60:90] = flash
    return times, frames


@pytest.mark.parametrize("delay_s", [0.05, 0.4, 0.75, 1.3, 3.2])
def test_delay_is_recovered_to_within_a_frame(delay_s: float) -> None:
    result = estimate_delay(*scene(delay_s))
    assert abs(result.delay_ms - delay_s * 1000) <= 1000 / FPS
    assert result.flashes >= 4
    assert result.spread_ms <= 1000 / FPS


def test_delay_longer_than_a_second_is_not_wrapped() -> None:
    assert estimate_delay(*scene(1.3)).delay_ms > 1000


def test_scene_without_flash_is_rejected() -> None:
    times, frames = scene(0.5, flash=43)
    with pytest.raises(DelayError, match="No flash seen"):
        estimate_delay(times, frames)


def test_short_capture_is_rejected() -> None:
    with pytest.raises(DelayError, match="Capture for longer"):
        estimate_delay(*scene(0.5, duration_s=16))


def test_capture_shorter_than_settling_time_is_rejected() -> None:
    with pytest.raises(DelayError, match="too short"):
        estimate_delay(*scene(0.5, duration_s=3))


def test_moved_flash_timing_is_rejected() -> None:
    times, frames = scene(0.2)
    frames[:, 20:40, 60:90] = 43
    drift = 0.2 + (times - times[0]) * 0.02
    lit = (times - drift) % FLASH_PERIOD_S < FLASH_LENGTH_S
    frames[lit, 20:40, 60:90] = 200
    with pytest.raises(DelayError, match="Timings disagree"):
        estimate_delay(times, frames)


def test_rtsp_url_escapes_credentials() -> None:
    secrets = Secrets(camera_username="a b", camera_password="p@ss/word")
    url = build_rtsp_url(Config(), secrets, "camera.local")
    assert url == "rtsp://a%20b:p%40ss%2Fword@camera.local:554/av_stream/ch0"


def test_rtsp_url_without_credentials() -> None:
    url = build_rtsp_url(Config(), Secrets(), "camera.local")
    assert url == "rtsp://camera.local:554/av_stream/ch0"


def test_server_provides_page_and_time() -> None:
    server = start_page_server(0)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(f"{base}/") as reply:
            assert b"requestAnimationFrame" in reply.read()
        with urllib.request.urlopen(f"{base}/time") as reply:
            assert json.load(reply)["time"] > 0
    finally:
        server.shutdown()
        server.server_close()


def test_write_saves_result_to_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    save_config(Config(), path)
    monkeypatch.setattr(
        "sys.argv",
        ["startcam-delay", "--config", str(path), "--host", "127.0.0.1",
         "--port", "0", "--write", "--no-wait"],
    )  # fmt: skip
    monkeypatch.setattr(delay_module, "capture_frames", lambda *_: scene(0.6))
    assert delay_module.main() == 0
    assert abs(load_config(path).camera.capture_latency_ms - 600) <= 1000 / FPS


def test_failed_measurement_leaves_configuration_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    save_config(Config(), path)
    monkeypatch.setattr(
        "sys.argv",
        ["startcam-delay", "--config", str(path), "--host", "127.0.0.1",
         "--port", "0", "--write", "--no-wait"],
    )  # fmt: skip
    monkeypatch.setattr(
        delay_module, "capture_frames", lambda *_: scene(0.6, flash=43)
    )
    assert delay_module.main() == 1
    assert load_config(path).camera.capture_latency_ms == 0
