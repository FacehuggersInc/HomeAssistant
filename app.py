#!/usr/bin/env python3
"""
Entry point.

Normally started by launcher.py, which supervises restarts and applies staged
updates while this process is stopped. Running it directly still works -- see
the LAUNCHER_ENV_FLAG check in main().

Usage:
    python app.py                 launch
    python app.py force           launch (explicit; what the launcher passes)
    python app.py update          stage an update and exit
    python app.py apply-update    apply a staged update in place, then exit
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.constants import (
    EXIT_OK,
    EXIT_UPDATE,
    EXIT_RESTART,
    LAUNCHER_ENV_FLAG,
)


def _log(msg: str) -> None:
    print(f"[APP] {msg}", flush=True)


def stage_update() -> int:
    """Download and stage an update, then exit 42 so the launcher applies it."""
    from src import updater
    try:
        updater.stage(log=_log)
    except updater.UpdateError as e:
        _log(f"Update failed: {e}")
        return 1
    return EXIT_UPDATE


def apply_update() -> int:
    """
    Apply a staged update from this process.

    Only for running without the launcher. The launcher path is preferred --
    it keeps the rollback armed until the new version proves it can start.
    """
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

    # Unsupervised. Nothing is watching for 42/43, so honour them here rather
    # than exiting with a code that silently does nothing. This is what the
    # old Client.run() tried to do with subprocess.Popen([sys.executable]),
    # which launched a bare REPL instead of the app.
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
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
