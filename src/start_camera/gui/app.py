#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: app.py
#  Description: startcam-gui application.
#  Date: 2026-10-31
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: None
#
#########################################################

"""Entry point for the startcam-gui application."""

#
# SYSTEM IMPORTS
#

import logging

#
# PROJECT IMPORTS
#
from start_camera import __version__
from start_camera.logging_setup import configure_logging

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

#
# FUNCTIONS
#


def main() -> int:
    """Run the GUI application and return its exit code."""
    configure_logging()
    logger.info("startcam-gui %s started", __version__)
    return 0


#########################################################
# MAIN
#########################################################

if __name__ == "__main__":
    raise SystemExit(main())
