#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_flags_overlay.py
#  Description: Tests for the flag artwork and overlay.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: numpy, opencv, pytest
#
#########################################################

"""Tests for the flag artwork and overlay."""

#
# SYSTEM IMPORTS
#

import string
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import pytest

#
# PROJECT IMPORTS
#
from start_camera.config import (
    Config,
    Corner,
    CountdownOverlayConfig,
    FlagAction,
    FlagEvent,
    FlagsOverlayConfig,
    OverlaysConfig,
    PreparatoryFlag,
    SequenceConfig,
    SequencesConfig,
)
from start_camera.ingest import Frame
from start_camera.overlays.compositor import Compositor, OverlayContext
from start_camera.overlays.countdown import CountdownOverlay
from start_camera.overlays.factory import build_compositor
from start_camera.overlays.flag_artwork import (
    BLUE,
    FLAGS,
    PENNANTS,
    RED,
    WHITE,
    YELLOW,
    draw_named_panel,
    draw_standard_flag,
    load_flag,
)
from start_camera.overlays.flags import FlagsOverlay
from start_camera.race_clock import RaceClock

#
#  GLOBAL VARIABLES
#

T0 = datetime(2026, 11, 1, 12, 0).timestamp()
MINUTE = 60.0
WIDTH = 1280
HEIGHT = 720
TAMESIS_FLAGS = ["O", "8", "R", "E", "F", "G", "T", "1", "A"]
EARLIER_FLAGS = ["H", "L", "N", "M", "T", "1", "2", "3", "4", "5"]

#
# FUNCTIONS
#


def context(now: float = T0) -> OverlayContext:
    """Return a context at a given time."""
    return OverlayContext(now, WIDTH, HEIGHT, True)


def race(*sequences: SequenceConfig, **options: object) -> RaceClock:
    """Return a race clock with its first warning at T0."""
    config = SequencesConfig(items=list(sequences), **options)
    clock = RaceClock(config, T0)
    if sequences:
        clock.start_now(T0)
    return clock


def merlin() -> SequenceConfig:
    """Return a class with a standard flag."""
    return SequenceConfig(name="Merlin Rocket", class_flag="O")


def overlay(
    clock: RaceClock,
    sequences: list[SequenceConfig] | None = None,
    countdown: CountdownOverlayConfig | None = None,
    tmp_path: Path | None = None,
    **options: object,
) -> FlagsOverlay:
    """Return an overlay."""
    if tmp_path is not None:
        options["custom_directory"] = str(tmp_path)
    return FlagsOverlay(
        FlagsOverlayConfig(**options),
        clock,
        sequences if sequences is not None else [merlin()],
        countdown,
    )


@pytest.mark.parametrize(
    "name", list(string.ascii_uppercase) + list(string.digits) + ["AP", "black"]
)
def test_standard_flags_exist(name: str) -> None:
    image = draw_standard_flag(name, 40)
    assert image is not None
    assert image.shape[0] == 40
    assert image.shape[2] == 4
    assert image.dtype == np.uint8


def test_first_substitute_exists() -> None:
    assert draw_standard_flag("First Substitute", 40) is not None


@pytest.mark.parametrize("name", TAMESIS_FLAGS + EARLIER_FLAGS)
def test_the_clubs_flags_are_all_built_in(name: str) -> None:
    assert name in FLAGS or name in PENNANTS


def test_names_ignore_capitals() -> None:
    assert np.array_equal(draw_standard_flag("o", 40), draw_standard_flag("O", 40))


def test_unknown_flag_has_no_standard_drawing() -> None:
    assert draw_standard_flag("Solo", 40) is None


def test_flags_look_different_from_each_other() -> None:
    images = [
        draw_standard_flag(name, 30).tobytes() for name in [*FLAGS, *PENNANTS]
    ]
    repeated = {
        name
        for name in ("T", "3", "K", "5")
        if images.count(draw_standard_flag(name, 30).tobytes()) > 1
    }
    assert len(set(images)) == len(images)
    assert not repeated


@pytest.mark.parametrize(
    ("name", "x", "y", "colour"),
    [
        ("E", 0.5, 0.1, BLUE),
        ("E", 0.5, 0.9, RED),
        ("Q", 0.5, 0.5, YELLOW),
        ("R", 0.1, 0.1, RED),
        ("R", 0.5, 0.5, YELLOW),
        ("X", 0.1, 0.1, WHITE),
        ("X", 0.5, 0.5, BLUE),
        ("T", 0.1, 0.5, RED),
        ("T", 0.5, 0.5, WHITE),
        ("T", 0.9, 0.5, BLUE),
        ("F", 0.5, 0.5, RED),
        ("F", 0.05, 0.05, WHITE),
        ("H", 0.1, 0.5, WHITE),
        ("H", 0.9, 0.5, RED),
        ("O", 0.9, 0.1, RED),
        ("O", 0.1, 0.9, YELLOW),
        ("P", 0.5, 0.5, WHITE),
        ("P", 0.1, 0.1, BLUE),
        ("G", 0.08, 0.5, YELLOW),
        ("G", 0.25, 0.5, BLUE),
        ("1", 0.5, 0.5, RED),
        ("1", 0.15, 0.5, WHITE),
        ("8", 0.5, 0.5, RED),
    ],
)
def test_flag_colours(name: str, x: float, y: float, colour: tuple[int, ...]) -> None:
    image = draw_standard_flag(name, 80)
    height, width = image.shape[:2]
    assert image[int(y * height), int(x * width), :3].tolist() == list(colour)


