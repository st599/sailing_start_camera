#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_viewer.py
#  Description: Tests for the viewer pages.
#  Date: 2026-10-31
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: pytest
#
#########################################################

"""Tests for the viewer pages."""

#
# SYSTEM IMPORTS
#

import http.client
from collections.abc import Iterator

import pytest

#
# PROJECT IMPORTS
#
from start_camera.config import StreamConfig
from start_camera.viewer import ViewerServer, build_pages, playlist_location

#
#  GLOBAL VARIABLES
#

STREAM = StreamConfig(hls_port=1234, webrtc_port=5678, viewer_port=1)

#
# FUNCTIONS
#


@pytest.fixture
def connection() -> Iterator[http.client.HTTPConnection]:
    """Return a connection to a running viewer server."""
    server = ViewerServer(STREAM.model_copy(update={"viewer_port": 0}))
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    try:
        yield connection
    finally:
        connection.close()
        server.stop()


def get(
    connection: http.client.HTTPConnection, path: str, method: str = "GET"
) -> tuple[int, str, http.client.HTTPResponse]:
    """Make a request and return the status, body and response."""
    connection.request(method, path)
    response = connection.getresponse()
    return response.status, response.read().decode(), response


def test_main_page_plays_hls(connection: http.client.HTTPConnection) -> None:
    status, body, response = get(connection, "/")
    assert status == 200
    assert response.getheader("Content-Type").startswith("text/html")
    assert ":1234/start/" in body
    assert "application/vnd.apple.mpegurl" in body


def test_low_delay_page_uses_webrtc(connection: http.client.HTTPConnection) -> None:
    status, body, _ = get(connection, "/live")
    assert status == 200
    assert ":5678/start/" in body


def test_page_ignores_query_string(connection: http.client.HTTPConnection) -> None:
    assert get(connection, "/?from=tv")[0] == 200


def test_head_has_no_body(connection: http.client.HTTPConnection) -> None:
    status, body, response = get(connection, "/", "HEAD")
    assert status == 200
    assert body == ""
    assert int(response.getheader("Content-Length")) > 0


def test_unknown_address_is_not_found(
    connection: http.client.HTTPConnection,
) -> None:
    assert get(connection, "/secrets.json")[0] == 404


def test_playlist_redirects_on_the_host_used(
    connection: http.client.HTTPConnection,
) -> None:
    status, _, response = get(connection, "/stream.m3u8")
    assert status == 302
    assert response.getheader("Location") == (
        "http://127.0.0.1:1234/start/index.m3u8"
    )


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("camera.local:8080", "http://camera.local:1234/start/index.m3u8"),
        ("10.0.0.2", "http://10.0.0.2:1234/start/index.m3u8"),
        ("[fe80::1]:8080", "http://[fe80::1]:1234/start/index.m3u8"),
        ("evil.example/path", None),
        ("a\r\nSet-Cookie: x", None),
        (None, None),
    ],
)
def test_playlist_location_checks_the_host(
    host: str | None, expected: str | None
) -> None:
    assert playlist_location(host, STREAM) == expected


def test_scripts_avoid_modern_javascript() -> None:
    for page in build_pages(STREAM).values():
        script = page.decode().split("<script>")[1]
        for modern in ("=>", "const ", "let ", "async ", "fetch(", "`"):
            assert modern not in script


def test_pages_load_nothing_from_the_internet() -> None:
    for page in build_pages(STREAM).values():
        text = page.decode()
        assert "https://" not in text
        assert "http://" not in text.replace('"http://" + location.hostname', "")


def test_port_in_use_raises() -> None:
    first = ViewerServer(STREAM.model_copy(update={"viewer_port": 0}))
    try:
        with pytest.raises(OSError):
            ViewerServer(STREAM.model_copy(update={"viewer_port": first.port}))
    finally:
        first.stop()
