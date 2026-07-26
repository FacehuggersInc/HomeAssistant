"""
Plugin pip dependency handling.

A plugin declares what it needs in its own plugin.toml:

    [requirements]
    pip = ["feedparser>=6.0", "Pillow"]

Nothing here ever runs automatically. Installing and uninstalling are both
driven by an explicit user action -- a plugin's toml is arbitrary text from
wherever the user got the plugin, so silently handing it to pip would make
dropping a folder into plugins/ equivalent to running an installer.

Everything targets the interpreter currently running the app
(`sys.executable -m pip`), which inside a virtualenv is that virtualenv's
pip. `assert_venv()` refuses to run at all outside one, so a misconfigured
launch cannot quietly install into the system Python.
"""

from __future__ import annotations

import re
import sys
import subprocess
from importlib import metadata
from pathlib import Path
from typing import Callable, Iterable, Optional

from src.constants import INSTALL_ROOT

# name[extras]specifier -- we only need the distribution name and the
# specifier tail; extras are passed through to pip untouched.
_REQ_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")

PIP_TIMEOUT = 600  # sec


class DependencyError(Exception):
    pass


def _noop(msg: str) -> None:
    pass


## -- ENVIRONMENT ------------------------------------------------------------

def in_venv() -> bool:
    return sys.prefix != sys.base_prefix


def assert_venv() -> None:
    if not in_venv():
        raise DependencyError(
            "Not running inside a virtualenv. Refusing to install or remove "
            "packages, since they would go into the system Python. Start the "
            "app through startup.sh / startup.bat."
        )


def venv_path() -> str:
    return sys.prefix


## -- PARSING ----------------------------------------------------------------

def normalize(name: str) -> str:
    """PEP 503 normalisation, so Pillow / pillow / PIL-style casing and
    separator differences all compare equal."""
    return re.sub(r"[-_.]+", "-", name).lower()


def split_requirement(spec: str) -> tuple[str, str]:
    """'requests>=2.28' -> ('requests', '>=2.28'). Returns ('', '') if the
    spec is unparseable, which the caller treats as invalid."""
    spec = spec.strip()
    if not spec or spec.startswith("#"):
        return "", ""
    m = _REQ_RE.match(spec)
    if not m:
        return "", ""
    return m.group(1), (m.group(3) or "").strip()


def requirements_of(config) -> list[str]:
    """
    Pull the pip requirement list out of a plugin's parsed toml.

    Accepts either shape, since both read naturally:

        [requirements]
        pip = [...]

        [plugin]
        requirements = [...]
    """
    out: list[str] = []

    def _collect(value):
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, (list, tuple)):
            out.extend(str(v) for v in value)

    if config is None:
        return []

    try:
        get = config.get
    except AttributeError:
        return []

    section = get("requirements", None)
    if section is not None:
        try:
            _collect(section.get("pip", None))
        except AttributeError:
            _collect(section)

    plugin_section = get("plugin", None)
    if plugin_section is not None:
        try:
            _collect(plugin_section.get("requirements", None))
        except AttributeError:
            pass

    # de-duplicate, keep declared order
    seen, unique = set(), []
    for spec in out:
        key = normalize(split_requirement(spec)[0])
        if key and key not in seen:
            seen.add(key)
            unique.append(spec.strip())
    return unique


## -- SATISFACTION -----------------------------------------------------------

def installed_version(name: str) -> Optional[str]:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None
    except Exception:
        return None


def _specifier_ok(version: str, specifier: str) -> bool:
    """
    Check a version against a specifier, using `packaging` when it is
    importable and degrading to a presence check when it is not.

    packaging is not in requirements.txt -- it is only ever present as a
    transitive dependency -- so this cannot assume it. Treating an
    uncheckable specifier as satisfied is the safe direction: the worst case
    is not prompting for an upgrade, rather than repeatedly prompting to
    install something already there.
    """
    if not specifier:
        return True
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version
        return Version(version) in SpecifierSet(specifier)
    except ImportError:
        return True
    except Exception:
        return True


