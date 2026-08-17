"""
Starting a timer from a phone.

A page with a form rather than an index button labelled "Start a 5 minute
timer": that button fired the endpoint with no arguments at all, so the
duration never arrived and the label was a promise nothing kept.

The page is three files in `web/` - `timer.html`, `timer.css` and `timer.js`
- read by `WebAssets`. See docs/web-ui.md.
"""

from __future__ import annotations

# Absolute, not relative.
#
# `from ..timers import clock` needs this module to have a package, and it
# does not when it is loaded by path - which is how a plugin loads its own
# pages. The relative form worked while the import machinery happened to line
# up and failed from inside render_page() when it did not, which is a worse
# place to find out.
from src.assets.bundled.CoreWidgetsBundle import ASSETS
from src.assets.bundled.CoreWidgetsBundle.timers import clock

PRESETS = [1, 2, 3, 5, 10, 15, 20, 30, 45, 60, 90]

PATH = "/public/timer_form"


def render_page(token: str, running: list, message: str = "",
                bad: bool = False, form: dict = None) -> str:
    """
    The timer form.

    `running` is the timers already on the panel. They are sent as data and
    drawn by the script, so a timer somebody called `<b>eggs` is text on the
    page rather than markup in it.
    """
    form = form or {}

    return ASSETS.page(
        title="Start a timer",
        heading="Start a timer",
        blurb="It appears on the panel straight away.",
        token=token, endpoint=PATH, message=message, bad=bad,
        body_file="timer.html", css_file="timer.css",
        script_file="timer.js",
        data={
            "presets": list(PRESETS),
            "running": [{"name": timer.name or "Timer",
                         "left": clock(timer.remaining())}
                        for timer in running or []],
            "form": {
                "hours": str(form.get("hours") or "0"),
                "minutes": str(form.get("minutes") or "5"),
                "seconds": str(form.get("seconds") or "0"),
                "name": str(form.get("name") or ""),
            },
            # Blank is a real answer here and is not one of the nine: a timer
            # with nowhere named goes wherever there is room.
            "quadrant": str(form.get("quadrant") or ""),
        },
    )
