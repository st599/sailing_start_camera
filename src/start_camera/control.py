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
from contextlib import AbstractContextManager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

#
# PROJECT IMPORTS
#
from start_camera.config import SequencesConfig
from start_camera.race_clock import Phase, RaceClock, RaceClockError, RaceState

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
    "set_start": RaceClock.set_start,
}
LOCAL_SOURCE = "local"
UNDER_WAY = (Phase.IN_SEQUENCE, Phase.POSTPONED)

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


class RaceObserver(Protocol):
    """Something told about every change to the racing, while it's being made."""

    def before_change(self, now: float) -> None:
        """Note the moment just before a change, with the racing as it was."""

    def after_change(
        self, command: str, source: str, arguments: dict[str, object], now: float
    ) -> None:
        """Note an accepted change, with the racing as it now is."""


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
        # Reentrant, so an observer can read the state while a change is made.
        self._lock = threading.RLock()
        self._observers: list[RaceObserver] = []
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

    def observe(self, observer: RaceObserver) -> None:
        """Tell an observer about every change from now on."""
        with self._lock:
            self._observers.append(observer)

    def locked(self) -> AbstractContextManager[object]:
        """Return a context that holds off changes, for reading several states."""
        return self._lock

    def replace_sequences(
        self,
        config: SequencesConfig,
        source: str = LOCAL_SOURCE,
        *,
        force: bool = False,
    ) -> bool:
        """Use new sequences from now on, starting the day's racing afresh.

        The race journal starts a new session, so only commands from now on are
        replayed after a restart.

        Args:
            config: The new sequences.
            source: Where the change came from, for the journal.
            force: Replace them even while a sequence is under way.

        Returns:
            False if they're the same as the sequences already in use, which are
            then kept as they are.

        Raises:
            RaceClockError: If a sequence is under way and force isn't set.
        """
        with self._lock:
            sequences_hash = _sequences_hash(config)
            if sequences_hash == self._sequences_hash:
                return False
            now = self._clock()
            phase = self._race_clock.state(now).phase
            if phase in UNDER_WAY and not force:
                raise RaceClockError(
                    f"The racing is {phase.value}, so the sequences can't change now"
                )
            self._notify_before(now)
            self._race_clock = RaceClock(config, now)
            self._sequences_hash = sequences_hash
            self._session_written = False
            logger.info("New sequences from %s: %d starts", source, len(config.items))
            self._write_records([self._session_record(now, source)])
            self._notify_after("set_sequences", source, {}, now)
            return True

    def with_source(self, source: str) -> "SourcedControl":
        """Return these controls, recording every command as coming from a source."""
        return SourcedControl(self, source)

    def start_now(self, source: str = LOCAL_SOURCE) -> None:
        """Bring the next start forward so its warning signal is now."""
        self._run("start_now", source)

    def shift(self, minutes: float, source: str = LOCAL_SOURCE) -> None:
        """Move every start that hasn't happened by a number of minutes."""
        self._run("shift", source, minutes=minutes)

    def set_start(
        self, start: float, name: str | None = None, source: str = LOCAL_SOURCE
    ) -> None:
        """Set when a start is, in seconds since the epoch, by default the next one."""
        self._run("set_start", source, start=start, name=name)

    def postpone(self, source: str = LOCAL_SOURCE) -> None:
        """Hold all starts that haven't happened, and fly the postponement flag."""
        self._run("postpone", source)

    def resume(self, source: str = LOCAL_SOURCE) -> None:
        """End a postponement."""
        self._run("resume", source)

    def general_recall(self, source: str = LOCAL_SOURCE) -> None:
        """Recall the most recent class, which then starts again."""
        self._run("general_recall", source)

    def individual_recall(self, source: str = LOCAL_SOURCE) -> None:
        """Fly the individual recall flag for the most recent class."""
        self._run("individual_recall", source)

    def clear_individual_recall(self, source: str = LOCAL_SOURCE) -> None:
        """Take down the individual recall flag."""
        self._run("clear_individual_recall", source)

    def skip(self, source: str = LOCAL_SOURCE) -> None:
        """Remove the next start, moving on to the one after."""
        self._run("skip", source)

    def abandon(self, source: str = LOCAL_SOURCE) -> None:
        """Stop all starts and fly the abandonment flag."""
        self._run("abandon", source)

    def _run(self, command: str, source: str, **arguments: object) -> None:
        """Carry out a command and record it, whether or not it was accepted.

        Raises:
            RaceClockError: If the command can't be carried out in the current state.
        """
        with self._lock:
            now = self._clock()
            self._notify_before(now)
            try:
                _apply(self._race_clock, command, now, arguments)
            except RaceClockError as error:
                logger.warning("Refused %s from %s: %s", command, source, error)
                self._write_command(command, source, now, arguments, accepted=False)
                raise
            logger.info(
                "Race command: %s %s(from %s)",
                command, f"{arguments} " if arguments else "", source,
            )  # fmt: skip
            self._write_command(command, source, now, arguments, accepted=True)
            self._notify_after(command, source, arguments, now)

    def _notify_before(self, now: float) -> None:
        """Tell the observers a change is about to be made."""
        for observer in self._observers:
            observer.before_change(now)

    def _notify_after(
        self, command: str, source: str, arguments: dict[str, object], now: float
    ) -> None:
        """Tell the observers about an accepted change."""
        for observer in self._observers:
            observer.after_change(command, source, arguments, now)

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
        self,
        command: str,
        source: str,
        now: float,
        arguments: dict[str, object],
        *,
        accepted: bool,
    ) -> None:
        """Add a command to the journal.

        A failure is only logged, because the journal must never stop race control.
        """
        records = []
        if not self._session_written:
            records.append(self._session_record(now))
        records.append(
            {
                "record": COMMAND_RECORD,
                "time": now,
                "local_time": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
                "command": command,
                "source": source,
                "arguments": arguments,
                "accepted": accepted,
            }
        )
        self._write_records(records)

    def _session_record(
        self, now: float, source: str | None = None
    ) -> dict[str, object]:
        """Return the record that begins a session of the journal."""
        record: dict[str, object] = {
            "record": SESSION_RECORD,
            "date": datetime.fromtimestamp(now).date().isoformat(),
            "sequences": self._sequences_hash,
        }
        if source is not None:
            record["source"] = source
        return record

    def _write_records(self, records: list[dict[str, object]]) -> None:
        """Add records to the journal.

        A failure is only logged, because the journal must never stop race control.
        """
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
    race_clock: RaceClock, command: str, now: float, arguments: dict[str, object]
) -> None:
    """Carry out a named command on a race clock."""
    function = COMMANDS[command]
    if command == "clear_individual_recall":
        function(race_clock)
    else:
        function(race_clock, now, **arguments)