def test_flag_a_has_a_swallowtail() -> None:
    image = draw_standard_flag("A", 80)
    assert image[40, -1, 3] == 0
    assert image[2, -2, 3] > 0
    assert image[40, 5, 3] == 255


def test_pennants_taper() -> None:
    image = draw_standard_flag("8", 80)
    assert image[2, -2, 3] == 0
    assert image[40, -2, 3] == 255
    assert image[40, 2, 3] == 255


def test_named_panel_is_solid_and_wide_enough() -> None:
    image = draw_named_panel("A Very Long Flag Name", 40)
    assert image.shape[0] == 40
    assert image[:, :, 3].min() == 255
    assert image.shape[1] > 200


def test_missing_folder_gives_standard_flag(tmp_path: Path) -> None:
    image = load_flag("O", 40, tmp_path / "missing")
    assert np.array_equal(image, draw_standard_flag("O", 40))


def write_image(
    path: Path, colour: tuple[int, int, int], size: tuple[int, int]
) -> None:
    """Write a plain image file."""
    cv2.imwrite(str(path), np.full((size[1], size[0], 3), colour, dtype=np.uint8))


def test_custom_image_is_loaded_at_the_right_height(tmp_path: Path) -> None:
    write_image(tmp_path / "Solo.png", (10, 20, 30), (100, 200))
    image = load_flag("Solo", 40, tmp_path)
    assert image.shape == (40, 20, 4)
    assert image[20, 10].tolist() == [10, 20, 30, 255]


def test_custom_name_ignores_capitals(tmp_path: Path) -> None:
    write_image(tmp_path / "SOLO.PNG", (10, 20, 30), (40, 40))
    assert load_flag("solo", 40, tmp_path)[0, 0, 0] == 10


def test_custom_image_replaces_a_standard_flag(tmp_path: Path) -> None:
    write_image(tmp_path / "O.png", (1, 2, 3), (60, 40))
    assert load_flag("O", 40, tmp_path)[0, 0].tolist() == [1, 2, 3, 255]


def test_transparency_is_kept(tmp_path: Path) -> None:
    image = np.zeros((40, 40, 4), dtype=np.uint8)
    cv2.imwrite(str(tmp_path / "Clear.png"), image)
    assert load_flag("Clear", 40, tmp_path)[0, 0, 3] == 0


def test_greyscale_image_works(tmp_path: Path) -> None:
    cv2.imwrite(str(tmp_path / "Grey.png"), np.full((40, 40), 77, dtype=np.uint8))
    assert load_flag("Grey", 40, tmp_path)[0, 0].tolist() == [77, 77, 77, 255]


def test_unreadable_image_falls_back_to_the_name(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "Solo.png").write_text("not an image", encoding="utf-8")
    image = load_flag("Solo", 40, tmp_path)
    assert image[:, :, 3].min() == 255
    assert "Can't read the flag image" in caplog.text


def test_unknown_flag_shows_its_name_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    image = load_flag("Mystery", 40, tmp_path)
    assert np.array_equal(image, draw_named_panel("Mystery", 40))
    assert "No artwork for flag Mystery" in caplog.text


def test_flag_name_cant_reach_outside_the_folder(tmp_path: Path) -> None:
    folder = tmp_path / "flags"
    folder.mkdir()
    write_image(tmp_path / "secret.png", (1, 2, 3), (40, 40))
    image = load_flag("../secret", 40, folder)
    assert np.array_equal(image, draw_named_panel("../secret", 40))


def test_non_image_files_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "O.txt").write_text("notes", encoding="utf-8")
    assert np.array_equal(load_flag("O", 40, tmp_path), draw_standard_flag("O", 40))


@pytest.mark.parametrize(
    ("minutes", "flags"),
    [
        (-1, ()),
        (0, ("O",)),
        (1, ("O", "P")),
        (4, ("O",)),
        (5, ()),
    ],
)
def test_flags_change_at_the_correct_times(
    minutes: float, flags: tuple[str, ...]
) -> None:
    assert overlay(race(merlin())).state(context(T0 + minutes * MINUTE)) == flags


