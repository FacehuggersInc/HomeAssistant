"""
Somewhere for a crash to leave its reason.

The panel restarts itself. That is the right behaviour and it is also how a
crash disappears: the launcher brings it back, whatever went to stderr is
gone with the old process, and what is left to debug from is the memory of
which page was on screen.

Three different things can end a Qt application and only one of them is an
ordinary traceback:

* an unhandled exception on the main thread - `sys.excepthook`
* one on a worker thread, which Python 3.8+ routes separately and which
  otherwise prints to a stderr nobody is reading - `threading.excepthook`
* a **fault** - a segfault, or an abort from C++ code touching a deleted
  object - which raises no Python exception at all and is why `faulthandler`
  is here rather than a bare try/except somewhere

All three end up in the same file, appended rather than overwritten, because
the interesting crash is very often not the most recent one.
"""

from __future__ import annotations

import faulthandler
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

_LOG: Path | None = None
_HANDLE = None


def log_path() -> Path:
    """`logs/crash.log` beside the install, made if it is not there."""
    from src.constants import INSTALL_ROOT
    folder = Path(INSTALL_ROOT) / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "crash.log"


def _write(header: str, body: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = f"\n{'=' * 70}\n{stamp}  {header}\n{'=' * 70}\n{body}\n"
    try:
        with open(_LOG or log_path(), "a", encoding="utf-8") as handle:
            handle.write(text)
    except Exception:
        pass
    # And to stderr as well, for anybody running it from a terminal.
    try:
        sys.stderr.write(text)
        sys.stderr.flush()
    except Exception:
        pass


def _excepthook(kind, value, tb) -> None:
    _write("Unhandled exception on the main thread",
           "".join(traceback.format_exception(kind, value, tb)))
    if _PREVIOUS_HOOK is not None:
        try:
            _PREVIOUS_HOOK(kind, value, tb)
        except Exception:
            pass


def _thread_excepthook(args) -> None:
    name = getattr(args.thread, "name", "?")
    _write(f"Unhandled exception on thread {name!r}",
           "".join(traceback.format_exception(
               args.exc_type, args.exc_value, args.exc_traceback)))


_PREVIOUS_HOOK = None


def install() -> Path:
    """
    Turn all three on. Returns where they will write.

    Safe to call twice; the second call is a no-op rather than a second open
    file handle on the same path.
    """
    global _LOG, _HANDLE, _PREVIOUS_HOOK
    if _LOG is not None:
        return _LOG

    _LOG = log_path()
    _PREVIOUS_HOOK = sys.excepthook
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook

    try:
        # Held open for the life of the process on purpose. A fault handler
        # cannot open a file at the moment it is needed - the interpreter is
        # already in no state to - so the descriptor has to exist first.
        _HANDLE = open(_LOG, "a", encoding="utf-8")
        _HANDLE.write(f"\n{'=' * 70}\n"
                      f"{datetime.now():%Y-%m-%d %H:%M:%S}  started\n")
        _HANDLE.flush()
        faulthandler.enable(file=_HANDLE, all_threads=True)
    except Exception as e:
        try:
            sys.stderr.write(f"[crash] Could not arm faulthandler: {e}\n")
        except Exception:
            pass
    return _LOG
