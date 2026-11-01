#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: youtube.py
#  Description: Push the stream to YouTube Live.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: ffmpeg
#
#########################################################

"""Push the stream to YouTube Live.

A separate process copies the published stream from the local server, so a problem with
YouTube or the internet can't affect viewers on the local network. The command line
holds the stream key, so nothing from it is logged without the key being removed.
"""

#
# SYSTEM IMPORTS
#

import logging
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from urllib.parse import quote

#
# PROJECT IMPORTS
#
from start_camera.config import StreamConfig
from start_camera.media_server import Backoff, log_output, publish_url

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

STALL_TIMEOUT_S = 30.0
START_DELAY_S = 3.0
CHECK_INTERVAL_S = 0.5
MAX_RETRY_S = 30.0
PROGRESS_PREFIX = b"total_size="

#
# FUNCTIONS
#


def youtube_command(stream: StreamConfig, stream_key: str) -> list[str]:
    """Return the command that copies the stream to YouTube. It holds the key.

    The video is copied without encoding again. YouTube expects audio, so a silent track
    is added. Progress is reported on stdout, to show when the connection has stalled.
    """
    url = f"{stream.youtube_ingest_url.rstrip('/')}/{quote(stream_key, safe='')}"
    return [
        "ffmpeg", "-nostdin", "-v", "error", "-nostats",
        "-progress", "pipe:1", "-stats_period", "5",
        "-rtsp_transport", "tcp", "-i", publish_url(stream),
        "-re", "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k", "-f", "flv", url,
    ]  # fmt: skip


class _Progress:
    """Watch a process's progress reports for the amount sent to grow."""

    def __init__(self, lines: Iterable[bytes]) -> None:
        """Start reading the reports in the background."""
        self.started_at = time.monotonic()
        self.last_growth = self.started_at
        self.connected = False
        self._size = b""
        threading.Thread(
            target=self._read, args=(lines,), daemon=True, name="youtube-progress"
        ).start()

    def _read(self, lines: Iterable[bytes]) -> None:
        """Note when the amount sent changes."""
        for line in lines:
            if line.startswith(PROGRESS_PREFIX) and line != self._size:
                self._size = line
                self.last_growth = time.monotonic()
                self.connected = True


class YouTubePush:
    """Keep the stream pushed to YouTube, retrying whenever it fails."""

    def __init__(
        self,
        stream: StreamConfig,
        stream_key: str,
        *,
        command: Callable[[StreamConfig, str], list[str]] = youtube_command,
        stall_timeout_s: float = STALL_TIMEOUT_S,
        start_delay_s: float = START_DELAY_S,
    ) -> None:
        """Prepare the push.

        Args:
            stream: The output settings.
            stream_key: The YouTube stream key.
            command: Builds the command.
            stall_timeout_s: How long with no data sent before a restart.
            start_delay_s: How long to wait for the local stream to start.
        """
        self._stream = stream
        self._key = stream_key
        self._command = command
        self._stall_timeout_s = stall_timeout_s
        self._start_delay_s = start_delay_s
        self._backoff = Backoff(maximum_s=MAX_RETRY_S)
        self._stop = threading.Event()
        self._process: subprocess.Popen[bytes] | None = None
        self._progress: _Progress | None = None
        self._reported_connected = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start pushing in the background."""
        self._thread = threading.Thread(target=self._run, daemon=True, name="youtube")
        self._thread.start()

    def stop(self) -> None:
        """Stop pushing."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _run(self) -> None:
        """Start and watch the process until stopped."""
        self._stop.wait(self._start_delay_s)
        try:
            while not self._stop.is_set():
                self._supervise()
                self._stop.wait(CHECK_INTERVAL_S)
        finally:
            self._end_process()

    def _supervise(self) -> None:
        """Start the process when it's due, or restart it if it has failed."""
        now = time.monotonic()
        if self._process is None:
            if self._backoff.ready(now):
                self._start_process(now)
            return
        self._report_connection()
        if self._process.poll() is not None:
            self._restart(now, "the connection ended")
        elif now - self._progress.last_growth >= self._stall_timeout_s:
            self._restart(now, "no data sent")

    def _start_process(self, now: float) -> None:
        """Start the process, or schedule another try if it can't start."""
        try:
            self._process = subprocess.Popen(
                self._command(self._stream, self._key),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as error:
            logger.warning("Can't start the YouTube push: %s", error.strerror)
            self._backoff.failed(now, 0.0)
            return
        self._reported_connected = False
        self._progress = _Progress(self._process.stdout)
        log_output(
            self._process.stderr,
            "YouTube push",
            redact=(self._key, quote(self._key, safe="")),
        )

    def _report_connection(self) -> None:
        """Log the first data sent by each connection."""
        if self._progress.connected and not self._reported_connected:
            self._reported_connected = True
            logger.info("Sending video to YouTube")

    def _restart(self, now: float, reason: str) -> None:
        """Clear away a failed process and schedule another try."""
        logger.warning("YouTube push stopped, will retry: %s", reason)
        self._backoff.failed(now, now - self._progress.started_at)
        self._end_process()

    def _end_process(self) -> None:
        """End the process if there is one."""
        if self._process is not None:
            self._process.kill()
            self._process.wait()
            self._process = None
