from src import *
from src.plugin.template import Plugin

from src.ui.overlays import Panel


class CarouselPlugin(Plugin):
    def __init__(self):
        self.builders = {}
        self.invalid_pages = []

        self.rotating_builders = False
        self.already_called_ids = []
        self.builder_used_timeslot = True
        self.last_built : list[any, str, str, bool] = [None, None, None, False]
        self.last_timeout_id = None

    ## CORE
    def load(self, carryover=None):
        # Interaction watching used to be this plugin's own job —
        # InteractionEventWatcher, installed directly on client.app.
        # That's a Client-level concern now (see InteractionWatcher/
        # _on_global_interaction in src/main.py): on_fresh_interaction
        # fires exactly on the edge this plugin cares about (the FIRST
        # interaction after a period of idleness), and
        # on_interaction_timeout fires exactly on the other edge (idle
        # threshold first crossed) — both replace what this plugin used
        # to detect by hand via its own last_interaction_time polling.
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

        self.client.public.expose("carouseltriggers", "add_carousel", self.add, True)
        self.client.public.expose("carouseltriggers", "remove_carousel", self.remove, True)

    def unload(self, carryover=None):
        if self.last_timeout_id:
            self.client.TIMEOUTS.cancel( self.last_timeout_id )


    ## EVENT
    def on_fresh_interaction(self, event) -> None:
        if self.rotating_builders:
            self.rotating_builders = False
            self.already_called_ids = []
            #Dismiss — destroy=True regardless of whether the builder
            #used client.create_panel() (which already defaults to
            #destroy_on_close=True) or built a Panel directly: this
            #plugin never reuses a builder's panel once it's done
            #showing it, so it should always be fully released here,
            #not just hidden. See Panel.close_panel()/_destroy() in
            #src/ui/overlays.py for what that actually does and why it
            #matters — this used to be exactly the leak that made the
            #app gradually slow down the longer it sat idle rotating
            #through builders.
            if isinstance(self.last_built[0], Panel) and self.last_built[3]:
                self.last_built[0].close_panel(destroy=True)
                #Cancel Timer
                if self.last_timeout_id:
                    self.client.TIMEOUTS.cancel(self.last_timeout_id)

    def on_interaction_timeout(self, event=None) -> None:
        self.rotating_builders = True

    def on_plugin_unload(self, plugin_key):
        if plugin_key in self.builders:
            del self.builders[plugin_key]
            for group in [g for g in self.invalid_pages if g[1] == plugin_key]:
                self.invalid_pages.remove( group )

    def check_time_update(self, *args):
        if self.builders:
            if self.client.PAGE and self.client.PAGE.name in [g[0] for g in self.invalid_pages]:
                if self.rotating_builders:
                    self.rotating_builders = False
                    self.already_called_ids = []

                    #Dismiss
                    if isinstance(self.last_built[0], Panel) and self.last_built[3] == True:
                        self.last_built[0].close_panel(destroy=True)
                        #Cancel Timer
                        if self.last_timeout_id:
                            self.client.TIMEOUTS.cancel(self.last_timeout_id)
                return

            if self.rotating_builders:
                if self.builder_used_timeslot == True:
                    self.builder_used_timeslot = False
                    self.client.call_on_ui( self.call_and_handle_random_builder )
                else:
                    if isinstance(self.last_built[0], bool) and self.last_built[0] == False:
                        pass #! IDK WHAT TO DO HERE YET
    
    def built_panel_timeout(self):
        panel: Panel = self.last_built[0]
        panel.close_panel(destroy=True)
        self.builder_used_timeslot = True

    ## FUNCTIONS
    def get_builders(self) -> list[tuple[Callable, str, str]]:
        builders = []
        for group in self.builders.values():
            builders += group
        return builders

    def get_random_unused_builder(self) -> tuple:
        all_builders = self.get_builders()
        if len(self.already_called_ids) == len(all_builders):
            self.already_called_ids = []
        builders = [b for b in all_builders if b[1] not in self.already_called_ids and not b[1] == self.last_built[1]]
        if len(builders) > 0:
            builder = random.choice( builders )
            self.already_called_ids.append(builder[1])
            return builder
        
        return (None, None, None, None)

    def call_and_handle_random_builder(self) -> None:
        callable, id, plugin, auto_dismiss = self.get_random_unused_builder()
        if callable:
            self.last_built[0] = callable( self.settings.carousel_rotate_time.value / 1000 )
            self.last_built[1] = id
            self.last_built[2] = plugin
            self.last_built[3] = auto_dismiss
            if isinstance(self.last_built[0], bool) and self.last_built[0] == True:
                self.builder_used_timeslot = True
            elif isinstance(self.last_built[0], Panel):
                self.last_timeout_id = self.client.TIMEOUTS.add(
                    self.settings.carousel_rotate_time.value / 1000,
                    self.built_panel_timeout,
                    f"builder_panel_timeout:{self.last_built[1]}",
                    True
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
            if not key in [k[0] for k in self.invalid_pages]:
                self.invalid_pages.append((key, plugin_key))