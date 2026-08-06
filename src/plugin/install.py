"""
Installing and updating a plugin from a zip.

A **plugin** here means a folder under `plugins/` - one that did not ship with
the app. Bundled plugins are part of the project and are updated with it; these
are not, and until now had no way to be updated at all except by somebody with
a keyboard and the machine in front of them.

This module decides what a zip would do and then does it. The two halves are
separate on purpose: an install is the one operation in the app that lets a
remote request write executable code onto the panel, so what it is about to do
has to be inspectable before it happens - by a person, at the panel, if the
plugin is new.

Nothing here touches the client, the registries or Qt. It is a folder, a zip
and a set of rules, so it can be exercised without a panel.

## The rules an update follows

The same ones a project update follows, because a plugin author's expectations
should not change with how the code arrived:

* **`[update] install_once`** files are written when they are absent and left
  alone forever after. This is for a file the plugin ships a starting version
  of and the person then edits.
* **`settings.json` is merged**, not replaced: structure and new keys from the
  incoming version, values from the installed one.
* **Identical files are not written.** Not an optimisation - copying takes the
  source's permissions, and a file out of a zip has none.
* A zip may contain **only the files that changed**. Anything it does not
  mention is left where it is, and the rules above are read from the installed
  `plugin.toml` when the zip does not carry one.
"""

from __future__ import annotations

import fnmatch
import io
import json
import posixpath
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:                      # pragma: no cover
    import tomli as tomllib                      # type: ignore


class Refusal(Exception):
    """The upload will not be installed, and this is why."""


#A zip that unpacks to more than this, or holds more members than this, is
#refused unread. Neither number is a plugin - the largest bundled one is a few
#hundred KB across a few dozen files - and a decompression bomb is otherwise
#free to fill the disk of a machine nobody is sitting at.
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 4000

#Never taken out of a zip. Build droppings and version control, which are
#large, meaningless to the running plugin, and in the case of `.pyc` actively
#harmful: a stale bytecode file beside a newer source is a plugin that runs
#code nobody uploaded.
SKIP_PARTS = {"__pycache__", ".git", ".svn", ".hg", ".idea", ".vscode"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".pyd"}
SKIP_NAMES = {".DS_Store", "Thumbs.db"}

#A folder name that can exist on every filesystem the panel runs on, and that
#is a legal Python package name so the loader can import it.
NAME_OK = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")

#Same rule as the create form uses, so a version accepted in one place is
#accepted in the other.
VERSION_OK = re.compile(r"^[0-9][0-9A-Za-z.\-+]{0,31}$")

#What a plugin needs before it can ever load. Checked for a NEW plugin only -
#an update is allowed to be partial, and usually is.
REQUIRED_FOR_NEW = ("plugin.toml", "main.py")


@dataclass
class Change:
    """One file, and what installing would do to it."""
    path: str
    action: str          # create | update | merge | unchanged | install_once
    detail: str = ""

    @property
    def writes(self) -> bool:
        return self.action in ("create", "update", "merge")


@dataclass
class Plan:
    """
    What a zip would do to `plugins/<name>`, before anything is written.

    Held rather than performed so it can be shown to somebody. A new plugin is
    a person granting a stranger's code the run of their house; an update is a
    person who already did that agreeing to a newer version of it. They are
    different decisions and the panel asks them differently.
    """
    name: str
    target: Path
    exists: bool
    key: str
    version: str
    changes: list = field(default_factory=list)
    files: dict = field(default_factory=dict)     # relpath -> bytes

    @property
    def is_new(self) -> bool:
        return not self.exists

    def counted(self, action: str) -> int:
        return sum(1 for change in self.changes if change.action == action)

    def summary(self) -> dict:
        return {
            "name":         self.name,
            "key":          self.key,
            "version":      self.version,
            "new":          self.is_new,
            "creates":      self.counted("create"),
            "updates":      self.counted("update"),
            "merges":       self.counted("merge"),
            "unchanged":    self.counted("unchanged"),
            "install_once": self.counted("install_once"),
            "writes":       sum(1 for c in self.changes if c.writes),
        }


## -- reading a zip ------------------------------------------------------------

