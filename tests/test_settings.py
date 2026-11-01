#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_settings.py
#  Description: Tests for the settings form and configuration files.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: pytest, pytest-qt, PySide6
#
#########################################################

"""Tests for the settings form and configuration files."""

#
# SYSTEM IMPORTS
#

import json
import subprocess
import sys
import threading
from datetime import time
from pathlib import Path

import numpy as np
import pytest

#
# PROJECT IMPORTS
#
from start_camera.config import (
    CameraConfig,
    Config,
    ConfigError,
    ControlConfig,
    LinePoint,
    Secrets,
    SequenceConfig,
    SequencesConfig,
    StartLineConfig,
    StreamConfig,
    load_config,
    save_config,
)
from start_camera.discovery import Candidate, DiscoveryError, StreamStatus
from start_camera.gui.settings_model import (
    FIELDS,
    GENERAL_ERRORS,
    ConfigDocument,
    apply_fields,
    read_fields,
)
from start_camera.ingest import Frame

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QFileDialog, QMessageBox  # noqa: E402

from start_camera.gui.main_window import MainWindow  # noqa: E402
from start_camera.gui.session import GuiSession  # noqa: E402
from start_camera.gui.settings_dialog import (  # noqa: E402
    SettingsDialog,
    describe_candidate,
    order_candidates,
)

#
#  GLOBAL VARIABLES
#

MAC = "6c:00:00:00:00:01"
CAMERA = Candidate("10.0.0.5", MAC, "CAMS3W", True, StreamStatus.READABLE)
OTHER = Candidate("10.0.0.9", None, None, False, StreamStatus.AUTH_REQUIRED)

#
# FUNCTIONS
#


def rich_config() -> Config:
    """Return a configuration with settings both on and off the form."""
    return Config(
        camera=CameraConfig(host="10.0.0.5", mac=MAC, scan_subnets=["10.9.0.0/24"]),
        start_line=StartLineConfig(
            enabled=True, points=[LinePoint(x=0.1, y=0.5), LinePoint(x=0.9, y=0.5)]
        ),
        sequences=SequencesConfig(
            first_start=time(13, 0),
            day_flags=["Y"],
            items=[SequenceConfig(name="Solo", class_flag="Solo")],
        ),
        control=ControlConfig(delay_steps=[2, -2]),
    )


def test_unchanged_form_gives_the_same_configuration() -> None:
    config = rich_config()
    result, errors = apply_fields(config, read_fields(config))
    assert errors == {}
    assert result == config


def test_settings_off_the_form_are_kept() -> None:
    config = rich_config()
    values = read_fields(config)
    values["stream.fps"] = "15"
    result, _ = apply_fields(config, values)
    assert result.stream.fps == 15
    assert result.sequences == config.sequences
    assert result.start_line == config.start_line
    assert result.control == config.control


def test_original_configuration_isnt_changed() -> None:
    config = rich_config()
    values = read_fields(config)
    values["stream.fps"] = "15"
    apply_fields(config, values)
    assert config.stream.fps == 20


@pytest.mark.parametrize(
    ("path", "text", "expected"),
    [
        ("stream.fps", "abc", "whole number"),
        ("stream.fps", "1.5", "whole number"),
        ("stream.fps", "", "whole number"),
        ("stream.fps", "0", "greater than or equal to 1"),
        ("stream.width", "1281", "multiple of 2"),
        ("camera.rtsp_port", "70000", "less than or equal to 65535"),
        ("camera.mac", "not-a-mac", "pattern"),
        ("camera.capture_latency_ms", "-1", "greater than or equal to 0"),
        ("camera.scan_subnets", "10.0.0.0/8", "bigger than"),
        ("camera.scan_subnets", "nonsense", "nonsense"),
        ("overlays.datetime.scale", "abc", "Enter a number"),
        ("overlays.datetime.scale", "0", "greater than 0"),
        ("overlays.countdown.colour", "red", "pattern"),
        ("stream.youtube_ingest_url", "http://example", "pattern"),
        ("camera.rtsp_path", "", "at least 1"),
    ],
)
def test_invalid_value_is_explained_against_its_field(
    path: str, text: str, expected: str
) -> None:
    config = Config()
    values = read_fields(config)
    values[path] = text
    result, errors = apply_fields(config, values)
    assert result is None
    assert list(errors) == [path]
    assert expected in errors[path]


