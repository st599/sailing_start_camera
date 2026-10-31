# Streaming

To publish the camera to the local network:

```bash
venv/bin/startcam --config config.json --secrets secrets.json
```

Press Ctrl+C to stop. It logs the addresses that viewers can open:

| Protocol | Address | Use |
|---|---|---|
| WebRTC | `http://<computer-ip>:8889/start/` | Lowest delay, in a browser |
| HLS | `http://<computer-ip>:8888/start/` | Most compatible, in a browser; higher delay |
| RTSP | `rtsp://<computer-ip>:8554/start` | VLC and similar players |

RTSP is served over TCP only. A player that tries UDP first may take a few seconds to
switch.

The ports come from the `stream` section of the configuration.

## How it behaves

- **Frame rate:** the output runs at `stream.fps`, whatever the camera delivers.
- **Outages:** viewers see "CAMERA SIGNAL LOST" until the camera returns.
- **Failures:** if the encoder or the server exits, it's restarted automatically.
- **Access:** anyone on the network can watch, and there's no password. Only this
  computer can publish, so other devices can't replace the stream.

## First run

The first run downloads MediaMTX, the server that viewers connect to, so it needs
internet access. The version is pinned and its checksum verified, and it's kept in
`~/.cache/start_camera`. After that, no internet is needed. Run it once before taking a
computer to a site with no internet.

MediaMTX is available here for 64-bit Linux on x86 and ARM, which includes the
Raspberry Pi 5 running a 64-bit OS.

## Firewalls

Viewers need these ports open on the computer: TCP 8554, 8888 and 8889, and UDP 8189 for
WebRTC.
