#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_command_line.py
#  Description: End-to-end tests of startcam.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: pytest, numpy, opencv, ffmpeg, MediaMTX
#
#########################################################

"""End-to-end tests of startcam, with an ffmpeg test source as the camera."""

#
# SYSTEM IMPORTS
#

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import cv2
import numpy as np
import pytest

#
# PROJECT IMPORTS
#
from start_camera import cli
from start_camera.config import load_config
from start_camera.media_server import ensure_binary

#
#  GLOBAL VARIABLES
#

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="needs ffmpeg",
)
GREY_SOURCE = "color=c=gray:size=640x360:rate=10"

#
# FUNCTIONS
#


def free_port() -> int:
    """Return a TCP port that's free now."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until(condition: Callable[[], bool], timeout: float = 40.0) -> bool:
    """Return whether a condition becomes true in time."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.2)
    return False


def descendants(root: int) -> list[int]:
    """Return the processes descended from a process."""
    parents = {}
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            except OSError:
                continue
            parents[int(entry.name)] = int(fields[1])
    found, pending = [], [root]
    while pending:
        current = pending.pop()
        for pid, parent in parents.items():
            if parent == current:
                found.append(pid)
                pending.append(pid)
    return found


def is_running(pid: int) -> bool:
    """Return whether a process exists and isn't a zombie."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return False
    return state != "Z"


def grab_frame(url: str) -> np.ndarray | None:
    """Return one frame of a stream, if it can be read."""
    try:
        reply = subprocess.run(
            ["ffmpeg", "-v", "quiet", "-rtsp_transport", "tcp",
             "-timeout", "4000000", "-i", url, "-frames:v", "1",
             "-f", "image2pipe", "-c:v", "png", "pipe:1"],
            capture_output=True, timeout=15,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        return None
    if not reply.stdout:
        return None
    return cv2.imdecode(np.frombuffer(reply.stdout, np.uint8), cv2.IMREAD_COLOR)


class FakeCamera:
    """An RTSP server fed by an ffmpeg test source."""

    def __init__(self, directory: Path) -> None:
        """Start the server and the source."""
        self.port = free_port()
        path = directory / "camera.yml"
        path.write_text(
            json.dumps(
                {
                    "logLevel": "error",
                    "hls": False,
                    "webrtc": False,
                    "srt": False,
                    "moq": False,
                    "rtmp": False,
                    "rtspAddress": f":{self.port}",
                    "rtspTransports": ["tcp"],
                    "paths": {"all_others": {}},
                }
            ),
            encoding="utf-8",
        )
        self._server = subprocess.Popen(
            [str(ensure_binary()), str(path)],
            cwd=directory,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)
        self._source = subprocess.Popen(
            ["ffmpeg", "-v", "quiet", "-re", "-f", "lavfi", "-i", GREY_SOURCE,
             "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
             "-g", "10", "-f", "rtsp", "-rtsp_transport", "tcp",
             f"rtsp://127.0.0.1:{self.port}/camera"],
            stdin=subprocess.DEVNULL,
        )  # fmt: skip

    def stop(self) -> None:
        """Stop the server and the source."""
        for process in (self._source, self._server):
            process.kill()
            process.wait()


@pytest.fixture
def running(tmp_path: Path) -> Iterator[tuple[subprocess.Popen[bytes], int, Path]]:
    """Run startcam against the stand-in camera."""
    camera = FakeCamera(tmp_path)
    rtsp_port = free_port()
    config = {
        "version": 1,
        "camera": {
            "host": "127.0.0.1",
            "rtsp_port": camera.port,
            "rtsp_path": "/camera",
        },
        "stream": {
            "width": 640,
            "height": 360,
            "bitrate_kbps": 300,
            "rtsp_port": rtsp_port,
            "hls_port": free_port(),
            "webrtc_port": free_port(),
            "viewer_port": free_port(),
        },
        "sequences": {
            "day_flags": ["Y"],
            "items": [{"name": "Topper", "class_flag": "T"}],
        },
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    log = tmp_path / "run.log"
    process = subprocess.Popen(
        [sys.executable, "-m", "start_camera.cli", "--config", str(path),
         "--log-file", str(log)],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        yield process, rtsp_port, log
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        camera.stop()


def live_frame(port: int) -> np.ndarray | None:
    """Return a frame showing the stand-in camera's picture, not the outage message."""
    frame = grab_frame(f"rtsp://127.0.0.1:{port}/start")
    if frame is None or frame[300, 300].mean() < 50:
        return None
    return frame


@needs_ffmpeg
def test_stream_has_the_camera_picture_and_overlays(
    running: tuple[subprocess.Popen[bytes], int, Path],
) -> None:
    _, port, _ = running
    frames: list[np.ndarray] = []

    def picture_arrives() -> bool:
        frame = live_frame(port)
        if frame is not None:
            frames.append(frame)
        return frame is not None

    assert wait_until(picture_arrives)
    frame = frames[0]
    assert frame.shape == (360, 640, 3)
    # The picture is mid grey, while the overlays' panels are dark.
    assert frame[20, 20:100].mean() < frame[300, 300].mean()
    assert frame[20, 585:620].mean() < frame[300, 300].mean()


