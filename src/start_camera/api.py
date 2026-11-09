#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: api.py
#  Description: The stable Python interface to the camera system.
#  Date: 2026-11-09
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy
#
#########################################################

"""The stable Python interface to the camera system.

A race control suite can run the whole system inside its own program with
CameraSystem. Only what's listed in __all__ is promised to stay stable; the rest of
the package may change.

The preview and the stream draw with separate compositors, as each runs on its own
thread, but they use the same camera frames, overlays and race controls, so they match.
"""

#
# SYSTEM IMPORTS
#

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self

import numpy as np
from numpy.typing import NDArray

#
# PROJECT IMPORTS
#
from start_camera.config import (
    Config,
    ConfigError,
    Secrets,
    load_config,
    load_secrets,
)
from start_camera.control import RaceControl
from start_camera.ingest import Frame, FrameSource
from start_camera.media_server import MediaServerError
from start_camera.overlays.countdown import RaceDisplay, RaceSummary
from start_camera.overlays.factory import build_compositor
from start_camera.pipeline import Streaming, addresses_for_viewers
from start_camera.race_clock import Phase, RaceClockError, ScheduledStart
from start_camera.recording import Recorder, RecordingError

#
#  GLOBAL VARIABLES
#

__all__ = [
    "CameraSystem",
    "Config",
    "ConfigError",
    "MediaServerError",
    "Phase",
    "RaceClockError",
    "RecordingError",
    "ScheduledStart",
    "Secrets",
    "SystemStatus",
]

#
# FUNCTIONS
#


@dataclass(frozen=True)
class SystemStatus:
    """A snapshot of the whole system.

    Times are seconds since the epoch. display is what the video's countdown shows,
    which allows for the camera's delay.
    """

    time: float
    phase: Phase
    display: str
    flags: tuple[str, ...]
    upcoming: ScheduledStart | None
    latest: ScheduledStart | None
    camera_live: bool
    streaming: bool
    recording: bool


class Source(Protocol):
    """Provides the camera's frames."""

    def start(self) -> None:
        """Start reading the camera."""
        ...

    def stop(self) -> None:
        """Stop reading the camera."""
        ...

    def latest(self) -> Frame:
        """Return the newest frame."""
        ...


