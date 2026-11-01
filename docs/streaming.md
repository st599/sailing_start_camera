# Streaming

To publish the camera to the local network:

```bash
venv/bin/startcam --config config.json --secrets secrets.json
```

Press Ctrl+C to stop. It logs the addresses that viewers can open:

| Use | Address |
|---|---|
| Smart TV or any browser | `http://<computer-ip>:8080/` |
| Lowest delay, in a browser | `http://<computer-ip>:8080/live` |
| Media player on a TV or computer, including VLC | `http://<computer-ip>:8080/stream.m3u8` |
| RTSP player | `rtsp://<computer-ip>:8554/start` |

The ports come from the `stream` section of the configuration.

If the computer's IP address changes, `http://<computer-name>.local:8080/` can keep working
on devices that support mDNS. Many TVs don't, so reserve the computer's IP address on the
router if you can.

## Watching on a smart TV

Try these in order:

1. **TV browser:** open `http://<computer-ip>:8080/`, and bookmark it if the TV allows.
2. **Media player app:** in VLC or a similar app on the TV, open
   `http://<computer-ip>:8080/stream.m3u8`. If the app needs the final address, use
   `http://<computer-ip>:8888/start/index.m3u8`. Some TVs' built-in media players have no
   way to enter an address, so this won't be an option on every TV.
3. **A device plugged into the TV:** a computer or streaming stick with a browser or media
   player can use the same addresses.

The stream is H.264 in HLS over plain HTTP, which is widely supported. It isn't offered
over DLNA, so a TV's DLNA or "media server" menu won't list it.

### RTSP players

RTSP is served over TCP only. A player that tries UDP first may take a few seconds to
switch, and some need telling, as in this example:

```bash
ffplay -rtsp_transport tcp rtsp://<computer-ip>:8554/start
```

Ubuntu's VLC 3.0.20 package has no `live555` module, so it can't open RTSP from any
source, including the camera directly. It reports "VLC is unable to open the MRL". Use the
media player address above instead.

## Delay

Measured from encoding a frame to its display in Firefox, on the same computer:

| Route | Delay |
|---|---|
| WebRTC (`/live`) | About 60-70 ms |
| HLS with the browser player (`/`) | About 5.8 s |

Delay across the network and from the camera comes on top, as does YouTube's own delay for
public viewers. HLS delay varies between sessions and players, and a TV may buffer more.
Every viewer's burned-in clock stays in step with their video.

## Date and time

The video shows the local date and time in the top left corner, as `HH:MM:SS dd/mm/yy`. The
format, position and size can be changed in the `overlays.datetime` section of the
configuration. It's the computer's clock, set back by `camera.capture_latency_ms`, so it
shows when the picture was captured.

Turn off the camera's **Watermark** setting, as otherwise it adds its own clock and logo.
If the computer's clock isn't synchronised using NTP, a warning is logged at startup.

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

Viewers need these ports open on the computer: TCP 8080, 8554, 8888 and 8889, and UDP 8189
for WebRTC. The phone control page needs TCP 8443.
