#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: recording.py
#  Description: Record the stream to files, watching the disk space.
#  Date: 2026-11-02
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: None
#
#########################################################

"""Record the stream to files, watching the disk space.

MediaMTX does the recording by copying the published stream, so it costs almost no
processor time. Recording stops before the disk fills, so the stream carries on.
"""

#
# SYSTEM IMPORTS
#

import logging
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

#
# PROJECT IMPORTS
#
from start_camera.config import RecordingConfig
from start_camera.media_server import MediaServerError

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

CHECK_INTERVAL_S = 30.0
LOW_SPACE_FACTOR = 3
LOW_SPACE_WARNING_INTERVAL_S = 600.0
GIGABYTE = 1_000_000_000

#
# FUNCTIONS
#


class RecordingError(Exception):
    """Recording can't start."""


class RecordingServer(Protocol):
    """The server that records."""

    def set_recording(self, record: bool) -> None:
        """Start or stop recording."""
        ...


class Recorder:
    """Start and stop recording, and stop it before the disk fills."""

    def __init__(
        self,
        config: RecordingConfig,
        server: RecordingServer,
        bitrate_kbps: int,
        *,
        free_space: Callable[[Path], int] = lambda path: shutil.disk_usage(path).free,
        check_interval_s: float = CHECK_INTERVAL_S,
    ) -> None:
        """Prepare to record.

        Args:
            config: The recording settings.
            server: The server that records.
            bitrate_kbps: The stream's bitrate, to estimate how long space will last.
            free_space: Returns the free bytes on the disk holding a folder.
            check_interval_s: How often to check the disk space while recording.
        """
        self.directory = Path(config.directory).expanduser().resolve()
        self._config = config
        self._server = server
        self._bytes_per_hour = bitrate_kbps * 1000 / 8 * 3600
        self._free_space = free_space
        self._check_interval_s = check_interval_s
        self._minimum = config.min_free_gb * GIGABYTE
        self._lock = threading.Lock()
        self._recording = False
        self._last_warning: float | None = None
        self._stop_watching = threading.Event()
        self._watcher: threading.Thread | None = None

    @property
    def is_recording(self) -> bool:
        """Return whether recording is on."""
        return self._recording

    def start(self) -> None:
        """Start recording.

        Raises:
            RecordingError: If the folder can't be used, there isn't enough space, or
                the server refused.
        """
        with self._lock:
            if self._recording:
                return
            free = self._check_space_to_start()
            try:
                self._server.set_recording(True)
            except MediaServerError as error:
                raise RecordingError(str(error)) from error
            self._recording = True
        hours = free / self._bytes_per_hour
        logger.info(
            "Recording to %s, with space for about %.0f hours", self.directory, hours
        )
        self._start_watching()

    def stop(self) -> None:
        """Stop recording. The current file is finished properly."""
        with self._lock:
            if not self._recording:
                return
            self._recording = False
            try:
                self._server.set_recording(False)
            except MediaServerError as error:
                logger.error("Couldn't stop recording: %s", error)
        self._stop_watching.set()
        logger.info("Recording stopped")

    def close(self) -> None:
        """Stop recording and watching the disk."""
        self.stop()
        self._stop_watching.set()
        if self._watcher is not None:
            self._watcher.join()

    def _check_space_to_start(self) -> int:
        """Return the free space, or refuse if the folder can't be used or is full."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            free = self._free_space(self.directory)
        except OSError as error:
            message = f"Can't record to {self.directory}: {error}"
            raise RecordingError(message) from error
        if free < self._minimum:
            raise RecordingError(
                f"Only {free / GIGABYTE:.1f} GB free in {self.directory}, so not "
                "recording"
            )
        return free

    def _start_watching(self) -> None:
        """Watch the disk space in the background."""
        if self._watcher is not None and self._watcher.is_alive():
            return
        self._stop_watching = threading.Event()
        self._watcher = threading.Thread(
            target=self._watch, daemon=True, name="recording-space"
        )
        self._watcher.start()

    def _watch(self) -> None:
        """Stop recording before the disk fills, warning when it's getting low."""
        while not self._stop_watching.wait(self._check_interval_s):
            try:
                free = self._free_space(self.directory)
            except OSError as error:
                logger.warning("Can't check the space for recording: %s", error)
                continue
            if free < self._minimum:
                logger.error(
                    "Recording stopped, as only %.1f GB is left. The stream carries on",
                    free / GIGABYTE,
                )
                self.stop()
                return
            self._warn_if_low(free)

    def _warn_if_low(self, free: int) -> None:
        """Warn, occasionally, that the space for recording is running low."""
        now = time.monotonic()
        # None until the first warning: the clock starts near zero when the computer
        # does, which would otherwise hide warnings soon after starting up.
        recently = (
            self._last_warning is not None
            and now - self._last_warning < LOW_SPACE_WARNING_INTERVAL_S
        )
        if free < self._minimum * LOW_SPACE_FACTOR and not recently:
            self._last_warning = now
            logger.warning(
                "Space for recording is low: about %.0f minutes left",
                (free - self._minimum) / self._bytes_per_hour * 60,
            )
