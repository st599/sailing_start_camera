#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: delay.py
#  Description: Measure the delay from camera to decoded frame.
#  Date: 2026-10-31
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, ffmpeg
#
#########################################################

"""Measure the delay from camera to decoded frame, for capture_latency_ms.

A web page, opened on a device in front of the camera, flashes at times set by this
computer's clock. The delay is how long after each flash the video shows it. Both ends
use the same clock, so it doesn't need to be synchronised with anything else.
"""

#
# SYSTEM IMPORTS
#

import argparse
import json
import logging
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

#
# PROJECT IMPORTS
#
from start_camera.config import (
    ConfigError,
    load_config,
    load_secrets,
    save_config,
)
from start_camera.discovery import build_rtsp_url
from start_camera.logging_setup import configure_logging

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

FLASH_PERIOD_S = 5.0
FLASH_LENGTH_S = 1.0
SETTLE_S = 5.0
MIN_FLASHES = 4
MIN_CONTRAST = 10.0
MAX_SPREAD_S = 0.2
BRIGHTEST_PIXELS = 0.01
FRAME_WIDTH = 160
FRAME_HEIGHT = 90
PAGE = f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Camera delay</title>
<style>html,body{{margin:0;height:100%;background:#000;color:#888;font:16px sans-serif}}
p{{padding:1em;margin:0}}</style></head><body><p id="note">Syncing...</p><script>
let offset = 0, best = Infinity;
async function sync() {{
  const start = performance.now();
  const reply = await fetch("/time", {{cache: "no-store"}});
  const server = (await reply.json()).time * 1000;
  const end = performance.now();
  if (end - start < best) {{
    best = end - start;
    offset = server + best / 2 - (performance.timeOrigin + end);
    document.getElementById("note").textContent =
      "Hold this in front of the camera. Sync within " + Math.ceil(best / 2) + " ms.";
  }}
}}
function draw() {{
  const now = performance.timeOrigin + performance.now() + offset;
  const lit = now % {FLASH_PERIOD_S * 1000:.0f} < {FLASH_LENGTH_S * 1000:.0f};
  document.body.style.background = lit ? "#fff" : "#000";
  requestAnimationFrame(draw);
}}
sync(); setInterval(sync, 1000);
navigator.wakeLock?.request("screen").catch(() => {{}});
requestAnimationFrame(draw);
</script></body></html>
"""

#
# FUNCTIONS
#


class DelayError(Exception):
    """The delay could not be measured reliably."""


@dataclass(frozen=True)
class DelayResult:
    """A measured delay."""

    delay_ms: int
    spread_ms: int
    flashes: int


class _FlashPageHandler(BaseHTTPRequestHandler):
    """Serve the flashing page and this computer's time."""

    def do_GET(self) -> None:  # noqa: N802 - the name http.server requires
        """Reply with the page or the current time."""
        if self.path == "/":
            body, content_type = PAGE.encode(), "text/html; charset=utf-8"
        elif self.path == "/time":
            body = json.dumps({"time": time.time()}).encode()
            content_type = "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Send request logs to the shared logger."""
        logger.debug(format, *args)


def start_page_server(port: int) -> ThreadingHTTPServer:
    """Serve the flashing page in a background thread."""
    server = ThreadingHTTPServer(("", port), _FlashPageHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def local_address(remote_host: str) -> str:
    """Return this computer's address on the network used to reach remote_host."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((remote_host, 9))
        return sock.getsockname()[0]


def capture_frames(
    url: str, duration_s: float
) -> tuple[NDArray[np.float64], NDArray[np.uint8]]:
    """Read small greyscale frames, noting when each arrives on this computer.

    Args:
        url: The camera's RTSP URL.
        duration_s: How long to capture.

    Returns:
        The arrival times in seconds, and the frames.

    Raises:
        DelayError: If no frames arrive.
    """
    size = FRAME_WIDTH * FRAME_HEIGHT
    command = [
        "ffmpeg", "-v", "quiet", "-fflags", "nobuffer", "-flags", "low_delay",
        "-rtsp_transport", "tcp", "-timeout", "10000000", "-i", url, "-an",
        "-vf", f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}", "-pix_fmt", "gray",
        "-f", "rawvideo", "pipe:1",
    ]  # fmt: skip
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    times: list[float] = []
    frames: list[bytes] = []
    try:
        end = time.monotonic() + duration_s
        while time.monotonic() < end:
            frame = process.stdout.read(size)
            if len(frame) < size:
                break
            times.append(time.time())
            frames.append(frame)
    finally:
        process.kill()
        process.wait()
    if not frames:
        raise DelayError("No video received. Check the camera address and password.")
    pixels = np.frombuffer(b"".join(frames), dtype=np.uint8)
    return np.array(times), pixels.reshape(-1, FRAME_HEIGHT, FRAME_WIDTH)


def _flash_signal(frames: NDArray[np.uint8]) -> NDArray[np.float64]:
    """Return the brightness over time of the pixels that vary the most."""
    variation = frames.std(axis=0).ravel()
    count = max(1, int(variation.size * BRIGHTEST_PIXELS))
    chosen = np.argpartition(variation, -count)[-count:]
    return frames.reshape(len(frames), -1)[:, chosen].mean(axis=1)


def _rising_edges(
    times: NDArray[np.float64], signal: NDArray[np.float64], threshold: float
) -> NDArray[np.float64]:
    """Return the arrival time of the first bright frame of each flash."""
    bright = signal >= threshold
    starts = np.flatnonzero(bright[1:] & ~bright[:-1]) + 1
    edges: list[float] = []
    for start in starts:
        if not edges or times[start] - edges[-1] >= FLASH_PERIOD_S / 2:
            edges.append(float(times[start]))
    return np.array(edges)


def estimate_delay(
    times: NDArray[np.float64], frames: NDArray[np.uint8]
) -> DelayResult:
    """Work out the delay from captured frames of the flashing page.

    Args:
        times: Arrival time of each frame, in seconds.
        frames: Greyscale frames.

    Returns:
        The median delay, and how far the individual flashes spread around it.

    Raises:
        DelayError: If the flash can't be seen, or the timings disagree, which
            usually means the camera or the page moved.
    """
    settled = times >= times[0] + SETTLE_S
    times, frames = times[settled], frames[settled]
    if len(times) < 2:
        raise DelayError("The capture was too short.")
    signal = _flash_signal(frames)
    low, high = np.percentile(signal, [5, 95])
    if high - low < MIN_CONTRAST:
        raise DelayError("No flash seen. Fill more of the camera's view with the page.")
    edges = _rising_edges(times, signal, float((low + high) / 2))
    if len(edges) < MIN_FLASHES:
        raise DelayError(
            f"Saw {len(edges)} flashes, need {MIN_FLASHES}. Capture for longer."
        )
    delays = edges % FLASH_PERIOD_S
    delays[delays > FLASH_PERIOD_S - 0.5] -= FLASH_PERIOD_S
    spread = float(delays.max() - delays.min())
    if spread > MAX_SPREAD_S:
        raise DelayError(
            f"Timings disagree by {spread * 1000:.0f} ms. Keep the camera and the "
            "page still, and try again."
        )
    delay = float(np.median(delays))
    if delay < 0:
        raise DelayError("The flash appeared early. Reload the page and try again.")
    return DelayResult(round(delay * 1000), round(spread * 1000), len(edges))


def _parse_arguments() -> argparse.Namespace:
    """Return the command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Measure the camera's delay, for capture_latency_ms."
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--secrets", type=Path, default=None)
    parser.add_argument("--host", help="camera address; overrides the configuration")
    parser.add_argument("--duration", type=float, default=45.0, help="seconds")
    parser.add_argument("--port", type=int, default=8090, help="flashing page port")
    parser.add_argument(
        "--write", action="store_true", help="save the result to the configuration"
    )
    parser.add_argument(
        "--no-wait", action="store_true", help="start without waiting for Enter"
    )
    return parser.parse_args()


def _measure(arguments: argparse.Namespace) -> None:
    """Run a measurement and optionally save the result."""
    config = load_config(arguments.config)
    secrets = load_secrets(arguments.secrets)
    host = arguments.host or config.camera.host
    if host is None:
        raise DelayError("No camera address. Use --host or set camera.host.")
    server = start_page_server(arguments.port)
    try:
        address = local_address(host)
        logger.info("Open http://%s:%d/ on a phone or laptop", address, arguments.port)
        logger.info("Hold it still, filling as much of the camera's view as possible")
        if not arguments.no_wait:
            logger.info("Press Enter when it's in place")
            input()
        logger.info("Measuring for %.0f seconds", arguments.duration)
        url = build_rtsp_url(config, secrets, host)
        times, frames = capture_frames(url, arguments.duration)
    finally:
        server.shutdown()
        server.server_close()
    result = estimate_delay(times, frames)
    logger.info(
        "Delay %d ms, spread %d ms, from %d flashes",
        result.delay_ms, result.spread_ms, result.flashes,
    )  # fmt: skip
    if arguments.write:
        config.camera.capture_latency_ms = result.delay_ms
        save_config(config, arguments.config)
    else:
        logger.info("Use --write to save this as capture_latency_ms")


def main() -> int:
    """Run the startcam-delay command and return its exit code."""
    configure_logging()
    try:
        _measure(_parse_arguments())
    except (ConfigError, DelayError, OSError) as error:
        logger.error("%s", error)
        return 1
    return 0


#########################################################
# MAIN
#########################################################

if __name__ == "__main__":
    raise SystemExit(main())
