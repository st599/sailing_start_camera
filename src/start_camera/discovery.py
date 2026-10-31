#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: discovery.py
#  Description: Find the camera on the network.
#  Date: 2026-10-31
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: ffmpeg (ffprobe), iproute2
#
#########################################################

"""Find the camera on the network.

Devices are found with ONVIF WS-Discovery and a scan for the RTSP port. Credentials are
only sent to hosts identified as the camera, because a scan can find anybody's devices.
"""

#
# SYSTEM IMPORTS
#

import ipaddress
import json
import logging
import re
import select
import socket
import subprocess
import time
import uuid
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import quote

#
# PROJECT IMPORTS
#
from start_camera.config import MIN_SCAN_PREFIX, Config, Secrets

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

CAMERA_MODEL_MARKER = "CAMS3W"
WS_DISCOVERY_ADDRESS = "239.255.255.250"
WS_DISCOVERY_PORT = 3702
WS_DISCOVERY_TIMEOUT_S = 3.0
PORT_TIMEOUT_S = 1.0
PORT_WORKERS = 128
PROBE_TIMEOUT_S = 15.0
PROBE_WORKERS = 8
DEFAULT_PREFIX = 24
VIRTUAL_INTERFACES = ("lo", "docker", "br-", "veth", "virbr", "tun", "tap")
ARP_TABLE = Path("/proc/net/arp")
NO_MAC = "00:00:00:00:00:00"

#
# FUNCTIONS
#


class DiscoveryError(Exception):
    """A single camera could not be chosen from the network."""


class StreamStatus(Enum):
    """What happened when a host's stream was probed."""

    READABLE = "stream readable"
    AUTH_REQUIRED = "password required or rejected"
    NO_STREAM = "no stream"


@dataclass(frozen=True)
class Candidate:
    """A device that may be the camera."""

    host: str
    mac: str | None
    model: str | None
    identified: bool
    status: StreamStatus


def build_rtsp_url(
    config: Config, secrets: Secrets, host: str, *, with_credentials: bool = True
) -> str:
    """Return the camera's RTSP URL. It can contain credentials, so never log it."""
    credentials = ""
    if with_credentials and secrets.camera_username is not None:
        credentials = quote(secrets.camera_username.get_secret_value(), safe="")
        if secrets.camera_password is not None:
            password = quote(secrets.camera_password.get_secret_value(), safe="")
            credentials += f":{password}"
        credentials += "@"
    camera = config.camera
    return f"rtsp://{credentials}{host}:{camera.rtsp_port}{camera.rtsp_path}"


def _primary_address() -> str | None:
    """Return the address of the default route. Connecting sends no traffic."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 9))
        except OSError:
            return None
        return sock.getsockname()[0]


def _interface_addresses() -> list[tuple[str, int]]:
    """Return the address and prefix length of each real network interface."""
    try:
        reply = subprocess.run(
            ["ip", "-j", "-4", "addr", "show", "up", "scope", "global"],
            capture_output=True, text=True, check=True, timeout=5,
        )  # fmt: skip
        interfaces = json.loads(reply.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        logger.debug("Can't list interfaces with ip; using the default route")
        primary = _primary_address()
        return [(primary, DEFAULT_PREFIX)] if primary else []
    return [
        (info["local"], info["prefixlen"])
        for interface in interfaces
        if not interface["ifname"].startswith(VIRTUAL_INTERFACES)
        for info in interface.get("addr_info", [])
    ]


def _subnet_for(address: str, prefix: int) -> ipaddress.IPv4Network:
    """Return an interface's subnet, narrowed if it's too big to scan."""
    if prefix < DEFAULT_PREFIX:
        logger.info(
            "Scanning only the /%d around %s, not its /%d. Use a scan subnet for more",
            DEFAULT_PREFIX, address, prefix,
        )  # fmt: skip
        prefix = DEFAULT_PREFIX
    return ipaddress.ip_network(f"{address}/{prefix}", strict=False)


def scan_subnets(extra: Iterable[str] = ()) -> list[ipaddress.IPv4Network]:
    """Return the local subnets plus any extra ones, without repeats."""
    subnets = [_subnet_for(*item) for item in _interface_addresses()]
    subnets += [ipaddress.ip_network(item, strict=False) for item in extra]
    for subnet in subnets:
        if subnet.prefixlen < MIN_SCAN_PREFIX:
            raise DiscoveryError(f"{subnet} is too big to scan")
    return list(dict.fromkeys(subnets))


def _probe_message() -> bytes:
    """Return a WS-Discovery probe for ONVIF cameras."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
 xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:dn="http://www.onvif.org/ver10/network/wsdl"><e:Header>
