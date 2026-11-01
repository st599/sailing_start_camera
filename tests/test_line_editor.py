#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_line_editor.py
#  Description: Tests for the start line editor.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, pytest, pytest-qt, PySide6
#
#########################################################

"""Tests for the start line editor."""

#
# SYSTEM IMPORTS
#

from pathlib import Path

import numpy as np
import pytest

#
# PROJECT IMPORTS
#
from start_camera.config import (
    Config,
    ControlConfig,
    LineFit,
    LinePoint,
    Secrets,
    StartLineConfig,
    StreamConfig,
    load_config,
)
from start_camera.gui.line_editor_model import MAX_ZOOM, LinePoints, ViewTransform
from start_camera.gui.settings_model import ConfigDocument
from start_camera.ingest import Frame
from start_camera.overlays.compositor import Compositor
from start_camera.overlays.start_line import StartLineOverlay

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from start_camera.gui.line_editor import LineEditorDialog  # noqa: E402
from start_camera.gui.main_window import MainWindow  # noqa: E402
from start_camera.gui.session import GuiSession  # noqa: E402

#
#  GLOBAL VARIABLES
#

WIDTH = 1280
HEIGHT = 720

#
# FUNCTIONS
#


def add_all(*points: tuple[float, float]) -> list[tuple[float, float]]:
    """Add points in turn and return the resulting order."""
    line = LinePoints()
    for point in points:
        line.add(point)
    return line.points


def test_first_two_points_are_the_ends() -> None:
    assert add_all((0.1, 0.5), (0.9, 0.5)) == [(0.1, 0.5), (0.9, 0.5)]


def test_point_between_the_ends_goes_in_the_middle() -> None:
    assert add_all((0.1, 0.5), (0.9, 0.5), (0.5, 0.45)) == [
        (0.1, 0.5),
        (0.5, 0.45),
        (0.9, 0.5),
    ]


def test_intermediate_points_can_be_clicked_in_any_order() -> None:
    expected = [(0.1, 0.5), (0.3, 0.48), (0.5, 0.47), (0.7, 0.48), (0.9, 0.5)]
    assert add_all(expected[0], expected[4], expected[3], expected[1], expected[2]) == (
        expected
    )


def test_point_beyond_the_last_end_extends_the_line() -> None:
    assert add_all((0.1, 0.5), (0.5, 0.5), (0.9, 0.5))[-1] == (0.9, 0.5)


def test_point_beyond_the_first_end_goes_first() -> None:
    assert add_all((0.5, 0.5), (0.9, 0.5), (0.1, 0.5))[0] == (0.1, 0.5)


def test_vertical_line_orders_correctly() -> None:
    assert add_all((0.5, 0.1), (0.5, 0.9), (0.52, 0.5))[1] == (0.52, 0.5)


def test_add_reports_where_the_point_went() -> None:
    line = LinePoints([(0.1, 0.5), (0.9, 0.5)])
    assert line.add((0.5, 0.5)) == 1


@pytest.mark.parametrize(
    ("point", "kept"), [((-0.2, 1.4), (0.0, 1.0)), ((0.123456789, 0.5), (0.12346, 0.5))]
)
def test_points_stay_on_the_frame_and_are_tidy(
    point: tuple[float, float], kept: tuple[float, float]
) -> None:
    line = LinePoints()
    line.add(point)
    assert line.points == [kept]
    line.move(0, point)
    assert line.points == [kept]


def test_remove_and_clear() -> None:
    line = LinePoints([(0.1, 0.5), (0.5, 0.5), (0.9, 0.5)])
    line.remove(1)
    assert line.points == [(0.1, 0.5), (0.9, 0.5)]
    line.clear()
    assert line.points == []


def view(width: int = 800, height: int = 450) -> ViewTransform:
    """Return a view of a 16:9 picture."""
    return ViewTransform(width, height, WIDTH, HEIGHT)


