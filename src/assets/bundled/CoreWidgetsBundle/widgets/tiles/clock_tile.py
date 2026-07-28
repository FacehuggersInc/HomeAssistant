from __future__ import annotations
from datetime import datetime
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout
from PyQt6.QtCore import Qt

from src.ui.widgets.tile import Tile
from src.styling import make_font, add_text_shadow, set_style

if TYPE_CHECKING:
    from src.main import Client


##CLOCK TILE

class ClockTile(Tile):
    """The time, in as much detail as it has room for."""

    KEY  = "clock_tile"
    NAME = "Clock"
    ICON = "mdi.clock-outline"

    MIN_GRID_W, MIN_GRID_H = 1, 1
    MAX_GRID_W, MAX_GRID_H = 6, 4

    def __init__(self, client: "Client", grid_w: int = 2, grid_h: int = 2):
        super().__init__(client, grid_w=grid_w, grid_h=grid_h, bg_color="#1a1a2e")

    ## -- variants

    def build_variants(self) -> None:
        # Two thresholds cover every size: time only until there is room for a
        # date under it, and a larger face once the tile is genuinely big.
        self.add_variant(1, 1, self._build_time_only)
        self.add_variant(2, 2, self._build_with_date)
        self.add_variant(4, 3, self._build_large)

    def _face(self, time_size: int, date_size: int, with_date: bool,
              with_seconds: bool = False) -> QWidget:
        host = QWidget()
        set_style(host, "common", "transparent")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.time_lbl = QLabel("--:--")
        self.time_lbl.setFont(make_font(time_size, bold=False, family="poppins-light"))
        self.time_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(self.time_lbl, "common", "text-strong")
        add_text_shadow(self.time_lbl, blur=10)
        layout.addWidget(self.time_lbl)

        self.date_lbl = QLabel("---") if with_date else None
        if self.date_lbl is not None:
            self.date_lbl.setFont(make_font(date_size, bold=False, family="poppins-light"))
            self.date_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            set_style(self.date_lbl, "common", "text-muted")
            add_text_shadow(self.date_lbl, blur=6)
            layout.addWidget(self.date_lbl)

        self.show_seconds = with_seconds
        return host

    def _build_time_only(self) -> QWidget:
        return self._face(30, 0, with_date=False)

    def _build_with_date(self) -> QWidget:
        return self._face(42, 13, with_date=True)

    def _build_large(self) -> QWidget:
        return self._face(76, 20, with_date=True, with_seconds=True)

    ## -- data

    def tick(self) -> None:
        now = datetime.now()

        time_format = self.client.SETTINGS.home.time_format.value
        if getattr(self, "show_seconds", False) and "%S" not in time_format:
            # Only where there is room for it - seconds on a one-cell tile are
            # unreadable and redraw the whole face every second for nothing.
            time_format = time_format.replace("%M", "%M:%S", 1)

        self.time_lbl.setText(now.strftime(time_format))
        if self.date_lbl is not None:
            self.date_lbl.setText(now.strftime(
                self.client.SETTINGS.home.date_format.value))