def test_all_problems_are_reported_together() -> None:
    values = read_fields(Config())
    values["stream.fps"] = "x"
    values["camera.rtsp_port"] = "y"
    _, errors = apply_fields(Config(), values)
    assert set(errors) == {"stream.fps", "camera.rtsp_port"}


def test_clashing_stream_ports_are_a_general_error() -> None:
    values = read_fields(Config())
    values["stream.hls_port"] = "8554"
    result, errors = apply_fields(Config(), values)
    assert result is None
    assert "must all be different" in errors[GENERAL_ERRORS]


def test_clash_with_the_control_page_port_is_a_general_error() -> None:
    values = read_fields(Config())
    values["stream.hls_port"] = "8443"
    _, errors = apply_fields(Config(), values)
    assert "page_port" in errors[GENERAL_ERRORS]


def test_blank_address_means_scan() -> None:
    config = rich_config()
    values = read_fields(config)
    values["camera.host"] = "  "
    values["camera.mac"] = ""
    result, _ = apply_fields(config, values)
    assert result.camera.host is None
    assert result.camera.mac is None


def test_subnets_are_a_comma_separated_list() -> None:
    values = read_fields(Config())
    values["camera.scan_subnets"] = "10.1.0.0/24, 10.2.0.0/24,"
    result, _ = apply_fields(Config(), values)
    assert result.camera.scan_subnets == ["10.1.0.0/24", "10.2.0.0/24"]


def test_no_field_is_a_secret() -> None:
    for field in FIELDS:
        assert "password" not in field.path
        assert "key" not in field.path.replace("keyframe", "")


def test_saved_configuration_contains_no_secrets(tmp_path: Path) -> None:
    document = ConfigDocument(rich_config())
    path = tmp_path / "c.json"
    document.save(path)
    text = path.read_text(encoding="utf-8").lower()
    assert "password" not in text
    assert "stream_key" not in text


def test_new_document_is_unsaved_and_untitled() -> None:
    document = ConfigDocument(Config())
    assert document.name == "Untitled"
    assert not document.modified


def test_update_marks_real_changes_only() -> None:
    document = ConfigDocument(Config())
    assert document.update(Config()) is False
    assert not document.modified
    assert document.update(Config(stream=StreamConfig(fps=10))) is True
    assert document.modified
    assert document.name == "Untitled *"


def test_save_clears_the_mark_and_remembers_the_file(tmp_path: Path) -> None:
    document = ConfigDocument(Config())
    document.update(Config(stream=StreamConfig(fps=10)))
    path = tmp_path / "race.json"
    document.save(path)
    assert not document.modified
    assert document.name == "race.json"
    document.update(Config(stream=StreamConfig(fps=12)))
    document.save()
    assert load_config(path).stream.fps == 12


def test_save_with_no_file_is_an_error() -> None:
    with pytest.raises(ValueError):
        ConfigDocument(Config()).save()


def test_open_reports_a_bad_file(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ConfigError, match="version"):
        ConfigDocument.open(path)


