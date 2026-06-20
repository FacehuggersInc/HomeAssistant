from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy
from PyQt6.QtCore import Qt, QTimer, QPoint, QRect

from src.styling import set_style

if TYPE_CHECKING:
    from src.main import Client


# ── Anchor constants ─────────────────────────────────────────────────────────

ANCHORS = (
    "top-left",
    "top-center",
    "top-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
)

TOPMOST  = "topmost"
FLOATING = "floating"   # set widget.floating = True, widget.float_x / float_y


# ── Base Widget ───────────────────────────────────────────────────────────────

class Widget(QWidget):
    """
    Base class for all home-screen widgets.

    Placement is declared via `anchor`:
      - "top-left", "top-center", "top-right"
      - "bottom-left", "bottom-center", "bottom-right"
      - "top-left:1"  →  second row in the top-left anchor zone
      - "topmost"     →  always raised above other anchored widgets
      - "floating"    →  absolute position; set float_x / float_y

    Periodic updates are driven by `start_tick(interval_ms)` which fires
    `tick()` on the main thread via QTimer — no background thread needed.
    """

    def __init__(
        self,
        client: "Client",
        key: str,
        anchor: str = "bottom-left",
        width: int | None = None,
        height: int | None = None,
        floating: bool = False,
        float_x: int = 0,
        float_y: int = 0,
    ):
        super().__init__()
        self.KEY      = key
        self.client   = client
        self.anchor   = anchor
        self.floating = floating
        self.float_x  = float_x
        self.float_y  = float_y
        self.tags: list[str] = []

        set_style(self, "common", "transparent")

        if width  is not None: self.setFixedWidth(width)
        if height is not None: self.setFixedHeight(height)

        # Tick timer — off by default; call start_tick() to enable
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._safe_tick)

    # ── Tick API ──────────────────────────────────────────────────────────────

    def start_tick(self, interval_ms: int = 1000) -> None:
        """Start periodic tick() calls on the main thread."""
        self._tick_timer.start(interval_ms)

    def stop_tick(self) -> None:
        """Stop periodic ticks."""
        self._tick_timer.stop()

    def _safe_tick(self) -> None:
        try:
            self.tick()
        except Exception:
            pass

    def tick(self) -> None:
        """Override to implement periodic widget updates."""
        pass


# ── Anchor zone ───────────────────────────────────────────────────────────────

class _AnchorZone(QWidget):
    """
    A transparent container pinned to one corner of the WidgetLayer.

    Internally it holds a QVBoxLayout (rows stacked top→bottom or bottom→top).
    Each row index maps to a QHBoxLayout inside the column.
    """

    def __init__(self, anchor_name: str, padding: int, widget_spacing: int):
        super().__init__()
        self.anchor_name    = anchor_name
        self.padding        = padding
        self.widget_spacing = widget_spacing

        set_style(self, "common", "transparent")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        # Outer column — top anchors grow downward, bottom anchors grow upward
        self._col = QVBoxLayout()
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(8)
        self._col.setSizeConstraint(self._col.SizeConstraint.SetFixedSize)
        if "top" in anchor_name:
            self._col.setAlignment(Qt.AlignmentFlag.AlignTop)
        else:
            self._col.setAlignment(Qt.AlignmentFlag.AlignBottom)

        self.setLayout(self._col)
        self._rows: dict[int, QHBoxLayout] = {}

    def add_widget(self, widget: Widget, row_index: int) -> None:
        """Insert widget into the given row, creating the row if needed."""
        if row_index not in self._rows:
            row_widget = QWidget()
            set_style(row_widget, "common", "transparent")
            # Fixed vertical size so row doesn't expand beyond its content
            row_widget.setSizePolicy(
                QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Fixed,
            )

            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(self.widget_spacing)

            if "right" in self.anchor_name:
                row_layout.setAlignment(Qt.AlignmentFlag.AlignRight)
            elif "center" in self.anchor_name:
                row_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            else:
                row_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

            insert_at = min(row_index, self._col.count())
            self._col.insertWidget(insert_at, row_widget)
            self._rows[row_index] = row_layout

        self._rows[row_index].addWidget(widget)
        self.adjustSize()

    def remove_widget(self, widget: Widget) -> None:
        widget.setParent(None)  # type: ignore[arg-type]
        self.adjustSize()


# ── Widget framework ──────────────────────────────────────────────────────────

