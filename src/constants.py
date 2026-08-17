import os
import platform
from pathlib import Path
from typing import Literal, Optional

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
    "on_transcribed_assistant",
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
EXIT_OK       = 0    # clean shutdown, do not relaunch
EXIT_UPDATE   = 42   # a staged update is waiting; apply it, then relaunch
EXIT_RESTART  = 43   # relaunch as-is, no update
EXIT_RUNNING  = 45   # another panel already holds the port; this one stood down
EXIT_MISSING  = 46   # a Python package is missing; install requirements and retry

LAUNCHER_ENV_FLAG = "HOMEASSISTANT_LAUNCHER"

#What the app MEANT to exit with, written just before it does.
#
#A process can finish its own shutdown cleanly and then die on the way out:
#Qt and the Chromium engine tear themselves down after the interpreter has
#finished with them, and a segfault there is reported as signal 11 no matter
#how tidy the exit was. The launcher sees -11, calls it a crash, and starts
#the app somebody just closed.
#
#The exit code cannot carry this - the process never got to return one - so
#it is left on disk a moment earlier instead. Read and deleted by the
#launcher on every run, so a stale one is never believed.
EXIT_INTENT = INSTALL_ROOT / ".exit-intent"


def record_exit_intent(code: int) -> None:
    """Say what the exit was meant to be, before anything can prevent it."""
    try:
        EXIT_INTENT.write_text(str(int(code)), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        # A read-only install, or a code that is not one. Neither is worth
        # failing a shutdown over; the launcher simply falls back to the
        # signal, which is what it did before this existed.
        pass


def clear_exit_intent() -> None:
    try:
        EXIT_INTENT.unlink()
    except OSError:
        pass


def take_exit_intent() -> Optional[int]:
    """The recorded intent, removed as it is read. None when there is none."""
    try:
        raw = EXIT_INTENT.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    finally:
        clear_exit_intent()
    try:
        return int(raw)
    except ValueError:
        return None

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
    # A fact about THIS machine's packages, not about the version. An update
    # replacing it would say the new requirements.txt had already been
    # installed from, which is the one thing it cannot know.
    ".requirements-installed",
    # Written and consumed within a single run. Shipping one would be a
    # message from another machine's shutdown.
    ".exit-intent",
}

UPDATE_MERGE_GLOBS = (
    "src/assets/bundled/*/settings.json",
)
