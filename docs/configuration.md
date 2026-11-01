# Configuration

Both applications read the same JSON file, which you can copy between computers.
[examples/config.json](../examples/config.json) is a complete example. Only `version`
is required; anything you leave out takes the default below.

Values are checked strictly: unknown fields and wrong types, such as `"554"` for a port,
are errors that name the field at fault.

## Sections

### `camera`

| Field | Default | Meaning |
|---|---|---|
| `host` | `null` | Camera IP address or name. `null` means find it by scanning |
| `mac` | `null` | Camera MAC address, used to find it again if its IP changes |
| `rtsp_port` | `554` | Camera RTSP port |
| `rtsp_path` | `/av_stream/ch0` | Camera stream path; `/av_stream/ch1` is the low resolution stream |
| `capture_latency_ms` | `0` | Camera delay, corrected for in the race clock |
| `scan_subnets` | `[]` | Extra subnets to scan, such as `192.168.1.0/24`; no bigger than `/20` |

### `stream`

| Field | Default | Meaning |
|---|---|---|
| `width`, `height` | `1280`, `720` | Output size; must be even numbers |
| `fps` | `20` | Output frame rate; the Cam-S3W sends 20 fps |
| `bitrate_kbps` | `2500` | Output video bitrate |
| `keyframe_interval_s` | `2` | Seconds between keyframes |
| `rtsp_port`, `hls_port`, `webrtc_port` | `8554`, `8888`, `8889` | Stream ports |
| `viewer_port` | `8080` | Port of the viewer pages |

All the stream's ports must be different.

| Field | Default | Meaning |
|---|---|---|
| `youtube_enabled` | `false` | Push the stream to YouTube Live |
| `youtube_ingest_url` | `rtmps://a.rtmp.youtube.com/live2` | Address to push to, from YouTube Studio |

### `overlays`

`datetime`, `countdown` and `flags` each have:

| Field | Meaning |
|---|---|
| `enabled` | Show this overlay (default `true`) |
| `position` | `top_left`, `top_right`, `bottom_left` or `bottom_right` |
| `scale` | Size multiplier (default `1.0`) |

`datetime` defaults to `top_left`, `countdown` to `top_right` and `flags` to
`bottom_right`. They also have:

| Overlay | Field | Default | Meaning |
|---|---|---|---|
| `datetime`, `countdown` | `colour` | `#ffffff` | Text colour as `#rrggbb` |
| `datetime` | `format` | `%H:%M:%S %d/%m/%y` | [strftime](https://docs.python.org/3/library/datetime.html#format-codes) format |
| `flags` | `show_labels` | `true` | Show each flag's name |

### `start_line`

| Field | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Show the line |
| `points` | `[]` | Points as `{"x": 0-1, "y": 0-1}`: fractions of the frame from the top left |
| `fit` | `straight` | `straight`, `polynomial` or `spline` |
| `polynomial_degree` | `2` | Degree (2-5) when `fit` is `polynomial` |
| `colour` | `#ff0000` | Line colour as `#rrggbb` |
| `width` | `2` | Line width in pixels |
| `opacity` | `0.8` | From 0 (invisible) to 1 (solid) |

An enabled line needs at least two points, or one more than the degree for `polynomial`.

### `sequences`

| Field | Default | Meaning |
|---|---|---|
| `first_start` | `null` | Local time of the first start, as `"HH:MM:SS"`. `null` means start by hand |
| `general_recall` | `delay_following` | `delay_following` or `end_of_queue`; see [Race control](race_control.md) |
| `recall_flag_minutes` | `1` | How long the First Substitute flies after a general recall |
| `items` | `[]` | The start sequences, in order; up to 10 |

Each item in `items` has:

| Field | Default | Meaning |
|---|---|---|
| `name` | required | Name of the class or sequence |
| `class_flag` | required | Name of its class flag |
| `preparatory_flag` | `P` | `P`, `I`, `Z`, `Z+I`, `U` or `black` |
| `minutes_after_previous` | `5` | Minutes from the previous start to this one; ignored for the first. With the default five minute sequence, `5` puts this class's warning signal at the previous class's start |
| `events` | five minute sequence | When flags go up and down |

Each event has `minutes_before_start`, `flag` and `action` (`up` or `down`). `flag` is
`class`, `preparatory` or the name of any other flag. The default is:

| Minutes before start | Flag | Action |
|---|---|---|
| 5 | `class` | `up` |
| 4 | `preparatory` | `up` |
| 1 | `preparatory` | `down` |
| 0 | `class` | `down` |

Check these against the current Racing Rules of Sailing and your sailing instructions.

## Secrets

Secrets never go in the configuration file; a `password` field there is an error. Put
them in a separate file, based on
[examples/secrets.example.json](../examples/secrets.example.json), and protect it:

```bash
chmod 600 secrets.json
```

`secrets.json` is ignored by Git. Environment variables override the file:

| Secrets file field | Environment variable |
|---|---|
| `camera_username` | `STARTCAM_CAMERA_USERNAME` |
| `camera_password` | `STARTCAM_CAMERA_PASSWORD` |
| `youtube_stream_key` | `STARTCAM_YOUTUBE_STREAM_KEY` |
| `control_password` | `STARTCAM_CONTROL_PASSWORD` |
