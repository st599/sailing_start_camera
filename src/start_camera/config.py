#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: config.py
#  Description: JSON configuration shared by both applications.
#  Date: 2026-10-31
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: pydantic
#
#########################################################

"""JSON configuration shared by both applications.

The configuration file can be copied between computers, so it never holds secrets.
Secrets come from a separate file or from environment variables.
"""

#
# SYSTEM IMPORTS
#

import ipaddress
import json
import logging
import os
import tempfile
from collections.abc import Mapping
from datetime import time
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_STARTS = 10
MIN_SCAN_PREFIX = 20
CLASS_FLAG = "class"
PREPARATORY_FLAG = "preparatory"
SECRET_ENVIRONMENT = {
    "camera_username": "STARTCAM_CAMERA_USERNAME",
    "camera_password": "STARTCAM_CAMERA_PASSWORD",
    "youtube_stream_key": "STARTCAM_YOUTUBE_STREAM_KEY",
    "control_password": "STARTCAM_CONTROL_PASSWORD",
}

Port = Annotated[int, Field(ge=1, le=65535)]
Fraction = Annotated[float, Field(ge=0, le=1)]
Colour = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]

#
# FUNCTIONS
#


class ConfigError(Exception):
    """A configuration or secrets file could not be read, validated or saved."""


class StrictModel(BaseModel):
    """Base model that rejects unknown fields and values of the wrong type."""

    model_config = ConfigDict(extra="forbid", strict=True)


class Corner(StrEnum):
    """A corner of the video frame."""

    TOP_LEFT = "top_left"
    TOP_RIGHT = "top_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"


class LineFit(StrEnum):
    """How the start line is fitted through its points."""

    STRAIGHT = "straight"
    POLYNOMIAL = "polynomial"
    SPLINE = "spline"


class PreparatoryFlag(StrEnum):
    """Flags that can act as the preparatory signal."""

    P = "P"
    I = "I"  # noqa: E741 - the name of the flag
    Z = "Z"
    Z_AND_I = "Z+I"
    U = "U"
    BLACK = "black"


class GeneralRecallMode(StrEnum):
    """What happens to the other classes after a general recall."""

    DELAY_FOLLOWING = "delay_following"
    END_OF_QUEUE = "end_of_queue"


class FlagAction(StrEnum):
    """What happens to a flag at an event."""

    UP = "up"
    DOWN = "down"


class CameraConfig(StrictModel):
    """Where to find the camera. Credentials are held in the secrets."""

    host: str | None = Field(default=None, min_length=1)
    mac: str | None = Field(
        default=None, pattern=r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$"
    )
    rtsp_port: Port = 554
    rtsp_path: str = Field(default="/av_stream/ch0", min_length=1)
    capture_latency_ms: int = Field(default=0, ge=0, le=60000)
    scan_subnets: list[str] = Field(default_factory=list)

    @field_validator("scan_subnets")
    @classmethod
    def _subnets_can_be_scanned(cls, values: list[str]) -> list[str]:
        for value in values:
            subnet = ipaddress.IPv4Network(value, strict=False)
            if subnet.prefixlen < MIN_SCAN_PREFIX:
                raise ValueError(f"{value} is bigger than /{MIN_SCAN_PREFIX}")
        return values


class StreamConfig(StrictModel):
    """Output video settings and viewer ports."""

    width: int = Field(default=1280, ge=16, le=7680, multiple_of=2)
    height: int = Field(default=720, ge=16, le=4320, multiple_of=2)
    fps: int = Field(default=20, ge=1, le=60)
    bitrate_kbps: int = Field(default=2500, ge=100, le=50000)
    keyframe_interval_s: int = Field(default=2, ge=1, le=10)
    rtsp_port: Port = 8554
    hls_port: Port = 8888
    webrtc_port: Port = 8889
    viewer_port: Port = 8080
    youtube_enabled: bool = False
    youtube_ingest_url: str = Field(
        default="rtmps://a.rtmp.youtube.com/live2", pattern=r"^rtmps?://\S+$"
    )

    @model_validator(mode="after")
    def _ports_are_different(self) -> Self:
        ports = (self.rtsp_port, self.hls_port, self.webrtc_port, self.viewer_port)
        if len(set(ports)) != len(ports):
            raise ValueError("the stream's ports must all be different")
        return self


