#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: output.py
#  Description: Encode frames at a fixed rate and publish them.
#  Date: 2026-10-31
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, ffmpeg
#
#########################################################

"""Encode frames at a fixed rate and publish them.

The output runs by the clock, not the camera. Each tick encodes whatever the newest
frame is, repeating frames when the camera is slower and sending the outage message when
it has stopped, so viewers always get a steady stream.
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
from collections import deque
from collections.abc import Callable
from types import TracebackType
from typing import Protocol, Self

import numpy as np
from numpy.typing import NDArray

#
# PROJECT IMPORTS
#
from start_camera.config import StreamConfig
from start_camera.ingest import Frame
from start_camera.media_server import Backoff, log_output, publish_url

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

WRITE_TIMEOUT_S = 5.0
BEHIND_WARNING_INTERVAL_S = 30.0
LATE_WINDOW_S = 10.0
LATE_TICKS_TO_WARN = 5

#
# FUNCTIONS
#


class FrameProvider(Protocol):
    """Anything that can provide the newest frame."""

    def latest(self) -> Frame:
        """Return the newest frame."""
        ...


class Server(Protocol):
    """The server that the encoder publishes to."""

    def ensure_running(self) -> None:
        """Start the server if it isn't running."""
        ...

    def is_ready(self) -> bool:
        """Return whether the server can accept the stream."""
        ...


class EncoderError(Exception):
    """The encoder stopped accepting frames."""


def encoder_command(stream: StreamConfig) -> list[str]:
    """Return the command that encodes raw frames from stdin and publishes them.

    Baseline H.264 without B-frames is what WebRTC browsers can reliably play. Frames
    are timed by their arrival, and the encoder evens them out to the output rate.
    """
    keyframe_interval = str(stream.fps * stream.keyframe_interval_s)
    bitrate = stream.bitrate_kbps
    return [
        "ffmpeg", "-nostdin", "-v", "error", "-use_wallclock_as_timestamps", "1",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-video_size", f"{stream.width}x{stream.height}",
        "-framerate", str(stream.fps), "-i", "pipe:0", "-an",
        "-fps_mode", "cfr", "-r", str(stream.fps),
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-profile:v", "baseline", "-pix_fmt", "yuv420p",
        "-b:v", f"{bitrate}k", "-maxrate", f"{bitrate}k",
        "-bufsize", f"{bitrate * 2}k",
        "-g", keyframe_interval, "-keyint_min", keyframe_interval,
        "-sc_threshold", "0",
        "-f", "rtsp", "-rtsp_transport", "tcp", publish_url(stream),
    ]  # fmt: skip


class Encoder:
    """A running encoder process that accepts frames."""

    def __init__(self, command: list[str], write_timeout_s: float) -> None:
        """Start the encoder."""
        self._write_timeout_s = write_timeout_s
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self._file_descriptor = self._process.stdin.fileno()
        os.set_blocking(self._file_descriptor, False)
        self.started_at = time.monotonic()
        log_output(self._process.stderr, "Encoder")

    def write(self, image: NDArray[np.uint8]) -> None:
        """Send a frame, or raise EncoderError if the encoder has stopped or hung."""
        view = memoryview(image).cast("B")
        deadline = time.monotonic() + self._write_timeout_s
        while view:
            remaining = deadline - time.monotonic()
            _, ready, _ = select.select([], [self._file_descriptor], [], remaining)
            if not ready:
                raise EncoderError("the encoder stopped accepting frames")
            try:
                view = view[os.write(self._file_descriptor, view) :]
            except BlockingIOError:
                continue
            except OSError as error:
                raise EncoderError("the encoder exited") from error

    def is_running(self) -> bool:
        """Return whether the process is still running."""
        return self._process.poll() is None

    def stop(self) -> None:
        """End the process."""
        self._process.kill()
        self._process.wait()
        self._process.stdin.close()


class Publisher:
    """Publish frames at the configured rate, restarting what fails."""

    def __init__(
        self,
        source: FrameProvider,
        server: Server,
        stream: StreamConfig,
        *,
        render: Callable[[Frame], NDArray[np.uint8]] = lambda frame: frame.image,
        command: Callable[[StreamConfig], list[str]] = encoder_command,
        write_timeout_s: float = WRITE_TIMEOUT_S,
    ) -> None:
        """Prepare to publish.

        Args:
            source: Provides the newest frame.
            server: The server to publish to.
            stream: The output settings.
            render: Turns a frame into the image to encode, such as by adding overlays.
            command: Builds the encoder command.
            write_timeout_s: How long the encoder may refuse a frame before a restart.
        """
        self._source = source
        self._server = server
        self._stream = stream
        self._render = render
        self._command = command
        self._write_timeout_s = write_timeout_s
        self._encoder: Encoder | None = None
        self._backoff = Backoff()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_behind_warning = 0.0
        self._recent_late_ticks: deque[float] = deque()
        self.frames_sent = 0
        self.late_ticks = 0

    def __enter__(self) -> Self:
        """Start publishing."""
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop publishing."""
        self.stop()

    def start(self) -> None:
        """Start the publishing thread."""
        self._thread = threading.Thread(target=self._run, daemon=True, name="publish")
        self._thread.start()

    def stop(self) -> None:
        """Stop publishing and end the encoder."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _run(self) -> None:
        """Encode the newest frame on each tick of the clock."""
        period = 1 / self._stream.fps
        next_tick = time.monotonic()
        try:
            while not self._stop.is_set():
                self._supervise()
                self._publish_frame()
                next_tick += period
                now = time.monotonic()
                if next_tick < now - period:
                    self.late_ticks += 1
                    self._warn_behind(now)
                    next_tick = now
                self._stop.wait(max(0.0, next_tick - now))
        finally:
            if self._encoder is not None:
                self._encoder.stop()

    def _supervise(self) -> None:
        """Keep the server and the encoder running."""
        self._server.ensure_running()
        now = time.monotonic()
        if self._encoder is not None and not self._encoder.is_running():
            self._encoder_failed("the encoder exited")
        if (
            self._encoder is None
            and self._backoff.ready(now)
            and self._server.is_ready()
        ):
            self._encoder = Encoder(self._command(self._stream), self._write_timeout_s)
            logger.info("Encoder started")

    def _publish_frame(self) -> None:
        """Send the newest frame to the encoder."""
        if self._encoder is None:
            return
        try:
            self._encoder.write(self._render(self._source.latest()))
            self.frames_sent += 1
        except EncoderError as error:
            self._encoder_failed(str(error))

    def _encoder_failed(self, reason: str) -> None:
        """Clear away a failed encoder and schedule a restart."""
        logger.warning("Restarting encoder: %s", reason)
        now = time.monotonic()
        self._backoff.failed(now, now - self._encoder.started_at)
        self._encoder.stop()
        self._encoder = None

    def _warn_behind(self, now: float) -> None:
        """Warn, occasionally, if this computer keeps failing to reach the output rate.

        An occasional late frame is normal, such as while the encoder loads, and warning
        about those would teach people to ignore a real problem.
        """
        self._recent_late_ticks.append(now)
        while now - self._recent_late_ticks[0] > LATE_WINDOW_S:
            self._recent_late_ticks.popleft()
        if len(self._recent_late_ticks) < LATE_TICKS_TO_WARN:
            return
        if now - self._last_behind_warning >= BEHIND_WARNING_INTERVAL_S:
            self._last_behind_warning = now
            logger.warning(
                "Can't keep up with %d fps. Try a lower frame rate or resolution",
                self._stream.fps,
            )
