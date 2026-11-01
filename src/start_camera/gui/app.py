#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: app.py
#  Description: startcam-gui application.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: PySide6
#
#########################################################

"""Entry point for the startcam-gui application."""

#
# SYSTEM IMPORTS
#

import argparse
import logging
import signal
from pathlib import Path
from types import FrameType

#
# PROJECT IMPORTS
#
from start_camera import __version__
from start_camera.clock_check import warn_if_clock_unsynchronised
from start_camera.config import Config, ConfigError, load_secrets
from start_camera.logging_setup import configure_logging

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

SIGNAL_CHECK_MILLISECONDS = 200

#
# FUNCTIONS
#


def _parse_arguments() -> argparse.Namespace:
    """Return the command-line arguments."""
    parser = argparse.ArgumentParser(description="Sailing start camera GUI.")
    parser.add_argument("--config", type=Path, help="configuration file")
    parser.add_argument("--secrets", type=Path, help="secrets file")
    parser.add_argument("--log-file", type=Path, help="also write the log to this file")
    return parser.parse_args()


def _run_window(arguments: argparse.Namespace) -> int:
    """Show the window until it's closed, and return the exit code."""
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        from start_camera.gui.main_window import MainWindow
        from start_camera.gui.session import GuiSession
        from start_camera.gui.settings_model import ConfigDocument
    except ImportError as error:
        logger.error(
            "The GUI needs PySide6. Install it with: pip install .[gui] (%s)", error
        )
        return 1
    document = (
        ConfigDocument.open(arguments.config)
        if arguments.config
        else ConfigDocument(Config())
    )
    secrets = load_secrets(arguments.secrets)
    warn_if_clock_unsynchronised()
    application = QApplication([])
    window = MainWindow(GuiSession(document.config, secrets), document)

    def request_quit(number: int, frame: FrameType | None) -> None:
        window.close()

    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, request_quit)
    # Python only notices signals when it runs, which this timer makes sure of.
    wake = QTimer()
    wake.start(SIGNAL_CHECK_MILLISECONDS)
    wake.timeout.connect(lambda: None)
    window.start()
    window.show()
    return application.exec()


def main() -> int:
    """Run the GUI application and return its exit code."""
    arguments = _parse_arguments()
    configure_logging(log_file=arguments.log_file)
    logger.info("startcam-gui %s started", __version__)
    try:
        return _run_window(arguments)
    except (ConfigError, OSError) as error:
        logger.error("%s", error)
        return 1


#########################################################
# MAIN
#########################################################

if __name__ == "__main__":
    raise SystemExit(main())