@pytest.mark.parametrize(
    ("size", "rect"),
    [
        ((800, 450), (0, 0, 800, 450)),
        ((800, 800), (0, 175, 800, 450)),
        ((1600, 450), (400, 0, 800, 450)),
    ],
)
def test_whole_picture_fits_and_keeps_its_shape(
    size: tuple[int, int], rect: tuple[int, int, int, int]
) -> None:
    assert view(*size).picture_rect() == pytest.approx(rect)


@pytest.mark.parametrize("size", [(800, 450), (800, 800), (1600, 450), (333, 777)])
@pytest.mark.parametrize("zoom", [1.0, 4.0])
def test_view_and_picture_positions_convert_exactly(
    size: tuple[int, int], zoom: float
) -> None:
    transform = view(*size)
    transform.zoom_at(zoom, size[0] / 2, size[1] / 2)
    point = (0.37, 0.61)
    assert transform.to_picture(*transform.to_view(point)) == pytest.approx(point)


def test_same_fraction_at_different_picture_sizes() -> None:
    small = ViewTransform(800, 450, 640, 360)
    large = ViewTransform(800, 450, 1920, 1080)
    assert small.to_picture(200, 100) == pytest.approx(large.to_picture(200, 100))


def test_zoom_keeps_the_pointed_at_place_still() -> None:
    transform = view()
    before = transform.to_picture(600, 200)
    transform.zoom_at(2.0, 600, 200)
    assert transform.zoom == 2.0
    assert transform.to_picture(600, 200) == pytest.approx(before)


def test_zoom_has_limits() -> None:
    transform = view()
    transform.zoom_at(0.1, 400, 225)
    assert transform.zoom == 1.0
    transform.zoom_at(1000, 400, 225)
    assert transform.zoom == MAX_ZOOM


def test_view_cant_leave_the_picture() -> None:
    transform = view()
    transform.zoom_at(2.0, 400, 225)
    transform.pan(10, 10)
    assert transform.centre_x == 0.75
    assert transform.centre_y == 0.75
    transform.pan(-10, -10)
    assert transform.centre_x == 0.25


def test_whole_picture_can_be_restored() -> None:
    transform = view()
    transform.zoom_at(4.0, 100, 100)
    transform.reset()
    assert transform.picture_rect() == pytest.approx((0, 0, 800, 450))


def test_only_the_picture_counts_as_on_the_picture() -> None:
    transform = view(800, 800)
    assert transform.is_on_picture(400, 400)
    assert not transform.is_on_picture(400, 100)


def live_frame(width: int = WIDTH, height: int = HEIGHT, live: bool = True) -> Frame:
    """Return a grey frame."""
    image = np.full((height, width, 3), 90, np.uint8)
    image.flags.writeable = False
    return Frame(image, 0.0, 1, live)


def make_dialog(
    qtbot: object, config: StartLineConfig | None = None, frame: Frame | None = None
) -> LineEditorDialog:
    """Return an editor sized so a view pixel is easy to work out."""
    dialog = LineEditorDialog(
        config or StartLineConfig(), lambda: frame or live_frame()
    )
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    return dialog


def at(dialog: LineEditorDialog, x: float, y: float) -> QPoint:
    """Return the canvas position of a point on the picture."""
    view_x, view_y = dialog.canvas.view.to_view((x, y))
    return QPoint(round(view_x), round(view_y))


def click(
    qtbot: object,
    dialog: LineEditorDialog,
    x: float,
    y: float,
    button: Qt.MouseButton = Qt.MouseButton.LeftButton,
) -> None:
    """Click a point on the picture."""
    qtbot.mouseClick(dialog.canvas, button, pos=at(dialog, x, y))


