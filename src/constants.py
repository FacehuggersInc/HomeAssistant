"""
Application-wide constants.

Tier 0: this module must never import anything from `src`, and never anything
outside the standard library. `launcher.py` imports it before third-party
packages are known to be importable, and `src/updater.py` imports it while
running detached from the app.
"""

import os
import platform
from pathlib import Path
from typing import Literal

APP_NAME = "Desktop Home Assistant"

EVENT_LEVELS = Literal["debug", "info", "warning", "error", "critical"]

EVENTS = Literal[
    "initialized",
    "on_focus",
    "on_un_focus",
    "on_visit",
    "on_leave",
    "on_update",
    "on_minimize",
    "on_maximize",
    "on_fullscreen",
    "on_state_change",
    "on_close",
    "on_settings_saved",
    "on_woke_assistant",
    "on_assistant_transcribed",
    "on_plugin_reloading",
    "on_plugin_unload",
    "on_interaction",
    "on_fresh_interaction",
    "on_interaction_timeout",
    "on_collection",
]

# Runtime list form of EVENTS. Literal is a typing construct -- `x in EVENTS`
# silently evaluates False, so anything doing a membership test uses this.
CLIENT_EVENT_NAMES: tuple[str, ...] = EVENTS.__args__


## -- PATHS ------------------------------------------------------------------

# The install root, derived from this file's location rather than os.getcwd()
# or sys.argv[0]. Both of those were in use inconsistently and both break the
# moment the app is launched from anywhere but its own folder -- systemd
# units, autostart entries, desktop shortcuts.
INSTALL_ROOT = Path(__file__).resolve().parent.parent

STAGING_DIR  = INSTALL_ROOT / ".update-staging"
BACKUP_DIR   = INSTALL_ROOT / ".update-backup"
LAUNCHER_LOG = INSTALL_ROOT / "startup.log"


def get_data_dir(app_name: str = APP_NAME) -> Path:
    """Per-user data directory. Lives outside the install root, so updates
    never touch it."""
    if platform.system() == "Windows":
        base = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        # XDG compliant - works on Arch, Mint, Ubuntu etc.
        base = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / app_name.replace(" ", "")


def get_data_file(app_name: str = APP_NAME) -> Path:
    """The settings JSON the Client reads through Dynaconf. The launcher reads
    this directly with the json module, since it runs before the app exists."""
    return get_data_dir(app_name) / f"{app_name.replace(' ', '')}.json"


## -- UPDATES ----------------------------------------------------------------

REPO_ZIP_URL = "https://github.com/FacehuggersInc/HomeAssistant/archive/refs/heads/main.zip"

# Exit codes app.py returns to the launcher.
EXIT_OK      = 0    # clean shutdown, do not relaunch
EXIT_UPDATE  = 42   # a staged update is waiting; apply it, then relaunch
EXIT_RESTART = 43   # relaunch as-is, no update

# Set by launcher.py in the child's environment. app.py uses its absence to
# tell it is running unsupervised, in which case it relaunches itself rather
# than exiting with a code nothing will act on.
LAUNCHER_ENV_FLAG = "HOMEASSISTANT_LAUNCHER"

# Paths (relative to the install root) an update must never overwrite.
# Everything here is either user-owned or machine-specific.
#
# Deliberately NOT preserved: src/assets/data/new-template.json. That is the
# shipped settings template, not user data -- the user's own values live in
# get_data_file(). The old root updater.py preserved it, which meant new
# settings introduced by an update never became visible.
#
# Also note startup.sh / startup.bat / launcher.py are NOT preserved here.
# The previous updaters both preserved startup.sh AND skipped every .sh file
# by extension, which made a launcher bug permanently unfixable in the field.
UPDATE_PRESERVE = {
    ".env",
    ".venv",
    "venv",
    "plugins",
    "logs",
    "startup.log",
    ".update-staging",
    ".update-backup",
}

# Files matched here are MERGED rather than replaced: the shipped file
# provides the structure and any new keys, the installed file provides the
# user's existing "value" entries.
#
# Plugin settings are loaded with `Settings(json.load(...))` and no merge
# step, so a straight overwrite silently resets every setting the user has
# changed. All three previous updaters did exactly that.
UPDATE_MERGE_GLOBS = (
    "src/assets/bundled/*/settings.json",
)
