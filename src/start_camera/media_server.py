#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: media_server.py
#  Description: Fetch, configure and supervise MediaMTX.
#  Date: 2026-10-31
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: MediaMTX
#
#########################################################

"""Fetch, configure and supervise MediaMTX, which serves the stream to viewers."""

#
# SYSTEM IMPORTS
#

import hashlib
import json
import logging
import platform
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#
# PROJECT IMPORTS
#
from start_camera.config import RecordingConfig, RecordingFormat, StreamConfig

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

MEDIAMTX_VERSION = "v1.21.3"
MEDIAMTX_CHECKSUMS = {
    "x86_64": (
        "amd64",
        "0a2ea7eba96023631efa1bea7804823a9145b043b6646a8a05798d4ac24ed635",
    ),
    "aarch64": (
        "arm64",
        "27f038237cb5f77a724f6afc58eca2798f2bc4edf9989895631a80eeb41a4fe2",
    ),
}
MEDIAMTX_URL = (
    "https://github.com/bluenviron/mediamtx/releases/download/{version}/"
    "mediamtx_{version}_linux_{architecture}.tar.gz"
)
CACHE_DIRECTORY = Path.home() / ".cache" / "start_camera"
STREAM_PATH = "start"
DOWNLOAD_TIMEOUT_S = 60
STABLE_AFTER_S = 30.0
API_TIMEOUT_S = 5.0
RECORDING_FORMATS = {RecordingFormat.MP4: "fmp4", RecordingFormat.MPEG_TS: "mpegts"}

#
# FUNCTIONS
#


class MediaServerError(Exception):
    """MediaMTX could not be fetched or controlled."""


@dataclass
class Backoff:
    """Delay retries of a process that keeps failing."""

    initial_s: float = 1.0
    maximum_s: float = 10.0
    delay_s: float = field(init=False, default=0.0)
    retry_at: float = field(init=False, default=0.0)

    def ready(self, now: float) -> bool:
        """Return whether it's time to try again."""
        return now >= self.retry_at

    def failed(self, now: float, ran_for_s: float) -> None:
        """Schedule the next try, starting afresh if the process had been stable."""
        if ran_for_s >= STABLE_AFTER_S or self.delay_s == 0.0:
            self.delay_s = self.initial_s
        else:
            self.delay_s = min(self.delay_s * 2, self.maximum_s)
        self.retry_at = now + self.delay_s


def log_output(lines: Iterable[bytes], name: str, redact: Iterable[str] = ()) -> None:
    """Log a process's output in the background.

    Args:
        lines: The process's output.
        name: What to call the process in the logs.
        redact: Secrets that must not reach the logs.
    """
    secrets = [secret for secret in redact if secret]

    def run() -> None:
        for line in lines:
            text = line.decode(errors="replace").rstrip()
            for secret in secrets:
                text = text.replace(secret, "***")
            logger.warning("%s: %s", name, text)

    threading.Thread(target=run, daemon=True, name=f"{name}-log").start()


def _download(url: str, destination: Path) -> None:
    """Download a file."""
    try:
        with (
            urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_S) as reply,
            destination.open("wb") as file,
        ):
            shutil.copyfileobj(reply, file)
    except (urllib.error.URLError, OSError) as error:
        raise MediaServerError(f"Can't download MediaMTX: {error}") from error


def _unpack_binary(archive: Path, destination: Path) -> None:
    """Move the mediamtx program out of an archive, ready to run."""
    temporary = destination.with_suffix(".tmp")
    try:
        with tarfile.open(archive) as tar, temporary.open("wb") as file:
            member = tar.extractfile("mediamtx")
            if member is None:
                raise MediaServerError("The MediaMTX archive has no program in it")
            shutil.copyfileobj(member, file)
    except (tarfile.TarError, KeyError) as error:
        raise MediaServerError(f"Can't unpack MediaMTX: {error}") from error
    temporary.chmod(0o755)
    temporary.replace(destination)


