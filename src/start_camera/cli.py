#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: cli.py
#  Description: startcam command-line application.
#  Date: 2026-10-31
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: ffmpeg, MediaMTX
#
#########################################################

"""Entry point for the startcam command-line application."""

#
# SYSTEM IMPORTS
#

import argparse
import logging
import signal
import socket
import threading
from pathlib import Path
from types import FrameType

#
# PROJECT IMPORTS
#
from start_camera import __version__
from start_camera.clock_check import warn_if_clock_unsynchronised
from start_camera.config import (
    Config,
    ConfigError,
    Secrets,
    load_config,
    load_secrets,
    save_config,
)
from start_camera.control import RaceControl
from start_camera.control_page import ControlPage, ControlPageError
from start_camera.discovery import (
    DiscoveryError,
    discover,
    log_candidates,
    primary_address,
    select_camera,
)
from start_camera.ingest import FrameSource
from start_camera.logging_setup import configure_logging
from start_camera.media_server import (
    MediaServer,
    MediaServerError,
    ensure_binary,
    viewer_urls,
)
from start_camera.output import Publisher
from start_camera.overlays.factory import build_compositor
from start_camera.terminal_keys import KeyHandler, TerminalKeys
from start_camera.viewer import ViewerServer
from start_camera.youtube import YouTubePush

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
# A service may have no sys.stdin at all, so this avoids relying on it.
STDIN_FILE_DESCRIPTOR = 0

#
# FUNCTIONS
#


def _parse_arguments() -> argparse.Namespace:
    """Return the command-line arguments."""
    parser = argparse.ArgumentParser(description="Sailing start line camera.")
    parser.add_argument("--config", type=Path, help="configuration file")
    parser.add_argument("--secrets", type=Path, help="secrets file")
    parser.add_argument("--scan", action="store_true", help="find the camera and exit")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="check the configuration and secrets files, and exit",
    )
    parser.add_argument(
        "--subnet",
        action="append",
        default=[],
        help="another subnet for --scan, such as 192.168.1.0/24; can be repeated",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="save the camera found by --scan to the configuration file",
    )
    parser.add_argument(
        "--log-level",
        choices=LOG_LEVELS,
        default="INFO",
        type=str.upper,
        help="the least important messages to show",
    )
    parser.add_argument("--log-file", type=Path, help="also write the log to this file")
    arguments = parser.parse_args()
    if arguments.write and not (arguments.scan and arguments.config):
        parser.error("--write needs --scan and --config")
    if arguments.check_config and not arguments.config:
        parser.error("--check-config needs --config")
    if not (arguments.scan or arguments.config):
        parser.error("use --config to stream, or --scan to find the camera")
    return arguments


def _check_config(arguments: argparse.Namespace) -> None:
    """Check the configuration files, and report anything that will cause a problem."""
    config = load_config(arguments.config)
    secrets = load_secrets(arguments.secrets)
    logger.info("%s is valid", arguments.config)
    if config.camera.host is None:
        logger.warning("camera.host isn't set, so the camera will be found by scanning")
    if secrets.camera_password is None:
        logger.warning("There's no camera password in the secrets")
    if config.stream.youtube_enabled and secrets.youtube_stream_key is None:
        logger.warning("YouTube is enabled, but the secrets have no youtube_stream_key")
    if secrets.control_password is None:
        logger.warning("There's no control_password, so the phone page will be off")
    logger.info("%d start sequences configured", len(config.sequences.items))


def _scan(arguments: argparse.Namespace) -> None:
    """Find the camera, log it and optionally save it."""
    config = load_config(arguments.config) if arguments.config else Config()
    candidates = discover(config, load_secrets(arguments.secrets), arguments.subnet)
    log_candidates(candidates)
    camera = select_camera(candidates, config.camera.mac)
    logger.info("Camera is at %s", camera.host)
    if arguments.write:
        config.camera.host = camera.host
        config.camera.mac = camera.mac or config.camera.mac
        save_config(config, arguments.config)


def _stop_on_signals() -> threading.Event:
    """Return an event that's set when the application is asked to stop."""
    stop = threading.Event()

    def request_stop(number: int, frame: FrameType | None) -> None:
        stop.set()

    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, request_stop)
    return stop


def _start_youtube(config: Config, secrets: Secrets) -> YouTubePush | None:
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


def _start_control_page(
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


def _log_viewer_addresses(config: Config) -> None:
    """Log where viewers can watch."""
    address = primary_address() or "localhost"
    for name, url in viewer_urls(config.stream, address).items():
        logger.info("%s: %s", name, url)
    logger.info(
        "If the address changes, http://%s.local:%d/ may still work",
        socket.gethostname(), config.stream.viewer_port,
    )  # fmt: skip


def _run_pipeline(
    config: Config, secrets: Secrets, server: MediaServer, stop: threading.Event
) -> None:
    """Run the stream and its controls until asked to stop."""
    control = RaceControl(config.sequences, Path(config.control.journal_path))
    render = build_compositor(config, race_clock=control).render
    keys = TerminalKeys(KeyHandler(control, stop.set), STDIN_FILE_DESCRIPTOR)
    with (
        FrameSource(config, secrets) as source,
        Publisher(source, server, config.stream, render=render),
    ):
        push = _start_youtube(config, secrets)
        page = _start_control_page(config, secrets, control)
        keys.start()
        try:
            stop.wait()
        finally:
            keys.stop()
            if page is not None:
                page.stop()
            if push is not None:
                push.stop()


def _stream(arguments: argparse.Namespace) -> None:
    """Publish the camera with the configuration until asked to stop."""
    config = load_config(arguments.config)
    secrets = load_secrets(arguments.secrets)
    stop = _stop_on_signals()
    warn_if_clock_unsynchronised()
    server = MediaServer(config.stream, ensure_binary())
    try:
        viewer = ViewerServer(config.stream)
        try:
            _log_viewer_addresses(config)
            _run_pipeline(config, secrets, server, stop)
        finally:
            viewer.stop()
    finally:
        server.stop()
    logger.info("Stopped")


def _run(arguments: argparse.Namespace) -> None:
    """Do what the arguments ask for."""
    if arguments.check_config:
        _check_config(arguments)
    elif arguments.scan:
        _scan(arguments)
    else:
        _stream(arguments)


def main() -> int:
    """Run the command-line application and return its exit code."""
    arguments = _parse_arguments()
    try:
        configure_logging(arguments.log_level, arguments.log_file)
    except OSError as error:
        configure_logging(arguments.log_level)
        logger.error("Can't open the log file: %s", error)
        return 1
    logger.info("startcam %s started", __version__)
    try:
        _run(arguments)
    except (
        ConfigError,
        DiscoveryError,
        MediaServerError,
        OSError,
        ValueError,
    ) as error:
        logger.error("%s", error)
        return 1
    return 0


#########################################################
# MAIN
#########################################################

if __name__ == "__main__":
    raise SystemExit(main())
