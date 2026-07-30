from __future__ import annotations

import io
import os
import json
from datetime import datetime, timezone
import fnmatch
import filecmp
import stat
import tomllib
import shutil
import zipfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from src.constants import (
    INSTALL_ROOT,
    STAGING_DIR,
    BACKUP_DIR,
    REPO_ZIP_URL,
    UPDATE_PRESERVE,
    UPDATE_MERGE_GLOBS,
)

MANIFEST_NAME = "update-manifest.json"
PAYLOAD_DIR = "payload"

SANITY_PATHS = ("app.py", "src/main.py", "src/constants.py")


class UpdateError(Exception):
    pass


def _noop(msg: str) -> None:
    pass


## -- HELPERS ----------------------------------------------------------------

def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def is_preserved(rel_path: str) -> bool:
    rel_path = rel_path.replace("\\", "/")
    for p in UPDATE_PRESERVE:
        if rel_path == p or rel_path.startswith(p + "/"):
            return True
    return False


def _glob_match(rel_path: str, pattern: str) -> bool:
    parts = rel_path.split("/")
    pat = pattern.split("/")
    if len(parts) != len(pat):
        return False
    return all(fnmatch.fnmatchcase(a, b) for a, b in zip(parts, pat))


def is_merged(rel_path: str) -> bool:
    rel_path = rel_path.replace("\\", "/")
    return any(_glob_match(rel_path, g) for g in UPDATE_MERGE_GLOBS)


def same_file(src: Path, dest: Path) -> bool:
    """
    Whether these two hold the same bytes.

    Compared rather than assumed different. Copying a file that has not changed
    is not free: copy2 takes the SOURCE's permissions, and a file out of a zip
    has none - so every update stripped the executable bit off startup.sh and
    the panel stopped starting on boot until somebody ran chmod again.

    Size first, since it settles most pairs without reading anything.
    """
    try:
        if src.stat().st_size != dest.stat().st_size:
            return False
    except OSError:
        return False
    try:
        return filecmp.cmp(src, dest, shallow=False)
    except OSError:
        return False


def install_once_globs() -> list:
    """
    Files a plugin wants written once and then left alone.

    Declared in the plugin's own `plugin.toml`:

        [update]
        install_once = ["config.json", "data/*.csv"]

    Paths are relative to the plugin's directory, so a plugin does not have to
    know where it is installed. This is for a file the plugin ships a starting
    version of and the person then edits - shipping a default and overwriting
    somebody's edits with it on every update is the same as not shipping one.
    """
    patterns: list = []
    for toml_file in sorted(INSTALL_ROOT.rglob("plugin.toml")):
        # Not into a preserved tree. `.venv` alone can hold thousands of
        # files, and nothing under a preserved path is copied anyway - so
        # reading a toml there would only slow the update down.
        if is_preserved(_rel(toml_file, INSTALL_ROOT)):
            continue
        try:
            with open(toml_file, "rb") as handle:
                config = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        section = config.get("update")
        if not isinstance(section, dict):
            continue
        declared = section.get("install_once")
        if isinstance(declared, str):
            declared = [declared]
        if not isinstance(declared, list):
            continue

        base = _rel(toml_file.parent, INSTALL_ROOT)
        for pattern in declared:
            pattern = str(pattern).replace("\\", "/").lstrip("/")
            if not pattern:
                continue
            patterns.append(f"{base}/{pattern}" if base != "." else pattern)
    return patterns


def is_install_once(rel_path: str, patterns: list) -> bool:
    rel_path = rel_path.replace("\\", "/")
    return any(_glob_match(rel_path, g) for g in patterns)


def merge_values(shipped, installed):
    """
    Structure and new keys from `shipped`, user values from `installed`.

    A settings leaf is {"type":..., "default":..., "value":...} and only
    "value" is user-owned. Keys the shipped version added arrive at their
    default; keys it dropped go away; anything the user changed survives.

    Lives here rather than in settings.py so the launcher's dependency
    surface stays constants + updater. The rollback path has to keep working
    when an update has broken the app, which it cannot do if it imports app
    code to get there.
    """
    if not isinstance(shipped, dict) or not isinstance(installed, dict):
        return shipped
    if "value" in shipped and "value" in installed:
        if type(shipped["value"]) is type(installed["value"]):
            shipped["value"] = installed["value"]
        return shipped
    for key, value in shipped.items():
        if key in installed:
            shipped[key] = merge_values(value, installed[key])
    return shipped


