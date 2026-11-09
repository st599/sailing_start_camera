#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: control_page.py
#  Description: A password-protected race control page for phones.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: openssl
#
#########################################################

"""A password-protected race control page for phones.

The page can abandon a race, so it's only served over HTTPS, using a self-signed
certificate that's kept so each phone only needs to accept it once.
"""

#
# SYSTEM IMPORTS
#

import hmac
import http.cookies
import json
import logging
import secrets
import shutil
import socket
import ssl
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qsl

#
# PROJECT IMPORTS
#
from start_camera.config import Config
from start_camera.control import RaceControl
from start_camera.network_api import (
    PREFIX,
    ApiError,
    NetworkApi,
    openapi_json,
    since_for_stream,
)
from start_camera.overlays.countdown import RaceDisplay
from start_camera.race_clock import RaceClockError
from start_camera.recording import RecordingError

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

CERTIFICATE_DIRECTORY = Path.home() / ".config" / "start_camera"
CERTIFICATE_FILE = "control_page.crt"
KEY_FILE = "control_page.key"
CERTIFICATE_DAYS = 3650
COOKIE_NAME = "startcam_session"
SESSION_LIFETIME_S = 12 * 3600.0
MAX_LOGIN_FAILURES = 5
LOCKOUT_S = 60.0
MAX_BODY_BYTES = 4096
MAX_API_BODY_BYTES = 65536
REQUEST_TIMEOUT_S = 30.0
SIMPLE_COMMANDS = (
    "start_now",
    "postpone",
    "resume",
    "general_recall",
    "individual_recall",
    "clear_individual_recall",
    "skip",
    "abandon",
)
RECORDING_COMMANDS = {"start_recording": "start", "stop_recording": "stop"}
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Race control</title>
<style>
* { box-sizing: border-box; }
body { margin: 0; padding: 12px; background: #fff; color: #000;
  font: 20px system-ui, sans-serif; }
#time { font-size: 44px; font-weight: 800; text-align: center; padding: 12px 4px;
  border: 4px solid #000; border-radius: 8px; overflow-wrap: anywhere; }
#flags { min-height: 32px; text-align: center; font-weight: 700; margin: 8px 0; }
#recording { text-align: center; font-weight: 800; color: #fff; background: #a00000;
  border-radius: 8px; padding: 6px; }
#message { min-height: 32px; text-align: center; font-weight: 700; color: #a00000; }
#offline { display: none; background: #a00000; color: #fff; font-weight: 800;
  text-align: center; padding: 12px; border-radius: 8px; margin-bottom: 8px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 10px; }
.steps { grid-template-columns: repeat(4, 1fr); }
button { min-height: 76px; width: 100%; font: 800 22px system-ui, sans-serif;
  background: #003a8c; color: #fff; border: 0; border-radius: 8px;
  touch-action: manipulation; }