def _member_is_symlink(info: zipfile.ZipInfo) -> bool:
    """
    Whether a zip entry is a symlink.

    Zip stores the unix mode in the top 16 bits of `external_attr`, and a
    symlink extracted into a plugin folder is a file that points anywhere on
    the disk - including out of it. Refused rather than followed.
    """
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _clean_member(name: str) -> str:
    """
    A member's path as a safe relative posix path, or "" to skip it.

    Every dangerous shape a zip can carry is refused here rather than caught
    later: absolute paths, `..` anywhere in them, Windows drive letters, and
    the build droppings that are merely noise.
    """
    name = str(name or "").replace("\\", "/").strip()
    if not name or name.endswith("/"):
        return ""
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return ""

    parts = [p for p in name.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return ""
    if any(p in SKIP_PARTS for p in parts):
        return ""
    if not parts or parts[-1] in SKIP_NAMES:
        return ""
    if posixpath.splitext(parts[-1])[1].lower() in SKIP_SUFFIXES:
        return ""
    return "/".join(parts)


def _strip_common_root(files: dict) -> tuple:
    """
    ({relpath: bytes}, stripped_name) with a single wrapping folder removed.

    A zip made by right-clicking a folder wraps everything in that folder's
    name; one made from inside it does not. Both are things somebody will
    upload, and the difference must not decide where the files land.

    Only when EVERY member shares the folder, and only when removing it leaves
    something - a plugin whose files are all inside `src/` is not a plugin
    wrapped in `src`.
    """
    if not files:
        return files, ""
    roots = {path.split("/")[0] for path in files if "/" in path}
    flat = [path for path in files if "/" not in path]
    if flat or len(roots) != 1:
        return files, ""

    root = roots.pop()
    stripped = {path.split("/", 1)[1]: data for path, data in files.items()}
    if not stripped or any(not p for p in stripped):
        return files, ""
    return stripped, root


def pack(folder: Path) -> bytes:
    """
    A plugin folder as a zip of its CONTENTS.

    The same shape the upload page accepts and the create page hands out, so
    downloading a plugin, editing it and sending it back is a round trip with
    nothing to rearrange in the middle.

    The droppings a zip should never carry are left out on the way IN as
    well - see `_clean_member` - but leaving them out here too means the file
    somebody downloads is not full of `__pycache__` from a plugin that has
    been running for a month.
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise Refusal(f"There is no folder at {folder}.")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_dir() or path.is_symlink():
                continue
            relative = path.relative_to(folder).as_posix()
            if not _clean_member(relative):
                continue
            archive.write(path, relative)
    return buffer.getvalue()


def read_zip(data: bytes, name: str = "") -> tuple:
    """
    ({relpath: bytes}, folder name) from an uploaded zip.

    Raises `Refusal` with a sentence a person can act on, which is the whole
    reason this is not just `ZipFile.extractall` - that would say
    "BadZipFile" to somebody who dragged the wrong file in.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except Exception as e:
        raise Refusal(f"That is not a readable zip file ({e}).") from e

    infos = [i for i in archive.infolist() if not i.is_dir()]
    if len(infos) > MAX_MEMBERS:
        raise Refusal(f"That zip holds {len(infos)} files, which is more than "
                      f"a plugin should be.")
    total = sum(max(0, i.file_size) for i in infos)
    if total > MAX_TOTAL_BYTES:
        raise Refusal(f"That zip unpacks to {total // (1024 * 1024)}MB, which "
                      f"is more than a plugin should be.")

    files: dict = {}
    for info in infos:
        if _member_is_symlink(info):
            raise Refusal(f"That zip contains a symbolic link "
                          f"({info.filename!r}), which is not allowed in a "
                          f"plugin.")
        relative = _clean_member(info.filename)
        if not relative:
            continue
        try:
            files[relative] = archive.read(info)
        except Exception as e:
            raise Refusal(f"Could not read {info.filename!r} from the zip "
                          f"({e}).") from e

    if not files:
        raise Refusal("That zip has nothing in it that a plugin could use.")

    files, stripped = _strip_common_root(files)
    return files, (name or stripped)


## -- planning -----------------------------------------------------------------

def safe_folder_name(name: str) -> str:
    """
    A folder name that is legal here, or "".

    Refused rather than reduced. Taking `Path(name).name` would turn
    `../evil` into `evil` and `a/b` into `b`, which is safe and silent - the
    upload lands somewhere the person did not name and the panel says nothing
    about it. A name with a separator in it is a mistake or an attack, and
    both deserve an answer.
    """
    name = str(name or "").strip()
    if name.lower().endswith(".zip"):
        name = name[:-4]
    if "/" in name or "\\" in name or name in (".", ".."):
        return ""
    return name if NAME_OK.match(name) else ""


def _read_toml(data: bytes) -> dict:
    try:
        return tomllib.loads(data.decode("utf-8"))
    except Exception as e:
        raise Refusal(f"The plugin.toml could not be read ({e}).") from e


def install_once_patterns(config: dict) -> list:
    """`[update] install_once` from a plugin's own config, as a glob list."""
    section = config.get("update")
    if not isinstance(section, dict):
        return []
    declared = section.get("install_once")
    if isinstance(declared, str):
        declared = [declared]
    if not isinstance(declared, list):
        return []
    return [str(p).replace("\\", "/").lstrip("/") for p in declared
            if str(p).strip()]


def _matches(relative: str, patterns: list) -> bool:
    parts = relative.split("/")
    for pattern in patterns:
        bits = pattern.split("/")
        if len(bits) != len(parts):
            continue
        if all(fnmatch.fnmatchcase(a, b) for a, b in zip(parts, bits)):
            return True
    return False


#`version = "..."` inside the [plugin] table, and the table header itself.
#
#Rewritten with a regex rather than by re-serialising the file. A toml round
#trip through a parser and a writer loses every comment in it, and a plugin's
#toml is mostly comments - the install_once block is four lines of explanation
#and one of syntax. Changing a version should not cost somebody their notes.
_VERSION_LINE = re.compile(r'^(\s*version\s*=\s*)"[^"]*"',
                           re.MULTILINE)
_PLUGIN_TABLE = re.compile(r'^\[plugin\]\s*$', re.MULTILINE)


def set_version(toml_text: str, version: str) -> str:
    """`toml_text` with `version` in its [plugin] table. Added if absent."""
    if _VERSION_LINE.search(toml_text):
        return _VERSION_LINE.sub(rf'\g<1>"{version}"', toml_text, count=1)
    found = _PLUGIN_TABLE.search(toml_text)
    if not found:
        return f'[plugin]\nversion = "{version}"\n' + toml_text
    at = found.end()
    return f'{toml_text[:at]}\nversion = "{version}"{toml_text[at:]}'


def plan(files: dict, name: str, plugins_dir: Path, version: str = "",
         taken: dict = None) -> Plan:
    """
    What installing `files` as `plugins/<name>` would do.

    Nothing is written. Every refusal that can be found without touching the
    disk is found here, so the panel can say no before it has half-written a
    plugin folder.

    `taken` maps keys already in use to the folder using them - normally
    `{key: folder}` for every plugin the panel knows about. A key belonging to
    another folder is refused: a plugin folder is found by scanning, the first
    key wins, and a second folder claiming it can never load. Passed in rather
    than looked up, so this module stays a folder, a zip and a set of rules.
    """
    folder = safe_folder_name(name)
    if not folder:
        raise Refusal(f"{name!r} is not a usable plugin folder name. Use "
                      f"letters, numbers, dashes and underscores, starting "
                      f"with a letter.")

    plugins_dir = Path(plugins_dir)
    target = plugins_dir / folder
    exists = target.is_dir()

    if not exists:
        missing = [f for f in REQUIRED_FOR_NEW if f not in files]
        if missing:
            raise Refusal(
                f"A new plugin needs {' and '.join(REQUIRED_FOR_NEW)} at the "
                f"top of the zip; this one is missing "
                f"{' and '.join(missing)}.")

    # A version typed on the form overrides whatever the zip says - and where
    # the zip carries no toml at all, it edits the installed one.
    #
    # That second case is the point of it. A partial upload is a handful of
    # changed files with no toml among them, so there is otherwise no way to
    # say that the thing on disk is now 0.2.0 - and a version that never moves
    # is a version that tells nobody anything.
    version = str(version or "").strip()
    if version and not VERSION_OK.match(version):
        raise Refusal(
            f"{version!r} will not work as a version. Start with a digit and "
            f"use numbers, letters, dots and dashes - 0.1.0, 2024.3, 1.0-beta.")

    installed_toml = (target / "plugin.toml") if exists else None
    if version:
        if "plugin.toml" in files:
            files = dict(files)
            files["plugin.toml"] = set_version(
                files["plugin.toml"].decode("utf-8"), version).encode("utf-8")
        elif installed_toml is not None and installed_toml.is_file():
            files = dict(files)
            files["plugin.toml"] = set_version(
                installed_toml.read_text(encoding="utf-8"),
                version).encode("utf-8")
        else:
            raise Refusal("There is no plugin.toml to put that version in.")

    # The incoming toml if the zip carries one, the installed one if not. An
    # update is allowed to be a handful of changed files, and the rules for
    # applying it still have to come from somewhere.
    config: dict = {}
    if "plugin.toml" in files:
        config = _read_toml(files["plugin.toml"])
    elif exists and (target / "plugin.toml").is_file():
        config = _read_toml((target / "plugin.toml").read_bytes())

    section = config.get("plugin") if isinstance(config.get("plugin"), dict) else {}
    key = str(section.get("key") or "").strip()
    version = str(section.get("version") or "").strip()
    if not key:
        raise Refusal("The plugin.toml has no [plugin] key, so nothing could "
                      "load it.")

    # A folder is a plugin's identity to the loader. A zip whose toml claims a
    # different key than the one already installed there is a different plugin
    # wearing this one's name, and applying it would leave a folder whose
    # contents and settings belong to two plugins at once.
    if exists and (target / "plugin.toml").is_file():
        try:
            installed = _read_toml((target / "plugin.toml").read_bytes())
            was = str((installed.get("plugin") or {}).get("key") or "").strip()
        except Refusal:
            was = ""
        if was and key and was != key:
            raise Refusal(
                f"{folder!r} already holds the plugin {was!r}, and this zip is "
                f"{key!r}. Upload it under its own name instead of replacing "
                f"another plugin.")

    # The key belongs to another folder, so this one can never load.
    owner = (taken or {}).get(key)
    if owner and str(owner) != folder:
        raise Refusal(
            f"The key {key!r} already belongs to the plugin in {str(owner)!r}. "
            f"Plugin folders are scanned in order and the first key wins, so "
            f"this one would never load. Change `key` in its plugin.toml to "
            f"something else and upload it again.")

    once = install_once_patterns(config)
    changes = []
    for relative in sorted(files):
        destination = target / relative
        if not exists or not destination.exists():
            if _matches(relative, once) and not exists:
                # A first install writes them; only later ones leave them.
                changes.append(Change(relative, "create", "install once"))
            else:
                changes.append(Change(relative, "create"))
            continue

        # install_once is checked FIRST, including against settings.json.
        # Merging is this module's idea of what is polite; install_once is
        # the plugin author saying "do not touch this", and the author wins.
        # A plugin that ships a settings file it then writes to itself would
        # otherwise have it rewritten from the shipped copy on every update.
        if _matches(relative, once):
            changes.append(Change(relative, "install_once",
                                  "kept - the plugin asked for this"))
            continue
        if relative == "settings.json":
            changes.append(Change(relative, "merge", "values kept"))
            continue
        try:
            if destination.read_bytes() == files[relative]:
                changes.append(Change(relative, "unchanged"))
                continue
        except OSError:
            pass
        changes.append(Change(relative, "update"))

    return Plan(name=folder, target=target, exists=exists, key=key,
                version=version, changes=changes, files=files)


## -- applying -----------------------------------------------------------------

def _merged_settings(shipped: bytes, installed: Path) -> bytes:
    """The incoming settings with the installed values kept."""
    from src.updater import merge_values
    try:
        new = json.loads(shipped.decode("utf-8"))
        old = json.loads(installed.read_text(encoding="utf-8"))
    except Exception:
        # Unreadable either side: the incoming file wins, which is what
        # would have happened without a merge at all.
        return shipped
    merged = merge_values(new, old)
    return json.dumps(merged, indent=4).encode("utf-8")


def apply(plan_: Plan) -> dict:
    """
    Carry out a plan, or leave the folder exactly as it was.

    Every file this replaces is held in memory first and put back if anything
    later in the run fails. A plugin half-written is a plugin that imports and
    then breaks somewhere unrelated, which is a worse outcome than a refused
    upload - and the only moment it can be avoided is this one.
    """
    target = plan_.target
    backup: dict = {}
    created: list = []
    written = 0

    try:
        target.mkdir(parents=True, exist_ok=True)
        for change in plan_.changes:
            if not change.writes:
                continue
            destination = target / change.path
            destination.parent.mkdir(parents=True, exist_ok=True)

            if destination.exists():
                backup[change.path] = destination.read_bytes()
            else:
                created.append(destination)

            data = plan_.files[change.path]
            if change.action == "merge" and destination.exists():
                data = _merged_settings(data, destination)
            destination.write_bytes(data)
            written += 1
    except Exception as e:
        for relative, data in backup.items():
            try:
                (target / relative).write_bytes(data)
            except OSError:
                pass
        for path in created:
            try:
                path.unlink()
            except OSError:
                pass
        if plan_.is_new:
            # It did not exist before this, so there is nothing to roll back
            # to - the folder goes with it rather than being left as a
            # half-plugin the loader will try to import.
            shutil.rmtree(target, ignore_errors=True)
        raise Refusal(f"Installing {plan_.name!r} failed and was rolled back "
                      f"({e}).") from e

    summary = plan_.summary()
    summary["written"] = written
    return summary
