#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: openapi.py
#  Description: The OpenAPI description of the network interface.
#  Date: 2026-11-09
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: None
#
#########################################################

"""The OpenAPI description of the network interface, served at /api/v1/openapi.json."""

#
#  GLOBAL VARIABLES
#

API_VERSION = "1.0.0"

_ERROR = {"$ref": "#/components/responses/Error"}
_STATUS_REPLY = {
    "description": "Done. The reply has the status afterwards",
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {"status": {"$ref": "#/components/schemas/Status"}},
            }
        }
    },
}
_ERRORS = {
    "400": _ERROR,
    "401": _ERROR,
    "403": _ERROR,
    "409": _ERROR,
    "429": _ERROR,
}
_RACE_COMMANDS = {
    "start_now": "Bring the next start forward, so its warning signal is now",
    "postpone": "Postpone the starts that haven't happened",
    "resume": "End a postponement",
    "general_recall": "Recall the most recent class",
    "individual_recall": "Fly flag X for the most recent class",
    "clear_individual_recall": "Take down flag X",
    "skip": "Move on to the next start",
    "abandon": "Stop all starts",
}


def _post(summary: str, body: dict[str, object] | None = None) -> dict[str, object]:
    """Return a POST operation that replies with the status."""
    operation: dict[str, object] = {
        "summary": summary,
        "responses": {"200": _STATUS_REPLY, **_ERRORS},
    }
    if body is not None:
        operation["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": body}},
        }
    return {"post": operation}


def _get(summary: str, schema: dict[str, object]) -> dict[str, object]:
    """Return a GET operation."""
    return {
        "get": {
            "summary": summary,
            "responses": {
                "200": {
                    "description": "Done",
                    "content": {"application/json": {"schema": schema}},
                },
                "401": _ERROR,
                "429": _ERROR,
            },
        }
    }


_TIME = {
    "oneOf": [
        {"type": "number", "description": "Seconds since the epoch"},
        {"type": "string", "description": "ISO 8601, with a time zone"},
    ]
}
_START = {
    "type": ["object", "null"],
    "properties": {
        "name": {"type": "string"},
        "class_flag": {"type": "string"},
        "start": {"type": "number"},
    },
}

OPENAPI: dict[str, object] = {
    "openapi": "3.1.0",
    "info": {
        "title": "Sailing start camera",
        "version": API_VERSION,
        "description": (
            "Control the start camera from a race control suite. Send "
            "'Authorization: Bearer <token>'. The read-only token can only use GET."
        ),
    },
    "servers": [{"url": "/api/v1"}],
    "security": [{"token": []}],
    "paths": {
        "/status": _get(
            "The racing, the camera, the stream and YouTube",
            {"$ref": "#/components/schemas/Status"},
        ),
        "/sequences": {
            **_get(
                "The day's sequences, as in the configuration file",
                {"type": "object", "properties": {"sequences": {"type": "object"}}},
            ),
            **_post(
                "Use new sequences straight away",
                {
                    "type": "object",
                    "required": ["sequences"],
                    "properties": {
                        "sequences": {
                            "type": "object",
                            "description": "As the configuration file's sequences",
                        },
                        "force": {"type": "boolean", "default": False},
                        "save": {"type": "boolean", "default": False},
                    },
                },
            ),
        },
        "/events": _get(
            "Recent events after a number",
            {
                "type": "object",
                "properties": {
                    "events": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Event"},
                    }
                },
            },
        ),
        "/events/stream": {
            "get": {
                "summary": (
                    "Events as Server-Sent Events. Starts after the since parameter "
                    "or the Last-Event-ID header"
                ),
                "parameters": [
                    {"name": "since", "in": "query", "schema": {"type": "integer"}}
                ],
                "responses": {
                    "200": {
                        "description": "A stream of events",
                        "content": {"text/event-stream": {}},
                    },
                    "401": _ERROR,
                    "503": _ERROR,
                },
            }
        },
        **{
            f"/race/{name}": _post(summary)
            for name, summary in _RACE_COMMANDS.items()
        },
        "/race/shift": _post(
            "Move every start that hasn't happened",
            {
                "type": "object",
                "required": ["minutes"],
                "properties": {"minutes": {"type": "number"}},
            },
        ),
        "/race/set_start": _post(
            "Set exactly when a start is. Needs this computer's clock to use NTP",
            {
                "type": "object",
                "required": ["start"],
                "properties": {
                    "start": _TIME,
                    "name": {"type": "string"},
                    "sent_at": _TIME,
                },
            },
        ),
        "/recording/start": _post("Start recording, while streaming"),
        "/recording/stop": _post("Stop recording"),
        "/stream/start": _post("Start the stream"),
        "/stream/stop": _post("Stop the stream"),
    },
    "components": {
        "securitySchemes": {"token": {"type": "http", "scheme": "bearer"}},
        "responses": {
            "Error": {
                "description": "Refused, with the reason",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "error": {"type": "string"},
                                "code": {"type": "string"},
                                "problems": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                        }
                    }
                },
            }
        },
        "schemas": {
            "Status": {
                "type": "object",
                "properties": {
                    "time": {"type": "number"},
                    "phase": {"type": "string"},
                    "display": {"type": "string"},
                    "flags": {"type": "array", "items": {"type": "string"}},
                    "upcoming": _START,
                    "latest": _START,
                    "camera_live": {"type": "boolean"},
                    "streaming": {"type": "boolean"},
                    "recording": {"type": "boolean"},
                    "youtube": {"enum": ["off", "connecting", "live"]},
                    "clock": {
                        "type": "object",
                        "properties": {
                            "time": {"type": "number"},
                            "synchronised": {"type": ["boolean", "null"]},
                            "last_difference": {"type": ["number", "null"]},
                        },
                    },
                    "viewers": {"type": "object"},
                    "last_event": {"type": "integer"},
                },
            },
            "Event": {
                "type": "object",
                "properties": {
                    "number": {"type": "integer"},
                    "time": {"type": "number"},
                    "kind": {"type": "string"},
                    "details": {"type": "object"},
                },
            },
        },
    },
}
