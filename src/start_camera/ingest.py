#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: ingest.py
#  Description: Read camera frames, surviving outages.
#  Date: 2026-10-31
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, opencv, ffmpeg
#
#########################################################

"""Read camera frames, surviving outages.

A background thread keeps only the newest frame, so delay can't build up however slowly
the consumer runs. During an outage, consumers get a message frame instead, so the
output stream stays alive, and the thread keeps reconnecting.
"""

#
# SYSTEM IMPORTS
#

import logging
import os
import select
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Self

import cv2
import numpy as np
from numpy.typing import NDArray

#
# PROJECT IMPORTS
#
from start_camera.config import Config, Secrets
from start_camera.discovery import (
    DiscoveryError,
    build_rtsp_url,
    discover,
    select_camera,
)

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

SIGNAL_LOST_MESSAGE = "CAMERA SIGNAL LOST"
CHANNELS = 3
REDISCOVER_AFTER_FAILURES = 3
STOP_CHECK_S = 0.1

#
# FUNCTIONS
#


class StreamEnded(Exception):
    """The camera stream stopped or went quiet."""


@dataclass(frozen=True)
class IngestTiming:
    """How long to wait at each stage, in seconds."""

    first_frame_timeout: float = 15.0
    stall_timeout: float = 5.0
    signal_lost_after: float = 2.0
    backoff_initial: float = 1.0
    backoff_max: float = 10.0


@dataclass(frozen=True)
class Frame:
    """A read-only video frame in BGR format."""

    image: NDArray[np.uint8]
    arrival_time: float
    sequence: int
    live: bool


def ffmpeg_command(url: str, width: int, height: int) -> list[str]:
    """Return the command that decodes a stream to raw frames on stdout."""
    return [
        "ffmpeg", "-nostdin", "-v", "quiet", "-fflags", "nobuffer", "-flags",
        "low_delay", "-rtsp_transport", "tcp", "-timeout", "10000000", "-i", url,
        "-an", "-vf", f"scale={width}:{height}", "-pix_fmt", "bgr24", "-f", "rawvideo",
        "pipe:1",
    ]  # fmt: skip


