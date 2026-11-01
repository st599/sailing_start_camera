#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: conftest.py
#  Description: Shared test settings.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: None
#
#########################################################

"""Shared test settings."""

#
# SYSTEM IMPORTS
#

import os

#
#  GLOBAL VARIABLES
#

# GUI tests run without a display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
