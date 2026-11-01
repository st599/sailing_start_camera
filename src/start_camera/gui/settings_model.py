#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: settings_model.py
#  Description: The configuration file being edited, and the settings form's fields.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: pydantic
#
#########################################################

"""The configuration file being edited, and the settings form's fields.

This is free of the GUI library. The form's values are checked by the same validation
that startcam uses, so anything accepted here will also run there.
"""

#
# SYSTEM IMPORTS
#

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import ValidationError

#
# PROJECT IMPORTS
#
from start_camera.config import Config, Corner, load_config, save_config

#
#  GLOBAL VARIABLES
#

GENERAL_ERRORS = ""

#
# FUNCTIONS
#


class FieldKind(Enum):
    """How a field is edited and stored."""

    TEXT = "text"
    OPTIONAL_TEXT = "optional text"
    INTEGER = "integer"
    NUMBER = "number"
    CHECK = "check"
    CHOICE = "choice"
    LIST = "list"


@dataclass(frozen=True)
class Field:
    """One setting on the form."""

    tab: str
    path: str
    label: str
    kind: FieldKind
    choices: tuple[str, ...] = ()


CORNERS = tuple(corner.value for corner in Corner)
FIELDS = (
    Field("Camera", "camera.host", "Address (blank to scan)", FieldKind.OPTIONAL_TEXT),
    Field("Camera", "camera.mac", "MAC address", FieldKind.OPTIONAL_TEXT),
    Field("Camera", "camera.rtsp_port", "RTSP port", FieldKind.INTEGER),
    Field("Camera", "camera.rtsp_path", "Stream path", FieldKind.TEXT),
    Field("Camera", "camera.capture_latency_ms", "Delay (ms)", FieldKind.INTEGER),
    Field("Camera", "camera.scan_subnets", "Extra subnets", FieldKind.LIST),
    Field("Stream", "stream.width", "Width", FieldKind.INTEGER),
    Field("Stream", "stream.height", "Height", FieldKind.INTEGER),
    Field("Stream", "stream.fps", "Frame rate", FieldKind.INTEGER),
    Field("Stream", "stream.bitrate_kbps", "Bitrate (kbps)", FieldKind.INTEGER),
    Field("Stream", "stream.keyframe_interval_s", "Keyframes (s)", FieldKind.INTEGER),
    Field("Stream", "stream.rtsp_port", "RTSP port", FieldKind.INTEGER),
    Field("Stream", "stream.hls_port", "HLS port", FieldKind.INTEGER),
    Field("Stream", "stream.webrtc_port", "WebRTC port", FieldKind.INTEGER),
    Field("Stream", "stream.viewer_port", "Viewer page port", FieldKind.INTEGER),
    Field("Stream", "stream.youtube_enabled", "Push to YouTube", FieldKind.CHECK),
    Field("Stream", "stream.youtube_ingest_url", "YouTube address", FieldKind.TEXT),
    Field("Overlays", "overlays.datetime.enabled", "Show date", FieldKind.CHECK),
    Field(
        "Overlays",
        "overlays.datetime.position",
        "Date position",
        FieldKind.CHOICE,
        CORNERS,
    ),
    Field("Overlays", "overlays.datetime.scale", "Date size", FieldKind.NUMBER),
    Field("Overlays", "overlays.datetime.colour", "Date colour", FieldKind.TEXT),
    Field("Overlays", "overlays.datetime.format", "Date format", FieldKind.TEXT),
    Field("Overlays", "overlays.countdown.enabled", "Show countdown", FieldKind.CHECK),
    Field(
        "Overlays",
        "overlays.countdown.position",
        "Countdown position",
        FieldKind.CHOICE,
        CORNERS,
    ),
    Field("Overlays", "overlays.countdown.scale", "Countdown size", FieldKind.NUMBER),
    Field("Overlays", "overlays.countdown.colour", "Countdown colour", FieldKind.TEXT),
    Field(
        "Overlays",
        "overlays.countdown.count_up_colour",
        "Count-up colour",
        FieldKind.TEXT,
    ),
    Field(
        "Overlays",
        "overlays.countdown.count_up_minutes",
        "Count-up minutes",
        FieldKind.NUMBER,
    ),
    Field("Overlays", "overlays.flags.enabled", "Show flags", FieldKind.CHECK),
    Field(
        "Overlays",
        "overlays.flags.position",
        "Flags position",
        FieldKind.CHOICE,
        CORNERS,
    ),
    Field("Overlays", "overlays.flags.scale", "Flags size", FieldKind.NUMBER),
    Field("Overlays", "overlays.flags.show_labels", "Flag labels", FieldKind.CHECK),
    Field("Overlays", "overlays.flags.custom_directory", "Flag images", FieldKind.TEXT),
)


