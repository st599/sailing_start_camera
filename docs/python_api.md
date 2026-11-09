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
| `ClockNotSynchronisedError` | A start time was refused because this computer's clock isn't synchronised. A kind of `RaceClockError` |
| `RecordingError` | Recording can't start, such as when the stream isn't running |
| `MediaServerError` | The stream's server can't be fetched or started |
| `OSError` | A port the stream needs is in use |

## Still to come

Setting the day's sequences, events, and a network interface are planned in issues #56 to #58.