def signal_lost_image(width: int, height: int) -> NDArray[np.uint8]:
    """Return a black image with the outage message across its centre."""
    image = np.zeros((height, width, CHANNELS), dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX
    (text_width, _), _ = cv2.getTextSize(SIGNAL_LOST_MESSAGE, font, 1.0, 2)
    scale = 0.7 * width / text_width
    thickness = max(1, round(2 * scale))
    (text_width, text_height), _ = cv2.getTextSize(
        SIGNAL_LOST_MESSAGE, font, scale, thickness
    )
    origin = ((width - text_width) // 2, (height + text_height) // 2)
    cv2.putText(
        image,
        SIGNAL_LOST_MESSAGE,
        origin,
        font,
        scale,
        (255, 255, 255),
        thickness,
        cv2.LINE_AA,
    )
    image.flags.writeable = False
    return image


def _read_frame(
    file_descriptor: int, size: int, timeout: float, stop: threading.Event
) -> bytearray:
    """Read one whole frame, or raise StreamEnded if the stream stops or stalls."""
    buffer = bytearray(size)
    view = memoryview(buffer)
    received = 0
    deadline = time.monotonic() + timeout
    while received < size:
        if stop.is_set():
            raise StreamEnded("stopped")
        ready, _, _ = select.select([file_descriptor], [], [], STOP_CHECK_S)
        if not ready:
            if time.monotonic() >= deadline:
                raise StreamEnded(f"no video for {timeout:.0f} seconds")
            continue
        count = os.readv(file_descriptor, [view[received:]])
        if count == 0:
            raise StreamEnded("the stream ended")
        received += count
    return buffer


class FrameSource:
    """Read the camera in a background thread and provide the newest frame."""

    def __init__(
        self,
        config: Config,
        secrets: Secrets,
        *,
        timing: IngestTiming | None = None,
        command: Callable[[str, int, int], list[str]] = ffmpeg_command,
        resolve_host: Callable[[], str] | None = None,
    ) -> None:
        """Prepare to read the camera.

        Args:
            config: The configuration.
            secrets: The camera's credentials.
            timing: Timeouts and backoff. Defaults suit a WiFi camera.
            command: Builds the decoding command.
            resolve_host: Finds the camera on the network. Defaults to a network scan.
        """
        self._config = config
        self._secrets = secrets
        self._timing = timing or IngestTiming()
        self._command = command
        self._resolve_host = resolve_host or self._discover_host
        self._width = config.stream.width
        self._height = config.stream.height
        self._host = config.camera.host
        self._lost_image = signal_lost_image(self._width, self._height)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._frame: Frame | None = None
        self._frame_clock = 0.0
        self._sequence = 0
        self._thread: threading.Thread | None = None

    def __enter__(self) -> Self:
        """Start reading the camera."""
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop reading the camera."""
        self.stop()

    def start(self) -> None:
        """Start the background thread."""
        self._thread = threading.Thread(target=self._run, daemon=True, name="ingest")
        self._thread.start()

    def stop(self) -> None:
        """Stop the background thread and its decoder."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def latest(self) -> Frame:
        """Return the newest frame, or the outage message if the video has stopped."""
        now = time.monotonic()
        with self._lock:
            frame = self._frame
            age = now - self._frame_clock
            sequence = self._sequence
        if frame is not None and age < self._timing.signal_lost_after:
            return frame
        return Frame(self._lost_image, time.time(), sequence, live=False)

    def _discover_host(self) -> str:
        """Find the camera with a network scan."""
        candidates = discover(self._config, self._secrets)
        return select_camera(candidates, self._config.camera.mac).host

    def _run(self) -> None:
        """Connect and read until stopped, backing off after failures."""
        failures = 0
        while not self._stop.is_set():
            received = False
            try:
                if self._host is None or (
                    failures and failures % REDISCOVER_AFTER_FAILURES == 0
                ):
                    self._update_host()
                received, reason = self._read_stream()
            except (DiscoveryError, OSError) as error:
                reason = str(error)
            if self._stop.is_set():
                break
            failures = 0 if received else failures + 1
            logger.warning("Camera signal lost: %s", reason)
            delay = self._timing.backoff_initial * 2 ** max(0, failures - 1)
            self._stop.wait(min(delay, self._timing.backoff_max))

    def _update_host(self) -> None:
        """Look for the camera, in case its address has changed."""
        host = self._resolve_host()
        if host != self._host:
            logger.info("Camera found at %s", host)
        self._host = host

    def _read_stream(self) -> tuple[bool, str]:
        """Read frames until the stream ends.

        Returns:
            Whether any frames arrived, and why the stream ended.
        """
        url = build_rtsp_url(self._config, self._secrets, self._host)
        process = subprocess.Popen(
            self._command(url, self._width, self._height),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        received = False
        reason = "stopped"
        try:
            file_descriptor = process.stdout.fileno()
            size = self._width * self._height * CHANNELS
            timeout = self._timing.first_frame_timeout
            while not self._stop.is_set():
                self._publish(_read_frame(file_descriptor, size, timeout, self._stop))
                if not received:
                    logger.info("Camera signal received")
                received = True
                timeout = self._timing.stall_timeout
        except StreamEnded as error:
            reason = str(error)
        finally:
            process.kill()
            process.wait()
            process.stdout.close()
        return received, reason

    def _publish(self, buffer: bytearray) -> None:
        """Make a new frame the newest."""
        image = np.frombuffer(buffer, dtype=np.uint8).reshape(
            self._height, self._width, CHANNELS
        )
        image.flags.writeable = False
        now = time.monotonic()
        with self._lock:
            self._sequence += 1
            self._frame = Frame(image, time.time(), self._sequence, live=True)
            self._frame_clock = now
