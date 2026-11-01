#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_publishing.py
#  Description: Tests for encoding and publishing.
#  Date: 2026-10-31
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, pytest, ffmpeg, MediaMTX
#
#########################################################

"""Tests for encoding and publishing, with the real ffmpeg and MediaMTX if possible."""

#
# SYSTEM IMPORTS
#

import hashlib
import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import pytest

#
# PROJECT IMPORTS
#
from start_camera import cli, media_server
from start_camera.config import Config, Secrets, StreamConfig, save_config
from start_camera.ingest import Frame
from start_camera.media_server import (
    Backoff,
    MediaServer,
    MediaServerError,
    build_server_config,
    ensure_binary,
    publish_url,
    viewer_urls,
)
from start_camera.output import Publisher, encoder_command

#
#  GLOBAL VARIABLES
#

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="needs ffmpeg",
)
WIDTH = 64
HEIGHT = 36
FPS = 20

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
        time.sleep(0.05)
    return False


class FakeSource:
    """Provide the same frame, counting the requests."""

    def __init__(self) -> None:
        """Make the frame."""
        self.calls = 0
        self.image = np.full((HEIGHT, WIDTH, 3), 128, dtype=np.uint8)

    def latest(self) -> Frame:
        """Return the frame."""
        self.calls += 1
        return Frame(self.image, time.time(), self.calls, live=True)


class FakeServer:
    """A server that's always ready."""

    def ensure_running(self) -> None:
        """Do nothing."""

    def is_ready(self) -> bool:
        """Report that the server is ready."""
        return True


def stream_config() -> StreamConfig:
    """Return small output settings on free ports."""
    return StreamConfig(
        width=WIDTH,
        height=HEIGHT,
        fps=FPS,
        bitrate_kbps=200,
        rtsp_port=free_port(),
        hls_port=free_port(),
        webrtc_port=free_port(),
    )


def probe(url: str) -> dict | None:
    """Return the first video stream's details, or None if it can't be read."""
    command = [
        "ffprobe", "-v", "error", "-rw_timeout", "4000000",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,r_frame_rate,profile",
        "-of", "json", url,
    ]  # fmt: skip
    if url.startswith("rtsp"):
        command[3:5] = ["-rtsp_transport", "tcp", "-timeout", "4000000"]
    try:
        reply = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return None
    streams = json.loads(reply.stdout or "{}").get("streams")
    return streams[0] if streams else None


def fake_encoder(script: str) -> Callable[[StreamConfig], list[str]]:
    """Return a command factory for a Python stand-in for the encoder."""
    return lambda stream: [sys.executable, "-c", script]


@pytest.fixture
def live_stream() -> Iterator[tuple[StreamConfig, MediaServer, Publisher]]:
    """Run the real server and encoder."""
    stream = stream_config()
    server = MediaServer(stream, ensure_binary())
    publisher = Publisher(FakeSource(), server, stream)
    publisher.start()
    try:
        yield stream, server, publisher
    finally:
        publisher.stop()
        server.stop()


@needs_ffmpeg
def test_published_stream_has_the_configured_format(
    live_stream: tuple[StreamConfig, MediaServer, Publisher],
) -> None:
    stream, _, _ = live_stream
    details: dict = {}

    def readable() -> bool:
        details.update(probe(publish_url(stream)) or {})
        return bool(details)

    assert wait_until(readable)
    assert details["codec_name"] == "h264"
    assert (details["width"], details["height"]) == (WIDTH, HEIGHT)
    assert details["profile"] in ("Baseline", "Constrained Baseline")


@needs_ffmpeg
def test_stream_is_served_over_hls(
    live_stream: tuple[StreamConfig, MediaServer, Publisher],
) -> None:
    stream, _, _ = live_stream

    def playable() -> bool:
        url = f"http://127.0.0.1:{stream.hls_port}/start/index.m3u8"
        details = probe(url)
        return details is not None and details["codec_name"] == "h264"

    assert wait_until(playable, timeout=30)


@needs_ffmpeg
def test_encoder_is_restarted_when_killed(
    live_stream: tuple[StreamConfig, MediaServer, Publisher],
) -> None:
    stream, _, publisher = live_stream
    assert wait_until(lambda: probe(publish_url(stream)) is not None)
    first = publisher._encoder
    first._process.kill()
    assert wait_until(lambda: publisher._encoder not in (None, first))
    assert wait_until(lambda: probe(publish_url(stream)) is not None)


@needs_ffmpeg
def test_server_is_restarted_when_killed(
    live_stream: tuple[StreamConfig, MediaServer, Publisher],
) -> None:
    stream, server, _ = live_stream
    assert wait_until(lambda: probe(publish_url(stream)) is not None)
    first = server._process
    first.kill()
    assert wait_until(lambda: server._process not in (None, first))
    assert wait_until(lambda: probe(publish_url(stream)) is not None, timeout=30)


