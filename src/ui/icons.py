"""
Icon registry — wraps qtawesome (Material Design Icons).

Usage
-----
    from src.ui.icons import icon, Icons

    # By registered name
    btn = IconButton(Icons.CLOSE, func)
    btn = IconButton(Icons.SETTINGS, func)

    # Direct qtawesome name (mdi.*)
    btn = IconButton("mdi.alarm", func)

    # Get a QIcon directly
    q_icon = icon(Icons.BELL, color="white", size=32)

Registered names are short strings that map to MDI icon names.
Any unrecognised string starting with "mdi." is passed straight
through to qtawesome, so plugins can use the full MDI catalogue
without registering.

Full MDI catalogue: https://materialdesignicons.com
"""

from __future__ import annotations
from typing import Optional

from PyQt6.QtGui import QIcon
from PyQt6.QtCore import QSize


# ── Registered name → MDI name ────────────────────────────────────────────────

_REGISTRY: dict[str, str] = {
    # Window controls
    "close":           "mdi.close",
    "window-close":    "mdi.close",
    "minimize":        "mdi.minus",
    "maximize":        "mdi.maximize",
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
    """
    Named constants for all registered icon names.
    Use these instead of bare strings for IDE autocomplete and refactoring.

        from src.ui.icons import Icons
        btn = IconButton(Icons.CLOSE, func)
    """
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

def resolve(name: str) -> str:
    """
    Resolve a registered name or raw mdi.* name to an MDI icon name.
    Returns the MDI name string, or None if unresolvable.
    """
    if name in _REGISTRY:
        return _REGISTRY[name]
    if name.startswith("mdi."):
        return name
    return None


def icon(
    name: str,
    color:        str   = "white",
    color_active: str   = None,
    scale_factor: float = 1.0,
    size:         int   = None,
) -> QIcon:
    """
    Return a QIcon for the given registered name or mdi.* name.

    Parameters
    ----------
    name         : registered name (e.g. Icons.CLOSE) or raw mdi.* name
    color        : icon colour, any CSS colour string
    color_active : colour when button is active/checked (optional)
    scale_factor : scale within the button (0.5–1.5 typical)
    size         : if given, returns icon pre-rendered at this pixel size

    Falls back to a generic question-mark icon if the name is not found.
    """
    import qtawesome as qta

    mdi_name = resolve(name)
    if mdi_name is None:
        mdi_name = "mdi.help-circle"

    options: dict = {"color": color, "scale_factor": scale_factor}
    if color_active:
        options["color_active"] = color_active

    try:
        q_icon = qta.icon(mdi_name, **options)
    except Exception:
        try:
            q_icon = qta.icon("mdi.help-circle", color=color)
        except Exception:
            q_icon = QIcon()

    return q_icon


def register(name: str, mdi_name: str) -> None:
    """
    Register a custom icon name → MDI name mapping.
    Call this from a plugin's load() to add plugin-specific icons.

        from src.ui.icons import register
        register("my-plugin-icon", "mdi.rocket")
    """
    _REGISTRY[name] = mdi_name