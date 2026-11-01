#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_terminal_keys.py
#  Description: Tests for the terminal keys.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: pytest
#
#########################################################

"""Tests for the terminal keys, including on a real pseudo-terminal."""

#
# SYSTEM IMPORTS
#

import logging
import os
import pty
import subprocess
import sys
import termios
import time
from collections.abc import Callable, Iterator

import pytest

#
# PROJECT IMPORTS
#
from start_camera.race_clock import Phase, RaceClockError, RaceState
from start_camera.terminal_keys import CONFIRM_TIMEOUT_S, KeyHandler, TerminalKeys

#
#  GLOBAL VARIABLES
#

T0 = 1000.0

#
# FUNCTIONS
#


class FakeControls:
    """Record the commands sent."""

    def __init__(self) -> None:
        """Start with no commands and no flags."""
        self.calls: list[tuple] = []
        self.flags: tuple[str, ...] = ()
        self.refuse = False

    def state(self, now: float) -> RaceState:
        """Return a state with the test's flags."""
        return RaceState(Phase.IDLE, self.flags, None, None)

    def _record(self, *call: object) -> None:
        if self.refuse:
            raise RaceClockError("refused")
        self.calls.append(call)

    def start_now(self) -> None:
        """Record the command."""
        self._record("start_now")

    def shift(self, minutes: float) -> None:
        """Record the command."""
        self._record("shift", minutes)

    def postpone(self) -> None:
        """Record the command."""
        self._record("postpone")

    def resume(self) -> None:
        """Record the command."""
        self._record("resume")

    def general_recall(self) -> None:
        """Record the command."""
        self._record("general_recall")

    def individual_recall(self) -> None:
        """Record the command."""
        self._record("individual_recall")

    def clear_individual_recall(self) -> None:
        """Record the command."""
        self._record("clear_individual_recall")

    def skip(self) -> None:
        """Record the command."""
        self._record("skip")

    def abandon(self) -> None:
        """Record the command."""
        self._record("abandon")


class Harness:
    """A key handler with a settable clock."""

    def __init__(self) -> None:
        """Set everything up."""
        self.controls = FakeControls()
        self.quit_requests = 0
        self.now = T0
        self.handler = KeyHandler(self.controls, self._quit, lambda: self.now)

    def _quit(self) -> None:
        self.quit_requests += 1


@pytest.fixture
def harness() -> Harness:
    """Return a fresh handler."""
    return Harness()


@pytest.mark.parametrize(
    ("key", "call"),
    [
        ("s", ("start_now",)),
        ("1", ("shift", 1.0)),
        ("5", ("shift", 5.0)),
        ("0", ("shift", 10.0)),
        ("-", ("shift", -1.0)),
        ("p", ("postpone",)),
        ("r", ("resume",)),
        ("g", ("general_recall",)),
        ("x", ("individual_recall",)),
        ("n", ("skip",)),
    ],
)
def test_keys_send_commands(harness: Harness, key: str, call: tuple) -> None:
    harness.handler.handle(key)
    assert harness.controls.calls == [call]


def test_x_clears_a_flying_recall(harness: Harness) -> None:
    harness.controls.flags = ("X",)
    harness.handler.handle("x")
    assert harness.controls.calls == [("clear_individual_recall",)]


def test_abandon_needs_confirming(harness: Harness) -> None:
    harness.handler.handle("a")
    assert harness.controls.calls == []
    harness.handler.handle("y")
    assert harness.controls.calls == [("abandon",)]


def test_quit_needs_confirming(harness: Harness) -> None:
    harness.handler.handle("q")
    assert harness.quit_requests == 0
    harness.handler.handle("y")
    assert harness.quit_requests == 1


@pytest.mark.parametrize("first", ["a", "q"])
def test_another_key_cancels_a_confirmation(harness: Harness, first: str) -> None:
    harness.handler.handle(first)
    harness.handler.handle("n")
    harness.handler.handle("y")
    assert harness.controls.calls == []
    assert harness.quit_requests == 0


