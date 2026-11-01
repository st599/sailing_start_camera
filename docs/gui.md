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

**File, Start line** places the start line on a frozen picture from the camera:

1. Click the two ends of the line. Line them up with what marks the line, such as a boat or
   marker on the transit, or the Inner Distance Mark. **New picture** freezes another moment.
2. Click any points between them. Points can be clicked in any order, and each goes where it
   adds the least length to the line. Add more where the lens curves the line, usually near
   the edges of the picture.
3. Drag a point to move it. Right-click a point, or select it and press Delete, to remove it.
4. Scroll to zoom in, and drag with the middle button to move around.
5. Choose the fit. `spline` goes through every point, and `polynomial` evens out small errors.

The preview is drawn by the same code as the stream. The line stops at its first and last
points. If the camera moves, the line has to be placed again. Each configuration file holds one
line, so use a file for each starting box.

**File, Start sequences** edits the day's starts:

- **Templates** replace the sequences with a saved set. Tamesis Club's Summer Series templates
  are built in, with the Blue, White and combined days, morning and afternoon, from the 2026
  Series Notice. **Save as template** keeps the current sequences for another day, in
  `~/.config/start_camera/race_templates`. A club can add its own JSON file of templates and
  classes there, in the format of `src/start_camera/race_templates_data`.
- **First start** is either a scheduled time or, when it's not ticked, whenever the race officer
  starts it.
- **Starts** can be added, with their flags from the class list, removed and reordered, up to 10.
- **A combined start** has its flags joined by `+`, such as `8+Solo`, and each is shown on the
  video.
- **Events** give the flag signals in minutes before the start. **Standard signals** restores
  Rule 26's, and the Thursday N18 signals (SI 12.4) are a template.
- **What flies when** shows the resulting times. Impossible timings, such as a flag coming down
  before it goes up, are explained in red, and have to be fixed before pressing OK. Overlaps are
  allowed, and are pointed out as a warning.

Check the templates against the current notices. **Changing the sequences starts the race journal
afresh**, so do this before racing rather than during it.

**Scan network**, on the Camera tab, lists the devices it finds, likeliest camera first. Select
one and press **Use selected camera** to fill in its address and MAC address. To test a camera
during a scan, it uses the camera login from the secrets.

Passwords and the stream key stay in `secrets.json`, and can't be edited here or written to a
configuration file. Changing `sequences` restarts the race journal, as described in
[Race control](race_control.md).
