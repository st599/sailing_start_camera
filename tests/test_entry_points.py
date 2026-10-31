#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_entry_points.py
#  Description: Tests for the application entry points.
#  Date: 2026-10-31
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: pytest
#
#########################################################

"""Tests for the application entry points."""

#
# SYSTEM IMPORTS
#

from collections.abc import Callable
from importlib.metadata import entry_points

import pytest

#
# PROJECT IMPORTS
#
from start_camera import __version__, cli
from start_camera.gui import app

#
# FUNCTIONS
#


@pytest.mark.parametrize(
    ("main", "name"), [(cli.main, "startcam"), (app.main, "startcam-gui")]
)
def test_main_logs_start_without_using_stdout(
    main: Callable[[], int], name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each application logs that it started, exits cleanly and leaves stdout alone."""
    result = main()
    captured = capsys.readouterr()
    assert result == 0
    assert f"{name} {__version__} started" in captured.err
    assert captured.out == ""


@pytest.mark.parametrize("name", ["startcam", "startcam-gui"])
def test_entry_point_is_installed(name: str) -> None:
    """The console commands are declared by the installed package."""
    assert entry_points(group="console_scripts", name=name)
