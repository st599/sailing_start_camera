#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_benchmark.py
#  Description: Tests for the benchmark.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, pytest
#
#########################################################

"""Tests for the benchmark, using stand-ins for the camera and the encoder."""

#
# SYSTEM IMPORTS
#

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

#
# PROJECT IMPORTS
#
from start_camera import benchmark
from start_camera.benchmark import (
    HD_CHANNEL,
    SD_CHANNEL,
    Result,
    Scenario,
    format_report,
    read_temperature,
    run_scenario,
    usable_encoders,
)
from start_camera.config import Config, Secrets, StreamConfig, save_config
from start_camera.ingest import Frame
from start_camera.output import Publisher

#
#  GLOBAL VARIABLES
#

BUSY_ENCODER = """
import os
os.set_blocking(0, False)
while True:
    try:
        if os.read(0, 65536) == b"":
            break
    except BlockingIOError:
        pass
"""

#
# FUNCTIONS
#


def result(**changes: object) -> Result:
    """Return a healthy result, with any changes."""
    values = {
        "scenario": Scenario(HD_CHANNEL, 1280, 720, 20),
        "output_fps": 20.0,
        "camera_fps": 20.0,
        "late_ticks": 0,
        "live_fraction": 1.0,
        "cpu_cores": 1.0,
        "cpu_by_role": {"encoder": 0.6, "decoder": 0.3},
        "memory_mb": 200.0,
        "render_ms": 1.5,
        "temperature_c": 55.0,
        "processor_count": 4,
    }
    return Result(**{**values, **changes})


def test_healthy_result_is_sustained() -> None:
    assert result().sustained


@pytest.mark.parametrize(
    "changes",
    [
        {"output_fps": 18.0},
        {"late_ticks": 1},
        {"live_fraction": 0.99},
        {"cpu_cores": 3.5},
    ],
)
def test_struggling_result_is_not_sustained(changes: dict[str, object]) -> None:
    assert not result(**changes).sustained


def test_camera_slower_than_output_can_still_be_sustained() -> None:
    assert result(camera_fps=10.0).sustained


def test_scenario_names() -> None:
    assert Scenario(HD_CHANNEL, 1280, 720, 20).name == "HD to 1280x720 at 20 fps"
    assert Scenario(SD_CHANNEL, 640, 360, 15).name == "SD to 640x360 at 15 fps"


def test_report_lists_results_and_encoders() -> None:
    report = format_report(
        [result(), result(temperature_c=None, late_ticks=3)], ["libx264"]
    )
    assert "H.264 encoders that work: libx264" in report
    assert "| HD to 1280x720 at 20 fps | 20.0 | 20.0 | 0 | 1.00 | 0.60 | 0.30" in report
    assert "55 C | yes |" in report
    assert "unknown | no |" in report


def test_report_with_no_encoders() -> None:
    assert "encoders that work: none" in format_report([], [])


def test_temperature_is_read(tmp_path: Path) -> None:
    path = tmp_path / "temp"
    path.write_text("54321\n", encoding="ascii")
    assert read_temperature(path) == pytest.approx(54.321)


def test_missing_temperature_is_none(tmp_path: Path) -> None:
    assert read_temperature(tmp_path / "missing") is None


def test_only_working_encoders_are_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        works = command[command.index("-c:v") + 1] == "good"
        return subprocess.CompletedProcess(command, 0 if works else 1)

    monkeypatch.setattr(subprocess, "run", run)
    assert usable_encoders(("good", "bad")) == ["good"]


def test_process_measures_include_child_processor_time() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "while True: pass"], stdout=subprocess.DEVNULL
    )
    try:
        time.sleep(0.5)
        tree = benchmark._process_tree(os.getpid())
    finally:
        child.kill()
        child.wait()
    assert child.pid in tree
    assert tree[child.pid] > 0


class FakeSource:
    """Stand in for the camera."""

    def __init__(self, config: Config, secrets: Secrets) -> None:
        """Make a frame to the configured size."""
        self.config = config
        stream = config.stream
        self.image = np.zeros((stream.height, stream.width, 3), dtype=np.uint8)
        self.started = time.monotonic()

    def __enter__(self) -> "FakeSource":
        """Start."""
        return self

    def __exit__(self, *_: object) -> None:
        """Stop."""

    def latest(self) -> Frame:
        """Return a frame, with a sequence rising at 10 frames a second."""
        sequence = int((time.monotonic() - self.started) * 10)
        return Frame(self.image, time.time(), sequence, live=True)


class FakeServer:
    """Stand in for the server."""

    def ensure_running(self) -> None:
        """Do nothing."""

    def is_ready(self) -> bool:
        """Report that the server is ready."""
        return True

    def stop(self) -> None:
        """Do nothing."""


def test_scenario_measures_the_pipeline() -> None:
    seen: list[StreamConfig] = []

    def publisher(
        source: FakeSource, server: FakeServer, stream: StreamConfig, **options: object
    ) -> Publisher:
        seen.append(stream)
        # The argument marks this stand-in as the encoder, as the real one's would.
        command = lambda _: [sys.executable, "-c", BUSY_ENCODER, "pipe:0"]  # noqa: E731
        return Publisher(source, server, stream, command=command, **options)

    scenario = Scenario(SD_CHANNEL, 64, 36, 20)
    outcome = run_scenario(
        Config(stream=StreamConfig(youtube_enabled=True)),
        Secrets(),
        scenario,
        1.0,
        settle_s=0.3,
        make_publisher=publisher,
        make_source=FakeSource,
        make_server=lambda stream: FakeServer(),
    )
    assert seen[0].width == 64
    assert seen[0].youtube_enabled is False
    assert 15 <= outcome.output_fps <= 22
    assert 8 <= outcome.camera_fps <= 12
    assert outcome.live_fraction == 1.0
    assert outcome.render_ms > 0
    assert outcome.cpu_by_role["encoder"] > 0
    assert outcome.cpu_cores >= outcome.cpu_by_role["encoder"]
    assert outcome.memory_mb > 0


@pytest.mark.parametrize(
    ("argument", "role"),
    [("pipe:0", "encoder"), ("pipe:1", "decoder"), ("mediamtx", "server")],
)
def test_process_role_comes_from_its_command(argument: str, role: str) -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", argument]
    )
    try:
        # A new process briefly shows its parent's command until it has started.
        end = time.monotonic() + 10
        while benchmark._role(child.pid) != role and time.monotonic() < end:
            time.sleep(0.01)
        assert benchmark._role(child.pid) == role
    finally:
        child.kill()
        child.wait()


def test_command_refuses_to_run_alongside_the_application(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        path = tmp_path / "c.json"
        save_config(
            Config(stream=StreamConfig(rtsp_port=listener.getsockname()[1])), path
        )
        monkeypatch.setattr("sys.argv", ["startcam-benchmark", "--config", str(path)])
        assert benchmark.main() == 1
    assert "Stop startcam" in capsys.readouterr().err


def test_command_writes_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "c.json"
    output = tmp_path / "report.md"
    save_config(Config(), config)
    monkeypatch.setattr(
        "sys.argv",
        ["startcam-benchmark", "--config", str(config), "--output", str(output),
         "--duration", "1"],
    )  # fmt: skip
    monkeypatch.setattr(benchmark, "_check_ports_free", lambda _: None)
    monkeypatch.setattr(benchmark, "run_scenario", lambda *_: result())
    monkeypatch.setattr(benchmark, "usable_encoders", lambda: ["libx264"])
    assert benchmark.main() == 0
    assert "Sustained" in output.read_text(encoding="utf-8")