def test_combined_signal_shows_as_separate_flags() -> None:
    sequence = SequenceConfig(
        name="A", class_flag="A", preparatory_flag=PreparatoryFlag.Z_AND_I
    )
    state = overlay(race(sequence)).state(context(T0 + MINUTE))
    assert state == ("A", "Z", "I")


def test_thursday_n18_signals_can_be_configured() -> None:
    events = [
        FlagEvent(minutes_before_start=1, flag="class", action=FlagAction.UP),
        FlagEvent(minutes_before_start=0, flag="class", action=FlagAction.DOWN),
    ]
    clock = race(SequenceConfig(name="N18", class_flag="R", events=events))
    instance = overlay(clock)
    assert instance.state(context(T0)) == ("R",)
    assert instance.state(context(T0 + 59)) == ("R",)
    assert instance.state(context(T0 + MINUTE)) == ()


def test_y_flies_all_day_alongside_other_flags() -> None:
    clock = race(merlin(), day_flags=["Y"])
    instance = overlay(clock)
    assert instance.state(context(T0 - 3600)) == ("Y",)
    assert instance.state(context(T0)) == ("O", "Y")
    assert instance.state(context(T0 + 5 * MINUTE)) == ("Y",)
    clock.abandon(T0 + 6 * MINUTE)
    assert instance.state(context(T0 + 6 * MINUTE)) == ("N", "Y")


def test_day_flags_are_off_by_default() -> None:
    assert SequencesConfig().day_flags == []


def test_postponement_shows_ap_only() -> None:
    clock = race(merlin())
    clock.postpone(T0 + MINUTE)
    assert overlay(clock).state(context(T0 + MINUTE)) == ("AP",)


def test_nothing_flying_draws_nothing() -> None:
    assert overlay(race(merlin())).draw(context(T0 - 3600)) is None


def test_stack_gets_taller_with_more_flags() -> None:
    instance = overlay(race(merlin()))
    one = instance.draw(context(T0))
    two = instance.draw(context(T0 + MINUTE))
    assert two.image.shape[0] > one.image.shape[0] * 1.6


def test_flags_do_not_overlap_vertically() -> None:
    clock = race(merlin(), day_flags=["Y"])
    layer = overlay(clock, show_labels=False).draw(context(T0 + MINUTE))
    image = layer.image
    opaque_rows = (image[:, :, 3] == 255).any(axis=1)
    edges = np.flatnonzero(np.diff(opaque_rows.astype(int)))
    # Each of the three flags has a start and an end.
    assert len(edges) == 6


def test_labels_can_be_turned_off() -> None:
    clock = race(merlin())
    with_labels = overlay(clock).draw(context(T0))
    without = overlay(clock, show_labels=False).draw(context(T0))
    assert with_labels.image.shape[1] > without.image.shape[1] * 2


def test_labels_name_the_class_and_signal() -> None:
    sequences = [merlin(), SequenceConfig(name="Solo", class_flag="Solo")]
    labels = overlay(race(), sequences)._labels
    assert labels["O"] == "O Merlin Rocket"
    assert labels["Solo"] == "Solo"
    assert labels["AP"] == "AP Postponed"
    assert labels["Y"] == "Y Lifejackets"
    assert labels["P"] == "P Preparatory"


def test_labels_cover_combined_and_black_preparatory_flags() -> None:
    def labels(flag: PreparatoryFlag) -> dict[str, str]:
        sequence = SequenceConfig(name="A", class_flag="A", preparatory_flag=flag)
        return overlay(race(), [sequence])._labels

    combined = labels(PreparatoryFlag.Z_AND_I)
    assert combined["Z"] == "Z Preparatory"
    assert combined["I"] == "I Preparatory"
    assert labels(PreparatoryFlag.BLACK)["black"] == "Black flag"


def test_a_class_flag_label_wins_over_a_preparatory_label() -> None:
    sequences = [
        SequenceConfig(name="A", class_flag="A"),
        SequenceConfig(name="Pico", class_flag="P"),
    ]
    assert overlay(race(), sequences)._labels["P"] == "P Pico"


def test_stack_sits_below_the_countdown_in_the_same_corner() -> None:
    clock = race(merlin())
    countdown_config = CountdownOverlayConfig()
    countdown = CountdownOverlay(countdown_config, clock).draw(context(T0))
    flags = overlay(clock, countdown=countdown_config).draw(context(T0))
    assert flags.y >= countdown.y + countdown.image.shape[0]
    assert flags.x + flags.image.shape[1] == countdown.x + countdown.image.shape[1]


