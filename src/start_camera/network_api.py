#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: network_api.py
#  Description: The JSON network interface for a race control suite.
#  Date: 2026-11-09
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: None
#
#########################################################

"""The JSON network interface for a race control suite, at /api/v1/.

It's served on the phone page's HTTPS port, and offers what the Python interface does.
Every request but the OpenAPI description needs a token, sent as
"Authorization: Bearer <token>". The api_token allows everything; the api_read_token
only allows reading, for something like a scoreboard. Events are streamed as
Server-Sent Events.

This module only turns requests into calls on the camera system and replies into
JSON; the phone page's server carries them.
"""

#
# SYSTEM IMPORTS
#

import hmac
import json
import logging
import queue
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

#
# PROJECT IMPORTS
#
from start_camera.errors import ClockNotSynchronisedError, SequencesError
from start_camera.events import Event
from start_camera.media_server import MediaServerError
from start_camera.openapi import OPENAPI
from start_camera.race_clock import RaceClockError
from start_camera.recording import RecordingError

if TYPE_CHECKING:
    from start_camera.api import CameraSystem
    from start_camera.control_page import FailureLimiter

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

PREFIX = "/api/v1/"
MAX_EVENT_STREAMS = 8
KEEPALIVE_S = 15.0
STREAM_POLL_S = 1.0
SIMPLE_RACE_COMMANDS = (
    "start_now",
    "postpone",
    "resume",
    "general_recall",
    "individual_recall",
    "clear_individual_recall",
    "skip",
    "abandon",
)

#
# FUNCTIONS
#


class Access(Enum):
    """What a token allows."""

    FULL = "full"
    READ = "read"


class ApiError(Exception):
    """A request can't be carried out, with the reply to send."""

    def __init__(
        self, status: int, code: str, message: str, **extra: object
    ) -> None:
        """Hold the reply."""
        super().__init__(message)
        self.status = status
        self.code = code
        self.extra = extra

    def body(self) -> dict[str, object]:
        """Return the reply's JSON body."""
        return {"error": str(self), "code": self.code, **self.extra}


