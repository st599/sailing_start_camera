#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_ingest.py
#  Description: Tests for reading camera frames.
#  Date: 2026-10-31
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, pytest
#
#########################################################

"""Tests for reading camera frames, using a stand-in for ffmpeg."""

#
# SYSTEM IMPORTS
#

import logging
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

#
# PROJECT IMPORTS
#
from start_camera.config import CameraConfig, Config, Secrets, StreamConfig
from start_camera.discovery import DiscoveryError
from start_camera.ingest import (
    FrameSource,
    IngestTiming,
    ffmpeg_command,
    signal_lost_image,
)

#
#  GLOBAL VARIABLES
#

WIDTH = 32
HEIGHT = 16
FAST = IngestTiming(
    first_frame_timeout=0.5,
    stall_timeout=0.2,
    signal_lost_after=0.15,
    backoff_initial=0.01,
    backoff_max=0.02,
)
SECRETS = Secrets(camera_username="user", camera_password="hunter2")
FAKE_FFMPEG = """
import sys, time
value, count, then = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
for _ in range(count):
    sys.stdout.buffer.write(bytes([value]) * {size})
    sys.stdout.buffer.flush()
    time.sleep(0.01)
if then == "partial":
    sys.stdout.buffer.write(b"x")
    sys.stdout.buffer.flush()
if then == "stall":
    time.sleep(30)
"""
FRAME_SIZE = WIDTH * HEIGHT * 3

#
# FUNCTIONS
#


def make_config(host: str | None = "camera.local") -> Config:
    """Return a configuration with small frames."""
    return Config(
        camera=CameraConfig(host=host), stream=StreamConfig(width=WIDTH, height=HEIGHT)
    )


class FakeDecoder:
    """Stand in for ffmpeg, following a script of (value, frames, then) for each run."""

    def __init__(self, tmp_path: Path, runs: list[tuple[int, int, str]]) -> None:
        """Write the stand-in program."""
        self.runs = runs
        self.urls: list[str] = []
        self.program = tmp_path / "fake_ffmpeg.py"
        self.program.write_text(
            FAKE_FFMPEG.replace("{size}", str(FRAME_SIZE)), encoding="utf-8"
        )

    def __call__(self, url: str, width: int, height: int) -> list[str]:
        """Return the command for the next run; afterwards, exit with no frames."""
        self.urls.append(url)
        value, count, then = self.runs.pop(0) if self.runs else (0, 0, "exit")
        return [sys.executable, str(self.program), str(value), str(count), then]


def wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Return whether a condition becomes true in time."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.005)
    return False


def test_frames_arrive_with_the_configured_shape(tmp_path: Path) -> None:
    decoder = FakeDecoder(tmp_path, [(7, 500, "stall")])
    with FrameSource(
        make_config(), SECRETS, timing=FAST, command=decoder
    ) as source:
        assert wait_until(lambda: source.latest().live)
        frame = source.latest()
    assert frame.image.shape == (HEIGHT, WIDTH, 3)
    assert frame.image[0, 0, 0] == 7
    assert frame.sequence >= 1


def test_frames_are_read_only(tmp_path: Path) -> None:
    decoder = FakeDecoder(tmp_path, [(7, 500, "stall")])
    with FrameSource(make_config(), SECRETS, timing=FAST, command=decoder) as source:
        assert wait_until(lambda: source.latest().live)
        with pytest.raises(ValueError):
            source.latest().image[0, 0, 0] = 1


def test_slow_consumer_gets_the_newest_frame(tmp_path: Path) -> None:
    decoder = FakeDecoder(tmp_path, [(7, 500, "stall")])
    with FrameSource(make_config(), SECRETS, timing=FAST, command=decoder) as source:
        assert wait_until(lambda: source.latest().live)
        first = source.latest().sequence
        time.sleep(0.1)
        assert source.latest().sequence > first + 1


def test_startup_failure_gives_message_then_recovers(tmp_path: Path) -> None:
    decoder = FakeDecoder(tmp_path, [(0, 0, "exit"), (0, 0, "exit"), (9, 500, "stall")])
    with FrameSource(make_config(), SECRETS, timing=FAST, command=decoder) as source:
        frame = source.latest()
        assert not frame.live
        assert frame.image.shape == (HEIGHT, WIDTH, 3)
        assert wait_until(lambda: source.latest().live)
        assert source.latest().image[0, 0, 0] == 9


def test_missing_decoder_gives_message_without_crashing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def missing(url: str, width: int, height: int) -> list[str]:
        return ["/nonexistent/ffmpeg"]

    with FrameSource(make_config(), SECRETS, timing=FAST, command=missing) as source:
        assert wait_until(lambda: "signal lost" in caplog.text)
        assert not source.latest().live


