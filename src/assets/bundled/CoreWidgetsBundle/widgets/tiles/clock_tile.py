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

    # Class-level, not set in __init__. Tile.__init__ calls apply_span(),
    # which calls tick_once() -> tick() before this subclass's own __init__
    # body has run - so an instance attribute assigned below would not exist
    # yet on the very first tick.
    _time_format = "%H:%M"
    _date_format = "%a %d %b"

    def __init__(self, client: "Client", grid_w: int = 2, grid_h: int = 2):
        super().__init__(client, grid_w=grid_w, grid_h=grid_h, bg_color="#1a1a2e")
        # Read once and refreshed on save, rather than two Dynaconf attribute
        # walks on every tick. tick() runs once a second for the life of the
        # app, and also fires on every variant swap during a resize drag.
        self._torn_down = False
        self._read_formats()
        self.client.subscribe_to_event("on_settings_saved", self._on_settings_saved)

    def _read_formats(self) -> None:
        self._time_format = str(self.client.setting("home.clock.time_format.value",
                                                    ClockTile._time_format))
        self._date_format = str(self.client.setting("home.clock.date_format.value",
                                                    ClockTile._date_format))

    def _on_settings_saved(self, event=None) -> None:
        try:
            self._read_formats()
        except RuntimeError:
            pass    # tile deleted between the save and this running

    def teardown(self) -> None:
        # remove_tile() and the page's own teardown can both reach a tile, so
        # this has to be safe to run twice.
        if getattr(self, "_torn_down", False):
            return
        self._torn_down = True
        try:
            self.client.unsubscribe_from_event("on_settings_saved",
                                               self._on_settings_saved)
        except Exception:
            pass

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

        time_format = self._time_format
        if getattr(self, "show_seconds", False) and "%S" not in time_format:
            # Only where there is room for it - seconds on a one-cell tile are
            # unreadable and redraw the whole face every second for nothing.
            time_format = time_format.replace("%M", "%M:%S", 1)

        self.time_lbl.setText(now.strftime(time_format))
        if self.date_lbl is not None:
            self.date_lbl.setText(now.strftime(self._date_format))