def test_cancelling_key_doesnt_also_act(harness: Harness) -> None:
    harness.handler.handle("a")
    harness.handler.handle("p")
    assert harness.controls.calls == []


def test_confirmation_times_out(harness: Harness) -> None:
    harness.handler.handle("a")
    harness.now += CONFIRM_TIMEOUT_S + 1
    harness.handler.handle("y")
    assert harness.controls.calls == []


def test_key_after_a_timeout_acts_normally(harness: Harness) -> None:
    harness.handler.handle("a")
    harness.now += CONFIRM_TIMEOUT_S + 1
    harness.handler.handle("p")
    assert harness.controls.calls == [("postpone",)]


def test_y_alone_does_nothing(harness: Harness) -> None:
    harness.handler.handle("y")
    assert harness.controls.calls == []
    assert harness.quit_requests == 0


@pytest.mark.parametrize("key", ["z", "\n", " ", ""])
def test_other_keys_are_ignored(harness: Harness, key: str) -> None:
    harness.handler.handle(key)
    assert harness.controls.calls == []


def test_refused_command_doesnt_stop_the_keys(harness: Harness) -> None:
    harness.controls.refuse = True
    harness.handler.handle("r")
    harness.controls.refuse = False
    harness.handler.handle("p")
    assert harness.controls.calls == [("postpone",)]


def test_help_lists_the_keys(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        harness.handler.handle("h")
    assert "general recall" in caplog.text


def wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Return whether a condition becomes true in time."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


CHILD = """
import logging, os, sys, termios, time
from start_camera.terminal_keys import KeyHandler, TerminalKeys
logging.disable(logging.CRITICAL)
class C:
    def start_now(self): print("started", flush=True)
    def __getattr__(self, name): return lambda *a: None
keys = TerminalKeys(KeyHandler(C(), lambda: None), 0)
before = termios.tcgetattr(0)
print("usable", keys.start(), flush=True)
print("single", not termios.tcgetattr(0)[3] & termios.ICANON, flush=True)
time.sleep(1.0)
keys.stop()
print("restored", termios.tcgetattr(0) == before, flush=True)
"""


@pytest.fixture
def child_on_a_terminal() -> Iterator[tuple[int, subprocess.Popen[bytes]]]:
    """Run a program on a real pseudo-terminal as its foreground process."""
    controller, follower = pty.openpty()

    def take_terminal() -> None:
        os.setsid()
        import fcntl

        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    process = subprocess.Popen(
        [sys.executable, "-c", CHILD],
        stdin=follower,
        stdout=subprocess.PIPE,
        preexec_fn=take_terminal,
    )
    os.close(follower)
    try:
        yield controller, process
    finally:
        process.kill()
        process.wait()
        os.close(controller)


def test_single_key_works_on_a_real_terminal(
    child_on_a_terminal: tuple[int, subprocess.Popen[bytes]],
) -> None:
    controller, process = child_on_a_terminal
    assert process.stdout.readline().strip() == b"usable True"
    assert process.stdout.readline().strip() == b"single True"
    os.write(controller, b"s")
    assert process.stdout.readline().strip() == b"started"
    assert process.stdout.readline().strip() == b"restored True"


def test_no_terminal_turns_the_keys_off(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    read_end, write_end = os.pipe()
    keys = TerminalKeys(harness.handler, read_end)
    with caplog.at_level(logging.INFO):
        assert keys.start() is False
    keys.stop()
    os.close(read_end)
    os.close(write_end)
    assert "keys are off" in caplog.text


def test_closed_input_turns_the_keys_off(harness: Harness) -> None:
    read_end, write_end = os.pipe()
    os.close(read_end)
    os.close(write_end)
    assert TerminalKeys(harness.handler, read_end).start() is False