def is_satisfied(spec: str) -> bool:
    name, specifier = split_requirement(spec)
    if not name:
        return True   # unparseable: not something we can act on
    version = installed_version(name)
    if version is None:
        return False
    return _specifier_ok(version, specifier)


def missing(specs: Iterable[str]) -> list[str]:
    """The subset of specs not currently satisfied, in declared order."""
    return [s for s in specs if not is_satisfied(s)]


## -- PROTECTED SET ----------------------------------------------------------

def core_packages() -> set[str]:
    """
    Normalised distribution names from the app's own requirements.txt.

    These are never uninstallable through the plugin UI. A plugin declaring
    `requests` and then being uninstalled would otherwise take out the Flask
    backend with it.
    """
    out: set[str] = set()
    req = INSTALL_ROOT / "requirements.txt"
    try:
        for line in req.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            line = line.split(";", 1)[0].strip()   # drop environment markers
            name, _ = split_requirement(line)
            if name:
                out.add(normalize(name))
    except OSError:
        pass
    return out


def removable_for(plugin_specs: Iterable[str],
                  other_plugin_specs: Iterable[str]) -> tuple[list[str], dict[str, str]]:
    """
    Split a plugin's requirements into what may be uninstalled and what must
    be kept, with a reason for each kept one.

    Returns (removable_names, {kept_name: reason}).
    """
    core = core_packages()
    others = {normalize(split_requirement(s)[0]) for s in other_plugin_specs}
    others.discard("")

    removable: list[str] = []
    kept: dict[str, str] = {}

    for spec in plugin_specs:
        name, _ = split_requirement(spec)
        if not name:
            continue
        norm = normalize(name)
        if norm in core:
            kept[name] = "required by the app itself"
        elif norm in others:
            kept[name] = "required by another installed plugin"
        elif installed_version(name) is None:
            kept[name] = "not installed"
        else:
            removable.append(name)

    return removable, kept


## -- PIP --------------------------------------------------------------------

def _run_pip(args: list[str], log: Callable[[str], None]) -> tuple[bool, str]:
    assert_venv()
    cmd = [sys.executable, "-m", "pip", "--disable-pip-version-check", *args]
    log(f"$ {' '.join(cmd[2:])}")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(INSTALL_ROOT),
            capture_output=True,
            text=True,
            timeout=PIP_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, f"pip timed out after {PIP_TIMEOUT}s"
    except OSError as e:
        return False, f"could not run pip: {e}"

    output = (proc.stdout or "") + (proc.stderr or "")
    for line in output.splitlines():
        if line.strip():
            log(line.rstrip())
    return proc.returncode == 0, output


def install(specs: Iterable[str], log: Callable[[str], None] = _noop) -> tuple[bool, str]:
    specs = [s for s in specs if split_requirement(s)[0]]
    if not specs:
        return True, "nothing to install"
    log(f"Installing into {venv_path()}")
    ok, out = _run_pip(["install", *specs], log)

    # importlib.metadata caches the distribution list, so a package installed
    # in this process is otherwise still reported as missing afterwards --
    # which would make the plugin fail to load immediately after a
    # successful install.
    invalidate_caches()
    return ok, out


def uninstall(names: Iterable[str], log: Callable[[str], None] = _noop) -> tuple[bool, str]:
    names = [n for n in names if n]
    if not names:
        return True, "nothing to uninstall"
    ok, out = _run_pip(["uninstall", "-y", *names], log)
    invalidate_caches()
    return ok, out


def invalidate_caches() -> None:
    """Make freshly installed or removed distributions visible to this
    process without a restart."""
    import importlib
    importlib.invalidate_caches()
    try:
        metadata.MetadataPathFinder.invalidate_caches()
    except Exception:
        pass
