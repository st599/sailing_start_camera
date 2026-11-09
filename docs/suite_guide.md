# Guide for race control suites

This guide is for someone writing a race control suite: the software that runs a race day's
signals, sound, sign-on and scoring. It takes you from nothing to a working start, then
through a day's racing with a delay and a general recall.

The camera system works on its own, as now, or controlled by your suite. When the suite is in
charge, it decides the timing: when each class starts, delays and recalls. The camera system
shows that on the video, records and streams it, and reports back what it showed.

Reference details are in the [Python interface](python_api.md) and the
[network interface](network_api.md).

## 1. Choose an interface

| | Python interface | Network interface |
|---|---|---|
| **Use it when** | The suite is written in Python, on the same computer as the camera system | The suite is in another language, or on another computer |
| **How** | `from start_camera.api import CameraSystem` | JSON over HTTPS, at `/api/v1/` |
| **Who runs the camera system** | The suite, inside its own program | `startcam` or `startcam-gui`, as a separate program |
| **Events** | Called back on a thread | Server-Sent Events |
| **Security** | None needed | A token, sent with every request |

Both offer the same commands, and both journal commands as coming from `suite`. If you're
unsure, use the network interface: the camera system then keeps running if the suite stops.

## 2. Set up the camera system

Follow the [setup guide](setup_guide.md) until a test sequence works from the GUI. Then:

1. **Check the clock.** Start times are refused unless this computer's clock is synchronised
   using NTP, so the countdown matches the suite's signals. Run `timedatectl`, and look for
   `System clock synchronized: yes`. Point the suite's computer at the same NTP server, such as
   the club's router or `pool.ntp.org`.
2. **For the network interface, make a token** and add it to `secrets.json` as `api_token`:

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   Something that should only watch, such as a scoreboard, gets a different token as
   `api_read_token`. It can read the status and events, but can't change anything. Keep tokens
   out of logs and code repositories, as you would a password.
3. **For the network interface, copy the certificate** from
   `~/.config/start_camera/control_page.crt` on the camera computer to the suite's computer, so
   the suite can check it's talking to the right machine.
4. **Start the camera system:** `startcam --config config.json --secrets secrets.json`. Its
   log shows `Network interface: https://<address>:8443/api/v1/`.

## 3. A first start

These steps run one start. Each is shown for the network interface with `curl`, and for the
Python interface. With `curl`, set these first:

```bash
TOKEN=...   # the api_token
CAMERA=https://startcam.local:8443/api/v1
AUTH=(--cacert control_page.crt -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")
```

**Check the camera system is ready.**

```bash
curl "${AUTH[@]}" $CAMERA/status
```

```python
system = CameraSystem.from_files("config.json", "secrets.json")
system.start()
print(system.status(), system.clock_status())
```

Check `camera_live` is true and `clock.synchronised` is true.

**Start the stream**, if it isn't running. `startcam` starts it by itself.

```bash
curl "${AUTH[@]}" -X POST $CAMERA/stream/start
```

```python
system.start_stream()
```

**Send the day's sequences.** These are the classes in start order, laid out as the
`sequences` section of the [configuration file](configuration.md). Each class gets the standard
five-minute sequence unless it says otherwise.

```bash
curl "${AUTH[@]}" -d '{"sequences": {"items": [
    {"name": "Lasers", "class_flag": "L"},
    {"name": "Toppers", "class_flag": "T", "minutes_after_previous": 5}]}}' \
  $CAMERA/sequences
```

```python
system.set_sequences({"items": [
    {"name": "Lasers", "class_flag": "L"},
    {"name": "Toppers", "class_flag": "T", "minutes_after_previous": 5},
]})
```

Problems come back with each one explained (`422` and `problems`, or `SequencesError`).
Warnings, such as overlapping flags, come back as `warnings`; the sequences are used anyway.

**Set the start time.** Send the start signal's time, not the warning signal's. Include
`sent_at`, your own clock's time, so the camera system can warn if the clocks disagree.

