"""
The bundle's skills, one module per group.

`main.py` holds the plugin and the handlers; the declarations - the examples
and the argument patterns, which are the bulk of it - live here. A group is a
module with `build(plugin, wake, key)` returning its skills, and it is listed
in GROUPS below. Nothing else has to be touched to add one.

The handlers stay on the plugin, so a group calls `plugin.something` rather
than owning the behaviour: what a skill does usually needs the client, the
panel and the other plugins, and that is the plugin's business.
"""

from __future__ import annotations

from . import (alarms, astronomy, conversions, dates, dictionary,
               navigation, notifications, quiet, system, timers, weather,
               wikipedia)

#Order is the order they are registered in, which does not affect matching -
#the engine scores every skill - but does decide the order they are listed in.
GROUPS = (dates, notifications, weather, astronomy, dictionary, conversions,
          wikipedia, timers, alarms, quiet, navigation, system)


def build_all(plugin, wake: str, key: str) -> list:
    """Every group's skills, in one list."""
    found = []
    for group in GROUPS:
        found.extend(group.build(plugin, wake, key))
    return found
