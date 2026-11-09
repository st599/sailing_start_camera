# Network interface

To get started, see the [guide for race control suites](suite_guide.md).

A race control suite written in any language can control the camera system over the network.
The interface offers what the [Python interface](python_api.md) does, as JSON over HTTPS, and
streams events as Server-Sent Events.

It runs on the phone page's port, 8443 by default (`control.page_port`), at `/api/v1/`. It
runs whenever the command-line application or the GUI does, whether or not it's streaming, so
the suite can start the stream itself.

## Turning it on

Add a token to `secrets.json`, or set it in the environment (see
[Configuration](configuration.md)):

```json
{ "api_token": "a-long-random-token" }
```

Make a token with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

- **`api_token`** allows everything.
- **`api_read_token`** only allows reading: status, sequences and events. It's for something
  that should only watch, such as a scoreboard. Use a different token from `api_token`.

Tokens must be at least 24 characters. With no token, the interface is off and the system
stays standalone, as before. The phone page doesn't need to be on. Its password and the tokens
are separate.

## Connecting

Send the token with every request:

```
Authorization: Bearer a-long-random-token
```

A missing or wrong token gets `401`. Five wrong tokens from one address lock it out for a
minute, with `429`. Tokens are never logged.

The connection uses the same self-signed certificate as the phone page. For the suite to check
it, copy `~/.config/start_camera/control_page.crt` from the camera computer to the suite's
computer, and trust it for that connection only. The certificate names the computer's name,
`<name>.local`, `localhost` and its address when the certificate was made. The example client's
`--certificate` option shows how.

The description at `/api/v1/openapi.json` is in OpenAPI 3.1 format, and doesn't need a token.

## Requests

| Request | What it does |
|---|---|
| `GET /api/v1/status` | The racing, the camera, the stream, recording, YouTube and the clock |
| `GET /api/v1/sequences` | The day's sequences, laid out as in the configuration file |
| `GET /api/v1/events?since=N` | The recent events after number `N` |
| `GET /api/v1/events/stream` | Events as they happen; see [Events](#events) |
| `POST /api/v1/race/start_now` | Bring the next start forward, so its warning signal is now |
| `POST /api/v1/race/shift` | Move every start that hasn't happened: `{"minutes": 5}` |
| `POST /api/v1/race/set_start` | Set exactly when a start is: `{"start": ..., "name": "Solo", "sent_at": ...}` |
| `POST /api/v1/race/postpone`, `resume` | Postpone, and end the postponement |
| `POST /api/v1/race/general_recall` | Recall the most recent class |
| `POST /api/v1/race/individual_recall`, `clear_individual_recall` | Fly and take down flag X |
| `POST /api/v1/race/skip` | Move on to the next start |
| `POST /api/v1/race/abandon` | Stop all starts |
| `POST /api/v1/sequences` | Use new sequences: `{"sequences": {...}, "force": false, "save": false}` |
| `POST /api/v1/recording/start`, `stop` | Start or stop recording, while streaming |
| `POST /api/v1/stream/start`, `stop` | Start or stop the stream. Starting can take a few seconds |

These behave as described for the [Python interface](python_api.md), including:

- **Start times:** `start` and `sent_at` are seconds since the epoch, or ISO 8601 times with a
  time zone, such as `2026-11-14T10:30:00+00:00`. Start times need this computer's clock to be
  synchronised using NTP.
- **Sequences:** the sequences are checked as in the GUI's sequence editor, and the video changes
  straight away.

Every command is journalled as coming from `suite`. A successful `POST` replies `200` with the
`status` afterwards. `POST /api/v1/sequences` also replies with `warnings`.

### Status

```json
{
  "time": 1794225600.0, "phase": "in sequence", "display": "Solo -04:59",
  "flags": ["S"],
  "upcoming": {"name": "Solo", "class_flag": "S", "start": 1794225900.0},
  "latest": null,
  "camera_live": true, "streaming": true, "recording": false, "youtube": "live",
  "clock": {"time": 1794225600.0, "synchronised": true, "last_difference": 0.004},
  "viewers": {"TV or browser": "http://..."},
  "last_event": 42
}
```

`youtube` is `off`, `connecting`, or `live` when video is reaching YouTube. `viewers` is empty
when the stream isn't running. `last_event` is the number of the newest event.

### Errors

A refused request has a status, and a JSON body with the reason and a `code`:

```json
{"error": "That start time has already passed", "code": "refused"}
```

| Status | `code` | When |
|---|---|---|
| 400 | `bad_request` | The request is malformed, such as a time without a time zone |
| 401 | `unauthorised` | The token is missing or wrong |
| 403 | `read_only` | The read-only token was used to change something |
| 403 | | The request came from a web page on another site |
| 404 | `not_found` | No such request, or the interface is off |
| 409 | `refused` | A race command doesn't make sense now, such as resuming without a postponement |
| 409 | `clock_not_synchronised` | A start time was refused, because this computer's clock isn't synchronised |
| 409 | `recording` | Recording can't start, such as when the stream isn't running |
| 413 | `bad_request` | The request is larger than 64 KB |
| 422 | `invalid_sequences` | The sequences can't be used. `problems` lists each reason |
| 429 | `locked_out` | Too many wrong tokens from this address |
| 500 | `internal` | Something unexpected went wrong. The camera system's log says what |
| 503 | `stream` | The stream can't start, such as when a port is in use |
| 503 | `busy` | Eight event streams are already open |

## Events

`GET /api/v1/events/stream` sends each event as it happens, as
[Server-Sent Events](https://html.spec.whatwg.org/multipage/server-sent-events.html):

```
id: 7
event: flag_up
data: {"number": 7, "time": 1794225660.0, "kind": "flag_up", "details": {"flag": "P"}}
```

- The kinds and their details are as in the [Python interface](python_api.md#events).
- The stream starts with any recent events after the `since` parameter, or after the
  `Last-Event-ID` header, which clients send when they reconnect. Without either, it starts with
  the last thousand events.
- A comment line is sent every 15 seconds when it's quiet, so a dropped connection is noticed.
- A gap in the numbers means events were missed. If the numbers start again from 1, the camera
  system restarted. Send the sequences again, unless they were saved, and check the status.
- Up to eight streams can be open at once. A client that can't use a stream can poll
  `GET /api/v1/events?since=N` instead.

## Example client

`examples/network_client.py` runs a start entirely from the network interface, using only
Python's standard library. It starts the stream, sends a one-class sequence, and sets its start
a few minutes ahead. Then it logs each event until the start, and how close the start event was
to the time it set:

```bash
STARTCAM_API_TOKEN=... python3 examples/network_client.py \
    https://startcam.local:8443 --certificate control_page.crt --minutes 6
```

`--stop-stream` stops the stream afterwards. The sequences it sends replace the day's
sequences, so use it for testing, not on a race day.
