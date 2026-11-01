#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: clock_check.py
#  Description: Check that the system clock is synchronised.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: systemd (timedatectl)
#
#########################################################

"""Check that the system clock is synchronised, as the overlays rely on it."""

#
# SYSTEM IMPORTS
#

import logging
import subprocess

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

TIMEOUT_S = 5

#
# FUNCTIONS
#


def clock_is_synchronised() -> bool | None:
    """Return whether NTP has set the system clock, or None if that's unknown."""
    try:
        reply = subprocess.run(
            ["timedatectl", "show", "--property=NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return {"yes": True, "no": False}.get(reply.stdout.strip())


def warn_if_clock_unsynchronised() -> None:
    """Log a warning if the system clock may be wrong."""
    synchronised = clock_is_synchronised()
    if synchronised is False:
        logger.warning(
            "The system clock isn't synchronised with NTP, so the time shown on the "
            "video may be wrong"
        )
    elif synchronised is None:
        logger.info("Can't tell whether the system clock is synchronised")
