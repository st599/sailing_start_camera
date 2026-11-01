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

#
# PROJECT IMPORTS
#
from start_camera.config import Config, Secrets
from start_camera.control import RaceControl
from start_camera.control_page import ControlPage, ControlPageError
from start_camera.discovery import primary_address
from start_camera.media_server import MediaServer, ensure_binary, viewer_urls
from start_camera.output import FrameProvider, Publisher
from start_camera.overlays.factory import build_compositor
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


def start_control_page(
    config: Config, secrets: Secrets, control: RaceControl
) -> ControlPage | None:
    """Start the phone control page if possible. The stream doesn't depend on it."""
    if secrets.control_password is None:
        logger.info("No control_password in the secrets, so the phone page is off")
        return None
    address = primary_address() or "localhost"
    try:
        page = ControlPage(
            config,
            control,
            secrets.control_password.get_secret_value(),
            address=address,
        )
    except (ControlPageError, OSError) as error:
        logger.error("The phone control page is off: %s", error)
        return None
    logger.info("Race officer's phone page: https://%s:%d/", address, page.port)
    return page


def addresses_for_viewers(config: Config) -> dict[str, str]:
    """Return where viewers can watch, by use."""
    return viewer_urls(config.stream, primary_address() or "localhost")


class Streaming:
    """The server, the encoder, the viewer pages, YouTube and the phone page."""

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
        self._server: MediaServer | None = None
        self._viewer: ViewerServer | None = None
        self._publisher: Publisher | None = None
        self._push: YouTubePush | None = None
        self._page: ControlPage | None = None

    def start(self) -> None:
        """Start everything.

        Raises:
            MediaServerError: If the server program can't be fetched.
            OSError: If a port can't be opened.
        """
        config = self._config
        self._server = MediaServer(config.stream, ensure_binary())
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
        render = build_compositor(config, race_clock=self._control).render
        self._publisher = Publisher(
            self._source, self._server, config.stream, render=render
        )
        self._publisher.start()
        self._push = start_youtube(config, self._secrets)
        self._page = start_control_page(config, self._secrets, self._control)

    def stop(self) -> None:
        """Stop everything that's running, in the reverse order."""
        for name in ("_page", "_push", "_publisher", "_viewer", "_server"):
            part = getattr(self, name)
            if part is not None:
                part.stop()
                setattr(self, name, None)