class TextOverlayConfig(StrictModel):
    """Settings shared by the text overlays."""

    enabled: bool = True
    position: Corner
    scale: float = Field(default=1.0, gt=0, le=10)
    colour: Colour = "#ffffff"


class DateTimeOverlayConfig(TextOverlayConfig):
    """The date and time overlay."""

    position: Corner = Corner.TOP_LEFT
    format: str = Field(default="%H:%M:%S %d/%m/%y", min_length=1)


class CountdownOverlayConfig(TextOverlayConfig):
    """The countdown and count-up overlay."""

    position: Corner = Corner.TOP_RIGHT
    count_up_colour: Colour = "#80ff80"
    count_up_minutes: float = Field(default=90, ge=0)


class FlagsOverlayConfig(StrictModel):
    """The start sequence flags overlay."""

    enabled: bool = True
    position: Corner = Corner.BOTTOM_RIGHT
    scale: float = Field(default=1.0, gt=0, le=10)
    show_labels: bool = True


class OverlaysConfig(StrictModel):
    """Settings for each overlay."""

    datetime: DateTimeOverlayConfig = Field(default_factory=DateTimeOverlayConfig)
    countdown: CountdownOverlayConfig = Field(default_factory=CountdownOverlayConfig)
    flags: FlagsOverlayConfig = Field(default_factory=FlagsOverlayConfig)


class LinePoint(StrictModel):
    """A point as fractions of the frame, so it survives resolution changes."""

    x: Fraction
    y: Fraction


class StartLineConfig(StrictModel):
    """The imaginary start line."""

    enabled: bool = False
    points: list[LinePoint] = Field(default_factory=list)
    fit: LineFit = LineFit.STRAIGHT
    polynomial_degree: int = Field(default=2, ge=2, le=5)
    colour: Colour = "#ff0000"
    width: int = Field(default=2, ge=1, le=20)
    opacity: Fraction = 0.8

    @model_validator(mode="after")
    def _enough_points(self) -> Self:
        needed = 2
        if self.fit is LineFit.POLYNOMIAL:
            needed = self.polynomial_degree + 1
        if self.enabled and len(self.points) < needed:
            raise ValueError(
                f"points: an enabled {self.fit.value} line needs at least "
                f"{needed} points, got {len(self.points)}"
            )
        return self


class FlagEvent(StrictModel):
    """A flag going up or down, at a time before the start."""

    minutes_before_start: float = Field(ge=0)
    flag: str = Field(min_length=1)
    action: FlagAction


def default_flag_events() -> list[FlagEvent]:
    """Return the events of the standard five minute start sequence."""
    return [
        FlagEvent(minutes_before_start=5, flag=CLASS_FLAG, action=FlagAction.UP),
        FlagEvent(minutes_before_start=4, flag=PREPARATORY_FLAG, action=FlagAction.UP),
        FlagEvent(
            minutes_before_start=1, flag=PREPARATORY_FLAG, action=FlagAction.DOWN
        ),
        FlagEvent(minutes_before_start=0, flag=CLASS_FLAG, action=FlagAction.DOWN),
    ]


class SequenceConfig(StrictModel):
    """One class's start sequence.

    An event's flag is "class", "preparatory" or the name of any other flag.
    """

    name: str = Field(min_length=1)
    class_flag: str = Field(min_length=1)
    preparatory_flag: PreparatoryFlag = PreparatoryFlag.P
    minutes_after_previous: float = Field(default=5, gt=0)
    events: list[FlagEvent] = Field(default_factory=default_flag_events, min_length=1)


class SequencesConfig(StrictModel):
    """The day's start sequences, in order.

    A first_start of None means the race officer starts the first sequence by hand.
    It is a local time of day. general_recall says whether later starts wait for the
    recalled class, or the recalled class goes to the end of the queue.
    """

    first_start: time | None = None
    general_recall: GeneralRecallMode = GeneralRecallMode.DELAY_FOLLOWING
    recall_flag_minutes: float = Field(default=1, gt=0)
    items: list[SequenceConfig] = Field(default_factory=list, max_length=MAX_STARTS)

    @field_validator("first_start")
    @classmethod
    def _first_start_is_local(cls, value: time | None) -> time | None:
        if value is not None and value.tzinfo is not None:
            raise ValueError("must be a local time, without a UTC offset")
        return value


