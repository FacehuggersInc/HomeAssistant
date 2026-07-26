"""
The update engine.

Replaces three divergent copies of this logic: app.py's do_update(), the
/update route in backend.py, and the old root-level updater.py (which nothing
ever invoked). They disagreed about what to preserve, all overwrote files
while the app was still running, and none could roll back.

The split here is deliberate:

  stage()   runs INSIDE the live app. It only downloads, extracts and
            validates into .update-staging/. It never touches an installed
            file, so it is safe to run while the UI is up and safe to fail.

  apply()   runs from the launcher, with the app stopped. Nothing is holding
            file handles at that point, which is what makes this work on
            Windows -- shutil.copy2 onto a file open in another process
            raises PermissionError there, and the old in-place updaters
            would leave a half-written install behind when it did.

  rollback() restores what apply() replaced.

Standard library only. The launcher imports this before third-party packages
are known to be importable.
"""

from __future__ import annotations

import io
import os
import json
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

# A repo zip missing these is not a valid install and is refused before
# anything on disk is touched.
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


def is_merged(rel_path: str) -> bool:
    rel_path = rel_path.replace("\\", "/")
    return any(Path(rel_path).match(g) for g in UPDATE_MERGE_GLOBS)


def merge_settings_json(shipped: str, installed: str) -> str:
    """
    Take structure and new keys from the shipped file, keep the user's own
    "value" entries from the installed one.

    A settings leaf looks like {"type":..., "default":..., "value":...}. Only
    "value" is user-owned; everything else belongs to whoever shipped the
    plugin. A key the new version dropped is dropped; a key it added arrives
    at its default.
    """
    try:
        new = json.loads(shipped)
        old = json.loads(installed)
    except (json.JSONDecodeError, TypeError):
        return shipped   # unparseable either side -> ship the new file as-is

    def walk(n, o):
        if not isinstance(n, dict) or not isinstance(o, dict):
            return n
        if "value" in n and "value" in o:
            # a leaf: adopt the user's value if the type still lines up
            if type(n["value"]) is type(o["value"]):
                n["value"] = o["value"]
            return n
        for k, v in n.items():
            if k in o:
                n[k] = walk(v, o[k])
        return n

    return json.dumps(walk(new, old), indent=4)


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
    """
    Download and unpack an update into .update-staging/ without touching the
    running install. Returns the manifest. Raises UpdateError on any failure,
    leaving the installed tree untouched.
    """
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
    """Extract, refusing entries that would escape dest (zip-slip)."""
    dest = dest.resolve()
    for info in z.infolist():
        target = (dest / info.filename).resolve()
        if not str(target).startswith(str(dest)):
            raise UpdateError(f"Archive contains an unsafe path: {info.filename}")
    z.extractall(dest)


## -- APPLY ------------------------------------------------------------------

def apply(log: Callable[[str], None] = _noop) -> dict:
    """
    Apply the staged update. MUST run with the app stopped.

    Every file replaced is copied into .update-backup/ first, so rollback()
    can put things back exactly as they were. Returns a result dict.
    """
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
    """Restore whatever the last apply() replaced, and delete what it added."""
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
