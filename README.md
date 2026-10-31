# sailing_start_camera
A start line camera for sailing races.

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
