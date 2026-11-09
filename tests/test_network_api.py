#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_network_api.py
#  Description: Tests for the network interface, over HTTPS.
#  Date: 2026-11-09
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: pytest
#
#########################################################

"""Tests for the network interface, over HTTPS."""

#
# SYSTEM IMPORTS
#

import http.client
import importlib.util
import json
import logging
import re
import socket
import ssl
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

#
# PROJECT IMPORTS
#
from start_camera import api, control_page
from start_camera.api import CameraSystem
from start_camera.config import (
    Config,
    ControlConfig,
    Secrets,
    SequenceConfig,
    SequencesConfig,
    StreamConfig,
)
from start_camera.control_page import ensure_certificate
from start_camera.ingest import Frame
from start_camera.openapi import OPENAPI

#
#  GLOBAL VARIABLES
#

TOKEN = "full-token-for-testing-0123456789"
READ_TOKEN = "read-token-for-testing-0123456789"
PASSWORD = "phone-password"
DAY = {"items": [{"name": "Lasers", "class_flag": "L"}]}

#
# FUNCTIONS
#


class FakeSource:
    """Stand in for the camera."""

    def __init__(self, config: Config, _: Secrets) -> None:
        """Make a frame."""
        image = np.zeros((config.stream.height, config.stream.width, 3), np.uint8)
        self.frame = Frame(image, 0.0, 1, True)

    def start(self) -> None:
        """Do nothing."""

    def stop(self) -> None:
        """Do nothing."""

    def latest(self) -> Frame:
        """Return the frame."""
        return self.frame


