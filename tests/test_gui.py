#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_gui.py
#  Description: Tests for the GUI, without a display.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, pytest, pytest-qt, PySide6
#
#########################################################

"""Tests for the GUI, without a display."""

#
# SYSTEM IMPORTS
#

import logging
import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

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
from start_camera.ingest import Frame
from start_camera.media_server import MediaServerError

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QPoint, QSize, Qt  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QMessageBox, QPushButton, QScrollArea  # noqa: E402

from start_camera.gui.main_window import MainWindow, VideoView  # noqa: E402
from start_camera.gui.session import GuiSession  # noqa: E402
from start_camera.race_clock import Phase  # noqa: E402

#
#  GLOBAL VARIABLES
#

T0 = datetime(2026, 11, 1, 12, 0).timestamp()
WIDTH = 64
HEIGHT = 36

#
# FUNCTIONS
#


class FakeSource:
    """Stand in for the camera."""

    def __init__(self, config: Config, secrets: Secrets) -> None:
        """Make a frame."""
        self.frame = Frame(np.full((HEIGHT, WIDTH, 3), 90, np.uint8), T0, 1, True)
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        """Note the call."""
        self.started += 1

    def stop(self) -> None:
        """Note the call."""
        self.stopped += 1

    def latest(self) -> Frame:
        """Return the frame."""
        return self.frame


class FakeStreaming:
    """Stand in for the stream."""

    instances: list["FakeStreaming"] = []
    start_error: Exception | None = None
    release: threading.Event | None = None

    def __init__(self, *_: object) -> None:
        """Note the instance."""
        self.started = self.stopped = 0
        FakeStreaming.instances.append(self)

    def start(self) -> None:
        """Note the call, or fail or wait as the test asks."""
        if FakeStreaming.release is not None:
            FakeStreaming.release.wait(5)
        if FakeStreaming.start_error is not None:
            raise FakeStreaming.start_error
        self.started += 1

    def stop(self) -> None:
        """Note the call."""
        self.stopped += 1


@pytest.fixture(autouse=True)
def reset_fake() -> Iterator[None]:
    """Clear what the fake remembers."""
    FakeStreaming.instances = []
    FakeStreaming.start_error = None
    FakeStreaming.release = None
    yield


class Harness:
    """A window, with its session and settable time and answers."""

    def __init__(self, qtbot: object, tmp_path: Path, **options: object) -> None:
        """Build the window."""
        self.now = T0
        self.answers: list[str] = []
        self.answer_yes = True
        items = [
            SequenceConfig(name="Solo", class_flag="Solo"),
            SequenceConfig(name="Topper", class_flag="T"),
        ]
        config = Config(
            stream=StreamConfig(width=WIDTH, height=HEIGHT),
            sequences=SequencesConfig(items=items),
            control=ControlConfig(journal_path=str(tmp_path / "j.jsonl")),
            **options,
        )
        self.session = GuiSession(
            config,
            Secrets(),
            make_source=FakeSource,
            make_streaming=FakeStreaming,
            clock=lambda: self.now,
        )
        self.source = self.session._source
        self.window = MainWindow(self.session)
        qtbot.addWidget(self.window)
        self.window._confirm = self._confirm
        self.qtbot = qtbot

    def _confirm(self, question: str) -> bool:
        self.answers.append(question)
        return self.answer_yes

    def button(self, text: str) -> QPushButton:
        """Return the button with some text."""
        for button in self.window.findChildren(QPushButton):
            if button.text() == text:
                return button
        raise AssertionError(f"No button {text}")

    def press(self, text: str) -> None:
        """Press a button."""
        self.qtbot.mouseClick(self.button(text), Qt.MouseButton.LeftButton)

    def phase(self) -> Phase:
        """Return the race's phase."""
        return self.session.summary().phase


@pytest.fixture
def harness(qtbot: object, tmp_path: Path) -> Harness:
    """Return a started window."""
    built = Harness(qtbot, tmp_path)
    built.window.start()
    built.window._timer.stop()
    return built


def test_start_starts_the_camera(harness: Harness) -> None:
    assert harness.source.started == 1


def test_frame_timer_follows_the_output_rate(qtbot: object, tmp_path: Path) -> None:
    built = Harness(qtbot, tmp_path)
    assert built.window._timer.interval() == 1000 // built.session.config.stream.fps


def test_picture_is_shown_with_the_overlays(harness: Harness) -> None:
    harness.window.refresh()
    frame = harness.window.video._frame
    assert frame.shape == (HEIGHT, WIDTH, 3)
    assert not np.array_equal(frame, harness.source.frame.image)


def test_preview_doesnt_change_the_camera_frame(harness: Harness) -> None:
    harness.window.refresh()
    assert (harness.source.frame.image == 90).all()


def test_shows_the_same_time_and_flags_as_the_controls(harness: Harness) -> None:
    harness.window.refresh()
    assert harness.window.time_label.text() == "READY"
    assert harness.window.flags_label.text() == "No flags"
    harness.press("Start now")
    assert harness.window.time_label.text() == "Solo -05:00"
    assert harness.window.flags_label.text() == "Flags: Solo"