def _get(data: dict[str, Any], path: str) -> Any:  # noqa: ANN401 - settings of any type
    """Return a nested value by its dotted path."""
    value = data
    for part in path.split("."):
        value = value[part]
    return value


def _set(data: dict[str, Any], path: str, value: object) -> None:
    """Set a nested value by its dotted path."""
    *parents, last = path.split(".")
    for part in parents:
        data = data[part]
    data[last] = value


def read_fields(config: Config) -> dict[str, str | bool]:
    """Return what the form shows for a configuration."""
    data = config.model_dump(mode="json")
    values: dict[str, str | bool] = {}
    for field in FIELDS:
        value = _get(data, field.path)
        if field.kind is FieldKind.CHECK:
            values[field.path] = value
        elif field.kind is FieldKind.LIST:
            values[field.path] = ", ".join(value)
        else:
            values[field.path] = "" if value is None else str(value)
    return values


def _parse(field: Field, value: str | bool) -> object:
    """Turn what's on the form into a stored value.

    Raises:
        ValueError: With a message for the user, if the text isn't the right kind.
    """
    if field.kind is FieldKind.CHECK:
        return value
    text = str(value).strip()
    if field.kind is FieldKind.OPTIONAL_TEXT:
        return text or None
    if field.kind is FieldKind.LIST:
        return [item.strip() for item in text.split(",") if item.strip()]
    if field.kind is FieldKind.INTEGER:
        try:
            return int(text)
        except ValueError:
            raise ValueError("Enter a whole number") from None
    if field.kind is FieldKind.NUMBER:
        try:
            return float(text)
        except ValueError:
            raise ValueError("Enter a number") from None
    return text


def apply_fields(
    config: Config, values: dict[str, str | bool]
) -> tuple[Config | None, dict[str, str]]:
    """Return a new configuration from the form, or the problems with it.

    Settings that aren't on the form, such as the sequences, are kept.

    Args:
        config: The configuration being edited, which isn't changed.
        values: What's on the form.

    Returns:
        The new configuration, or None, and the errors by field path. Errors that aren't
        about one field are under GENERAL_ERRORS.
    """
    data = config.model_dump(mode="json")
    errors: dict[str, str] = {}
    for field in FIELDS:
        try:
            _set(data, field.path, _parse(field, values[field.path]))
        except ValueError as error:
            errors[field.path] = str(error)
    if errors:
        return None, errors
    try:
        return Config.model_validate_json(json.dumps(data)), {}
    except ValidationError as error:
        return None, _errors_by_field(error)


def _errors_by_field(error: ValidationError) -> dict[str, str]:
    """Return validation errors under the fields they belong to."""
    paths = {field.path for field in FIELDS}
    errors: dict[str, str] = {}
    for item in error.errors(include_input=False, include_url=False):
        location = ".".join(str(part) for part in item["loc"])
        message = item["msg"].removeprefix("Value error, ")
        while location not in paths and "." in location:
            location = location.rsplit(".", 1)[0]
        if location not in paths:
            message = f"{location}: {message}" if location else message
            location = GENERAL_ERRORS
        previous = errors.get(location)
        errors[location] = f"{previous}\n{message}" if previous else message
    return errors


class ConfigDocument:
    """The configuration being edited, and the file it belongs to."""

    def __init__(self, config: Config, path: Path | None = None) -> None:
        """Hold the configuration.

        Args:
            config: The configuration.
            path: Its file, if it has one.
        """
        self.config = config
        self.path = path
        self.modified = False

    @classmethod
    def open(cls, path: Path) -> "ConfigDocument":
        """Return the document for a file.

        Raises:
            ConfigError: If the file can't be read or isn't valid.
        """
        return cls(load_config(path), path)

    @property
    def name(self) -> str:
        """Return a name for the window's title."""
        name = self.path.name if self.path else "Untitled"
        return f"{name} *" if self.modified else name

    def update(self, config: Config) -> bool:
        """Change the configuration, and return whether that's a real change."""
        if config == self.config:
            return False
        self.config = config
        self.modified = True
        return True

    def save(self, path: Path | None = None) -> None:
        """Save to a file, by default the document's own.

        Raises:
            ConfigError: If it can't be saved.
            ValueError: If the document has no file yet, and none was given.
        """
        target = path or self.path
        if target is None:
            raise ValueError("The configuration has no file yet")
        save_config(self.config, target)
        self.path = target
        self.modified = False