def test_clicking_adds_points_at_the_right_place(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    click(qtbot, dialog, 0.1, 0.6)
    click(qtbot, dialog, 0.9, 0.7)
    click(qtbot, dialog, 0.5, 0.5)
    points = dialog.points.points
    assert len(points) == 3
    assert points[0] == pytest.approx((0.1, 0.6), abs=0.003)
    assert points[1] == pytest.approx((0.5, 0.5), abs=0.003)
    assert points[2] == pytest.approx((0.9, 0.7), abs=0.003)


def test_clicking_outside_the_picture_adds_nothing(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    dialog.resize(600, 1000)
    QApplication.processEvents()
    qtbot.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=QPoint(10, 2))
    assert dialog.points.points == []


def test_dragging_moves_a_point(qtbot: object) -> None:
    config = StartLineConfig(points=[LinePoint(x=0.2, y=0.5), LinePoint(x=0.8, y=0.5)])
    dialog = make_dialog(qtbot, config)
    qtbot.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=at(dialog, 0.2, 0.5))
    qtbot.mouseMove(dialog.canvas, pos=at(dialog, 0.3, 0.6))
    target = at(dialog, 0.3, 0.6)
    qtbot.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=target)
    assert len(dialog.points.points) == 2
    assert dialog.points.points[0] == pytest.approx((0.3, 0.6), abs=0.003)


def test_clicking_a_point_selects_it_without_adding_another(qtbot: object) -> None:
    config = StartLineConfig(points=[LinePoint(x=0.2, y=0.5), LinePoint(x=0.8, y=0.5)])
    dialog = make_dialog(qtbot, config)
    click(qtbot, dialog, 0.8, 0.5)
    assert dialog.canvas.selected == 1
    assert len(dialog.points.points) == 2


def test_right_click_removes_a_point(qtbot: object) -> None:
    config = StartLineConfig(points=[LinePoint(x=0.2, y=0.5), LinePoint(x=0.8, y=0.5)])
    dialog = make_dialog(qtbot, config)
    click(qtbot, dialog, 0.2, 0.5, Qt.MouseButton.RightButton)
    assert dialog.points.points == [(0.8, 0.5)]