```bash
curl "${AUTH[@]}" -d '{"start": "2026-11-14T10:30:00+00:00", "name": "Lasers",
    "sent_at": "'$(date -u +%Y-%m-%dT%H:%M:%S.%N+00:00)'"}' $CAMERA/race/set_start
```

```python
system.set_start(datetime(2026, 11, 14, 10, 30, tzinfo=UTC), "Lasers", sent_at=time.time())
```

The video now counts down to 10:30:00, the class flag goes up at 10:25:00, and Toppers follow
at 10:35:00.

**Listen for events**, to confirm each signal was shown:

```bash
curl -N "${AUTH[@]}" $CAMERA/events/stream
```

```python
system.subscribe(lambda event: print(event.number, event.kind, event.details))
```

At 10:25:00 you'll get `flag_up` with `L`, at 10:26:00 `flag_up` with `P`, and at 10:30:00
`flag_down` with `L` and `start` with `Lasers`. That's a working start.

## 4. Commands

Every command acts on the next start or the most recent one, as the race officer's buttons do,
and is described in [Race control](race_control.md). Network paths are after `/api/v1`.

| What | Python | Network |
|---|---|---|
| Set exactly when a start is | `set_start(start, name, sent_at=...)` | `POST /race/set_start` |
| Move every start that hasn't happened | `shift(minutes)` | `POST /race/shift` |
| Make the next warning signal now | `start_now()` | `POST /race/start_now` |
| Postpone, and end the postponement | `postpone()`, `resume()` | `POST /race/postpone`, `/race/resume` |
| General recall of the most recent class | `general_recall()` | `POST /race/general_recall` |
| Individual recall: fly and take down flag X | `individual_recall()`, `clear_individual_recall()` | `POST /race/individual_recall`, `/race/clear_individual_recall` |
| Move on to the next start | `skip()` | `POST /race/skip` |
| Abandon | `abandon()` | `POST /race/abandon` |
| Send the day's sequences | `set_sequences(sequences, force=, save=)` | `POST /sequences` |
| Read the day's sequences | `config.sequences` | `GET /sequences` |
| Start and stop the stream | `start_stream()`, `stop_stream()` | `POST /stream/start`, `/stream/stop` |
| Start and stop recording | `start_recording()`, `stop_recording()` | `POST /recording/start`, `/recording/stop` |
| Status | `status()`, `clock_status()`, `youtube_state()` | `GET /status` |
| Recent events | `events_since(number)` | `GET /events?since=` |
| Events as they happen | `subscribe(listener)` | `GET /events/stream` |
| The picture, such as for the suite's own screen | `preview()` with overlays, `camera_frame()` without | Watch the stream at `viewers` in the status |
| Other information | `camera_is_live()`, `summary()`, `viewer_addresses()`, `recording_directory()` | In `GET /status` |

A command that doesn't make sense at the time, such as resuming without a postponement, is
refused with the reason, and changes nothing. The suite should show the reason to its user.

**Times.** Since the suite is in charge of timing, prefer `set_start` to `start_now`: a time
can't be moved by a network delay, but the moment a command arrives can. Use `shift` for a
delay of whole minutes, and `set_start` for anything else, such as after a recall.

**Sequences.** Send them before the first start. They're refused while a sequence is under way
or racing is postponed, as changing them starts the day afresh; `force` overrides that.
Sending the same sequences again changes nothing, so it's safe to send them whenever the suite
reconnects. `save` keeps them in the configuration file for a restart.

## 5. Events

Each event has a `number`, its `time`, a `kind` and `details`. Flag, start and phase events have
the moment the change happened by the race clock, to within a millisecond.

| Kind | When | Use it to |
|---|---|---|
| `flag_up`, `flag_down` | A flag goes up or comes down on the video | Confirm your own signals were shown |
| `start` | A class starts | Start the race's timing in scoring |
| `phase` | Racing moves between idle, waiting, in sequence, finished, postponed and abandoned | Show the state |
| `schedule` | A race command was carried out, from any `source`: `suite`, `gui`, `phone` or `keys` | Notice changes made at the start box, and update your schedule from `upcoming` |
| `sequences` | New sequences were set | Notice someone else changing them |
| `recording_started`, `recording_stopped` | Recording starts or stops | Note where to find the video for protests |
| `camera_lost`, `camera_regained` | The camera's picture stops or comes back | Warn the race officer |
| `stream_started`, `stream_stopped` | The stream starts or stops | Show whether viewers can watch |