class CameraSystem:
    """The camera, its overlays, the race controls, the stream and recording.

    Prepare it with the configuration, then start it, or use it in a with block.
    Starting reads the camera; the stream is started separately.
    """

    def __init__(
        self,
        config: Config,
        secrets: Secrets,
        *,
        make_source: Callable[[Config, Secrets], Source] | None = None,
        make_streaming: Callable[..., Streaming] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Prepare everything, without starting it.

        Args:
            config: The configuration.
            secrets: The credentials.
            make_source: Makes what reads the camera. For testing.
            make_streaming: Makes what publishes the stream. For testing.
            clock: Provides the current time. For testing.
        """
        self.config = config
        self.control = RaceControl(
            config.sequences, Path(config.control.journal_path), clock
        )
        self.secrets = secrets
        self._source = (make_source or FrameSource)(config, secrets)
        self._make_streaming = make_streaming or Streaming
        self._clock = clock
        self._compositor = build_compositor(config, clock, self.control)
        self._display = RaceDisplay(config, self.control)
        self._streaming: Streaming | None = None
        self._stream_lock = threading.Lock()

    @classmethod
    def from_files(
        cls, config_path: Path | str, secrets_path: Path | str | None = None
    ) -> "CameraSystem":
        """Return a system for a configuration file and an optional secrets file.

        Raises:
            ConfigError: If a file can't be read or isn't valid.
        """
        secrets = load_secrets(Path(secrets_path) if secrets_path else None)
        return cls(load_config(Path(config_path)), secrets)

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
        """Stop everything."""
        self.close()

    def status(self) -> SystemStatus:
        """Return a snapshot of the racing, the camera, the stream and recording."""
        now = self._clock()
        race = self.control.state(now)
        return SystemStatus(
            time=now,
            phase=race.phase,
            display=self.summary().display,
            flags=race.flags,
            upcoming=race.upcoming,
            latest=race.latest,
            camera_live=self.camera_is_live(),
            streaming=self.is_streaming,
            recording=self.is_recording,
        )

    def start_now(self) -> None:
        """Bring the next start forward so its warning signal is now.

        Raises:
            RaceClockError: If this can't be done now. The same goes for the other
                race commands.
        """
        self.control.start_now()

    def shift(self, minutes: float) -> None:
        """Move every start that hasn't happened by a number of minutes."""
        self.control.shift(minutes)

    def postpone(self) -> None:
        """Hold the starts that haven't happened, and fly the postponement flag."""
        self.control.postpone()

    def resume(self) -> None:
        """End a postponement. The next warning signal is one minute later."""
        self.control.resume()

    def general_recall(self) -> None:
        """Recall the most recent class, which then starts again."""
        self.control.general_recall()

    def individual_recall(self) -> None:
        """Fly the individual recall flag for the most recent class."""
        self.control.individual_recall()

    def clear_individual_recall(self) -> None:
        """Take down the individual recall flag."""
        self.control.clear_individual_recall()

    def skip(self) -> None:
        """Remove the next start, moving on to the one after."""
        self.control.skip()

    def abandon(self) -> None:
        """Stop all starts and fly the abandonment flag."""
        self.control.abandon()

    @property
    def is_streaming(self) -> bool:
        """Return whether the stream is running."""
        return self._streaming is not None

    def start(self) -> None:
        """Start reading the camera for the preview."""
        self._source.start()

    def preview(self) -> NDArray[np.uint8]:
        """Return the newest frame with its overlays."""
        return self._compositor.render(self._source.latest())

    def camera_frame(self) -> Frame:
        """Return the camera's newest frame, without overlays."""
        return self._source.latest()

    def camera_is_live(self) -> bool:
        """Return whether the camera's picture is arriving."""
        return self._source.latest().live

    def summary(self) -> RaceSummary:
        """Return the state of the racing, matching what the video shows."""
        return self._display.summary(self._clock())

    def viewer_addresses(self) -> dict[str, str]:
        """Return where viewers can watch."""
        return addresses_for_viewers(self.config)

    @property
    def is_recording(self) -> bool:
        """Return whether the stream is being recorded."""
        recorder = self._recorder()
        return recorder is not None and recorder.is_recording

    def recording_directory(self) -> str:
        """Return where recordings go."""
        recorder = self._recorder()
        return str(recorder.directory) if recorder is not None else ""

    def start_recording(self) -> None:
        """Start recording.

        Raises:
            RecordingError: If the stream isn't running, or recording can't start.
        """
        recorder = self._recorder()
        if recorder is None:
            raise RecordingError("Start the stream before recording")
        recorder.start()

    def stop_recording(self) -> None:
        """Stop recording."""
        recorder = self._recorder()
        if recorder is not None:
            recorder.stop()

    def _recorder(self) -> Recorder | None:
        """Return what controls recording, while streaming."""
        streaming = self._streaming
        return getattr(streaming, "recorder", None) if streaming else None

    def start_stream(self) -> None:
        """Start the stream. This can take a while, so don't call it from a GUI thread.

        Raises:
            MediaServerError: If the server program can't be fetched.
            OSError: If a port can't be opened.
        """
        with self._stream_lock:
            if self._streaming is not None:
                return
            streaming = self._make_streaming(
                self.config, self.secrets, self.control, self._source
            )
            streaming.start()
            self._streaming = streaming

    def stop_stream(self) -> None:
        """Stop the stream, leaving the preview running."""
        with self._stream_lock:
            if self._streaming is not None:
                self._streaming.stop()
                self._streaming = None

    def close(self) -> None:
        """Stop everything."""
        self.stop_stream()
        self._source.stop()
