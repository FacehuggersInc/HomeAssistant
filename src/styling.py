import os
import json
import itertools
from pathlib import Path

from PyQt6.QtGui import QColor, QFont, QLinearGradient, QGradient
from PyQt6.QtCore import Qt

from src.settings import Settings

# ── Colour helpers ──────────────────────────────────────────────────────────

def hsl(hue: int, saturation: int, lightness: int) -> str:
    color = QColor.fromHslF(hue / 360.0, saturation / 100.0, lightness / 100.0)
    return color.name()  # '#rrggbb'

def opacity(opaqueness: float, color: str) -> str:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, opaqueness)))
    return c.name(QColor.NameFormat.HexArgb)  # '#aarrggbb'

def hex_to_qcolor(hex_str: str) -> QColor:
    return QColor(hex_str)

# ── Brand palette ───────────────────────────────────────────────────────────

FONT      = "poppins-light"
FONT_BOLD = "poppins-medium"
SPOTIFY   = "#1dcf5d"

COLORS = Settings({
    "PRIMARY": {
        "LIGHT": hsl(151, 63, 45),
        "DARK":  hsl(151, 62, 15),
    },
    "DARK": {
        "BGDARK":  hsl(0, 0, 5),
        "BG":      hsl(0, 0, 15),
        "BGLIGHT": hsl(0, 0, 20),
        "BORDER": {
            "NORMAL":    hsl(0, 0, 30),
            "HIGHLIGHT": hsl(0, 0, 60),
        },
        "TEXT": {
            "IMPORTANT": hsl(0, 0, 95),
            "MUTED":     hsl(0, 0, 70),
        },
    },
})

# ── Size scale (px) ─────────────────────────────────────────────────────────

SIZES = Settings({
    "S1": 16,
    "S2": 18,
    "S3": 20,
    "M1": 25,
    "M2": 28,
    "M3": 31,
    "L1": 35,
    "L2": 45,
    "L3": 60,
})


def line_height(size: int, bold: bool = False, padding: int = 0) -> int:
    """
    How tall a single line of this font actually needs to be.

    Measured, not guessed. A fixed height picked by eye clips the descenders of
    anything larger than it was chosen for, and the result does not look like a
    layout bug from outside - it looks like the font is wrong. S3 bold needs 31
    pixels; a row built at 28 loses the bottom of every 'g' in it.

    `padding` is added on top for a row that also wants breathing room.
    """
    from PyQt6.QtGui import QFontMetrics
    return QFontMetrics(make_font(size, bold=bold)).height() + int(padding)


def make_font(size: int, bold: bool = False, family: str = FONT) -> QFont:
    f = QFont(family, size)
    f.setBold(bold)
    return f

# Style descriptors — safe to create before QApplication exists
STYLES = Settings({
    "H1": {"size": SIZES.L1,  "bold": True,  "color": COLORS.DARK.TEXT.IMPORTANT},
    "H2": {"size": SIZES.M3,  "bold": True,  "color": COLORS.DARK.TEXT.IMPORTANT},
    "H3": {"size": SIZES.M2,  "bold": True,  "color": COLORS.DARK.TEXT.IMPORTANT},
    "I1": {"size": SIZES.M1,  "bold": True,  "color": COLORS.DARK.TEXT.IMPORTANT},
    "I2": {"size": SIZES.S3,  "bold": True,  "color": COLORS.DARK.TEXT.IMPORTANT},
    "I3": {"size": SIZES.S2,  "bold": False, "color": COLORS.DARK.TEXT.MUTED},
    "I4": {"size": SIZES.S1,  "bold": False, "color": COLORS.DARK.TEXT.MUTED},
})

SETTING_STYLE      = STYLES.I2
SETTING_DESC_STYLE = STYLES.I3


# ── Text shadow helper ────────────────────────────────────────────────────────

def add_text_shadow(widget, blur: int = 4, offset_x: int = 1,
                    offset_y: int = 1, color: str = "#000000") -> None:
    from PyQt6.QtWidgets import QGraphicsDropShadowEffect
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(offset_x, offset_y)
    effect.setColor(QColor(color))
    widget.setGraphicsEffect(effect)


#Beside this module, not below the working directory.
#
#Resolved from cwd, the stylesheets are only found when the application is
#launched from the project root. Started from anywhere else - a service file
#with a different WorkingDirectory, a shell in another folder - every widget
#loses its styling with nothing said, and a lookup that creates what it fails
#to find leaves an empty directory for the next run to succeed against.
STYLES_DIR = Path(__file__).resolve().parent / "assets" / "styles"

def _styles_dir() -> Path:
    """
    Where the stylesheets are. Never created here: a read that makes the thing
    it is looking for turns a wrong path into a silent lack of styling.
    """
    return STYLES_DIR

def load_styles() -> None:
    for file in _styles_dir().glob("*.css"):
        STYLES[file.stem] = get_styles_from_file(file.stem)
    with open(".styles", "w") as dump_file:
        json.dump(STYLES.to_dict(), dump_file, indent=2)

