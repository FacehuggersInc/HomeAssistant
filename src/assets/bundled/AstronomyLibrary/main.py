"""
Where the sun and moon are, as a library.

A plugin that registers no page, no widget and no skill - it exists to put
`astronomy.py` on the public registry so more than one plugin can have it.

It lives here rather than in `src/` because it is not the panel's own
machinery: nothing in the client needs to know where the moon is, and a
plugin that wants to can be uninstalled. It lives here rather than inside the
night clock because Core Widgets loads BEFORE the night clock, so a
dependency in that direction is a cycle - and this has no dependencies at
all, which lets everything else depend on it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.plugin.template import Plugin

if TYPE_CHECKING:
    from src.main import Client


class AstronomyLibrary(Plugin):

    KEY = "astronomy"

    def load(self, carryover=None):
        from . import astronomy

        # The module as well as the functions. A caller that wants one thing
        # takes the callable; a caller doing several sums in a row would
        # rather have the module than fish six names out of a dict.
        self.client.public.expose(self.KEY, "astronomy", {
            "module":            astronomy,
            "sun_times":         astronomy.sun_times,
            "next_sun_event":    astronomy.next_sun_event,
            "describe_wait":     astronomy.describe_wait,
            "moon_phase":        astronomy.moon_phase,
            "moon_name":         astronomy.moon_name,
            "moon_illumination": astronomy.moon_illumination,
            "moon_waxing":       astronomy.moon_waxing,
            "moon_age":          astronomy.moon_age,
        })
        self.client.log("info", "[Astronomy] Sun and moon available.")

    def unload(self, carryover=None):
        # `public.clear(key)` on unload takes the exposure with it - see the
        # plugin manager. Nothing else to undo: there is no state, no timer
        # and no widget.
        pass
