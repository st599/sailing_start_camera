#!/usr/bin/env python3

########################################################
#
#  Sailing Start Camera
#
#  Filename: race_templates.py
#  Description: Reusable sets of start sequences, and lists of class flags.
#  Date: 2026-11-01
#  Author: Simon st599
#  Copyright: Simon st599 MMXXVI
#  Licence: MIT
#  Dependencies: pydantic
#
#########################################################

"""Reusable sets of start sequences, and lists of class flags.

Templates are JSON files, so a club can have its own. Those in the user's folder are
added to the ones that come with the application.
"""

#
# SYSTEM IMPORTS
#

import logging
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import Field, ValidationError

#
# PROJECT IMPORTS
#
from start_camera.config import ConfigError, SequencesConfig, StrictModel

#
#  GLOBAL VARIABLES
#

logger = logging.getLogger(__name__)

BUILT_IN_DIRECTORY = Path(__file__).parent / "race_templates_data"
USER_DIRECTORY = Path.home() / ".config" / "start_camera" / "race_templates"
USER_FILE = "my_templates.json"

#
# FUNCTIONS
#


class ClassEntry(StrictModel):
    """A class and its flag."""

    name: str = Field(min_length=1)
    flag: str = Field(min_length=1)


class RaceTemplate(StrictModel):
    """A named set of start sequences."""

    name: str = Field(min_length=1)
    sequences: SequencesConfig


class TemplateFile(StrictModel):
    """A file of templates and classes, usually for one club."""

    club: str = Field(min_length=1)
    classes: list[ClassEntry] = Field(default_factory=list)
    templates: list[RaceTemplate] = Field(default_factory=list)


@dataclass
class TemplateLibrary:
    """Every template and class that's available."""

    templates: list[RaceTemplate] = field(default_factory=list)
    classes: list[ClassEntry] = field(default_factory=list)


def _read_file(path: Path) -> TemplateFile | None:
    """Return a file of templates. A bad one is skipped, so it can't stop the others."""
    try:
        return TemplateFile.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValidationError) as error:
        logger.warning("Skipping the template file %s: %s", path.name, error)
        return None


def load_library(
    built_in: Path = BUILT_IN_DIRECTORY, user: Path = USER_DIRECTORY
) -> TemplateLibrary:
    """Return all the templates and classes, with the user's after the built-in ones."""
    library = TemplateLibrary()
    for directory in (built_in, user):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            contents = _read_file(path)
            if contents is not None:
                library.templates.extend(contents.templates)
                library.classes.extend(contents.classes)
    return library


def save_user_template(
    name: str, sequences: SequencesConfig, directory: Path = USER_DIRECTORY
) -> None:
    """Save a template for reuse, replacing any of the user's with the same name.

    Raises:
        ConfigError: If it can't be saved.
    """
    path = directory / USER_FILE
    contents = None
    if path.exists():
        contents = _read_file(path)
        if contents is None:
            raise ConfigError(f"{path}: can't add to this file, as it isn't valid")
    contents = contents or TemplateFile(club="My templates")
    kept = [item for item in contents.templates if item.name != name]
    contents.templates = [*kept, RaceTemplate(name=name, sequences=sequences)]
    temporary = path.with_suffix(".tmp")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        text = contents.model_dump_json(indent=2) + "\n"
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    except OSError as error:
        raise ConfigError(f"{path}: cannot save file: {error}") from error
