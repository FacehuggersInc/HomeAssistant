from __future__ import annotations

import re
import sys
import subprocess
from importlib import metadata
from pathlib import Path
from typing import Callable, Iterable, Optional

from src.constants import INSTALL_ROOT

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
    return re.sub(r"[-_.]+", "-", name).lower()


def split_requirement(spec: str) -> tuple[str, str]:
    spec = spec.strip()
    if not spec or spec.startswith("#"):
        return "", ""
    m = _REQ_RE.match(spec)
    if not m:
        return "", ""
    return m.group(1), (m.group(3) or "").strip()


def requirements_of(config) -> list[str]:
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
    return [s for s in specs if not is_satisfied(s)]


## -- PROTECTED SET ----------------------------------------------------------

def core_packages() -> set[str]:
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

    # Required: importlib.metadata caches distributions, so a package installed
    # in this process still reports as missing without this.
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
    import importlib
    importlib.invalidate_caches()
    try:
        metadata.MetadataPathFinder.invalidate_caches()
    except Exception:
        pass
