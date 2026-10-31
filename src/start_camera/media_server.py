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
from start_camera.config import StreamConfig

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

#
# FUNCTIONS
#


class MediaServerError(Exception):
    """MediaMTX could not be fetched."""


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


def log_output(lines: Iterable[bytes], name: str) -> None:
    """Log a process's output in the background.

    Only use this for processes whose output can't contain secrets.
    """

    def run() -> None:
        for line in lines:
            logger.warning("%s: %s", name, line.decode(errors="replace").rstrip())

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


def build_server_config(stream: StreamConfig) -> dict[str, Any]:
    """Return the MediaMTX configuration.

    Anyone on the network can watch, but only this computer can publish, so other
    devices can't replace the stream. Unused servers are off.
    """
    return {
        "logLevel": "warn",
        "logDestinations": ["stdout"],
        "api": False,
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
                "permissions": [{"action": "publish", "path": ""}],
            },
        ],
        "paths": {STREAM_PATH: {}},
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


class MediaServer:
    """Run MediaMTX, restarting it if it exits."""

    def __init__(self, stream: StreamConfig, binary: Path) -> None:
        """Prepare the server.

        Args:
            stream: The output settings.
            binary: The MediaMTX program.
        """
        self._stream = stream
        self._binary = binary
        self._directory = tempfile.TemporaryDirectory(prefix="start_camera_")
        self._config_path = Path(self._directory.name) / "mediamtx.yml"
        # JSON is valid YAML, so no YAML library is needed.
        self._config_path.write_text(
            json.dumps(build_server_config(stream)), encoding="utf-8"
        )
        self._process: subprocess.Popen[bytes] | None = None
        self._started_at = 0.0
        self._backoff = Backoff()

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
            [str(self._binary), str(self._config_path)],
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