def test_frames_are_sent_at_the_output_rate(tmp_path: Path) -> None:
    source = FakeSource()
    script = "import sys\nwhile sys.stdin.buffer.read(65536): pass"
    stream = StreamConfig(width=WIDTH, height=HEIGHT, fps=FPS)
    with Publisher(source, FakeServer(), stream, command=fake_encoder(script)):
        assert wait_until(lambda: source.calls > 0)
        start_calls, started = source.calls, time.monotonic()
        time.sleep(1.0)
        rate = (source.calls - start_calls) / (time.monotonic() - started)
    assert FPS * 0.8 <= rate <= FPS * 1.1


def test_render_decides_what_is_encoded(tmp_path: Path) -> None:
    output = tmp_path / "frames"
    size = WIDTH * HEIGHT * 3
    script = (
        f"import sys\nopen({str(output)!r}, 'wb').write(sys.stdin.buffer.read({size}))"
    )
    marked = np.full((HEIGHT, WIDTH, 3), 77, dtype=np.uint8)
    stream = StreamConfig(width=WIDTH, height=HEIGHT, fps=FPS)
    with Publisher(
        FakeSource(),
        FakeServer(),
        stream,
        render=lambda frame: marked,
        command=fake_encoder(script),
    ):
        assert wait_until(lambda: output.exists() and output.stat().st_size > 0)
    assert output.read_bytes()[0] == 77


@pytest.mark.parametrize(
    ("script", "timeout"), [("pass", 5.0), ("import time; time.sleep(60)", 0.2)]
)
def test_failed_encoder_is_replaced(
    tmp_path: Path, script: str, timeout: float
) -> None:
    starts = []

    def command(stream: StreamConfig) -> list[str]:
        starts.append(1)
        return [sys.executable, "-c", script]

    big = StreamConfig(width=1920, height=1080, fps=FPS)
    source = FakeSource()
    source.image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    publisher = Publisher(
        source, FakeServer(), big, command=command, write_timeout_s=timeout
    )
    with publisher:
        assert wait_until(lambda: len(starts) >= 2)


def test_stop_ends_the_encoder() -> None:
    stream = StreamConfig(width=WIDTH, height=HEIGHT, fps=FPS)
    script = "import sys\nwhile sys.stdin.buffer.read(65536): pass"
    publisher = Publisher(
        FakeSource(), FakeServer(), stream, command=fake_encoder(script)
    )
    publisher.start()
    assert wait_until(lambda: publisher._encoder is not None)
    encoder = publisher._encoder
    publisher.stop()
    assert not encoder.is_running()


def test_encoder_waits_for_the_server() -> None:
    class NotReady(FakeServer):
        def is_ready(self) -> bool:
            return False

    stream = StreamConfig(width=WIDTH, height=HEIGHT, fps=FPS)
    publisher = Publisher(FakeSource(), NotReady(), stream)
    with publisher:
        time.sleep(0.2)
        assert publisher._encoder is None


def test_encoder_command_uses_the_stream_settings() -> None:
    stream = StreamConfig(fps=20, keyframe_interval_s=2, bitrate_kbps=1500)
    command = encoder_command(stream)
    assert command[command.index("-g") + 1] == "40"
    assert command[command.index("-b:v") + 1] == "1500k"
    assert command[command.index("-video_size") + 1] == "1280x720"
    assert command[-1] == "rtsp://127.0.0.1:8554/start"


def test_only_this_computer_can_publish() -> None:
    users = build_server_config(StreamConfig())["authInternalUsers"]
    publishers = [
        user
        for user in users
        if any(item["action"] == "publish" for item in user["permissions"])
    ]
    assert [user["ips"] for user in publishers] == [["127.0.0.1"]]
    readers = [
        user
        for user in users
        if any(item["action"] == "read" for item in user["permissions"])
    ]
    assert [user["ips"] for user in readers] == [[]]


def test_server_config_uses_the_configured_ports() -> None:
    stream = StreamConfig(rtsp_port=1111, hls_port=2222, webrtc_port=3333)
    config = build_server_config(stream)
    assert config["rtspAddress"] == ":1111"
    assert config["hlsAddress"] == ":2222"
    assert config["webrtcAddress"] == ":3333"
    assert config["rtmp"] is False
    assert config["api"] is False
    assert config["moq"] is False


def test_server_files_stay_out_of_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    script = tmp_path / "server.sh"
    script.write_text("#!/bin/sh\ntouch auto.key\n", encoding="utf-8")
    script.chmod(0o755)
    server = MediaServer(StreamConfig(), script)
    server.ensure_running()
    server._process.wait()
    server.stop()
    assert sorted(item.name for item in tmp_path.iterdir()) == ["server.sh"]


