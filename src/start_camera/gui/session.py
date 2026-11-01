#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: session.py
#  Description: Everything the GUI window controls.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: None
#
#########################################################

"""Everything the GUI window controls, free of the GUI library so it's easy to test.

The preview and the stream draw with separate compositors, as each runs on its own
thread, but they use the same camera frames, overlays and race controls, so they match.
"""

#
# SYSTEM IMPORTS
#

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

#
# PROJECT IMPORTS
#
from start_camera.config import Config, Secrets
from start_camera.control import RaceControl
from start_camera.ingest import Frame, FrameSource
from start_camera.overlays.countdown import RaceDisplay, RaceSummary
from start_camera.overlays.factory import build_compositor
from start_camera.pipeline import Streaming, addresses_for_viewers

#
# FUNCTIONS
#


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


class GuiSession:
    """The camera, the overlays, the race controls and the stream."""

    def __init__(
        self,
        config: Config,
        secrets: Secrets,
        *,
        make_source: Callable[[Config, Secrets], Source] = FrameSource,
        make_streaming: Callable[..., Streaming] = Streaming,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Prepare everything, without starting it.

        Args:
            config: The configuration.
            secrets: The credentials.
            make_source: Makes what reads the camera.
            make_streaming: Makes what publishes the stream.
            clock: Provides the current time.
        """
        self.config = config
        self.control = RaceControl(
            config.sequences, Path(config.control.journal_path), clock
        )
        self._secrets = secrets
        self._source = make_source(config, secrets)
        self._make_streaming = make_streaming
        self._clock = clock
        self._compositor = build_compositor(config, clock, self.control)
        self._display = RaceDisplay(config, self.control)
        self._streaming: Streaming | None = None
        self._stream_lock = threading.Lock()

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

    def camera_is_live(self) -> bool:
        """Return whether the camera's picture is arriving."""
        return self._source.latest().live

    def summary(self) -> RaceSummary:
        """Return the state of the racing, matching what the video shows."""
        return self._display.summary(self._clock())

    def viewer_addresses(self) -> dict[str, str]:
        """Return where viewers can watch."""
        return addresses_for_viewers(self.config)

    def start_stream(self) -> None:
        """Start the stream. This can take a while, so don't call it on the GUI thread.

        Raises:
            MediaServerError: If the server program can't be fetched.
            OSError: If a port can't be opened.
        """
        with self._stream_lock:
            if self._streaming is not None:
                return
            streaming = self._make_streaming(
                self.config, self._secrets, self.control, self._source
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
