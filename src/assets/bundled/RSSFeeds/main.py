from src import *
from src.plugin.template import Plugin
from src.enums import Asset
from src.ui.overlays import Panel
from src.styling import set_style, make_font

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,QScrollArea, QScroller,
)

from .api.rss import RSSFeedAPI

class RSSFeedsPlugin(Plugin):
    def __init__(self):
        self.feeds_path : Asset = Asset(Path(os.getcwd() / "RSSFeeds"))
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

    ## FUNCTIONS
    def plugin_has_registered(self, plugin_key:str):
        if self.builders.get(plugin_key):
            return True
        return False

    def add_feed(self, plugin_key:str, url:str, transformer:dict):
        if not self.plugin_has_registered(plugin_key):
            self.feeds.setdefault(plugin_key, [])
        id = self.client.uuid()
        self.feeds[plugin_key].append((url, id, plugin_key, transformer))
        
        return id

    def get_feeds(self) -> list[tuple[Callable, str, str]]:
        builders = []
        for group in self.builders.values():
            builders += group
        return builders

    def get_random_unused_feed(self) -> tuple:
        all_feeds = self.get_feeds()
        if len(self.used_feed_ids) == len(self.used_feed_ids):
            self.used_feed_ids = []
        feeds = [b for b in all_feeds if b[1] not in self.used_feed_ids and not b[1] == self.last_feed[1]]
        if len(feeds) > 0:
            feed = random.choice( feeds )
            self.used_feed_ids.append(feed[1])
            return feed
        
        return (None, None, None, None)

    ## UI


    def build_new_feed_panel(self, time_ms:int):
        if not self.current_feed_data or len(self.current_feed_data['items']) == len(self.used_item_ids):
            if len(self.current_feed_data['items']) == len(self.used_item_ids):
                self.used_item_ids = []

            self.last_feed = self.get_random_unused_feed()

            api: RSSFeedAPI = self.client.API.get('RSS')
            if not api: return True
            self.current_feed_data = api.parse(
                self.last_feed[0],
                transformer = self.last_feed[-1]
            )
        
        data = None
        for item in self.current_feed_data['items']:
            if not item['id'] in self.used_item_ids:
                self.used_item_ids.append(item['id'])
                data = item

        panel : Panel = self.client.create_panel()
        
        header = QHBoxLayout()
        title_lbl = QLabel("Notifications")
        title_lbl.setFont(make_font(20, bold=True))
        set_style(title_lbl, "common", "text-strong")
        header.addWidget(title_lbl)
        panel.add_content( header )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        set_style(scroll, "notification", "notification-scroll", object_tag="QScrollArea")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        QScroller.grabGesture(
            scroll.viewport(), 
            QScroller.ScrollerGestureType.LeftMouseButtonGesture
        )

        self._list_widget = QWidget()
        set_style(self._list_widget, "common", "transparent")
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch()

        scroll.setWidget(self._list_widget)
        panel.add_content(scroll)