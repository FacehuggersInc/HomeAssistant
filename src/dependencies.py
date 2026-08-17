"""
Keeping the installed packages in step with requirements.txt.

An update can add a dependency. Nothing about copying files installs it, so
the next launch imports a module that is not there and the panel does not
start - and the failure is an ImportError deep in a traceback, on a machine
with no console, from a launcher that was restarting it five times before
giving up.

The check is a stamp, not a survey. Asking pip whether every requirement is
satisfied means resolving the file on every boot, which is slow and needs the
network to be sure; hashing requirements.txt and comparing it to the hash
that was last installed answers the question that actually matters - "has
this changed since anything was done about it" - from one file read.

That leaves one case the stamp cannot see: a virtual environment rebuilt or
broken while requirements.txt stayed the same. `install()` is therefore also
callable directly, which is what the launcher does when the app dies for a
missing module.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Callable

from src.constants import INSTALL_ROOT

REQUIREMENTS = INSTALL_ROOT / "requirements.txt"

#Beside the file it describes. Not in the data directory: this is a fact
#about THIS copy of the tree, and an install moved or copied elsewhere should
#not inherit a stamp saying its packages are already in place.
STAMP = INSTALL_ROOT / ".requirements-installed"

#Long. This is pip against an index, possibly building a wheel, on hardware
#that may be a small board. Ten minutes is not generous for onnx or torch.
INSTALL_TIMEOUT = 900


def _noop(msg: str) -> None:
    pass


def _hash() -> str:
    """requirements.txt as a digest, or empty when there is no such file."""
    try:
        return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    except OSError:
        return ""


def _stamped() -> str:
    try:
        return STAMP.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def needs_install() -> bool:
    """
    Whether requirements.txt has changed since anything was installed from it.

    False when there is no requirements.txt to read - a tree without one is
    not a tree with unmet requirements, and guessing otherwise would run pip
    on every boot forever.
    """
    current = _hash()
    if not current:
        return False
    return current != _stamped()


def _write_stamp() -> None:
    try:
        STAMP.write_text(_hash(), encoding="utf-8")
    except OSError:
        # A read-only install. The install still happened; it will simply be
        # attempted again next time, which is wasteful rather than wrong.
        pass


def install(log: Callable[[str], None] = _noop, stamp: bool = True) -> bool:
    """
    Run pip against requirements.txt. Returns whether it succeeded.

    `sys.executable -m pip` rather than a bare `pip`, so the packages land in
    the interpreter that is about to import them. A panel with a virtual
    environment and a system pip on PATH installs into the wrong one
    otherwise, and the symptom is identical to not having installed at all.

    Only stamps on success. A failed install that recorded itself as done
    would never be retried, and the panel would be permanently short of a
    package with nothing left to say so.
    """
    if not REQUIREMENTS.is_file():
        log("No requirements.txt - nothing to install.")
        return False

    log(f"Installing from {REQUIREMENTS.name} (this can take a while)...")
    try:
        done = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)],
            capture_output=True, text=True, timeout=INSTALL_TIMEOUT,
            cwd=str(INSTALL_ROOT),
        )
    except subprocess.TimeoutExpired:
        log(f"pip did not finish within {INSTALL_TIMEOUT}s. Giving up on it.")
        return False
    except Exception as e:
        log(f"Could not run pip: {type(e).__name__}: {e}")
        return False

    if done.returncode != 0:
        log(f"pip failed ({done.returncode}).")
        # The last few lines, not the whole build. pip's output is long and
        # the reason is at the end of it.
        for line in (done.stderr or done.stdout or "").strip().splitlines()[-8:]:
            log(f"    {line}")
        return False

    log("Requirements installed.")
    if stamp:
        _write_stamp()
    return True


def ensure(log: Callable[[str], None] = _noop) -> bool:
    """
    Install only if requirements.txt has changed. Returns whether it ran.

    The ordinary boot reads one small file, compares two hashes and returns.
    """
    if not needs_install():
        return False
    log("requirements.txt has changed since packages were last installed.")
    install(log)
    return True
