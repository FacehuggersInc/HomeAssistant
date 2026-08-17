#!/usr/bin/env python3

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Chromium's own dark mode, before anything imports WebEngine.
#
# Set here because these are read once when the engine initialises, and it
# initialises on the first import of QtWebEngine - anything later is ignored.
#
# forceDarkModeEnabled inverts a light page rather than asking it for a dark
# theme, which is why the image policy matters: without it, photographs come
# out as negatives. Selective leaves images alone and darkens the rest.
#
# MediaSessionService is what puts a Chromium page on the system's media
# controls - MPRIS, here. The panel has a hidden page playing music, and a
# page that announces itself there is announced to the panel's own reader of
# it: the music card hands back to the system source, the system source finds
# the page still holding the track, and the card opens again showing what it
# just closed. Nothing needs it - the player is driven by JavaScript, not by
# media keys.
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", " ".join([
    "--blink-settings="
    "forceDarkModeEnabled=true,"
    "forceDarkModeImagePolicy=1,"
    "forceDarkModeInversionAlgorithm=4",
    "--disable-features=MediaSessionService,SystemMediaControls",
]))

from src.constants import (
    EXIT_OK,
    EXIT_UPDATE,
    EXIT_RESTART,
    EXIT_RUNNING,
    EXIT_MISSING,
    LAUNCHER_ENV_FLAG,
)
from src.crash import install as install_crash_handlers

# Before anything else can fail. A panel that dies takes its reason with it
# otherwise: the launcher restarts it, whatever was on stderr is gone, and
# what is left to work from is "it crashed on the night page".
install_crash_handlers()


USAGE = """Usage:
    python app.py                 launch
    python app.py force           launch (what the launcher passes)
    python app.py update          stage an update and exit
    python app.py apply-update    apply a staged update in place, then exit"""


def _log(msg: str) -> None:
    print(f"[APP] {msg}", flush=True)


def stage_update() -> int:
    from src import updater
    try:
        updater.stage(log=_log)
    except updater.UpdateError as e:
        _log(f"Update failed: {e}")
        return 1
    return EXIT_UPDATE


def apply_update() -> int:
    from src import updater
    if not updater.has_staged_update():
        _log("No staged update to apply.")
        return 1
    try:
        result = updater.apply(log=_log)
    except updater.UpdateError as e:
        _log(f"Apply failed: {e}")
        return 1
    _log(f"Applied {result['copied']} files.")
    return EXIT_OK


def launch() -> int:
    # Before the window, the plugins or the Flask thread.
    #
    # In app.py rather than in the launcher because both ways in come
    # through here: the launcher runs this file, and running it directly is
    # how the duplicate usually happens in the first place. A check the
    # launcher owns is a check that a bare `python app.py` walks straight
    # past.
    #
    # Fails open. Not starting is the worst outcome this can produce, and it
    # is a convenience check - if it cannot run, the right answer is to say
    # so and carry on, not to leave somebody with a panel that will not boot
    # and no idea why.
    try:
        from src.single_instance import already_running
        running, why = already_running()
    except Exception as e:
        _log(f"Could not check for another panel ({type(e).__name__}: {e}). "
             f"Starting anyway.")
        running, why = False, ""

    if running:
        _log(why)
        _log("Not starting. Close the other one first, or use its window.")
        return EXIT_RUNNING

    # The import that pulls in the whole application, and the one that finds
    # out whether its packages are here.
    #
    # A missing package is not a crash and should not be treated as one: it
    # cannot be fixed by restarting, and five attempts with a backoff turns
    # one clear ImportError into two minutes of a panel looking broken. Named
    # as its own exit code so the launcher can install what is missing and
    # try once more.
    try:
        from src.main import Client
    except ModuleNotFoundError as e:
        _log(f"A required package is missing: {e.name or e}")
        _log("The application cannot start until it is installed.")
        return EXIT_MISSING

    client = Client()
    try:
        client.run()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else EXIT_OK
    else:
        code = EXIT_OK

    # Under the launcher, exit codes are the whole protocol -- just return.
    if os.environ.get(LAUNCHER_ENV_FLAG):
        return code

    if code in (EXIT_UPDATE, EXIT_RESTART):
        if code == EXIT_UPDATE:
            rc = apply_update()
            if rc not in (EXIT_OK,):
                return rc
        _log("Relaunching...")
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), "force"])

    return code


def main() -> int:
    args = [a.lower() for a in sys.argv[1:]]

    if not args or "force" in args:
        return launch()
    if "update" in args:
        return stage_update()
    if "apply-update" in args:
        return apply_update()

    print(f"Unknown arguments: {sys.argv[1:]}")
    print(USAGE)
    return 1


if __name__ == "__main__":
    sys.exit(main())
