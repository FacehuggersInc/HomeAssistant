from PyQt6.QtGui import QColor, QFont, QLinearGradient, QGradient
from PyQt6.QtCore import Qt

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
# make_font() and apply_label_style().

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

# ── Qt stylesheet helpers ────────────────────────────────────────────────────

def qss_color(hex_color: str) -> str:
    """Ensure the color is safe to embed in a QSS string."""
    c = QColor(hex_color)
    return c.name()

def make_background_qss(bg: str, border_color: str = "transparent",
                         border_radius: int = 0, border_width: int = 0) -> str:
    return (
        f"background-color: {qss_color(bg)}; "
        f"border: {border_width}px solid {qss_color(border_color)}; "
        f"border-radius: {border_radius}px;"
    )

def make_gradient_qss(color_top_left: str, color_bottom_right: str,
                       border_radius: int = 0) -> str:
    return (
        f"background: qlineargradient("
        f"x1:0, y1:0, x2:1, y2:1, "
        f"stop:0 {qss_color(color_top_left)}, "
        f"stop:1 {qss_color(color_bottom_right)});"
        f"border-radius: {border_radius}px;"
    )

# Theme gradient as a QSS string (used by widgets / pages that set stylesheets)
THEME_GRADIENT_QSS = make_gradient_qss(
    COLORS.DARK.BGDARK,
    COLORS.DARK.BG,
    border_radius=0,
)

def apply_label_style(label, style_key: str) -> None:
    """Apply a STYLES entry to a QLabel."""
    from PyQt6.QtWidgets import QLabel as _QL
    style = STYLES[style_key]
    label.setFont(make_font(style["size"], style.get("bold", False)))
    label.setStyleSheet(f"color: {style['color']}; background: transparent;")


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