def test_configuration_from_the_form_runs_in_startcam(tmp_path: Path) -> None:
    config = rich_config()
    values = read_fields(config)
    values["stream.fps"] = "15"
    values["overlays.flags.position"] = "bottom_left"
    result, _ = apply_fields(config, values)
    path = tmp_path / "c.json"
    ConfigDocument(result).save(path)
    code = subprocess.run(
        [sys.executable, "-m", "start_camera.cli", "--check-config",
         "--config", str(path)],
        capture_output=True, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    assert code.returncode == 0, code.stderr


def make_dialog(
    qtbot: object, config: Config | None = None, **options: object
) -> SettingsDialog:
    """Return a settings form."""
    dialog = SettingsDialog(config or Config(), Secrets(), **options)
    qtbot.addWidget(dialog)
    return dialog


def test_form_shows_the_configuration(qtbot: object) -> None:
    dialog = make_dialog(qtbot, rich_config())
    assert dialog.values() == read_fields(rich_config())


def test_error_appears_next_to_the_field_and_form_stays_open(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    dialog.set_value("stream.fps", "lots")
    dialog.accept_values()
    assert dialog.result_config is None
    assert dialog.error_for("stream.fps") == "Enter a whole number"
    assert dialog.error_for("stream.width") == ""


@pytest.mark.parametrize(
    ("path", "tab"),
    [("camera.rtsp_port", "Camera"), ("stream.fps", "Stream"),
     ("overlays.flags.scale", "Overlays")],
)  # fmt: skip
def test_form_shows_the_tab_with_the_problem(
    qtbot: object, path: str, tab: str
) -> None:
    dialog = make_dialog(qtbot)
    dialog._tabs.setCurrentIndex(2 if tab != "Overlays" else 0)
    dialog.set_value(path, "bad")
    dialog.accept_values()
    assert dialog._tabs.tabText(dialog._tabs.currentIndex()) == tab


def test_error_clears_once_fixed(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    dialog.set_value("stream.fps", "lots")
    dialog.accept_values()
    dialog.set_value("stream.fps", "10")
    dialog.accept_values()
    assert dialog.error_for("stream.fps") == ""
    assert dialog.result_config.stream.fps == 10


def test_general_error_is_shown(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    dialog.set_value("stream.hls_port", "8554")
    dialog.accept_values()
    assert "must all be different" in dialog.general_error.text()


def test_error_text_cant_contain_markup(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    dialog.set_value("camera.scan_subnets", "<b>bad</b>")
    dialog.accept_values()
    assert "<b>" in dialog.error_for("camera.scan_subnets")


def test_position_is_a_choice(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    dialog.set_value("overlays.flags.position", "bottom_left")
    dialog.accept_values()
    assert dialog.result_config.overlays.flags.position.value == "bottom_left"


def test_checkbox_is_stored(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    dialog.set_value("stream.youtube_enabled", True)
    dialog.accept_values()
    assert dialog.result_config.stream.youtube_enabled is True


def test_scan_lists_devices_with_the_camera_first(qtbot: object) -> None:
    dialog = make_dialog(qtbot, scan=lambda *_: [OTHER, CAMERA])
    dialog.scan_button.click()
    qtbot.waitUntil(lambda: dialog.scan_results.count() == 2, timeout=3000)
    assert dialog.scan_results.item(0).text().startswith("10.0.0.5")
    assert dialog.scan_status.text() == "Camera found at 10.0.0.5"
    assert dialog.scan_button.isEnabled()


def test_using_a_scanned_camera_fills_in_the_form(qtbot: object) -> None:
    dialog = make_dialog(qtbot, scan=lambda *_: [OTHER, CAMERA])
    dialog.scan_button.click()
    qtbot.waitUntil(lambda: dialog.use_button.isEnabled(), timeout=3000)
    dialog.use_button.click()
    dialog.accept_values()
    assert dialog.result_config.camera.host == "10.0.0.5"
    assert dialog.result_config.camera.mac == MAC


def test_using_a_device_with_no_mac_clears_the_old_one(qtbot: object) -> None:
    config = Config(camera=CameraConfig(host="10.0.0.5", mac=MAC))
    dialog = make_dialog(qtbot, config, scan=lambda *_: [OTHER])
    dialog.scan_button.click()
    qtbot.waitUntil(lambda: dialog.use_button.isEnabled(), timeout=3000)
    dialog.use_button.click()
    assert dialog.values()["camera.host"] == "10.0.0.9"
    assert dialog.values()["camera.mac"] == ""


def test_scan_finding_nothing_explains_why(qtbot: object) -> None:
    dialog = make_dialog(qtbot, scan=lambda *_: [])
    dialog.scan_button.click()
    qtbot.waitUntil(
        lambda: "No camera found" in dialog.scan_status.text(), timeout=3000
    )
    assert not dialog.use_button.isEnabled()


@pytest.mark.parametrize("problem", [DiscoveryError("too big"), OSError("no network")])
def test_scan_failure_is_shown(qtbot: object, problem: Exception) -> None:
    def fail(*_: object) -> list[Candidate]:
        raise problem

    dialog = make_dialog(qtbot, scan=fail)
    dialog.scan_button.click()
    qtbot.waitUntil(lambda: str(problem) in dialog.scan_status.text(), timeout=3000)
    assert dialog.scan_button.isEnabled()


def test_scan_stays_responsive(qtbot: object) -> None:
    release = threading.Event()

    def slow(*_: object) -> list[Candidate]:
        release.wait(5)
        return [CAMERA]

    dialog = make_dialog(qtbot, scan=slow)
    dialog.scan_button.click()
    assert not dialog.scan_button.isEnabled()
    assert dialog.scan_status.text() == "Scanning..."
    release.set()
    qtbot.waitUntil(dialog.scan_button.isEnabled, timeout=3000)


def test_scan_uses_the_values_on_the_form(qtbot: object) -> None:
    seen: list[Config] = []

    def record(config: Config, secrets: Secrets) -> list[Candidate]:
        seen.append(config)
        return []

    dialog = make_dialog(qtbot, scan=record)
    dialog.set_value("camera.scan_subnets", "10.5.0.0/24")
    dialog.scan_button.click()
    qtbot.waitUntil(lambda: bool(seen), timeout=3000)
    assert seen[0].camera.scan_subnets == ["10.5.0.0/24"]


def test_candidates_are_ordered_and_described() -> None:
    weak = Candidate("10.0.0.7", None, "CAMS3W", True, StreamStatus.NO_STREAM)
    assert order_candidates([OTHER, weak, CAMERA]) == [CAMERA, weak, OTHER]
    text = describe_candidate(OTHER)
    assert "unknown model" in text
    assert "not identified" in text


class FakeSource:
    """Stand in for the camera."""

    def __init__(self, config: Config, secrets: Secrets) -> None:
        """Make a frame to the configured size."""
        size = (config.stream.height, config.stream.width, 3)
        self.frame = Frame(np.full(size, 90, np.uint8), 0.0, 1, True)
        self.stopped = 0

    def start(self) -> None:
        """Do nothing."""

    def stop(self) -> None:
        """Note the call."""
        self.stopped += 1

    def latest(self) -> Frame:
        """Return the frame."""
        return self.frame


class FakeStreaming:
    """Stand in for the stream."""

    def __init__(self, *_: object) -> None:
        """Do nothing."""

    def start(self) -> None:
        """Do nothing."""

    def stop(self) -> None:
        """Do nothing."""


class Harness:
    """A window with stand-ins for its dialogs."""

    def __init__(self, qtbot: object, tmp_path: Path) -> None:
        """Build the window."""
        self.tmp_path = tmp_path
        self.config = Config(
            stream=StreamConfig(width=64, height=36),
            control=ControlConfig(journal_path=str(tmp_path / "j.jsonl")),
        )
        self.document = ConfigDocument(self.config)
        self.window = MainWindow(
            self.make_session(self.config),
            self.document,
            make_session=self.make_session,
        )
        qtbot.addWidget(self.window)
        self.window.start()
        self.window._timer.stop()
        self.problems: list[str] = []
        self.window._show_problem = self.problems.append
        self.save_answer = QMessageBox.StandardButton.Discard
        self.asked = 0
        self.window._ask_to_save = self._ask

    def make_session(self, config: Config) -> GuiSession:
        """Return a session with stand-ins."""
        return GuiSession(
            config, Secrets(), make_source=FakeSource, make_streaming=FakeStreaming
        )

    def _ask(self) -> QMessageBox.StandardButton:
        self.asked += 1
        return self.save_answer

    def change(self, **stream: object) -> None:
        """Change the configuration, as the settings form would."""
        settings = self.document.config.stream.model_copy(update=stream)
        new = self.document.config.model_copy(update={"stream": settings})
        self.document.update(new)
        self.window._show_title()


@pytest.fixture
def harness(qtbot: object, tmp_path: Path) -> Harness:
    """Return a started window."""
    return Harness(qtbot, tmp_path)


def accept_settings(monkeypatch: pytest.MonkeyPatch, config: Config | None) -> None:
    """Make the settings form return straight away, accepted if given a result."""

    class Answer:
        result_config = config

        def exec(self) -> int:
            return 1 if config is not None else 0

    monkeypatch.setattr(MainWindow, "_settings_dialog", lambda self: Answer())


def test_title_shows_the_file_and_unsaved_changes(harness: Harness) -> None:
    assert harness.window.windowTitle().startswith("Untitled -")
    harness.change(fps=10)
    assert harness.window.windowTitle().startswith("Untitled * -")


def test_new_settings_are_used_straight_away(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = harness.window._session
    settings = harness.config.model_copy(
        update={"stream": StreamConfig(width=32, height=18, fps=10)}
    )
    accept_settings(monkeypatch, settings)
    harness.window._open_settings()
    assert harness.window._session is not old
    assert old._source.stopped == 1
    assert harness.window._session.config.stream.fps == 10
    assert harness.window.video._frame.shape == (18, 32, 3)
    assert harness.window._timer.interval() == 100
    assert harness.document.modified
    harness.window._timer.stop()


def test_cancelling_or_no_change_keeps_the_session(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = harness.window._session
    accept_settings(monkeypatch, None)
    harness.window._open_settings()
    accept_settings(monkeypatch, harness.config)
    harness.window._open_settings()
    assert harness.window._session is old
    assert not harness.document.modified


def test_commands_go_to_the_new_session(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = harness.config.model_copy(
        update={"sequences": SequencesConfig(items=[
            SequenceConfig(name="Solo", class_flag="Solo")
        ])}
    )
    accept_settings(monkeypatch, settings)
    harness.window._open_settings()
    harness.window._timer.stop()
    harness.window._send_command("start_now")
    assert harness.window.time_label.text() == "Solo -05:00"


def test_delay_buttons_follow_the_configuration(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, qtbot: object
) -> None:
    settings = harness.config.model_copy(
        update={"control": harness.config.control.model_copy(
            update={"delay_steps": [3.0]}
        )}
    )
    accept_settings(monkeypatch, settings)
    harness.window._open_settings()
    harness.window._timer.stop()
    qtbot.wait(10)
    assert harness.window._delay_row.count() == 1


def test_settings_cant_change_while_streaming(harness: Harness) -> None:
    harness.window._session.start_stream()
    harness.window._open_settings()
    harness.window._open_file()
    assert len(harness.problems) == 2
    assert "Stop the stream" in harness.problems[0]
    harness.window._session.stop_stream()


def test_save_writes_a_file_startcam_can_read(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "saved.json"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *_: (str(path), "")
    )
    harness.change(fps=10)
    assert harness.window._save_file() is True
    assert load_config(path).stream.fps == 10
    assert harness.window.windowTitle().startswith("saved.json -")


def test_save_uses_the_existing_file_without_asking(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "saved.json"
    harness.document.save(path)

    def fail(*_: object) -> None:
        raise AssertionError("shouldn't ask")

    monkeypatch.setattr(QFileDialog, "getSaveFileName", fail)
    harness.change(fps=11)
    assert harness.window._save_file() is True
    assert load_config(path).stream.fps == 11


def test_cancelling_save_as_saves_nothing(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: ("", ""))
    harness.change(fps=10)
    assert harness.window._save_file() is False
    assert harness.document.modified


def test_save_failure_is_reported(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "missing" / "c.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(path), ""))
    harness.change(fps=10)
    assert harness.window._save_file() is False
    assert "cannot save" in harness.problems[0]
    assert harness.document.modified


def test_open_loads_a_file_and_uses_it(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "other.json"
    other = Config(
        stream=StreamConfig(width=32, height=18),
        control=ControlConfig(journal_path=str(tmp_path / "j2.jsonl")),
    )
    save_config(other, path)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(path), ""))
    harness.window._open_file()
    harness.window._timer.stop()
    assert harness.window._session.config.stream.width == 32
    assert harness.window.windowTitle().startswith("other.json -")


def test_open_bad_file_explains_and_changes_nothing(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"version": 99}), encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(path), ""))
    old = harness.window._session
    harness.window._open_file()
    assert "unsupported version" in harness.problems[0]
    assert harness.window._session is old


@pytest.mark.parametrize(
    ("answer", "opened"),
    [
        (QMessageBox.StandardButton.Cancel, False),
        (QMessageBox.StandardButton.Discard, True),
    ],
)
def test_open_warns_about_unsaved_changes(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    answer: QMessageBox.StandardButton,
    opened: bool,
) -> None:
    calls: list[int] = []

    def choose(*_: object) -> tuple[str, str]:
        calls.append(1)
        return "", ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
    harness.change(fps=10)
    harness.save_answer = answer
    harness.window._open_file()
    assert harness.asked == 1
    assert bool(calls) == opened


def test_closing_with_unsaved_changes_can_be_cancelled(harness: Harness) -> None:
    from PySide6.QtGui import QCloseEvent

    harness.change(fps=10)
    harness.save_answer = QMessageBox.StandardButton.Cancel
    event = QCloseEvent()
    harness.window.closeEvent(event)
    assert not event.isAccepted()
    assert harness.window._session._source.stopped == 0


def test_closing_can_save_first(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from PySide6.QtGui import QCloseEvent

    path = tmp_path / "saved.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(path), ""))
    harness.change(fps=10)
    harness.save_answer = QMessageBox.StandardButton.Save
    event = QCloseEvent()
    harness.window.closeEvent(event)
    assert event.isAccepted()
    assert load_config(path).stream.fps == 10


def test_closing_without_changes_doesnt_ask(harness: Harness) -> None:
    from PySide6.QtGui import QCloseEvent

    event = QCloseEvent()
    harness.window.closeEvent(event)
    assert event.isAccepted()
    assert harness.asked == 0
