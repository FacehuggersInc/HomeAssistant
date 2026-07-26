from __future__ import annotations

import io
import os
import json
import fnmatch
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

        manifest = {
            "source": url,
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

    copied = skipped = merged = 0
    backed_up: list[str] = []
    added: list[str] = []

    for src in sorted(payload.rglob("*")):
        if not src.is_file():
            continue
        rel = _rel(src, payload)
        if is_preserved(rel):
            skipped += 1
            continue

        dest = INSTALL_ROOT / rel
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

        shutil.copy2(src, dest)
        copied += 1

    (BACKUP_DIR / "backup-manifest.json").write_text(json.dumps({
        "replaced": backed_up,
        "added": added,
    }, indent=2))

    clear_staging()
    log(f"Applied {copied} files ({merged} merged, {skipped} preserved).")
    return {"copied": copied, "merged": merged, "skipped": skipped,
            "replaced": len(backed_up), "added": len(added)}


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
