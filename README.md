# sailing_start_camera
A start line camera for sailing races.

## Getting started

- **Running a race?** Use the [race-day guide](docs/race_day_guide.md).
- **Setting the system up?** Follow the [setup guide](docs/setup_guide.md).

## Reference

- [Command-line application](docs/command_line.md)
- [GUI application](docs/gui.md)
- [Streaming and viewing](docs/streaming.md)
- [Race control](docs/race_control.md)
- [Flags](docs/flags.md)
- [Configuration](docs/configuration.md)
- [YouTube Live](docs/youtube.md)
- [The camera](docs/camera.md)
- [Performance](docs/performance.md)

## Development setup

```bash
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -m pip install --no-deps -e .
venv/bin/python -m ruff check .
venv/bin/python -m pytest
```

The GUI, and its tests, also need:
`venv/bin/python -m pip install -r requirements-gui.txt`.
It isn't needed for the command-line application, such as on a Raspberry Pi.

## Licence

MIT. See [LICENSE](LICENSE).
