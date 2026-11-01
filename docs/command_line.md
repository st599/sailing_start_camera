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

## Phone control page

Race officers can control the racing from a phone on the same network. To turn this on, add a
shared password to `secrets.json`:

```json
{ "control_password": "choose-a-long-password" }
```

The address is logged at startup, such as `https://192.168.0.10:8443/`.

**The first time on each phone, the browser shows a security warning.** This is expected,
because the certificate is created by this computer, not a public authority. It still encrypts
the connection, so the password can't be read by others on the WiFi. Check the address is
right, then choose the browser's option to continue. Don't do this for an address you don't
recognise. The warning may also return if the computer's address changes.

The page shows the same time and flags as the video, and updates every second. It has large
buttons for each race command and the `control.delay_steps` delays. Abandon, general recall and
next start ask for confirmation. A red `CONNECTION LOST` banner shows if the phone loses
contact, and the buttons won't work until it's back. Use the flags on the water in any case.

- **Logging in:** a login lasts 12 hours, or until the application restarts. Five wrong
  passwords from one device lock it out for a minute.
- **The certificate:** it's kept in `~/.config/start_camera`, and lasts 10 years. Delete the
  folder to create a new one, and each phone then needs to accept it again.
- **With no password set:** the page is off, and the video carries on as normal. The same goes
  if the page can't start, and the log says why.

## Stopping

Ctrl+C, `q` then `y`, or a `SIGTERM` or `SIGHUP` signal all stop it cleanly, and end the
processes it started. If it's killed outright with `SIGKILL`, those processes end by themselves.
That needs the `setpriv` command for the server, which is part of `util-linux` and is normally
already installed.
