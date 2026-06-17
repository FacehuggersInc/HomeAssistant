from src.ui.widgets.tile import Tile
from src import *

class MyTile(Tile):
    def __init__(self, client):
        super().__init__(client, key="mytile", grid_w=2, grid_h=2,
                         bg_color="#1a3a5c")
        lbl = QLabel("Hello")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._content_layout.addWidget(lbl)
