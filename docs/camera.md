# Sonoff Cam-S3W

Findings from one camera tested on 2026-10-31, on the same WiFi network as the computer
and in the same building. Firmware may change this behaviour.

## Enabling the stream

The camera's local stream needs switching on. eWeLink can show the camera while the
local stream is off, and closing the app doesn't enable it.

1. Open `http://<camera-ip>/` in a browser and log in.
2. Open the **ONVIF/RTSP** settings, turn them on and set a username and password.

With it off, port 554 accepts connections but never answers, and `ffprobe` reports
`Invalid data found when processing input`. WS-Discovery also got no reply.

## Streams

URL format: `rtsp://<username>:<password>@<camera-ip>:554/av_stream/<channel>`

| Channel | Video | Frame rate | Keyframes | Measured bitrate |
|---|---|---|---|---|
| `ch0` | H.264 High, 2304×1296, yuv420p | 20 fps | Every 3 s | 1417 kbps |
| `ch1` | H.264 High, 640×360, yuv420p | 20 fps | Every 2 s | 205 kbps |

- Both channels also carry audio: G.711 A-law (`pcm_alaw`), 8 kHz, mono.
- TCP and UDP transport both work. RTSP isn't encrypted, so use it only on a trusted
  network.
- Requests without valid credentials get `401 Unauthorized`.
- The bitrate was measured on a mostly static indoor scene. Expect more over moving water.

## Discovery

- With the stream on, the camera answers ONVIF WS-Discovery, at
  `http://<camera-ip>:80/onvif/device_service`.
- Its scopes contain `hardware/SN-T23N-CAMS3W-01`, which identifies the model.
- Unauthenticated `GetDeviceInformation` requests are refused with `Sender not
  Authorized`. `GetSystemDateAndTime` is answered.
- The unauthenticated web page has the title `sonoff-camera-web` and its server header is
  `onvif-simple`.

## Finding the camera

ONVIF/RTSP must be on. To find the camera, and save its address to the configuration:

```bash
startcam --scan --config config.json --secrets secrets.json --write
```

It checks the computer's own subnets, using ONVIF discovery and a scan for the RTSP port.
Its password only goes to a device identified as the camera: by model, saved MAC address
or `camera.host`.

If a WiFi extender or router puts the camera on another subnet:

- Add it with `--subnet 192.168.1.0/24`, or `camera.scan_subnets` in the configuration.
- The computer must be able to reach that subnet. If the extender uses its own private
  network with NAT, connect the computer to the extender as well, or set the extender to
  bridge or access point mode.
- Its MAC address may be hidden or changed. A matching MAC address is preferred, but a
  single camera of the right model is accepted, with a warning.
- If the camera can't be identified as this model, set `camera.host`.

## Stability

| Test | Duration | Longest gap | Gaps over 500 ms | Median gap |
|---|---|---|---|---|
| `ch0` | 3 minutes | 210 ms | 0 | 50 ms |
| `ch1` | 1 minute | 277 ms | 0 | 50 ms |

This is a short test on a good connection, without a WiFi extender. It says little about
long runs or the final mounting position.

The first frame took about 1.8 s to capture after connecting to `ch0`. Decoding `ch0`
used about 26% of one CPU core on a laptop; this hasn't been measured on a Raspberry Pi.

## Image

- **Burned-in overlays:** by default the camera draws its own date and time at the top
  left and a Sonoff logo at the top right, the same corners as our date and countdown
  overlays. Turn off the **Watermark** setting on the camera's web page to remove both
  from `ch0` and `ch1`.
- **Lens:** strong wide-angle distortion curves straight lines, particularly near the
  frame edges, so a straight start line fit won't suit every camera position.

## Delay

The delay from camera to decoded frame is at most about 0.7-0.8 s on `ch0`, over a good
WiFi connection. This is only an upper limit, from three frames of a whole-second clock
on a screen, and relies on a clock offset read from a blurry image. It is not an exact
value or a typical one.

Delay depends on the WiFi path, so measure it again with the camera installed, and
whenever the network changes. You need a phone or laptop on the same network, and a
`config.json` in the current folder. If you don't have one yet, this is enough:

```json
{ "version": 1, "camera": { "host": "<camera-ip>" } }
```

Then run:

```bash
startcam-delay --config config.json --secrets secrets.json --write
```

1. Open the address it logs on the phone or laptop, and turn the brightness up.
2. Hold the screen still in front of the camera, filling as much of the view as you can.
3. Press Enter. The screen flashes white every 5 seconds for the 45 second measurement.

`--write` saves the result as `capture_latency_ms`; leave it out to only see the result.
Use `--host <camera-ip>` if the configuration has no camera address. The page syncs to
the measuring computer's clock, so neither clock needs to be correct. If the camera or
screen moves, the command reports an error instead of a value, and saves nothing.
Delays of up to about 4.5 s can be measured.
