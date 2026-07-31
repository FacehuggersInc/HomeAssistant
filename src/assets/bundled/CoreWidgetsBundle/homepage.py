"""
Reaching the home page's sub-page and its widget framework.

The plugin and its timer service both place widgets, and a lookup written
twice is a lookup that can disagree. PageRegistry has no get_page, so a
sub-page is reached through its parent's entry.
"""

from __future__ import annotations

HOME_PAGE = "#cwb_home_page"


def sub_home(client):
    """The home page's `home` sub-page, or None when it is not built."""
    entry = client.PAGES.get_entry(HOME_PAGE)
    if entry is None or getattr(entry, "instance", None) is None:
        return None
    return entry.instance.sub_page_dict.get("home")


def widget_framework(client):
    """The framework that owns the home page's widgets, or None."""
    page = sub_home(client)
    if page is None or not page.has_feature("widget_framework"):
        return None
    return page.features().widget_framework