def test_viewer_urls() -> None:
    urls = viewer_urls(StreamConfig(), "10.0.0.2")
    assert urls["TV or browser"] == "http://10.0.0.2:8080/"
    assert urls["Lowest delay browser"] == "http://10.0.0.2:8080/live"
    assert urls["TV media player"] == "http://10.0.0.2:8080/stream.m3u8"
    assert urls["RTSP player"] == "rtsp://10.0.0.2:8554/start"


def test_backoff_grows_and_resets() -> None:
    backoff = Backoff(initial_s=1.0, maximum_s=4.0)
    assert backoff.ready(0.0)
    delays = []
    for _ in range(4):
        backoff.failed(0.0, ran_for_s=1.0)
        delays.append(backoff.delay_s)
    assert delays == [1.0, 2.0, 4.0, 4.0]
    assert not backoff.ready(3.0)
    assert backoff.ready(4.0)
    backoff.failed(0.0, ran_for_s=60.0)
    assert backoff.delay_s == 1.0


def make_archive(tmp_path: Path, content: bytes = b"#!/bin/sh\n") -> tuple[Path, str]:
    """Return a fake MediaMTX archive and its checksum."""
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("mediamtx")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return archive, hashlib.sha256(archive.read_bytes()).hexdigest()


def use_archive(
    monkeypatch: pytest.MonkeyPatch, archive: Path, checksum: str
) -> None:
    """Make ensure_binary fetch a local archive."""
    monkeypatch.setattr(media_server, "MEDIAMTX_URL", archive.as_uri())
    monkeypatch.setattr(
        media_server, "MEDIAMTX_CHECKSUMS", {"test": ("test", checksum)}
    )
    monkeypatch.setattr(media_server.platform, "machine", lambda: "test")


def test_binary_is_downloaded_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, checksum = make_archive(tmp_path)
    use_archive(monkeypatch, archive, checksum)
    cache = tmp_path / "cache"
    binary = ensure_binary(cache)
    assert os.access(binary, os.X_OK)
    archive.unlink()
    assert ensure_binary(cache) == binary
    assert [item.name for item in cache.iterdir()] == [binary.name]


def test_wrong_checksum_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, _ = make_archive(tmp_path)
    use_archive(monkeypatch, archive, "0" * 64)
    cache = tmp_path / "cache"
    with pytest.raises(MediaServerError, match="checksum"):
        ensure_binary(cache)
    assert list(cache.iterdir()) == []


def test_archive_without_program_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz"):
        pass
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    use_archive(monkeypatch, archive, checksum)
    with pytest.raises(MediaServerError, match="unpack"):
        ensure_binary(tmp_path / "cache")


def test_failed_download_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing.tar.gz"
    use_archive(monkeypatch, missing, "0" * 64)
    with pytest.raises(MediaServerError, match="Can't download"):
        ensure_binary(tmp_path / "cache")


def test_unsupported_platform_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(media_server.platform, "machine", lambda: "armv7l")
    with pytest.raises(MediaServerError, match="armv7l"):
        ensure_binary(tmp_path / "cache")


def test_pinned_platforms_include_the_raspberry_pi() -> None:
    assert media_server.MEDIAMTX_CHECKSUMS["aarch64"][0] == "arm64"


class FakeFrameSource:
    """Stand in for the camera in the command-line test."""

    def __init__(self, config: Config, secrets: Secrets) -> None:
        """Accept the arguments of FrameSource."""

    def __enter__(self) -> FakeSource:
        """Provide frames."""
        return FakeSource()

    def __exit__(self, *_: object) -> None:
        """Do nothing."""


class FakeViewer:
    """Stand in for the viewer server in the command-line test."""

    def stop(self) -> None:
        """Do nothing."""


def test_command_streams_until_terminated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "config.json"
    save_config(Config(), path)
    monkeypatch.setattr("sys.argv", ["startcam", "--config", str(path)])
    monkeypatch.setattr(cli, "ensure_binary", lambda: Path("/bin/true"))
    monkeypatch.setattr(cli, "FrameSource", FakeFrameSource)
    monkeypatch.setattr(
        cli, "Publisher", lambda *_, **__: FakeFrameSource(None, None)
    )
    monkeypatch.setattr(cli, "ViewerServer", lambda stream: FakeViewer())
    timer = threading.Timer(0.2, os.kill, (os.getpid(), signal.SIGTERM))
    timer.start()
    previous = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    try:
        assert cli.main() == 0
    finally:
        timer.join()
        signal.signal(signal.SIGTERM, previous[0])
        signal.signal(signal.SIGINT, previous[1])
    captured = capsys.readouterr()
    assert ":8080/" in captured.err
    assert "Stopped" in captured.err
    assert captured.out == ""


def test_command_reports_a_bad_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "sys.argv", ["startcam", "--config", str(tmp_path / "missing.json")]
    )
    assert cli.main() == 1
    assert "cannot read file" in capsys.readouterr().err
