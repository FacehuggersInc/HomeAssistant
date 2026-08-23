"""
Enough of `pkg_resources` for a library that only wants its own version.

setuptools v82 removed `pkg_resources`, and `webrtcvad` imports it on its
first line to fill in `__version__` and for nothing else. So an environment
with a current setuptools cannot import webrtcvad, the speech process refuses
to start, and installing setuptools does not help - the module is gone from
it, not missing from it.

**Pinning `setuptools<82` is the other answer and is worse.** It holds the
whole environment on a superseded build tool to satisfy one line in one
dependency, and `install()` is a single all-or-nothing pip call, so a pin
that conflicts with anything else fails every package in requirements.txt at
once.

The shim is installed **only around the import that needs it**, and only when
the real module is genuinely absent. A fake `pkg_resources` left in
`sys.modules` is worse than none: a package that asks for a part this does not
have gets an `AttributeError` where it would have got a clean
`ImportError` and taken its own fallback. Libraries read their version at
import time, so the shim has nothing left to do once the import returns.
"""

from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from importlib import metadata


class DistributionNotFound(Exception):
    """No package by that name. `pkg_resources` raises this; so does this."""


class _Distribution:
    """The two attributes anything reaching for a version actually reads."""

    def __init__(self, name: str, version: str):
        self.project_name = name
        self.key = name.lower()
        self.version = version

    def __repr__(self) -> str:
        return f"{self.project_name} {self.version}"


def get_distribution(name) -> _Distribution:
    """
    `importlib.metadata` behind the old name.

    The same question, answered from the standard library, which is where the
    packages still using this are being told to go.
    """
    name = str(getattr(name, "project_name", name) or "")
    try:
        return _Distribution(name, metadata.version(name))
    except metadata.PackageNotFoundError as exc:
        raise DistributionNotFound(str(exc)) from exc


def _module() -> types.ModuleType:
    module = types.ModuleType("pkg_resources")
    module.__doc__ = __doc__
    # Deliberately small. Whatever else `pkg_resources` offered, nothing here
    # asks for it, and inventing a wider surface is inventing behaviour that
    # has never been checked against the real one.
    module.get_distribution = get_distribution
    module.DistributionNotFound = DistributionNotFound
    module.__ha_shim__ = True
    return module


def available() -> bool:
    """Whether the real `pkg_resources` can be imported."""
    try:
        import pkg_resources  # noqa: F401
    except ImportError:
        return False
    return True


@contextmanager
def installed():
    """
    Stand in for `pkg_resources` for the length of the block, if it is absent.

    A no-op when the real one is there, so an environment on an older
    setuptools behaves exactly as it did and nothing here is in the way.
    """
    if available():
        yield False
        return

    sys.modules["pkg_resources"] = _module()
    try:
        yield True
    finally:
        # Only ours, and only if nothing has replaced it since. Removing a
        # real module somebody else installed in the meantime would be a
        # stranger failure than the one this exists to fix.
        current = sys.modules.get("pkg_resources")
        if getattr(current, "__ha_shim__", False):
            del sys.modules["pkg_resources"]