def test_shows_commands_from_other_controls(harness: Harness) -> None:
    harness.session.control.start_now()
    harness.session.control.postpone()
    harness.window.refresh()
    assert harness.window.time_label.text() == "POSTPONED"
    assert harness.window.flags_label.text() == "Flags: AP"


def test_camera_status(harness: Harness) -> None:
    harness.window.refresh()
    assert harness.window.camera_label.text() == "Camera: live"
    frame = harness.source.frame
    harness.source.frame = Frame(frame.image, T0, 1, False)
    harness.window.refresh()
    assert harness.window.camera_label.text() == "Camera: no signal"


@pytest.mark.parametrize(
    ("button", "phase"), [("Postpone", Phase.POSTPONED)]
)
def test_race_buttons_send_commands(
    harness: Harness, button: str, phase: Phase
) -> None:
    harness.press("Start now")
    harness.press(button)
    assert harness.phase() is phase


def test_resume_button(harness: Harness) -> None:
    harness.press("Start now")
    harness.press("Postpone")
    harness.press("Resume")
    assert harness.phase() is Phase.WAITING


@pytest.mark.parametrize(
    ("label", "minutes"),
    [("+1 min", 1), ("+5 min", 5), ("+10 min", 10), ("-1 min", -1)],
)
def test_delay_buttons(harness: Harness, label: str, minutes: int) -> None:
    harness.press("Start now")
    harness.press(label)
    start = harness.session.control.state(T0).upcoming.start
    assert start == T0 + 300 + minutes * 60


def test_refused_command_explains_why(harness: Harness) -> None:
    harness.press("Resume")
    assert "isn't postponed" in harness.window.message_label.text()


def test_message_clears_on_the_next_command(harness: Harness) -> None:
    harness.press("Resume")
    harness.press("Start now")
    assert harness.window.message_label.text() == ""


@pytest.mark.parametrize(
    ("label", "question"),
    [
        ("Abandon", "Abandon the racing?"),
        ("Next start", "Skip the next start?"),
        ("General recall", "General recall?"),
    ],
)
def test_disruptive_buttons_ask_first(
    harness: Harness, label: str, question: str
) -> None:
    harness.press("Start now")
    before = harness.session.control.state(T0)
    harness.answer_yes = False
    harness.press(label)
    assert harness.answers == [question]
    assert harness.session.control.state(T0) == before


def test_abandon_when_confirmed(harness: Harness) -> None:
    harness.press("Abandon")
    assert harness.phase() is Phase.ABANDONED


def test_general_recall_when_confirmed(harness: Harness) -> None:
    harness.press("Start now")
    harness.now += 300
    harness.press("General recall")
    assert "First Substitute" in harness.session.summary().flags


def test_skip_when_confirmed(harness: Harness) -> None:
    harness.press("Start now")
    harness.press("Next start")
    assert harness.session.control.state(T0).upcoming.name == "Topper"


def test_individual_recall_button_toggles(harness: Harness) -> None:
    harness.press("Start now")
    harness.now += 300
    harness.press("Individual recall")
    assert "X" in harness.session.summary().flags
    harness.press("Clear X")
    assert "X" not in harness.session.summary().flags
    assert harness.button("Individual recall") is not None


def wait_for_stream(harness: Harness) -> None:
    """Wait for the background work on the stream to finish."""
    harness.qtbot.waitUntil(lambda: not harness.window._busy, timeout=5000)


def test_stream_starts_and_stops(harness: Harness) -> None:
    harness.press("Start streaming")
    wait_for_stream(harness)
    assert harness.session.is_streaming
    assert harness.window.stream_label.text() == "Stream: ON"
    assert "TV or browser" in harness.window.addresses_label.text()
    harness.press("Stop streaming")
    wait_for_stream(harness)
    assert not harness.session.is_streaming
    assert harness.window.stream_label.text() == "Stream: off"
    assert harness.window.addresses_label.text() == ""
    assert FakeStreaming.instances[0].stopped == 1


def test_stopping_the_stream_asks_first(harness: Harness) -> None:
    harness.press("Start streaming")
    wait_for_stream(harness)
    harness.answer_yes = False
    harness.press("Stop streaming")
    assert harness.session.is_streaming


def test_stopping_the_stream_leaves_the_preview_running(harness: Harness) -> None:
    harness.press("Start streaming")
    wait_for_stream(harness)
    harness.press("Stop streaming")
    wait_for_stream(harness)
    assert harness.source.stopped == 0
    harness.window.refresh()


def test_window_stays_responsive_while_the_stream_starts(harness: Harness) -> None:
    FakeStreaming.release = threading.Event()
    harness.press("Start streaming")
    assert not harness.button("Start streaming").isEnabled()
    assert harness.window.stream_label.text() == "Stream: please wait..."
    harness.press("Start now")
    assert harness.window.time_label.text() == "Solo -05:00"
    FakeStreaming.release.set()
    wait_for_stream(harness)
    assert harness.session.is_streaming


