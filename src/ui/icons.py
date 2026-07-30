from __future__ import annotations
from typing import Optional
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtCore import QSize


# ── Registered name → MDI name ────────────────────────────────────────────────

_REGISTRY: dict[str, str] = {
    # Window controls
    "close":           "mdi.close",
    "window-close":    "mdi.close",
    "minimize":        "mdi.minus",
    "maximize":        "mdi.window-maximize",
    "fullscreen":      "mdi.fullscreen",
    "arrows-maximize": "mdi.fullscreen",
    "fullscreen-exit": "mdi.fullscreen-exit",
    "restore":         "mdi.window-restore",

    # Navigation / pages
    "settings":        "mdi.cog",
    "home":            "mdi.home",
    "back":            "mdi.arrow-left",
    "forward":         "mdi.arrow-right",
    "menu":            "mdi.menu",

    # Actions
    "refresh":         "mdi.refresh",
    "reload":          "mdi.reload",
    "add":             "mdi.plus",
    "remove":          "mdi.minus",
    "delete":          "mdi.delete",
    "edit":            "mdi.pencil",
    "save":            "mdi.content-save",
    "download":        "mdi.download",
    "upload":          "mdi.upload",
    "search":          "mdi.magnify",
    "filter":          "mdi.filter",
    "sort":            "mdi.sort",
    "copy":            "mdi.content-copy",
    "share":           "mdi.share",
    "open":            "mdi.open-in-new",

    # Status / alerts
    "check":           "mdi.check",
    "info":            "mdi.information",
    "warning":         "mdi.alert",
    "error":           "mdi.alert-circle",
    "success":         "mdi.check-circle",

    # Media
    "play":            "mdi.play",
    "pause":           "mdi.pause",
    "stop":            "mdi.stop",
    "skip-next":       "mdi.skip-next",
    "skip-previous":   "mdi.skip-previous",
    "shuffle":         "mdi.shuffle",
    "repeat":          "mdi.repeat",
    "volume-up":       "mdi.volume-high",
    "volume-down":     "mdi.volume-medium",
    "volume-mute":     "mdi.volume-off",

    # Notifications / communication
    "bell":            "mdi.bell",
    "bell-off":        "mdi.bell-off",
    "notification":    "mdi.bell",
    "message":         "mdi.message",
    "email":           "mdi.email",

    # Files / content
    "folder":          "mdi.folder",
    "file":            "mdi.file",
    "image":           "mdi.image",

    # Misc
    "pin":             "mdi.pin",
    "push-pin":        "mdi.pin",
    "unpin":           "mdi.pin-off",
    "star":            "mdi.star",
    "heart":           "mdi.heart",
    "tag":             "mdi.tag",
    "link":            "mdi.link",
    "lock":            "mdi.lock",
    "unlock":          "mdi.lock-open",
    "eye":             "mdi.eye",
    "eye-off":         "mdi.eye-off",
    "extension":       "mdi.puzzle",
    "plugin":          "mdi.puzzle",
    "assistant":       "mdi.microphone",
    "microphone":      "mdi.microphone",
    "microphone-off":  "mdi.microphone-off",
    "weather":         "mdi.weather-partly-cloudy",
    "calendar":        "mdi.calendar",
    "clock":           "mdi.clock",
    "timer":           "mdi.timer",
    "wifi":            "mdi.wifi",
    "bluetooth":       "mdi.bluetooth",
    "power":           "mdi.power",
    "brightness":      "mdi.brightness-6",
    "palette":         "mdi.palette",
    "code":            "mdi.code-tags",
    "terminal":        "mdi.console",
}


# ── Constants class for IDE autocomplete ──────────────────────────────────────