class Config(StrictModel):
    """The complete application configuration."""

    version: int = SCHEMA_VERSION
    camera: CameraConfig = Field(default_factory=CameraConfig)
    stream: StreamConfig = Field(default_factory=StreamConfig)
    overlays: OverlaysConfig = Field(default_factory=OverlaysConfig)
    start_line: StartLineConfig = Field(default_factory=StartLineConfig)
    sequences: SequencesConfig = Field(default_factory=SequencesConfig)


class Secrets(StrictModel):
    """Credentials kept out of the configuration file."""

    camera_username: SecretStr | None = None
    camera_password: SecretStr | None = None
    youtube_stream_key: SecretStr | None = None
    control_password: SecretStr | None = None


def _validation_message(path: Path, error: ValidationError) -> str:
    """Describe validation errors by field, never quoting the offending value."""
    lines = [f"{path}: invalid configuration"]
    for item in error.errors(include_input=False, include_url=False):
        field = ".".join(str(part) for part in item["loc"]) or "(top level)"
        lines.append(f"  {field}: {item['msg']}")
    return "\n".join(lines)


def _read_json_object(path: Path) -> tuple[str, dict[str, Any]]:
    """Return a JSON file's text and its parsed top-level object."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(f"{path}: cannot read file: {error}") from error
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ConfigError(
            f"{path}: invalid JSON at line {error.lineno}, column {error.colno}: "
            f"{error.msg}"
        ) from error
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: the top level must be a JSON object")
    return text, data


def load_config(path: Path) -> Config:
    """Load and validate a configuration file.

    Args:
        path: The JSON configuration file.

    Returns:
        The validated configuration.

    Raises:
        ConfigError: If the file cannot be read, has an unsupported version or is
            invalid. The message names each field at fault.
    """
    text, data = _read_json_object(path)
    if "version" not in data:
        raise ConfigError(f"{path}: version: required field is missing")
    version = data["version"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise ConfigError(
            f"{path}: version: unsupported version {version!r}, "
            f"expected {SCHEMA_VERSION}"
        )
    try:
        return Config.model_validate_json(text)
    except ValidationError as error:
        raise ConfigError(_validation_message(path, error)) from error


def save_config(config: Config, path: Path) -> None:
    """Save a configuration file atomically.

    An existing file is replaced only once the new one is completely written, so a
    failure can't leave a damaged configuration.

    Args:
        config: The configuration to save.
        path: The destination file.

    Raises:
        ConfigError: If the file cannot be written.
    """
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(config.model_dump_json(indent=2) + "\n")
            file.flush()
            os.fsync(file.fileno())
        temporary.chmod(0o644)
        temporary.replace(path)
    except OSError as error:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise ConfigError(f"{path}: cannot save file: {error}") from error
    logger.info("Saved configuration to %s", path)


def load_secrets(
    path: Path | None = None, environ: Mapping[str, str] | None = None
) -> Secrets:
    """Load secrets from an optional file, then apply environment variables.

    Args:
        path: Optional JSON secrets file.
        environ: The environment to read. Defaults to the process environment.

    Returns:
        The secrets. Environment variables take priority over the file.

    Raises:
        ConfigError: If the file cannot be read or is invalid. The message never
            contains a secret value.
    """
    values: dict[str, Any] = {}
    if path is not None:
        _, values = _read_json_object(path)
        if path.stat().st_mode & 0o077:
            logger.warning("%s can be read by other users; use chmod 600", path)
    environ = os.environ if environ is None else environ
    for field, variable in SECRET_ENVIRONMENT.items():
        if environ.get(variable):
            values[field] = environ[variable]
    try:
        return Secrets.model_validate_json(json.dumps(values))
    except ValidationError as error:
        source = path if path is not None else Path("environment")
        raise ConfigError(_validation_message(source, error)) from error
