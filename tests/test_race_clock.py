#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_race_clock.py
#  Description: Tests for the race clock.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: pytest
#
#########################################################

"""Tests for the race clock. Times are in seconds, from an arbitrary moment."""

#
# SYSTEM IMPORTS
#

from datetime import date, datetime, time
from pathlib import Path

import pytest

#
# PROJECT IMPORTS
#
from start_camera.config import (
    MAX_STARTS,
    ConfigError,
    FlagAction,
    FlagEvent,
    GeneralRecallMode,
    PreparatoryFlag,
    SequenceConfig,
    SequencesConfig,
    load_config,
)
from start_camera.race_clock import (
    Phase,
    RaceClock,
    RaceClockError,
    first_start_time,
    warning_lead_s,
)

#
#  GLOBAL VARIABLES
#

T0 = datetime(2026, 11, 1, 12, 0).timestamp()
MINUTE = 60.0

#
# FUNCTIONS
#


def classes(*names: str, **options: object) -> list[SequenceConfig]:
    """Return sequences named after their class flags."""
    return [SequenceConfig(name=name, class_flag=name, **options) for name in names]


def manual(*names: str, **options: object) -> RaceClock:
    """Return a clock waiting for the race officer, with the first warning at T0."""
    clock = RaceClock(SequencesConfig(items=classes(*names), **options), T0)
    clock.start_now(T0)
    return clock


def at(minutes_after_warning: float) -> float:
    """Return a time relative to the first warning at T0."""
    return T0 + minutes_after_warning * MINUTE


FIRST_START = at(5)
SECOND_START = at(10)
THIRD_START = at(15)


@pytest.mark.parametrize(
    ("minutes", "flags"),
    [
        (-0.01, ()),
        (0, ("A",)),
        (0.99, ("A",)),
        (1, ("A", "P")),
        (3.99, ("A", "P")),
        (4, ("A",)),
        (4.99, ("A",)),
        (5, ()),
        (6, ()),
    ],
)
def test_default_sequence_flags_at_each_boundary(
    minutes: float, flags: tuple[str, ...]
) -> None:
    assert manual("A").state(at(minutes)).flags == flags


def test_flag_order_and_preparatory_choice() -> None:
    items = [
        SequenceConfig(name="A", class_flag="A", preparatory_flag=PreparatoryFlag.U)
    ]
    clock = RaceClock(SequencesConfig(items=items), T0)
    clock.start_now(T0)
    assert clock.state(at(2)).flags == ("A", "U")


def test_custom_events_with_a_named_flag() -> None:
    events = [
        FlagEvent(minutes_before_start=3, flag="class", action=FlagAction.UP),
        FlagEvent(minutes_before_start=2, flag="Orange", action=FlagAction.UP),
        FlagEvent(minutes_before_start=1, flag="Orange", action=FlagAction.DOWN),
        FlagEvent(minutes_before_start=0, flag="class", action=FlagAction.DOWN),
    ]
    items = [SequenceConfig(name="Lasers", class_flag="Laser", events=events)]
    clock = RaceClock(SequencesConfig(items=items), T0)
    clock.start_now(T0)
    assert clock.state(T0).upcoming.start == T0 + 3 * MINUTE
    assert clock.state(T0 + 1.5 * MINUTE).flags == ("Laser", "Orange")
    assert clock.state(T0 + 2.5 * MINUTE).flags == ("Laser",)


def test_events_may_be_listed_in_any_order() -> None:
    events = list(reversed(SequenceConfig(name="A", class_flag="A").events))
    items = [SequenceConfig(name="A", class_flag="A", events=events)]
    clock = RaceClock(SequencesConfig(items=items), T0)
    clock.start_now(T0)
    assert clock.state(at(2)).flags == ("A", "P")
    assert clock.state(at(4.5)).flags == ("A",)


def test_rolling_start_raises_next_warning_as_the_previous_flag_drops() -> None:
    clock = manual("A", "B")
    assert clock.state(at(4.99)).flags == ("A",)
    assert clock.state(FIRST_START).flags == ("B",)
    assert clock.state(FIRST_START).upcoming.name == "B"
    assert clock.state(FIRST_START).latest.name == "A"


