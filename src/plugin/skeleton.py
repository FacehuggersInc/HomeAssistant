"""
The starting point for a new plugin.

Deliberately almost nothing: a `main.py` that loads, a `plugin.toml` that
describes it, and a `settings.json` only if it was asked for. Everything else
a plugin can have - pages, widgets, skills, an API - is a decision with an
intention behind it, and a skeleton that guesses at those is a folder somebody
has to delete their way out of before they can start.

What comes out is the **contents** of the folder rather than the folder, so
unpacking it inside `plugins/MyPlugin/` puts the files where they belong. That
is the same shape the upload page accepts, so a skeleton can be edited and
sent straight back without being rearranged.
"""

from __future__ import annotations

import io
import json
import re
import zipfile

from src.plugin.install import NAME_OK, safe_folder_name

#A plugin key is what everything else refers to it by - settings paths,
#dependencies, the public registry. Lowercase and plain, because it appears in
#places where case is not preserved and punctuation is not welcome.
KEY_OK = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class BadSkeleton(Exception):
    """The details given would not make a plugin that loads."""


def suggest_key(name: str) -> str:
    """A usable key from a folder name, for a field somebody has not filled."""
    key = re.sub(r"[^a-z0-9_]", "", str(name or "").lower())
    return key if KEY_OK.match(key) else ""


def _class_name(name: str) -> str:
    """A CamelCase class name from a folder name."""
    parts = re.split(r"[^A-Za-z0-9]+", str(name or ""))
    joined = "".join(p[:1].upper() + p[1:] for p in parts if p)
    return joined if joined and joined[0].isalpha() else "MyPlugin"


MAIN_PY = '''"""
{name}.
"""

from __future__ import annotations

from src.plugin.template import Plugin


class {klass}(Plugin):

    KEY = "{key}"

    def load(self, carryover=None):
        """
        Called when the plugin is loaded, and again after every reload.

        Anything registered here - a page, a widget, a skill, an event
        subscription - must be undone in `unload`, or a reload leaves the old
        one behind beside the new.
        """
        self.client.log("info", "[{klass}] Loaded.")

    def unload(self, carryover=None):
        """Give back everything `load` took."""
        self.client.log("info", "[{klass}] Unloaded.")
'''

TOML = '''[plugin]
name = "{name}"
key = "{key}"
# Lower loads earlier. Anything another plugin reads from should be below it.
order = 100
version = "{version}"
description = "{description}"
icon = "puzzle"
{settings_block}
# Files this plugin ships a starting version of and the person then edits.
# They are written when absent and never overwritten by an update.
#
#   [update]
#   install_once = ["config.json"]
'''

SETTINGS_BLOCK = '''
[settings]
path = "plugins/{folder}/{settings}"
'''

SETTINGS_JSON = {
    "{key}": {
        "example": {
            "type": "str",
            "default": "",
            "value": "",
            "label": "An example setting",
            "description": "Delete this once there is a real one.",
        }
    }
}


VERSION_OK = re.compile(r"^[0-9][0-9A-Za-z.\-+]{0,31}$")


def build(name: str, key: str = "", description: str = "",
          settings_file: str = "", version: str = "0.1.0") -> tuple:
    """
    (folder name, {relpath: bytes}) for a new plugin.

    `settings_file` is the name of the settings file to create, or "" for
    none. It is written into `plugin.toml` as well: a settings.json with
    nothing pointing at it is never read, and the plugin then has no settings
    at all with every option silently falling back to its default - which
    looks exactly like the settings not working.
    """
    folder = safe_folder_name(name)
    if not folder:
        raise BadSkeleton(
            f"{name!r} will not work as a folder name. Use letters, numbers, "
            f"dashes and underscores, starting with a letter.")

    key = str(key or "").strip().lower() or suggest_key(folder)
    if not KEY_OK.match(key):
        raise BadSkeleton(
            f"{key!r} will not work as a plugin key. Use lowercase letters, "
            f"numbers and underscores, starting with a letter.")

    version = str(version or "").strip() or "0.1.0"
    if not VERSION_OK.match(version):
        raise BadSkeleton(
            f"{version!r} will not work as a version. Start with a digit and "
            f"use numbers, letters, dots and dashes - 0.1.0, 2024.3, 1.0-beta.")

    settings = str(settings_file or "").strip()
    if settings:
        if "/" in settings or "\\" in settings or settings.startswith("."):
            raise BadSkeleton("The settings file must be a plain filename in "
                              "the plugin's own folder.")
        if not settings.lower().endswith(".json"):
            settings += ".json"

    files: dict = {}
    files["main.py"] = MAIN_PY.format(
        name=name, klass=_class_name(folder), key=key).encode("utf-8")
    files["plugin.toml"] = TOML.format(
        name=name, key=key, version=version,
        description=str(description or "").replace('"', "'"),
        settings_block=(SETTINGS_BLOCK.format(folder=folder, settings=settings)
                        if settings else ""),
    ).encode("utf-8")

    if settings:
        body = json.dumps(SETTINGS_JSON, indent=4).replace("{key}", key)
        files[settings] = body.encode("utf-8")

    return folder, files


def as_zip(files: dict) -> bytes:
    """The files as a zip of the folder's CONTENTS, not of the folder."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(files):
            archive.writestr(relative, files[relative])
    return buffer.getvalue()
