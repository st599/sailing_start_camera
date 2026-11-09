#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: errors.py
#  Description: Errors that a race control suite may need to tell apart.
#  Date: 2026-11-09
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: None
#
#########################################################

"""Errors that a race control suite may need to tell apart.

They're kept here, rather than with the Python interface, so the network interface
can use them too.
"""

#
# PROJECT IMPORTS
#
from start_camera.race_clock import RaceClockError

#
# FUNCTIONS
#


class SequencesError(ValueError):
    """Sequences can't be used, for the reasons in problems."""

    def __init__(self, problems: list[str]) -> None:
        """Hold the problems, one per line of the message."""
        super().__init__("\n".join(["The sequences can't be used:", *problems]))
        self.problems = problems


class ClockNotSynchronisedError(RaceClockError):
    """A start time can't be accepted, as this computer's clock may be wrong."""