def test_larger_interval_leaves_a_gap() -> None:
    items = [
        SequenceConfig(name="A", class_flag="A"),
        SequenceConfig(name="B", class_flag="B", minutes_after_previous=8),
    ]
    clock = RaceClock(SequencesConfig(items=items), T0)
    clock.start_now(T0)
    assert clock.state(FIRST_START).flags == ()
    assert clock.state(at(7.99)).flags == ()
    assert clock.state(at(8)).flags == ("B",)
    assert clock.state(at(13)).latest.name == "B"


def test_smaller_interval_overlaps_without_repeating_flags() -> None:
    items = [
        SequenceConfig(name="A", class_flag="A"),
        SequenceConfig(name="B", class_flag="B", minutes_after_previous=1),
    ]
    clock = RaceClock(SequencesConfig(items=items), T0)
    clock.start_now(T0)
    assert clock.state(at(2.5)).flags == ("A", "P", "B")


def test_ten_starts_run_in_turn() -> None:
    names = [str(number) for number in range(MAX_STARTS)]
    clock = manual(*names)
    for index, name in enumerate(names):
        start = at(5 + 5 * index)
        assert clock.state(start - 1).upcoming.name == name
        assert clock.state(start).latest.name == name
    assert clock.state(at(5 + 5 * MAX_STARTS)).phase is Phase.FINISHED


def test_more_than_ten_starts_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    items = ",".join(
        f'{{"name": "{index}", "class_flag": "{index}"}}'
        for index in range(MAX_STARTS + 1)
    )
    path.write_text(
        f'{{"version": 1, "sequences": {{"items": [{items}]}}}}', encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="sequences.items"):
        load_config(path)


def test_lead_comes_from_the_earliest_event() -> None:
    assert warning_lead_s(classes("A")[0]) == 5 * MINUTE


@pytest.mark.parametrize(
    ("minutes", "phase"),
    [
        (-1, Phase.WAITING),
        (0, Phase.IN_SEQUENCE),
        (4.99, Phase.IN_SEQUENCE),
        (5, Phase.FINISHED),
    ],
)
def test_phases(minutes: float, phase: Phase) -> None:
    assert manual("A").state(at(minutes)).phase is phase


def test_no_sequences_is_idle() -> None:
    clock = RaceClock(SequencesConfig(), T0)
    state = clock.state(T0)
    assert state.phase is Phase.IDLE
    assert state.flags == ()
    assert state.upcoming is None
    assert state.latest is None


def test_manual_sequence_waits_for_the_race_officer() -> None:
    clock = RaceClock(SequencesConfig(items=classes("A", "B")), T0)
    state = clock.state(T0 + 3600)
    assert state.phase is Phase.IDLE
    assert state.flags == ()
    assert state.upcoming is None


def test_start_now_starts_the_warning_at_once() -> None:
    clock = RaceClock(SequencesConfig(items=classes("A", "B")), T0)
    clock.start_now(at(7))
    assert clock.state(at(7)).flags == ("A",)
    assert clock.state(at(7)).upcoming.start == at(12)


def test_scheduled_first_start_is_local_time_today() -> None:
    config = SequencesConfig(first_start=time(13, 0), items=classes("A", "B"))
    clock = RaceClock(config, T0)
    first = datetime(2026, 11, 1, 13, 0).timestamp()
    assert clock.state(T0).phase is Phase.WAITING
    assert clock.state(T0).upcoming.start == first
    assert clock.state(first - 5 * MINUTE).flags == ("A",)
    assert clock.state(first).upcoming.name == "B"


def test_first_start_time_uses_the_date_given() -> None:
    config = SequencesConfig(first_start=time(9, 30))
    expected = datetime(2026, 7, 4, 9, 30).timestamp()
    assert first_start_time(config, date(2026, 7, 4)) == expected
    assert first_start_time(SequencesConfig(), date(2026, 7, 4)) is None


def test_scheduled_time_already_past_counts_as_having_happened() -> None:
    config = SequencesConfig(first_start=time(11, 0), items=classes("A", "B"))
    state = RaceClock(config, T0).state(T0)
    assert state.phase is Phase.FINISHED
    assert state.latest.name == "B"