class SourcedControl:
    """Race controls that record every command as coming from one source."""

    def __init__(self, control: RaceControl, source: str) -> None:
        """Hold the controls and the source."""
        self._control = control
        self.source = source

    def state(self, now: float) -> RaceState:
        """Return the state of the start sequences at a moment."""
        return self._control.state(now)

    def start_now(self) -> None:
        """Bring the next start forward so its warning signal is now."""
        self._control.start_now(self.source)

    def shift(self, minutes: float) -> None:
        """Move every start that hasn't happened by a number of minutes."""
        self._control.shift(minutes, self.source)

    def set_start(self, start: float, name: str | None = None) -> None:
        """Set when a start is, in seconds since the epoch."""
        self._control.set_start(start, name, self.source)

    def postpone(self) -> None:
        """Hold all starts that haven't happened."""
        self._control.postpone(self.source)

    def resume(self) -> None:
        """End a postponement."""
        self._control.resume(self.source)

    def general_recall(self) -> None:
        """Recall the most recent class."""
        self._control.general_recall(self.source)

    def individual_recall(self) -> None:
        """Fly the individual recall flag."""
        self._control.individual_recall(self.source)

    def clear_individual_recall(self) -> None:
        """Take down the individual recall flag."""
        self._control.clear_individual_recall(self.source)

    def skip(self) -> None:
        """Remove the next start."""
        self._control.skip(self.source)

    def abandon(self) -> None:
        """Stop all starts."""
        self._control.abandon(self.source)
