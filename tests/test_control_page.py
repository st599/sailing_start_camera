#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_control_page.py
#  Description: Tests for the phone control page.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: pytest, openssl
#
#########################################################

"""Tests for the phone control page, over real HTTPS connections."""

#
# SYSTEM IMPORTS
#

import http.client
import json
import logging
import shutil
import socket
import ssl
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

#
# PROJECT IMPORTS
#
from start_camera import control_page, pipeline
from start_camera.config import (
    CameraConfig,
    Config,
    ControlConfig,
    Secrets,
    SequenceConfig,
    SequencesConfig,
    StreamConfig,
)
from start_camera.control import RaceControl
from start_camera.control_page import (
    LOCKOUT_S,
    MAX_BODY_BYTES,
    MAX_LOGIN_FAILURES,
    PAGE,
    SESSION_LIFETIME_S,
    ControlPage,
    ControlPageError,
    ensure_certificate,
)

#
#  GLOBAL VARIABLES
#

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl")
T0 = datetime(2026, 11, 1, 12, 0).timestamp()
PASSWORD = "correct horse battery"

#
# FUNCTIONS
#


class Clock:
    """A clock that a test can set."""

    def __init__(self) -> None:
        """Set the time."""
        self.now = T0

    def __call__(self) -> float:
        """Return the time."""
        return self.now