def test_start_now_brings_a_scheduled_start_forward() -> None:
    config = SequencesConfig(first_start=time(13, 0), items=classes("A", "B"))
    clock = RaceClock(config, T0)
    clock.start_now(T0)
    assert clock.state(T0).upcoming.start == T0 + 5 * MINUTE


def test_start_now_is_refused_while_a_sequence_is_running() -> None:
    clock = manual("A")
    with pytest.raises(RaceClockError, match="running"):
        clock.start_now(at(1))


def test_start_now_with_nothing_left_is_refused() -> None:
    with pytest.raises(RaceClockError, match="no more starts"):
        manual("A").start_now(at(6))


def test_shift_moves_this_start_and_all_later_ones() -> None:
    clock = manual("A", "B", "C")
    clock.shift(at(1), 3)
    assert clock.state(at(1)).upcoming.start == at(8)
    assert clock.state(at(8)).upcoming.start == at(13)
    assert clock.state(at(13)).upcoming.start == at(18)


def test_shift_changes_the_flags_straight_away() -> None:
    clock = manual("A")
    assert clock.state(at(4.5)).flags == ("A",)
    clock.shift(at(4.5), 2)
    assert clock.state(at(4.5)).flags == ("A", "P")


def test_shift_leaves_started_classes_alone() -> None:
    clock = manual("A", "B")
    clock.shift(at(6), 2)
    assert clock.state(at(6)).latest.start == FIRST_START
    assert clock.state(at(6)).upcoming.start == SECOND_START + 2 * MINUTE


def test_shift_can_bring_a_start_forward() -> None:
    clock = manual("A")
    clock.shift(T0, -2)
    assert clock.state(T0).upcoming.start == FIRST_START - 2 * MINUTE


def test_shift_into_the_past_is_refused() -> None:
    clock = manual("A")
    with pytest.raises(RaceClockError, match="past"):
        clock.shift(at(4), -1)
    assert clock.state(at(4)).upcoming.start == FIRST_START


def test_shift_with_no_start_set_is_refused() -> None:
    clock = RaceClock(SequencesConfig(items=classes("A")), T0)
    with pytest.raises(RaceClockError, match="hasn't been set"):
        clock.shift(T0, 1)


def test_postpone_flies_ap_and_holds_the_flags() -> None:
    clock = manual("A", "B")
    clock.postpone(at(2))
    state = clock.state(at(2))
    assert state.phase is Phase.POSTPONED
    assert state.flags == ("AP",)
    assert state.upcoming is None


def test_time_passing_doesnt_start_a_postponed_class() -> None:
    clock = manual("A")
    clock.postpone(at(2))
    state = clock.state(at(60))
    assert state.flags == ("AP",)
    assert state.phase is Phase.POSTPONED
    assert state.latest is None


def test_long_postponement_can_still_be_resumed() -> None:
    clock = manual("A", "B")
    clock.postpone(at(2))
    clock.resume(at(600))
    state = clock.state(at(600))
    assert state.phase is Phase.WAITING
    assert state.upcoming.name == "A"
    assert state.upcoming.start == at(606)


def test_time_passing_doesnt_start_an_abandoned_class() -> None:
    clock = manual("A", "B")
    clock.abandon(at(2))
    assert clock.state(at(60)).latest is None


def test_abandoning_keeps_the_class_that_had_started() -> None:
    clock = manual("A", "B")
    clock.abandon(at(6))
    assert clock.state(at(60)).latest.name == "A"


def test_resume_gives_the_warning_a_minute_later_and_keeps_spacing() -> None:
    clock = manual("A", "B")
    clock.postpone(at(2))
    clock.resume(at(30))
    assert clock.state(at(30)).flags == ()
    assert clock.state(at(30.99)).flags == ()
    assert clock.state(at(31)).flags == ("A",)
    assert clock.state(at(30)).upcoming.start == at(36)
    assert clock.state(at(36)).upcoming.start == at(41)


def test_postponing_leaves_a_started_class_counting() -> None:
    clock = manual("A", "B")
    clock.postpone(at(6))
    assert clock.state(at(6)).latest.name == "A"
    clock.resume(at(7))
    assert clock.state(at(7)).upcoming.name == "B"


