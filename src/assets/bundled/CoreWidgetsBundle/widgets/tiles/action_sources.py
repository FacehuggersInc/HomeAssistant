"""
What an action tile can be pointed at.

One place that walks the registries and answers with a flat list of things
that can be run, each described the same way whatever it came from. The
dialogs above this do not need to know that an endpoint is a Flask route and a
public entry is a bound method - they need a name, where it came from, and
what arguments it takes.

Only callables. A registry holds values as well - the calendar exposes its
sticker store, which is an object rather than a function - and a tile that
"runs" one of those has nothing to run.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from src.main import Client


#Where a runnable came from. The tile needs this to call it back later.
FROM_ENDPOINT = "endpoint"
FROM_PUBLIC = "public"
FROM_PLAYER = "player"
FROM_QUICK = "quick"
FROM_USER = "user"
FROM_AUDIO = "audio"
FROM_PAGE = "page"
#The panel's own HTTP routes. Not in the API registry - that one holds what
#plugins registered, and these are built into the backend - but they are the
#most useful things here and nothing else lists them.
FROM_CORE = "core"

#One icon per registry, so a list can be read by where things came from
#without anybody reading the subtitle.
#
#The registry rather than the entry: an endpoint may register an icon of its
#own, and using those here means the column says something different on every
#row and nothing about where anything lives. The endpoint's own icon is still
#its own - it is what the dashboard draws.
REGISTRY_ICONS = {
    FROM_ENDPOINT: "mdi.api",
    FROM_PUBLIC:   "mdi.puzzle-outline",
    FROM_PLAYER:   "mdi.play-circle-outline",
    FROM_QUICK:    "mdi.lightning-bolt-outline",
    FROM_USER:     "mdi.account-outline",
    FROM_AUDIO:    "mdi.volume-high",
    FROM_PAGE:     "mdi.file-outline",
    FROM_CORE:     "mdi.home-outline",
}

#And what each is called, for the badge.
#The badge, which is the registry rather than the kind of thing that
#registered it. "Plugin" said nothing - every entry here came from a plugin.
REGISTRY_NAMES = {
    FROM_ENDPOINT: "Endpoint",
    FROM_PUBLIC:   "Public",
    FROM_PLAYER:   "Player",
    FROM_QUICK:    "Quick",
    FROM_USER:     "Users",
    FROM_AUDIO:    "Audio",
    FROM_PAGE:     "Page",
    FROM_CORE:     "Panel",
}


#A sentence saying where something lives and how it is reached, for the setup
#dialog to show. The badge and the icon are for scanning a list; this is for
#somebody who has stopped on one and wants to know what they are pointing at.
REGISTRY_ABOUT = {
    FROM_ENDPOINT: "An HTTP endpoint. It can also be reached from a browser "
                   "at this address.",
    FROM_PUBLIC:   "A function a plugin published for other plugins to call. "
                   "It is not reachable over HTTP.",
    FROM_PLAYER:   "A media player control.",
    FROM_QUICK:    "A quick settings action.",
    FROM_USER:     "A user account action.",
    FROM_AUDIO:    "An audio device control.",
    FROM_PAGE:     "A page on this panel.",
    FROM_CORE:     "Built into the panel itself. Reachable from a "
                   "browser at this address, like any endpoint.",
}


def about(kind: str) -> str:
    """One sentence on what kind of thing this is."""
    return REGISTRY_ABOUT.get(kind, "Something registered that can be called.")


def icon_for(kind: str, opens_page: bool = False) -> str:
    """The icon for a registry, or the one that says 'this opens a page'."""
    if opens_page:
        # The exception worth making: an endpoint that answers with a page is
        # a different thing to point a tile at than one that answers with
        # data, and that difference matters more than which registry it is in.
        return "mdi.open-in-app"
    return REGISTRY_ICONS.get(kind, "mdi.function-variant")

#Argument kinds a dialog can build. Deliberately few: these are what a
#keyboard can produce and what JSON can carry.
KINDS = ("text", "number", "boolean", "none", "json")


@dataclass
class Argument:
    """One argument a runnable accepts."""

    name: str
    kind: str = "text"
    #What the function itself would use if this were left out. `None` here
    #means it has no default and the call needs one.
    default: Any = None
    required: bool = False

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind,
                "default": self.default, "required": self.required}


@dataclass
class Runnable:
    """One thing an action tile can be pointed at."""

    key: str                     # unique, and what gets saved on the tile
    label: str                   # what a person reads
    source: str                  # which registry, in words
    kind: str = FROM_ENDPOINT
    owner: str = ""              # the plugin that registered it
    name: str = ""               # endpoint path, or the entry's name
    description: str = ""
    icon: str = "mdi.function-variant"
    badge: str = ""
    #Whether running this does something a person would notice. Endpoints say
    #so themselves; a callable cannot be asked.
    danger: bool = False
    #True when the endpoint answers with a page rather than data - the tile
    #opens it instead of reading a value out of it.
    opens_page: bool = False
    arguments: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "source": self.source,
                "kind": self.kind, "owner": self.owner, "name": self.name,
                "icon": self.icon, "badge": self.badge,
                "danger": self.danger, "opens_page": self.opens_page,
                "arguments": [a.to_dict() for a in self.arguments]}


def _kind_of(default: Any, annotation: Any) -> str:
    """The argument kind to offer, from whatever the function declared."""
    for source in (annotation, type(default) if default is not None else None):
        if source is bool:
            return "boolean"
        if source in (int, float):
            return "number"
        if source in (dict, list, tuple):
            return "json"
    return "text"


def arguments_of(callback: Callable) -> list:
    """
    What a callable accepts, as arguments a dialog can offer.

    Read from the signature rather than asked of the person. Every one of
    these has a name the caller cannot see and would otherwise have to guess,
    and most have a default that says both the type and what happens when it
    is left out.

    `self`, `*args` and `**kwargs` are dropped: the first is bound already,
    and the other two are a promise to accept anything rather than a question
    worth asking.
    """
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return []

    found = []
    for name, parameter in signature.parameters.items():
        if name in ("self", "cls"):
            continue
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            continue
        has_default = parameter.default is not inspect.Parameter.empty
        default = parameter.default if has_default else None
        annotation = (parameter.annotation
                      if parameter.annotation is not inspect.Parameter.empty
                      else None)
        found.append(Argument(
            name=name,
            kind=_kind_of(default, annotation),
            default=default,
            required=not has_default,
        ))
    return found


def endpoints(client: "Client") -> list:
    """Every registered HTTP endpoint, as runnables."""
    registry = getattr(client, "API", None)
    if registry is None:
        return []

    found = []
    try:
        owners = registry.owners()
    except Exception as e:
        client.log("warning", f"[Actions] Could not list endpoints: {e}")
        return []

    for owner in owners:
        try:
            names = registry.endpoints_for(owner)
        except Exception:
            continue
        for name in names:
            try:
                entry = registry.get_endpoint(name)
            except Exception:
                entry = None
            endpoint = entry[1] if isinstance(entry, tuple) else entry
            if endpoint is None:
                continue

            # `_APIEndpoint__callback`, because the attribute is private and
            # Python mangles the name. Asking for "callback" answered None
            # for every endpoint ever registered, so all of them were skipped
            # as unrunnable and the list showed only the public registry.
            callback = getattr(endpoint, "_APIEndpoint__callback", None)
            if callback is None:
                callback = getattr(endpoint, "callback", None)
            if not callable(callback):
                # Nothing to run. A registration without one is a route
                # somebody reserved and never filled in.
                continue

            # `gui` means it answers with a page a person opens; `action`
            # means the point is that it ran. Endpoints say which they are, so
            # the tile does not have to guess from what comes back.
            label = (getattr(endpoint, "gui", "") or
                     getattr(endpoint, "action", "") or name)
            opens = bool(getattr(endpoint, "gui", ""))
            found.append(Runnable(
                key=f"{FROM_ENDPOINT}:{name}",
                label=str(label),
                source=f"{owner} \u00b7 {name}",
                kind=FROM_ENDPOINT,
                owner=owner,
                name=name,
                description=str(getattr(endpoint, "description", "") or ""),
                icon=icon_for(FROM_ENDPOINT, opens),
                badge="Page" if opens else REGISTRY_NAMES[FROM_ENDPOINT],
                danger=bool(getattr(endpoint, "danger", False)),
                opens_page=bool(getattr(endpoint, "gui", "")),
                arguments=arguments_of(callback),
            ))
    return found


def public_entries(client: "Client") -> list:
    """Every callable a plugin exposed publicly, as runnables."""
    registry = getattr(client, "public", None)
    if registry is None:
        return []

    try:
        # `{plugin: [surface, ...]}` - see PublicRegistry.list().
        surfaces = registry.list() or {}
    except Exception as e:
        client.log("warning", f"[Actions] Could not list public entries: {e}")
        return []

    found = []
    for owner, names in surfaces.items():
        for name in names or []:
            found.extend(_entries_of(registry, str(owner), str(name)))
    return found


def _entries_of(registry, owner: str, surface: str) -> list:
    """The callables in one exposed surface."""
    try:
        entries = getattr(registry, surface)
    except Exception:
        return []
    if not hasattr(entries, "keys"):
        # Not a mapping. A plugin may expose anything it likes; only the ones
        # shaped like a set of named things have entries to offer.
        return []

    found = []
    for name in sorted(entries.keys()):
        try:
            value = entries[name]
        except Exception:
            continue
        if not callable(value):
            # A store, a number, a list. The calendar exposes its sticker
            # store this way - useful to a plugin, nothing for a tile to run.
            continue
        found.append(Runnable(
            key=f"{FROM_PUBLIC}:{surface}.{name}",
            label=name.replace("_", " ").strip().capitalize(),
            # Who registered it, then how it is reached. The owner was known
            # and not shown, so a dozen entries from different plugins all
            # read as the surface name alone.
            source=f"{owner} \u00b7 {surface}.{name}",
            kind=FROM_PUBLIC,
            owner=owner,
            name=f"{surface}.{name}",
            icon=icon_for(FROM_PUBLIC),
            badge=REGISTRY_NAMES[FROM_PUBLIC],
            arguments=arguments_of(value),
        ))
    return found


#The panel's own routes, described by hand.
#
#Read from the backend they cannot be: they are Flask views closed over the
#client, so `inspect.signature` sees a function of no arguments while the real
#ones arrive in the query string. Listing them by hand is the only way to say
#what they take, and it is worth doing - `/process` alone makes every skill
#reachable from a tile.
CORE_ROUTES = [
    ("/process", "Ask the assistant", "mdi.microphone-message",
     "Runs a phrase through the assistant, exactly as if it had been heard.",
     [("q", "text", "", True)], False),
    ("/say", "Say something", "mdi.account-voice",
     "Reads a message out on the panel.",
     [("message", "text", "", True), ("from", "text", "", False)], False),
    ("/goto/page", "Go to a page", "mdi.page-next-outline",
     "Switches the panel to a page.",
     [("page", "text", "", True)], False),
    ("/dashboard/state", "What the panel is doing", "mdi.information-outline",
     "Volume, brightness, quiet hours, updates and the current page.",
     [], False),
    ("/backlight", "Screen brightness", "mdi.brightness-6",
     "What is driving the screen, and at what level.",
     [("survey", "text", "", False)], False),
    ("/quick", "Quick settings", "mdi.tune",
     "Every quick setting and its current state.", [], False),
    ("/pages", "Every page", "mdi.file-multiple-outline",
     "What pages are registered.", [], False),
    ("/clipboard", "The clipboard", "mdi.clipboard-outline",
     "Read what is on the panel's clipboard, or put something on it.",
     [("text", "text", "", False)], False),
    ("/ping", "Is it awake", "mdi.access-point",
     "Answers if the panel is running.", [], False),
    ("/update/check", "Check for an update", "mdi.cloud-download-outline",
     "Whether one is waiting, without installing anything.", [], False),
    ("/notify", "Send a notification", "mdi.bell-outline",
     "Puts a notification on the panel.",
     [("title", "text", "", True), ("message", "text", "", False),
      ("icon", "text", "", False)], False),
    ("/quiet/dnd/on", "Do not disturb on", "mdi.bell-off-outline",
     "Holds notifications back.", [], True),
    ("/quiet/dnd/off", "Do not disturb off", "mdi.bell-ring-outline",
     "Lets notifications through again.", [], True),
    ("/restart", "Restart the panel", "mdi.restart",
     "Stops and starts it. Anything unsaved goes with it.", [], True),
]


def core_routes(client: "Client") -> list:
    """The panel's own HTTP routes, as runnables."""
    found = []
    for path, label, glyph, about, arguments, danger in CORE_ROUTES:
        found.append(Runnable(
            key=f"{FROM_CORE}:{path}",
            label=label,
            source=f"the panel \u00b7 {path}",
            kind=FROM_CORE,
            owner="panel",
            name=path,
            description=about,
            icon=glyph,
            badge=REGISTRY_NAMES[FROM_CORE],
            danger=danger,
            arguments=[Argument(name=name, kind=kind, default=default,
                                required=required)
                       for name, kind, default, required in arguments],
        ))
    return found


def everything(client: "Client") -> list:
    """
    Every runnable, sorted by what it is and where it came from.

    Endpoints first: they are the ones with a description and an icon, so a
    list opens on the entries that read as something rather than on a wall of
    function names.
    """
    found = []
    for gather in (core_routes, endpoints, public_entries):
        try:
            found.extend(gather(client))
        except Exception as e:
            client.log("warning", f"[Actions] {gather.__name__} failed: {e}")
    # The panel's own first, then plugin endpoints, then everything else.
    # These are the ones somebody reaches for and the only ones with a
    # description written for a person rather than for a log.
    order = {FROM_CORE: 0, FROM_ENDPOINT: 1}
    return sorted(found, key=lambda r: (order.get(r.kind, 2),
                                        r.source.lower()))


def find(client: "Client", key: str):
    """One runnable by its saved key, or None if it has gone."""
    for runnable in everything(client):
        if runnable.key == key:
            return runnable
    return None