class Icons:
    CLOSE           = "close"
    MINIMIZE        = "minimize"
    MAXIMIZE        = "maximize"
    FULLSCREEN      = "fullscreen"
    FULLSCREEN_EXIT = "fullscreen-exit"
    RESTORE         = "restore"
    SETTINGS        = "settings"
    HOME            = "home"
    BACK            = "back"
    FORWARD         = "forward"
    MENU            = "menu"
    REFRESH         = "refresh"
    RELOAD          = "reload"
    ADD             = "add"
    REMOVE          = "remove"
    DELETE          = "delete"
    EDIT            = "edit"
    SAVE            = "save"
    DOWNLOAD        = "download"
    UPLOAD          = "upload"
    SEARCH          = "search"
    FILTER          = "filter"
    COPY            = "copy"
    SHARE           = "share"
    CHECK           = "check"
    INFO            = "info"
    WARNING         = "warning"
    ERROR           = "error"
    SUCCESS         = "success"
    PLAY            = "play"
    PAUSE           = "pause"
    STOP            = "stop"
    SKIP_NEXT       = "skip-next"
    SKIP_PREVIOUS   = "skip-previous"
    RESTART         = "restart"
    PLAY_PAUSE      = "play-pause"
    CHEVRON_DOWN        = "chevron-down"
    CHEVRON_RIGHT       = "chevron-right"
    HEADPHONES          = "headphones"
    CELLPHONE           = "cellphone"
    PLUS_CIRCLE         = "plus-circle"
    LOCK_OPEN           = "lock-open-variant"
    MAGNIFY             = "magnify"
    LINK_OFF            = "link-off"
    DELETE_OUTLINE      = "delete-outline"
    ACCOUNT_REMOVE      = "account-remove"
    PENCIL              = "pencil"
    SAVE_CONTENT        = "content-save"
    ARROW_LEFT          = "arrow-left"
    BATTERY             = "battery-70"
    BATTERY_ALERT       = "battery-alert"
    EARTH               = "earth"
    KEY                 = "key-variant"
    CHECK_CIRCLE        = "check-circle"
    INFO_OUTLINE        = "information-outline"
    PUZZLE              = "puzzle"
    TUNE                = "tune"
    ACCOUNT_MULTIPLE    = "account-multiple"
    OPEN_IN_NEW     = "open-in-new"
    ALARM_SNOOZE    = "alarm-snooze"
    SIGNAL          = "signal"
    WIDGETS         = "widgets"
    WIFI_OFF            = "wifi-off"
    WIFI_1              = "wifi-strength-1"
    WIFI_2              = "wifi-strength-2"
    WIFI_3              = "wifi-strength-3"
    WIFI_4              = "wifi-strength-4"
    BLUETOOTH_OFF       = "bluetooth-off"
    BLUETOOTH_CONNECTED = "bluetooth-connect"
    SHUFFLE         = "shuffle"
    REPEAT          = "repeat"
    VOLUME_UP       = "volume-up"
    VOLUME_DOWN     = "volume-down"
    VOLUME_MUTE     = "volume-mute"
    BELL            = "bell"
    BELL_OFF        = "bell-off"
    NOTIFICATION    = "notification"
    MESSAGE         = "message"
    EMAIL           = "email"
    FOLDER          = "folder"
    FILE            = "file"
    IMAGE           = "image"
    PIN             = "pin"
    UNPIN           = "unpin"
    STAR            = "star"
    HEART           = "heart"
    TAG             = "tag"
    LINK            = "link"
    LOCK            = "lock"
    UNLOCK          = "unlock"
    EYE             = "eye"
    EYE_OFF         = "eye-off"
    EXTENSION       = "extension"
    PLUGIN          = "plugin"
    ASSISTANT       = "assistant"
    MICROPHONE      = "microphone"
    MICROPHONE_OFF  = "microphone-off"
    WEATHER         = "weather"
    CALENDAR        = "calendar"
    CLOCK           = "clock"
    TIMER           = "timer"
    WIFI            = "wifi"
    BLUETOOTH       = "bluetooth"
    POWER           = "power"
    BRIGHTNESS      = "brightness"
    PALETTE         = "palette"
    CODE            = "code"
    TERMINAL        = "terminal"


# ── Public API ─────────────────────────────────────────────────────────────────

#Every prefix qtawesome ships. A name carrying one of these is passed through
#as written rather than being treated as an unregistered alias.
_FONT_PREFIXES = ("mdi.", "mdi6.", "fa.", "fa5.", "fa5s.", "fa5b.", "fa5r.",
                  "fa6.", "fa6s.", "fa6b.", "fa6r.", "ei.", "msc.", "ph.",
                  "ri.")


def candidates(name: str) -> list:
    """
    Every font name worth trying for `name`, best first.

    Three things used to fall through to the question mark:

    * a bare name that is a perfectly good icon - `robot`, `calendar-month`.
      resolve() only accepted registered aliases or an explicit `mdi.` prefix,
      so a plugin.toml saying `icon = "robot"` got the fallback.
    * an `mdi.` name that only exists in Material Design Icons **6**.
      qtawesome's `mdi` prefix is 5.9.55; `timer-plus-outline` and a few
      hundred others arrived in 6.x, and asking `mdi.` for one raises.
    * an `mdi6.` name, which did not start with "mdi." and so resolved to
      nothing at all.
    """
    name = (name or "").strip()
    if not name:
        return []

    out = []

    def add(value):
        if value and value not in out:
            out.append(value)

    if name in _REGISTRY:
        add(_REGISTRY[name])

    if name.startswith(_FONT_PREFIXES):
        add(name)
        # An mdi. name that is 6-only still works if asked for by its right
        # prefix, so try that before giving up on it.
        if name.startswith("mdi."):
            add("mdi6." + name[4:])
    else:
        add(f"mdi.{name}")
        add(f"mdi6.{name}")

    return out


def resolve(name: str) -> str:
    """The first candidate, for callers that only want a name."""
    found = candidates(name)
    return found[0] if found else None


def icon(
    name: str,
    color:        str   = "white",
    color_active: str   = None,
    scale_factor: float = 1.0,
    size:         int   = None,
) -> QIcon:
    import qtawesome as qta

    options: dict = {"color": color, "scale_factor": scale_factor}
    if color_active:
        options["color_active"] = color_active

    for candidate in candidates(name):
        try:
            return qta.icon(candidate, **options)
        except Exception:
            continue    # wrong font set, or no such glyph - try the next

    try:
        return qta.icon("mdi.help-circle", color=color)
    except Exception:
        return QIcon()


def register(name: str, mdi_name: str) -> None:
    _REGISTRY[name] = mdi_name


_IMAGE_SUFFIXES = (".png", ".svg", ".jpg", ".jpeg", ".webp", ".ico", ".bmp", ".gif")


def is_icon_path(value: str) -> bool:
    if not value:
        return False
    if "/" in value or "\\" in value:
        return True
    return Path(value).suffix.lower() in _IMAGE_SUFFIXES


def resolve_plugin_icon(value: str, color: str = "white", size: int = None) -> Optional[QIcon]:
    if not value:
        return None

    if is_icon_path(value):
        path = Path(value)
        if not path.exists():
            return None
        return QIcon(str(path))

    return icon(value, color=color, size=size)