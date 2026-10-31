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
#  Dependencies: ffmpeg
#
#########################################################

"""Entry point for the startcam command-line application."""

#
# SYSTEM IMPORTS
#

import argparse
import logging
from pathlib import Path

#
# PROJECT IMPORTS
#
from start_camera import __version__
from start_camera.config import (
    Config,
    ConfigError,
    load_config,
    load_secrets,
    save_config,
)
from start_camera.discovery import (
    DiscoveryError,
    discover,
    log_candidates,
    select_camera,
)
from start_camera.logging_setup import configure_logging

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

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
    arguments = parser.parse_args()
    if arguments.write and not (arguments.scan and arguments.config):
        parser.error("--write needs --scan and --config")
    return arguments


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


def main() -> int:
    """Run the command-line application and return its exit code."""
    configure_logging()
    arguments = _parse_arguments()
    logger.info("startcam %s started", __version__)
    if not arguments.scan:
        return 0
    try:
        _scan(arguments)
    except (ConfigError, DiscoveryError, OSError, ValueError) as error:
        logger.error("%s", error)
        return 1
    return 0


#########################################################
# MAIN
#########################################################

if __name__ == "__main__":
    raise SystemExit(main())
