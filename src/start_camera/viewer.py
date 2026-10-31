#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: viewer.py
#  Description: Serve simple pages for watching the stream.
#  Date: 2026-10-31
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: None
#
#########################################################

"""Serve simple pages for watching the stream.

The pages are self-contained, because a club network may have no internet. The scripts
avoid modern JavaScript, because smart TV browsers can be old.
"""

#
# SYSTEM IMPORTS
#

import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#
# PROJECT IMPORTS
#
from start_camera.config import StreamConfig
from start_camera.media_server import STREAM_PATH

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

PLAYER_OPTIONS = "controls=0&muted=1&autoplay=1&playsinline=1"
HOST_PATTERN = re.compile(r"^(\[[0-9a-fA-F:.]+\]|[A-Za-z0-9.-]+)(:\d+)?$")
STYLE = """html, body, video, iframe {
  margin: 0; width: 100%; height: 100%; border: 0; background: #000; overflow: hidden;
}
p { position: absolute; top: 40%; width: 100%; text-align: center; color: #fff;
  font: 24px sans-serif; }"""
HLS_PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Start line camera</title><style>{style}</style></head><body><script>
var base = "http://" + location.hostname + ":{hls_port}/{path}/";
var video = document.createElement("video");
function show(element) {{ document.body.appendChild(element); }}
function retry() {{ setTimeout(function () {{ location.reload(); }}, 5000); }}
if (video.canPlayType && video.canPlayType("application/vnd.apple.mpegurl")) {{
  video.src = base + "index.m3u8";
  video.autoplay = true;
  video.muted = true;
  video.setAttribute("playsinline", "");
  video.onerror = retry;
  video.onended = retry;
  show(video);
}} else if (window.MediaSource) {{
  var frame = document.createElement("iframe");
  frame.src = base + "?{options}";
  frame.allow = "autoplay; fullscreen";
  show(frame);
}} else {{
  var message = document.createElement("p");
  message.textContent = "This browser can't play the stream here. In a media " +
    "player, open " + base + "index.m3u8";
  show(message);
}}
</script></body></html>
"""
LOW_DELAY_PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Start line camera</title><style>{style}</style></head><body><script>
var frame = document.createElement("iframe");
frame.src = "http://" + location.hostname + ":{webrtc_port}/{path}/?{options}";
frame.allow = "autoplay; fullscreen";
document.body.appendChild(frame);
</script></body></html>
"""

#
# FUNCTIONS
#


def build_pages(stream: StreamConfig) -> dict[str, bytes]:
    """Return the page for each address."""
    values = {
        "style": STYLE,
        "path": STREAM_PATH,
        "options": PLAYER_OPTIONS,
        "hls_port": stream.hls_port,
        "webrtc_port": stream.webrtc_port,
    }
    return {
        "/": HLS_PAGE.format(**values).encode(),
        "/live": LOW_DELAY_PAGE.format(**values).encode(),
    }


def playlist_location(host_header: str | None, stream: StreamConfig) -> str | None:
    """Return the playlist address on the host a viewer used, if it's a valid host."""
    if host_header is None or not (match := HOST_PATTERN.match(host_header)):
        return None
    return f"http://{match.group(1)}:{stream.hls_port}/{STREAM_PATH}/index.m3u8"


def _make_handler(
    stream: StreamConfig, pages: dict[str, bytes]
) -> type[BaseHTTPRequestHandler]:
    """Return a request handler for the pages."""

    class ViewerHandler(BaseHTTPRequestHandler):
        """Serve the viewer pages and the playlist redirect."""

        def _reply(self, include_body: bool) -> None:
            """Reply to a request."""
            path = self.path.split("?", 1)[0]
            if path == "/stream.m3u8":
                self._redirect()
            elif path in pages:
                body = pages[path]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                if include_body:
                    self.wfile.write(body)
            else:
                self.send_error(404)

        def _redirect(self) -> None:
            """Send a media player to the HLS playlist."""
            location = playlist_location(self.headers.get("Host"), stream)
            if location is None:
                self.send_error(400)
                return
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802 - the name http.server requires
            """Reply to a GET request."""
            self._reply(include_body=True)

        def do_HEAD(self) -> None:  # noqa: N802 - the name http.server requires
            """Reply to a HEAD request, which some TV players send first."""
            self._reply(include_body=False)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            """Send request logs to the shared logger."""
            logger.debug(format, *args)

    return ViewerHandler


class ViewerServer:
    """Serve the viewer pages in a background thread."""

    def __init__(self, stream: StreamConfig) -> None:
        """Open the viewer port.

        Args:
            stream: The output settings.

        Raises:
            OSError: If the port can't be opened.
        """
        handler = _make_handler(stream, build_pages(stream))
        self._server = ThreadingHTTPServer(("", stream.viewer_port), handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="viewer"
        )
        self._thread.start()

    def stop(self) -> None:
        """Close the viewer port."""
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()
