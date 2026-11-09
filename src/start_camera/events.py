#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: events.py
#  Description: Numbered events for a race control suite, delivered to listeners.
#  Date: 2026-11-09
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: None
#
#########################################################

"""Numbered events for a race control suite, delivered to listeners.

Publishing only adds an event to each listener's queue, and each listener has its own
thread, so a slow or broken listener can't hold up the stream, the race clock or the
other listeners. A listener that falls too far behind loses events, which it can see
from a gap in the numbers.
"""

#
# SYSTEM IMPORTS
#

import logging
import queue
import threading
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

HISTORY_LENGTH = 1000
QUEUE_LENGTH = 1000

#
# FUNCTIONS
#


class EventKind(StrEnum):
    """What an event is about."""

    FLAG_UP = "flag_up"
    FLAG_DOWN = "flag_down"
    START = "start"
    PHASE = "phase"
    SCHEDULE = "schedule"
    SEQUENCES = "sequences"
    RECORDING_STARTED = "recording_started"
    RECORDING_STOPPED = "recording_stopped"
    CAMERA_LOST = "camera_lost"
    CAMERA_REGAINED = "camera_regained"
    STREAM_STARTED = "stream_started"
    STREAM_STOPPED = "stream_stopped"


@dataclass(frozen=True)
class Event:
    """Something that happened.

    number increases by one for each event, starting from 1 when the system starts,
    so a gap means events were missed and a smaller number means a restart. time is
    when it happened, in seconds since the epoch.
    """

    number: int
    time: float
    kind: EventKind
    details: Mapping[str, object] = field(default_factory=dict)


Listener = Callable[[Event], None]


class _Delivery:
    """One listener's queue and the thread that calls it."""

    def __init__(self, listener: Listener) -> None:
        """Start the thread."""
        self.listener = listener
        self._queue: queue.Queue[Event | None] = queue.Queue(QUEUE_LENGTH)
        self._full = False
        self._thread = threading.Thread(
            target=self._deliver, name="event-listener", daemon=True
        )
        self._thread.start()

    def put(self, event: Event) -> None:
        """Queue an event, dropping it if the listener is too far behind."""
        try:
            self._queue.put_nowait(event)
            self._full = False
        except queue.Full:
            if not self._full:
                logger.warning("An event listener is too slow, so it's missing events")
            self._full = True

    def stop(self) -> None:
        """Stop the thread once the queued events are delivered."""
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

    def _deliver(self) -> None:
        """Call the listener with each event."""
        while (event := self._queue.get()) is not None:
            try:
                self.listener(event)
            except Exception:
                logger.exception("An event listener failed")


class EventHub:
    """Numbers events, keeps the recent ones, and delivers them to listeners."""

    def __init__(self) -> None:
        """Start with no events and no listeners."""
        self._lock = threading.Lock()
        self._number = 0
        self._history: deque[Event] = deque(maxlen=HISTORY_LENGTH)
        self._deliveries: list[_Delivery] = []

    def publish(self, kind: EventKind, time: float, **details: object) -> Event:
        """Number an event and queue it for every listener, without waiting."""
        with self._lock:
            self._number += 1
            event = Event(self._number, time, kind, MappingProxyType(details))
            self._history.append(event)
            for delivery in self._deliveries:
                delivery.put(event)
        logger.debug("Event %d: %s %s", event.number, kind.value, details)
        return event

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Call a listener with every event from now on, on a thread of its own.

        Returns:
            A function that stops the calls.
        """
        delivery = _Delivery(listener)
        with self._lock:
            self._deliveries.append(delivery)

        def unsubscribe() -> None:
            with self._lock:
                if delivery in self._deliveries:
                    self._deliveries.remove(delivery)
            delivery.stop()

        return unsubscribe

    def since(self, number: int) -> list[Event]:
        """Return the recent events after a number, oldest first."""
        with self._lock:
            return [event for event in self._history if event.number > number]

    def close(self) -> None:
        """Stop every listener's thread."""
        with self._lock:
            deliveries, self._deliveries = self._deliveries, []
        for delivery in deliveries:
            delivery.stop()