def style_scrollbar(widget) -> None:
    """
    Give a scroll area the shared scrollbar, keeping whatever it already has.

    Appended rather than set. `setStyleSheet` REPLACES, and set_style() uses
    it - so a scroll area given the scrollbar sheet and then styled for
    anything else silently loses the scrollbar again and shows the platform's
    own. Every call site had that ordering wrong at once, which is what a trap
    rather than a mistake looks like.

    Safe to call at any point, and safe to call twice.
    """
    try:
        sheet = get_style_sheet("scrollbar")
        if not sheet.strip():
            return
        existing = widget.styleSheet() or ""
        if "QScrollBar" in existing:
            return
        widget.setStyleSheet((existing + "\n" + sheet).strip())
    except Exception as e:
        print(f"[Styling] Could not style a scrollbar - {e}")


def get_style_sheet(css_file_name: str) -> str:
    stylesheet = _styles_dir() / f"{css_file_name.strip()}.css"
    if stylesheet.exists():
        with open(stylesheet, "r") as file:
            return file.read()
    return ""

def get_styles_from_file(css_file_name: str) -> dict:
    stylesheet = _styles_dir() / f"{css_file_name.strip()}.css"
    found_styles: dict = {}

    if not stylesheet.exists():
        return found_styles

    def _store_declarations(target_key: str, body: str) -> None:
        for declaration in body.split(";"):
            declaration = declaration.strip()
            if not declaration or ":" not in declaration:
                continue   #blank segments and comments are skipped, not crashed on
            prop, value = declaration.split(":", 1)
            found_styles[target_key][prop.strip()] = value.strip()

    with open(stylesheet, "r") as file:
        found_style = False
        in_comment = False
        key = ""
        pending = ""   #declaration text not yet terminated by a ';'
        for line in file.readlines():
            clean = line.strip()

            if in_comment:
                if "*/" not in clean:
                    continue
                clean = clean.split("*/", 1)[1].strip()
                in_comment = False
            while "/*" in clean:
                before, _, after = clean.partition("/*")
                if "*/" in after:
                    clean = f"{before} {after.partition('*/')[2]}".strip()
                else:
                    clean = before.strip()
                    in_comment = True
                    break

            if not clean:
                continue

            #Build Key (selector lists spread across multiple lines)
            if not found_style and clean.endswith(","):
                key += clean
                continue

            if "{" in clean and not found_style:
                head, _, rest = clean.partition("{")
                key += head.strip()
                found_styles[key] = {}
                found_style = True
                pending = ""
                clean = rest

            if found_style:
                # Declarations are terminated by ';', not by end of line. A
                # value wrapped across two lines - a qlineargradient with its
                # stops on the next line is the usual one - used to be stored
                # as a truncated value plus a bogus property invented from the
                # remainder, which Qt then discarded without a word.
                if "}" in clean:
                    body, _, _trailing = clean.partition("}")
                    _store_declarations(key, f"{pending} {body}")
                    pending = ""
                    found_style = False
                    key = ""
                else:
                    pending = f"{pending} {clean}".strip()
                    if ";" in pending:
                        complete, _, remainder = pending.rpartition(";")
                        _store_declarations(key, complete)
                        pending = remainder.strip()

    return found_styles

## -- Selector matching

def _parse_selector_list(raw_selector: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for part in raw_selector.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            base, pseudo_text = part.split(":", 1)
            pseudo = f":{pseudo_text.strip()}"
        else:
            base, pseudo = part, ""
        base = base.strip()
        if base and base[0] in (".", "#"):
            base = base[1:]
        pairs.append((base, pseudo))
    return pairs

def get_style(id: str, clazz: str, object_tag: str = None, override: dict = None) -> str:
    styles: dict = STYLES.get(id)
    if not styles:
        return ""

    matched: list[tuple[str, dict]] = []
    for raw_selector in styles:
        for base, pseudo in _parse_selector_list(raw_selector):
            if base == clazz or (object_tag and base == object_tag):
                matched.append((pseudo, styles[raw_selector]))
                break   #a selector list only needs to match once per block

    style_str = ""
    for pseudo, props in matched:
        final_style = dict(props)

        if override:
            #"*" applies ONLY to the base selector (no pseudo)
            if "*" in override and pseudo == "":
                final_style.update(override["*"])

            #pseudo overrides like ":hover" apply to their matching block only
            for state, values in override.items():
                if state != "*" and state == pseudo:
                    final_style.update(values)

        style_str += f"{object_tag}{pseudo} {{\n"
        for prop, value in final_style.items():
            style_str += f"    {prop}: {value};\n"
        style_str += "}\n"

    return style_str

_anon_style_counter = itertools.count()

def set_style(style_able: object, id: str, clazz: str,
             object_tag: str = None, override: dict = None) -> None:
    try:
        if object_tag:
            tag = object_tag
        else:
            class_name = style_able.__class__.__name__
            if not style_able.objectName():
                style_able.setObjectName(f"_anon_{class_name}_{next(_anon_style_counter)}")
            tag = f"{class_name}#{style_able.objectName()}"
        style_able.setStyleSheet(get_style(id, clazz, tag, override))
    except Exception as e:
        # A print, deliberately.
        #
        # This module is imported by everything and reaches nothing - there is
        # no client here to log through, and importing one would be a cycle.
        # A style that fails to apply looks like a layout bug from outside, so
        # saying so on stdout beats saying nothing.
        print(f"[Styling] Could not style {style_able.__class__.__name__} "
              f"with {id}:{clazz} - {e}")