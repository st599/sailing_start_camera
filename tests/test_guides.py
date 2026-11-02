#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: test_guides.py
#  Description: Tests that the guides match the application.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: pytest
#
#########################################################

"""Tests that the guides match the application, so that they can't drift apart."""

#
# SYSTEM IMPORTS
#

import json
import re
from pathlib import Path

import pytest

#
# PROJECT IMPORTS
#
from start_camera.config import Secrets, load_config

#
#  GLOBAL VARIABLES
#

ROOT = Path(__file__).parent.parent
DOCS = ROOT / "docs"
RACE_DAY = DOCS / "race_day_guide.md"
SETUP = DOCS / "setup_guide.md"
SOURCE = "\n".join(
    path.read_text(encoding="utf-8")
    for path in (ROOT / "src").rglob("*")
    if path.suffix in (".py", ".json")
)
# Messages from other programs, which can't be found in this project's source.
OUTSIDE_MESSAGES = {
    "Address already in use",
    "could not connect to display",
    "CONNECTION LOST",
}
BUTTONS = {
    "Start now",
    "Postpone",
    "Resume",
    "Individual recall",
    "Clear X",
    "General recall",
    "Next start",
    "Abandon",
    "Start streaming",
    "Stop streaming",
}
STATES = {"POSTPONED", "ABANDONED", "READY", "CAMERA SIGNAL LOST"}
ALL_MARKDOWN = sorted(DOCS.glob("*.md")) + [ROOT / "README.md"]

#
# FUNCTIONS
#


def _heading_anchors(path: Path) -> set[str]:
    """Return the link targets of a Markdown file's headings."""
    anchors = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            text = line.lstrip("#").strip().lower()
            text = re.sub(r"[^\w\- ]", "", text).replace(" ", "-")
            anchors.add(text)
    return anchors


@pytest.mark.parametrize("path", ALL_MARKDOWN, ids=lambda item: item.name)
def test_links_between_guides_lead_somewhere(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for target in re.findall(r"\]\((?!https?:)([^)]+)\)", text):
        name, _, anchor = target.partition("#")
        linked = (path.parent / name).resolve() if name else path
        assert linked.exists(), target
        if anchor and linked.suffix == ".md":
            assert anchor in _heading_anchors(linked), target


def test_readme_leads_with_the_guides() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/race_day_guide.md" in text
    assert "docs/setup_guide.md" in text


def test_race_day_guide_says_the_overlay_is_only_an_aid() -> None:
    text = RACE_DAY.read_text(encoding="utf-8")
    assert "only an aid" in text
    assert "official signals" in text


@pytest.mark.parametrize("button", sorted(BUTTONS))
def test_buttons_in_the_guide_are_real(button: str) -> None:
    assert button in SOURCE


@pytest.mark.parametrize("button", sorted(BUTTONS))
def test_race_day_guide_covers_every_button(button: str) -> None:
    assert button in RACE_DAY.read_text(encoding="utf-8")


@pytest.mark.parametrize("state", sorted(STATES))
def test_what_the_guide_says_youll_see_is_real(state: str) -> None:
    assert state in SOURCE
    assert state in RACE_DAY.read_text(encoding="utf-8")


def _table_messages(path: Path) -> list[str]:
    """Return the messages quoted in the first column of a guide's tables."""
    messages = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("| "):
            messages += re.findall(r"`([^`]+)`", line.split("|")[1])
    return messages


def test_setup_guide_messages_are_real() -> None:
    messages = _table_messages(SETUP)
    assert len(messages) >= 8
    for message in messages:
        if message in OUTSIDE_MESSAGES or message in ("libxcb-cursor0", "xcb"):
            continue
        assert message in SOURCE, message


def test_race_day_messages_are_real() -> None:
    text = RACE_DAY.read_text(encoding="utf-8")
    for message in ("CONNECTION LOST", "Too many attempts", "No class has started"):
        assert message in text
        assert message in SOURCE


def _code_blocks(path: Path, language: str) -> list[str]:
    """Return a guide's code blocks in a language."""
    pattern = rf"```{language}\n(.*?)```"
    return re.findall(pattern, path.read_text(encoding="utf-8"), re.DOTALL)


def test_setup_guide_secrets_are_valid() -> None:
    blocks = _code_blocks(SETUP, "json")
    Secrets.model_validate_json(blocks[0])
    assert set(json.loads(blocks[0])) == {
        "camera_username",
        "camera_password",
        "control_password",
    }


def test_setup_guides_starting_configuration_is_valid(tmp_path: Path) -> None:
    command = re.search(r"echo '(.*?)' > config\.json", SETUP.read_text("utf-8"))
    path = tmp_path / "config.json"
    path.write_text(command.group(1), encoding="utf-8")
    assert load_config(path).version == 1


def test_setup_guide_template_exists() -> None:
    assert "Rule 26, one class" in SETUP.read_text(encoding="utf-8")
    assert "Rule 26, one class" in SOURCE


def test_setup_guide_commands_are_installed() -> None:
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    text = SETUP.read_text(encoding="utf-8")
    for command in set(re.findall(r"venv/bin/(startcam[\w-]*)", text)):
        assert f"{command} =" in project, command


def test_pi_steps_say_they_are_untested() -> None:
    assert "hasn't been tried on a real Pi" in SETUP.read_text(encoding="utf-8")
