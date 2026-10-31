#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_logging_setup.py
#  Description: Tests for the shared logging configuration.
#  Date: 2026-10-31
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: pytest
#
#########################################################

"""Tests for the shared logging configuration."""

#
# SYSTEM IMPORTS
#

import logging
from pathlib import Path

import pytest

#
# PROJECT IMPORTS
#
from start_camera.logging_setup import configure_logging

#
# FUNCTIONS
#


def test_records_are_written_to_log_file(tmp_path: Path) -> None:
    """A configured log file receives log records."""
    log_file = tmp_path / "startcam.log"
    configure_logging(log_file=log_file)
    logging.getLogger("test").info("hello")
    logging.shutdown()
    assert "hello" in log_file.read_text(encoding="utf-8")


def test_level_filters_lower_records(tmp_path: Path) -> None:
    """Records below the chosen level are dropped."""
    log_file = tmp_path / "startcam.log"
    configure_logging(level="warning", log_file=log_file)
    logging.getLogger("test").info("quiet")
    logging.getLogger("test").warning("loud")
    logging.shutdown()
    text = log_file.read_text(encoding="utf-8")
    assert "quiet" not in text
    assert "loud" in text


def test_unknown_level_is_rejected() -> None:
    """An unknown level name is an error."""
    with pytest.raises(ValueError):
        configure_logging(level="chatty")
