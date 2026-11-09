#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: suite_day_example.py
#  Description: A race control suite running a day's racing on the camera system.
#  Date: 2026-11-09
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: start_camera
#
#########################################################

"""A race control suite running a day's racing on the camera system.

This is the worked example in docs/suite_guide.md. Two classes start five minutes
apart. Before the first warning signal, the race officer delays the starts by five
minutes. After the first start, they call a general recall, and the suite sets when
the recalled class starts again. It takes about half an hour in real time:

    python examples/suite_day_example.py config.json secrets.json

It replaces the day's sequences, so don't run it on a race day. Add --stream to
watch it on the video.
"""

#
# SYSTEM IMPORTS
#

import argparse
import logging
import math
import sys
import time
from collections.abc import Callable

#
# PROJECT IMPORTS
#
from start_camera.api import (
    CameraSystem,
    Event,
    EventKind,
    RaceClockError,
    SequenceConfig,
    SequencesConfig,
)

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger("suite")

MINUTE = 60.0
DAY = SequencesConfig(
    items=[
        SequenceConfig(name="Lasers", class_flag="L"),
        SequenceConfig(name="Toppers", class_flag="T", minutes_after_previous=5),
    ]
)
DELAY_MINUTES = 5
RECALL_AFTER_S = 20.0
RESTART_GAP_MINUTES = 10

#
# FUNCTIONS
#


def log_event(event: Event) -> None:
    """Log what the camera system reports, as a suite might check its signals."""
    clock = time.strftime("%H:%M:%S", time.localtime(event.time))
    details = event.details
    if event.kind in (EventKind.FLAG_UP, EventKind.FLAG_DOWN):
        logger.info(
            "#%d %s %s %s", event.number, clock, event.kind.value, details["flag"]
        )
    elif event.kind is EventKind.START:
        logger.info("#%d %s start %s", event.number, clock, details["name"])
    elif event.kind is EventKind.SCHEDULE:
        logger.info(
            "#%d %s %s from %s",
            event.number, clock, details["command"], details["source"],
        )  # fmt: skip
    else:
        logger.info("#%d %s %s", event.number, clock, event.kind.value)


def next_whole_minute(moment: float, minutes_ahead: float) -> float:
    """Return a whole minute at least some minutes after a moment."""
    return math.ceil((moment + minutes_ahead * MINUTE) / MINUTE) * MINUTE


def run_day(system: CameraSystem, wait_until: Callable[[float], None]) -> None:
    """Run the day's racing.

    Args:
        system: A started camera system.
        wait_until: Waits until a moment, in seconds since the epoch.
    """
    system.subscribe(log_event)

    # 1. Send the day's sequences, and check the clocks agree.
    for warning in system.set_sequences(DAY, force=True):
        logger.warning("Check the sequences: %s", warning)
    clock = system.clock_status()
    if not clock.synchronised:
        logger.error("This computer's clock isn't synchronised; start times will fail")

    # 2. Set the first start. The suite is in charge of timing, so it sends times.
    first = next_whole_minute(clock.time, 10)
    system.set_start(first, "Lasers", sent_at=time.time())
    logger.info("Lasers start at %s", time.strftime("%H:%M", time.localtime(first)))

    # 3. Before the warning signal, the race officer delays the starts.
    wait_until(first - 7 * MINUTE)
    system.shift(DELAY_MINUTES)
    first += DELAY_MINUTES * MINUTE

    # 4. Lasers start, then a general recall shortly after.
    wait_until(first + RECALL_AFTER_S)
    system.general_recall()

    # 5. The suite decides when Lasers start again, and tells the camera system.
    restart = next_whole_minute(first, RESTART_GAP_MINUTES)
    try:
        system.set_start(restart, "Lasers", sent_at=time.time())
    except RaceClockError as error:
        logger.error("The camera system refused the restart: %s", error)
        raise

    # 6. Toppers follow Lasers at their usual spacing. Wait for both starts.
    wait_until(restart + DAY.items[1].minutes_after_previous * MINUTE + 1)
    logger.info("Finished: %s", system.status().phase.value)


def starts_so_far(system: CameraSystem) -> list[str]:
    """Return each class's start, in order, from the recent events."""
    return [
        str(event.details["name"])
        for event in system.events_since()
        if event.kind is EventKind.START
    ]


def sleep_until(moment: float) -> None:
    """Wait in real time until a moment."""
    time.sleep(max(0.0, moment - time.time()))


def main() -> int:
    """Run the example."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config")
    parser.add_argument("secrets", nargs="?")
    parser.add_argument("--stream", action="store_true", help="Start the stream")
    arguments = parser.parse_args()
    with CameraSystem.from_files(arguments.config, arguments.secrets) as system:
        if arguments.stream:
            system.start_stream()
        run_day(system, sleep_until)
        time.sleep(0.5)  # Let the last events reach the log.
        logger.info("Starts: %s", ", ".join(starts_so_far(system)))
    return 0


#########################################################
# MAIN
#########################################################

if __name__ == "__main__":
    sys.exit(main())