@needs_ffmpeg
def test_terminating_leaves_no_processes(
    running: tuple[subprocess.Popen[bytes], int, Path],
) -> None:
    process, port, log = running
    assert wait_until(lambda: live_frame(port) is not None)
    children = descendants(process.pid)
    assert len(children) >= 3
    process.send_signal(signal.SIGTERM)
    assert process.wait(timeout=20) == 0
    assert wait_until(lambda: not any(map(is_running, children)), timeout=5)
    assert "Stopped" in log.read_text(encoding="utf-8")


@needs_ffmpeg
@pytest.mark.skipif(shutil.which("setpriv") is None, reason="needs setpriv")
def test_being_killed_outright_leaves_no_processes(
    running: tuple[subprocess.Popen[bytes], int, Path],
) -> None:
    process, port, _ = running
    assert wait_until(lambda: live_frame(port) is not None)
    children = descendants(process.pid)
    process.kill()
    process.wait()
    assert wait_until(lambda: not any(map(is_running, children)), timeout=15)


@needs_ffmpeg
def test_runs_with_stdin_closed(tmp_path: Path) -> None:
    code = (
        "import sys; sys.argv = ['startcam', '--check-config', '--config', "
        f"{str(write_config(tmp_path / 'c.json'))!r}]; "
        "from start_camera.cli import main; sys.exit(main())"
    )
    reply = subprocess.run(
        [sys.executable, "-c", code], stdin=None, close_fds=True, cwd=tmp_path
    )
    assert reply.returncode == 0


def test_keys_are_off_when_stdin_is_closed() -> None:
    code = (
        "import os; os.close(0); "
        "from start_camera.terminal_keys import TerminalKeys; "
        "import sys; sys.exit(TerminalKeys(None, 0).start() is not False)"
    )
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_the_command_line_application_doesnt_need_the_gui_library() -> None:
    code = "import sys, start_camera.cli; sys.exit('PySide6' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def run_main(
    monkeypatch: pytest.MonkeyPatch, *arguments: str
) -> int:
    """Run the application with some arguments."""
    monkeypatch.setattr("sys.argv", ["startcam", *arguments])
    return cli.main()


def write_config(path: Path, **sections: object) -> Path:
    """Write a configuration file."""
    path.write_text(json.dumps({"version": 1, **sections}), encoding="utf-8")
    return path


def test_check_config_accepts_a_good_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = write_config(tmp_path / "c.json", camera={"host": "camera"})
    assert run_main(monkeypatch, "--check-config", "--config", str(path)) == 0
    assert "is valid" in capsys.readouterr().err


def test_check_config_rejects_a_bad_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = write_config(tmp_path / "c.json", camera={"rtsp_port": 0})
    assert run_main(monkeypatch, "--check-config", "--config", str(path)) == 1
    assert "camera.rtsp_port" in capsys.readouterr().err


def test_check_config_warns_about_what_will_cause_problems(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("STARTCAM_CAMERA_PASSWORD", raising=False)
    monkeypatch.delenv("STARTCAM_YOUTUBE_STREAM_KEY", raising=False)
    path = write_config(tmp_path / "c.json", stream={"youtube_enabled": True})
    assert run_main(monkeypatch, "--check-config", "--config", str(path)) == 0
    errors = capsys.readouterr().err
    assert "camera.host isn't set" in errors
    assert "no camera password" in errors
    assert "no youtube_stream_key" in errors


def test_check_config_doesnt_change_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_config(tmp_path / "c.json")
    before = path.read_text(encoding="utf-8")
    run_main(monkeypatch, "--check-config", "--config", str(path))
    assert path.read_text(encoding="utf-8") == before
    assert load_config(path).version == 1


@pytest.mark.parametrize(
    "arguments",
    [[], ["--write"], ["--check-config"], ["--log-level", "loud", "--scan"]],
)
def test_bad_arguments_are_refused(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    with pytest.raises(SystemExit) as error:
        run_main(monkeypatch, *arguments)
    assert error.value.code == 2


def test_log_file_and_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_config(tmp_path / "c.json")
    log = tmp_path / "run.log"
    arguments = ["--check-config", "--config", str(config), "--log-file", str(log)]
    assert run_main(monkeypatch, *arguments, "--log-level", "warning") == 0
    text = log.read_text(encoding="utf-8")
    assert "is valid" not in text
    assert "camera.host isn't set" in text


def test_unwritable_log_file_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = write_config(tmp_path / "c.json")
    log = tmp_path / "missing" / "run.log"
    arguments = ["--check-config", "--config", str(config), "--log-file", str(log)]
    assert run_main(monkeypatch, *arguments) == 1
    assert "Can't open the log file" in capsys.readouterr().err


def test_viewer_port_in_use_is_reported_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "ensure_binary", lambda: Path("/bin/true"))
    previous = [
        signal.getsignal(item)
        for item in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    ]
    with socket.socket() as listener:
        listener.bind(("", 0))
        listener.listen()
        path = write_config(
            tmp_path / "c.json", stream={"viewer_port": listener.getsockname()[1]}
        )
        try:
            assert run_main(monkeypatch, "--config", str(path)) == 1
        finally:
            for item, handler in zip(
                (signal.SIGINT, signal.SIGTERM, signal.SIGHUP), previous, strict=True
            ):
                signal.signal(item, handler)
    assert os.path.exists(path)
    assert "Address already in use" in capsys.readouterr().err