def test_right_click_on_nothing_does_nothing(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    click(qtbot, dialog, 0.5, 0.5, Qt.MouseButton.RightButton)
    assert dialog.points.points == []


@pytest.mark.parametrize("key", [Qt.Key.Key_Delete, Qt.Key.Key_Backspace])
def test_delete_key_removes_the_selected_point(qtbot: object, key: Qt.Key) -> None:
    config = StartLineConfig(points=[LinePoint(x=0.2, y=0.5), LinePoint(x=0.8, y=0.5)])
    dialog = make_dialog(qtbot, config)
    click(qtbot, dialog, 0.2, 0.5)
    qtbot.keyClick(dialog.canvas, key)
    assert dialog.points.points == [(0.8, 0.5)]
    assert dialog.canvas.selected is None


def test_delete_with_nothing_selected_is_safe(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    qtbot.keyClick(dialog.canvas, Qt.Key.Key_Delete)
    assert dialog.points.points == []


def wheel(dialog: LineEditorDialog, position: QPointF, delta: int) -> None:
    """Send a scroll to the canvas."""
    event = QWheelEvent(
        position,
        dialog.canvas.mapToGlobal(position),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(dialog.canvas, event)


def test_scrolling_zooms_and_clicks_stay_accurate(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    position = QPointF(at(dialog, 0.7, 0.6))
    for _ in range(6):
        wheel(dialog, position, 120)
    assert dialog.canvas.view.zoom > 3
    click(qtbot, dialog, 0.7, 0.6)
    assert dialog.points.points[0] == pytest.approx((0.7, 0.6), abs=0.001)


def test_zoom_buttons_and_whole_picture(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    dialog.canvas.zoom_by(2)
    assert dialog.canvas.view.zoom == 2
    dialog.canvas.reset_view()
    assert dialog.canvas.view.zoom == 1


def test_middle_drag_moves_the_view_without_adding_points(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    dialog.canvas.zoom_by(4)
    before = dialog.canvas.view.centre_x
    qtbot.mousePress(dialog.canvas, Qt.MouseButton.MiddleButton, pos=QPoint(300, 200))
    qtbot.mouseMove(dialog.canvas, pos=QPoint(200, 200))
    qtbot.mouseRelease(dialog.canvas, Qt.MouseButton.MiddleButton, pos=QPoint(200, 200))
    assert dialog.canvas.view.centre_x > before
    assert dialog.points.points == []


def test_picture_stays_frozen_until_asked(qtbot: object) -> None:
    frames = [live_frame(), live_frame()]
    frames[1] = Frame(np.full((HEIGHT, WIDTH, 3), 200, np.uint8), 0.0, 2, True)
    supply = iter(frames)
    dialog = LineEditorDialog(StartLineConfig(), lambda: next(supply))
    qtbot.addWidget(dialog)
    assert dialog.canvas._frame[0, 0, 0] == 90
    dialog.refresh_preview()
    assert dialog.canvas._frame[0, 0, 0] == 90
    dialog.new_picture()
    assert dialog.canvas._frame[0, 0, 0] == 200


def test_new_picture_keeps_the_points_and_zoom(qtbot: object) -> None:
    config = StartLineConfig(points=[LinePoint(x=0.2, y=0.5), LinePoint(x=0.8, y=0.5)])
    dialog = make_dialog(qtbot, config)
    dialog.canvas.zoom_by(2)
    dialog.new_picture()
    assert dialog.canvas.view.zoom == 2
    assert len(dialog.points.points) == 2


def test_no_signal_is_explained(qtbot: object) -> None:
    dialog = make_dialog(qtbot, frame=live_frame(live=False))
    assert "No picture" in dialog.signal_label.text()


@pytest.mark.parametrize("fit", list(LineFit))
@pytest.mark.parametrize("size", [(640, 360), (1280, 720), (1920, 1080)])
def test_preview_is_exactly_what_the_stream_draws(
    qtbot: object, fit: LineFit, size: tuple[int, int]
) -> None:
    frame = live_frame(*size)
    dialog = make_dialog(qtbot, frame=frame)
    for point in ((0.1, 0.6), (0.9, 0.7), (0.5, 0.5), (0.3, 0.52)):
        dialog.points.add(point)
    dialog.fit_box.setCurrentText(fit.value)
    dialog.refresh_preview()
    dialog.accept_line()
    stream = Compositor([StartLineOverlay(dialog.result_config)]).render(frame)
    assert np.array_equal(dialog.canvas._frame, stream)


def test_preview_follows_each_change(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    click(qtbot, dialog, 0.1, 0.5)
    click(qtbot, dialog, 0.9, 0.5)
    first = dialog.canvas._frame.copy()
    assert not np.array_equal(first, live_frame().image)
    dialog.width_box.setValue(10)
    assert not np.array_equal(dialog.canvas._frame, first)


def test_changing_the_fit_changes_the_preview(qtbot: object) -> None:
    config = StartLineConfig(
        points=[
            LinePoint(x=0.1, y=0.5),
            LinePoint(x=0.5, y=0.3),
            LinePoint(x=0.9, y=0.5),
        ]
    )
    dialog = make_dialog(qtbot, config)
    straight = dialog.canvas._frame.copy()
    dialog.fit_box.setCurrentText("spline")
    assert not np.array_equal(dialog.canvas._frame, straight)


def test_new_line_goes_through_the_points_clicked(qtbot: object) -> None:
    assert make_dialog(qtbot).fit_box.currentText() == "spline"


def test_existing_line_keeps_its_fit(qtbot: object) -> None:
    config = StartLineConfig(
        fit=LineFit.STRAIGHT, points=[LinePoint(x=0.1, y=0.5), LinePoint(x=0.9, y=0.5)]
    )
    assert make_dialog(qtbot, config).fit_box.currentText() == "straight"


def test_degree_only_applies_to_polynomials(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    assert not dialog.degree_box.isEnabled()
    dialog.fit_box.setCurrentText("polynomial")
    assert dialog.degree_box.isEnabled()


def test_too_few_points_are_explained(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    click(qtbot, dialog, 0.5, 0.5)
    dialog.accept_line()
    assert dialog.result_config is None
    assert "at least 2 different points" in dialog.error_label.text()


def test_bad_colour_is_explained_and_keeps_working(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    dialog.colour_edit.setText("red")
    assert "#ff0000" in dialog.error_label.text()
    dialog.colour_edit.setText("#00ff00")
    assert dialog.error_label.text() == ""


def test_result_holds_the_settings(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    dialog.points.add((0.1, 0.5))
    dialog.points.add((0.9, 0.5))
    dialog.colour_edit.setText("#00ff00")
    dialog.width_box.setValue(4)
    dialog.opacity_box.setValue(0.5)
    dialog.accept_line()
    result = dialog.result_config
    assert result.enabled is True
    assert result.colour == "#00ff00"
    assert result.width == 4
    assert result.opacity == 0.5
    assert [(point.x, point.y) for point in result.points] == [(0.1, 0.5), (0.9, 0.5)]


def test_line_can_be_hidden_with_no_points(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    dialog.enabled_box.setChecked(False)
    dialog.accept_line()
    assert dialog.result_config.enabled is False


def test_clear_removes_the_points(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    click(qtbot, dialog, 0.5, 0.5)
    dialog._clear()
    assert dialog.points.points == []
    assert dialog.canvas.selected is None


@pytest.mark.parametrize("size", [(800, 500), (1100, 700), (1600, 1000)])
def test_works_at_different_window_sizes(
    qtbot: object, size: tuple[int, int]
) -> None:
    dialog = make_dialog(qtbot)
    dialog.resize(*size)
    QApplication.processEvents()
    click(qtbot, dialog, 0.25, 0.75)
    assert dialog.points.points[0] == pytest.approx((0.25, 0.75), abs=0.005)


def test_editor_fits_a_small_screen(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    needed = dialog.minimumSizeHint()
    assert needed.width() <= 1024
    assert needed.height() <= 600


def test_picture_gets_most_of_the_window(qtbot: object) -> None:
    dialog = make_dialog(qtbot)
    dialog.resize(1100, 700)
    QApplication.processEvents()
    assert dialog.canvas.width() > 750


class FakeSource:
    """Stand in for the camera."""

    def __init__(self, config: Config, secrets: Secrets) -> None:
        """Make a frame."""
        stream = config.stream
        self.frame = live_frame(stream.width, stream.height)

    def start(self) -> None:
        """Do nothing."""

    def stop(self) -> None:
        """Do nothing."""

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


def make_window(qtbot: object, tmp_path: Path) -> MainWindow:
    """Return a started window."""

    def make_session(config: Config) -> GuiSession:
        return GuiSession(
            config, Secrets(), make_source=FakeSource, make_streaming=FakeStreaming
        )

    config = Config(
        stream=StreamConfig(width=640, height=360),
        control=ControlConfig(journal_path=str(tmp_path / "j.jsonl")),
    )
    window = MainWindow(
        make_session(config), ConfigDocument(config), make_session=make_session
    )
    qtbot.addWidget(window)
    window.start()
    window._timer.stop()
    return window


def test_saved_line_appears_in_the_window_and_the_file(
    qtbot: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window(qtbot, tmp_path)
    line = StartLineConfig(
        enabled=True, points=[LinePoint(x=0.1, y=0.5), LinePoint(x=0.9, y=0.5)]
    )

    class Answer:
        result_config = line

        def exec(self) -> int:
            return 1

    monkeypatch.setattr(MainWindow, "_line_editor", lambda self: Answer())
    before = window.video._frame.copy()
    window._open_line_editor()
    window._timer.stop()
    assert window._session.config.start_line == line
    assert not np.array_equal(window.video._frame, before)
    assert window._document.modified
    path = tmp_path / "c.json"
    window._document.save(path)
    assert load_config(path).start_line == line


def test_cancelling_changes_nothing(
    qtbot: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window(qtbot, tmp_path)

    class Answer:
        def exec(self) -> int:
            return 0

    monkeypatch.setattr(MainWindow, "_line_editor", lambda self: Answer())
    window._open_line_editor()
    assert not window._document.modified


def test_line_cant_change_while_streaming(
    qtbot: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window(qtbot, tmp_path)
    problems: list[str] = []
    window._show_problem = problems.append
    window._session.start_stream()
    window._open_line_editor()
    assert "Stop the stream" in problems[0]
    window._session.stop_stream()


def test_real_editor_opens_from_the_window(qtbot: object, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path)
    dialog = window._line_editor()
    qtbot.addWidget(dialog)
    assert dialog.canvas._frame.shape == (360, 640, 3)
