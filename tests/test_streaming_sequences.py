#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_streaming_sequences.py
#  Description: Tests for changing the stream's sequences while it runs.
#  Date: 2026-11-09
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: pytest
#
#########################################################

"""Tests for changing the stream's sequences while it runs."""

#
# SYSTEM IMPORTS
#

import time
from pathlib import Path

import numpy as np

#
# PROJECT IMPORTS
#
from start_camera.config import (
    Config,
    ControlConfig,
    Secrets,
    SequenceConfig,
    SequencesConfig,
    StreamConfig,
)
from start_camera.control import RaceControl
from start_camera.ingest import Frame
from start_camera.overlays.factory import build_compositor
from start_camera.pipeline import Streaming

#
#  GLOBAL VARIABLES
#


#
# FUNCTIONS
#


def sequences(name: str) -> SequencesConfig:
    """Return one class's sequence."""
    return SequencesConfig(items=[SequenceConfig(name=name, class_flag=name)])


def test_draw_uses_the_newest_overlays(tmp_path: Path) -> None:
    now = time.time()  # The stream's overlays use the real clock.
    config = Config(
        stream=StreamConfig(width=320, height=180),
        control=ControlConfig(journal_path=str(tmp_path / "journal.jsonl")),
    )
    control = RaceControl(config.sequences, tmp_path / "journal.jsonl", lambda: now)
    streaming = Streaming(config, Secrets(), control, source=None)
    frame = Frame(np.zeros((180, 320, 3), np.uint8), now, 1, True)
    assert streaming._draw(frame) is frame.image
    streaming._render = build_compositor(config, race_clock=control).render
    plain = streaming._draw(frame).copy()
    control.replace_sequences(sequences("A"))
    streaming.use_sequences(sequences("A"))
    control.start_now()
    assert not np.array_equal(streaming._draw(frame), plain)
    assert streaming._config.sequences.items[0].name == "A"
