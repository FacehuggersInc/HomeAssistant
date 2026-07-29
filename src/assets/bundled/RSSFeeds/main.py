from __future__ import annotations

import os
import json
import random
import re
import time
from html import escape as html_escape
from pathlib import Path

from threading import Thread

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap, QPainter, QColor

from src.plugin.template import Plugin
from src.enums import Asset
from src.ui.overlays import Panel
from src.styling import set_style, make_font

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QScroller,
    QTextBrowser, QFrame, QSizePolicy,
)

from .api.rss import RSSFeedAPI

class RSSFeedsPlugin(Plugin):
    def __init__(self):
        self.feeds_path : Asset = Asset(Path(os.getcwd()) / "RSSFeeds")
        self.feeds = {}
        self.__builder_idletriggers_id = None

        # Where the round-robin is up to, and everything already shown.
        self._cursor = 0
        self._shown_items: set = set()
        # feed_id -> (fetched_at, items, feed_title)
        self._feed_cache: dict = {}

    def load(self):
        self.client.API.register_api("rssfeeds", "RSS", RSSFeedAPI())
        self.client.public.expose("rssfeeds", "add_rss_feed", self.add_feed, True)

        # The folder is registered as an asset, so the feed files are
        # reachable and uploadable the same way stickers are.
        try:
            self.feeds_path.mkdir(parents=True, exist_ok=True)
            self.feeds_path.mark_uploadable()
            self.client.register_asset("rssfeeds", self.feeds_path, "FOLDER")
        except Exception as e:
            self.client.log("warning",
                            f"[RSSFeedsPlugin] Could not register the feeds "
                            f"folder: {e}")

        self.client.API.register(
            "rssfeeds", "rss_feeds", self.api_feeds, requires_auth=True,
            gui="Feeds",
            description="Add or remove the feeds the panel shows when idle.")

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
        # Endpoints and API classes both, in one call.
        self.client.API.unregister("rssfeeds")

    ## FEED FILES ON DISK

    @staticmethod
    def safe_filename(name: str) -> str:
        """
        A filename that cannot escape the feeds folder.

        This arrives from a form over the network, so it is a filename in the
        same sense an upload's is: `../../etc/cron.d/x` is a perfectly valid
        thing for somebody to type into a text box.
        """
        name = (name or "").strip().replace("\\", "/").split("/")[-1]
        name = re.sub(r"[^A-Za-z0-9 _\-]+", "", name).strip(" .-_")
        name = re.sub(r"\s+", " ", name)[:48]
        return name

    def feed_files(self) -> list:
        """[(name, url)] from the folder, sorted."""
        out = []
        try:
            files = sorted(self.feeds_path.glob("*.json"))
        except OSError:
            return out
        for file in files:
            try:
                definition = json.loads(file.read_text(encoding="utf-8"))
                url = definition.get("url") if isinstance(definition, dict) else None
            except Exception:
                continue
            if url:
                out.append((file.stem, str(url)))
        return out

    def write_feed_file(self, name: str, url: str) -> tuple:
        """Save a feed. Returns (ok, message)."""
        name = self.safe_filename(name)
        url = (url or "").strip()

        if not name:
            return False, "Give it a name."
        if not url.lower().startswith(("http://", "https://")):
            return False, "The address must start with http:// or https://."
        if any(existing == url for _n, existing in self.feed_files()):
            return False, "That feed is already subscribed."

        path = self.feeds_path / f"{name}.json"
        if path.exists():
            return False, f"There is already a feed called '{name}'."

        try:
            self.feeds_path.mkdir(parents=True, exist_ok=True)
            # The url key only, which is the whole format. A transformer is
            # inferred on first use and does not belong in a file somebody
            # typed by hand.
            path.write_text(json.dumps({"url": url}, indent=4),
                            encoding="utf-8")
        except OSError as e:
            return False, f"Could not save it: {e}"

        self.add_feed("rssfeeds", url, None)
        self._feed_cache.clear()
        return True, f"Added '{name}'."

    def remove_feed_file(self, name: str) -> tuple:
        name = self.safe_filename(name)
        path = self.feeds_path / f"{name}.json"
        if not name or not path.is_file():
            return False, f"There is no feed called '{name}'."

        try:
            url = (json.loads(path.read_text(encoding="utf-8")) or {}).get("url")
        except Exception:
            url = None
        try:
            path.unlink()
        except OSError as e:
            return False, f"Could not remove it: {e}"

        # Out of the live rotation as well as off the disk, or it keeps
        # appearing until the next restart.
        for owner, group in self.feeds.items():
            self.feeds[owner] = [f for f in group if f[0] != url]
        self._feed_cache.clear()
        self._shown_items.clear()
        return True, f"Removed '{name}'."

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
            # Optional, for a host that wants something specific - a cookie,
            # a different agent. Merged over the defaults, so a feed only has
            # to name what it needs.
            headers = definition.get("headers") if isinstance(definition, dict) else None
            if headers is not None and not isinstance(headers, dict):
                self.client.log("warning", f"[RSSFeedsPlugin] feed file "
                                           f"'{file.name}' has a non-object "
                                           f"'headers' - ignoring it.")
                headers = None

            if not url:
                self.client.log("warning", f"[RSSFeedsPlugin] feed file '{file.name}' is missing a 'url' string — skipped.")
                continue

            if transformer is not None and not isinstance(transformer, dict):
                self.client.log("warning", f"[RSSFeedsPlugin] feed file '{file.name}' has a non-object 'transformer' — ignoring it, will auto-infer one instead.")
                transformer = None

            self.add_feed("rssfeeds", url, transformer, headers)

    ## API

    def api_feeds(self, name: str = "", url: str = "", remove: str = "",
                  **_ignored):
        """The page a phone manages feeds from, and what it posts back to."""
        from .api.feeds_page import render_page
        from flask import request as _request

        token = ""
        try:
            token = (_request.args.get("token")
                     or _request.headers.get("X-Client-Token") or "")
        except Exception:
            pass

        message, bad = "", False
        if remove:
            ok, message = self.remove_feed_file(remove)
            bad = not ok
        elif name or url:
            ok, message = self.write_feed_file(name, url)
            bad = not ok
            if ok:
                name = url = ""

        page = render_page(token, self.feed_files(), message=message, bad=bad,
                           form={"name": name, "url": url})
        return page, 200, {"Content-Type": "text/html; charset=utf-8"}

    ## FUNCTIONS
    def plugin_has_registered(self, plugin_key:str):
        if self.feeds.get(plugin_key):
            return True
        return False

    def add_feed(self, plugin_key:str, url:str, transformer:dict = None,
                 headers:dict = None):
        if not self.plugin_has_registered(plugin_key):
            self.feeds.setdefault(plugin_key, [])
        id = self.client.uuid()
        self.feeds[plugin_key].append((url, id, plugin_key, transformer,
                                       headers or {}))
        return id

    def _set_feed_transformer(self, plugin_key:str, feed_id:str, transformer:dict):
        group = self.feeds.get(plugin_key, [])
        for i, feed in enumerate(group):
            if feed[1] == feed_id:
                group[i] = (feed[0], feed[1], feed[2], transformer, feed[4])
                break

    def get_feeds(self) -> list[tuple[str, str, str, dict, dict]]:
        feeds = []
        for group in self.feeds.values():
            feeds += group
        return feeds

    ## ROTATION
    #
    # One item from each feed in turn, rather than everything from one feed and
    # then everything from the next. With a Steam deals feed and a news feed
    # the old order meant twenty game deals before a single headline; this way
    # they alternate, and nothing repeats until every item from every feed has
    # been shown once.

    #how long a feed's parsed items are trusted before refetching
    FEED_TTL = 900

    def _feed_items(self, feed: tuple) -> list:
        """
        A feed's items, parsed and cached.

        Cached because this is now consulted once per panel rather than once
        per feed exhaustion - refetching on every rotation would hit the
        network every minute for as long as the panel sat idle.
        """
        url, feed_id, owner_key, transformer, headers = feed
        entry = self._feed_cache.get(feed_id)
        if entry and (time.time() - entry[0]) < self.FEED_TTL:
            return entry[1]

        api: RSSFeedAPI = self.client.API.get('RSS')
        if not api:
            return []

        # Fetched once. Inferring a transformer and then applying it with a
        # second parse() would be two requests back to back for every feed,
        # which is enough on its own to trip a rate limiter.
        try:
            api.missing_paths.clear()
            raw, _ = api.parse(url, headers=headers)
        except Exception as e:
            self.client.log("warning", f"[RSSFeedsPlugin] '{url}' failed: {e}")
            self._hold_off(feed_id)
            return []

        problem = api.fetch_problem(raw)
        if problem:
            self.client.log("warning", f"[RSSFeedsPlugin] '{url}' {problem}.")
            self._hold_off(feed_id, long=raw.get("status") == 429)
            return []

        if not transformer:
            transformer = api.infer_transformer(raw)
            self._set_feed_transformer(owner_key, feed_id, transformer)

        try:
            transformed = api.transform_data(raw, transformer) or {}
        except Exception as e:
            self.client.log("warning", f"[RSSFeedsPlugin] '{url}' could not be "
                                       f"read: {e}")
            self._hold_off(feed_id)
            return []

        items = transformed.get("items") or []
        title = transformed.get("title") or ""

        if api.missing_paths:
            self.client.log("debug", f"[RSSFeedsPlugin] '{title or url}' has no "
                                     f"{', '.join(sorted(api.missing_paths))}.")
        if items:
            self.client.log("debug", f"[RSSFeedsPlugin] '{title or url}' "
                                     f"loaded {len(items)} items.")
        else:
            self.client.log("warning", f"[RSSFeedsPlugin] '{url}' has no items.")
            self._hold_off(feed_id)
            return []

        self._feed_cache[feed_id] = (time.time(), items, title)
        return items

    #how long a feed that answered badly is left alone. A rate limiter needs
    #considerably longer than a missing file does.
    RETRY_AFTER = 900
    RETRY_AFTER_LIMITED = 3600

    def _hold_off(self, feed_id: str, long: bool = False) -> None:
        """
        Stop asking a feed that is not answering.

        Cached as empty with a future timestamp, so the ordinary TTL check
        keeps it out of the rotation without a second mechanism.
        """
        wait = self.RETRY_AFTER_LIMITED if long else self.RETRY_AFTER
        stamp = time.time() + (wait - self.FEED_TTL)
        self._feed_cache[feed_id] = (stamp, [], "")

    def _feed_title(self, feed_id: str) -> str:
        entry = self._feed_cache.get(feed_id)
        return entry[2] if entry else ""

    @staticmethod
    def _item_key(feed_id: str, item: dict, index: int) -> str:
        """
        A key unique across every feed.

        The item's own id alone is not enough: two feeds can both number their
        entries from one, and one would then suppress the other.
        """
        own = item.get("id") or item.get("link") or item.get("title")
        return f"{feed_id}:{own or index}"

    def next_item(self) -> tuple:
        """
        (item, feed_title) for the next thing to show, or (None, "").

        Takes one item from each feed in turn. A feed with nothing left is
        skipped; when every feed is exhausted the shown-list is cleared and it
        starts round again.
        """
        feeds = self.get_feeds()
        if not feeds:
            return None, ""

        # Two passes at most: one from where the cursor is, and one more after
        # a reset. Without the bound, a set of feeds that are all empty would
        # spin here forever.
        for attempt in range(2):
            for _ in range(len(feeds)):
                feed = feeds[self._cursor % len(feeds)]
                self._cursor = (self._cursor + 1) % len(feeds)

                feed_id = feed[1]
                items = self._feed_items(feed)
                for index, item in enumerate(items):
                    key = self._item_key(feed_id, item, index)
                    if key in self._shown_items:
                        continue
                    self._shown_items.add(key)
                    return item, self._feed_title(feed_id)

            if attempt == 0:
                # Everything seen. Round again from the top.
                self.client.log("debug",
                                "[RSSFeedsPlugin] All items shown - starting over.")
                self._shown_items.clear()

        return None, ""

    ## UI

    #the panel, as a fraction of the screen. It was whatever Panel defaulted
    #to, which on a wide screen was a column of text with a great deal of
    #nothing beside it.
    WIDTH_RATIO = 0.40
    MIN_WIDTH   = 460
    MAX_WIDTH   = 780
    #the picture strip across the top. Fixed, so the layout below it does not
    #jump about as pictures of different shapes arrive.
    IMAGE_HEIGHT = 260
    #how many of an entry's images are worth downloading
    MAX_IMAGES = 6
    #how long each one is shown for
    CAROUSEL_MS = 4500
    #the theme accent, matching .dialog-button-primary's border. Qt's own link
    #blue is nearly invisible on this background.
    LINK_COLOUR = "#6fa8e0"
    #the countdown bar. Green, matching the accent every page the panel serves
    #to a phone already uses (--accent in webui.py), and tall enough to read
    #from across a room rather than being a hairline.
    PROGRESS_HEIGHT = 7
    PROGRESS_COLOUR = (47, 240, 142, 225)

    def _panel_width(self) -> int:
        try:
            host = self.client.OVERLAYS
            if host is not None and host.width() > 0:
                return max(self.MIN_WIDTH,
                           min(self.MAX_WIDTH, int(host.width() * self.WIDTH_RATIO)))
        except Exception:
            pass
        return self.MIN_WIDTH

    #tall enough for the text plus its padding, and no taller
    TAG_HEIGHT = 30

    def _make_tag(self, text:str, style:str) -> QLabel:
        tag = QLabel(text)
        tag.setFont(make_font(14, bold=True))
        tag.setWordWrap(False)
        # Fixed on both axes. A QLabel defaults to Preferred/Preferred, so in
        # a column with spare height the layout stretched these pills into
        # tall coloured slabs - which is exactly what they looked like.
        tag.setFixedHeight(self.TAG_HEIGHT)
        tag.setSizePolicy(QSizePolicy.Policy.Maximum,
                          QSizePolicy.Policy.Fixed)
        tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # A stylesheet class rather than an inline setStyleSheet: the colours
        # belong with every other colour in the app, and an inline sheet on a
        # QLabel also stops it inheriting anything else.
        set_style(tag, "rss", style)
        return tag

    ## -- progress

    def _build_progress(self, seconds: float, width: int) -> QWidget:
        """
        A thin bar counting down to the next article.

        The builder is handed how long this panel will be up for, so the panel
        can say so rather than leaving somebody wondering whether it is stuck.

        Painted by a plain widget rather than a QProgressBar: a progress bar
        comes with a groove, a border, a chunk and a text format, all of which
        would have to be styled away to get a line.
        """
        bar = QWidget()
        bar.setFixedHeight(self.PROGRESS_HEIGHT)
        bar.setFixedWidth(width)
        bar.fraction = 0.0
        set_style(bar, "common", "transparent")

        total = max(1.0, float(seconds or 0))
        started = time.monotonic()

        def paint(event, widget=bar):
            painter = QPainter(widget)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.fillRect(widget.rect(), QColor(255, 255, 255, 26))
            filled = int(widget.width() * max(0.0, min(1.0, widget.fraction)))
            if filled:
                painter.fillRect(0, 0, filled, widget.height(),
                                 QColor(*self.PROGRESS_COLOUR))
            painter.end()

        bar.paintEvent = paint

        timer = QTimer(bar)
        timer.setInterval(120)

        def tick():
            try:
                bar.fraction = min(1.0, (time.monotonic() - started) / total)
                bar.update()
                if bar.fraction >= 1.0:
                    timer.stop()
            except RuntimeError:
                pass      # the panel went; the timer goes with it

        timer.timeout.connect(tick)
        timer.start()
        return bar

    ## -- pictures

    def _build_carousel(self, urls: list, width: int) -> QLabel:
        """
        The picture strip across the top of the panel.

        Full bleed: no margin, no rounding, cropped to fill rather than fitted
        inside. A feed image letterboxed inside a padded box looks like a
        placeholder; one that runs edge to edge looks like the article.

        With more than one image it cycles. The timer is parented to the label
        so it dies with the panel - a transient panel that left a timer running
        would keep swapping pictures nobody can see.
        """
        holder = QLabel()
        holder.setFixedSize(width, self.IMAGE_HEIGHT)
        holder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        holder.setScaledContents(False)
        set_style(holder, "rss", "image")

        holder._frames = []          # loaded pixmaps, in arrival order
        holder._index = 0

        for url in urls[:self.MAX_IMAGES]:
            self._fetch_image(url, holder, width)

        if len(urls) > 1:
            timer = QTimer(holder)
            timer.setInterval(self.CAROUSEL_MS)
            timer.timeout.connect(lambda h=holder: self._advance_carousel(h))
            timer.start()
        return holder

    def _advance_carousel(self, holder: QLabel) -> None:
        try:
            frames = getattr(holder, "_frames", [])
            if len(frames) < 2:
                return
            holder._index = (holder._index + 1) % len(frames)
            holder.setPixmap(frames[holder._index])
        except RuntimeError:
            pass      # the panel went away; the timer goes with it

    def _fetch_image(self, url: str, label: QLabel, width: int) -> None:
        """
        Download a picture and put it in a label, without blocking anything.

        On a worker, because this is called while building a panel on the UI
        thread and a feed image is a network round trip. The label is checked
        for still existing on the way back: the panel is transient and may
        well have been dismissed before the bytes arrived.
        """
        def work():
            data = None
            try:
                import urllib.request
                request = urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(request, timeout=8) as response:
                    data = response.read(6 * 1024 * 1024)
            except Exception as e:
                self.client.log("debug", f"[RSSFeeds] Image failed: {e}")
                return
            if not data:
                return

            def apply():
                try:
                    pixmap = QPixmap()
                    if not pixmap.loadFromData(data):
                        return
                    # Expanding, then cropped to the middle. KeepAspectRatio
                    # would letterbox, which is exactly the padded-box look
                    # this is meant to get rid of.
                    scaled = pixmap.scaled(
                        width, self.IMAGE_HEIGHT,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation)
                    if scaled.width() > width or scaled.height() > self.IMAGE_HEIGHT:
                        left = max(0, (scaled.width() - width) // 2)
                        top = max(0, (scaled.height() - self.IMAGE_HEIGHT) // 2)
                        scaled = scaled.copy(left, top, width, self.IMAGE_HEIGHT)

                    frames = getattr(label, "_frames", None)
                    if frames is None:
                        frames = label._frames = []
                    frames.append(scaled)
                    if len(frames) == 1:
                        label.setPixmap(scaled)
                        label.setVisible(True)
                except RuntimeError:
                    # The panel went away while this was in flight. Expected.
                    pass

            self.client.call_on_ui(apply)

        Thread(target=work, name="__rss_image", daemon=True).start()

    def build_new_feed_panel(self, time_ms:int):
        """
        Build one article panel.

        `time_ms` is misnamed by the caller - IdleRandomTriggers passes
        `rotate_time.value / 1000`, which is **seconds**. Left as-is because
        renaming it here would not change what arrives, but the progress bar
        below treats it as seconds, which is what it is.
        """
        data, feed_title = self.next_item()
        if data is None:
            return True      # nothing to show this round

        width = self._panel_width()
        panel : Panel = self.client.create_panel(width=width)

        # No margins on the panel itself. The picture runs edge to edge, and
        # everything below it sits in its own padded container - margins here
        # would inset the image too, which is the boxed-in look being fixed.
        panel.content_layout.setContentsMargins(0, 0, 0, 0)
        panel.content_layout.setSpacing(0)

        panel.add_content(self._build_progress(time_ms, width))

        images = [str(u) for u in (data.get("images") or []) if u]
        if not images and data.get("image"):
            images = [str(data["image"])]
        if images:
            panel.add_content(self._build_carousel(images, width))

        text_holder = QWidget()
        set_style(text_holder, "common", "transparent")
        text_column = QVBoxLayout(text_holder)
        text_column.setContentsMargins(24, 22, 24, 20)
        text_column.setSpacing(14)
        inner_width = max(120, width - 48)
        panel.add_content(text_holder)
        panel.content_layout.setStretchFactor(text_holder, 1)

        item_title_lbl = QLabel(data.get('title') or "Untitled")
        # Considerably larger than it was. This is the one thing somebody
        # glancing at the panel from across the room can actually read.
        item_title_lbl.setFont(make_font(28, bold=True))
        item_title_lbl.setWordWrap(True)
        # The wrapped height, worked out and fixed.
        #
        # `Minimum` was not enough: it means "no smaller than the hint", and
        # the column will still hand it anything going spare. `Fixed` with a
        # size policy is not enough either, because that trusts sizeHint(),
        # which for a wrapping label is a guess at a squarish box rather than
        # the height at this width.
        #
        # setFixedHeight sets the minimum AND the maximum, so there is no
        # width left for a layout to give away. heightForWidth() is exact, and
        # the font is already set above so it measures the real thing.
        item_title_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft
                                    | Qt.AlignmentFlag.AlignTop)
        item_title_lbl.setFixedWidth(inner_width)
        item_title_lbl.setFixedHeight(
            max(1, item_title_lbl.heightForWidth(inner_width)))
        set_style(item_title_lbl, "common", "text-strong")
        text_column.addWidget(item_title_lbl)

        tag_specs = [
            (feed_title,                                 "tag-feed"),
            (self._nice_date(data.get('published')),     "tag-date"),
            (data.get('author'),                         "tag-author"),
        ]
        tags = [(str(value), style) for value, style in tag_specs if value]
        if tags:
            tags_widget = QWidget()
            set_style(tags_widget, "common", "transparent")
            tags_widget.setFixedHeight(self.TAG_HEIGHT)
            tags_widget.setSizePolicy(QSizePolicy.Policy.Preferred,
                                      QSizePolicy.Policy.Fixed)
            tags_row = QHBoxLayout(tags_widget)
            tags_row.setContentsMargins(0, 0, 0, 0)
            tags_row.setSpacing(8)
            for text, style in tags:
                tags_row.addWidget(self._make_tag(self._shorten(text), style))
            tags_row.addStretch()
            text_column.addWidget(tags_widget)

        body = QTextBrowser()
        body.setFrameShape(QFrame.Shape.NoFrame)
        body.setOpenExternalLinks(True)
        body.setFont(make_font(18))
        body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.viewport().setAutoFillBackground(False)
        body.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        set_style(body, "rss", "body")
        set_style(body.viewport(), "common", "transparent")

        summary = data.get('summary') or ""
        if not self._has_text(summary):
            # A link post has no body of its own - its entire content was the
            # thumbnail and Reddit's furniture, both now gone. Saying where it
            # points is more use than an empty panel.
            link = str(data.get("link") or "")
            summary = self._link_only_body(link)

        if self._looks_like_html(summary):
            # setHtml, not setMarkdown. Most feeds send HTML, and asking Qt to
            # read it as markdown printed the tags out as text - which is what
            # made the panel look like a dump of source rather than an article.
            body.setHtml(self._prepare_html(summary))
        elif summary.strip():
            body.setMarkdown(summary)
        else:
            body.setMarkdown("*No summary provided.*")

        QScroller.grabGesture(
            body.viewport(),
            QScroller.ScrollerGestureType.LeftMouseButtonGesture
        )
        text_column.addWidget(body)
        # The body takes the spare height, so the text fills the panel rather
        # than huddling under the title with empty space beneath it.
        text_column.setStretchFactor(body, 1)

        return panel

    ## -- text

    @staticmethod
    def _nice_date(value) -> str:
        """
        "18 Feb 2020" out of whatever the feed put in its pubDate.

        Feeds send RFC-822, and "Tue, 18 Feb 2020 10:29:00 -0800" as a pill on
        a wall panel is a wall of punctuation nobody reads.
        """
        text = str(value or "").strip()
        if not text:
            return ""
        from email.utils import parsedate_to_datetime
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            try:
                from datetime import datetime as _dt
                when = _dt.fromisoformat(text.replace("Z", "+00:00"))
            except (TypeError, ValueError):
                # Unparseable, so show it as it came rather than nothing.
                return text
        return when.strftime("%d %b %Y")

    @staticmethod
    def _shorten(text: str, limit: int = 42) -> str:
        text = " ".join(str(text or "").split())
        return text if len(text) <= limit else text[:limit - 1] + "\u2026"

    @staticmethod
    def _has_text(html: str) -> bool:
        """Whether anything survives once the tags come off."""
        stripped = re.sub(r"<[^>]*>", " ", str(html or ""))
        stripped = re.sub(r"&[a-z#0-9]+;", " ", stripped, flags=re.I)
        return bool(stripped.strip())

    @staticmethod
    def _link_only_body(link: str) -> str:
        if not link:
            return ""
        from urllib.parse import urlsplit
        host = urlsplit(link).netloc or link
        host = host[4:] if host.startswith("www.") else host
        return (f'<p>Links to <a href="{html_escape(link)}">'
                f'{html_escape(host)}</a></p>')

    @staticmethod
    def _looks_like_html(text: str) -> bool:
        return bool(text) and bool(re.search(r"<(p|br|div|img|a|ul|li|b|i|strong)\b",
                                             text, re.I))

    def _prepare_html(self, html: str, shown_image: str = "") -> str:
        """
        Tidy a feed's HTML for a small dark panel.

        Feeds are written for a white page in a browser: black text, fixed
        pixel widths, and the same image the panel is already showing at the
        top. Left alone that is unreadable here.
        """
        cleaned = html
        # The picture is already above. Leaving it inline shows it twice, at
        # whatever size the publisher chose.
        cleaned = re.sub(r"<img\b[^>]*>", "", cleaned, flags=re.I)
        # Publishers' own colours and sizes, which are for a white page.
        cleaned = re.sub(r"""\s(?:style|width|height|bgcolor|color)\s*=\s*["'][^"']*["']""",
                         "", cleaned, flags=re.I)
        cleaned = re.sub(r"<(script|style)\b.*?</\1>", "", cleaned,
                         flags=re.I | re.S)
        # a:link explicitly, because a QTextBrowser's default is the same
        # saturated blue on every theme and is near unreadable on a dark
        # panel. This is the accent the dialog buttons use.
        return f'''<style>
a, a:link, a:visited {{ color: {self.LINK_COLOUR}; text-decoration: none; }}
</style>
<div style="color:#ccd2dc; font-size:18px; line-height:162%;">
{cleaned}
</div>'''