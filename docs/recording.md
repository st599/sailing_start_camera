# Recording

The stream can be recorded to files, with its overlays, such as for reviewing a start later.
Recording copies the stream without encoding it again, so it uses almost no processor time and
doesn't change the picture.

## Starting and stopping

Recording can only run while the stream is running.

- **GUI:** **Start recording**, under **Start streaming**. Stopping asks for confirmation.
- **Phone page:** **Start recording**, near the bottom. A red `RECORDING` banner shows while it
  runs.
- **Automatically:** set `recording.start_with_stream` to `true`, for example on a Raspberry Pi.

Stopping the stream also stops recording, and the current file is finished properly.

## Where recordings go

Files are named by the local date and time and the time zone, such as
`recordings/2026-05-10_14-00-00+0100_start.mp4`. A new
file starts every `segment_minutes`, and each time recording starts. At most the last second is
lost if the power fails.

Files older than `delete_after_days`, 14 days by default, are deleted while the stream is running.
Set it to `0` to keep everything. The `recordings` folder isn't in Git.

## Disk space

At the default 2.5 Mbps, an hour takes about 1.1 GB. When recording starts, the log says how many
hours of space there are. A warning is logged when space gets low, and recording stops before the
free space falls below `min_free_gb`, while the stream carries on. On a Raspberry Pi, a USB drive is
better than the SD card.

## Settings

| Field | Default | Meaning |
|---|---|---|
| `directory` | `recordings` | Where files go. A relative path starts from where the application runs |
| `format` | `mp4` | `mp4` plays in most players; `mpegts` copes better with a power cut |
| `segment_minutes` | `60` | How long each file can be |
| `delete_after_days` | `14` | How long to keep recordings; `0` keeps everything |
| `start_with_stream` | `false` | Record whenever the stream is running |
| `min_free_gb` | `2.0` | Stop recording before the free space falls below this |

## Playing recordings

VLC plays both formats. An MP4 recording also plays in most browsers and video players. The time and
countdown on the picture show when each moment happened.
