#!/usr/bin/env python3

from __future__ import annotations

import os
import sys
import json
import time
import subprocess
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.constants import (
    INSTALL_ROOT,
    LAUNCHER_LOG,
    LAUNCHER_ENV_FLAG,
    EXIT_OK,
    EXIT_UPDATE,
    EXIT_RESTART,
    EXIT_RUNNING,
    get_data_file,
)
from src import updater

EXIT_LAUNCHER_UPDATED = 44

APP_ENTRY = INSTALL_ROOT / "app.py"

DEFAULTS = {
    "restart_on_crash":     True,
    "max_restart_attempts": 5,
    "crash_window":         120,   # sec; running longer than this resets the counter
    "update_grace_period":  60,    # sec; failing inside this after an update -> rollback
}


## -- LOGGING ----------------------------------------------------------------

def log(msg: str) -> None:
    line = f"[{datetime.now():%H:%M:%S}] [LAUNCHER] {msg}"
    print(line, flush=True)
    try:
        with open(LAUNCHER_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


## -- SETTINGS ---------------------------------------------------------------

def load_settings() -> dict:
    out = dict(DEFAULTS)
    try:
        data = json.loads(get_data_file().read_text(encoding="utf-8"))
        section = data.get("application", {}).get("updates", {})
        for key in out:
            entry = section.get(key)
            if isinstance(entry, dict) and "value" in entry:
                value = entry["value"]
                if isinstance(value, type(out[key])):
                    out[key] = value
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        pass
    return out


## -- APP PROCESS ------------------------------------------------------------

def run_app() -> int:
    env = dict(os.environ)
    env[LAUNCHER_ENV_FLAG] = "1"
    log("Launching app...")
    try:
        return subprocess.call([sys.executable, str(APP_ENTRY), "force"],
                               cwd=str(INSTALL_ROOT), env=env)
    except KeyboardInterrupt:
        return EXIT_OK
    except OSError as e:
        log(f"Could not launch app: {e}")
        return 1


def launcher_hash() -> str:
    try:
        import hashlib
        return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()
    except OSError:
        return ""


## -- MAIN LOOP --------------------------------------------------------------

def main() -> int:
    log("=== launcher started ===")
    log(f"root={INSTALL_ROOT}")
    log(f"python={sys.executable}")

    if not APP_ENTRY.is_file():
        log(f"FATAL: {APP_ENTRY} not found")
        return 1

    settings = load_settings()
    log(f"policy: restart_on_crash={settings['restart_on_crash']} "
        f"max_attempts={settings['max_restart_attempts']}")

    attempts = 0
    just_updated = False

    while True:
        # ---- apply a staged update before starting anything
        if updater.has_staged_update():
            log("Staged update found, applying...")
            before = launcher_hash()
            try:
                result = updater.apply(log)
                just_updated = True
                attempts = 0
            except updater.UpdateError as e:
                log(f"Update failed to apply: {e}")
                updater.clear_staging()
                just_updated = False
            else:
                if launcher_hash() != before:
                    log("launcher.py was updated -- re-running with new code.")
                    return EXIT_LAUNCHER_UPDATED
                # settings schema may have changed with the update
                settings = load_settings()

        # ---- run
        started = time.time()
        code = run_app()
        ran_for = time.time() - started
        log(f"App exited with code {code} after {ran_for:.1f}s")

        # ---- stood down: a panel is already running here
        #
        # Ahead of the crash policy, because this is not a crash. Restarting
        # would retry five times against a panel that is working perfectly,
        # and the exponential backoff would make it look like a boot loop.
        if code == EXIT_RUNNING:
            log("A panel is already running on this machine. Nothing to do.")
            return 0

        # ---- clean shutdown
        if code == EXIT_OK:
            if just_updated:
                updater.clear_backup()
            log("Clean exit. Done.")
            return 0

        # ---- app asked to restart, with or without an update
        if code in (EXIT_UPDATE, EXIT_RESTART):
            if just_updated:
                updater.clear_backup()
                just_updated = False
            attempts = 0
            reason = "update" if code == EXIT_UPDATE else "restart"
            log(f"Relaunching ({reason})...")
            continue

        # ---- anything else is a crash

        # a fresh update that dies immediately gets reverted
        if just_updated and ran_for < settings["update_grace_period"]:
            log(f"Update failed to start (crashed in {ran_for:.1f}s). Rolling back...")
            if updater.rollback(log):
                just_updated = False
                attempts = 0
                log("Rollback complete. Relaunching previous version...")
                continue
            log("Rollback unavailable -- continuing with crash policy.")

        just_updated = False

        if not settings["restart_on_crash"]:
            log("restart_on_crash is off. Not restarting.")
            return code

        # a long-lived run that later crashed is not a boot loop
        if ran_for >= settings["crash_window"]:
            attempts = 0

        attempts += 1
        if attempts > settings["max_restart_attempts"]:
            log(f"Giving up after {attempts - 1} restart attempts.")
            return code

        delay = min(2 ** attempts, 30)
        log(f"Restarting in {delay}s (attempt {attempts}/{settings['max_restart_attempts']})...")
        time.sleep(delay)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("Interrupted.")
        sys.exit(0)
