#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_discovery.py
#  Description: Tests for finding the camera, with mocked networking.
#  Date: 2026-10-31
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: pytest
#
#########################################################

"""Tests for finding the camera, with mocked networking."""

#
# SYSTEM IMPORTS
#

import ipaddress
import socket
import subprocess
import threading
from pathlib import Path

import pytest

#
# PROJECT IMPORTS
#
from start_camera import cli, discovery
from start_camera.config import (
    CameraConfig,
    Config,
    ConfigError,
    Secrets,
    load_config,
    save_config,
)
from start_camera.discovery import (
    Candidate,
    DiscoveryError,
    StreamStatus,
    build_rtsp_url,
    parse_arp_table,
    parse_probe_reply,
    probe_stream,
    scan_port,
    scan_subnets,
    select_camera,
    ws_discovery,
)

#
#  GLOBAL VARIABLES
#

CAMERA_MODEL = "SN-T23N-CAMS3W-01"
CAMERA_MAC = "6c:00:00:00:00:01"
SECRETS = Secrets(camera_username="user", camera_password="pw")
PROBE_REPLY = (
    "<d:Scopes>onvif://www.onvif.org/hardware/"
    f"{CAMERA_MODEL} onvif://www.onvif.org/location/anywhere</d:Scopes>"
).encode()

#
# FUNCTIONS
#


def candidate(
    host: str = "10.0.0.5",
    mac: str | None = CAMERA_MAC,
    identified: bool = True,
    status: StreamStatus = StreamStatus.READABLE,
) -> Candidate:
    """Return a candidate, by default a readable camera."""
    return Candidate(host, mac, CAMERA_MODEL, identified, status)


