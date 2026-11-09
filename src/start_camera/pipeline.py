#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: pipeline.py
#  Description: Start and stop everything that publishes the stream.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: ffmpeg, MediaMTX
#
#########################################################

"""Start and stop everything that publishes the stream.

Both applications use this, so that they can't drift apart.
"""

#
# SYSTEM IMPORTS
#

import logging
import socket
from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray

#
# PROJECT IMPORTS
#
from start_camera.config import Config, Secrets, SequencesConfig
from start_camera.control import RaceControl
from start_camera.discovery import primary_address
from start_camera.ingest import Frame
from start_camera.media_server import MediaServer, ensure_binary, viewer_urls
from start_camera.output import FrameProvider, Publisher
from start_camera.overlays.factory import build_compositor
from start_camera.recording import Recorder, RecordingError
from start_camera.viewer import ViewerServer
from start_camera.youtube import YouTubePush

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

#
# FUNCTIONS
#


def start_youtube(config: Config, secrets: Secrets) -> YouTubePush | None:
    """Start the YouTube push if it's enabled and possible."""
    if not config.stream.youtube_enabled:
        return None
    if secrets.youtube_stream_key is None:
        logger.error("YouTube is enabled, but the secrets have no youtube_stream_key")
        return None
    push = YouTubePush(config.stream, secrets.youtube_stream_key.get_secret_value())
    push.start()
    logger.info("YouTube push enabled")
    return push


def addresses_for_viewers(config: Config) -> dict[str, str]:
    """Return where viewers can watch, by use."""
    return viewer_urls(config.stream, primary_address() or "localhost")


class Streaming:
    """The server, the encoder, the viewer pages and YouTube."""

    def __init__(
        self,
        config: Config,
        secrets: Secrets,
        control: RaceControl,
        source: FrameProvider,
    ) -> None:
        """Hold what the stream is made from.

        Args:
            config: The configuration.
            secrets: The credentials.
            control: The race controls.
            source: Provides the camera's frames.
        """
        self._config = config
        self._secrets = secrets
        self._control = control
        self._source = source
        self._render: Callable[[Frame], NDArray[np.uint8]] | None = None
        self._server: MediaServer | None = None
        self._viewer: ViewerServer | None = None
        self._publisher: Publisher | None = None
        self._push: YouTubePush | None = None
        self.recorder: Recorder | None = None

    def start(self) -> None:
        """Start everything.

        Raises:
            MediaServerError: If the server program can't be fetched.
            OSError: If a port can't be opened.
        """
        config = self._config
        self._server = MediaServer(config.stream, ensure_binary(), config.recording)
        self.recorder = Recorder(
            config.recording, self._server, config.stream.bitrate_kbps
        )
        try:
            self._viewer = ViewerServer(config.stream)
        except OSError:
            self.stop()
            raise
        for name, url in addresses_for_viewers(config).items():
            logger.info("%s: %s", name, url)
        logger.info(
            "If the address changes, http://%s.local:%d/ may still work",
            socket.gethostname(), config.stream.viewer_port,
        )  # fmt: skip
        self._render = build_compositor(config, race_clock=self._control).render
        self._publisher = Publisher(
            self._source, self._server, config.stream, render=self._draw
        )
        self._publisher.start()
        self._push = start_youtube(config, self._secrets)
        if config.recording.start_with_stream:
            self._start_recording()

    @property
    def youtube_state(self) -> str:
        """Return "off", "connecting", or "live" when video is reaching YouTube."""
        push = self._push
        if push is None:
            return "off"
        return "live" if push.is_live else "connecting"

    def use_sequences(self, sequences: SequencesConfig) -> None:
        """Draw the overlays for new sequences from the next frame on."""
        self._config = self._config.model_copy(update={"sequences": sequences})
        if self._render is not None:
            self._render = build_compositor(
                self._config, race_clock=self._control
            ).render

    def _draw(self, frame: Frame) -> NDArray[np.uint8]:
        """Return a frame with the current overlays."""
        render = self._render
        return frame.image if render is None else render(frame)

    def _start_recording(self) -> None:
        """Start recording, without letting a problem stop the stream."""
        try:
            self.recorder.start()
        except RecordingError as error:
            logger.error("Not recording: %s", error)

    def stop(self) -> None:
        """Stop everything that's running, in the reverse order."""
        if self.recorder is not None:
            # Stopping first finishes the current recording properly.
            self.recorder.close()
            self.recorder = None
        for name in ("_push", "_publisher", "_viewer", "_server"):
            part = getattr(self, name)
            if part is not None:
                part.stop()
                setattr(self, name, None)