def added_paths(shipped, installed, prefix=""):
    """Dotted paths present in `shipped` but not `installed`. For logging."""
    out = []
    if not isinstance(shipped, dict):
        return out
    if "value" in shipped:
        return out
    for key, value in shipped.items():
        path = f"{prefix}.{key}" if prefix else key
        if not isinstance(installed, dict) or key not in installed:
            out.append(path)
        else:
            out.extend(added_paths(value, installed[key], path))
    return out


def merge_settings_json(shipped: str, installed: str) -> str:
    try:
        new = json.loads(shipped)
        old = json.loads(installed)
    except (json.JSONDecodeError, TypeError):
        return shipped   # unparseable either side -> ship the new file as-is
    return json.dumps(merge_values(new, old), indent=4)


## -- STAGE ------------------------------------------------------------------

def has_staged_update() -> bool:
    return (STAGING_DIR / MANIFEST_NAME).is_file() and (STAGING_DIR / PAYLOAD_DIR).is_dir()


def read_manifest() -> Optional[dict]:
    try:
        return json.loads((STAGING_DIR / MANIFEST_NAME).read_text())
    except Exception:
        return None


def clear_staging() -> None:
    shutil.rmtree(STAGING_DIR, ignore_errors=True)


def stage(url: str = REPO_ZIP_URL, log: Callable[[str], None] = _noop,
          timeout: int = 60) -> dict:
    log("Downloading update...")

    tmp = Path(tempfile.mkdtemp(prefix="ha-update-"))
    try:
        buf = io.BytesIO()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "HomeAssistant-Updater"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                shutil.copyfileobj(r, buf)
        except urllib.error.URLError as e:
            raise UpdateError(f"Download failed: {e.reason}") from e
        except OSError as e:
            raise UpdateError(f"Download failed: {e}") from e

        size = buf.tell()
        if size < 1024:
            raise UpdateError(f"Downloaded archive is implausibly small ({size} bytes)")
        buf.seek(0)

        log(f"Extracting ({size // 1024} KB)...")
        try:
            with zipfile.ZipFile(buf) as z:
                bad = z.testzip()
                if bad is not None:
                    raise UpdateError(f"Archive is corrupt at {bad}")
                _safe_extract(z, tmp)
        except zipfile.BadZipFile as e:
            raise UpdateError(f"Downloaded file is not a valid zip: {e}") from e

        roots = [d for d in tmp.iterdir() if d.is_dir() and d.name != "__MACOSX"]
        if len(roots) != 1:
            raise UpdateError(f"Expected exactly one folder in the archive, found {len(roots)}")
        repo_root = roots[0]

        missing = [p for p in SANITY_PATHS if not (repo_root / p).is_file()]
        if missing:
            raise UpdateError(f"Archive does not look like a valid install (missing {missing})")

        log("Staging...")
        clear_staging()
        payload = STAGING_DIR / PAYLOAD_DIR
        payload.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(repo_root, payload)

        files = []
        for p in sorted(payload.rglob("*")):
            if p.is_file():
                r = _rel(p, payload)
                if not is_preserved(r):
                    files.append(r)

        # Captured here, not at apply time: this is the moment the payload was
        # actually pulled, and the branch head may move before the restart.
        sha = ""
        commit_info = {}
        try:
            from src import update_check
            commit = update_check.latest_commit(timeout=10)
            sha = commit.sha
            commit_info = commit.as_dict()
        except Exception as e:
            log(f"Could not record the source commit ({e}); "
                "the next update check will re-baseline.")

        manifest = {
            "source": url,
            "sha": sha,
            "commit": commit_info,
            "file_count": len(files),
            "files": files,
        }
        (STAGING_DIR / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2))
        log(f"Staged {len(files)} files. Restart to apply.")
        return manifest

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _safe_extract(z: zipfile.ZipFile, dest: Path) -> None:
    dest = dest.resolve()
    for info in z.infolist():
        target = (dest / info.filename).resolve()
        if not str(target).startswith(str(dest)):
            raise UpdateError(f"Archive contains an unsafe path: {info.filename}")
    z.extractall(dest)


## -- APPLY ------------------------------------------------------------------