def ensure_binary(cache_directory: Path = CACHE_DIRECTORY) -> Path:
    """Return the path of the pinned MediaMTX, downloading it if needed.

    Args:
        cache_directory: Where to keep the program.

    Returns:
        The path of the program.

    Raises:
        MediaServerError: If this platform isn't supported, or the download fails or
            doesn't match its checksum.
    """
    binary = cache_directory / f"mediamtx-{MEDIAMTX_VERSION}"
    if binary.exists():
        return binary
    machine = platform.machine()
    if machine not in MEDIAMTX_CHECKSUMS:
        raise MediaServerError(f"MediaMTX isn't available for {machine}")
    architecture, checksum = MEDIAMTX_CHECKSUMS[machine]
    url = MEDIAMTX_URL.format(version=MEDIAMTX_VERSION, architecture=architecture)
    logger.info("Downloading MediaMTX %s", MEDIAMTX_VERSION)
    cache_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache_directory) as directory:
        archive = Path(directory) / "mediamtx.tar.gz"
        _download(url, archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != checksum:
            raise MediaServerError("The MediaMTX download failed its checksum")
        _unpack_binary(archive, binary)
    return binary


def recording_settings(recording: RecordingConfig, record: bool) -> dict[str, Any]:
    """Return the MediaMTX settings for recording.

    Recordings copy the stream without encoding it again. The folder is made absolute,
    as MediaMTX runs in a temporary folder that's deleted when it stops.
    """
    directory = Path(recording.directory).expanduser().resolve()
    days = recording.delete_after_days
    return {
        "record": record,
        # MediaMTX insists on the stream's name, and warns without the time zone.
        "recordPath": str(directory / "%Y-%m-%d_%H-%M-%S%z_%path"),
        "recordFormat": RECORDING_FORMATS[recording.format],
        "recordPartDuration": "1s",
        "recordSegmentDuration": f"{recording.segment_minutes}m",
        "recordDeleteAfter": f"{days * 24}h" if days else "0s",
    }


def build_server_config(
    stream: StreamConfig,
    recording: RecordingConfig | None = None,
    *,
    api_port: int | None = None,
    record: bool = False,
) -> dict[str, Any]:
    """Return the MediaMTX configuration.

    Anyone on the network can watch, but only this computer can publish or use the
    control interface, so other devices can't replace the stream or control recording.
    Unused servers are off.

    Args:
        stream: The output settings.
        recording: The recording settings, if recording can be used.
        api_port: The control interface's port, which listens on this computer only.
        record: Whether to record.
    """
    local_permissions = [{"action": "publish", "path": ""}]
    if api_port is not None:
        local_permissions.append({"action": "api", "path": ""})
    path_settings = recording_settings(recording, record) if recording else {}
    return {
        "logLevel": "warn",
        "logDestinations": ["stdout"],
        "api": api_port is not None,
        "apiAddress": f"127.0.0.1:{api_port or 9997}",
        "metrics": False,
        "pprof": False,
        "playback": False,
        "rtmp": False,
        "srt": False,
        "moq": False,
        "rtsp": True,
        "rtspAddress": f":{stream.rtsp_port}",
        "rtspTransports": ["tcp"],
        "hls": True,
        "hlsAddress": f":{stream.hls_port}",
        "hlsVariant": "mpegts",
        "hlsAlwaysRemux": True,
        "webrtc": True,
        "webrtcAddress": f":{stream.webrtc_port}",
        "authInternalUsers": [
            {
                "user": "any",
                "pass": "",
                "ips": [],
                "permissions": [{"action": "read", "path": ""}],
            },
            {
                "user": "any",
                "pass": "",
                "ips": ["127.0.0.1"],
                "permissions": local_permissions,
            },
        ],
        "paths": {STREAM_PATH: path_settings},
    }


def publish_url(stream: StreamConfig) -> str:
    """Return the local address that the encoder publishes to."""
    return f"rtsp://127.0.0.1:{stream.rtsp_port}/{STREAM_PATH}"


def viewer_urls(stream: StreamConfig, address: str) -> dict[str, str]:
    """Return the addresses that viewers can open, by use."""
    return {
        "TV or browser": f"http://{address}:{stream.viewer_port}/",
        "Lowest delay browser": f"http://{address}:{stream.viewer_port}/live",
        "TV media player": f"http://{address}:{stream.viewer_port}/stream.m3u8",
        "RTSP player": f"rtsp://{address}:{stream.rtsp_port}/{STREAM_PATH}",
    }


def _free_local_port() -> int:
    """Return a port that's free on this computer now."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _end_with_parent() -> list[str]:
    """Return a command prefix that ends a process if the application is killed.

    Otherwise, a server left behind would hold the ports until it was stopped by hand.
    The other processes end by themselves when their pipes to the application close.
    """
    setpriv = shutil.which("setpriv")
    return [setpriv, "--pdeathsig", "KILL"] if setpriv else []


class MediaServer:
    """Run MediaMTX, restarting it if it exits."""

    def __init__(
        self,
        stream: StreamConfig,
        binary: Path,
        recording: RecordingConfig | None = None,
    ) -> None:
        """Prepare the server.

        Args:
            stream: The output settings.
            binary: The MediaMTX program.
            recording: The recording settings, if recording can be used.
        """
        self._stream = stream
        self._binary = binary
        self._recording = recording
        self._api_port = _free_local_port() if recording else None
        self._record = False
        self._directory = tempfile.TemporaryDirectory(prefix="start_camera_")
        self._config_path = Path(self._directory.name) / "mediamtx.yml"
        self._write_config()
        self._process: subprocess.Popen[bytes] | None = None
        self._started_at = 0.0
        self._backoff = Backoff()

    def _write_config(self) -> None:
        """Write the configuration, which MediaMTX also reads if it restarts."""
        settings = build_server_config(
            self._stream, self._recording, api_port=self._api_port, record=self._record
        )
        temporary = self._config_path.with_suffix(".tmp")
        # JSON is valid YAML, so no YAML library is needed.
        temporary.write_text(json.dumps(settings), encoding="utf-8")
        temporary.replace(self._config_path)

    def set_recording(self, record: bool) -> None:
        """Start or stop recording, without interrupting the stream.

        Raises:
            MediaServerError: If recording isn't set up, or the server refused.
        """
        if self._recording is None:
            raise MediaServerError("Recording isn't set up")
        self._record = record
        self._write_config()
        if self._process is None or self._process.poll() is not None:
            # The server reads the new setting when it next starts.
            return
        request = urllib.request.Request(
            f"http://127.0.0.1:{self._api_port}/v3/config/paths/patch/{STREAM_PATH}",
            data=json.dumps({"record": record}).encode(),
            headers={"Content-Type": "application/json"},
            method="PATCH",
        )
        try:
            with urllib.request.urlopen(request, timeout=API_TIMEOUT_S):
                pass
        except (urllib.error.URLError, OSError) as error:
            message = f"MediaMTX didn't change recording: {error}"
            raise MediaServerError(message) from error

    def ensure_running(self) -> None:
        """Start the server if it isn't running and a retry is due."""
        now = time.monotonic()
        if self._process is not None:
            if self._process.poll() is None:
                return
            logger.warning(
                "MediaMTX exited with code %d; restarting", self._process.returncode
            )
            self._process.stdout.close()
            self._process = None
            self._backoff.failed(now, now - self._started_at)
        if self._backoff.ready(now):
            self._start(now)

    def _start(self, now: float) -> None:
        """Start the server process."""
        self._process = subprocess.Popen(
            [*_end_with_parent(), str(self._binary), str(self._config_path)],
            cwd=self._directory.name,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self._started_at = now
        log_output(self._process.stdout, "MediaMTX")

    def is_ready(self) -> bool:
        """Return whether the server is accepting connections for the stream."""
        if self._process is None or self._process.poll() is not None:
            return False
        with socket.socket() as sock:
            sock.settimeout(0.2)
            return sock.connect_ex(("127.0.0.1", self._stream.rtsp_port)) == 0

    def stop(self) -> None:
        """Stop the server."""
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            self._process = None
        self._directory.cleanup()