@pytest.mark.parametrize(
    "problem",
    [OSError("Address already in use"), MediaServerError("Can't download MediaMTX")],
)
def test_failure_to_stream_is_shown(harness: Harness, problem: Exception) -> None:
    FakeStreaming.start_error = problem
    harness.press("Start streaming")
    wait_for_stream(harness)
    assert not harness.session.is_streaming
    assert str(problem) in harness.window.message_label.text()
    assert harness.button("Start streaming").isEnabled()


def test_stream_can_start_again_after_failing(harness: Harness) -> None:
    FakeStreaming.start_error = OSError("busy")
    harness.press("Start streaming")
    wait_for_stream(harness)
    FakeStreaming.start_error = None
    harness.press("Start streaming")
    wait_for_stream(harness)
    assert harness.session.is_streaming


def test_closing_stops_everything(harness: Harness) -> None:
    harness.press("Start streaming")
    wait_for_stream(harness)
    event = QCloseEvent()
    harness.window.closeEvent(event)
    assert event.isAccepted()
    assert FakeStreaming.instances[0].stopped == 1
    assert harness.source.stopped == 1


def test_closing_a_running_stream_asks_first(harness: Harness) -> None:
    harness.press("Start streaming")
    wait_for_stream(harness)
    harness.answer_yes = False
    event = QCloseEvent()
    harness.window.closeEvent(event)
    assert not event.isAccepted()
    assert harness.session.is_streaming
    harness.answer_yes = True


def test_closing_without_a_stream_doesnt_ask(harness: Harness) -> None:
    event = QCloseEvent()
    harness.window.closeEvent(event)
    assert event.isAccepted()
    assert harness.answers == []


def test_log_messages_appear_in_the_window(harness: Harness, qtbot: object) -> None:
    logging.getLogger("gui-test").warning("something to see")
    qtbot.waitUntil(
        lambda: "something to see" in harness.window.log_view.toPlainText(),
        timeout=2000,
    )


def test_log_handler_is_removed_on_closing(harness: Harness) -> None:
    harness.window.closeEvent(QCloseEvent())
    assert harness.window._log_handler not in logging.getLogger().handlers


def test_real_confirm_uses_a_message_box(
    qtbot: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = Harness(qtbot, tmp_path)
    del built.window._confirm
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_: QMessageBox.StandardButton.Yes,
    )
    assert built.window._confirm("Sure?") is True


@pytest.mark.parametrize(
    ("widget", "frame", "expected"),
    [
        (QSize(640, 360), (36, 64), (0, 0, 640, 360)),
        (QSize(640, 720), (36, 64), (0, 180, 640, 360)),
        (QSize(1000, 360), (36, 64), (180, 0, 640, 360)),
    ],
)
def test_picture_keeps_its_shape_and_stays_centred(
    qtbot: object,
    widget: QSize,
    frame: tuple[int, int],
    expected: tuple[int, int, int, int],
) -> None:
    view = VideoView()
    qtbot.addWidget(view)
    view.resize(widget)
    view.show_frame(np.zeros((*frame, 3), dtype=np.uint8))
    area = view.picture_area()
    assert (area.x(), area.y(), area.width(), area.height()) == expected


def test_picture_is_drawn_in_the_right_colours(qtbot: object) -> None:
    view = VideoView()
    qtbot.addWidget(view)
    view.resize(64, 36)
    frame = np.zeros((36, 64, 3), dtype=np.uint8)
    frame[:, :] = (10, 20, 200)
    view.show_frame(frame)
    colour = view.grab().toImage().pixelColor(30, 18)
    assert (colour.red(), colour.green(), colour.blue()) == (200, 20, 10)


def test_nothing_to_show_is_safe(qtbot: object) -> None:
    view = VideoView()
    qtbot.addWidget(view)
    assert view.picture_area().isEmpty()
    view.grab()


def test_window_fits_a_small_screen(harness: Harness) -> None:
    assert harness.window.minimumWidth() <= 1024
    assert harness.window.minimumHeight() <= 600
    harness.window.resize(1024, 600)
    assert harness.window.size() == QSize(1024, 600)


@pytest.mark.parametrize("size", [(1024, 600), (1200, 720), (1920, 1080)])
def test_every_button_fits_in_the_panel(
    harness: Harness, qtbot: object, size: tuple[int, int]
) -> None:
    harness.window.show()
    harness.window.resize(*size)
    qtbot.wait(50)
    viewport = harness.window.findChild(QScrollArea).viewport()
    for button in harness.window.findChildren(QPushButton):
        left = button.mapTo(viewport, QPoint(0, 0)).x()
        assert left >= 0, button.text()
        assert left + button.width() <= viewport.width(), button.text()
        assert button.width() >= button.fontMetrics().horizontalAdvance(button.text())