def to_json(value: object) -> object:
    """Return a value as JSON data: dataclasses, enums and mappings included."""
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return to_json(asdict(value))  # type: ignore[call-overload]
    if isinstance(value, Mapping):
        return {str(key): to_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [to_json(item) for item in value]
    return value


def event_json(event: Event) -> dict[str, object]:
    """Return an event as JSON data."""
    return {
        "number": event.number,
        "time": event.time,
        "kind": event.kind.value,
        "details": to_json(event.details),
    }


def _moment(value: object, name: str) -> datetime | float:
    """Return a time from a request: seconds since the epoch, or ISO 8601."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ApiError(400, "bad_request", f"{name} must be a number or a time")
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as error:
            raise ApiError(400, "bad_request", f"{name} isn't a time") from error
    return float(value)


def _optional_name(body: Mapping[str, object]) -> str | None:
    """Return a class name from a request, if there is one."""
    name = body.get("name")
    if name is not None and not isinstance(name, str):
        raise ApiError(400, "bad_request", "name must be text")
    return name


def _flag(body: Mapping[str, object], name: str) -> bool:
    """Return a true or false option from a request, false if it's missing."""
    value = body.get(name, False)
    if not isinstance(value, bool):
        raise ApiError(400, "bad_request", f"{name} must be true or false")
    return value


class NetworkApi:
    """Carries out network requests on a camera system."""

    def __init__(
        self,
        system: "CameraSystem",
        token: str | None,
        read_token: str | None,
        limiter: "FailureLimiter",
    ) -> None:
        """Hold the system and the tokens.

        Args:
            system: The camera system.
            token: The token that allows everything, or None for none.
            read_token: The token that only allows reading, or None for none.
            limiter: Locks out an address that keeps sending wrong tokens.
        """
        self._system = system
        self._tokens = [
            (token.encode(), Access.FULL) if token else None,
            (read_token.encode(), Access.READ) if read_token else None,
        ]
        self.limiter = limiter
        self._streams = threading.BoundedSemaphore(MAX_EVENT_STREAMS)
        self.stopping = threading.Event()

    def authorise(self, header: str | None, address: str) -> Access:
        """Return what a request's token allows.

        Raises:
            ApiError: If the token is missing or wrong, or the address is locked out.
        """
        locked = self.limiter.seconds_locked(address)
        if locked > 0:
            raise ApiError(
                429, "locked_out", f"Too many wrong tokens. Try again in {locked:.0f}s"
            )
        token = b""
        if header and header.startswith("Bearer "):
            token = header.removeprefix("Bearer ").strip().encode()
        access = None
        for entry in self._tokens:
            # Every token is compared, in constant time, so timing gives nothing away.
            if entry is not None and hmac.compare_digest(token, entry[0]):
                access = access or entry[1]
        if access is None:
            self.limiter.failed(address)
            logger.warning("Network interface: refused a request from %s", address)
            raise ApiError(401, "unauthorised", "A valid token is needed")
        self.limiter.succeeded(address)
        return access

    def get(self, path: str, query: Mapping[str, str]) -> dict[str, object]:
        """Answer a GET request for a path after the prefix.

        Raises:
            ApiError: If the path isn't known, or the request is wrong.
        """
        if path == "status":
            return self.status()
        if path == "sequences":
            return {"sequences": self._system.config.sequences.model_dump(mode="json")}
        if path == "events":
            since = _since(query.get("since"))
            return {"events": [event_json(e) for e in self._system.events_since(since)]}
        raise ApiError(404, "not_found", "Not found")

    def post(
        self, path: str, body: Mapping[str, object], access: Access
    ) -> dict[str, object]:
        """Carry out a POST request for a path after the prefix.

        Raises:
            ApiError: If it isn't allowed, isn't known, or is refused.
        """
        if access is not Access.FULL:
            raise ApiError(403, "read_only", "This token can only read")
        try:
            reply = self._post(path, body)
        except ClockNotSynchronisedError as error:
            raise ApiError(409, "clock_not_synchronised", str(error)) from error
        except RaceClockError as error:
            raise ApiError(409, "refused", str(error)) from error
        except SequencesError as error:
            raise ApiError(
                422, "invalid_sequences", str(error), problems=error.problems
            ) from error
        except RecordingError as error:
            raise ApiError(409, "recording", str(error)) from error
        except ValueError as error:
            raise ApiError(400, "bad_request", str(error)) from error
        except (MediaServerError, OSError) as error:
            raise ApiError(503, "stream", f"The stream can't start: {error}") from error
        return {**reply, "status": self.status()}

    def status(self) -> dict[str, object]:
        """Return the state of the racing and the system."""
        system = self._system
        status = system.status()
        latest = system.events_since(0)[-1:]
        return {
            **to_json(status),
            "clock": to_json(system.clock_status()),
            "viewers": system.viewer_addresses() if status.streaming else {},
            "last_event": latest[0].number if latest else 0,
        }

    def stream_events(
        self,
        since: int,
        open_stream: Callable[[], None],
        write: Callable[[bytes], None],
    ) -> None:
        """Send events as Server-Sent Events until the client goes or we stop.

        Args:
            since: Send the events after this number first.
            open_stream: Sends the reply's headers.
            write: Sends part of the stream.

        Raises:
            ApiError: If too many clients are already listening.
        """
        if not self._streams.acquire(blocking=False):
            raise ApiError(503, "busy", "Too many event streams are open")
        waiting: queue.Queue[Event] = queue.Queue()
        # Subscribing first means nothing is lost between the history and new events.
        unsubscribe = self._system.subscribe(waiting.put)
        try:
            open_stream()
            self._send_events(since, waiting, write)
        except OSError:
            pass
        finally:
            unsubscribe()
            self._streams.release()

    def _send_events(
        self, since: int, waiting: "queue.Queue[Event]", write: Callable[[bytes], None]
    ) -> None:
        """Send missed events, then each new one, with a keepalive when quiet."""
        last = since
        for event in self._system.events_since(since):
            write(_sse(event))
            last = event.number
        quiet = 0.0
        while not self.stopping.is_set():
            try:
                event = waiting.get(timeout=STREAM_POLL_S)
            except queue.Empty:
                quiet += STREAM_POLL_S
                if quiet >= KEEPALIVE_S:
                    write(b": keepalive\n\n")
                    quiet = 0.0
                continue
            if event.number > last:
                write(_sse(event))
                last = event.number
                quiet = 0.0

    def _post(self, path: str, body: Mapping[str, object]) -> dict[str, object]:
        """Route a POST request."""
        system = self._system
        section, _, name = path.partition("/")
        if section == "race" and name in SIMPLE_RACE_COMMANDS:
            getattr(system, name)()
        elif path == "race/shift":
            minutes = body.get("minutes")
            if isinstance(minutes, bool) or not isinstance(minutes, int | float):
                raise ApiError(400, "bad_request", "minutes must be a number")
            system.shift(minutes)
        elif path == "race/set_start":
            sent_at = body.get("sent_at")
            system.set_start(
                _moment(body.get("start"), "start"),
                _optional_name(body),
                sent_at=None if sent_at is None else _moment(sent_at, "sent_at"),
            )
        elif path == "sequences":
            sequences = body.get("sequences")
            if not isinstance(sequences, dict):
                raise ApiError(400, "bad_request", "sequences must be an object")
            warnings = system.set_sequences(
                sequences, force=_flag(body, "force"), save=_flag(body, "save")
            )
            return {"warnings": warnings}
        elif path in ("recording/start", "recording/stop"):
            getattr(system, f"{name}_recording")()
        elif path in ("stream/start", "stream/stop"):
            getattr(system, f"{name}_stream")()
        else:
            raise ApiError(404, "not_found", "Not found")
        return {}


def openapi_json() -> bytes:
    """Return the interface's OpenAPI description."""
    return json.dumps(OPENAPI).encode()


def _since(value: str | None) -> int:
    """Return an event number from a request, 0 if there isn't one."""
    if value is None or value == "":
        return 0
    try:
        return max(0, int(value))
    except ValueError as error:
        raise ApiError(400, "bad_request", "since must be a whole number") from error


def since_for_stream(query: Mapping[str, str], last_event_id: str | None) -> int:
    """Return where an event stream starts: after Last-Event-ID, or since."""
    return _since(last_event_id if last_event_id else query.get("since"))


def _sse(event: Event) -> bytes:
    """Return an event as a Server-Sent Event."""
    data = json.dumps(event_json(event), default=str)
    return f"id: {event.number}\nevent: {event.kind.value}\ndata: {data}\n\n".encode()

