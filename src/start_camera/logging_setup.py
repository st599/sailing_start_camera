#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: logging_setup.py
#  Description: Shared logging configuration.
#  Date: 2026-10-31
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: None
#
#########################################################

"""Shared logging configuration for both applications."""

#
# SYSTEM IMPORTS
#

import logging
from pathlib import Path

#
#  GLOBAL VARIABLES
#

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

#
# FUNCTIONS
#


def configure_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """Send log records to stderr and, optionally, to a file.

    Args:
        level: Name of the minimum level to record, such as "INFO".
        log_file: Optional file that also receives the records.

    Raises:
        ValueError: If level is not a known logging level.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level.upper(), format=LOG_FORMAT, handlers=handlers, force=True
    )
