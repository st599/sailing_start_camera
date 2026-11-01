#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: terminal_keys.py
#  Description: Race controls from single key presses in a terminal.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: None
#
#########################################################

"""Race controls from single key presses in a terminal.

Keys that end something, abandoning the racing or quitting, need confirming, so a stray
key press can't do either.
"""

#
# SYSTEM IMPORTS
#

import logging
import os
import select
import termios
import threading
import time
import tty
from collections.abc import Callable
from typing import Protocol

#
# PROJECT IMPORTS
#
from start_camera.race_clock import INDIVIDUAL_RECALL_FLAG, RaceClockError, RaceState

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

CONFIRM_KEY = "y"
CONFIRM_TIMEOUT_S = 5.0
POLL_INTERVAL_S = 0.1
DELAY_KEYS = {"1": 1.0, "5": 5.0, "0": 10.0, "-": -1.0}
HELP = (
    "Keys: s start now | 1 5 0 delay +1 +5 +10 min | - delay -1 min | p postpone | "
    "r resume | g general recall | x individual recall on/off | n next start | "
    "a abandon | q quit | h this help"
)

#
# FUNCTIONS
#


class Controls(Protocol):
    """The race commands that keys can send."""

    def state(self, now: float) -> RaceState:
        """Return the state at a moment."""
        ...

    def start_now(self) -> None:
        """Bring the next start forward."""
        ...

    def shift(self, minutes: float) -> None:
        """Move the starts that haven't happened."""
        ...

    def postpone(self) -> None:
        """Postpone the starts."""
        ...

    def resume(self) -> None:
        """End a postponement."""
        ...

    def general_recall(self) -> None:
        """Recall the most recent class."""
        ...

    def individual_recall(self) -> None:
        """Fly the individual recall flag."""
        ...

    def clear_individual_recall(self) -> None:
        """Take down the individual recall flag."""
        ...

    def skip(self) -> None:
        """Move on to the next start."""
        ...

    def abandon(self) -> None:
        """Abandon the racing."""
        ...


class KeyHandler:
    """Turn key presses into race commands."""

    def __init__(
        self,
        controls: Controls,
        request_quit: Callable[[], None],
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Hold what the keys act on.

        Args:
            controls: The race commands.
            request_quit: Called when quitting is confirmed.
            clock: Provides the current time.
        """
        self._controls = controls
        self._request_quit = request_quit
        self._clock = clock
        self._pending: Callable[[], None] | None = None
        self._pending_until = 0.0
        self._commands: dict[str, Callable[[], None]] = {
            "s": controls.start_now,
            "p": controls.postpone,
            "r": controls.resume,
            "g": controls.general_recall,
            "x": self._toggle_individual_recall,
            "n": controls.skip,
        }

    def handle(self, key: str) -> None:
        """Act on a key press. A refused command is logged and otherwise ignored."""
        if self._resolve_confirmation(key):
            return
        if key == "a":
            self._ask_to_confirm("abandon the racing", self._controls.abandon)
        elif key == "q":
            self._ask_to_confirm("quit and stop the stream", self._request_quit)
        elif key == "h":
            logger.info("%s", HELP)
        elif key in DELAY_KEYS:
            self._run(lambda: self._controls.shift(DELAY_KEYS[key]))
        elif key in self._commands:
            self._run(self._commands[key])

    def _resolve_confirmation(self, key: str) -> bool:
        """Deal with a key answering a confirmation, and return whether it did."""
        pending = self._pending
        if pending is None:
            return False
        self._pending = None
        if self._clock() > self._pending_until:
            logger.info("That confirmation timed out")
            return False
        if key == CONFIRM_KEY:
            self._run(pending)
        else:
            logger.info("Cancelled")
        return True

    def _ask_to_confirm(self, action: str, command: Callable[[], None]) -> None:
        """Hold a command until it's confirmed."""
        self._pending = command
        self._pending_until = self._clock() + CONFIRM_TIMEOUT_S
        logger.info("Press %s to %s, or any other key to cancel", CONFIRM_KEY, action)

    def _toggle_individual_recall(self) -> None:
        """Raise the individual recall flag, or take it down if it's flying."""
        flags = self._controls.state(self._clock()).flags
        if INDIVIDUAL_RECALL_FLAG in flags:
            self._controls.clear_individual_recall()
        else:
            self._controls.individual_recall()

    def _run(self, command: Callable[[], None]) -> None:
        """Run a command. Refusals have already been logged with the reason."""
        try:
            command()
        except RaceClockError as error:
            logger.debug("Key command refused: %s", error)


class TerminalKeys:
    """Read single key presses from a terminal in the background."""

    def __init__(self, handler: KeyHandler, file_descriptor: int) -> None:
        """Hold the handler and the terminal.

        Args:
            handler: Acts on the keys.
            file_descriptor: The terminal to read.
        """
        self._handler = handler
        self._file_descriptor = file_descriptor
        self._settings: list | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """Start reading keys, and return whether there's a terminal to read them from.

        Without one, such as when running as a service, this does nothing.
        """
        if not self._in_foreground():
            logger.info("No terminal, so the race control keys are off")
            return False
        self._settings = termios.tcgetattr(self._file_descriptor)
        # Single key presses, while Ctrl+C still stops the application.
        tty.setcbreak(self._file_descriptor)
        self._thread = threading.Thread(target=self._run, daemon=True, name="keys")
        self._thread.start()
        logger.info("%s", HELP)
        return True

    def stop(self) -> None:
        """Stop reading keys and put the terminal back as it was."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        if self._settings is not None:
            termios.tcsetattr(
                self._file_descriptor, termios.TCSADRAIN, self._settings
            )
            self._settings = None

    def _in_foreground(self) -> bool:
        """Return whether this is a terminal that this process may read."""
        try:
            return (
                os.isatty(self._file_descriptor)
                and os.tcgetpgrp(self._file_descriptor) == os.getpgrp()
            )
        except OSError:
            return False

    def _run(self) -> None:
        """Pass on each key press until stopped or the terminal goes away."""
        while not self._stop.is_set():
            ready, _, _ = select.select(
                [self._file_descriptor], [], [], POLL_INTERVAL_S
            )
            if not ready:
                continue
            try:
                data = os.read(self._file_descriptor, 1)
            except OSError:
                return
            if not data:
                return
            self._handler.handle(data.decode(errors="ignore").lower())
