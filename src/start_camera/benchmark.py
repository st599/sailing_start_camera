#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: benchmark.py
#  Description: Measure what a computer can sustain.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, opencv, ffmpeg, MediaMTX
#
#########################################################

"""Measure what a computer can sustain, to choose the video settings.

Each scenario runs the real pipeline from the camera to the server, with overlays that
are as busy as real ones, and records how well the computer keeps up.
"""

#
# SYSTEM IMPORTS
#

import argparse
import logging
import os
import platform
import socket
import subprocess
import time
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

#
# PROJECT IMPORTS
#
from start_camera.config import (
    Config,
    ConfigError,
    Corner,
    Secrets,
    StreamConfig,
    load_config,
    load_secrets,
)
from start_camera.ingest import Frame, FrameSource
from start_camera.logging_setup import configure_logging
from start_camera.media_server import MediaServer, MediaServerError, ensure_binary
from start_camera.output import Publisher
from start_camera.overlays.compositor import (
    Compositor,
    Layer,
    OverlayContext,
    corner_position,
)

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

HD_CHANNEL = "/av_stream/ch0"
SD_CHANNEL = "/av_stream/ch1"
SETTLE_S = 10.0
DEFAULT_DURATION_S = 60.0
THERMAL_ZONE = Path("/sys/class/thermal/thermal_zone0/temp")
ENCODER_CANDIDATES = ("libx264", "h264_v4l2m2m", "h264_vaapi", "h264_nvenc")
MIN_FPS_FRACTION = 0.95
MAX_CPU_FRACTION = 0.8
PROCESS_ROLES = (
    ("encoder", "pipe:0"),
    ("decoder", "pipe:1"),
    ("server", "mediamtx"),
)

#
# FUNCTIONS
#


@dataclass(frozen=True)
class Scenario:
    """The settings to test."""

    channel: str
    width: int
    height: int
    fps: int

    @property
    def name(self) -> str:
        """Return a short description."""
        channel = "HD" if self.channel == HD_CHANNEL else "SD"
        return f"{channel} to {self.width}x{self.height} at {self.fps} fps"


DEFAULT_SCENARIOS = (
    Scenario(HD_CHANNEL, 1280, 720, 20),
    Scenario(HD_CHANNEL, 1280, 720, 15),
    Scenario(HD_CHANNEL, 960, 540, 20),
    Scenario(SD_CHANNEL, 1280, 720, 20),
    Scenario(SD_CHANNEL, 640, 360, 20),
)


@dataclass(frozen=True)
class Result:
    """What a scenario achieved."""

    scenario: Scenario
    output_fps: float
    camera_fps: float
    late_ticks: int
    live_fraction: float
    cpu_cores: float
    cpu_by_role: dict[str, float]
    memory_mb: float
    render_ms: float
    temperature_c: float | None
    processor_count: int

    @property
    def sustained(self) -> bool:
        """Return whether the settings can be relied on."""
        return (
            self.output_fps >= self.scenario.fps * MIN_FPS_FRACTION
            and self.late_ticks == 0
            and self.live_fraction == 1.0
            and self.cpu_cores <= self.processor_count * MAX_CPU_FRACTION
        )


