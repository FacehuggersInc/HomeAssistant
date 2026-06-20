from __future__ import annotations
from datetime import datetime
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QLabel, QVBoxLayout
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics

from src.ui.widgets.tile import Tile
from src.styling import make_font, add_text_shadow, set_style

if TYPE_CHECKING:
    from src.main import Client


##CLOCK TILE

class ClockTile(Tile):
    """
    Example tile. Displays the current time and date.
    Starts in the tile panel — drag it onto the grid to place it.
    """

    KEY  = "clock_tile"
    NAME = "Clock"
    ICON = "mdi.clock-outline"

    def __init__(self, client: "Client"):
        super().__init__(client, grid_w=2, grid_h=2, bg_color="#1a1a2e")

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.time_lbl = QLabel("--:--")
        self.time_lbl.setFont(make_font(42, bold=False, family="poppins-light"))
        set_style(self.time_lbl, "common", "text-strong")
        self.time_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        add_text_shadow(self.time_lbl, blur=10)

        self.date_lbl = QLabel("---")
        self.date_lbl.setFont(make_font(13, bold=False, family="poppins-light"))
        set_style(self.date_lbl, "common", "text-muted")
        self.date_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        add_text_shadow(self.date_lbl, blur=6)

        layout.addWidget(self.time_lbl)
        layout.addWidget(self.date_lbl)
        self.content_layout.addLayout(layout)

    def tick(self) -> None:
        now = datetime.now()
        self.time_lbl.setText(now.strftime(
            self.client.SETTINGS.home.time_format.value
        ))
        self.date_lbl.setText(now.strftime(
            self.client.SETTINGS.home.date_format.value
        ))