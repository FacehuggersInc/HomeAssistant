"""
Packages: everything another machine needs, built on demand.

A package is not a file sitting in the tree waiting to be served. It is a
**builder** - a function that runs when somebody asks for it and returns the
files. That difference is the whole point: the panel knows its own address,
its port, the voice that is configured and the model that is chosen, so the
script it hands over can be already pointed at the right place and the README
can say exactly what to type at both ends. A static zip cannot know any of
that, and every one of those details is a step somebody would otherwise get
wrong once and then debug.

Setting up a second machine is the thing this exists to make short. The
speech server is the first of them; a plugin wanting to ship a companion
script, a systemd unit or a config file registers a builder of its own and
appears in the same list.

Owner-keyed like every other registry, so a plugin's packages go when the
plugin does.
"""

from __future__ import annotations

import io
import re
import zipfile

#What a package may be called. The name reaches a filename and a URL, so it
#is the same shape a plugin key is and for the same reasons.
KEY_OK = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

#A version is displayed and compared by eye, never parsed. Anything that reads
#as a version is fine; anything with a slash in it is not, because it ends up
#in a filename.
VERSION_OK = re.compile(r"^[0-9][A-Za-z0-9._-]{0,31}$")


class BadPackage(Exception):
    """The package could not be described, or could not be built."""


class Package:
    """One buildable bundle."""

    def __init__(self, owner: str, key: str, name: str, builder,
                 description: str = "", version: str = "1.0",
                 contents: tuple = ()):
        self.owner = owner
        self.key = key
        self.name = name
        self.builder = builder
        self.description = description or ""
        self.version = version or "1.0"
        # What is in it, in words, for somebody deciding whether to download
        # it. A package is code that will be run on another machine, and
        # "trust me" is not a reasonable thing to ask of a download.
        self.contents = tuple(contents or ())

    def build(self) -> tuple:
        """
        (folder name, {relpath: bytes}), from the builder.

        Called at the moment of download rather than at registration, so what
        comes out reflects the settings as they are now.
        """
        made = self.builder()
        if not isinstance(made, tuple) or len(made) != 2:
            raise BadPackage(f"'{self.key}' did not answer with a folder and "
                             f"its files.")
        folder, files = made
        if not folder or not isinstance(files, dict) or not files:
            raise BadPackage(f"'{self.key}' built nothing.")
        for path, body in files.items():
            if not isinstance(body, (bytes, bytearray)):
                raise BadPackage(f"'{self.key}' put something in {path} that "
                                 f"is not bytes.")
            if path.startswith("/") or ".." in path.split("/"):
                # A path that climbs out of the folder it is unpacked into is
                # the oldest archive trick there is, and this archive is built
                # by a plugin and unpacked by somebody on another machine.
                raise BadPackage(f"'{self.key}' tried to write outside its "
                                 f"own folder: {path}")
        return folder, files

    def describe(self) -> dict:
        return {"key": self.key, "name": self.name, "owner": self.owner,
                "version": self.version, "description": self.description,
                "contents": list(self.contents)}

    def matches(self, needle: str) -> bool:
        """Whether a search should find this."""
        if not needle:
            return True
        needle = needle.strip().lower()
        haystack = " ".join((self.key, self.name, self.description,
                             self.owner, " ".join(self.contents))).lower()
        return needle in haystack


class PackageRegistry:

    def __init__(self, client):
        self.client = client
        self.packages: dict = {}

    ## -- registering

    def register(self, owner: str, key: str, name: str, builder,
                 description: str = "", version: str = "1.0",
                 contents: tuple = ()) -> bool:
        """
        Offer a package for download.

        `builder` takes nothing and answers `(folder, {relpath: bytes})` - the
        same shape the plugin skeleton produces, so `as_zip` is shared and a
        package can be edited and sent back like any other folder.

        A key another owner holds is refused and logged, the way every other
        registry refuses one. The same owner may re-register, which is how a
        reloaded plugin puts its builder back.
        """
        key = str(key or "").strip().lower()
        if not KEY_OK.match(key):
            self.client.log("warning",
                            f"[Packages] '{key}' will not work as a package "
                            f"key. Lowercase letters, numbers, dashes and "
                            f"underscores, starting with a letter.")
            return False
        version = str(version or "1.0").strip()
        if not VERSION_OK.match(version):
            self.client.log("warning",
                            f"[Packages] '{version}' will not work as a "
                            f"version for '{key}'.")
            return False
        if not callable(builder):
            self.client.log("warning",
                            f"[Packages] '{key}' was registered without "
                            f"anything to build it.")
            return False

        held = self.packages.get(key)
        if held is not None and held.owner != owner:
            self.client.log("warning",
                            f"[Packages] '{owner}' cannot register '{key}' - "
                            f"'{held.owner}' already has it.")
            return False

        self.packages[key] = Package(owner, key, str(name or key), builder,
                                     description, version, contents)
        return True

    def unregister(self, owner: str, key: str = "") -> int:
        gone = [k for k, p in self.packages.items()
                if p.owner == owner and (not key or k == key)]
        for k in gone:
            self.packages.pop(k, None)
        return len(gone)

    ## -- asking

    def get(self, key: str):
        return self.packages.get(str(key or "").strip().lower())

    def all(self, search: str = "") -> list:
        """Every package a search matches, by name."""
        found = [p for p in self.packages.values() if p.matches(search)]
        return sorted(found, key=lambda p: (p.name.lower(), p.key))

    def for_owner(self, owner: str) -> list:
        return [p for p in self.packages.values() if p.owner == owner]

    def build(self, key: str) -> tuple:
        """
        (folder, zip bytes) for one package.

        Raises `BadPackage` when there is no such package or its builder
        failed - the caller is answering a download and a reason is what it
        has to show.
        """
        package = self.get(key)
        if package is None:
            raise BadPackage(f"There is no package called '{key}'.")
        try:
            folder, files = package.build()
        except BadPackage:
            raise
        except Exception as exc:
            raise BadPackage(f"'{key}' could not be built: {exc}")
        return folder, as_zip(files)


def as_zip(files: dict) -> bytes:
    """
    The files as a zip of the folder's CONTENTS, not of the folder.

    The same shape the plugin skeleton produces, so unpacking one inside a
    folder somebody made puts the files where they belong rather than nesting
    them one deeper.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(files):
            archive.writestr(relative, files[relative])
    return buffer.getvalue()
