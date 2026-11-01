# Race control

This describes how start sequences behave. Check it against the current Racing Rules of
Sailing and your sailing instructions, which take priority. The defaults came from secondary
sources and from the club's answers, not from the current rulebook's text.

The overlay is an aid, and not an official signal.

## Sequences

Up to 10 starts can run in order, each with its own name, class flag, preparatory flag and
flag timings. Flags are named freely, so any class flag can be used.

The default sequence, in minutes before the start, is:

| Minutes | Signal |
|---|---|
| 5 | Class flag up (warning) |
| 4 | Preparatory flag up |
| 1 | Preparatory flag down |
| 0 | Class flag down (start) |

Each start's time is `minutes_after_previous` after the one before. With the default of 5,
each class's warning goes up as the previous class's flag drops, with no gap. Use a larger
value for a gap. Smaller values overlap, and both sets of flags show together.

## Starting the first sequence

If `first_start` is set, the sequence runs from that local time today. Otherwise it waits
for the race officer to start it, and the first warning signal goes up at that moment.

## Countdown display

The top right corner shows the class name and a time, on the first line that applies:

| Situation | Shows |
|---|---|
| Abandoned | `ABANDONED` |
| Postponed | `POSTPONED` |
| A class's sequence is running | `Lasers -04:59`, counting down to `00:00` at the start |
| Within `count_up_minutes` of the last start | `Lasers +12:34` in the count-up colour |
| A later start is scheduled | `Lasers 13:00:00`, the time of the start |
| Waiting for the race officer | `READY` |
| None of these | Nothing |

With rolling starts, the next class's countdown appears as soon as its sequence begins, so
there's no count-up between them. The count-up then runs after the last start.

The display is `+00:00` for the first second after a start.

## Commands

| Command | Effect |
|---|---|
| Start now | Brings the next start forward so its warning signal is now. Later starts move with it. |
| Shift | Moves all starts that haven't happened by the amount given. Flags follow straight away. |
| Postpone | Flies AP. Starts that haven't happened are held, and their flags come down. |
| Resume | AP comes down. The next warning signal is 1 minute later, and later starts keep their spacing. |
| General recall | Flies the First Substitute for `recall_flag_minutes`, and restarts the most recent class. |
| Individual recall | Flies X until it's cleared, 4 minutes after the start, or 1 minute before the next start, whichever comes first. |
| Skip | Removes the next start. Other starts stay at the same times. |
| Abandon | Flies N and stops all starts. Start now then runs every sequence again from the first. |

### General recall

The recalled class's new warning signal is 1 minute after the First Substitute comes down.
`general_recall` decides what happens to the other classes:

- **`delay_following`:** classes that haven't started are delayed, and follow the recalled
  class at their normal spacing. Their flags come down until then.
- **`end_of_queue`:** other classes carry on at their times, and the recalled class goes to
  the end of the queue. It starts at its normal spacing after the last class, or later if
  it needs time for its own sequence.

## The race journal

Every command, including any that was refused, is added to `control.journal_path` with its
time, for later review. Each line is a JSON record.

If the application restarts, it replays the day's accepted commands, so the racing carries on
as if nothing had happened: a postponement stays in force, and a start that was delayed stays
delayed. Time carries on during the restart, so a start that passed in that time counts as
having happened.

The journal only applies on the day it was written and with the same `sequences`
configuration. If either has changed, racing starts afresh, and earlier records stay in the
file. Change `sequences` only between days, or expect any commands so far to be forgotten.

If the journal can't be written, an error is logged and the command still takes effect, but it
wouldn't survive a restart.

## If the clock changes

Everything is worked out from the current time and the stored start times. If the
computer's clock jumps, the display immediately shows what's correct for the new time, so
a start can appear to be sooner, later or already past. Synchronise the clock before racing.

If the time for a start has passed when the application starts, that start counts as having
happened.
