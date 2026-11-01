#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_youtube.py
#  Description: Tests for the YouTube push.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: pytest, ffmpeg, MediaMTX
#
#########################################################

"""Tests for the YouTube push, using stand-ins for ffmpeg and for YouTube."""

#
# SYSTEM IMPORTS
#

import json
import logging
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import pytest

#
# PROJECT IMPORTS
#
from start_camera import pipeline
from start_camera.config import Config, ConfigError, Secrets, StreamConfig, load_config
from start_camera.ingest import Frame
from start_camera.media_server import MediaServer, ensure_binary, publish_url
from start_camera.output import Publisher
from start_camera.youtube import YouTubePush, youtube_command

#
#  GLOBAL VARIABLES
#

KEY = "abcd-efgh/1234"
needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="needs ffmpeg",
)

#
# FUNCTIONS
#


def free_port() -> int:
    """Return a TCP port that's free now."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until(condition: Callable[[], bool], timeout: float = 20.0) -> bool:
    """Return whether a condition becomes true in time."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


def fake_push(
    tmp_path: Path, script: str, **options: float
) -> tuple[YouTubePush, list[int]]:
    """Return a push that runs a Python stand-in, and a record of its starts."""
    program = tmp_path / "fake.py"
    program.write_text(script, encoding="utf-8")
    starts: list[int] = []

    def command(stream: StreamConfig, key: str) -> list[str]:
        starts.append(1)
        return [sys.executable, str(program), key]

    options = {"start_delay_s": 0.0, "stall_timeout_s": 5.0, **options}
    return YouTubePush(StreamConfig(), KEY, command=command, **options), starts


GROWING = """
import sys, time
size = 0
while True:
    size += 1
    print(f"total_size={size}", flush=True)
    time.sleep(0.02)
"""
STALLED = """
import sys, time
print("total_size=1", flush=True)
while True:
    print("total_size=1", flush=True)
    time.sleep(0.02)
"""
EXITS = "import sys\nsys.exit(1)"
ERROR = """
import sys, time
print(f"Failed to open rtmps://host/live2/{sys.argv[1]}", file=sys.stderr, flush=True)
print("Also rtmps://host/live2/abcd-efgh%2F1234", file=sys.stderr, flush=True)
time.sleep(0.1)
"""


def test_command_copies_video_and_adds_silent_audio() -> None:
    command = youtube_command(StreamConfig(), "key")
    assert command[command.index("-c:v") + 1] == "copy"
    assert command[command.index("-c:a") + 1] == "aac"
    assert any("anullsrc" in item for item in command)
    assert publish_url(StreamConfig()) in command
    assert command[-1] == "rtmps://a.rtmp.youtube.com/live2/key"


def test_command_escapes_the_key_and_extra_slash() -> None:
    stream = StreamConfig(youtube_ingest_url="rtmp://example/live/")
    assert youtube_command(stream, "a/b")[-1] == "rtmp://example/live/a%2Fb"


@pytest.mark.parametrize("url", ["http://example/", "rtmp://", "rtmp://a b", ""])
def test_bad_ingest_url_is_rejected(tmp_path: Path, url: str) -> None:
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps({"version": 1, "stream": {"youtube_ingest_url": url}}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="stream.youtube_ingest_url"):
        load_config(path)