button:active { background: #000; }
button.warn { background: #a00000; }
button.quiet { background: #444; min-height: 52px; font-size: 18px; }
input { width: 100%; font-size: 24px; padding: 16px; margin: 10px 0;
  border: 3px solid #000; border-radius: 8px; }
[hidden] { display: none !important; }
</style></head><body>
<div id="offline">CONNECTION LOST</div>
<form id="login" hidden>
  <h1>Race control</h1>
  <input id="password" type="password" autocomplete="current-password"
    placeholder="Password" aria-label="Password">
  <button type="submit">Log in</button>
</form>
<main id="panel" hidden>
  <div id="time" role="timer"></div>
  <div id="flags"></div>
  <div id="recording" hidden>&#9679; RECORDING</div>
  <div id="message" role="status"></div>
  <div class="grid"><button data-command="start_now">Start now</button>
    <button data-command="skip" data-confirm="Skip the next start?">Next start</button>
  </div>
  <div class="grid steps" id="steps"></div>
  <div class="grid"><button data-command="postpone">Postpone</button>
    <button data-command="resume">Resume</button>
    <button id="recall" data-command="individual_recall">Individual recall</button>
    <button class="warn" data-command="general_recall"
      data-confirm="General recall?">General recall</button>
    <button class="warn" data-command="abandon"
      data-confirm="Abandon the racing?">Abandon</button>
    <button id="record" class="quiet" data-command="start_recording" hidden>
      Start recording</button>
    <button class="quiet" id="logout" type="button">Log out</button>
  </div>
</main>
<script>
const $ = (id) => document.getElementById(id);
let showingSteps = false;

async function call(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  };
  return fetch(path, {...options, cache: "no-store"});
}

function showLogin() {
  $("panel").hidden = true;
  $("login").hidden = false;
}

function addSteps(steps) {
  if (showingSteps) return;
  showingSteps = true;
  for (const minutes of steps) {
    const button = document.createElement("button");
    button.textContent = (minutes > 0 ? "+" : "") + minutes;
    button.setAttribute("aria-label", `Delay by ${minutes} minutes`);
    button.addEventListener("click", () => send("shift", minutes));
    $("steps").append(button);
  }
}

function show(state) {
  $("login").hidden = true;
  $("panel").hidden = false;
  $("time").textContent = state.display || "-";
  $("flags").textContent = state.flags.length ? "Flags: " + state.flags.join(", ") : "";
  addSteps(state.delay_steps);
  const flying = state.flags.includes("X");
  const recall = flying ? "clear_individual_recall" : "individual_recall";
  $("recall").dataset.command = recall;
  $("recall").textContent = flying ? "Clear X" : "Individual recall";
  const recording = state.recording;
  $("record").hidden = recording === null;
  $("recording").hidden = recording !== true;
  $("record").dataset.command = recording ? "stop_recording" : "start_recording";
  $("record").textContent = recording ? "Stop recording" : "Start recording";
  if (recording) $("record").dataset.confirm = "Stop recording?";
  else delete $("record").dataset.confirm;
}

async function refresh() {
  try {
    const reply = await call("/api/state");
    $("offline").style.display = "none";
    if (reply.status === 401) return showLogin();
    show(await reply.json());
  } catch (error) {
    $("offline").style.display = "block";
  }
}

async function send(command, minutes) {
  $("message").textContent = "";
  try {
    const reply = await call("/api/command", {command, minutes});
    if (reply.status === 401) return showLogin();
    const result = await reply.json();
    if (!reply.ok) $("message").textContent = result.error;
    refresh();
  } catch (error) {
    $("offline").style.display = "block";
  }
}

document.querySelectorAll("button[data-command]").forEach((button) => {
  button.addEventListener("click", () => {
    const question = button.dataset.confirm;
    if (!question || confirm(question)) send(button.dataset.command);
  });
});

$("login").addEventListener("submit", async (event) => {
  event.preventDefault();
  const reply = await call("/api/login", {password: $("password").value});
  $("password").value = "";
  if (reply.ok) return refresh();
  alert((await reply.json()).error);
});

$("logout").addEventListener("click", async () => {
  await call("/api/logout", {});
  showLogin();
});

refresh();
setInterval(refresh, 1000);
</script></body></html>
"""

#
# FUNCTIONS
#


class ControlPageError(Exception):
    """The control page could not be set up."""


class Recording(Protocol):
    """Starts and stops recording."""

    @property
    def is_recording(self) -> bool:
        """Return whether recording is on."""
        ...

    def start(self) -> None:
        """Start recording."""
        ...

    def stop(self) -> None:
        """Stop recording."""
        ...


def ensure_certificate(directory: Path, address: str) -> tuple[Path, Path]:
    """Return a self-signed certificate and key, creating them only if needed.

    Keeping them means a phone only has to accept the certificate once.

    Args:
        directory: Where to keep them.
        address: The computer's address, to name in a new certificate.

    Returns:
        The paths of the certificate and the key.

    Raises:
        ControlPageError: If they can't be created.
    """
    certificate, key = directory / CERTIFICATE_FILE, directory / KEY_FILE
    if certificate.exists() and key.exists():
        return certificate, key
    openssl = shutil.which("openssl")
    if openssl is None:
        raise ControlPageError("Can't create a certificate without openssl")
    host = socket.gethostname()
    names = f"DNS:{host},DNS:{host}.local,DNS:localhost,IP:127.0.0.1"
    if address.replace(".", "").isdigit():
        names += f",IP:{address}"
    command = [
        openssl, "req", "-x509", "-newkey", "ec", "-pkeyopt",
        "ec_paramgen_curve:prime256v1", "-nodes", "-keyout", str(key),
        "-out", str(certificate), "-days", str(CERTIFICATE_DAYS),
        "-subj", f"/CN={host}", "-addext", f"subjectAltName={names}",
    ]  # fmt: skip
    try:
        directory.mkdir(parents=True, exist_ok=True)
        # The key is created private, with no moment when others could read it.
        key.touch(mode=0o600, exist_ok=True)
        key.chmod(0o600)
        subprocess.run(command, capture_output=True, check=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        raise ControlPageError(f"Can't create a certificate: {error}") from error
    logger.info("Created a certificate for the race control page")
    return certificate, key


@dataclass
class _Attempts:
    """Recent failures from one address."""

    failures: int = 0
    locked_until: float = 0.0


class FailureLimiter:
    """Locks out an address for a while after repeated wrong passwords or tokens."""

    def __init__(self, clock: Callable[[], float]) -> None:
        """Start with no failures."""
        self._clock = clock
        self._lock = threading.Lock()
        self._attempts: dict[str, _Attempts] = {}

    def seconds_locked(self, address: str) -> float:
        """Return how long an address must wait before trying again."""
        with self._lock:
            attempts = self._attempts.get(address)
            return max(0.0, attempts.locked_until - self._clock()) if attempts else 0.0

    def failed(self, address: str) -> None:
        """Note a failure, locking the address out after too many."""
        with self._lock:
            attempts = self._attempts.setdefault(address, _Attempts())
            attempts.failures += 1
            if attempts.failures >= MAX_LOGIN_FAILURES:
                attempts.failures = 0
                attempts.locked_until = self._clock() + LOCKOUT_S

    def succeeded(self, address: str) -> None:
        """Clear an address's failures."""
        with self._lock:
            self._attempts.pop(address, None)


class _Sessions:
    """Logged-in phones, and limits on guessing the password."""

    def __init__(
        self, password: str, clock: Callable[[], float], limiter: FailureLimiter
    ) -> None:
        """Hold the password."""
        self._password = password.encode()
        self._clock = clock
        self._lock = threading.Lock()
        self._expiry: dict[str, float] = {}
        self.limiter = limiter

    def seconds_locked(self, address: str) -> float:
        """Return how long an address must wait before trying again."""
        return self.limiter.seconds_locked(address)

    def log_in(self, address: str, password: str) -> str | None:
        """Return a new session token if the password is right."""
        # Comparing in constant time gives nothing away about a wrong password.
        if not hmac.compare_digest(password.encode(), self._password):
            self.limiter.failed(address)
            return None
        self.limiter.succeeded(address)
        with self._lock:
            now = self._clock()
            self._expiry = {
                token: expiry for token, expiry in self._expiry.items() if expiry > now
            }
            token = secrets.token_urlsafe(32)
            self._expiry[token] = now + SESSION_LIFETIME_S
            return token

    def is_valid(self, token: str | None) -> bool:
        """Return whether a session token is current."""
        if token is None:
            return False
        with self._lock:
            return self._expiry.get(token, 0.0) > self._clock()

    def log_out(self, token: str | None) -> None:
        """End a session."""
        with self._lock:
            self._expiry.pop(token or "", None)


class _RequestError(Exception):
    """A request can't be carried out, with the status to reply with."""

    def __init__(self, status: int, message: str) -> None:
        """Hold the reply."""
        super().__init__(message)
        self.status = status
        self.message = message


class _PageState:
    """Everything the requests share."""

    def __init__(
        self,
        config: Config,
        control: RaceControl,
        sessions: _Sessions | None,
        clock: Callable[[], float],
        recorder: Recording | None = None,
        api: NetworkApi | None = None,
    ) -> None:
        """Hold what the page shows and controls."""
        self.control = control.with_source("phone")
        self._recorder = recorder
        self.sessions = sessions
        self.api = api
        self._clock = clock
        self._delay_steps = config.control.delay_steps
        self._display = RaceDisplay(config, control)

    def describe(self) -> dict[str, Any]:
        """Return the state, showing the same time and flags as the video."""
        summary = self._display.summary(self._clock())
        return {
            "phase": summary.phase.value,
            "display": summary.display,
            "flags": list(summary.flags),
            "delay_steps": self._delay_steps,
            "recording": self._recorder.is_recording if self._recorder else None,
        }

    def run_command(self, body: dict[str, Any]) -> None:
        """Carry out a command from a request.

        Raises:
            _RequestError: If the command isn't allowed, or is refused.
        """
        command = body.get("command")
        try:
            if command == "shift":
                minutes = body.get("minutes")
                if minutes not in self._delay_steps:
                    raise _RequestError(400, "That delay isn't one of the steps")
                self.control.shift(minutes)
            elif command in SIMPLE_COMMANDS:
                getattr(self.control, command)()
            elif command in RECORDING_COMMANDS and self._recorder is not None:
                getattr(self._recorder, RECORDING_COMMANDS[command])()
            else:
                raise _RequestError(400, "Unknown command")
        except (RaceClockError, RecordingError) as error:
            raise _RequestError(409, str(error)) from error


def _make_handler(state: _PageState) -> type[BaseHTTPRequestHandler]:
    """Return a request handler for the page."""

    class ControlPageHandler(BaseHTTPRequestHandler):
        """Serve the page and its commands."""

        protocol_version = "HTTP/1.1"
        timeout = REQUEST_TIMEOUT_S

        def do_GET(self) -> None:  # noqa: N802 - the name http.server requires
            """Serve the page, its state, or the network interface."""
            path, query = _split(self.path)
            if path.startswith(PREFIX):
                self._handle_api("GET", path.removeprefix(PREFIX), query)
            elif state.sessions is None:
                self._send_json(404, {"error": "Not found"})
            elif path == "/":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif path == "/api/state":
                if self._require_session():
                    self._send_json(200, state.describe())
            else:
                self._send_json(404, {"error": "Not found"})

        def do_POST(self) -> None:  # noqa: N802 - the name http.server requires
            """Handle a login, a logout, a command, or the network interface."""
            path, query = _split(self.path)
            if path.startswith(PREFIX):
                self._handle_api("POST", path.removeprefix(PREFIX), query)
                return
            try:
                if state.sessions is None:
                    raise _RequestError(404, "Not found")
                body = self._read_body(MAX_BODY_BYTES)
                self._check_origin()
                self._dispatch_post(path, body)
            except _RequestError as error:
                self._send_json(error.status, {"error": str(error)})

        def _dispatch_post(self, path: str, body: dict[str, Any]) -> None:
            """Carry out a POST request from the phone page."""
            if path == "/api/login":
                self._log_in(body)
            elif path == "/api/logout":
                state.sessions.log_out(self._session_token())
                self._send_json(200, {}, self._cookie("", 0))
            elif path == "/api/command":
                if self._require_session():
                    state.run_command(body)
                    self._send_json(200, state.describe())
            else:
                self._send_json(404, {"error": "Not found"})

        def _handle_api(self, method: str, path: str, query: dict[str, str]) -> None:
            """Answer a request to the network interface."""
            api = state.api
            try:
                if api is None:
                    raise ApiError(404, "not_found", "The network interface is off")
                if method == "GET" and path == "openapi.json":
                    self._send(200, openapi_json(), "application/json")
                    return
                body = self._api_body(method)
                self._check_origin()
                access = api.authorise(
                    self.headers.get("Authorization"), self.client_address[0]
                )
                if method == "GET" and path == "events/stream":
                    self._stream_events(api, query)
                elif method == "GET":
                    self._send_json(200, api.get(path, query))
                else:
                    self._send_json(200, api.post(path, body, access))
            except _RequestError as error:
                reply = {"error": str(error), "code": "bad_request"}
                self._send_json(error.status, reply)
            except ApiError as error:
                self._send_json(error.status, error.body())
            except Exception:
                logger.exception("Network interface: couldn't answer %s", path)
                reply = {"error": "Something went wrong", "code": "internal"}
                self._send_json(500, reply)

        def _api_body(self, method: str) -> dict[str, Any]:
            """Return a network request's JSON body, which only a POST has."""
            return self._read_body(MAX_API_BODY_BYTES) if method == "POST" else {}

        def _stream_events(self, api: NetworkApi, query: dict[str, str]) -> None:
            """Send events until the client goes away."""
            since = since_for_stream(query, self.headers.get("Last-Event-ID"))

            def open_stream() -> None:
                self.close_connection = True
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.flush()

            def write(data: bytes) -> None:
                self.wfile.write(data)
                self.wfile.flush()

            api.stream_events(since, open_stream, write)

        def _log_in(self, body: dict[str, Any]) -> None:
            """Check a password, limiting how often it can be tried."""
            address = self.client_address[0]
            locked = state.sessions.seconds_locked(address)
            if locked > 0:
                logger.warning("Refused a login from %s during its lockout", address)
                raise _RequestError(
                    429, f"Too many attempts. Try again in {locked:.0f} seconds"
                )
            password = body.get("password")
            token = None
            if isinstance(password, str):
                token = state.sessions.log_in(address, password)
            if token is None:
                logger.warning("Failed login on the control page from %s", address)
                raise _RequestError(401, "Wrong password")
            logger.info("Logged in to the control page from %s", address)
            self._send_json(
                200, {}, self._cookie(token, round(SESSION_LIFETIME_S))
            )

        def _cookie(self, value: str, max_age: int) -> str:
            """Return a session cookie that other sites and scripts can't use."""
            return (
                f"{COOKIE_NAME}={value}; Max-Age={max_age}; Path=/; Secure; HttpOnly; "
                "SameSite=Strict"
            )

        def _session_token(self) -> str | None:
            """Return the request's session token, if it has one."""
            try:
                cookies = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
            except http.cookies.CookieError:
                return None
            morsel = cookies.get(COOKIE_NAME)
            return morsel.value if morsel else None

        def _require_session(self) -> bool:
            """Refuse a request that isn't logged in."""
            if state.sessions.is_valid(self._session_token()):
                return True
            self._send_json(401, {"error": "Please log in"})
            return False

        def _check_origin(self) -> None:
            """Refuse a request sent by another site."""
            origin = self.headers.get("Origin")
            if origin is not None and origin != f"https://{self.headers.get('Host')}":
                raise _RequestError(403, "Request from another site")

        def _read_body(self, limit: int) -> dict[str, Any]:
            """Return the request's JSON body, within a size limit."""
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                self.close_connection = True
                raise _RequestError(400, "Bad request") from error
            if not 0 <= length <= limit:
                self.close_connection = True
                raise _RequestError(413, "Request too large")
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError as error:
                raise _RequestError(400, "Bad request") from error
            if not isinstance(body, dict):
                raise _RequestError(400, "Bad request")
            return body

        def _send_json(
            self, status: int, value: dict[str, Any], cookie: str | None = None
        ) -> None:
            """Send a JSON reply."""
            self._send(status, json.dumps(value).encode(), "application/json", cookie)

        def _send(
            self,
            status: int,
            body: bytes,
            content_type: str,
            cookie: str | None = None,
        ) -> None:
            """Send a reply."""
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            if cookie is not None:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            """Send request logs to the shared logger."""
            logger.debug(format, *args)

    return ControlPageHandler


def _split(target: str) -> tuple[str, dict[str, str]]:
    """Return a request's path and its query parameters."""
    path, _, query = target.partition("?")
    return path, dict(parse_qsl(query))


class _QuietServer(ThreadingHTTPServer):
    """A server that doesn't print an error for each failed connection."""

    daemon_threads = True

    def handle_error(self, request: object, client_address: object) -> None:
        """Log connection errors, such as a phone rejecting the certificate."""
        logger.debug("Connection problem on the control page", exc_info=True)


class ControlPage:
    """Serve the race control page in a background thread."""

    def __init__(
        self,
        config: Config,
        control: RaceControl,
        password: str | None,
        *,
        address: str = "localhost",
        certificate_directory: Path = CERTIFICATE_DIRECTORY,
        clock: Callable[[], float] = time.time,
        recorder: Recording | None = None,
        api: NetworkApi | None = None,
    ) -> None:
        """Open the page's port.

        Args:
            config: The configuration.
            control: The race controls.
            password: The password for the page, or None to serve only the network
                interface.
            address: The computer's address, to name in a new certificate.
            certificate_directory: Where the certificate is kept.
            clock: Provides the current time.
            recorder: Controls recording, if the page may use it.
            api: The network interface, if it's on.

        Raises:
            ControlPageError: If the certificate can't be created or used.
            OSError: If the port can't be opened.
        """
        certificate, key = ensure_certificate(certificate_directory, address)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            context.load_cert_chain(certificate, key)
        except ssl.SSLError as error:
            raise ControlPageError(f"Can't use the certificate: {error}") from error
        limiter = api.limiter if api is not None else FailureLimiter(clock)
        sessions = None if password is None else _Sessions(password, clock, limiter)
        state = _PageState(config, control, sessions, clock, recorder, api)
        self._api = api
        address_and_port = ("", config.control.page_port)
        self._server = _QuietServer(address_and_port, _make_handler(state))
        # The handshake happens in each connection's own thread, so one slow phone
        # can't hold up the others.
        self._server.socket = context.wrap_socket(
            self._server.socket, server_side=True, do_handshake_on_connect=False
        )
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="control-page"
        )
        self._thread.start()

    def stop(self) -> None:
        """Close the page's port, and end any event streams."""
        if self._api is not None:
            self._api.stopping.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()
