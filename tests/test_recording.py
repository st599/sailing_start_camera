#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_recording.py
#  Description: Tests for recording the stream.
#  Date: 2026-11-02
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, pytest, ffmpeg, MediaMTX
#
#########################################################

"""Tests for recording the stream, including with the real MediaMTX."""

#
# SYSTEM IMPORTS
#

import json
import logging
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

#
# PROJECT IMPORTS
#
from start_camera.config import (
    Config,
    RecordingConfig,
    RecordingFormat,
    StreamConfig,
    load_config,
)
from start_camera.ingest import Frame
from start_camera.media_server import (
    MediaServer,
    MediaServerError,
    build_server_config,
    ensure_binary,
    recording_settings,
)
from start_camera.output import Publisher
from start_camera.recording import GIGABYTE, Recorder, RecordingError

#
#  GLOBAL VARIABLES
#

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="needs ffmpeg",
)

#
# FUNCTIONS
#


def test_recording_defaults_follow_the_issue() -> None:
    config = RecordingConfig()
    assert config.delete_after_days == 14
    assert config.format is RecordingFormat.MP4
    assert config.start_with_stream is False


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"segment_minutes": 0}, "segment_minutes"),
        ({"delete_after_days": -1}, "delete_after_days"),
        ({"format": "avi"}, "format"),
        ({"min_free_gb": 0}, "min_free_gb"),
        ({"directory": ""}, "directory"),
    ],
)
def test_bad_settings_are_rejected(changes: dict[str, object], field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        RecordingConfig(**changes)


def test_example_configuration_has_recording() -> None:
    config = load_config(Path(__file__).parent.parent / "examples" / "config.json")
    assert config.recording.delete_after_days == 14


def test_folder_is_absolute_so_it_survives_the_server_stopping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = recording_settings(RecordingConfig(directory="recordings"), False)
    path = settings["recordPath"]
    assert path.startswith(str(tmp_path / "recordings"))
    assert "%path" in path
    assert "%z" in path


@pytest.mark.parametrize(
    ("days", "expected"), [(14, "336h"), (1, "24h"), (0, "0s")]
)
def test_recordings_are_kept_for_the_configured_days(days: int, expected: str) -> None:
    settings = recording_settings(RecordingConfig(delete_after_days=days), False)
    assert settings["recordDeleteAfter"] == expected


@pytest.mark.parametrize(
    ("format_", "expected"),
    [(RecordingFormat.MP4, "fmp4"), (RecordingFormat.MPEG_TS, "mpegts")],
)
def test_formats(format_: object, expected: str) -> None:
    settings = recording_settings(RecordingConfig(format=format_), False)
    assert settings["recordFormat"] == expected


def test_segments_and_loss_on_power_failure() -> None:
    settings = recording_settings(RecordingConfig(segment_minutes=30), True)
    assert settings["recordSegmentDuration"] == "30m"
    assert settings["recordPartDuration"] == "1s"
    assert settings["record"] is True


def test_control_interface_only_listens_on_this_computer() -> None:
    config = build_server_config(StreamConfig(), RecordingConfig(), api_port=1234)
    assert config["api"] is True
    assert config["apiAddress"] == "127.0.0.1:1234"
    api_users = [
        user
        for user in config["authInternalUsers"]
        if any(item["action"] == "api" for item in user["permissions"])
    ]
    assert [user["ips"] for user in api_users] == [["127.0.0.1"]]


def test_no_recording_means_no_control_interface() -> None:
    config = build_server_config(StreamConfig())
    assert config["api"] is False
    assert config["paths"]["start"] == {}


def test_server_without_recording_refuses(tmp_path: Path) -> None:
    server = MediaServer(StreamConfig(), Path("/bin/true"))
    with pytest.raises(MediaServerError, match="isn't set up"):
        server.set_recording(True)
    server.stop()


def test_setting_survives_a_server_restart(tmp_path: Path) -> None:
    server = MediaServer(
        StreamConfig(), Path("/bin/true"), RecordingConfig(directory=str(tmp_path))
    )
    server.set_recording(True)
    written = json.loads(server._config_path.read_text(encoding="utf-8"))
    assert written["paths"]["start"]["record"] is True
    server.set_recording(False)
    written = json.loads(server._config_path.read_text(encoding="utf-8"))
    assert written["paths"]["start"]["record"] is False
    server.stop()


class FakeServer:
    """Record what the recorder asks for."""

    def __init__(self, error: Exception | None = None) -> None:
        """Start with no requests."""
        self.requests: list[bool] = []
        self.error = error

    def set_recording(self, record: bool) -> None:
        """Note the request, or fail."""
        if self.error is not None:
            raise self.error
        self.requests.append(record)


class Disk:
    """A disk whose free space a test can set."""

    def __init__(self, gigabytes: float) -> None:
        """Set the free space."""
        self.free = int(gigabytes * GIGABYTE)

    def __call__(self, path: Path) -> int:
        """Return the free space."""
        return self.free


def recorder(
    tmp_path: Path, disk: Disk, server: FakeServer | None = None
) -> tuple[Recorder, FakeServer]:
    """Return a recorder that checks the disk often."""
    server = server or FakeServer()
    made = Recorder(
        RecordingConfig(directory=str(tmp_path / "rec"), min_free_gb=2),
        server,
        2500,
        free_space=disk,
        check_interval_s=0.02,
    )
    return made, server


def wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Return whether a condition becomes true in time."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_start_and_stop(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    made, server = recorder(tmp_path, Disk(100))
    with caplog.at_level(logging.INFO):
        made.start()
    assert made.is_recording
    assert (tmp_path / "rec").is_dir()
    assert "space for about 89 hours" in caplog.text
    made.start()
    made.close()
    assert server.requests == [True, False]
    assert not made.is_recording


def test_wont_start_with_too_little_space(tmp_path: Path) -> None:
    made, server = recorder(tmp_path, Disk(1))
    with pytest.raises(RecordingError, match="Only 1.0 GB free"):
        made.start()
    assert server.requests == []
    assert not made.is_recording


def test_stops_before_the_disk_fills(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    disk = Disk(100)
    made, server = recorder(tmp_path, disk)
    made.start()
    disk.free = int(1 * GIGABYTE)
    assert wait_until(lambda: not made.is_recording)
    assert server.requests == [True, False]
    assert "The stream carries on" in caplog.text
    made.close()


def test_warns_when_space_is_low(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    disk = Disk(100)
    made, _ = recorder(tmp_path, disk)
    made.start()
    disk.free = int(4 * GIGABYTE)
    assert wait_until(lambda: "Space for recording is low" in caplog.text)
    time.sleep(0.1)
    assert caplog.text.count("Space for recording is low") == 1
    assert made.is_recording
    made.close()


def test_folder_that_cant_be_made_is_explained(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    made = Recorder(
        RecordingConfig(directory=str(blocker / "rec")), FakeServer(), 2500
    )
    with pytest.raises(RecordingError, match="Can't record to"):
        made.start()


def test_server_refusal_is_explained(tmp_path: Path) -> None:
    made, _ = recorder(
        tmp_path, Disk(100), FakeServer(MediaServerError("server restarting"))
    )
    with pytest.raises(RecordingError, match="server restarting"):
        made.start()
    assert not made.is_recording


def test_recording_can_start_again_after_stopping(tmp_path: Path) -> None:
    made, server = recorder(tmp_path, Disk(100))
    made.start()
    made.stop()
    made.start()
    assert server.requests == [True, False, True]
    made.close()


def free_port() -> int:
    """Return a TCP port that's free now."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class MovingSource:
    """Provide frames that change, so the encoder has something to send."""

    def __init__(self) -> None:
        """Start counting."""
        self.count = 0

    def latest(self) -> Frame:
        """Return a frame of a changing shade."""
        self.count += 1
        image = np.full((36, 64, 3), self.count % 255, dtype=np.uint8)
        return Frame(image, time.time(), self.count, live=True)


@pytest.fixture
def live(tmp_path: Path) -> Iterator[tuple[MediaServer, Publisher, Path]]:
    """Run the real server and encoder, with recording set up."""
    stream = StreamConfig(
        width=64,
        height=36,
        bitrate_kbps=200,
        rtsp_port=free_port(),
        hls_port=free_port(),
        webrtc_port=free_port(),
        viewer_port=free_port(),
    )
    folder = tmp_path / "recordings"
    recording = RecordingConfig(directory=str(folder))
    server = MediaServer(stream, ensure_binary(), recording)
    publisher = Publisher(MovingSource(), server, stream)
    publisher.start()
    try:
        yield server, publisher, folder
    finally:
        publisher.stop()
        server.stop()


def files(folder: Path) -> list[Path]:
    """Return the recordings in a folder."""
    return sorted(folder.glob("*.mp4")) if folder.exists() else []


def probe(path: Path) -> dict[str, str]:
    """Return a recording's video codec and length."""
    reply = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,width:format=duration", "-of", "json",
         str(path)],
        capture_output=True, text=True, timeout=15,
    )  # fmt: skip
    data = json.loads(reply.stdout or "{}")
    stream = (data.get("streams") or [{}])[0]
    return {**stream, "duration": data.get("format", {}).get("duration", "0")}


def wait_for_publishing(server: MediaServer, publisher: Publisher) -> None:
    """Wait until the encoder is publishing."""
    assert wait_until(lambda: publisher.frames_sent > 20, timeout=30)


@needs_ffmpeg
def test_recording_copies_the_stream_without_interrupting_it(
    live: tuple[MediaServer, Publisher, Path],
) -> None:
    server, publisher, folder = live
    wait_for_publishing(server, publisher)
    encoder = publisher._encoder
    server.set_recording(True)
    assert wait_until(lambda: bool(files(folder)), timeout=10)
    time.sleep(3)
    server.set_recording(False)
    time.sleep(1)
    recording = probe(files(folder)[0])
    assert recording["codec_name"] == "h264"
    assert recording["width"] == 64
    assert float(recording["duration"]) >= 2
    assert publisher._encoder is encoder
    assert encoder.is_running()
    assert files(folder)[0].name.endswith("_start.mp4")


@needs_ffmpeg
def test_recording_starts_a_new_file_each_time(
    live: tuple[MediaServer, Publisher, Path],
) -> None:
    server, publisher, folder = live
    wait_for_publishing(server, publisher)
    server.set_recording(True)
    assert wait_until(lambda: len(files(folder)) == 1, timeout=10)
    time.sleep(1.2)
    server.set_recording(False)
    time.sleep(1.2)
    server.set_recording(True)
    assert wait_until(lambda: len(files(folder)) == 2, timeout=10)
    server.set_recording(False)


@needs_ffmpeg
def test_recording_carries_on_after_the_server_restarts(
    live: tuple[MediaServer, Publisher, Path],
) -> None:
    server, publisher, folder = live
    wait_for_publishing(server, publisher)
    server.set_recording(True)
    assert wait_until(lambda: len(files(folder)) == 1, timeout=10)
    first = server._process
    first.kill()
    assert wait_until(lambda: server._process not in (None, first), timeout=20)
    assert wait_until(lambda: len(files(folder)) == 2, timeout=30)
    server.set_recording(False)


def test_files_stay_out_of_git() -> None:
    ignored = subprocess.run(
        ["git", "check-ignore", "recordings/2026-05-10_14-00-00+0100_start.mp4"],
        capture_output=True,
        cwd=Path(__file__).parent.parent,
    )
    assert ignored.returncode == 0


def test_configuration_with_recording_runs_in_startcam(tmp_path: Path) -> None:
    from start_camera.config import save_config

    path = tmp_path / "c.json"
    save_config(Config(recording=RecordingConfig(start_with_stream=True)), path)
    reply = subprocess.run(
        [sys.executable, "-m", "start_camera.cli", "--check-config",
         "--config", str(path)],
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )  # fmt: skip
    assert reply.returncode == 0
