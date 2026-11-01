# Performance

**The defaults haven't been confirmed on a Raspberry Pi 5.** The default of 1280x720 at
20 fps is provisional until the benchmark below has been run on one.

## Running the benchmark

Stop `startcam` first, as the benchmark uses the same ports. The camera must be reachable,
because each test runs the real pipeline from the camera to the server with overlays as
busy as the real ones. It takes about 6 minutes:

```bash
venv/bin/startcam-benchmark --config config.json --secrets secrets.json --output results.md
```

A setting counts as sustained if it meets all of these during the measurement:

- The output reaches at least 95% of the target frame rate.
- No frames are late.
- The camera signal isn't lost.
- It uses no more than 80% of the processor.

This is a short test. A setting also needs the longer test in the Raspberry Pi install
guide, as a hot processor can slow down over time. The scene matters too: this test should
be repeated on a moving scene, such as water, because that takes more encoding work.

## Results

### Laptop baseline (not a Raspberry Pi)

Measured on 2026-11-01 with an Intel Core i5-10310U (8 logical processors), for 30 seconds
per setting, on a mostly still indoor scene. HD is camera channel `ch0`, and SD is `ch1`.

| Settings | Output fps | Camera fps | Late | Processor (cores) | Encoder | Decoder | Overlays (ms) | Memory (MB) | Sustained |
|---|---|---|---|---|---|---|---|---|---|
| HD to 1280x720 at 20 fps | 20.0 | 20.0 | 0 | 2.50 | 1.25 | 0.92 | 7.38 | 418 | yes |
| HD to 1280x720 at 15 fps | 15.0 | 20.0 | 0 | 2.01 | 0.92 | 0.84 | 6.64 | 426 | yes |
| HD to 960x540 at 20 fps | 19.9 | 20.0 | 0 | 1.53 | 0.60 | 0.71 | 5.33 | 358 | yes |
| SD to 1280x720 at 20 fps | 20.0 | 20.0 | 0 | 1.83 | 1.05 | 0.46 | 6.43 | 404 | yes |
| SD to 640x360 at 20 fps | 20.0 | 20.0 | 0 | 0.42 | 0.23 | 0.07 | 3.88 | 298 | yes |

The only H.264 encoder that worked here was `libx264`, which runs in software.

What this suggests for the Raspberry Pi, until it's measured:

- The default setting uses 2.5 cores on this laptop, so a Raspberry Pi 5, with 4 cores, may
  have little to spare.
- Using the SD channel at 1280x720 saves about 0.7 cores, but gives a softer picture.
- 960x540 at 20 fps from the HD channel, and 640x360 from the SD channel, are the
  fallbacks.

### Raspberry Pi 5

Not yet measured.