class FakeNetwork:
    """Replace the network functions used by discover, recording stream probes."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        onvif: dict[str, str | None],
        open_hosts: list[str],
        macs: dict[str, str],
        direct_replies: dict[str, str | None] | None = None,
    ) -> None:
        """Set up what the network will report."""
        self.urls: list[str] = []
        self.subnets: list[ipaddress.IPv4Network] = []
        direct = direct_replies or {}

        def fake_discovery(
            addresses: list[str], hosts: tuple[str, ...] | list[str] = ()
        ) -> dict[str, str | None]:
            return {host: direct[host] for host in hosts if host in direct} or (
                dict(onvif) if not hosts else {}
            )

        def fake_scan(subnets: list[ipaddress.IPv4Network], port: int) -> list[str]:
            self.subnets = list(subnets)
            return open_hosts

        def fake_probe(url: str) -> StreamStatus:
            self.urls.append(url)
            return StreamStatus.READABLE if "@" in url else StreamStatus.AUTH_REQUIRED

        monkeypatch.setattr(discovery, "ws_discovery", fake_discovery)
        monkeypatch.setattr(discovery, "scan_port", fake_scan)
        monkeypatch.setattr(discovery, "probe_stream", fake_probe)
        monkeypatch.setattr(discovery, "read_arp_table", lambda: macs)
        monkeypatch.setattr(
            discovery, "_interface_addresses", lambda: [("10.0.0.2", 24)]
        )


def test_camera_is_found_by_model(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeNetwork(
        monkeypatch, {"10.0.0.5": CAMERA_MODEL}, ["10.0.0.5"], {"10.0.0.5": CAMERA_MAC}
    )
    camera = select_camera(discovery.discover(Config(), SECRETS), None)
    assert camera.host == "10.0.0.5"
    assert camera.mac == CAMERA_MAC


def test_camera_is_found_at_new_address_by_mac(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config(camera=CameraConfig(host="10.0.0.5", mac=CAMERA_MAC.upper()))
    FakeNetwork(monkeypatch, {}, ["10.0.0.99"], {"10.0.0.99": CAMERA_MAC})
    camera = select_camera(discovery.discover(config, SECRETS), config.camera.mac)
    assert camera.host == "10.0.0.99"


def test_credentials_only_go_to_identified_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = FakeNetwork(
        monkeypatch,
        {"10.0.0.5": CAMERA_MODEL, "10.0.0.6": "OtherCamera"},
        ["10.0.0.5", "10.0.0.6", "10.0.0.7"],
        {},
    )
    candidates = discovery.discover(Config(), SECRETS)
    with_password = [url for url in network.urls if "pw" in url]
    assert with_password == ["rtsp://user:pw@10.0.0.5:554/av_stream/ch0"]
    assert [item.identified for item in candidates] == [True, False, False]


def test_configured_host_on_another_subnet_is_trusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config(camera=CameraConfig(host="10.9.0.5"))
    FakeNetwork(monkeypatch, {}, ["10.9.0.5"], {})
    camera = select_camera(discovery.discover(config, SECRETS), None)
    assert camera.host == "10.9.0.5"
    assert camera.mac is None


def test_host_on_another_subnet_is_identified_by_direct_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config(camera=CameraConfig(scan_subnets=["10.9.0.0/24"]))
    network = FakeNetwork(
        monkeypatch, {}, ["10.9.0.5"], {}, direct_replies={"10.9.0.5": CAMERA_MODEL}
    )
    camera = select_camera(discovery.discover(config, SECRETS), None)
    assert camera.host == "10.9.0.5"
    assert ipaddress.ip_network("10.9.0.0/24") in network.subnets
    assert ipaddress.ip_network("10.0.0.0/24") in network.subnets


def test_saved_mac_picks_between_cameras() -> None:
    candidates = [candidate("10.0.0.5", "aa:aa:aa:aa:aa:aa"), candidate("10.0.0.6")]
    assert select_camera(candidates, CAMERA_MAC).host == "10.0.0.6"


def test_changed_mac_is_accepted_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    camera = select_camera([candidate(mac="aa:aa:aa:aa:aa:aa")], CAMERA_MAC)
    assert camera.host == "10.0.0.5"
    assert "MAC address isn't the saved one" in caplog.text


def test_several_cameras_are_an_error() -> None:
    with pytest.raises(DiscoveryError, match="10.0.0.5, 10.0.0.6"):
        select_camera([candidate("10.0.0.5"), candidate("10.0.0.6")], None)


@pytest.mark.parametrize(
    ("candidates", "message"),
    [
        ([], "No camera found.*scan_subnets"),
        ([candidate(status=StreamStatus.AUTH_REQUIRED)], "rejected the login"),
        ([candidate(status=StreamStatus.NO_STREAM)], "ONVIF/RTSP is turned on"),
        (
            [candidate(identified=False, status=StreamStatus.AUTH_REQUIRED)],
            "none identified as the camera",
        ),
    ],
)
def test_no_camera_is_explained(candidates: list[Candidate], message: str) -> None:
    with pytest.raises(DiscoveryError, match=message):
        select_camera(candidates, None)


def test_unidentified_readable_device_is_not_chosen() -> None:
    with pytest.raises(DiscoveryError, match="none identified"):
        select_camera([candidate(identified=False)], None)


def test_probe_reply_gives_hardware_name() -> None:
    assert parse_probe_reply(PROBE_REPLY) == CAMERA_MODEL
    assert parse_probe_reply(b"<nothing/>") is None
    assert parse_probe_reply(b"\xff\xfe") is None


def test_ws_discovery_receives_direct_reply() -> None:
    responder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    responder.bind(("127.0.0.1", discovery.WS_DISCOVERY_PORT))

    def respond() -> None:
        _, sender = responder.recvfrom(65535)
        responder.sendto(PROBE_REPLY, sender)
        responder.close()

    threading.Thread(target=respond, daemon=True).start()
    found = ws_discovery([], ["127.0.0.1"], timeout_s=0.5)
    assert found == {"127.0.0.1": CAMERA_MODEL}


def test_ws_discovery_without_replies_is_empty() -> None:
    assert ws_discovery([], [], timeout_s=0.01) == {}


def test_scan_port_finds_open_host() -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        subnet = ipaddress.ip_network("127.0.0.0/29")
        assert scan_port([subnet], port) == ["127.0.0.1"]


def test_arp_table_is_parsed_without_incomplete_entries() -> None:
    table = parse_arp_table(
        "IP address  HW type  Flags  HW address  Mask  Device\n"
        "10.0.0.5  0x1  0x2  6C:00:00:00:00:01  *  wlan0\n"
        "10.0.0.6  0x1  0x0  00:00:00:00:00:00  *  wlan0\n"
    )
    assert table == {"10.0.0.5": CAMERA_MAC}


def test_subnets_combine_without_repeats(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "_interface_addresses", lambda: [("10.0.0.2", 24)])
    subnets = scan_subnets(["10.0.0.9/24", "10.9.0.0/24"])
    assert [str(item) for item in subnets] == ["10.0.0.0/24", "10.9.0.0/24"]


def test_big_local_subnet_is_narrowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "_interface_addresses", lambda: [("10.1.2.3", 8)])
    assert [str(item) for item in scan_subnets()] == ["10.1.2.0/24"]


def test_big_extra_subnet_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "_interface_addresses", lambda: [])
    with pytest.raises(DiscoveryError, match="too big"):
        scan_subnets(["10.0.0.0/8"])


@pytest.mark.parametrize("value", ["10.0.0.0/8", "nonsense"])
def test_bad_scan_subnet_in_configuration_is_named(
    tmp_path: Path, value: str
) -> None:
    path = tmp_path / "c.json"
    path.write_text(
        f'{{"version": 1, "camera": {{"scan_subnets": ["{value}"]}}}}', encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="camera.scan_subnets"):
        load_config(path)


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "status"),
    [
        (0, "h264\n", "", StreamStatus.READABLE),
        (0, "", "", StreamStatus.NO_STREAM),
        (1, "", "Server returned 401 Unauthorized", StreamStatus.AUTH_REQUIRED),
        (1, "", "Invalid data found", StreamStatus.NO_STREAM),
    ],
)
def test_probe_stream_status(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
    stderr: str,
    status: StreamStatus,
) -> None:
    reply = subprocess.CompletedProcess([], returncode, stdout, stderr)
    monkeypatch.setattr(subprocess, "run", lambda *_, **__: reply)
    assert probe_stream("rtsp://host/") is status


def test_probe_timeout_means_no_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(*_: object, **__: object) -> None:
        raise subprocess.TimeoutExpired("ffprobe", 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert probe_stream("rtsp://host/") is StreamStatus.NO_STREAM


def test_url_can_leave_out_credentials() -> None:
    url = build_rtsp_url(Config(), SECRETS, "10.0.0.5", with_credentials=False)
    assert url == "rtsp://10.0.0.5:554/av_stream/ch0"


def test_scan_command_writes_camera_to_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.json"
    save_config(Config(camera=CameraConfig(host="10.0.0.5")), path)
    FakeNetwork(monkeypatch, {}, ["10.0.0.5"], {"10.0.0.5": CAMERA_MAC})
    monkeypatch.setattr(
        "sys.argv", ["startcam", "--scan", "--config", str(path), "--write"]
    )
    monkeypatch.setenv("STARTCAM_CAMERA_USERNAME", "user")
    monkeypatch.setenv("STARTCAM_CAMERA_PASSWORD", "pw")
    assert cli.main() == 0
    saved = load_config(path).camera
    assert (saved.host, saved.mac) == ("10.0.0.5", CAMERA_MAC)
    captured = capsys.readouterr()
    assert "Camera is at 10.0.0.5" in captured.err
    assert "pw" not in captured.err.replace("rtsp", "")
    assert captured.out == ""


def test_scan_command_reports_nothing_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    FakeNetwork(monkeypatch, {}, [], {})
    monkeypatch.setattr("sys.argv", ["startcam", "--scan"])
    assert cli.main() == 1
    assert "No camera found" in capsys.readouterr().err


def test_write_without_scan_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["startcam", "--write"])
    with pytest.raises(SystemExit):
        cli.main()
