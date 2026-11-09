#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: api.py
#  Description: The stable Python interface to the camera system.
#  Date: 2026-11-09
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy
#
#########################################################

"""The stable Python interface to the camera system.

A race control suite can run the whole system inside its own program with
CameraSystem. Only what's listed in __all__ is promised to stay stable; the rest of
the package may change.

The preview and the stream draw with separate compositors, as each runs on its own
thread, but they use the same camera frames, overlays and race controls, so they match.
"""

#
# SYSTEM IMPORTS
#

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, Self

import numpy as np
from numpy.typing import NDArray
from pydantic import ValidationError

from start_camera import control_page

#
# PROJECT IMPORTS
#
from start_camera.clock_check import clock_is_synchronised
from start_camera.config import (
    Config,
    ConfigError,
    FlagAction,
    FlagEvent,
    GeneralRecallMode,
    PreparatoryFlag,
    Secrets,
    SequenceConfig,
    SequencesConfig,
    load_config,
    load_secrets,
    save_config,
)
from start_camera.control import RaceControl, SourcedControl
from start_camera.control_page import ControlPage, ControlPageError, FailureLimiter
from start_camera.discovery import primary_address
from start_camera.errors import ClockNotSynchronisedError, SequencesError
from start_camera.events import Event, EventHub, EventKind
from start_camera.ingest import Frame, FrameSource
from start_camera.media_server import MediaServerError
from start_camera.network_api import NetworkApi
from start_camera.overlays.countdown import RaceDisplay, RaceSummary
from start_camera.overlays.factory import build_compositor
from start_camera.pipeline import Streaming, addresses_for_viewers
from start_camera.race_clock import Phase, RaceClockError, ScheduledStart
from start_camera.recording import Recorder, RecordingError
from start_camera.sequence_model import check_sequences
from start_camera.watcher import SystemState, Watcher

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

SUITE_SOURCE = "suite"
MAX_CLOCK_DIFFERENCE_S = 0.5

__all__ = [
    "CameraSystem",
    "ClockNotSynchronisedError",
    "ClockStatus",
    "Config",
    "ConfigError",
    "Event",
    "EventKind",
    "MediaServerError",
    "Phase",
    "RaceClockError",
    "RecordingError",
    "FlagAction",
    "FlagEvent",
    "GeneralRecallMode",
    "PreparatoryFlag",
    "ScheduledStart",
    "Secrets",
    "SequenceConfig",
    "SequencesConfig",
    "SequencesError",
    "SystemStatus",
]

#
# FUNCTIONS
#


@dataclass(frozen=True)
class ClockStatus:
    """This computer's clock, for checking it against the race control suite's.

    last_difference is how far this clock was from the suite's when a command last said
    when it was sent, in seconds; positive means this clock was ahead.
    """

    time: float
    synchronised: bool | None
    last_difference: float | None


@dataclass(frozen=True)
class SystemStatus:
    """A snapshot of the whole system.

    Times are seconds since the epoch. display is what the video's countdown shows,
    which allows for the camera's delay. youtube is "off", "connecting", or "live"
    when video is reaching YouTube.
    """

    time: float
    phase: Phase
    display: str
    flags: tuple[str, ...]
    upcoming: ScheduledStart | None
    latest: ScheduledStart | None
    camera_live: bool
    streaming: bool
    recording: bool
    youtube: str = "off"


def _checked_sequences(sequences: SequencesConfig | dict[str, Any]) -> SequencesConfig:
    """Return valid sequences, or raise SequencesError with every problem."""
    # Checked as JSON, as in a configuration file. A SequencesConfig is checked again,
    # as a suite may have changed it since it was made.
    if isinstance(sequences, SequencesConfig):
        text = sequences.model_dump_json()
    else:
        text = json.dumps(sequences, default=str)
    try:
        checked = SequencesConfig.model_validate_json(text)
    except ValidationError as error:
        raise SequencesError(_field_problems(error)) from error
    errors = check_sequences(checked).errors
    if errors:
        raise SequencesError(errors)
    return checked


def _field_problems(error: ValidationError) -> list[str]:
    """Describe validation errors by field, never quoting the offending value."""
    problems = []
    for item in error.errors(include_input=False, include_url=False):
        field = ".".join(str(part) for part in item["loc"]) or "(top level)"
        problems.append(f"{field}: {item['msg']}")
    return problems


def _seconds(moment: datetime | float) -> float:
    """Return a moment in seconds since the epoch.

    Raises:
        ValueError: If a datetime has no time zone, which would leave it ambiguous.
    """
    if isinstance(moment, datetime):
        if moment.tzinfo is None:
            raise ValueError("A start time needs a time zone")
        return moment.timestamp()
    return float(moment)


