#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: control.py
#  Description: One command interface for all the race controls.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: None
#
#########################################################

"""One command interface for all the race controls.

The GUI, the terminal keys and the phone page all use RaceControl, which makes commands
safe to send from any thread.

Every command is written to a journal with its time. The journal is both the record for
later review and how the racing survives a restart: the race clock's state depends only
on its commands and their times, so replaying them gives the same state.
"""

#
# SYSTEM IMPORTS
#

import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

#
# PROJECT IMPORTS
#
from start_camera.config import SequencesConfig
from start_camera.race_clock import RaceClock, RaceClockError, RaceState

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

SESSION_RECORD = "session"
COMMAND_RECORD = "command"
COMMANDS: dict[str, Callable[..., None]] = {
    "start_now": RaceClock.start_now,
    "shift": RaceClock.shift,
    "postpone": RaceClock.postpone,
    "resume": RaceClock.resume,
    "general_recall": RaceClock.general_recall,
    "individual_recall": RaceClock.individual_recall,
    "clear_individual_recall": RaceClock.clear_individual_recall,
    "skip": RaceClock.skip,
    "abandon": RaceClock.abandon,
}

#
# FUNCTIONS
#


def _sequences_hash(config: SequencesConfig) -> str:
    """Return a value that changes if the sequences change."""
    return hashlib.sha256(config.model_dump_json().encode()).hexdigest()


def _read_records(path: Path) -> list[dict[str, Any]]:
    """Return a journal's records, skipping any damaged by a crash during writing."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as error:
        logger.warning("Can't read the race journal %s: %s", path, error)
        return []
    records = []
    for number, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Skipping damaged line %d of the race journal", number)
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _commands_to_replay(
    records: list[dict[str, Any]], today: date, sequences_hash: str
) -> list[dict[str, Any]] | None:
    """Return today's session's commands, or None if it isn't for today's racing."""
    commands = None
    for record in records:
        if record.get("record") == SESSION_RECORD:
            matches = (
                record.get("date") == today.isoformat()
                and record.get("sequences") == sequences_hash
            )
            commands = [] if matches else None
        elif commands is not None and record.get("record") == COMMAND_RECORD:
            if record.get("accepted"):
                commands.append(record)
    return commands


class RaceControl:
    """A race clock that can be used safely from any thread, with a journal."""

    def __init__(
        self,
        config: SequencesConfig,
        journal_path: Path,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Set up the race clock, resuming today's racing if there's a journal for it.

        Args:
            config: The sequences.
            journal_path: The journal file.
            clock: Provides the current time.
        """
        self._lock = threading.Lock()
        self._clock = clock
        self._journal_path = journal_path
        self._sequences_hash = _sequences_hash(config)
        now = clock()
        self._race_clock = RaceClock(config, now)
        replay = _commands_to_replay(
            _read_records(journal_path),
            datetime.fromtimestamp(now).date(),
            self._sequences_hash,
        )
        self._session_written = replay is not None
        if replay:
            self._replay(replay)

    def state(self, now: float) -> RaceState:
        """Return the state of the start sequences at a moment."""
        with self._lock:
            return self._race_clock.state(now)

    def start_now(self) -> None:
        """Bring the next start forward so its warning signal is now."""
        self._run("start_now")

    def shift(self, minutes: float) -> None:
        """Move every start that hasn't happened by a number of minutes."""
        self._run("shift", minutes=minutes)

    def postpone(self) -> None:
        """Hold all starts that haven't happened, and fly the postponement flag."""
        self._run("postpone")

    def resume(self) -> None:
        """End a postponement."""
        self._run("resume")

    def general_recall(self) -> None:
        """Recall the most recent class, which then starts again."""
        self._run("general_recall")

    def individual_recall(self) -> None:
        """Fly the individual recall flag for the most recent class."""
        self._run("individual_recall")

    def clear_individual_recall(self) -> None:
        """Take down the individual recall flag."""
        self._run("clear_individual_recall")

    def skip(self) -> None:
        """Remove the next start, moving on to the one after."""
        self._run("skip")

    def abandon(self) -> None:
        """Stop all starts and fly the abandonment flag."""
        self._run("abandon")

    def _run(self, command: str, **arguments: float) -> None:
        """Carry out a command and record it, whether or not it was accepted.

        Raises:
            RaceClockError: If the command can't be carried out in the current state.
        """
        with self._lock:
            now = self._clock()
            try:
                _apply(self._race_clock, command, now, arguments)
            except RaceClockError as error:
                logger.warning("Refused %s: %s", command, error)
                self._write_command(command, now, arguments, accepted=False)
                raise
            logger.info("Race command: %s %s", command, arguments or "")
            self._write_command(command, now, arguments, accepted=True)

    def _replay(self, commands: list[dict[str, Any]]) -> None:
        """Bring the race clock up to date with today's commands."""
        for record in commands:
            try:
                _apply(
                    self._race_clock,
                    record["command"],
                    record["time"],
                    record.get("arguments", {}),
                )
            except (RaceClockError, KeyError, TypeError) as error:
                logger.warning("Couldn't replay %s: %s", record.get("command"), error)
        logger.info("Resumed today's racing from %d commands", len(commands))

    def _write_command(
        self, command: str, now: float, arguments: dict[str, float], *, accepted: bool
    ) -> None:
        """Add a command to the journal.

        A failure is only logged, because the journal must never stop race control.
        """
        records = []
        if not self._session_written:
            records.append(
                {
                    "record": SESSION_RECORD,
                    "date": datetime.fromtimestamp(now).date().isoformat(),
                    "sequences": self._sequences_hash,
                }
            )
        records.append(
            {
                "record": COMMAND_RECORD,
                "time": now,
                "local_time": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
                "command": command,
                "arguments": arguments,
                "accepted": accepted,
            }
        )
        text = "".join(json.dumps(record) + "\n" for record in records)
        try:
            with self._journal_path.open("a", encoding="utf-8") as file:
                file.write(text)
                file.flush()
                # A command must survive a power cut, which is likely at a start box.
                os.fsync(file.fileno())
        except OSError as error:
            logger.error("Can't write the race journal: %s", error)
            return
        self._session_written = True


def _apply(
    race_clock: RaceClock, command: str, now: float, arguments: dict[str, float]
) -> None:
    """Carry out a named command on a race clock."""
    function = COMMANDS[command]
    if command == "clear_individual_recall":
        function(race_clock)
    else:
        function(race_clock, now, **arguments)