def test_healthy_push_keeps_running(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    push, starts = fake_push(tmp_path, GROWING, stall_timeout_s=0.5)
    with caplog.at_level(logging.INFO):
        push.start()
        assert wait_until(lambda: "Sending video to YouTube" in caplog.text)
        time.sleep(1.0)
        push.stop()
    assert len(starts) == 1


def test_exited_push_is_retried(tmp_path: Path) -> None:
    push, starts = fake_push(tmp_path, EXITS)
    push.start()
    assert wait_until(lambda: len(starts) >= 2)
    push.stop()


def test_stalled_push_is_restarted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    push, starts = fake_push(tmp_path, STALLED, stall_timeout_s=0.5)
    push.start()
    assert wait_until(lambda: len(starts) >= 2)
    push.stop()
    assert "no data sent" in caplog.text


def test_stream_key_is_not_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    push, starts = fake_push(tmp_path, ERROR)
    with caplog.at_level(logging.DEBUG):
        push.start()
        assert wait_until(lambda: "Also rtmps" in caplog.text)
        push.stop()
    assert "Failed to open rtmps://host/live2/***" in caplog.text
    assert KEY not in caplog.text
    assert "abcd-efgh" not in caplog.text


def test_missing_program_is_retried_without_the_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def missing(stream: StreamConfig, key: str) -> list[str]:
        return ["/nonexistent/ffmpeg", key]

    push = YouTubePush(StreamConfig(), KEY, command=missing, start_delay_s=0.0)
    push.start()
    assert wait_until(lambda: "Can't start" in caplog.text)
    push.stop()
    assert KEY not in caplog.text


def test_stop_ends_the_process(tmp_path: Path) -> None:
    push, _ = fake_push(tmp_path, GROWING)
    push.start()
    assert wait_until(lambda: push._process is not None)
    process = push._process
    push.stop()
    assert process.poll() is not None


def test_nothing_starts_when_disabled() -> None:
    assert pipeline.start_youtube(Config(), Secrets()) is None


def test_missing_key_is_reported_without_stopping(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = Config(stream=StreamConfig(youtube_enabled=True))
    assert pipeline.start_youtube(config, Secrets()) is None
    assert "no youtube_stream_key" in caplog.text


def test_enabled_push_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []

    class FakePush:
        def __init__(self, stream: StreamConfig, key: str) -> None:
            started.append(key)

        def start(self) -> None:
            started.append("started")

    monkeypatch.setattr(pipeline, "YouTubePush", FakePush)
    config = Config(stream=StreamConfig(youtube_enabled=True))
    assert pipeline.start_youtube(config, Secrets(youtube_stream_key=KEY)) is not None
    assert started == [KEY, "started"]


class FlatSource:
    """Provide a plain frame."""

    def latest(self) -> Frame:
        """Return the frame."""
        image = np.full((36, 64, 3), 128, dtype=np.uint8)
        return Frame(image, time.time(), 0, live=True)


def probe_codecs(url: str) -> set[str]:
    """Return the codecs of an RTSP stream, or nothing if it can't be read."""
    try:
        reply = subprocess.run(
            ["ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-timeout", "4000000",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", url],
            capture_output=True, text=True, timeout=12,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        return set()
    return {line.strip(",") for line in reply.stdout.split()}


class FakeIngest:
    """A MediaMTX server that accepts RTMP as YouTube would."""

    def __init__(self) -> None:
        """Prepare the settings."""
        self.rtmp_port = free_port()
        self.rtsp_port = free_port()
        self._directory = tempfile.TemporaryDirectory()
        self._config = Path(self._directory.name) / "ingest.yml"
        self._config.write_text(
            json.dumps(
                {
                    "logLevel": "error",
                    "hls": False,
                    "webrtc": False,
                    "srt": False,
                    "moq": False,
                    "rtmpAddress": f":{self.rtmp_port}",
                    "rtspAddress": f":{self.rtsp_port}",
                    "rtspTransports": ["tcp"],
                    "paths": {"all_others": {}},
                }
            ),
            encoding="utf-8",
        )
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        """Start the server."""
        self.process = subprocess.Popen(
            [str(ensure_binary()), str(self._config)],
            cwd=self._directory.name,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self) -> None:
        """Stop the server."""
        self.process.kill()
        self.process.wait()

    def received(self) -> set[str]:
        """Return the codecs the server is receiving."""
        return probe_codecs(f"rtsp://127.0.0.1:{self.rtsp_port}/live2/testkey")


@pytest.fixture
def pushed() -> Iterator[tuple[StreamConfig, FakeIngest]]:
    """Run the local stream, a fake YouTube and the push between them."""
    ingest = FakeIngest()
    ingest.start()
    stream = StreamConfig(
        width=64,
        height=36,
        bitrate_kbps=200,
        rtsp_port=free_port(),
        hls_port=free_port(),
        webrtc_port=free_port(),
        viewer_port=free_port(),
        youtube_ingest_url=f"rtmp://127.0.0.1:{ingest.rtmp_port}/live2",
    )
    server = MediaServer(stream, ensure_binary())
    publisher = Publisher(FlatSource(), server, stream)
    publisher.start()
    push = YouTubePush(stream, "testkey", start_delay_s=1.0)
    push.start()
    try:
        yield stream, ingest
    finally:
        push.stop()
        publisher.stop()
        server.stop()
        ingest.stop()


@needs_ffmpeg
def test_push_sends_video_and_silent_audio(
    pushed: tuple[StreamConfig, FakeIngest],
) -> None:
    _, ingest = pushed
    assert wait_until(lambda: {"h264", "aac"} <= ingest.received(), timeout=30)


@needs_ffmpeg
def test_losing_the_ingest_leaves_the_local_stream_and_recovers(
    pushed: tuple[StreamConfig, FakeIngest],
) -> None:
    stream, ingest = pushed
    assert wait_until(lambda: "h264" in ingest.received(), timeout=30)
    ingest.stop()
    time.sleep(1.0)
    assert "h264" in probe_codecs(publish_url(stream))
    ingest.start()
    assert wait_until(lambda: "h264" in ingest.received(), timeout=45)
    assert "h264" in probe_codecs(publish_url(stream))