class WidgetFramework(QWidget):
    """
    Transparent overlay that sits above page content and manages widget
    placement into named anchor zones.

    Anchor zone layout:

        top-left    top-center    top-right
        [padding]
        ...content...
        [padding]
        bottom-left bottom-center bottom-right

    Each zone is a QWidget pinned to its corner.  Zones are created
    lazily the first time a widget claims them.

    Floating widgets (widget.floating = True) are placed at absolute
    coordinates via setGeometry() and raised to the top.
    """

    def __init__(self, client: "Client", page_key: str,
                 padding: int = 35, widget_spacing: int = 5):
        super().__init__()
        self.client         = client
        self.page_key       = page_key
        self.padding        = padding
        self.widget_spacing = widget_spacing

        set_style(self, "common", "transparent")

        self._zones:   dict[str, _AnchorZone] = {}
        self._widgets: list[Widget]            = []
        self._topmost: list[Widget]            = []

    # ── Public API ────────────────────────────────────────────────────────────

    def add(self, widgets: list[Widget]) -> None:
        """Register and place a list of widgets."""
        for widget in widgets:
            if any(w.KEY == widget.KEY for w in self._widgets):
                raise KeyError(f"Widget key '{widget.KEY}' already exists.")

            self._widgets.append(widget)
            widget.setParent(self)

            if widget.floating or widget.anchor == FLOATING:
                widget.tags.append("floating")
                widget.move(widget.float_x, widget.float_y)
                widget.show()

            elif widget.anchor == TOPMOST:
                widget.tags.append("topmost")
                self._topmost.append(widget)
                widget.show()

            else:
                widget.tags.append("anchored")
                self._place_in_zone(widget)
                widget.show()

        # Raise all topmost widgets above everything else
        for w in self._topmost:
            w.raise_()

    def remove(self, key: str) -> None:
        """Remove a widget by key."""
        found = [w for w in self._widgets if w.KEY == key]
        if not found:
            return
        widget = found[0]
        widget.stop_tick()

        if "anchored" in widget.tags:
            anchor_name, _ = self._parse_anchor(widget.anchor)
            zone = self._zones.get(anchor_name)
            if zone:
                zone.remove_widget(widget)
        else:
            widget.setParent(None)  # type: ignore[arg-type]

        self._widgets.remove(widget)
        if widget in self._topmost:
            self._topmost.remove(widget)

    def tick_widgets(self) -> None:
        """Called by the page's tick loop — no-op since each widget uses QTimer."""
        pass  # widgets manage their own QTimers

    def update_geometry(self) -> None:
        """
        Called when the parent page is resized.
        Repositions all anchor zones and floating widgets.
        """
        self.setGeometry(0, 0, self.parent().width(), self.parent().height())
        self._reposition_zones()
        self._reposition_floating()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _parse_anchor(self, anchor: str) -> tuple[str, int]:
        """Split 'top-left:1' → ('top-left', 1)."""
        if ":" in anchor:
            name, idx = anchor.split(":", 1)
            return name, int(idx)
        return anchor, 0

    def _get_or_create_zone(self, anchor_name: str) -> _AnchorZone:
        if anchor_name not in self._zones:
            zone = _AnchorZone(anchor_name, self.padding, self.widget_spacing)
            zone.setParent(self)
            zone.show()
            self._zones[anchor_name] = zone
            self._reposition_zone(anchor_name, zone)
        return self._zones[anchor_name]

    def _place_in_zone(self, widget: Widget) -> None:
        anchor_name, row_index = self._parse_anchor(widget.anchor)

        # Conflict check — warn if two widgets share the same zone:row
        for existing in self._widgets:
            if existing.KEY == widget.KEY:
                continue
            if "anchored" not in existing.tags:
                continue
            ex_name, ex_row = self._parse_anchor(existing.anchor)
            if ex_name == anchor_name and ex_row == row_index:
                import warnings
                warnings.warn(
                    f"Widget '{widget.KEY}' shares anchor '{anchor_name}:{row_index}' "
                    f"with '{existing.KEY}'. Both will appear in the same row.",
                    stacklevel=3,
                )
                break

        zone = self._get_or_create_zone(anchor_name)
        zone.add_widget(widget, row_index)
        zone.adjustSize()
        self._reposition_zone(anchor_name, zone)

    def _reposition_zones(self) -> None:
        for anchor_name, zone in self._zones.items():
            self._reposition_zone(anchor_name, zone)

    def _reposition_zone(self, anchor_name: str, zone: _AnchorZone) -> None:
        """Pin a zone widget to its corner using absolute positioning."""
        if not self.parent():
            return
        w = self.width()
        h = self.height()
        p = self.padding
        zw = zone.sizeHint().width()
        zh = zone.sizeHint().height()

        if   anchor_name == "top-left":
            zone.move(p, p)
        elif anchor_name == "top-center":
            zone.move((w - zw) // 2, p)
        elif anchor_name == "top-right":
            zone.move(w - zw - p, p)
        elif anchor_name == "bottom-left":
            zone.move(p, h - zh - p)
        elif anchor_name == "bottom-center":
            zone.move((w - zw) // 2, h - zh - p)
        elif anchor_name == "bottom-right":
            zone.move(w - zw - p, h - zh - p)

        zone.raise_()

    def _reposition_floating(self) -> None:
        for widget in self._widgets:
            if "floating" in widget.tags:
                widget.move(widget.float_x, widget.float_y)

    def resizeEvent(self, event: "QResizeEvent") -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._reposition_zones()
        self._reposition_floating()
        for w in self._topmost:
            w.raise_()