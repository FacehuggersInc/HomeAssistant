from __future__ import annotations

import random
from typing import Callable

from src.plugin.template import Plugin

from src.ui.overlays import Panel


class IdleTriggersPlugin(Plugin):
    def __init__(self):
        self.builders = {}
        self.invalid_pages = []
        self._invalid_keys = set()   #self.invalid_pages page keys, see check_time_update

        self.rotating_builders = False
        self.already_called_ids = []
        self.builder_used_timeslot = True
        self.last_built : list[any, str, str, bool] = [None, None, None, False]
        self.last_timeout_id = None

    ## CORE
    def load(self, carryover=None):
        self.client.subscribe_to_event(
            "on_fresh_interaction",
            self.on_fresh_interaction
        )

        self.client.subscribe_to_event(
            "on_interaction_timeout",
            self.on_interaction_timeout
        )

        self.client.subscribe_to_event(
            "on_plugin_unload",
            self.on_plugin_unload
        )

        self.client.subscribe_to_event(
            "on_update",
            self.check_time_update
        )

        self.client.public.expose("carouseltriggers", "add_trigger", self.add, True)
        self.client.public.expose("carouseltriggers", "remove_trigger", self.remove, True)

    def unload(self, carryover=None):
        if self.last_timeout_id:
            self.client.TIMEOUTS.cancel( self.last_timeout_id )


    ## EVENT
    def on_fresh_interaction(self, event) -> None:
        if self.rotating_builders:
            self.rotating_builders = False
            self.already_called_ids = []
            if isinstance(self.last_built[0], Panel) and self.last_built[3]:
                self.last_built[0].close_panel(destroy=True)
                # Dropped, so a destroyed panel is not reached again on the
                # next dismissal path.
                self.last_built[0] = None
                #Cancel Timer
                if self.last_timeout_id:
                    self.client.TIMEOUTS.cancel(self.last_timeout_id)

    def on_interaction_timeout(self, event=None) -> None:
        if self.client.PAGE and self.client.PAGE.name == "#settings":
            return
        # Checked on the way in as well as in the tick: arming here and
        # unwinding on the next pass meant a trigger could be built and
        # dismissed inside one frame on a page that never wanted it.
        if self._page_refuses():
            return
        self.rotating_builders = True

    def on_plugin_unload(self, plugin_key):
        if plugin_key in self.builders:
            del self.builders[plugin_key]
            for group in [g for g in self.invalid_pages if g[1] == plugin_key]:
                self.invalid_pages.remove( group )
            self._rebuild_invalid_keys()

    def _rebuild_invalid_keys(self) -> None:
        self._invalid_keys = {k[0] for k in self.invalid_pages}

    def _page_refuses(self) -> bool:
        """
        Whether the page on screen wants nothing to do with idle triggers.

        Two ways in, checked here rather than special-cased anywhere else:

        * a plugin registered the page key through add(), which is the
          existing route and needs the other plugin to know about this one.
        * the page carries `blocks_idle_triggers = True`, which lets a page
          refuse for itself without either plugin importing the other. A night
          clock is the case that wanted it - a screensaver over a screensaver
          is nobody's idea of restful.

        Deliberately not the same thing as `blocks_idle`. That stops the idle
        clock entirely; this only stops *these* triggers, so a page can still
        go idle, still time out, and still be interacted with normally.
        """
        page = getattr(self.client, "PAGE", None)
        if page is None:
            return False
        if getattr(page, "name", None) in self._invalid_keys:
            return True
        return bool(getattr(page, "blocks_idle_triggers", False))

    def check_time_update(self, *args):
        if not self.builders:
            return

        # Precomputed. This runs on on_update - 20 times a second, forever -
        # and rebuilt a list from self.invalid_pages on every single pass.
        if self._page_refuses():
            if self.rotating_builders:
                self.rotating_builders = False
                self.already_called_ids = []

                #Dismiss
                if isinstance(self.last_built[0], Panel) and self.last_built[3] == True:
                    self.last_built[0].close_panel(destroy=True)
                    self.last_built[0] = None
                    #Cancel Timer
                    if self.last_timeout_id:
                        self.client.TIMEOUTS.cancel(self.last_timeout_id)
            return

        if self.rotating_builders:
            if self.builder_used_timeslot == True:
                self.builder_used_timeslot = False
                self.client.call_on_ui( self.call_and_handle_random_builder )
    
    def built_panel_timeout(self):
        panel = self.last_built[0]
        if isinstance(panel, Panel):
            panel.close_panel(destroy=True)
        self.last_built[0] = None
        self.builder_used_timeslot = True

    ## FUNCTIONS
    def get_builders(self) -> list[tuple[Callable, str, str]]:
        builders = []
        for group in self.builders.values():
            builders += group
        return builders

    def get_random_unused_builder(self) -> tuple:
        all_builders = self.get_builders()
        if not all_builders:
            return (None, None, None, None)

        if len(self.already_called_ids) >= len(all_builders):
            self.already_called_ids = []

        builders = [b for b in all_builders if b[1] not in self.already_called_ids]

        if len(builders) > 1:
            builders = [b for b in builders if b[1] != self.last_built[1]]

        if builders:
            builder = random.choice(builders)
            self.already_called_ids.append(builder[1])
            return builder

        return (None, None, None, None)

    def call_and_handle_random_builder(self) -> None:
        callable, id, plugin, auto_dismiss = self.get_random_unused_builder()
        if callable:
            self.last_built[0] = callable( self.settings.rotate_time.value / 1000 )
            self.last_built[1] = id
            self.last_built[2] = plugin
            self.last_built[3] = auto_dismiss
            if isinstance(self.last_built[0], bool) and self.last_built[0] == True:
                self.builder_used_timeslot = True
            elif isinstance(self.last_built[0], Panel):
                self.last_timeout_id = self.client.TIMEOUTS.add(
                    self.settings.rotate_time.value / 1000,
                    self.built_panel_timeout,
                    f"builder_panel_timeout:{self.last_built[1]}",
                    True,
                    transient=True,
                )

    def plugin_has_registered(self, plugin_key:str):
        if self.builders.get(plugin_key):
            return True
        return False

    def add(self, plugin_key:str, builder_function:Callable, auto_dismiss:bool = True, global_invalid_pages:list[str] = []):
        if not self.plugin_has_registered(plugin_key):
            self.builders.setdefault(plugin_key, [])
        id = self.client.uuid()
        self.builders[plugin_key].append((builder_function, id, plugin_key, auto_dismiss))
        if len(global_invalid_pages) > 0:
            self.add_invalid_pages( plugin_key, global_invalid_pages )
        
        return id

    def remove(self, id:str):
        for plugin_key in self.builders:
            removal = None
            for group in self.builders[plugin_key]:
                if group[1] == id:
                    removal = group
            if removal: self.builders[plugin_key].remove(removal)
            if self.last_built[1] == id:
                if isinstance(self.last_built[0], Panel):
                    self.last_built[0].close_panel(destroy=True)
                self.last_built = [None, None, None, False]


    def add_invalid_pages(self, plugin_key:str, keys:list):
        for key in keys:
            if not key in self._invalid_keys:
                self.invalid_pages.append((key, plugin_key))
        self._rebuild_invalid_keys()