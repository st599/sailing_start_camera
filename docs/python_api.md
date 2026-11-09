# Python interface

A race control suite can run the camera system inside its own Python program, using
`start_camera.api`. Only the names in `start_camera.api` are promised to stay stable; the rest
of the package may change between versions.

Install the package into the suite's Python environment as in the [setup guide](setup_guide.md),
then:

```python
from start_camera.api import CameraSystem, RaceClockError

with CameraSystem.from_files("config.json", "secrets.json") as system:
    system.start_stream()
    system.start_now()
    print(system.status().display)
```

`examples/python_api_example.py` is a complete example.

## `CameraSystem`

| Call | What it does |
|---|---|
| `CameraSystem(config, secrets)` | Prepare the system from a `Config` and `Secrets` |
| `CameraSystem.from_files(config_path, secrets_path=None)` | The same, from files |
| `start()`, or entering a `with` block | Start reading the camera |
| `close()`, or leaving the `with` block | Stop everything, including the stream and recording |
| `start_stream()`, `stop_stream()` | Start or stop the stream, the viewer pages, YouTube and the phone page. Starting can take a few seconds |
| `start_recording()`, `stop_recording()` | Record the stream to files, while it's running |
| `status()` | A `SystemStatus` snapshot |
| `set_start(start, name=None, sent_at=None)` | Set exactly when a start is; see [Start times from the suite](#start-times-from-the-suite) |
| `set_sequences(sequences, force=False, save=False)` | Use the day's sequences from the suite; see [The day's sequences](#the-days-sequences) |
| `subscribe(listener)` | Call a function with every [event](#events) from now on. Returns a function that stops the calls |
| `events_since(number=0)` | The recent events after a number, oldest first |
| `clock_status()` | A `ClockStatus`: this computer's clock, and whether it's synchronised |
| `start_now()` | Bring the next start forward, so its warning signal is now |
| `shift(minutes)` | Move every start that hasn't happened |
| `postpone()`, `resume()` | Postpone, and end the postponement |
| `general_recall()` | Recall the most recent class |
| `individual_recall()`, `clear_individual_recall()` | Fly and take down flag X |
| `skip()` | Move on to the next start |
| `abandon()` | Stop all starts |
| `preview()` | The newest frame with its overlays, as a BGR NumPy array |
| `camera_frame()` | The camera's newest frame, without overlays |
| `camera_is_live()` | Whether the camera's picture is arriving |
| `summary()` | The countdown and flags as the video shows them |
| `recording_directory()` | Where recordings go, while the stream is running |
| `viewer_addresses()` | Where viewers can watch |

The race commands behave as described in [Race control](race_control.md), and are safe to call
from any thread. A command that doesn't make sense at the time raises `RaceClockError`, with the
reason, and changes nothing. Every command goes in the race journal, as for the other controls.

The phone page and the keys keep working alongside the suite. The last command wins. The journal
records where each command came from: `suite`, `gui`, `phone` or `keys`.

## Start times from the suite

When the suite is in charge of timing, it should tell the camera when each start is, rather than
sending `start_now()` at the right moment. A start time can't be moved by a network delay.

```python
from datetime import datetime, timezone

start = datetime(2026, 11, 14, 10, 30, tzinfo=timezone.utc)
system.set_start(start, "Solo", sent_at=datetime.now(timezone.utc))
```

- `start` is the start signal, a `datetime` with a time zone or seconds since the epoch. The
  warning signal and flags follow from the class's sequence, and later starts keep their spacing.
- `name` picks the class; without it, the next start is set. A class with several starts gets
  its next one that hasn't happened.
- A start that has already passed, an unknown class, or a postponed or abandoned day is refused
  with `RaceClockError`. Use `resume()` first after a postponement.
- `sent_at` is when the suite sent the command, by its own clock. If the two clocks differ by
  more than half a second, a warning is logged, and `clock_status().last_difference` shows by
  how much.

The countdown is only right if both computers' clocks are. This computer's clock must be
synchronised using NTP: if it isn't, or that can't be checked, `set_start` raises
`ClockNotSynchronisedError` and changes nothing. The suite's computer should use the same NTP
server. On a computer without `timedatectl`, or for testing, `control.require_ntp` can be set to
`false` in the [configuration](configuration.md), at the risk of a wrong countdown.

## The day's sequences

The suite can send the day's classes, flags and times from its own schedule, instead of a
template being picked in the GUI:

```python
from start_camera.api import SequenceConfig, SequencesConfig

day = SequencesConfig(
    items=[
        SequenceConfig(name="Lasers", class_flag="L"),
        SequenceConfig(name="Toppers", class_flag="T", minutes_after_previous=10),
    ]
)
warnings = system.set_sequences(day)
```

- The sequences can also be a dictionary, laid out as the `sequences` section of the
  [configuration file](configuration.md). `SequenceConfig`, `FlagEvent`, `FlagAction`,
  `PreparatoryFlag` and `GeneralRecallMode` are there for building them in Python.
- They're checked as in the GUI's sequence editor. If anything makes them unusable, such as a
  missing name or a flag coming down without having gone up, `SequencesError` is raised and the
  old sequences stay. Its `problems` list says what's wrong, one item for each problem.
- Anything that's only worth checking, such as two classes' flags overlapping, is logged and
  returned as a list of messages. The sequences are used anyway.
- The video's countdown and flags change straight away, without restarting the stream.
- New sequences start the day's racing afresh, with nothing scheduled unless they have a
  `first_start`. While a sequence is under way or the racing is postponed, they're refused with
  `RaceClockError`, unless `force=True` is passed. Sending the sequences already in use changes
  nothing, so it's safe to send them again.
- `save=True` also saves them to the configuration file, which needs the system to have been
  made with `CameraSystem.from_files`. Without saving, a restart goes back to the file's
  sequences, so the suite should send its sequences again after a restart.

## Events

Events tell the suite what's happening as it happens, so it can confirm its signals were
shown, and notice changes made at the start box.

```python
from start_camera.api import Event, EventKind

def on_event(event: Event) -> None:
    if event.kind is EventKind.FLAG_UP:
        print(event.number, event.time, event.details["flag"])

stop = system.subscribe(on_event)
```

Each `Event` has:

| Field | Meaning |
|---|---|
| `number` | Goes up by one for each event, from 1 when the system starts. A gap means events were missed; a smaller number means the camera system restarted |
| `time` | When it happened, in seconds since the epoch |
| `kind` | An `EventKind`, below |
| `details` | More about it, as below; it can't be changed |

| `EventKind` | `details` |
|---|---|
| `FLAG_UP`, `FLAG_DOWN` | `flag`, such as `"P"` or a class flag |
| `START` | The class that started: `name`, `class_flag` and `start` |
| `PHASE` | The new `phase`, such as `"in sequence"` or `"postponed"` |
| `SCHEDULE` | A race command: its `command`, `arguments` and `source`, and the `upcoming` start afterwards (`name`, `class_flag` and `start`), or `None` |
| `SEQUENCES` | New sequences were set: `command` and `source` |
| `RECORDING_STARTED`, `RECORDING_STOPPED` | Nothing |
| `CAMERA_LOST`, `CAMERA_REGAINED` | Nothing |
| `STREAM_STARTED`, `STREAM_STOPPED` | Nothing |

- Flag, start and phase events have the moment the change happened by the race clock, to
  within a millisecond, rather than when it was noticed. They're noticed within about a
  twentieth of a second.
- `source` says where a command came from: `suite`, `gui`, `phone` or `keys`. A command's
  `SCHEDULE` event comes before the flag and phase events it causes.
- Recording, camera and stream events are noticed within about a twentieth of a second, and
  that's their time.
- Each listener is called on a thread of its own, in order. A slow listener can't hold up the
  stream, the racing or the other listeners, but if it falls a thousand events behind it
  misses some, which shows as a gap in the numbers. A listener that raises an exception is
  logged and keeps getting events.
- `events_since(number)` returns the last thousand events after a number, so a listener can
  fetch any it missed.

## `ClockStatus`

| Field | Meaning |
|---|---|
| `time` | This computer's clock, in seconds since the epoch |
| `synchronised` | `True` if NTP has set the clock, `False` if not, `None` if it can't be checked |
| `last_difference` | How far this clock was ahead of the suite's at the last `sent_at`, in seconds, or `None` |

## `SystemStatus`

| Field | Meaning |
|---|---|
| `time` | When the snapshot was taken, in seconds since the epoch |
| `phase` | A `Phase`, such as `Phase.IN_SEQUENCE` |
| `display` | The countdown as the video shows it, such as `Solo -04:59` |
| `flags` | The flags flying, such as `("O", "P")` |
| `upcoming`, `latest` | The next and the most recent start, as `ScheduledStart` with `name`, `class_flag` and `start` in seconds since the epoch, or `None` |
| `camera_live` | Whether the camera's picture is arriving |
| `streaming`, `recording` | Whether the stream and recording are running |

## Errors

| Error | When |
|---|---|
| `ConfigError` | A configuration or secrets file can't be read or isn't valid |
| `RaceClockError` | A race command can't be carried out now |
| `SequencesError` | Sequences sent by the suite can't be used. Its `problems` list says why. A kind of `ValueError` |
| `ClockNotSynchronisedError` | A start time was refused because this computer's clock isn't synchronised. A kind of `RaceClockError` |
| `RecordingError` | Recording can't start, such as when the stream isn't running |
| `MediaServerError` | The stream's server can't be fetched or started |
| `OSError` | A port the stream needs is in use |

## Still to come

A network interface is planned in issue #58.
