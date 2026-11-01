# sailing_start_camera
A start line camera for sailing races.

## Documentation

- [Command-line application](docs/command_line.md)
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

The GUI also needs its optional dependencies:
`venv/bin/python -m pip install -e .[gui]`.

## Licence

MIT. See [LICENSE](LICENSE).
