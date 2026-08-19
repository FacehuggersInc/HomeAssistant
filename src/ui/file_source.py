"""
What is in a folder, where the interesting folders are, and finding a file.

No Qt beyond `GridItem`, and nothing about how any of it is drawn. Path
handling is where a file explorer's bugs live - a folder that cannot be read,
a symlink pointing at its own parent, a name that is not valid UTF-8 - and
all of that is answerable here, on its own, without a screen.

The dialog that uses this is `ItemGridDialog` with browsing switched on; see
src/ui/grid_dialog.py.
"""

from __future__ import annotations

import os
import platform
import time
from pathlib import Path
from typing import Callable, Optional

from src.ui.grid_dialog import GridItem


#What a name ending in this is, for the icon and for sorting by kind.
KINDS = {
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg",
              ".ico", ".tiff", ".avif"},
    "sound": {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus"},
    "video": {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"},
    "page":  {".txt", ".md", ".json", ".toml", ".yaml", ".yml", ".ini",
              ".cfg", ".log", ".csv", ".xml", ".html", ".py", ".js", ".css"},
}

#Icons for the two things a listing holds that a GridItem kind does not
#already cover.
FOLDER_ICON = "mdi.folder"
UP_ICON = "mdi.arrow-up-bold-box-outline"

#A search stops at whichever of these it reaches first.
#
#Bounded on all three because a mistyped starting folder is `/`, and a search
#that walks a whole disk is one nobody waits for and nobody can cancel from a
#touch screen. Deep rather than wide is the usual shape here, so the depth is
#the loosest of the three.
SEARCH_MAX_RESULTS = 400
SEARCH_MAX_SECONDS = 4.0
SEARCH_MAX_DEPTH = 8


def kind_of(path: Path) -> str:
    """What a file is, by its name. Folders answer `folder`."""
    try:
        if path.is_dir():
            return "folder"
    except OSError:
        return "file"
    suffix = path.suffix.lower()
    for name, endings in KINDS.items():
        if suffix in endings:
            return name
    return "file"


def pretty_size(count: int) -> str:
    """Bytes, as somebody would say them."""
    size = float(count or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}".replace(".0 ", " ")
        size /= 1024
    return f"{size:.1f} TB"


def pretty_date(when: float) -> str:
    """
    A date, shortened by how long ago it was.

    Today is a time, this year is a day and a month, and older is a year.
    A column of full timestamps is a column nobody reads, and the year is
    the part that matters once something is old.
    """
    if not when:
        return ""
    try:
        moment = time.localtime(when)
        now = time.localtime()
    except (OSError, OverflowError, ValueError):
        return ""
    if moment[:3] == now[:3]:
        return time.strftime("%H:%M", moment)
    if moment[0] == now[0]:
        return time.strftime("%d %b", moment)
    return time.strftime("%b %Y", moment)


def _stat(path: Path):
    """`(size, modified)`, or zeros for anything that will not answer."""
    try:
        info = path.stat()
        return int(info.st_size), float(info.st_mtime)
    except (OSError, ValueError):
        # A broken symlink, a file that went between listing and stat, a
        # mount that hung. None of those is a reason to fail the listing.
        return 0, 0.0


def to_item(path: Path, root: Path = None) -> GridItem:
    """
    One path, as something the dialog can draw.

    `key` is the full path, because that is what a caller is choosing. The
    label is only the name - a column of absolute paths is unreadable, and
    the path is on the bar above it.
    """
    kind = kind_of(path)
    is_dir = kind == "folder"
    size, modified = _stat(path)

    if is_dir:
        subtitle = pretty_date(modified)
    else:
        subtitle = f"{pretty_size(size)}  \u00b7  {pretty_date(modified)}".strip()

    # A found file is shown with where it is, since the folder it is in is
    # the thing the search took away.
    if root is not None:
        try:
            where = path.parent.relative_to(root)
            if str(where) not in (".", ""):
                subtitle = f"{where}  \u00b7  {subtitle}" if subtitle else str(where)
        except ValueError:
            pass

    item = GridItem(
        key=str(path),
        label=path.name or str(path),
        # Images preview themselves; everything else takes its kind's icon.
        preview=str(path) if kind == "image" else "",
        subtitle=subtitle,
        icon=FOLDER_ICON if is_dir else "",
        kind="" if is_dir else kind,
        data=path,
    )
    item.is_dir = is_dir
    item.size_bytes = size
    item.modified = modified
    item.path = path
    if is_dir:
        # KINDS has no folder, and a folder must not fall back to the file
        # icon - telling the two apart is the whole job of this list.
        item.icon = FOLDER_ICON
    return item


def entries(folder, show_hidden: bool = False,
            select: str = "both") -> tuple:
    """
    What is in one folder, as `(items, problem)`.

    `problem` is a sentence for the person rather than an exception: a folder
    they cannot read is a normal thing to tap on, and a dialog that closes or
    throws is worse than one that says so and stays where it is.

    Folders are always listed whatever `select` says. Choosing a file means
    walking through folders to reach it, and a file picker that hides them is
    a picker that can only see one directory.
    """
    folder = Path(folder)
    try:
        found = list(folder.iterdir())
    except PermissionError:
        return [], f"No permission to read {folder}."
    except FileNotFoundError:
        return [], f"{folder} is not there any more."
    except NotADirectoryError:
        return [], f"{folder} is a file, not a folder."
    except OSError as e:
        return [], f"Could not read {folder}: {e}"

    items = []
    for entry in found:
        try:
            hidden = entry.name.startswith(".")
        except (UnicodeDecodeError, ValueError):
            # A name the filesystem will not decode. Skipped rather than
            # allowed to break every other row.
            continue
        if hidden and not show_hidden:
            continue

        item = to_item(entry)
        if not item.is_dir and select == "folder":
            continue
        items.append(item)

    return sort_items(items), ""


def sort_items(items: list, key: str = "name",
               descending: bool = False) -> list:
    """
    Folders first, then whatever was asked for.

    Always folders first, whichever sort is chosen: they are the way through
    rather than a choice, and a folder sorted into the middle of a thousand
    files by size is a folder nobody finds.
    """
    orders = {
        "name": lambda i: i.label.lower(),
        "size": lambda i: i.size_bytes,
        "date": lambda i: i.modified,
        "kind": lambda i: (i.kind, i.label.lower()),
    }
    order = orders.get(key, orders["name"])
    return sorted(items,
                  key=lambda i: (not i.is_dir, order(i)),
                  reverse=descending)


def search(root, query: str, show_hidden: bool = False,
           select: str = "both",
           max_results: int = SEARCH_MAX_RESULTS,
           max_seconds: float = SEARCH_MAX_SECONDS,
           max_depth: int = SEARCH_MAX_DEPTH,
           should_stop: Callable = None) -> tuple:
    """
    Everything under `root` whose name matches, as `(items, note)`.

    From here downwards and never above: a search that can leave the folder
    somebody is standing in is a search whose results they cannot place.

    Bounded three ways, and `note` says which bound was hit. A search that
    quietly returns the first four hundred of nine thousand is a search that
    tells somebody their file is not there.

    Symlinked folders are not followed. One pointing at its own parent is a
    walk that never ends, and there is no answer to give afterwards.
    """
    root = Path(root)
    wanted = [word for word in str(query or "").lower().split() if word]
    if not wanted:
        return [], ""

    started = time.time()
    found, note = [], ""
    stack = [(root, 0)]

    while stack:
        if should_stop is not None and should_stop():
            note = "Stopped."
            break
        if len(found) >= max_results:
            note = f"First {max_results}. Narrow it down for the rest."
            break
        if time.time() - started > max_seconds:
            note = "Took too long - showing what was found so far."
            break

        here, depth = stack.pop(0)
        try:
            entries_here = list(here.iterdir())
        except OSError:
            # Unreadable, gone, or a mount that will not answer. Skipped
            # silently: a search reporting every folder it could not open
            # would be a list of complaints rather than of results.
            continue

        for entry in entries_here:
            try:
                name = entry.name
            except (UnicodeDecodeError, ValueError):
                continue
            if name.startswith(".") and not show_hidden:
                continue

            try:
                is_dir = entry.is_dir() and not entry.is_symlink()
            except OSError:
                is_dir = False

            if is_dir and depth < max_depth:
                stack.append((entry, depth + 1))

            if not all(word in name.lower() for word in wanted):
                continue
            if select == "folder" and not is_dir:
                continue
            if select == "file" and is_dir:
                continue
            found.append(to_item(entry, root=root))
            if len(found) >= max_results:
                # Here, not only at the top of the walk. One folder holding a
                # thousand matches adds all thousand before the outer check
                # comes round again, and the cap it was given means nothing.
                note = f"First {max_results}. Narrow it down for the rest."
                stack = []
                break

    return sort_items(found), note


## -- where the interesting folders are


def _removable_linux() -> list:
    """
    Anything mounted where removable media is mounted.

    By where it IS rather than by asking the kernel what the device is:
    /run/media/<user> and /media/<user> are where every desktop automounter
    puts a USB stick, and a panel that has one plugged in has it there.
    Reading /sys/block for the removable flag finds the device and not the
    mount point, which is the part somebody wants to open.
    """
    found = []
    user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    roots = [Path("/run/media") / user, Path("/media") / user,
             Path("/media")]

    for base in roots:
        try:
            if not base.is_dir():
                continue
            for entry in sorted(base.iterdir()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                # A mount point, not just a folder somebody made under /mnt.
                try:
                    if not os.path.ismount(entry):
                        continue
                except OSError:
                    continue
                if entry not in found:
                    found.append(entry)
        except OSError:
            continue
    return found


def _removable_windows() -> list:
    import ctypes
    import string

    DRIVE_REMOVABLE = 2
    found = []
    try:
        kernel = ctypes.windll.kernel32
    except Exception:
        return found
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        try:
            if kernel.GetDriveTypeW(drive) == DRIVE_REMOVABLE:
                found.append(Path(drive))
        except Exception:
            continue
    return found


def _removable_mac() -> list:
    found = []
    try:
        for entry in sorted(Path("/Volumes").iterdir()):
            if entry.is_dir():
                found.append(entry)
    except OSError:
        pass
    return found


def other_mounts() -> list:
    """
    Whatever is mounted under /mnt.

    Kept apart from `removable()` and labelled differently, because they are
    not the same thing. /media and /run/media are where an automounter puts a
    stick somebody just plugged in; /mnt is where somebody mounted a disk on
    purpose and left it there. Calling a permanent second drive "removable"
    invites pulling it out.
    """
    if platform.system() == "Windows":
        return []
    found = []
    try:
        for entry in sorted(Path("/mnt").iterdir()):
            try:
                if entry.is_dir() and os.path.ismount(entry):
                    found.append(entry)
            except OSError:
                continue
    except OSError:
        pass
    return found


def removable() -> list:
    """Mounted removable media, as paths. Empty when there is none."""
    system = platform.system()
    try:
        if system == "Windows":
            return _removable_windows()
        if system == "Darwin":
            return _removable_mac()
        return _removable_linux()
    except Exception:
        # Nothing about looking for a USB stick is worth failing a dialog.
        return []


def shortcut_groups(client=None) -> list:
    """
    The left rail, as `(heading, [(label, path, icon), ...])`.

    Grouped, because the three kinds are not the same kind of thing. Desktop
    is where a person keeps their own files; Assets are the folders the panel
    itself reads from; Mounted is whatever is plugged in or bolted on. A
    single column of them reads as one arbitrary list, and the asset folders
    - which are the ones somebody is usually looking for - sit in the middle
    of it with nothing marking them out.

    Only the groups that have something in them. A heading over nothing is a
    heading that says a feature is missing.
    """
    groups = []
    seen = set()

    def gather(entries) -> list:
        found = []
        for label, path, icon in entries:
            try:
                resolved = Path(path).expanduser()
                if not resolved.is_dir():
                    continue
                key = str(resolved)
                if key in seen:
                    continue
                seen.add(key)
                found.append((label, resolved, icon))
            except (OSError, RuntimeError, ValueError):
                continue
        return found

    desktop = [("Home", Path.home(), "mdi.home")]
    for name, folder in (("Documents", "Documents"), ("Pictures", "Pictures"),
                         ("Downloads", "Downloads"), ("Music", "Music"),
                         ("Videos", "Videos")):
        desktop.append((name, Path.home() / folder, "mdi.folder-outline"))

    assets = []
    try:
        from src.constants import APP_NAME, INSTALL_ROOT, get_data_dir

        assets.append(("Panel", INSTALL_ROOT, "mdi.application-outline"))
        assets.append(("Data", get_data_dir(APP_NAME), "mdi.database-outline"))
        assets.append(("Logs", Path(INSTALL_ROOT) / "logs",
                       "mdi.file-document-outline"))
    except Exception:
        pass
    try:
        for key, asset in (client.ASSETS.get("FOLDER", {}) or {}).items():
            if getattr(asset, "is_guarded", False):
                continue
            assets.append((str(key).replace("_", " ").title(), str(asset),
                           "mdi.folder-outline"))
    except Exception:
        pass

    mounted = [(drive.name or str(drive), drive, "mdi.usb-flash-drive-outline")
               for drive in removable()]
    mounted += [(mount.name or str(mount), mount, "mdi.harddisk")
                for mount in other_mounts()]

    for heading, entries in (("Desktop", desktop), ("Assets", assets),
                             ("Mounted", mounted)):
        found = gather(entries)
        if found:
            groups.append((heading, found))
    return groups


def shortcuts(client=None) -> list:
    """
    The left rail flattened, as `(label, path, icon)`.

    For anything that wants the places without the headings.
    """
    rail = []
    for _heading, entries in shortcut_groups(client):
        rail.extend(entries)
    return rail


def parents_of(folder) -> list:
    """
    Every step from the root down to here, for the path bar.

    `(label, path)` pairs. The first is the filesystem root, which is what
    makes tapping it a way out of anywhere.
    """
    folder = Path(folder)
    steps = []
    for parent in reversed([folder] + list(folder.parents)):
        label = parent.name or str(parent)
        steps.append((label, parent))
    return steps


def resolve(text: str, current=None) -> Optional[Path]:
    """
    What somebody typed, as a folder that exists - or None.

    Relative to where they are standing, because a path bar somebody has
    tapped into is a path bar they are editing rather than replacing.
    """
    text = str(text or "").strip()
    if not text:
        return None
    try:
        path = Path(text).expanduser()
        if not path.is_absolute() and current is not None:
            path = Path(current) / path
        path = path.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    return path if path.exists() else None
