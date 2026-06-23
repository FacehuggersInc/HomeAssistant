import itertools

from PyQt6.QtGui import QColor, QFont, QLinearGradient, QGradient
from PyQt6.QtCore import Qt

from src import *
from src.settings import Settings

# ── Colour helpers ──────────────────────────────────────────────────────────

def hsl(hue: int, saturation: int, lightness: int) -> str:
    """Return a CSS hex string from HSL values."""
    color = QColor.fromHslF(hue / 360.0, saturation / 100.0, lightness / 100.0)
    return color.name()  # '#rrggbb'

def opacity(opaqueness: float, color: str) -> str:
    """Return a hex colour string with alpha baked in (ARGB hex)."""
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, opaqueness)))
    return c.name(QColor.NameFormat.HexArgb)  # '#aarrggbb'

def hex_to_qcolor(hex_str: str) -> QColor:
    """Convert any hex string (with or without alpha prefix) to QColor."""
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

# ── Text style helpers ───────────────────────────────────────────────────────
# Rather than storing Qt QFont objects directly (which require QApplication to
# exist first), STYLES stores plain dicts that UI code converts on demand via
# make_font().

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
    """
    Apply a drop shadow effect to any QWidget (typically a QLabel).
    This simulates text-shadow since Qt stylesheets don't support it.

    Example
    -------
        from src.styling import add_text_shadow
        add_text_shadow(my_label, blur=6, color="#000000")
    """
    from PyQt6.QtWidgets import QGraphicsDropShadowEffect
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(offset_x, offset_y)
    effect.setColor(QColor(color))
    widget.setGraphicsEffect(effect)

## CSS LOADING & SETTING
## -- This is a tiny class-substitution system so widgets can pull their
## -- QSS from a .css file instead of a hardcoded Python string.
## --
## -- A CSS file uses plain placeholder selectors (".some-class", with
## -- an optional ":pseudo" state such as ":hover"). load_styles() parses
## -- every file once into STYLES; get_style()/set_style() then look a
## -- class up by name and swap its selector for the CALLING widget's
## -- real Qt selector (its class name, or "QWidget#object_name" when
## -- child widgets must be excluded from the rule), carrying over any
## -- :pseudo state. For literal Qt selectors this substitution can't
## -- represent — descendant selectors, "::sub-control" pseudo-elements,
## -- scrollbar rules — use get_style_sheet() instead, which returns a
## -- CSS file's contents completely unmodified.

STYLES_DIR = Path("src") / "assets" / "styles"

def _styles_dir() -> Path:
    """Resolve (creating if missing) the directory CSS class files live in."""
    directory = Path(os.getcwd()) / STYLES_DIR
    if not directory.exists(): directory.mkdir(parents=True, exist_ok=True)
    return directory

def load_styles() -> None:
    """Parse every *.css file under STYLES_DIR into STYLES[file_stem]."""
    for file in _styles_dir().glob("*.css"):
        STYLES[file.stem] = get_styles_from_file(file.stem)
    with open(".styles", "w") as dump_file:
        json.dump(STYLES.to_dict(), dump_file, indent=2)

def get_style_sheet(css_file_name: str) -> str:
    """Return a CSS file's raw contents, unmodified."""
    stylesheet = _styles_dir() / f"{css_file_name.strip()}.css"
    if stylesheet.exists():
        with open(stylesheet, "r") as file:
            return file.read()
    return ""

def get_styles_from_file(css_file_name: str) -> dict:
    """
    Parse a CSS file into {raw_selector_text: {prop: value}}.
    Selectors are kept as their raw text (e.g. ".btn-primary:hover" or a
    comma-joined multi-selector list) — see _parse_selector_list() for
    how that text later gets matched and replaced.
    """
    stylesheet = _styles_dir() / f"{css_file_name.strip()}.css"
    found_styles: dict = {}

    if not stylesheet.exists():
        return found_styles

    def _store_declarations(target_key: str, body: str) -> None:
        """Split a block's body text on ';' and store each prop:value pair."""
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
        for line in file.readlines():
            clean = line.strip()

            #Strip /* ... */ comments first — both single-line and the
            #continuation of a block comment opened on an earlier line —
            #so stray punctuation in prose (a comma, a brace) can never
            #be mistaken for real selector/block syntax
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

            #Start Capturing — text up to '{' joins the selector key;
            #anything AFTER '{' on the same line is block body content,
            #which falls through to Finalize Block below
            if "{" in clean and not found_style:
                head, _, rest = clean.partition("{")
                key += head.strip()
                found_styles[key] = {}
                found_style = True
                clean = rest

            #Finalize Block — handles both a closing '}' on its own line
            #and a condensed one-liner like ".cls { color: red; }"
            if found_style:
                if "}" in clean:
                    body, _, _trailing = clean.partition("}")
                    _store_declarations(key, body)
                    found_style = False
                    key = ""
                else:
                    _store_declarations(key, clean)

    return found_styles

## -- Selector matching

def _parse_selector_list(raw_selector: str) -> list[tuple[str, str]]:
    """
    Split a raw, possibly comma-separated selector into (base, pseudo)
    pairs, e.g. ".btn,.btn-alt:hover" -> [("btn", ""), ("btn-alt", ":hover")].
    A leading '.' or '#' is stripped since matching only cares about the
    plain class name, not which symbol authored it.
    """
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
    """
    Render every block in stylesheet `id` whose selector matches `clazz`
    (or, failing that, `object_tag` directly), swapping the matched
    selector's text for `object_tag` so the result actually styles the
    calling widget. Any :pseudo state on a matched selector carries
    over to the rendered output.

    `override` patches specific properties at call time without
    touching the CSS file:
      - override["*"]      -> merged into the base (no-pseudo) block
      - override[":hover"] -> merged into the matching :hover block
    Only states that already exist as a block in the CSS file can be
    overridden — override patches an existing block, it can't invent one.
    """
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
    """Resolve a CSS class via get_style() and apply it straight onto a widget."""
    try:
        if object_tag:
            tag = object_tag
        else:
            # IMPORTANT: never fall back to a bare type name like "QFrame"
            # or "QLabel" here (that used to be the default — class_name
            # with no ID). Qt stylesheets cascade to descendants, so a
            # bare-type selector set on one widget leaks every property it
            # doesn't itself override onto every OTHER same-typed widget
            # nested anywhere underneath it — borders, backgrounds, and
            # radii showing up on things that never asked for them, purely
            # because they happen to be the same Qt class as something
            # higher up the tree that got styled. Auto-assigning a unique
            # objectName and using an ID-qualified selector (Type#name)
            # keeps every call scoped to just the one widget it's actually
            # for, the same as if the caller had passed object_tag itself.
            class_name = style_able.__class__.__name__
            if not style_able.objectName():
                style_able.setObjectName(f"_anon_{class_name}_{next(_anon_style_counter)}")
            tag = f"{class_name}#{style_able.objectName()}"
        style_able.setStyleSheet(get_style(id, clazz, tag, override))
    except Exception as e:
        print(f"Failed to set style on '{style_able.__class__.__name__}' with {id}:{clazz} ? {e}")