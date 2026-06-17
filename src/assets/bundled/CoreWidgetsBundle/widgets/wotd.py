from __future__ import annotations
import time
from pathlib import Path
from datetime import datetime
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QVBoxLayout, QLabel
from PyQt6.QtCore import Qt

from src.ui.widget import Widget
from src.styling import make_font, add_text_shadow

if TYPE_CHECKING:
    from src.main import Client


class WordOfTheDayWidget(Widget):
    """
    Displays the Wordnik Word of the Day.
    Cached to disk and refreshed once per day.
    """

    def __init__(self, client: "Client"):
        super().__init__(
            client = client,
            key    = "wotdwidget",
            anchor = "top-left:1",
            width  = 450,
            height = 95,
        )

        self._check_interval = 60 * 60  # check every hour whether date changed
        self._next_check     = time.time() + self._check_interval
        self._wotd_path      = Path(client.DATAPATH) / "wotd.json"
        self._data: dict     = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        shadow = ""

        self._word_lbl = QLabel("", self)
        self._word_lbl.setFont(make_font(32, bold=True, family="poppins-medium"))
        self._word_lbl.setStyleSheet(
            f"color: white; background: transparent; letter-spacing: 2px; "
        )

        self._source_lbl = QLabel("", self)
        self._source_lbl.setFont(make_font(20, bold=False, family="poppins-light"))
        self._source_lbl.setStyleSheet(
            f"color: white; background: transparent; letter-spacing: 2px; "
        )

        add_text_shadow(self._word_lbl, blur=8)
        add_text_shadow(self._source_lbl, blur=8)
        layout.addWidget(self._word_lbl)
        layout.addWidget(self._source_lbl)

        self._load_or_fetch()
        self.start_tick(interval_ms=60_000)  # check once a minute

    # ── Data helpers ──────────────────────────────────────────────────────────

    def _today(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _date_from_iso(self, iso: str) -> str:
        return iso.split("T")[0].strip()

    def _load_or_fetch(self) -> None:
        if self._wotd_path.exists():
            try:
                data = self.client.load(self._wotd_path)
                if data and self._date_from_iso(data.get("publishDate", "")) == self._today():
                    self._data = data
                    self._update_display()
                    return
            except Exception:
                pass
        self._fetch()

    def _fetch(self) -> None:
        try:
            data = self.client.API["wordnik"].get_wotd()
            if data:
                self._data = data
                self.client.dump(data, self._wotd_path)
                self._update_display()
        except Exception as e:
            self.client.log("warning", f"[WordOfTheDayWidget] fetch failed: {e}")

    def _update_display(self) -> None:
        if not self._data:
            return
        word   = self._data.get("word", "")
        source = self._data.get("contentProvider", {}).get("name", "")
        self._word_lbl.setText(f"{word[0].upper()}{word[1:]}" if word else "")
        self._source_lbl.setText(f"WOTD • {source}" if source else "")

    # ── Tick ──────────────────────────────────────────────────────────────────

    def tick(self) -> None:
        if time.time() < self._next_check:
            return
        self._next_check = time.time() + self._check_interval
        if self._data and self._date_from_iso(self._data.get("publishDate", "")) != self._today():
            self._fetch()