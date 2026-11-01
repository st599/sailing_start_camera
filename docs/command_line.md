# Command-line application

`startcam` streams the camera with its overlays, and can run with no display attached, such
as on a Raspberry Pi.

| Command | What it does |
|---|---|
| `startcam --config config.json --secrets secrets.json` | Stream until stopped |
| `startcam --scan --config config.json --secrets secrets.json --write` | Find the camera and save its address |
| `startcam --check-config --config config.json --secrets secrets.json` | Check the files, then exit |

Other options:

| Option | Meaning |
|---|---|
| `--log-level` | `debug`, `info`, `warning` or `error` |
| `--log-file` | Also write the log to this file |
| `--subnet` | Another subnet for `--scan`; can be repeated |

The exit code is 0 on success and 1 on an error, and the log says what went wrong.

## Race control keys

These are single key presses, with no need to press Enter:

| Key | Action |
|---|---|
| `s` | Start now |
| `1` `5` `0` | Delay by +1, +5 or +10 minutes |
| `-` | Delay by -1 minute |
| `p` | Postpone |
| `r` | Resume |
| `g` | General recall |
| `x` | Individual recall; press again to clear |
| `n` | Skip to the next start |
| `a` | Abandon, then `y` to confirm |
| `q` | Quit, then `y` to confirm |
| `h` | Show the keys |

A confirmation is cancelled by any other key, and times out after 5 seconds. A command that
can't be carried out is refused, and the log says why.

These keys are fixed. `control.delay_steps` in the configuration is for the GUI and the phone
page.

With no terminal, such as when running as a service, the application runs with the keys off.

## Stopping

Ctrl+C, `q` then `y`, or a `SIGTERM` or `SIGHUP` signal all stop it cleanly, and end the
processes it started. If it's killed outright with `SIGKILL`, those processes end by themselves.
That needs the `setpriv` command for the server, which is part of `util-linux` and is normally
already installed.
