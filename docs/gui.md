# GUI application

`startcam-gui` shows the camera with its overlays, controls the racing and starts and stops
the stream. It needs a display, and PySide6:

```bash
venv/bin/python -m pip install -r requirements-gui.txt
venv/bin/startcam-gui --config config.json --secrets secrets.json
```

Stop `startcam` first, as they use the same ports and race journal.

If it reports that it could not load the Qt platform plugin `xcb` on Ubuntu or Debian, install
the missing library with `sudo apt install libxcb-cursor0`.

## The window

- **The picture** shows the video with the same overlays as the stream. It's a preview on this
  computer, so it's ahead of what stream viewers see by the stream's delay.
- **The panel** shows the time and flags, the race commands and the delay buttons, which come
  from `control.delay_steps`. These are the same commands as the keys and the phone page, and
  they're described in [Race control](race_control.md). Abandon, general recall and next start ask
  for confirmation. If a command is refused, the reason appears under the time.
- **Start streaming** starts the stream, the viewer pages, the phone page and YouTube if it's
  enabled. The addresses then appear in the panel. The first time, it may need to download
  MediaMTX. Stopping the stream leaves the preview and the racing running.
- **The log** at the bottom shows what's happening, including any problems.

The window fits screens of 1024x600 and larger, and the picture scales with the window. If the
panel doesn't fit, it scrolls.

Closing the window stops everything, and asks first if the stream is running. Racing carries on
from the race journal when it's next started, as it does for `startcam`.

## Settings and files

The **File** menu opens and saves configuration files, which work unchanged with `startcam`.
`--config` is optional, and without it the window starts with the defaults. The title shows
the file's name, with a `*` if there are unsaved changes, and you're offered the chance to save
them before they'd be lost.

**File, Settings** edits the camera, stream and overlay settings. A problem with a value is
explained directly beneath it, and nothing changes until they're all valid. New settings are
used straight away, and aren't in the file until you save them. Settings can't change while
the stream is running, so stop it first.

**Scan network**, on the Camera tab, lists the devices it finds, likeliest camera first. Select
one and press **Use selected camera** to fill in its address and MAC address. To test a camera
during a scan, it uses the camera login from the secrets.

Passwords and the stream key stay in `secrets.json`, and can't be edited here or written to a
configuration file. Changing `sequences` restarts the race journal, as described in
[Race control](race_control.md).