**Missed events.** The numbers go up by one each time. After a gap, fetch the missing ones with
`events_since` or `GET /events?since=`; the network event stream does this by itself when it
reconnects. If the numbers start again from 1, the camera system has restarted (see
[below](#7-if-something-goes-wrong)).

## 6. A day's racing

`examples/suite_day_example.py` runs this day through the Python interface; a test plays it on a
simulated clock, so it stays correct. Two classes start five minutes apart. The race officer
delays the starts by five minutes, then calls a general recall on the first class.

1. **Morning:** the suite sends the day's sequences, checks `clock_status()`, and sets the
   first start, Lasers at a whole minute at least ten minutes ahead, say 10:30. Toppers follow
   at 10:35. Events: `sequences`, then `schedule` for `set_start`.
2. **A delay**, before the warning signal: the wind has dropped, so the race officer delays
   five minutes. The suite sends `shift(5)`. Lasers now start at 10:35 and Toppers at 10:40.
   Event: `schedule` for `shift`, with the new `upcoming` start.
3. **Lasers' sequence:** `flag_up L` at 10:30, `flag_up P` at 10:31, `flag_down P` at 10:34,
   then `flag_down L` and `start Lasers` at 10:35. Toppers' class flag goes up as Lasers start.
4. **A general recall** at 10:35:20: the suite sends `general_recall()`. The first substitute
   flies, and the camera system reschedules Lasers by its own rule. Events: `schedule` for
   `general_recall`, `flag_up First Substitute`.
5. **The suite sets the restart**, since it's in charge of timing: Lasers at 10:45, with
   `set_start(…, "Lasers")`. Toppers follow at 10:50, at their usual spacing.
6. **The restart and Toppers:** `start Lasers` at 10:45 and `start Toppers` at 10:50, each with
   its flags before it.

Meanwhile, if the race officer presses **Postpone** on the phone, the suite gets `schedule` with
`source` `phone` and `phase` `postponed`, and should update its own schedule to match. To end
the postponement, the race officer or the suite resumes (`resume()`), then the suite sends a new
`set_start`; start times are refused while racing is postponed.

## 7. If something goes wrong

**What stays standalone.** The phone page, the terminal keys and the GUI keep working alongside
the suite, and the last command wins. If the suite stops, disconnects or crashes, nothing
changes: the camera system carries on with the schedule it has, flying flags and starting
classes on time. It never postpones by itself. Race officers can take over from the phone or the
GUI at any time.

**When the suite reconnects:**

1. Read `GET /status` or `status()`, and compare `upcoming` with the suite's own schedule.
2. Fetch the events it missed, from its last event number, and look at each `schedule` event.
3. Send the sequences again; if they haven't changed, nothing happens.

**If the camera system restarts**, such as after a power cut, event numbers start again from 1.
The day's commands are replayed from the race journal, so the schedule survives, as long as the
sequences are the same. Sequences sent by the suite are lost unless they were sent with `save`,
so send them again, then check the status, and set the start times again if they differ.

**Common problems:**

| Problem | What to do |
|---|---|
| `401 unauthorised` | Check the token, and that it's in the camera system's secrets |
| `403 read_only` | That's the read-only token; use `api_token` to change things |
| `409 clock_not_synchronised` | The camera computer's clock isn't synchronised. Check `timedatectl` and its network connection to the NTP server |
| A warning that the clocks differ | The suite's computer and the camera computer disagree by more than half a second. Point both at the same NTP server |
| `409 refused` | The command doesn't make sense now; the `error` says why |
| `422 invalid_sequences` | Fix each item in `problems` |
| A certificate error | Use the camera computer's `control_page.crt`, and its name or the address in the certificate |
| No events arrive | Check the stream is read without buffering, such as with `curl -N`. A comment arrives every 15 seconds to show the connection is alive |