class BusyOverlay:
    """Stand in for a real overlay, redrawing once a second like a clock."""

    def __init__(self, corner: Corner, width: int, height: int) -> None:
        """Set the overlay's corner and size."""
        self._corner = corner
        self._width = width
        self._height = height

    def state(self, context: OverlayContext) -> Hashable:
        """Change every second."""
        return int(context.now)

    def draw(self, context: OverlayContext) -> Layer:
        """Draw some text on a translucent background."""
        image = np.full((self._height, self._width, 4), (0, 0, 0, 128), dtype=np.uint8)
        text = f"{int(context.now) % 100000}"
        cv2.putText(
            image,
            text,
            (8, self._height - 12),
            cv2.FONT_HERSHEY_SIMPLEX,
            self._height / 40,
            (255, 255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        x, y = corner_position(self._corner, context, self._width, self._height)
        return Layer(image, x, y)


class LineOverlay:
    """Stand in for the start line, which spans the frame and rarely changes."""

    def state(self, context: OverlayContext) -> Hashable:
        """Never change."""
        return 0

    def draw(self, context: OverlayContext) -> Layer:
        """Draw a line across the frame."""
        image = np.zeros((40, context.width, 4), dtype=np.uint8)
        end = (context.width - 1, 35)
        cv2.line(image, (0, 5), end, (0, 0, 255, 200), 2, cv2.LINE_AA)
        return Layer(image, 0, context.height // 2)


def _benchmark_overlays() -> list[BusyOverlay | LineOverlay]:
    """Return overlays comparable to the real ones."""
    return [
        LineOverlay(),
        BusyOverlay(Corner.TOP_LEFT, 420, 48),
        BusyOverlay(Corner.TOP_RIGHT, 240, 72),
        BusyOverlay(Corner.BOTTOM_RIGHT, 240, 120),
    ]


def read_temperature(path: Path = THERMAL_ZONE) -> float | None:
    """Return the processor temperature in Celsius, if this computer reports it."""
    try:
        return int(path.read_text(encoding="ascii")) / 1000
    except (OSError, ValueError):
        return None


def _read_process(pid: int) -> tuple[int, float] | None:
    """Return a process's parent and processor seconds used, if it still exists."""
    try:
        # The process name can contain spaces, so count fields after its bracket.
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    after_name = fields.rsplit(")", 1)[1].split()
    ticks = int(after_name[11]) + int(after_name[12])
    return int(after_name[1]), ticks / os.sysconf("SC_CLK_TCK")


def _process_tree(root: int) -> dict[int, float]:
    """Return the processor seconds used by a process and its descendants."""
    processes = {}
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit() and (found := _read_process(int(entry.name))):
            processes[int(entry.name)] = found
    tree = {}
    pending = [root]
    while pending:
        pid = pending.pop()
        if pid in processes:
            tree[pid] = processes[pid][1]
            pending.extend(
                child for child, (parent, _) in processes.items() if parent == pid
            )
    return tree


def _role(pid: int) -> str:
    """Return what a process does in the pipeline."""
    try:
        command = Path(f"/proc/{pid}/cmdline").read_text(errors="replace")
    except OSError:
        return "other"
    for role, marker in PROCESS_ROLES:
        if marker in command:
            return role
    return "application"


def _memory_mb(pids: dict[int, float]) -> float:
    """Return the memory held by the processes, in megabytes."""
    pages = 0
    for pid in pids:
        try:
            pages += int(Path(f"/proc/{pid}/statm").read_text().split()[1])
        except (OSError, IndexError, ValueError):
            continue
    return pages * os.sysconf("SC_PAGE_SIZE") / 1_000_000


class _TimedRender:
    """Render frames, recording how long it takes."""

    def __init__(self, render: Callable[[Frame], NDArray[np.uint8]]) -> None:
        """Hold the function to time."""
        self._render = render
        self.seconds = 0.0
        self.count = 0

    def __call__(self, frame: Frame) -> NDArray[np.uint8]:
        """Render a frame."""
        started = time.perf_counter()
        image = self._render(frame)
        self.seconds += time.perf_counter() - started
        self.count += 1
        return image


def _scenario_config(config: Config, scenario: Scenario) -> Config:
    """Return the configuration with a scenario's settings."""
    return config.model_copy(
        update={
            "camera": config.camera.model_copy(update={"rtsp_path": scenario.channel}),
            "stream": config.stream.model_copy(
                update={
                    "width": scenario.width,
                    "height": scenario.height,
                    "fps": scenario.fps,
                    "youtube_enabled": False,
                }
            ),
        }
    )


def run_scenario(
    config: Config,
    secrets: Secrets,
    scenario: Scenario,
    duration_s: float,
    *,
    settle_s: float = SETTLE_S,
    make_publisher: Callable[..., Publisher] = Publisher,
    make_source: Callable[[Config, Secrets], FrameSource] = FrameSource,
    make_server: Callable[[StreamConfig], MediaServer] | None = None,
) -> Result:
    """Run the pipeline with a scenario's settings, and measure it after it settles."""
    settings = _scenario_config(config, scenario)
    make_server = make_server or (lambda s: MediaServer(s, ensure_binary()))
    server = make_server(settings.stream)
    render = _TimedRender(Compositor(_benchmark_overlays()).render)
    root = os.getpid()
    live = total = 0
    try:
        with make_source(settings, secrets) as source:
            publisher = make_publisher(source, server, settings.stream, render=render)
            with publisher:
                time.sleep(settle_s)
                started = time.monotonic()
                before = _process_tree(root)
                sent, late = publisher.frames_sent, publisher.late_ticks
                renders, render_seconds = render.count, render.seconds
                first_sequence = source.latest().sequence
                while (elapsed := time.monotonic() - started) < duration_s:
                    time.sleep(0.05)
                    total += 1
                    live += source.latest().live
                after = _process_tree(root)
                roles = {pid: _role(pid) for pid in after}
                memory_mb = _memory_mb(after)
                elapsed = time.monotonic() - started
                frames = publisher.frames_sent - sent
                late_ticks = publisher.late_ticks - late
                rendered = render.count - renders
                render_time = render.seconds - render_seconds
                last_sequence = source.latest().sequence
    finally:
        server.stop()
    by_role: dict[str, float] = {}
    for pid, seconds in after.items():
        used = (seconds - before.get(pid, 0.0)) / elapsed
        by_role[roles[pid]] = by_role.get(roles[pid], 0.0) + used
    return Result(
        scenario=scenario,
        output_fps=frames / elapsed,
        camera_fps=(last_sequence - first_sequence) / elapsed,
        late_ticks=late_ticks,
        live_fraction=live / total if total else 0.0,
        cpu_cores=sum(by_role.values()),
        cpu_by_role=by_role,
        memory_mb=memory_mb,
        render_ms=render_time / rendered * 1000 if rendered else 0.0,
        temperature_c=read_temperature(),
        processor_count=os.cpu_count() or 1,
    )


def usable_encoders(candidates: tuple[str, ...] = ENCODER_CANDIDATES) -> list[str]:
    """Return the H.264 encoders that actually work on this computer."""
    usable = []
    for encoder in candidates:
        command = [
            "ffmpeg", "-nostdin", "-v", "quiet", "-f", "lavfi",
            "-i", "testsrc2=size=1280x720:rate=20", "-t", "1", "-pix_fmt", "yuv420p",
            "-c:v", encoder, "-f", "null", "-",
        ]  # fmt: skip
        try:
            reply = subprocess.run(command, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if reply.returncode == 0:
            usable.append(encoder)
    return usable


def format_report(results: list[Result], encoders: list[str]) -> str:
    """Return the results as Markdown."""
    machine = f"{platform.machine()}, {os.cpu_count()} processors"
    lines = [
        f"Computer: {platform.node()} ({machine})",
        f"H.264 encoders that work: {', '.join(encoders) or 'none'}",
        "",
        "| Settings | Output fps | Camera fps | Late | Processor (cores) | Encoder "
        "| Decoder | Overlays (ms) | Memory (MB) | Temperature | Sustained |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for result in results:
        temperature = result.temperature_c
        lines.append(
            f"| {result.scenario.name} | {result.output_fps:.1f} "
            f"| {result.camera_fps:.1f} | {result.late_ticks} "
            f"| {result.cpu_cores:.2f} "
            f"| {result.cpu_by_role.get('encoder', 0.0):.2f} "
            f"| {result.cpu_by_role.get('decoder', 0.0):.2f} "
            f"| {result.render_ms:.2f} | {result.memory_mb:.0f} "
            f"| {f'{temperature:.0f} C' if temperature is not None else 'unknown'} "
            f"| {'yes' if result.sustained else 'no'} |"
        )
    return "\n".join(lines) + "\n"


def _check_ports_free(stream: StreamConfig) -> None:
    """Stop early if the application is already using the stream's ports."""
    with socket.socket() as sock:
        sock.settimeout(0.2)
        if sock.connect_ex(("127.0.0.1", stream.rtsp_port)) == 0:
            raise MediaServerError(
                f"Port {stream.rtsp_port} is in use. Stop startcam before benchmarking."
            )


def _parse_arguments() -> argparse.Namespace:
    """Return the command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Measure which video settings this computer can sustain."
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--secrets", type=Path)
    parser.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_DURATION_S,
        help="seconds to measure each scenario",
    )
    parser.add_argument("--output", type=Path, help="also write the report here")
    return parser.parse_args()


def _benchmark(arguments: argparse.Namespace) -> None:
    """Run all the scenarios and report the results."""
    config = load_config(arguments.config)
    secrets = load_secrets(arguments.secrets)
    _check_ports_free(config.stream)
    results = []
    for scenario in DEFAULT_SCENARIOS:
        logger.info("Measuring %s for %.0f seconds", scenario.name, arguments.duration)
        results.append(run_scenario(config, secrets, scenario, arguments.duration))
    report = format_report(results, usable_encoders())
    for line in report.splitlines():
        logger.info("%s", line)
    if arguments.output:
        arguments.output.write_text(report, encoding="utf-8")


def main() -> int:
    """Run the startcam-benchmark command and return its exit code."""
    configure_logging()
    try:
        _benchmark(_parse_arguments())
    except (ConfigError, MediaServerError, OSError) as error:
        logger.error("%s", error)
        return 1
    return 0


#########################################################
# MAIN
#########################################################

if __name__ == "__main__":
    raise SystemExit(main())
