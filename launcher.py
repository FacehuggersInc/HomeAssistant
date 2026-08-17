#!/usr/bin/env python3

from __future__ import annotations

import os
import sys
import json
import time
import subprocess
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

#Somewhere to write before the real log is reachable.
#
#Everything below imports from `src`, and until those succeed there is no
#LAUNCHER_LOG constant to write to and no log() to call. An ImportError here
#- a half-copied update, a file that did not land, a name that moved - killed
#the launcher with a traceback on a stderr nobody is watching, and left
#nothing on disk at all. The one failure that most needs writing down was the
#one that could not be.
_FALLBACK_LOG = Path(__file__).resolve().parent / "startup.log"


def _emergency(message: str) -> None:
    """Never raises. This runs when the normal path is not available."""
    line = f"[{datetime.now():%H:%M:%S}] [LAUNCHER] {message}"
    print(line, file=sys.stderr, flush=True)
    try:
        with open(_FALLBACK_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


try:
    from src.constants import (
        INSTALL_ROOT,
        LAUNCHER_LOG,
        LAUNCHER_ENV_FLAG,
        EXIT_OK,
        EXIT_UPDATE,
        EXIT_RESTART,
        EXIT_RUNNING,
        EXIT_MISSING,
        get_data_file,
        clear_exit_intent,
        take_exit_intent,
    )
    from src import updater
    from src import dependencies
except Exception as e:
    _emergency(f"FATAL: could not import the application: "
               f"{type(e).__name__}: {e}")
    _emergency("This usually means an update landed only partly - a file that "
               "did not copy, or one that did while another did not.")
    for line in traceback.format_exc().splitlines():
        _emergency(f"    {line}")
    sys.exit(1)

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
    #Whether pip has already been run for a missing package this session.
    #
    #One retry, not a loop. If installing did not fix it, running pip again
    #will not either - the package is unavailable for this platform, the
    #index is unreachable, or the name in requirements.txt is wrong - and a
    #launcher that keeps trying hides that behind a boot loop.
    installed_for_missing = False

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

        # ---- packages, before the thing that imports them
        #
        # An update copies files; it does not install anything. A release
        # that added a dependency therefore started a panel that could not
        # import it, and the launcher restarted that five times before
        # giving up. Checked every loop rather than once, because an update
        # applied above may have brought a new requirements.txt with it.
        try:
            dependencies.ensure(log)
        except Exception as e:
            log(f"Could not check requirements: {type(e).__name__}: {e}")

        # ---- run
        #
        # Any leftover marker is cleared first, so what is read afterwards can
        # only have been written by the run that just happened.
        clear_exit_intent()
        started = time.time()
        code = run_app()
        ran_for = time.time() - started
        log(f"App exited with code {code} after {ran_for:.1f}s")

        # ---- died on the way out, having already finished
        #
        # A negative code is a signal, and the usual one here is a segfault
        # in Qt's or Chromium's teardown - after the app has closed its
        # plugins, saved its settings and decided what to exit with. Somebody
        # closing the panel deliberately then watched it start itself again,
        # which is the opposite of what they asked for.
        #
        # The intent is only consulted for a signal. A process that returned
        # a code returned the one it meant.
        intent = take_exit_intent()
        if code < 0 and intent is not None:
            log(f"The process died on signal {-code} during shutdown, after "
                f"finishing cleanly and asking to exit with {intent}. Taking "
                f"it at its word.")
            code = intent
        elif code < 0:
            log(f"The process died on signal {-code} without finishing its "
                f"own shutdown. Treating it as a crash.")

        # ---- a package is missing
        #
        # Not a crash, and not something a restart fixes. The stamp cannot
        # see this case - requirements.txt is unchanged and something removed
        # or broke the environment - so the install is forced rather than
        # asked for.
        if code == EXIT_MISSING:
            if installed_for_missing:
                log("Still missing a package after installing. Not retrying.")
                log("Install the requirements by hand and start again:")
                log(f"    {sys.executable} -m pip install -r "
                    f"{INSTALL_ROOT / 'requirements.txt'}")
                return code
            installed_for_missing = True
            log("A package is missing. Installing the requirements...")
            if not dependencies.install(log):
                log("The install did not succeed. Not retrying.")
                return code
            attempts = 0
            continue

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
        elif not just_updated:
            # Said rather than passed over.
            #
            # A rollback needs a backup, and only `updater.apply()` makes
            # one. A version put here any other way - a zip unpacked over the
            # tree, a git pull, files copied by hand - has nothing to go back
            # TO, so nothing happens and nothing explains why. Somebody
            # watching a crash loop is entitled to know the launcher is not
            # quietly declining to help.
            if updater.has_backup():
                log("Not rolling back: this run did not follow an update. "
                    "A backup exists from an earlier one - "
                    "`python app.py apply-update` is not what you want; "
                    "restore it by hand if this version is the problem.")
            else:
                log("No rollback available: this version was not installed by "
                    "the updater, so there is no backup of the previous one.")

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