class _SystemRecording:
    """Lets the phone page start and stop recording through the system."""

    def __init__(self, system: "CameraSystem") -> None:
        """Hold the system."""
        self._system = system

    @property
    def is_recording(self) -> bool:
        """Return whether recording is on."""
        return self._system.is_recording

    def start(self) -> None:
        """Start recording."""
        self._system.start_recording()

    def stop(self) -> None:
        """Stop recording."""
        self._system.stop_recording()


class Source(Protocol):
    """Provides the camera's frames."""

    def start(self) -> None:
        """Start reading the camera."""
        ...

    def stop(self) -> None:
        """Stop reading the camera."""
        ...

    def latest(self) -> Frame:
        """Return the newest frame."""
        ...


class CameraSystem:
    """The camera, its overlays, the race controls, the stream and recording.

    Prepare it with the configuration, then start it, or use it in a with block.
    Starting reads the camera; the stream is started separately.
    """

    def __init__(
        self,
        config: Config,
        secrets: Secrets,
        *,
        make_source: Callable[[Config, Secrets], Source] | None = None,
        make_streaming: Callable[..., Streaming] | None = None,
        clock: Callable[[], float] = time.time,
        clock_synchronised: Callable[[], bool | None] = clock_is_synchronised,
        config_path: Path | str | None = None,
    ) -> None:
        """Prepare everything, without starting it.

        Args:
            config: The configuration.
            secrets: The credentials.
            make_source: Makes what reads the camera. For testing.
            make_streaming: Makes what publishes the stream. For testing.
            clock: Provides the current time. For testing.
            clock_synchronised: Says whether NTP has set the clock. For testing.
            config_path: Where the configuration came from, to save new sequences.
        """
        self.config = config
        self.control = RaceControl(
            config.sequences, Path(config.control.journal_path), clock
        )
        self.secrets = secrets
        self._source = (make_source or FrameSource)(config, secrets)
        self._make_streaming = make_streaming or Streaming
        self._clock = clock
        self._compositor = build_compositor(config, clock, self.control)
        self._display = RaceDisplay(config, self.control)
        self._streaming: Streaming | None = None
        self._stream_lock = threading.Lock()
        self._clock_synchronised = clock_synchronised
        self._last_difference: float | None = None
        self._config_path = Path(config_path) if config_path else None
        self._events = EventHub()
        self._page: ControlPage | None = None
        self._watcher = Watcher(self.control, self._events, clock, self._system_state)

    @classmethod
    def from_files(
        cls, config_path: Path | str, secrets_path: Path | str | None = None
    ) -> "CameraSystem":
        """Return a system for a configuration file and an optional secrets file.

        Raises:
            ConfigError: If a file can't be read or isn't valid.
        """
        secrets = load_secrets(Path(secrets_path) if secrets_path else None)
        return cls(load_config(Path(config_path)), secrets, config_path=config_path)

    def __enter__(self) -> Self:
        """Start reading the camera."""
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop everything."""
        self.close()

    def status(self) -> SystemStatus:
        """Return a snapshot of the racing, the camera, the stream and recording."""
        now = self._clock()
        race = self.control.state(now)
        return SystemStatus(
            time=now,
            phase=race.phase,
            display=self.summary().display,
            flags=race.flags,
            upcoming=race.upcoming,
            latest=race.latest,
            camera_live=self.camera_is_live(),
            streaming=self.is_streaming,
            recording=self.is_recording,
            youtube=self.youtube_state(),
        )

    def youtube_state(self) -> str:
        """Return "off", "connecting", or "live" when video is reaching YouTube."""
        streaming = self._streaming
        return streaming.youtube_state if streaming is not None else "off"

    def start_now(self) -> None:
        """Bring the next start forward so its warning signal is now.

        Raises:
            RaceClockError: If this can't be done now. The same goes for the other
                race commands.
        """
        self._suite.start_now()

    def set_start(
        self,
        start: datetime | float,
        name: str | None = None,
        *,
        sent_at: datetime | float | None = None,
    ) -> None:
        """Set exactly when a start is, with the starts after it following.

        This is how a race control suite in charge of timing tells the camera when its
        start is, so a network delay can't move it. This computer's clock must be
        synchronised using NTP, or the countdown wouldn't match the suite's signals.

        Args:
            start: The start time: a datetime with a time zone, or seconds since the
                epoch.
            name: The class whose start it is. Defaults to the next start.
            sent_at: When the suite sent this, by its own clock, to check the two
                clocks agree.

        Raises:
            ClockNotSynchronisedError: If this computer's clock may be wrong.
            RaceClockError: If the start has already passed, the class doesn't exist
                or has started, or racing is postponed or abandoned.
            ValueError: If a datetime has no time zone.
        """
        when = _seconds(start)
        self._require_synchronised_clock()
        if sent_at is not None:
            self._compare_clocks(_seconds(sent_at))
        self._suite.set_start(when, name)

    def set_sequences(
        self,
        sequences: SequencesConfig | dict[str, Any],
        *,
        force: bool = False,
        save: bool = False,
    ) -> list[str]:
        """Use the day's sequences from the suite, straight away.

        They're checked as in the GUI's sequence editor. The video's countdown and
        flags change from the next frame, without restarting the stream. Changing the
        sequences starts the day's racing afresh, so sending the ones already in use
        changes nothing.

        Args:
            sequences: The sequences, or the same as a dictionary, as in the
                "sequences" section of the configuration file.
            force: Change them even while a sequence is under way or postponed.
            save: Also save them to the configuration file.

        Returns:
            Anything worth checking, such as flags that overlap. The sequences are
            used anyway.

        Raises:
            SequencesError: If the sequences are invalid, with each problem.
            RaceClockError: If a sequence is under way, and force isn't set.
            ConfigError: If saving was asked for and isn't possible. The sequences
                are still used.
        """
        checked = _checked_sequences(sequences)
        warnings = check_sequences(checked).warnings
        for warning in warnings:
            logger.warning("Sequences from the suite: %s", warning)
        if self.control.replace_sequences(checked, SUITE_SOURCE, force=force):
            self._use_sequences(checked)
        if save:
            self._save_config()
        return warnings

    def _use_sequences(self, sequences: SequencesConfig) -> None:
        """Draw the overlays for new sequences."""
        self.config = self.config.model_copy(update={"sequences": sequences})
        self._compositor = build_compositor(self.config, self._clock, self.control)
        self._display = RaceDisplay(self.config, self.control)
        with self._stream_lock:
            if self._streaming is not None:
                self._streaming.use_sequences(sequences)

    def _save_config(self) -> None:
        """Save the configuration to the file it came from."""
        if self._config_path is None:
            raise ConfigError("There's no configuration file to save the sequences to")
        save_config(self.config, self._config_path)
        logger.info("Saved the sequences to %s", self._config_path)

    def clock_status(self) -> ClockStatus:
        """Return this computer's clock, and whether it's synchronised."""
        return ClockStatus(
            self._clock(), self._clock_synchronised(), self._last_difference
        )

    def shift(self, minutes: float) -> None:
        """Move every start that hasn't happened by a number of minutes."""
        self._suite.shift(minutes)

    def postpone(self) -> None:
        """Hold the starts that haven't happened, and fly the postponement flag."""
        self._suite.postpone()

    def resume(self) -> None:
        """End a postponement. The next warning signal is one minute later."""
        self._suite.resume()

    def general_recall(self) -> None:
        """Recall the most recent class, which then starts again."""
        self._suite.general_recall()

    def individual_recall(self) -> None:
        """Fly the individual recall flag for the most recent class."""
        self._suite.individual_recall()

    def clear_individual_recall(self) -> None:
        """Take down the individual recall flag."""
        self._suite.clear_individual_recall()

    def skip(self) -> None:
        """Remove the next start, moving on to the one after."""
        self._suite.skip()

    def abandon(self) -> None:
        """Stop all starts and fly the abandonment flag."""
        self._suite.abandon()

    @property
    def _suite(self) -> SourcedControl:
        """Return the race controls, recording commands as coming from the suite."""
        return self.control.with_source(SUITE_SOURCE)

    def _require_synchronised_clock(self) -> None:
        """Refuse a start time if this computer's clock may be wrong."""
        if not self.config.control.require_ntp:
            return
        if self._clock_synchronised() is not True:
            raise ClockNotSynchronisedError(
                "This computer's clock isn't synchronised with NTP, so start times "
                "can't be accepted"
            )

    def _compare_clocks(self, sent_at: float) -> None:
        """Warn if this computer's clock and the suite's disagree."""
        difference = self._clock() - sent_at
        self._last_difference = difference
        if abs(difference) > MAX_CLOCK_DIFFERENCE_S:
            logger.warning(
                "This clock is %.2f seconds %s the race control suite's, so the "
                "countdown may not match its signals",
                abs(difference), "ahead of" if difference > 0 else "behind",
            )  # fmt: skip

    @property
    def is_streaming(self) -> bool:
        """Return whether the stream is running."""
        return self._streaming is not None

    def start(self) -> None:
        """Start the camera, events, and the phone page and network interface if set."""
        self._source.start()
        self._watcher.start()
        if self._page is None:
            self._page = self._start_page()

    def _start_page(self) -> ControlPage | None:
        """Start the phone page and the network interface, if either is set up.

        Neither is needed for the video, so a problem is only logged.
        """
        secrets = self.secrets
        password = secrets.control_password
        api = self._network_api()
        if password is None:
            logger.info("No control_password in the secrets, so the phone page is off")
        if password is None and api is None:
            return None
        address = primary_address() or "localhost"
        try:
            page = ControlPage(
                self.config,
                self.control,
                password.get_secret_value() if password else None,
                address=address,
                certificate_directory=control_page.CERTIFICATE_DIRECTORY,
                recorder=_SystemRecording(self),
                api=api,
            )
        except (ControlPageError, OSError) as error:
            logger.error("The phone page and network interface are off: %s", error)
            return None
        if password is not None:
            logger.info("Race officer's phone page: https://%s:%d/", address, page.port)
        if api is not None:
            logger.info("Network interface: https://%s:%d/api/v1/", address, page.port)
        return page

    def _network_api(self) -> NetworkApi | None:
        """Return the network interface, if there's a token for it."""
        token, read = self.secrets.api_token, self.secrets.api_read_token
        if token is None and read is None:
            logger.info("No api_token in the secrets, so the network interface is off")
            return None
        if token and read and token.get_secret_value() == read.get_secret_value():
            logger.warning("api_read_token is the same as api_token, so it can control")
        return NetworkApi(
            self,
            token.get_secret_value() if token else None,
            read.get_secret_value() if read else None,
            FailureLimiter(time.time),
        )

    def subscribe(self, listener: Callable[[Event], None]) -> Callable[[], None]:
        """Call a function with every event from now on.

        Each listener is called on a thread of its own, so a slow one can't hold up the
        stream or the racing. One that falls far behind misses events, which shows as a
        gap in their numbers.

        Returns:
            A function that stops the calls.
        """
        return self._events.subscribe(listener)

    def events_since(self, number: int = 0) -> list[Event]:
        """Return the recent events after a number, oldest first.

        This is how a listener can fetch events it missed. Only the most recent
        thousand are kept.
        """
        return self._events.since(number)

    def _system_state(self) -> SystemState:
        """Return whether the stream and recording are running, and the camera live."""
        return SystemState(self.is_streaming, self.is_recording, self.camera_is_live())

    def preview(self) -> NDArray[np.uint8]:
        """Return the newest frame with its overlays."""
        return self._compositor.render(self._source.latest())

    def camera_frame(self) -> Frame:
        """Return the camera's newest frame, without overlays."""
        return self._source.latest()

    def camera_is_live(self) -> bool:
        """Return whether the camera's picture is arriving."""
        return self._source.latest().live

    def summary(self) -> RaceSummary:
        """Return the state of the racing, matching what the video shows."""
        return self._display.summary(self._clock())

    def viewer_addresses(self) -> dict[str, str]:
        """Return where viewers can watch."""
        return addresses_for_viewers(self.config)

    @property
    def is_recording(self) -> bool:
        """Return whether the stream is being recorded."""
        recorder = self._recorder()
        return recorder is not None and recorder.is_recording

    def recording_directory(self) -> str:
        """Return where recordings go."""
        recorder = self._recorder()
        return str(recorder.directory) if recorder is not None else ""

    def start_recording(self) -> None:
        """Start recording.

        Raises:
            RecordingError: If the stream isn't running, or recording can't start.
        """
        recorder = self._recorder()
        if recorder is None:
            raise RecordingError("Start the stream before recording")
        recorder.start()

    def stop_recording(self) -> None:
        """Stop recording."""
        recorder = self._recorder()
        if recorder is not None:
            recorder.stop()

    def _recorder(self) -> Recorder | None:
        """Return what controls recording, while streaming."""
        streaming = self._streaming
        return getattr(streaming, "recorder", None) if streaming else None

    def start_stream(self) -> None:
        """Start the stream. This can take a while, so don't call it from a GUI thread.

        Raises:
            MediaServerError: If the server program can't be fetched.
            OSError: If a port can't be opened.
        """
        with self._stream_lock:
            if self._streaming is not None:
                return
            streaming = self._make_streaming(
                self.config, self.secrets, self.control, self._source
            )
            streaming.start()
            self._streaming = streaming

    def stop_stream(self) -> None:
        """Stop the stream, leaving the preview running."""
        with self._stream_lock:
            if self._streaming is not None:
                self._streaming.stop()
                self._streaming = None

    def close(self) -> None:
        """Stop everything."""
        if self._page is not None:
            self._page.stop()
            self._page = None
        self.stop_stream()
        self._watcher.stop()
        # The last check publishes the stream stopping, before the camera stops.
        self._watcher.check()
        self._source.stop()
        self._events.close()