@pytest.mark.parametrize(
    "command",
    [
        lambda clock, now: clock.shift(now, 1),
        lambda clock, now: clock.postpone(now),
        lambda clock, now: clock.general_recall(now),
        lambda clock, now: clock.individual_recall(now),
        lambda clock, now: clock.skip(now),
        lambda clock, now: clock.start_now(now),
    ],
)
def test_commands_are_refused_during_a_postponement(command: object) -> None:
    clock = manual("A", "B")
    clock.postpone(at(6))
    with pytest.raises(RaceClockError, match="postponed"):
        command(clock, at(6))


def test_resume_without_a_postponement_is_refused() -> None:
    with pytest.raises(RaceClockError, match="isn't postponed"):
        manual("A").resume(T0)


def test_postpone_with_nothing_left_is_refused() -> None:
    with pytest.raises(RaceClockError, match="no more starts"):
        manual("A").postpone(at(6))


def test_general_recall_delays_following_classes_by_default() -> None:
    clock = manual("A", "B", "C")
    clock.general_recall(at(5.5))
    state = clock.state(at(5.5))
    assert state.flags == ("First Substitute",)
    assert state.upcoming.name == "A"
    # The flag flies for 1 minute, and the warning is 1 minute after that.
    assert state.upcoming.start == at(12.5)
    assert clock.state(at(6.49)).flags == ("First Substitute",)
    assert clock.state(at(6.5)).flags == ()
    assert clock.state(at(7.49)).flags == ()
    assert clock.state(at(7.5)).flags == ("A",)
    assert clock.state(at(12.5)).upcoming.name == "B"
    assert clock.state(at(12.5)).upcoming.start == at(17.5)
    assert clock.state(at(17.5)).upcoming.start == at(22.5)


def test_recall_flag_time_is_configurable() -> None:
    clock = manual("A", recall_flag_minutes=3)
    clock.general_recall(at(5.5))
    assert clock.state(at(8.49)).flags == ("First Substitute",)
    assert clock.state(at(8.5)).flags == ()
    assert clock.state(at(9.5)).flags == ("A",)


def test_general_recall_can_send_the_class_to_the_end_of_the_queue() -> None:
    clock = manual("A", "B", "C", general_recall=GeneralRecallMode.END_OF_QUEUE)
    clock.general_recall(at(5.5))
    state = clock.state(at(5.5))
    assert state.flags == ("B", "First Substitute")
    assert state.upcoming.name == "B"
    assert state.upcoming.start == SECOND_START
    assert clock.state(SECOND_START).upcoming.name == "C"
    assert clock.state(THIRD_START).upcoming.name == "A"
    assert clock.state(THIRD_START).upcoming.start == at(20)
    assert clock.state(at(20)).latest.name == "A"
    assert clock.state(at(20)).phase is Phase.FINISHED


def test_end_of_queue_leaves_enough_time_when_nothing_else_is_left() -> None:
    clock = manual("A", general_recall=GeneralRecallMode.END_OF_QUEUE)
    clock.general_recall(at(5.5))
    assert clock.state(at(5.5)).upcoming.start == at(12.5)


def test_end_of_queue_waits_for_a_late_last_class() -> None:
    clock = manual("A", "B", general_recall=GeneralRecallMode.END_OF_QUEUE)
    clock.general_recall(at(9.9))
    assert clock.state(at(9.9)).upcoming.name == "B"
    assert clock.state(at(10)).upcoming.name == "A"
    assert clock.state(at(10)).upcoming.start == pytest.approx(at(16.9))


def test_repeated_general_recalls() -> None:
    clock = manual("A", "B")
    clock.general_recall(at(5.5))
    clock.general_recall(at(13))
    assert clock.state(at(13)).upcoming.name == "A"
    assert clock.state(at(13)).upcoming.start == at(20)
    assert clock.state(at(20)).upcoming.start == at(25)


def test_general_recall_before_any_start_is_refused() -> None:
    with pytest.raises(RaceClockError, match="No class has started"):
        manual("A").general_recall(at(1))


def test_general_recall_replaces_an_individual_recall() -> None:
    clock = manual("A", "B")
    clock.individual_recall(FIRST_START)
    clock.general_recall(FIRST_START)
    assert "X" not in clock.state(FIRST_START).flags


