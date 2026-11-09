#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_suite_guide.py
#  Description: Keeps the guide for race control suites in step with the code.
#  Date: 2026-11-09
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: pytest
#
#########################################################

"""Keeps the guide for race control suites in step with the code."""

#
# SYSTEM IMPORTS
#

import importlib.util
import logging
import time
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

#
# PROJECT IMPORTS
#
from start_camera.api import CameraSystem, EventKind
from start_camera.config import Config, ControlConfig, Secrets, StreamConfig
from start_camera.ingest import Frame
from start_camera.network_api import SIMPLE_RACE_COMMANDS
from start_camera.openapi import OPENAPI

#
#  GLOBAL VARIABLES
#

ROOT = Path(__file__).parent.parent
GUIDE = (ROOT / "docs" / "suite_guide.md").read_text(encoding="utf-8")
MINUTE = 60.0

#
# FUNCTIONS
#


class FakeSource:
    """Stand in for the camera."""

    def __init__(self, *_: object) -> None:
        """Make a frame."""
        self.frame = Frame(np.zeros((36, 64, 3), np.uint8), 0.0, 1, True)

    def start(self) -> None:
        """Do nothing."""

    def stop(self) -> None:
        """Do nothing."""

    def latest(self) -> Frame:
        """Return the frame."""
        return self.frame


def load_example() -> ModuleType:
    """Return the worked example as a module."""
    spec = importlib.util.spec_from_file_location(
        "suite_day_example", ROOT / "examples" / "suite_day_example.py"
    )
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    return example


def test_the_worked_example_runs_the_day(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    example = load_example()
    now = [time.time()]
    config = Config(
        stream=StreamConfig(width=64, height=36),
        control=ControlConfig(journal_path=str(tmp_path / "journal.jsonl")),
    )
    system = CameraSystem(
        config,
        Secrets(),
        make_source=FakeSource,
        clock=lambda: now[0],
        clock_synchronised=lambda: True,
    )

    def wait_until(moment: float) -> None:
        now[0] = max(now[0], moment)
        system._watcher.check()

    with system, caplog.at_level(logging.INFO):
        example.run_day(system, wait_until)
        events = system.events_since()
    first = example.next_whole_minute(events[0].time, 10) + 5 * MINUTE
    starts = [
        (event.details["name"], event.time)
        for event in events
        if event.kind is EventKind.START
    ]
    assert starts == [
        ("Lasers", pytest.approx(first, abs=0.002)),
        ("Lasers", pytest.approx(first + 10 * MINUTE, abs=0.002)),
        ("Toppers", pytest.approx(first + 15 * MINUTE, abs=0.002)),
    ]
    commands = [
        event.details["command"]
        for event in events
        if event.kind is EventKind.SCHEDULE
    ]
    assert commands == ["set_start", "shift", "general_recall", "set_start"]
    flags = {e.details["flag"] for e in events if e.kind is EventKind.FLAG_UP}
    assert {"L", "T", "P", "First Substitute"} <= flags
    assert "Finished: finished" in caplog.text


def test_guide_covers_every_python_command() -> None:
    public = [
        name
        for name in dir(CameraSystem)
        if not name.startswith("_")
        and callable(getattr(CameraSystem, name))
        and name not in ("start", "close", "from_files")
    ]
    for name in public:
        assert f"{name}(" in GUIDE, name


def test_guide_covers_every_network_request() -> None:
    for path in OPENAPI["paths"]:
        if path == "/openapi.json":
            continue
        assert path in GUIDE, path
    for command in SIMPLE_RACE_COMMANDS:
        assert command in GUIDE, command


def test_guide_covers_every_event() -> None:
    for kind in EventKind:
        assert f"`{kind.value}`" in GUIDE, kind