<w:MessageID>uuid:{uuid.uuid4()}</w:MessageID>
<w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
<w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
</e:Header><e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe>
</e:Body></e:Envelope>""".encode()


def parse_probe_reply(reply: bytes) -> str | None:
    """Return the hardware name in a WS-Discovery reply, or None if it has none."""
    match = re.search(r"onvif\.org/hardware/([^\s<]+)", reply.decode(errors="replace"))
    return match.group(1) if match else None


def _discovery_sockets(
    addresses: Iterable[str], hosts: Iterable[str]
) -> list[socket.socket]:
    """Send probes by multicast on each interface and directly to each host."""
    message = _probe_message()
    sockets = []
    for address in addresses:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sockets.append(sock)
        try:
            sock.bind((address, 0))
            sock.setsockopt(
                socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(address)
            )
            sock.sendto(message, (WS_DISCOVERY_ADDRESS, WS_DISCOVERY_PORT))
        except OSError as error:
            logger.debug("Can't send WS-Discovery from %s: %s", address, error)
    direct = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sockets.append(direct)
    for host in hosts:
        try:
            direct.sendto(message, (host, WS_DISCOVERY_PORT))
        except OSError as error:
            logger.debug("Can't send WS-Discovery to %s: %s", host, error)
    return sockets


def ws_discovery(
    addresses: Iterable[str],
    hosts: Iterable[str] = (),
    timeout_s: float = WS_DISCOVERY_TIMEOUT_S,
) -> dict[str, str | None]:
    """Return the hosts answering WS-Discovery, with their hardware names.

    Args:
        addresses: Local addresses to send multicast probes from.
        hosts: Hosts to probe directly, as multicast doesn't cross routers.
        timeout_s: How long to wait for replies.
    """
    sockets = _discovery_sockets(addresses, hosts)
    found: dict[str, str | None] = {}
    try:
        end = time.monotonic() + timeout_s
        while (remaining := end - time.monotonic()) > 0:
            readable, _, _ = select.select(sockets, [], [], remaining)
            for sock in readable:
                try:
                    reply, sender = sock.recvfrom(65535)
                except OSError:
                    continue
                found[sender[0]] = parse_probe_reply(reply)
    finally:
        for sock in sockets:
            sock.close()
    return found


def _port_is_open(host: str, port: int) -> bool:
    """Return whether a host accepts connections on a port."""
    with socket.socket() as sock:
        sock.settimeout(PORT_TIMEOUT_S)
        return sock.connect_ex((host, port)) == 0


def scan_port(subnets: Iterable[ipaddress.IPv4Network], port: int) -> list[str]:
    """Return the hosts in the subnets that accept connections on a port."""
    hosts = [str(host) for subnet in subnets for host in subnet.hosts()]
    with ThreadPoolExecutor(PORT_WORKERS) as pool:
        results = pool.map(lambda host: _port_is_open(host, port), hosts)
        return [host for host, is_open in zip(hosts, results, strict=True) if is_open]


def parse_arp_table(text: str) -> dict[str, str]:
    """Return the MAC address of each host in a /proc/net/arp table."""
    table = {}
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 4 and fields[3] != NO_MAC:
            table[fields[0]] = fields[3].lower()
    return table


def read_arp_table() -> dict[str, str]:
    """Return the MAC addresses this computer knows. Routers hide those beyond them."""
    try:
        return parse_arp_table(ARP_TABLE.read_text(encoding="utf-8"))
    except OSError as error:
        logger.debug("Can't read the ARP table: %s", error)
        return {}


def probe_stream(url: str) -> StreamStatus:
    """Try to read a stream with ffprobe, whose output is kept out of the logs."""
    command = [
        "ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-timeout", "8000000",
        "-show_entries", "stream=codec_name", "-of", "csv=p=0", url,
    ]  # fmt: skip
    try:
        reply = subprocess.run(
            command, capture_output=True, text=True, timeout=PROBE_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return StreamStatus.NO_STREAM
    except OSError as error:
        raise DiscoveryError(f"Can't run ffprobe: {error.strerror}") from error
    if reply.returncode == 0 and reply.stdout.strip():
        return StreamStatus.READABLE
    if "401" in reply.stderr:
        return StreamStatus.AUTH_REQUIRED
    return StreamStatus.NO_STREAM


def _is_identified(host: str, mac: str | None, model: str | None, c: Config) -> bool:
    """Return whether a host is the camera, so may be given its credentials."""
    saved_mac = c.camera.mac.lower() if c.camera.mac else None
    return (
        host == c.camera.host
        or (mac is not None and mac == saved_mac)
        or (model is not None and CAMERA_MODEL_MARKER in model)
    )


def discover(
    config: Config, secrets: Secrets, extra_subnets: Iterable[str] = ()
) -> list[Candidate]:
    """Find the devices on the network that may be the camera.

    Args:
        config: The configuration, including any saved camera details.
        secrets: The camera's credentials.
        extra_subnets: Subnets to scan as well as the configured and local ones.

    Returns:
        The devices found, each probed for a stream.
    """
    extra = [*config.camera.scan_subnets, *extra_subnets]
    subnets = scan_subnets(extra)
    logger.info("Scanning %s", ", ".join(str(subnet) for subnet in subnets))
    addresses = [address for address, _ in _interface_addresses()]
    models = ws_discovery(addresses)
    open_hosts = scan_port(subnets, config.camera.rtsp_port)
    unanswered = [host for host in open_hosts if host not in models]
    if unanswered:
        models |= ws_discovery([], unanswered)
    macs = read_arp_table()

    def check(host: str) -> Candidate:
        mac, model = macs.get(host), models.get(host)
        identified = _is_identified(host, mac, model, config)
        url = build_rtsp_url(config, secrets, host, with_credentials=identified)
        return Candidate(host, mac, model, identified, probe_stream(url))

    hosts = sorted(set(open_hosts) | set(models), key=ipaddress.ip_address)
    with ThreadPoolExecutor(PROBE_WORKERS) as pool:
        return list(pool.map(check, hosts))


def select_camera(candidates: Iterable[Candidate], saved_mac: str | None) -> Candidate:
    """Choose the camera, preferring the saved MAC address.

    Args:
        candidates: The devices found.
        saved_mac: The camera's saved MAC address, if any.

    Returns:
        The camera.

    Raises:
        DiscoveryError: If no camera can be read, or it's unclear which is correct.
    """
    readable = [
        item
        for item in candidates
        if item.identified and item.status is StreamStatus.READABLE
    ]
    saved = saved_mac.lower() if saved_mac else None
    matching = [item for item in readable if saved and item.mac == saved]
    if len(matching) == 1:
        return matching[0]
    if len(readable) == 1:
        if saved:
            logger.warning(
                "The camera's MAC address isn't the saved one. A router or WiFi "
                "extender can cause this, as can a different camera of the same model"
            )
        return readable[0]
    if readable:
        hosts = ", ".join(item.host for item in readable)
        raise DiscoveryError(f"Several cameras found: {hosts}. Use camera.host.")
    raise DiscoveryError(_no_camera_message(list(candidates)))


def _no_camera_message(candidates: list[Candidate]) -> str:
    """Explain why no camera could be chosen."""
    for item in candidates:
        if item.identified and item.status is StreamStatus.AUTH_REQUIRED:
            return f"Camera at {item.host} rejected the login. Check the secrets."
        if item.identified:
            return (
                f"Camera at {item.host} sent no stream. Check that ONVIF/RTSP is "
                "turned on and that camera.rtsp_path is correct."
            )
    if candidates:
        hosts = ", ".join(item.host for item in candidates)
        return (
            f"Devices found at {hosts}, but none identified as the camera. To use "
            "one, set camera.host."
        )
    return (
        "No camera found. Check that it's powered and ONVIF/RTSP is turned on. If "
        "it's on a different subnet, add that subnet with camera.scan_subnets."
    )


def log_candidates(candidates: Iterable[Candidate]) -> None:
    """Log each device found."""
    for item in candidates:
        logger.info(
            "Found %s: MAC %s, model %s, %s, %s",
            item.host, item.mac or "unknown", item.model or "unknown",
            "identified as the camera" if item.identified else "not identified",
            item.status.value,
        )  # fmt: skip
