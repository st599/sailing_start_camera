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

Opening configuration files and changing settings in the window are still to come. For now, use
`--config` as above.
