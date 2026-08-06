"""
An upload held between being shown and being agreed to.

Overwriting somebody's plugin is not something to do on the strength of a file
picker. The upload therefore happens twice: once to say what it *would* do,
and once to do it. This is what sits in the middle.

Two things make that harder than a dictionary with a token in it:

* The folder can change between the two. Somebody edits a file at the panel,
  or a second upload lands first, and the "yes" that comes back was given to a
  plan that described a folder which no longer exists. The staged plan carries
  a fingerprint of what it was planned against and refuses to apply against
  anything else.
* A plan is single use. A token that still works after it has been applied is
  a second install nobody asked for, arriving from a browser's back button.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from pathlib import Path
from threading import RLock

from src.plugin.install import Plan


#How long a shown plan stands. Long enough to read a list of files and decide,
#short enough that a tab left open overnight cannot apply anything.
EXPIRES_AFTER = 10 * 60.0

#And how many can be waiting at once. A staged plan holds the whole upload in
#memory, so this is a bound on what a stream of previews nobody confirms can
#cost.
MAX_STAGED = 8


def fingerprint(folder: Path) -> str:
    """
    A short digest of what is in `folder` now.

    Names, sizes and modification times rather than contents: this exists to
    notice that the folder moved under a plan, not to verify it, and reading
    every byte of every plugin on every preview is a cost with no answer
    behind it.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return "absent"
    digest = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        if path.is_dir():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        digest.update(str(path.relative_to(folder).as_posix()).encode("utf-8"))
        digest.update(f"{stat.st_size}:{int(stat.st_mtime)}".encode("utf-8"))
    return digest.hexdigest()[:32]


class StagedUpload:

    def __init__(self, plan: Plan, who: str):
        self.token = secrets.token_urlsafe(16)
        self.plan = plan
        self.who = who or "a device"
        self.at = time.time()
        self.against = fingerprint(plan.target)
        self.used = False

    @property
    def expired(self) -> bool:
        return time.time() - self.at > EXPIRES_AFTER


class Staging:
    """The plans currently shown to somebody and not yet answered."""

    def __init__(self):
        self._lock = RLock()
        self._staged: dict = {}

    def stage(self, plan: Plan, who: str) -> StagedUpload:
        with self._lock:
            self._sweep()
            if len(self._staged) >= MAX_STAGED:
                # The oldest goes. Nobody is coming back to a preview from
                # eight uploads ago.
                oldest = min(self._staged, key=lambda t: self._staged[t].at)
                self._staged.pop(oldest, None)
            staged = StagedUpload(plan, who)
            self._staged[staged.token] = staged
            return staged

    def peek(self, token: str) -> StagedUpload | None:
        with self._lock:
            self._sweep()
            return self._staged.get(str(token or ""))

    def claim(self, token: str) -> tuple:
        """
        (staged, refusal) - the plan, or why it will not be applied.

        Removed as it is handed over, whether or not the caller goes on to
        succeed. A token that survives its own use is a back button that
        installs something twice.
        """
        with self._lock:
            # Not swept first. `_sweep` drops expired entries, and dropping
            # one before looking at it turns "that waited too long" into
            # "that was never here" - the same answer given to a made-up
            # token, and no help at all to somebody who left a tab open.
            staged = self._staged.pop(str(token or ""), None)

        if staged is None:
            return None, ("That upload is no longer waiting to be confirmed. "
                          "Upload it again.")
        if staged.used:
            return None, "That upload has already been applied."
        if staged.expired:
            return None, ("That upload waited too long to be confirmed. "
                          "Upload it again.")

        now = fingerprint(staged.plan.target)
        if now != staged.against:
            return None, ("The plugin folder changed since that list was "
                          "made, so applying it could overwrite something "
                          "the list did not mention. Upload it again.")

        staged.used = True
        with self._lock:
            self._sweep()
        return staged, ""

    def _sweep(self) -> None:
        for token, staged in list(self._staged.items()):
            if staged.expired or staged.used:
                self._staged.pop(token, None)


## -- describing a plan --------------------------------------------------------

def installed_version(target) -> str:
    """The version in the plugin.toml already on disk, or ""."""
    from pathlib import Path
    try:
        import tomllib
    except ModuleNotFoundError:                  # pragma: no cover
        import tomli as tomllib                  # type: ignore
    toml = Path(target) / "plugin.toml"
    if not toml.is_file():
        return ""
    try:
        with open(toml, "rb") as handle:
            config = tomllib.load(handle)
        return str((config.get("plugin") or {}).get("version") or "")
    except Exception:
        return ""


def report(plan: Plan, loaded: bool = False, running_version: str = "") -> dict:
    """
    What a plan would do, in the terms somebody deciding needs.

    Grouped by what happens to a file rather than by folder, because the
    question being answered is "what am I about to lose", and the answer is
    the overwrite list. New files are listed separately: adding one is not a
    thing to be nervous about, and mixing the two makes a list of forty
    additions look like forty losses.
    """
    overwritten = [c.path for c in plan.changes if c.action == "update"]
    created = [c.path for c in plan.changes if c.action == "create"]
    merged = [c.path for c in plan.changes if c.action == "merge"]
    kept = [{"path": c.path, "why": c.detail}
            for c in plan.changes if c.action == "install_once"]
    unchanged = [c.path for c in plan.changes if c.action == "unchanged"]

    notes = []
    if merged:
        notes.append("Settings are merged: anything you have changed keeps "
                     "its value, and new options arrive at their defaults.")
    if kept:
        notes.append("Some files are left alone because the plugin asked for "
                     "them to be written once and then never again.")
    if unchanged and not (overwritten or created or merged):
        notes.append("Every file in this upload is already identical to what "
                     "is installed, so nothing would change.")

    # The one thing that is true after the files land and is not obvious from
    # them: the folder is new and the code in memory is not.
    if loaded:
        reload_note = ("This plugin is running. The files change immediately, "
                       "but the running plugin does not - it must be reloaded "
                       "for these changes to take effect.")
    elif plan.is_new:
        reload_note = ("This plugin is not installed yet. It will not do "
                       "anything until it is loaded.")
    else:
        reload_note = ("This plugin is installed but not running. It will "
                       "pick these changes up when it is next loaded.")

    # What is on disk now, so the card can say 0.1.0 -> 0.2.0 rather than
    # showing one number and leaving somebody to remember the other.
    was = running_version or installed_version(plan.target)
    if was and plan.version and was != plan.version:
        notes.insert(0, f"Version {was} becomes {plan.version}.")
    elif was and plan.version and was == plan.version:
        notes.insert(0, f"Both are version {plan.version}. If this is a "
                        f"change, the version was not bumped.")

    return {
        "name":            plan.name,
        "key":             plan.key,
        "version":         plan.version,
        "was_version":     was,
        "running_version": running_version,
        "new":             plan.is_new,
        "loaded":          loaded,
        "overwritten":     sorted(overwritten),
        "created":         sorted(created),
        "merged":          sorted(merged),
        "kept":            kept,
        "unchanged":       sorted(unchanged),
        "writes":          len(overwritten) + len(created) + len(merged),
        "notes":           notes,
        "reload_note":     reload_note,
        "needs_reload":    bool(loaded and (overwritten or created or merged)),
    }
