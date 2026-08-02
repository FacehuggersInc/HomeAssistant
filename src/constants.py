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
    "on_heard_assistant",
    "on_transcribing_assistant",
    "on_assistant_transcribed",
    "on_assistant_cancelled",
    "on_assistant_fallback",
    "on_plugin_reloading",
    "on_plugin_unload",
    "on_interaction",
    "on_fresh_interaction",
    "on_interaction_timeout",
    "on_collection",
]

# Runtime form: `x in EVENTS` against a typing.Literal is silently always False.
CLIENT_EVENT_NAMES: tuple[str, ...] = EVENTS.__args__


## -- PATHS ------------------------------------------------------------------

INSTALL_ROOT = Path(__file__).resolve().parent.parent

STAGING_DIR  = INSTALL_ROOT / ".update-staging"
BACKUP_DIR   = INSTALL_ROOT / ".update-backup"
LAUNCHER_LOG = INSTALL_ROOT / "startup.log"


def get_data_dir(app_name: str = APP_NAME) -> Path:
    if platform.system() == "Windows":
        base = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        # XDG compliant - works on Arch, Mint, Ubuntu etc.
        base = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / app_name.replace(" ", "")


def get_data_file(app_name: str = APP_NAME) -> Path:
    return get_data_dir(app_name) / f"{app_name.replace(' ', '')}.json"


## -- UPDATES ----------------------------------------------------------------

REPO_ZIP_URL = "https://github.com/FacehuggersInc/HomeAssistant/archive/refs/heads/main.zip"

# Exit codes app.py returns to the launcher.
EXIT_OK      = 0    # clean shutdown, do not relaunch
EXIT_UPDATE  = 42   # a staged update is waiting; apply it, then relaunch
EXIT_RESTART = 43   # relaunch as-is, no update

LAUNCHER_ENV_FLAG = "HOMEASSISTANT_LAUNCHER"

UPDATE_PRESERVE = {
    ".env",
    ".venv",
    "venv",
    "plugins",
    "logs",
    "startup.log",
    ".update-staging",
    ".update-backup",
    ".update-version.json",
}

UPDATE_MERGE_GLOBS = (
    "src/assets/bundled/*/settings.json",
)
