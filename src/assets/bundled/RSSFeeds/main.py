from __future__ import annotations

import os
import json
import random
from pathlib import Path

from PyQt6.QtCore import Qt

from src.plugin.template import Plugin
from src.enums import Asset
from src.ui.overlays import Panel
from src.styling import set_style, make_font

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QScroller,
    QTextBrowser, QFrame,
)

from .api.rss import RSSFeedAPI

class RSSFeedsPlugin(Plugin):
    def __init__(self):
        self.feeds_path : Asset = Asset(Path(os.getcwd()) / "RSSFeeds")
        self.feeds = {}
        self.__builder_idletriggers_id = None

        self.last_feed = [None, None, None, None]

        self.used_feed_ids = []
        self.used_item_ids = []

        self.current_feed_data = {
            "title": "None",
            "items" : []
        }

    def load(self):
        self.client.API['RSS'] = RSSFeedAPI()
        self.client.public.expose("rssfeeds", "add_rss_feed", self.add_feed, True)

        self._load_feed_files()

        if self.client.PLUGIN.has_plugin("idletriggers"):
            if self.client.public.has( "add_trigger" ):
                id = self.client.public.add_trigger(
                    "rssfeeds",
                    self.build_new_feed_panel,
                    global_invalid_pages = ["#settings"]
                )
                self.__builder_idletriggers_id = id

    def unload(self):
        del self.client.API['RSS']

    ## FEED FILES
    def _load_feed_files(self):
        if not self.feeds_path.exists():
            try:
                self.feeds_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                self.client.log("warning", f"[RSSFeedsPlugin] couldn't create feeds folder '{self.feeds_path}': {e}")
            return

        for file in sorted(self.feeds_path.glob("*.json")):
            try:
                definition = json.loads(file.read_text(encoding="utf-8"))
            except Exception as e:
                self.client.log("warning", f"[RSSFeedsPlugin] couldn't read feed file '{file.name}': {e}")
                continue

            url = definition.get("url") if isinstance(definition, dict) else None
            transformer = definition.get("transformer") if isinstance(definition, dict) else None

            if not url:
                self.client.log("warning", f"[RSSFeedsPlugin] feed file '{file.name}' is missing a 'url' string — skipped.")
                continue

            if transformer is not None and not isinstance(transformer, dict):
                self.client.log("warning", f"[RSSFeedsPlugin] feed file '{file.name}' has a non-object 'transformer' — ignoring it, will auto-infer one instead.")
                transformer = None

            self.add_feed("rssfeeds", url, transformer)

    ## FUNCTIONS
    def plugin_has_registered(self, plugin_key:str):
        if self.feeds.get(plugin_key):
            return True
        return False

    def add_feed(self, plugin_key:str, url:str, transformer:dict = None):
        if not self.plugin_has_registered(plugin_key):
            self.feeds.setdefault(plugin_key, [])
        id = self.client.uuid()
        self.feeds[plugin_key].append((url, id, plugin_key, transformer))
        
        return id

    def _set_feed_transformer(self, plugin_key:str, feed_id:str, transformer:dict):
        group = self.feeds.get(plugin_key, [])
        for i, feed in enumerate(group):
            if feed[1] == feed_id:
                group[i] = (feed[0], feed[1], feed[2], transformer)
                break

    def get_feeds(self) -> list[tuple[str, str, str, dict]]:
        feeds = []
        for group in self.feeds.values():
            feeds += group
        return feeds

    def get_random_unused_feed(self) -> tuple:
        all_feeds = self.get_feeds()
        if not all_feeds:
            return (None, None, None, None)
        if len(self.used_feed_ids) >= len(all_feeds):
            self.used_feed_ids = []
        feeds = [f for f in all_feeds if f[1] not in self.used_feed_ids]
        if len(feeds) > 1:
            feeds = [f for f in feeds if f[1] != self.last_feed[1]]
        if feeds:
            feed = random.choice(feeds)
            self.used_feed_ids.append(feed[1])
            return feed

        return (None, None, None, None)

    ## UI

    def _make_tag(self, text:str, color:str) -> QLabel:
        tag = QLabel(text)
        tag.setFont(make_font(12, bold=True))
        tag.setWordWrap(False)
        tag.setStyleSheet(
            "QLabel {"
            f"background-color: {color};"
            "color: white;"
            "border-radius: 8px;"
            "padding: 3px 9px;"
            "}"
        )
        return tag

    def build_new_feed_panel(self, time_ms:int):
        items = (self.current_feed_data or {}).get('items') or []
        if not self.current_feed_data or len(items) <= len(self.used_item_ids):
            self.used_item_ids = []

            self.last_feed = self.get_random_unused_feed()
            if not self.last_feed[0]:
                return True  # no feeds registered yet — nothing to show this round

            api: RSSFeedAPI = self.client.API.get('RSS')
            if not api: return True

            url, feed_id, owner_key, transformer = self.last_feed

            if not transformer:
                raw, _ = api.parse(url)
                transformer = api.infer_transformer(raw)
                self._set_feed_transformer(owner_key, feed_id, transformer)
                self.last_feed = (url, feed_id, owner_key, transformer)

            transformed, _ = api.parse(url, transformer=transformer)
            self.current_feed_data = transformed or {"title": "None", "items": []}
            items = self.current_feed_data.get('items') or []

        # Pull exactly one not-yet-shown item out of the current feed.
        data = None
        for item in items:
            item_id = item.get('id')
            if item_id is None or item_id in self.used_item_ids:
                continue
            self.used_item_ids.append(item_id)
            data = item
            break

        if data is None:
            return True

        panel : Panel = self.client.create_panel()

        panel.content_layout.setContentsMargins(20, 24, 20, 20)
        panel.content_layout.setSpacing(12)

        header_widget = QWidget()
        set_style(header_widget, "common", "transparent")
        header = QHBoxLayout(header_widget)
        header.setContentsMargins(0, 0, 0, 0)
        
        item_title_lbl = QLabel(data.get('title') or "Untitled")
        item_title_lbl.setFont(make_font(17, bold=True))
        item_title_lbl.setWordWrap(True)
        set_style(item_title_lbl, "common", "text-strong")
        header.addWidget(item_title_lbl)
        panel.add_content(header_widget)

        tag_specs = [
            (self.current_feed_data.get('title'), "#3b82f6"),  # feed name
            (data.get('published'),               "#8b5cf6"), # published date
            (data.get('author'),                  "#10b981"), # author
        ]
        tags = [(str(value), color) for value, color in tag_specs if value]
        if tags:
            tags_widget = QWidget()
            set_style(tags_widget, "common", "transparent")
            tags_row = QHBoxLayout(tags_widget)
            tags_row.setContentsMargins(0, 0, 0, 0)
            tags_row.setSpacing(6)
            for text, color in tags:
                tags_row.addWidget(self._make_tag(text, color))
            tags_row.addStretch()
            panel.add_content(tags_widget)

        body = QTextBrowser()
        body.setFrameShape(QFrame.Shape.NoFrame)
        body.setOpenExternalLinks(True)
        body.setFont(make_font(14))
        body.setMarkdown(data.get('summary') or "*No summary provided.*")
        body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.viewport().setAutoFillBackground(False)
        set_style(body, "common", "text-muted")
        QScroller.grabGesture(
            body.viewport(),
            QScroller.ScrollerGestureType.LeftMouseButtonGesture
        )
        panel.add_content(body)

        return panel