@pytest.mark.parametrize("ending", ["exit", "partial", "stall"])
def test_dropout_shows_message_then_recovers(tmp_path: Path, ending: str) -> None:
    decoder = FakeDecoder(tmp_path, [(7, 5, ending), (9, 500, "stall")])
    timing = replace(FAST, backoff_initial=0.5, backoff_max=0.5)
    with FrameSource(make_config(), SECRETS, timing=timing, command=decoder) as source:
        assert wait_until(lambda: source.latest().live)
        assert wait_until(lambda: not source.latest().live)
        assert wait_until(
            lambda: source.latest().live and source.latest().image[0, 0, 0] == 9
        )


def test_sequence_keeps_rising_after_recovery(tmp_path: Path) -> None:
    decoder = FakeDecoder(tmp_path, [(7, 5, "exit"), (9, 500, "stall")])
    with FrameSource(make_config(), SECRETS, timing=FAST, command=decoder) as source:
        assert wait_until(lambda: source.latest().live)
        first = source.latest().sequence
        assert wait_until(lambda: source.latest().image[0, 0, 0] == 9)
        assert source.latest().sequence > first


def test_camera_is_rediscovered_after_repeated_failures(tmp_path: Path) -> None:
    decoder = FakeDecoder(tmp_path, [(0, 0, "exit")] * 3 + [(9, 500, "stall")])
    with FrameSource(
        make_config(),
        SECRETS,
        timing=FAST,
        command=decoder,
        resolve_host=lambda: "new.local",
    ) as source:
        assert wait_until(lambda: source.latest().live)
    assert "camera.local" in decoder.urls[0]
    assert "new.local" in decoder.urls[-1]


def test_camera_with_no_address_is_discovered_first(tmp_path: Path) -> None:
    decoder = FakeDecoder(tmp_path, [(9, 500, "stall")])
    with FrameSource(
        make_config(host=None),
        SECRETS,
        timing=FAST,
        command=decoder,
        resolve_host=lambda: "found.local",
    ) as source:
        assert wait_until(lambda: source.latest().live)
    assert "found.local" in decoder.urls[0]


def test_failed_discovery_keeps_retrying(tmp_path: Path) -> None:
    attempts = []

    def resolve() -> str:
        attempts.append(1)
        if len(attempts) < 3:
            raise DiscoveryError("No camera found")
        return "found.local"

    decoder = FakeDecoder(tmp_path, [(9, 500, "stall")])
    with FrameSource(
        make_config(host=None),
        SECRETS,
        timing=FAST,
        command=decoder,
        resolve_host=resolve,
    ) as source:
        assert not source.latest().live
        assert wait_until(lambda: source.latest().live)


def test_stop_ends_the_decoder_and_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processes = []
    original = subprocess.Popen

    def recording(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", recording)
    decoder = FakeDecoder(tmp_path, [(7, 3, "stall")])
    source = FrameSource(make_config(), SECRETS, timing=FAST, command=decoder)
    source.start()
    assert wait_until(lambda: source.latest().live)
    source.stop()
    assert source._thread is not None
    assert not source._thread.is_alive()
    assert all(process.poll() is not None for process in processes)


def test_stop_is_prompt_while_the_stream_is_quiet(tmp_path: Path) -> None:
    decoder = FakeDecoder(tmp_path, [(7, 3, "stall")])
    timing = replace(FAST, first_frame_timeout=60, stall_timeout=60)
    source = FrameSource(make_config(), SECRETS, timing=timing, command=decoder)
    source.start()
    assert wait_until(lambda: source.latest().live)
    started = time.monotonic()
    source.stop()
    assert time.monotonic() - started < 2


def test_password_is_not_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    decoder = FakeDecoder(tmp_path, [(7, 2, "exit"), (0, 0, "exit")])
    with (
        caplog.at_level(logging.DEBUG),
        FrameSource(make_config(), SECRETS, timing=FAST, command=decoder),
    ):
        assert wait_until(lambda: "signal lost" in caplog.text)
    assert "hunter2" in decoder.urls[0]
    assert "hunter2" not in caplog.text


def test_signal_lost_image_has_a_message_on_black() -> None:
    image = signal_lost_image(1280, 720)
    assert image.shape == (720, 1280, 3)
    assert image[0, 0].tolist() == [0, 0, 0]
    assert image.max() == 255
    assert image[300:420].max() == 255
    assert not image.flags.writeable


def test_signal_lost_image_works_at_smallest_size() -> None:
    assert signal_lost_image(16, 16).shape == (16, 16, 3)


def test_ffmpeg_command_requests_the_frame_size() -> None:
    command = ffmpeg_command("rtsp://camera/", 1280, 720)
    assert "scale=1280:720" in command
    assert command[command.index("-i") + 1] == "rtsp://camera/"
    assert command[-1] == "pipe:1"