def test_individual_recall_ends_four_minutes_after_the_start() -> None:
    clock = manual("A")
    clock.individual_recall(FIRST_START)
    assert clock.state(FIRST_START).flags == ("X",)
    assert clock.state(at(8.99)).flags == ("X",)
    assert clock.state(at(9)).flags == ()


def test_individual_recall_ends_a_minute_before_the_next_start() -> None:
    items = [
        SequenceConfig(name="A", class_flag="A"),
        SequenceConfig(name="B", class_flag="B", minutes_after_previous=3),
    ]
    clock = RaceClock(SequencesConfig(items=items), T0)
    clock.start_now(T0)
    clock.individual_recall(FIRST_START)
    assert "X" in clock.state(at(6.99)).flags
    assert "X" not in clock.state(at(7)).flags


def test_individual_recall_can_be_cleared() -> None:
    clock = manual("A")
    clock.individual_recall(FIRST_START)
    clock.clear_individual_recall()
    assert clock.state(FIRST_START).flags == ()


def test_individual_recall_before_any_start_is_refused() -> None:
    with pytest.raises(RaceClockError, match="No class has started"):
        manual("A").individual_recall(at(1))


def test_skip_removes_the_next_start_and_keeps_later_times() -> None:
    clock = manual("A", "B", "C")
    clock.skip(at(1))
    assert clock.state(at(1)).upcoming.name == "B"
    assert clock.state(at(1)).upcoming.start == SECOND_START
    assert clock.state(at(1)).flags == ()


def test_skip_with_nothing_left_is_refused() -> None:
    with pytest.raises(RaceClockError, match="no more starts"):
        manual("A").skip(at(6))


def test_abandon_flies_n_and_stops_everything() -> None:
    clock = manual("A", "B")
    clock.abandon(at(2))
    state = clock.state(at(2))
    assert state.phase is Phase.ABANDONED
    assert state.flags == ("N",)
    assert state.upcoming is None


def test_abandon_clears_a_postponement_and_recalls() -> None:
    clock = manual("A", "B")
    clock.individual_recall(FIRST_START)
    clock.abandon(FIRST_START)
    assert clock.state(FIRST_START).flags == ("N",)
    clock = manual("A", "B")
    clock.postpone(at(1))
    clock.abandon(at(1))
    assert clock.state(at(1)).phase is Phase.ABANDONED
    assert clock.state(at(1)).flags == ("N",)


def test_second_abandon_is_refused() -> None:
    clock = manual("A")
    clock.abandon(T0)
    with pytest.raises(RaceClockError, match="already abandoned"):
        clock.abandon(T0)


def test_start_now_after_abandoning_runs_every_sequence_again() -> None:
    clock = manual("A", "B")
    clock.abandon(at(7))
    clock.start_now(at(20))
    state = clock.state(at(20))
    assert state.phase is Phase.IN_SEQUENCE
    assert state.flags == ("A",)
    assert state.upcoming.start == at(25)
    assert state.latest is None
    assert clock.state(at(25)).upcoming.name == "B"


@pytest.mark.parametrize(
    "command",
    [
        lambda clock, now: clock.shift(now, 1),
        lambda clock, now: clock.postpone(now),
        lambda clock, now: clock.skip(now),
    ],
)
def test_commands_are_refused_after_abandoning(command: object) -> None:
    clock = manual("A", "B")
    clock.abandon(T0)
    with pytest.raises(RaceClockError, match="abandoned"):
        command(clock, T0)


def test_clock_moving_backwards_gives_the_state_for_that_time() -> None:
    clock = manual("A")
    assert clock.state(at(6)).phase is Phase.FINISHED
    assert clock.state(at(2)).phase is Phase.IN_SEQUENCE
    assert clock.state(at(2)).flags == ("A", "P")
    assert clock.state(at(-10)).phase is Phase.WAITING


def test_clock_jumping_forwards_counts_starts_as_having_happened() -> None:
    state = manual("A", "B").state(at(1000))
    assert state.phase is Phase.FINISHED
    assert state.latest.name == "B"


def test_state_is_the_same_each_time_it_is_asked() -> None:
    clock = manual("A", "B")
    assert clock.state(at(2)) == clock.state(at(2))