# Must run with the app stopped - Windows cannot replace an open file.
def apply(log: Callable[[str], None] = _noop) -> dict:
    if not has_staged_update():
        raise UpdateError("No staged update to apply")

    manifest = read_manifest() or {}
    payload = STAGING_DIR / PAYLOAD_DIR

    shutil.rmtree(BACKUP_DIR, ignore_errors=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    copied = skipped = merged = unchanged = kept = 0
    backed_up: list[str] = []
    added: list[str] = []
    once = install_once_globs()

    for src in sorted(payload.rglob("*")):
        if not src.is_file():
            continue
        rel = _rel(src, payload)
        if is_preserved(rel):
            skipped += 1
            continue

        dest = INSTALL_ROOT / rel

        # Already here, and the plugin asked for it to stay that way.
        if dest.exists() and is_install_once(rel, once):
            kept += 1
            continue

        # Byte for byte the same. Not copied at all: see same_file().
        if dest.exists() and not is_merged(rel) and same_file(src, dest):
            unchanged += 1
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)

        if dest.exists():
            backup = BACKUP_DIR / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, backup)
            backed_up.append(rel)
        else:
            added.append(rel)

        if is_merged(rel) and dest.exists():
            try:
                text = merge_settings_json(
                    src.read_text(encoding="utf-8"),
                    dest.read_text(encoding="utf-8"),
                )
                dest.write_text(text, encoding="utf-8")
                merged += 1
                continue
            except OSError:
                pass   # fall through to a plain copy

        # The destination's permissions, where it had some.
        #
        # copy2 takes the source's, and a payload extracted from a zip has
        # none worth having - so a startup script that WAS executable stopped
        # being one every time it changed. Read before the copy, applied after.
        previous_mode = None
        if dest.exists():
            try:
                previous_mode = stat.S_IMODE(dest.stat().st_mode)
            except OSError:
                previous_mode = None

        shutil.copy2(src, dest)

        if previous_mode is not None:
            try:
                current = stat.S_IMODE(dest.stat().st_mode)
                # Only ever adding back what was there. A file the payload made
                # more permissive stays that way; one that lost its execute bit
                # gets it back.
                if current != previous_mode:
                    os.chmod(dest, current | previous_mode)
            except OSError:
                pass

        copied += 1

    (BACKUP_DIR / "backup-manifest.json").write_text(json.dumps({
        "replaced": backed_up,
        "added": added,
    }, indent=2))

    _record_applied_version(manifest, log)

    clear_staging()
    log(f"Applied {copied} files ({merged} merged, {skipped} preserved, "
        f"{unchanged} unchanged, {kept} kept from the first install).")
    return {"copied": copied, "merged": merged, "skipped": skipped,
            "unchanged": unchanged, "kept": kept,
            "replaced": len(backed_up), "added": len(added)}


def _record_applied_version(manifest: dict, log: Callable[[str], None]) -> None:
    """
    Stamp the install with the commit it now contains.

    Without this the checker has nothing to compare against and would keep
    re-baselining after every update - which means the first check following an
    update always says "up to date", whether it is or not.
    """
    commit = (manifest or {}).get("commit") or {}
    if not commit.get("sha"):
        return
    try:
        from src import update_check
        payload = dict(commit)
        payload["recorded_at"] = datetime.now(timezone.utc).isoformat()
        payload["note"] = "applied by the updater"
        update_check.VERSION_FILE.write_text(json.dumps(payload, indent=2))
        log(f"Recorded installed version {commit['sha'][:7]}.")
    except Exception as e:
        log(f"Could not record the installed version: {e}")


## -- ROLLBACK ---------------------------------------------------------------

def has_backup() -> bool:
    return (BACKUP_DIR / "backup-manifest.json").is_file()


def rollback(log: Callable[[str], None] = _noop) -> bool:
    if not has_backup():
        log("No backup to roll back to.")
        return False

    try:
        info = json.loads((BACKUP_DIR / "backup-manifest.json").read_text())
    except Exception as e:
        log(f"Backup manifest unreadable: {e}")
        return False

    restored = removed = 0

    for rel in info.get("replaced", []):
        src = BACKUP_DIR / rel
        if src.is_file():
            dest = INSTALL_ROOT / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            restored += 1

    for rel in info.get("added", []):
        try:
            (INSTALL_ROOT / rel).unlink(missing_ok=True)
            removed += 1
        except OSError:
            pass

    log(f"Rolled back {restored} files, removed {removed} new ones.")
    shutil.rmtree(BACKUP_DIR, ignore_errors=True)
    return True


def clear_backup() -> None:
    shutil.rmtree(BACKUP_DIR, ignore_errors=True)
