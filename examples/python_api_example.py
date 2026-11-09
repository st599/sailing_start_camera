#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: python_api_example.py
#  Description: Run a start sequence through the Python interface.
#  Date: 2026-11-09
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: start_camera
#
#########################################################

"""Run a start sequence through the Python interface, as a race control suite might.

    python examples/python_api_example.py config.json secrets.json
"""

#
# SYSTEM IMPORTS
#

import logging
import sys
import time

#
# PROJECT IMPORTS
#
from start_camera.api import CameraSystem, RaceClockError

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger("example")

#
# FUNCTIONS
#


def run(config_path: str, secrets_path: str | None, seconds: float = 20.0) -> None:
    """Stream, start a sequence, delay it, then stop."""
    with CameraSystem.from_files(config_path, secrets_path) as system:
        system.start_stream()
        system.start_now()
        status = system.status()
        logger.info("Countdown: %s, flags: %s", status.display, status.flags)
        system.shift(1)
        logger.info("After a 1 minute delay: %s", system.status().display)
        try:
            system.resume()
        except RaceClockError as error:
            logger.info("Refused, as expected: %s", error)
        time.sleep(seconds)
        logger.info("Camera live: %s", system.status().camera_live)


#########################################################
# MAIN
#########################################################

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