def test_stack_clears_a_larger_countdown() -> None:
    clock = race(merlin())
    countdown_config = CountdownOverlayConfig(scale=3)
    countdown = CountdownOverlay(countdown_config, clock).draw(context(T0))
    flags = overlay(clock, countdown=countdown_config).draw(context(T0))
    assert flags.y >= countdown.y + countdown.image.shape[0]


def test_stack_ignores_a_countdown_elsewhere() -> None:
    clock = race(merlin())
    config = CountdownOverlayConfig(position=Corner.BOTTOM_LEFT)
    assert overlay(clock, countdown=config).draw(context(T0)).y == 16


def test_stack_at_the_bottom_stays_on_the_frame() -> None:
    clock = race(merlin())
    layer = overlay(
        clock, countdown=CountdownOverlayConfig(position=Corner.BOTTOM_RIGHT),
        position=Corner.BOTTOM_RIGHT,
    ).draw(context(T0))
    assert layer.y + layer.image.shape[0] <= HEIGHT


def test_size_is_configurable_and_follows_the_resolution() -> None:
    clock = race(merlin())
    small = overlay(clock).draw(context(T0)).image.shape[0]
    large = overlay(clock, scale=2).draw(context(T0)).image.shape[0]
    assert large > small * 1.8
    low = overlay(clock).draw(OverlayContext(T0, 640, 360, True)).image.shape[0]
    assert low == pytest.approx(small / 2, abs=3)


def test_flag_is_visible_on_a_dark_panel_on_any_background() -> None:
    clock = race(SequenceConfig(name="Topper", class_flag="T"))
    instance = overlay(clock, show_labels=False)
    frame = Frame(np.full((HEIGHT, WIDTH, 3), 255, dtype=np.uint8), T0, 1, True)
    image = Compositor([instance], clock=lambda: T0).render(frame)
    layer = instance.draw(context(T0))
    gap = 6
    middle = layer.y + layer.image.shape[0] // 2
    pixel = image[middle, layer.x + gap + 5]
    assert pixel.tolist() == list(RED)


def test_redraws_only_when_the_flags_change() -> None:
    now = [T0]
    draws = []

    class Counting(FlagsOverlay):
        def draw(self, context: OverlayContext) -> object:
            draws.append(1)
            return super().draw(context)

    instance = Counting(FlagsOverlayConfig(), race(merlin()), [merlin()])
    compositor = Compositor([instance], clock=lambda: now[0])
    frame = Frame(np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8), T0, 1, True)
    for step in range(20):
        now[0] = T0 + step
        compositor.render(frame)
    assert len(draws) == 1
    now[0] = T0 + MINUTE
    compositor.render(frame)
    assert len(draws) == 2


def test_each_image_is_loaded_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loads = []

    def counting(name: str, height: int, directory: Path) -> np.ndarray:
        loads.append(name)
        return np.zeros((height, height, 4), dtype=np.uint8)

    monkeypatch.setattr("start_camera.overlays.flags.load_flag", counting)
    instance = overlay(race(merlin()))
    for _ in range(3):
        instance.draw(context(T0))
    assert loads == ["O"]


def test_custom_folder_is_used(tmp_path: Path) -> None:
    write_image(tmp_path / "Solo.png", (1, 2, 3), (40, 40))
    sequence = SequenceConfig(name="Solo", class_flag="Solo")
    instance = overlay(race(sequence), [sequence], tmp_path=tmp_path, show_labels=False)
    image = instance.draw(context(T0)).image
    assert image[image.shape[0] // 2, 10, :3].tolist() == [1, 2, 3]


def kinds(config: Config) -> list[type]:
    """Return the types of overlay a configuration gives."""
    compositor = build_compositor(config, clock=lambda: T0)
    return [type(item.overlay) for item in compositor._overlays]


def test_factory_adds_flags_above_the_countdown() -> None:
    config = Config(sequences=SequencesConfig(items=[merlin()]))
    assert kinds(config)[-2:] == [CountdownOverlay, FlagsOverlay]


def test_factory_adds_flags_for_day_flags_alone() -> None:
    config = Config(sequences=SequencesConfig(day_flags=["Y"]))
    assert FlagsOverlay in kinds(config)
    assert CountdownOverlay not in kinds(config)


def test_factory_leaves_flags_out_with_nothing_to_show() -> None:
    assert FlagsOverlay not in kinds(Config())


def test_factory_leaves_flags_out_when_disabled() -> None:
    config = Config(
        sequences=SequencesConfig(items=[merlin()]),
        overlays=OverlaysConfig(flags=FlagsOverlayConfig(enabled=False)),
    )
    assert FlagsOverlay not in kinds(config)


def test_flags_share_the_race_clock_with_the_countdown() -> None:
    config = Config(sequences=SequencesConfig(items=[merlin()]))
    overlays = build_compositor(config, clock=lambda: T0)._overlays
    assert overlays[-1].overlay._race_clock is overlays[-2].overlay._race_clock
