"""
Core Widgets.

`ASSETS` is the plugin's web folder - the pages it serves to a phone, kept as
files rather than as strings inside Python. See docs/web-ui.md.

Declared here rather than in one of the page modules because several of them
share the folder and the endpoint that serves it, and the one that happened
to be imported first would otherwise own it.
"""

from pathlib import Path

from src.webui import WebAssets

ASSETS = WebAssets(Path(__file__).with_name("web"),
                   required=("list.html", "list.css", "list.js",
                             "timer.html", "timer.css", "timer.js",
                             "sticker.html", "sticker.css",
                             "sticker.js"))