@pytest.fixture(scope="session")
def certificates(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return a folder with a certificate, shared as creating one is slow."""
    directory = tmp_path_factory.mktemp("certificates")
    ensure_certificate(directory, "127.0.0.1")
    return directory


@dataclass
class Site:
    """A running page, and a way to talk to it."""

    page: ControlPage
    control: RaceControl
    clock: Clock
    cookie: str | None = None

    def request(
        self,
        method: str,
        path: str,
        body: object = None,
        headers: dict[str, str] | None = None,
        raw: bytes | None = None,
    ) -> tuple[int, Any, http.client.HTTPResponse]:
        """Make a request and return the status, the body and the response."""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        connection = http.client.HTTPSConnection(
            "127.0.0.1", self.page.port, context=context, timeout=5
        )
        all_headers = dict(headers or {})
        if self.cookie:
            all_headers["Cookie"] = self.cookie
        data = raw if raw is not None else (
            None if body is None else json.dumps(body).encode()
        )
        connection.request(method, path, body=data, headers=all_headers)
        response = connection.getresponse()
        content = response.read()
        connection.close()
        try:
            return response.status, json.loads(content), response
        except ValueError:
            return response.status, content.decode(), response

    def log_in(self, password: str = PASSWORD) -> int:
        """Log in, keeping the cookie, and return the status."""
        status, _, response = self.request(
            "POST", "/api/login", {"password": password}
        )
        cookie = response.getheader("Set-Cookie")
        if cookie:
            self.cookie = cookie.split(";")[0]
        return status

    def command(self, command: str, **options: object) -> tuple[int, Any]:
        """Send a command and return the status and body."""
        status, body, _ = self.request(
            "POST", "/api/command", {"command": command, **options}
        )
        return status, body


def make_config(**options: object) -> Config:
    """Return a configuration with two classes, on a free port."""
    items = [
        SequenceConfig(name="Solo", class_flag="Solo"),
        SequenceConfig(name="Topper", class_flag="T"),
    ]
    return Config(
        sequences=SequencesConfig(items=items),
        control=ControlConfig(page_port=8443),
        **options,
    )


@pytest.fixture
def site(tmp_path: Path, certificates: Path) -> Iterator[Site]:
    """Run the page on a free port."""
    yield from run_site(tmp_path, certificates)


def run_site(tmp_path: Path, certificates: Path, **options: object) -> Iterator[Site]:
    """Run the page for a configuration."""
    clock = Clock()
    config = make_config(**options)
    control = RaceControl(config.sequences, tmp_path / "journal.jsonl", clock)
    config.control.page_port = _free_port()
    page = ControlPage(
        config, control, PASSWORD, certificate_directory=certificates, clock=clock
    )
    try:
        yield Site(page, control, clock)
    finally:
        page.stop()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


def test_page_is_served_without_logging_in(site: Site) -> None:
    status, body, response = site.request("GET", "/")
    assert status == 200
    assert "Race control" in body
    assert response.getheader("Content-Type").startswith("text/html")
    assert response.getheader("X-Frame-Options") == "DENY"
    assert "default-src" in response.getheader("Content-Security-Policy")


def test_page_loads_nothing_from_the_internet() -> None:
    assert "http://" not in PAGE
    assert "https://" not in PAGE
    assert "src=" not in PAGE


def test_page_has_a_button_for_every_command() -> None:
    for command in control_page.SIMPLE_COMMANDS:
        assert command in PAGE


@pytest.mark.parametrize("label", ["Abandon the racing?", "General recall?", "Skip"])
def test_disruptive_buttons_ask_for_confirmation(label: str) -> None:
    assert label in PAGE


def test_plain_http_doesnt_work(site: Site) -> None:
    connection = http.client.HTTPConnection("127.0.0.1", site.page.port, timeout=5)
    with pytest.raises((OSError, http.client.HTTPException)):
        connection.request("GET", "/")
        connection.getresponse().read()
    connection.close()


def test_state_needs_a_login(site: Site) -> None:
    assert site.request("GET", "/api/state")[0] == 401


@pytest.mark.parametrize("command", ["abandon", "postpone", "start_now"])
def test_commands_are_refused_without_a_login(site: Site, command: str) -> None:
    assert site.command(command)[0] == 401
    assert site.control.state(T0).phase.value == "idle"


def test_commands_are_refused_with_a_made_up_session(site: Site) -> None:
    site.cookie = "startcam_session=made-up"
    assert site.command("abandon")[0] == 401


def test_commands_are_refused_with_a_malformed_cookie(site: Site) -> None:
    site.cookie = "startcam_session"
    assert site.command("abandon")[0] == 401


def test_wrong_password_is_refused(site: Site) -> None:
    assert site.log_in("wrong") == 401
    assert site.cookie is None
    assert site.command("abandon")[0] == 401


@pytest.mark.parametrize("password", [None, 123, [], ""])
def test_malformed_password_is_refused(site: Site, password: object) -> None:
    assert site.request("POST", "/api/login", {"password": password})[0] == 401


def test_non_ascii_password_is_handled(site: Site) -> None:
    assert site.log_in("pässwörd") == 401


def test_login_sets_a_protected_cookie(site: Site) -> None:
    _, _, response = site.request("POST", "/api/login", {"password": PASSWORD})
    cookie = response.getheader("Set-Cookie")
    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie


def test_each_login_gets_a_different_session(site: Site) -> None:
    site.log_in()
    first = site.cookie
    site.log_in()
    assert site.cookie != first


def test_repeated_failures_lock_the_address_out(site: Site) -> None:
    for _ in range(MAX_LOGIN_FAILURES):
        assert site.log_in("wrong") == 401
    assert site.log_in() == 429
    site.clock.now += LOCKOUT_S + 1
    assert site.log_in() == 200


def test_a_good_login_clears_the_failures(site: Site) -> None:
    for _ in range(MAX_LOGIN_FAILURES - 1):
        site.log_in("wrong")
    assert site.log_in() == 200
    for _ in range(MAX_LOGIN_FAILURES - 1):
        assert site.log_in("wrong") == 401
    assert site.log_in() == 200


def test_session_expires(site: Site) -> None:
    site.log_in()
    site.clock.now += SESSION_LIFETIME_S - 1
    assert site.request("GET", "/api/state")[0] == 200
    site.clock.now += 2
    assert site.request("GET", "/api/state")[0] == 401


def test_logout_ends_the_session(site: Site) -> None:
    site.log_in()
    assert site.request("POST", "/api/logout", {})[0] == 200
    assert site.request("GET", "/api/state")[0] == 401


def test_password_isnt_logged(site: Site, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        site.log_in()
        site.log_in("a-wrong-password")
    assert PASSWORD not in caplog.text
    assert "a-wrong-password" not in caplog.text
    assert "Failed login" in caplog.text


@pytest.mark.parametrize(
    ("first", "command", "phase"),
    [
        ([], "start_now", "in sequence"),
        (["start_now"], "postpone", "postponed"),
        (["start_now", "postpone"], "resume", "waiting"),
        (["start_now"], "abandon", "abandoned"),
    ],
)
def test_commands_work(
    site: Site, first: list[str], command: str, phase: str
) -> None:
    site.log_in()
    for item in first:
        site.command(item)
    status, body = site.command(command)
    assert status == 200
    assert body["phase"] == phase


def test_skip_moves_to_the_next_start(site: Site) -> None:
    site.log_in()
    site.command("start_now")
    site.command("skip")
    assert site.control.state(site.clock.now).upcoming.name == "Topper"


def test_recalls_work(site: Site) -> None:
    site.log_in()
    site.command("start_now")
    site.clock.now += 300
    assert "X" in site.command("individual_recall")[1]["flags"]
    assert "X" not in site.command("clear_individual_recall")[1]["flags"]
    assert "First Substitute" in site.command("general_recall")[1]["flags"]


@pytest.mark.parametrize("minutes", [1, 5, 10, -1])
def test_delay_steps_work(site: Site, minutes: int) -> None:
    site.log_in()
    site.command("start_now")
    status, _ = site.command("shift", minutes=minutes)
    assert status == 200
    expected = T0 + 300 + minutes * 60
    assert site.control.state(T0).upcoming.start == expected


@pytest.mark.parametrize("minutes", [2, 1000, -60, None, "1", [1]])
def test_other_delays_are_refused(site: Site, minutes: object) -> None:
    site.log_in()
    site.command("start_now")
    assert site.command("shift", minutes=minutes)[0] == 400
    assert site.control.state(T0).upcoming.start == T0 + 300


@pytest.mark.parametrize(
    "command", ["explode", "state", "_run", "__class__", None, 1]
)
def test_only_listed_commands_are_run(site: Site, command: object) -> None:
    site.log_in()
    status, body, _ = site.request("POST", "/api/command", {"command": command})
    assert status == 400
    assert body["error"] == "Unknown command"


def test_refused_command_explains_why(site: Site) -> None:
    site.log_in()
    status, body = site.command("resume")
    assert status == 409
    assert "isn't postponed" in body["error"]


def test_state_reflects_other_controls(site: Site) -> None:
    site.log_in()
    assert site.request("GET", "/api/state")[1]["display"] == "READY"
    site.control.start_now()
    state = site.request("GET", "/api/state")[1]
    assert state["display"] == "Solo -05:00"
    assert state["flags"] == ["Solo"]
    site.control.postpone()
    state = site.request("GET", "/api/state")[1]
    assert state["display"] == "POSTPONED"
    assert state["flags"] == ["AP"]


def test_state_lists_the_delay_steps(site: Site) -> None:
    site.log_in()
    assert site.request("GET", "/api/state")[1]["delay_steps"] == [1, 5, 10, -1]


def test_display_allows_for_the_camera_delay(
    tmp_path: Path, certificates: Path
) -> None:
    runner = run_site(
        tmp_path, certificates, camera=CameraConfig(capture_latency_ms=2000)
    )
    delayed = next(runner)
    delayed.log_in()
    delayed.command("start_now")
    delayed.clock.now += 10
    state = delayed.request("GET", "/api/state")[1]
    # 10 seconds on, less the 2 second delay, as the video would show.
    assert state["display"] == "Solo -04:52"
    runner.close()


def test_request_from_another_site_is_refused(site: Site) -> None:
    site.log_in()
    status, _, _ = site.request(
        "POST",
        "/api/command",
        {"command": "abandon"},
        {"Origin": "https://evil.example"},
    )
    assert status == 403
    assert site.control.state(T0).phase.value == "idle"


def test_request_from_the_page_itself_is_allowed(site: Site) -> None:
    site.log_in()
    origin = f"https://127.0.0.1:{site.page.port}"
    status, _, _ = site.request(
        "POST", "/api/command", {"command": "start_now"}, {"Origin": origin}
    )
    assert status == 200


@pytest.mark.parametrize("raw", [b"not json", b"[]", b"1", b'"text"'])
def test_bad_bodies_are_refused(site: Site, raw: bytes) -> None:
    assert site.request("POST", "/api/login", raw=raw)[0] == 400


def test_oversized_body_is_refused(site: Site) -> None:
    raw = b" " * (MAX_BODY_BYTES + 1)
    assert site.request("POST", "/api/login", raw=raw)[0] == 413


def test_unknown_addresses_are_not_found(site: Site) -> None:
    assert site.request("GET", "/secrets.json")[0] == 404
    assert site.request("POST", "/api/other", {})[0] == 404


def test_a_rejected_connection_doesnt_stop_the_server(site: Site) -> None:
    with socket.create_connection(("127.0.0.1", site.page.port)) as sock:
        sock.sendall(b"this is not TLS\r\n\r\n")
        sock.settimeout(2)
        try:
            sock.recv(100)
        except OSError:
            pass
    assert site.request("GET", "/")[0] == 200


def test_certificate_is_created_with_a_private_key(tmp_path: Path) -> None:
    certificate, key = ensure_certificate(tmp_path / "new", "10.0.0.2")
    assert "BEGIN CERTIFICATE" in certificate.read_text(encoding="ascii")
    assert stat.S_IMODE(key.stat().st_mode) == 0o600


def test_certificate_is_kept(tmp_path: Path) -> None:
    certificate, _ = ensure_certificate(tmp_path, "10.0.0.2")
    before = certificate.read_bytes()
    ensure_certificate(tmp_path, "10.0.0.3")
    assert certificate.read_bytes() == before


def test_missing_openssl_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(control_page.shutil, "which", lambda _: None)
    with pytest.raises(ControlPageError, match="without openssl"):
        ensure_certificate(tmp_path, "10.0.0.2")


def test_damaged_certificate_is_reported(tmp_path: Path) -> None:
    (tmp_path / control_page.CERTIFICATE_FILE).write_text("bad", encoding="ascii")
    (tmp_path / control_page.KEY_FILE).write_text("bad", encoding="ascii")
    config = make_config()
    control = RaceControl(config.sequences, tmp_path / "j.jsonl", Clock())
    with pytest.raises(ControlPageError, match="Can't use the certificate"):
        ControlPage(config, control, PASSWORD, certificate_directory=tmp_path)


def test_page_port_must_differ_from_the_stream_ports() -> None:
    with pytest.raises(ValidationError, match="page_port"):
        Config(
            stream=StreamConfig(viewer_port=9000),
            control=ControlConfig(page_port=9000),
        )


def test_no_password_leaves_the_page_off(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = make_config()
    control = RaceControl(config.sequences, tmp_path / "j.jsonl", Clock())
    with caplog.at_level(logging.INFO):
        assert pipeline.start_control_page(config, Secrets(), control) is None
    assert "phone page is off" in caplog.text


def test_page_failure_doesnt_stop_the_application(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_: object, **__: object) -> None:
        raise ControlPageError("no certificate")

    monkeypatch.setattr(pipeline, "ControlPage", fail)
    config = make_config()
    control = RaceControl(config.sequences, tmp_path / "j.jsonl", Clock())
    secrets = Secrets(control_password=PASSWORD)
    assert pipeline.start_control_page(config, secrets, control) is None
    assert "phone control page is off" in caplog.text


def test_port_in_use_doesnt_stop_the_application(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_: object, **__: object) -> None:
        raise OSError("Address already in use")

    monkeypatch.setattr(pipeline, "ControlPage", fail)
    config = make_config()
    control = RaceControl(config.sequences, tmp_path / "j.jsonl", Clock())
    secrets = Secrets(control_password=PASSWORD)
    assert pipeline.start_control_page(config, secrets, control) is None
    assert "Address already in use" in caplog.text
