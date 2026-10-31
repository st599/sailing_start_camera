#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_config.py
#  Description: Tests for the configuration and secrets.
#  Date: 2026-10-31
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: pytest
#
#########################################################

"""Tests for the configuration and secrets."""

#
# SYSTEM IMPORTS
#

import json
import logging
import os
from datetime import time
from pathlib import Path
from typing import Any

import pytest

#
# PROJECT IMPORTS
#
from start_camera.config import (
    SCHEMA_VERSION,
    Config,
    ConfigError,
    LineFit,
    LinePoint,
    SequenceConfig,
    StartLineConfig,
    default_flag_events,
    load_config,
    load_secrets,
    save_config,
)

#
#  GLOBAL VARIABLES
#

EXAMPLES = Path(__file__).parent.parent / "examples"

#
# FUNCTIONS
#


def full_config() -> Config:
    """Return a configuration with non-default values in every section."""
    return Config.model_validate_json(
        (EXAMPLES / "config.json").read_text(encoding="utf-8")
    )


def write_json(path: Path, data: object) -> Path:
    """Write data to a JSON file and return its path."""
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_round_trip_is_unchanged(tmp_path: Path) -> None:
    config = full_config()
    path = tmp_path / "config.json"
    save_config(config, path)
    first_save = path.read_text(encoding="utf-8")
    loaded = load_config(path)
    save_config(loaded, path)
    assert loaded == config
    assert path.read_text(encoding="utf-8") == first_save


def test_example_file_uses_every_section() -> None:
    config = load_config(EXAMPLES / "config.json")
    assert config.start_line.fit is LineFit.SPLINE
    assert config.sequences.first_start == time(13, 0)
    assert [item.name for item in config.sequences.items] == ["Handicap", "Lasers"]
    assert config.sequences.items[0].events == default_flag_events()


def test_version_alone_gives_defaults(tmp_path: Path) -> None:
    path = write_json(tmp_path / "c.json", {"version": SCHEMA_VERSION})
    assert load_config(path) == Config()


def test_defaults_match_the_camera() -> None:
    config = Config()
    assert config.camera.rtsp_path == "/av_stream/ch0"
    assert config.stream.fps == 20


def test_default_sequence_is_five_minutes() -> None:
    events = default_flag_events()
    assert [(e.minutes_before_start, e.flag, e.action.value) for e in events] == [
        (5, "class", "up"),
        (4, "preparatory", "up"),
        (1, "preparatory", "down"),
        (0, "class", "down"),
    ]


@pytest.mark.parametrize("data", [{}, {"camera": {}}])
def test_missing_version_is_named(tmp_path: Path, data: dict[str, Any]) -> None:
    path = write_json(tmp_path / "c.json", data)
    with pytest.raises(ConfigError, match="version: required"):
        load_config(path)


@pytest.mark.parametrize("version", [0, 2, 999, "1", 1.0, True, None])
def test_unknown_version_is_rejected(tmp_path: Path, version: object) -> None:
    path = write_json(tmp_path / "c.json", {"version": version})
    with pytest.raises(ConfigError, match="version: unsupported version"):
        load_config(path)


@pytest.mark.parametrize(
    ("section", "field_name"),
    [
        ({"camera": {"rtsp_port": 70000}}, "camera.rtsp_port"),
        ({"camera": {"rtsp_port": "554"}}, "camera.rtsp_port"),
        ({"camera": {"mac": "not-a-mac"}}, "camera.mac"),
        ({"camera": {"capture_latency_ms": -1}}, "camera.capture_latency_ms"),
        ({"stream": {"width": 1281}}, "stream.width"),
        ({"stream": {"fps": 0}}, "stream.fps"),
        (
            {"overlays": {"datetime": {"position": "middle"}}},
            "overlays.datetime.position",
        ),
        ({"overlays": {"countdown": {"colour": "red"}}}, "overlays.countdown.colour"),
        ({"start_line": {"points": [{"x": 1.5, "y": 0}]}}, "start_line.points.0.x"),
        ({"start_line": {"points": [{"x": 0.5}]}}, "start_line.points.0.y"),
        ({"start_line": {"fit": "wiggly"}}, "start_line.fit"),
        ({"sequences": {"first_start": "25:00:00"}}, "sequences.first_start"),
        ({"sequences": {"items": [{"class_flag": "A"}]}}, "sequences.items.0.name"),
        ({"sequences": {"items": [{"name": "A"}]}}, "sequences.items.0.class_flag"),
        (
            {
                "sequences": {
                    "items": [{"name": "A", "class_flag": "A", "events": []}]
                }
            },
            "sequences.items.0.events",
        ),
        ({"unknown": 1}, "unknown"),
        ({"camera": {"password": "x"}}, "camera.password"),
    ],
)
def test_invalid_value_names_the_field(
    tmp_path: Path, section: dict[str, Any], field_name: str
) -> None:
    path = write_json(tmp_path / "c.json", {"version": SCHEMA_VERSION, **section})
    with pytest.raises(ConfigError, match=f"{field_name}: "):
        load_config(path)


def test_all_errors_are_reported_together(tmp_path: Path) -> None:
    path = write_json(
        tmp_path / "c.json",
        {"version": SCHEMA_VERSION, "camera": {"rtsp_port": 0}, "stream": {"fps": 0}},
    )
    with pytest.raises(ConfigError) as error:
        load_config(path)
    assert "camera.rtsp_port" in str(error.value)
    assert "stream.fps" in str(error.value)


