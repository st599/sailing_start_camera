#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: network_client.py
#  Description: Run a start sequence through the network interface.
#  Date: 2026-11-09
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: None
#
#########################################################

"""Run a start sequence through the network interface, as a race control suite might.

It only uses Python's standard library, to show what a suite in any language needs
to do. The token comes from the STARTCAM_API_TOKEN environment variable:

    STARTCAM_API_TOKEN=... python examples/network_client.py \\
        https://startcam.local:8443 --certificate control_page.crt

It starts the stream, sends a one-class sequence, sets its start a few minutes ahead,
and logs each event until the start. The camera system's own certificate, copied
from ~/.config/start_camera/control_page.crt, lets the connection be checked.
"""

#
# SYSTEM IMPORTS
#

import argparse
import json
import logging
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger("network_client")

TOKEN_VARIABLE = "STARTCAM_API_TOKEN"
TIMEOUT_S = 30.0
TEST_SEQUENCES = {
    "items": [{"name": "Test", "class_flag": "T", "preparatory_flag": "P"}]
}

#
# FUNCTIONS
#


class Client:
    """Talks to the camera system's network interface."""

    def __init__(self, url: str, token: str, context: ssl.SSLContext) -> None:
        """Hold where it is and the token."""
        self._base = url.rstrip("/") + "/api/v1/"
        self._token = token
        self._context = context

    def call(self, method: str, path: str, body: object = None) -> dict[str, Any]:
        """Make a request and return its JSON reply.

        Raises:
            RuntimeError: If it's refused, with the camera system's reason.
        """
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self._base + path, data=data, method=method, headers=self._headers()
        )
        try:
            with urllib.request.urlopen(
                request, context=self._context, timeout=TIMEOUT_S
            ) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            reply = json.loads(error.read() or b"{}")
            raise RuntimeError(
                f"{method} {path}: {error.code} {reply.get('code')}: "
                f"{reply.get('error')}"
            ) from error

    def events(self, since: int) -> Iterator[dict[str, Any]]:
        """Yield each event after a number, as it happens."""
        request = urllib.request.Request(
            self._base + f"events/stream?since={since}", headers=self._headers()
        )
        with urllib.request.urlopen(request, context=self._context) as response:
            data = ""
            for raw in response:
                line = raw.decode().rstrip("\n")
                if line.startswith("data: "):
                    data = line.removeprefix("data: ")
                elif line == "" and data:
                    yield json.loads(data)
                    data = ""

    def _headers(self) -> dict[str, str]:
        """Return the headers every request needs."""
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }


def ssl_context(certificate: str | None, insecure: bool) -> ssl.SSLContext:
    """Return how to check the camera system's certificate."""
    if insecure:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    return ssl.create_default_context(cafile=certificate)


def run(client: Client, minutes: float, stop_stream: bool) -> None:
    """Run one start, driven entirely from here."""
    status = client.call("GET", "status")
    logger.info(
        "Camera live: %s, streaming: %s", status["camera_live"], status["streaming"]
    )
    if not status["clock"]["synchronised"]:
        logger.warning("The camera system's clock isn't synchronised with NTP")
    if not status["streaming"]:
        client.call("POST", "stream/start")
        logger.info("Started the stream")
    reply = client.call(
        "POST", "sequences", {"sequences": TEST_SEQUENCES, "force": True}
    )
    for warning in reply["warnings"]:
        logger.warning("Sequences: %s", warning)
    last_event = reply["status"]["last_event"]
    start = time.time() + minutes * 60
    request = {"start": start, "name": "Test", "sent_at": time.time()}
    client.call("POST", "race/set_start", request)
    logger.info("Start set for %s", time.strftime("%H:%M:%S", time.localtime(start)))
    for event in client.events(last_event):
        details = event["details"]
        logger.info(
            "Event %d at %s: %s %s",
            event["number"],
            time.strftime("%H:%M:%S", time.localtime(event["time"])),
            event["kind"],
            details.get("flag") or details.get("phase") or details.get("name") or "",
        )
        if event["kind"] == "start":
            difference = event["time"] - start
            logger.info("Started %.3f seconds from the time set", difference)
            break
    if stop_stream:
        client.call("POST", "stream/stop")
        logger.info("Stopped the stream")


def main() -> int:
    """Run the example."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", help="Such as https://startcam.local:8443")
    parser.add_argument("--certificate", help="The camera system's certificate")
    parser.add_argument(
        "--insecure", action="store_true", help="Don't check the certificate"
    )
    parser.add_argument(
        "--minutes", type=float, default=6, help="Minutes until the start"
    )
    parser.add_argument(
        "--stop-stream", action="store_true", help="Stop the stream afterwards"
    )
    arguments = parser.parse_args()
    token = os.environ.get(TOKEN_VARIABLE)
    if not token:
        logger.error("Set %s to the camera system's api_token", TOKEN_VARIABLE)
        return 2
    client = Client(
        arguments.url, token, ssl_context(arguments.certificate, arguments.insecure)
    )
    try:
        run(client, arguments.minutes, arguments.stop_stream)
    except (RuntimeError, OSError) as error:
        logger.error("%s", error)
        return 1
    return 0


#########################################################
# MAIN
#########################################################

if __name__ == "__main__":
    sys.exit(main())
