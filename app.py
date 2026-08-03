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
    LAUNCHER_ENV_FLAG,
)


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
    from src.main import Client

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