def test_matching_stream_ports_are_rejected(tmp_path: Path) -> None:
    path = write_json(
        tmp_path / "c.json",
        {"version": SCHEMA_VERSION, "stream": {"hls_port": 8554}},
    )
    with pytest.raises(ConfigError, match="stream: .*must all be different"):
        load_config(path)


@pytest.mark.parametrize(
    ("fit", "count", "valid"),
    [
        (LineFit.STRAIGHT, 1, False),
        (LineFit.STRAIGHT, 2, True),
        (LineFit.SPLINE, 1, False),
        (LineFit.SPLINE, 2, True),
        (LineFit.POLYNOMIAL, 2, False),
        (LineFit.POLYNOMIAL, 3, True),
    ],
)
def test_enabled_line_needs_enough_points(
    fit: LineFit, count: int, valid: bool
) -> None:
    points = [LinePoint(x=i / 10, y=0.5) for i in range(count)]
    if valid:
        StartLineConfig(enabled=True, fit=fit, points=points)
    else:
        with pytest.raises(ValueError, match="points"):
            StartLineConfig(enabled=True, fit=fit, points=points)


def test_disabled_line_may_have_no_points() -> None:
    assert StartLineConfig().points == []


def test_integer_coordinates_are_accepted(tmp_path: Path) -> None:
    path = write_json(
        tmp_path / "c.json",
        {
            "version": SCHEMA_VERSION,
            "start_line": {"points": [{"x": 0, "y": 1}]},
        },
    )
    assert load_config(path).start_line.points == [LinePoint(x=0, y=1)]


def test_first_start_with_utc_offset_is_rejected(tmp_path: Path) -> None:
    path = write_json(
        tmp_path / "c.json",
        {"version": SCHEMA_VERSION, "sequences": {"first_start": "13:00:00+01:00"}},
    )
    with pytest.raises(ConfigError, match="sequences.first_start: .*local time"):
        load_config(path)


def test_sequences_do_not_share_default_events() -> None:
    first = SequenceConfig(name="A", class_flag="A")
    second = SequenceConfig(name="B", class_flag="B")
    first.events.pop()
    assert len(second.events) == 4


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"{not json", "invalid JSON at line 1"),
        (b"[]", "top level must be a JSON object"),
        (b"\xff\xfe", "cannot read file"),
    ],
)
def test_unreadable_content_is_reported(
    tmp_path: Path, content: bytes, message: str
) -> None:
    path = tmp_path / "c.json"
    path.write_bytes(content)
    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_missing_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read file"):
        load_config(tmp_path / "missing.json")


def test_failed_save_keeps_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    save_config(Config(), path)
    original = path.read_text(encoding="utf-8")

    def fail(descriptor: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(ConfigError, match="disk full"):
        save_config(full_config(), path)
    assert path.read_text(encoding="utf-8") == original
    assert [item.name for item in tmp_path.iterdir()] == ["config.json"]


def test_save_to_missing_directory_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot save file"):
        save_config(Config(), tmp_path / "missing" / "config.json")


def test_saved_config_contains_no_secrets(tmp_path: Path) -> None:
    secret_file = write_json(
        tmp_path / "secrets.json",
        {"camera_password": "hunter2", "youtube_stream_key": "abcd-1234"},
    )
    load_secrets(secret_file, environ={})
    path = tmp_path / "config.json"
    save_config(full_config(), path)
    text = path.read_text(encoding="utf-8").lower()
    assert "hunter2" not in text
    assert "abcd-1234" not in text
    assert "password" not in text
    assert "stream_key" not in text


def test_secrets_come_from_file(tmp_path: Path) -> None:
    path = write_json(tmp_path / "s.json", {"camera_password": "from-file"})
    secrets = load_secrets(path, environ={})
    assert secrets.camera_password is not None
    assert secrets.camera_password.get_secret_value() == "from-file"
    assert secrets.youtube_stream_key is None


def test_environment_overrides_secrets_file(tmp_path: Path) -> None:
    path = write_json(tmp_path / "s.json", {"camera_password": "from-file"})
    secrets = load_secrets(path, environ={"STARTCAM_CAMERA_PASSWORD": "from-env"})
    assert secrets.camera_password is not None
    assert secrets.camera_password.get_secret_value() == "from-env"


def test_empty_environment_variable_is_ignored() -> None:
    secrets = load_secrets(environ={"STARTCAM_CAMERA_PASSWORD": ""})
    assert secrets.camera_password is None


def test_secrets_are_hidden_when_displayed() -> None:
    secrets = load_secrets(environ={"STARTCAM_YOUTUBE_STREAM_KEY": "abcd-1234"})
    assert "abcd-1234" not in repr(secrets)
    assert "abcd-1234" not in str(secrets)


def test_invalid_secrets_error_hides_values(tmp_path: Path) -> None:
    path = write_json(
        tmp_path / "s.json", {"camera_pasword": "hunter2", "camera_username": 12345}
    )
    with pytest.raises(ConfigError) as error:
        load_secrets(path, environ={})
    message = str(error.value)
    assert "camera_pasword" in message
    assert "camera_username" in message
    assert "hunter2" not in message
    assert "12345" not in message


def test_readable_secrets_file_gives_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = write_json(tmp_path / "s.json", {})
    path.chmod(0o644)
    with caplog.at_level(logging.WARNING):
        load_secrets(path, environ={})
    assert "chmod 600" in caplog.text


def test_private_secrets_file_gives_no_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = write_json(tmp_path / "s.json", {})
    path.chmod(0o600)
    with caplog.at_level(logging.WARNING):
        load_secrets(path, environ={})
    assert caplog.text == ""