class FakeStreaming:
    """Stand in for the stream."""

    def __init__(self, *_: object) -> None:
        """Start stopped."""
        self.recorder = None
        self.youtube_state = "connecting"

    def start(self) -> None:
        """Do nothing."""

    def stop(self) -> None:
        """Do nothing."""

    def use_sequences(self, _: SequencesConfig) -> None:
        """Do nothing."""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def certificates(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return a folder with a certificate, shared as creating one is slow."""
    directory = tmp_path_factory.mktemp("certificates")
    ensure_certificate(directory, "127.0.0.1")
    return directory


@dataclass
class Server:
    """A running camera system, and a way to talk to its network interface."""

    system: CameraSystem
    port: int

    def request(
        self,
        method: str,
        path: str,
        body: object = None,
        token: str | None = TOKEN,
        headers: dict[str, str] | None = None,
        raw: bytes | None = None,
    ) -> tuple[int, Any]:
        """Make a request and return the status and the JSON body."""
        all_headers = dict(headers or {})
        if token is not None:
            all_headers["Authorization"] = f"Bearer {token}"
        data = raw if raw is not None else (
            None if body is None else json.dumps(body).encode()
        )
        connection = self.connect()
        connection.request(method, path, body=data, headers=all_headers)
        response = connection.getresponse()
        content = response.read()
        connection.close()
        return response.status, json.loads(content)

    def connect(self) -> http.client.HTTPSConnection:
        """Return a connection to the server."""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return http.client.HTTPSConnection(
            "127.0.0.1", self.port, context=context, timeout=5
        )


def run_server(
    tmp_path: Path,
    certificates: Path,
    monkeypatch: pytest.MonkeyPatch,
    secrets: Secrets,
    synchronised: bool = True,
) -> Iterator[Server]:
    """Run a camera system with its network interface."""
    monkeypatch.setattr(control_page, "CERTIFICATE_DIRECTORY", certificates)
    monkeypatch.setattr(api, "Streaming", FakeStreaming)
    config = Config(
        stream=StreamConfig(width=64, height=36),
        control=ControlConfig(
            journal_path=str(tmp_path / "journal.jsonl"), page_port=_free_port()
        ),
        sequences=SequencesConfig(items=[SequenceConfig(name="Solo", class_flag="S")]),
    )
    system = CameraSystem(
        config,
        secrets,
        make_source=FakeSource,
        clock_synchronised=lambda: synchronised,
    )
    with system:
        yield Server(system, config.control.page_port)


@pytest.fixture
def server(
    tmp_path: Path, certificates: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Server]:
    """Run with both tokens and the phone page."""
    secrets = Secrets(
        api_token=TOKEN, api_read_token=READ_TOKEN, control_password=PASSWORD
    )
    yield from run_server(tmp_path, certificates, monkeypatch, secrets)


def test_description_is_served_without_a_token(server: Server) -> None:
    status, body = server.request("GET", "/api/v1/openapi.json", token=None)
    assert status == 200
    assert body == json.loads(json.dumps(OPENAPI))


@pytest.mark.parametrize("token", [None, "wrong", TOKEN + "x", ""])
def test_requests_without_the_token_are_refused(server: Server, token: str) -> None:
    for method, path in (("GET", "/api/v1/status"), ("POST", "/api/v1/race/abandon")):
        status, body = server.request(method, path, {}, token=token)
        assert status == 401
        assert body["code"] == "unauthorised"
    assert server.system.status().phase.value == "idle"


def test_repeated_wrong_tokens_lock_the_address_out(server: Server) -> None:
    for _ in range(5):
        server.request("GET", "/api/v1/status", token="wrong")
    status, body = server.request("GET", "/api/v1/status")
    assert status == 429
    assert body["code"] == "locked_out"


def test_tokens_arent_logged(server: Server, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        server.request("GET", "/api/v1/status")
        server.request("GET", "/api/v1/status", token="wrong-token-guess")
    assert TOKEN not in caplog.text
    assert "wrong-token-guess" not in caplog.text


def test_status(server: Server) -> None:
    status, body = server.request("GET", "/api/v1/status")
    assert status == 200
    assert body["phase"] == "idle"
    assert body["camera_live"] is True
    assert body["streaming"] is False
    assert body["youtube"] == "off"
    assert body["clock"]["synchronised"] is True
    assert body["viewers"] == {}


def test_race_commands_come_from_the_suite(server: Server) -> None:
    status, body = server.request("POST", "/api/v1/race/start_now")
    assert status == 200
    assert body["status"]["phase"] == "in sequence"
    assert body["status"]["upcoming"]["name"] == "Solo"
    status, body = server.request("POST", "/api/v1/race/shift", {"minutes": 2})
    assert status == 200
    events = server.request("GET", "/api/v1/events")[1]["events"]
    schedule = [e for e in events if e["kind"] == "schedule"]
    assert [e["details"]["source"] for e in schedule] == ["suite", "suite"]
    assert schedule[1]["details"]["arguments"] == {"minutes": 2}


def test_refused_command_explains_why(server: Server) -> None:
    status, body = server.request("POST", "/api/v1/race/resume")
    assert status == 409
    assert body["code"] == "refused"
    assert "isn't postponed" in body["error"]


@pytest.mark.parametrize("minutes", [None, "5", True, [1]])
def test_shift_needs_a_number(server: Server, minutes: object) -> None:
    status, body = server.request("POST", "/api/v1/race/shift", {"minutes": minutes})
    assert status == 400
    assert body["code"] == "bad_request"


def test_set_start_by_iso_time(server: Server) -> None:
    start = datetime.fromtimestamp(server.system.status().time + 600, UTC)
    status, body = server.request(
        "POST",
        "/api/v1/race/set_start",
        {
            "start": start.isoformat(),
            "name": "Solo",
            "sent_at": datetime.now(UTC).isoformat(),
        },
    )
    assert status == 200
    assert body["status"]["upcoming"]["start"] == pytest.approx(start.timestamp())
    assert body["status"]["clock"]["last_difference"] is not None


@pytest.mark.parametrize(
    ("start", "code"),
    [
        ("2026-11-09T10:00:00", "bad_request"),
        ("tomorrow", "bad_request"),
        (None, "bad_request"),
        (1.0, "refused"),
    ],
)
def test_bad_start_times(server: Server, start: object, code: str) -> None:
    status, body = server.request("POST", "/api/v1/race/set_start", {"start": start})
    assert body["code"] == code
    assert status == (409 if code == "refused" else 400)


def test_start_times_need_ntp(
    tmp_path: Path, certificates: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = Secrets(api_token=TOKEN)
    for running in run_server(
        tmp_path, certificates, monkeypatch, secrets, synchronised=False
    ):
        status, body = running.request(
            "POST", "/api/v1/race/set_start", {"start": 4_000_000_000}
        )
        assert status == 409
        assert body["code"] == "clock_not_synchronised"


def test_sequences(server: Server) -> None:
    status, body = server.request("POST", "/api/v1/sequences", {"sequences": DAY})
    assert status == 200
    assert body["warnings"] == []
    status, body = server.request("GET", "/api/v1/sequences")
    assert body["sequences"]["items"][0]["name"] == "Lasers"


def test_invalid_sequences_list_each_problem(server: Server) -> None:
    bad = {"items": [{"name": "", "class_flag": "L", "minutes_after_previous": 0}]}
    status, body = server.request("POST", "/api/v1/sequences", {"sequences": bad})
    assert status == 422
    assert body["code"] == "invalid_sequences"
    assert len(body["problems"]) == 2


@pytest.mark.parametrize(
    "body", [{}, {"sequences": []}, {"sequences": DAY, "force": "yes"}]
)
def test_malformed_sequences_requests(server: Server, body: object) -> None:
    assert server.request("POST", "/api/v1/sequences", body)[0] == 400


def test_sequences_while_under_way_need_force(server: Server) -> None:
    server.request("POST", "/api/v1/race/start_now")
    status, body = server.request("POST", "/api/v1/sequences", {"sequences": DAY})
    assert (status, body["code"]) == (409, "refused")
    request = {"sequences": DAY, "force": True}
    assert server.request("POST", "/api/v1/sequences", request)[0] == 200


def test_stream_and_recording(server: Server) -> None:
    status, body = server.request("POST", "/api/v1/recording/start")
    assert (status, body["code"]) == (409, "recording")
    status, body = server.request("POST", "/api/v1/stream/start")
    assert status == 200
    assert body["status"]["streaming"] is True
    assert body["status"]["youtube"] == "connecting"
    assert body["status"]["viewers"]
    status, body = server.request("POST", "/api/v1/stream/stop")
    assert body["status"]["streaming"] is False


def test_unknown_paths(server: Server) -> None:
    assert server.request("GET", "/api/v1/nothing")[0] == 404
    assert server.request("POST", "/api/v1/race/explode")[0] == 404
    assert server.request("GET", "/api/v2/status")[0] == 404


def test_read_only_token_can_only_read(server: Server) -> None:
    assert server.request("GET", "/api/v1/status", token=READ_TOKEN)[0] == 200
    assert server.request("GET", "/api/v1/events", token=READ_TOKEN)[0] == 200
    for path in ("/api/v1/race/start_now", "/api/v1/stream/start"):
        status, body = server.request("POST", path, {}, token=READ_TOKEN)
        assert (status, body["code"]) == (403, "read_only")
    assert server.system.status().phase.value == "idle"


def test_large_bodies_are_refused(server: Server) -> None:
    raw = b'{"sequences": "' + b"x" * 70_000 + b'"}'
    status, _ = server.request("POST", "/api/v1/sequences", raw=raw)
    assert status == 413


def test_requests_from_another_site_are_refused(server: Server) -> None:
    status, _ = server.request(
        "POST", "/api/v1/race/abandon", headers={"Origin": "https://evil.example"}
    )
    assert status == 403


def test_events_since_a_number(server: Server) -> None:
    server.request("POST", "/api/v1/race/start_now")
    events = server.request("GET", "/api/v1/events")[1]["events"]
    assert events[0]["number"] == 1
    later = server.request("GET", "/api/v1/events?since=1")[1]["events"]
    assert [e["number"] for e in later] == [e["number"] for e in events[1:]]
    assert server.request("GET", "/api/v1/events?since=x")[0] == 400


def read_events(response: http.client.HTTPResponse, count: int) -> list[dict]:
    """Return the next Server-Sent Events from a stream."""
    events: list[dict] = []
    fields: dict[str, str] = {}
    while len(events) < count:
        line = response.readline().decode().rstrip("\n")
        if line == "":
            if "data" in fields:
                events.append({**json.loads(fields["data"]), "_id": fields["id"]})
            fields = {}
        elif not line.startswith(":"):
            name, _, value = line.partition(": ")
            fields[name] = value
    return events


def test_event_stream(server: Server) -> None:
    server.request("POST", "/api/v1/race/start_now")
    connection = server.connect()
    connection.request(
        "GET", "/api/v1/events/stream", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    response = connection.getresponse()
    assert response.status == 200
    assert response.getheader("Content-Type") == "text/event-stream"
    first = read_events(response, 3)
    assert [e["kind"] for e in first] == ["schedule", "flag_up", "phase"]
    assert first[0]["_id"] == "1"
    server.request("POST", "/api/v1/race/postpone")
    later = read_events(response, 1)
    assert later[0]["kind"] == "schedule"
    assert later[0]["details"]["command"] == "postpone"
    connection.close()


def test_event_stream_resumes_after_the_last_event_id(server: Server) -> None:
    server.request("POST", "/api/v1/race/start_now")
    connection = server.connect()
    connection.request(
        "GET",
        "/api/v1/events/stream",
        headers={"Authorization": f"Bearer {READ_TOKEN}", "Last-Event-ID": "1"},
    )
    response = connection.getresponse()
    assert read_events(response, 1)[0]["number"] == 2
    connection.close()


def test_event_stream_needs_a_token(server: Server) -> None:
    status, _ = server.request("GET", "/api/v1/events/stream", token=None)
    assert status == 401


def test_phone_page_works_alongside(server: Server) -> None:
    connection = server.connect()
    connection.request(
        "POST", "/api/login", body=json.dumps({"password": PASSWORD}).encode()
    )
    response = connection.getresponse()
    response.read()
    cookie = response.getheader("Set-Cookie").split(";")[0]
    connection.request(
        "POST",
        "/api/command",
        body=json.dumps({"command": "start_now"}).encode(),
        headers={"Cookie": cookie},
    )
    assert connection.getresponse().status == 200
    connection.close()
    events = server.request("GET", "/api/v1/events")[1]["events"]
    assert events[0]["details"]["source"] == "phone"
    # The token doesn't open the phone page's own commands.
    status, _ = server.request("POST", "/api/command", {"command": "abandon"})
    assert status == 401


def test_without_a_password_only_the_interface_is_served(
    tmp_path: Path, certificates: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for running in run_server(
        tmp_path, certificates, monkeypatch, Secrets(api_token=TOKEN)
    ):
        assert running.request("GET", "/", token=None)[0] == 404
        assert running.request("GET", "/api/v1/status")[0] == 200


def test_without_a_token_the_interface_is_off(
    tmp_path: Path, certificates: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = Secrets(control_password=PASSWORD)
    for running in run_server(tmp_path, certificates, monkeypatch, secrets):
        status, body = running.request("GET", "/api/v1/status")
        assert status == 404
        assert "off" in body["error"]


def test_read_only_interface(
    tmp_path: Path, certificates: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = Secrets(api_read_token=READ_TOKEN)
    for running in run_server(tmp_path, certificates, monkeypatch, secrets):
        assert running.request("GET", "/api/v1/status", token=READ_TOKEN)[0] == 200
        status, _ = running.request("POST", "/api/v1/race/start_now", token=READ_TOKEN)
        assert status == 403


def test_short_tokens_are_refused() -> None:
    with pytest.raises(ValidationError, match="api_token"):
        Secrets(api_token="short")


def test_example_client_runs_a_start(
    server: Server, certificates: Path, caplog: pytest.LogCaptureFixture
) -> None:
    spec = importlib.util.spec_from_file_location(
        "network_client",
        Path(__file__).parent.parent / "examples" / "network_client.py",
    )
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    context = example.ssl_context(str(certificates / "control_page.crt"), False)
    client = example.Client(f"https://127.0.0.1:{server.port}", TOKEN, context)
    with caplog.at_level(logging.INFO):
        example.run(client, minutes=0.03, stop_stream=True)
    assert "Started the stream" in caplog.text
    assert "start Test" in caplog.text
    assert "Stopped the stream" in caplog.text
    started = float(caplog.text.split("Started ")[-1].split(" seconds")[0])
    assert abs(started) < 0.01


def test_unexpected_errors_are_reported(
    server: Server, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken() -> None:
        raise KeyError("oops")

    monkeypatch.setattr(server.system, "skip", broken)
    status, body = server.request("POST", "/api/v1/race/skip")
    assert (status, body["code"]) == (500, "internal")
    assert "Network interface: couldn't answer" in caplog.text


def test_guide_lists_every_request_and_error_code() -> None:
    guide = (Path(__file__).parent.parent / "docs" / "network_api.md").read_text()
    for path in OPENAPI["paths"]:
        name = path.rsplit("/", 1)[-1]
        assert f"/{name}" in guide or f"`{name}`" in guide, path
    source = Path(api.__file__).with_name("network_api.py").read_text()
    for code in re.findall(r'ApiError\(\s*\d+, "(\w+)"', source):
        assert f"`{code}`" in guide, code
