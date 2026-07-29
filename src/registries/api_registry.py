from __future__ import annotations
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from src.main import Client

class APIEndpoint():
    # Distinguishes "nothing cached yet" from a cached falsy value. Using the
    # value's own truthiness meant an endpoint returning {} , [] or 0 re-ran
    # its callback on every single request and never actually cached.
    _NOTHING = object()

    def __init__(self, owner:str, key:str, authed:bool, cached:bool, callback:Callable,
                 gui:str = "", description:str = "", action:str = "",
                 danger:bool = False, accepts_files:bool = False):
        self.owner : str = owner
        self.key : str = key
        self.authed : bool = authed
        self.cached : bool = cached
        # A label, if this endpoint returns a page a person would open rather
        # than data a script would read. Empty means it is not listed on the
        # index - most endpoints are not something to click.
        self.gui : str = gui
        self.description : str = description
        # A button on the index rather than a page. Same endpoint either way -
        # the difference is whether opening it is the point, or whether the
        # point is that it ran.
        self.action : str = action
        self.danger : bool = danger
        # Whether a multipart upload should be forwarded to this callback as
        # a `files` keyword. Opt-in rather than always: every other endpoint
        # would otherwise get an unexpected keyword and raise TypeError.
        self.accepts_files : bool = accepts_files
        self.__callback : Callable = callback
        self.data = self._NOTHING

    def clear_cache(self) -> None:
        self.data = self._NOTHING

    def call(self, *args, **kwargs):
        data = self.__callback(*args, **kwargs)

        # 200, not 0. A status of 0 is not a valid HTTP status: werkzeug writes
        # the status line "HTTP/1.0 0 UNKNOWN" and every client rejects it, so
        # any endpoint whose callback returned a bare value instead of a
        # (body, status) tuple was unreachable over HTTP.
        #
        # Three shapes are accepted, because an endpoint serving a page wants
        # to say so: (body), (body, status) and (body, status, headers).
        # Unpacking into two names regardless meant a callback setting a
        # Content-Type failed with "too many values to unpack" - which reaches
        # the caller as a bad-arguments error naming nothing useful.
        headers = None
        if isinstance(data, tuple) and len(data) == 3:
            body, status, headers = data
        elif isinstance(data, tuple) and len(data) == 2:
            body, status = data
        elif isinstance(data, tuple) and len(data) == 1:
            body, status = data[0], 200
        else:
            body, status = data, 200

        if self.cached:
            # Applied to every return shape. Caching used to live in the tuple
            # branch alone, so cached=True on a callback returning a bare dict
            # did nothing at all and gave no indication of it.
            if self.data is self._NOTHING:
                self.data = body
            body = self.data

        if headers is not None:
            return body, status, headers
        return body, status


class APIRegistry():
    def __init__(self, client):
        self.client : Client = client
        self.__store = {}
        # key -> (owner plugin key, instance). See register_api.
        self.__services = {}

    def endpoints_for(self, plugin_key:str) -> list[str]:
        """Endpoint names owned by a plugin, sorted."""
        return sorted(self.__store.get(plugin_key, {}).keys())

    def owners(self) -> list[str]:
        return sorted(self.__store.keys())

    def plugin_has_registered(self, plugin_key:str) -> bool:
        endpoints = self.__store.get(plugin_key, None)
        if endpoints:
            return True
        
        return False

    ## ── Services ─────────────────────────────────────────────────────────────
    #
    # An API *class* rather than an HTTP endpoint: the weather client, the RSS
    # parser. These lived in a plain `client.API` dict beside this registry,
    # which meant two things called "the API" with different rules - nothing
    # owned the dict, so a plugin that unloaded left its object behind for
    # anything still holding a reference to call into.
    #
    # Registered here they have an owner, and go when that owner does.

    def register_api(self, plugin_key: str, key: str, instance,
                     replace: bool = False):
        """
        Register an API class under a key. Returns the instance.

        Refuses to overwrite another plugin's service unless asked, for the
        same reason endpoints do: two plugins quietly fighting over one key is
        far harder to find than a warning at startup.
        """
        existing = self.__services.get(key)
        if existing is not None and not replace:
            owner, _ = existing
            if owner != plugin_key:
                self.client.log(
                    "warning", f"[APIRegistry] '{plugin_key}' cannot register "
                               f"API '{key}' - already owned by '{owner}'.")
                return self.__services[key][1]
        self.__services[key] = (plugin_key, instance)
        self.client.log("info", f"[APIRegistry] API '{key}' registered under "
                                f"ownership of '{plugin_key}'")
        return instance

    def unregister_api(self, plugin_key: str, key: str = "") -> None:
        """Drop one service, or every service a plugin registered."""
        if key:
            entry = self.__services.get(key)
            if entry and entry[0] == plugin_key:
                del self.__services[key]
                self.client.log("info", f"[APIRegistry] API '{key}' un-registered")
            return
        for name in [k for k, (owner, _) in self.__services.items()
                     if owner == plugin_key]:
            del self.__services[name]
            self.client.log("info", f"[APIRegistry] API '{name}' un-registered")

    def api(self, key: str, default=None):
        """The instance registered under a key, or `default`."""
        entry = self.__services.get(key)
        return entry[1] if entry else default

    def apis(self) -> dict:
        """Every registered service, as {key: (owner, instance)}."""
        return dict(self.__services)

    def api_owner(self, key: str) -> str:
        entry = self.__services.get(key)
        return entry[0] if entry else ""

    # Dict access, because `client.API["weather"]` reads better than
    # `client.API.api("weather")` at a call site and every existing one is
    # already written that way.
    def __getitem__(self, key: str):
        entry = self.__services.get(key)
        if entry is None:
            raise KeyError(key)
        return entry[1]

    def __setitem__(self, key: str, instance) -> None:
        self.register_api("unowned", key, instance, replace=True)

    def __delitem__(self, key: str) -> None:
        self.__services.pop(key, None)

    def __contains__(self, key: str) -> bool:
        return key in self.__services

    def get(self, key: str, default=None):
        return self.api(key, default)

    ## ── Endpoints ────────────────────────────────────────────────────────────

    def plugin_has_endpoint(self, plugin_key:str, endpoint:str):
        endpoints = self.__store.get(plugin_key, None)
        if endpoints:
            api_endpoint : APIEndpoint = endpoints.get(endpoint, None)
            if api_endpoint != None: return True
        
        return False

    def gui_endpoints(self) -> list:
        """Every endpoint that said it is a page, for the index."""
        out = []
        for owner, endpoints in self.__store.items():
            for key, endpoint in endpoints.items():
                if endpoint.gui:
                    out.append(endpoint)
        return sorted(out, key=lambda e: e.gui.lower())

    def action_endpoints(self) -> list:
        """Every endpoint that said it is a button, for the index."""
        out = []
        for owner, endpoints in self.__store.items():
            for key, endpoint in endpoints.items():
                if endpoint.action:
                    out.append(endpoint)
        return sorted(out, key=lambda e: e.action.lower())

    def get_endpoint(self, endpoint:str) -> tuple[str, APIEndpoint]:
        for plugin_key in self.__store:
            if endpoint in self.__store[plugin_key]:
                return plugin_key, self.__store[plugin_key][endpoint]
        
        return None

    def unregister(self, plugin_key:str, endpoint:str = ""):
        # A whole-plugin unregister takes its services with it. They were
        # registered by the same plugin and nothing else should be calling
        # into an object whose owner has gone.
        if plugin_key and not endpoint:
            self.unregister_api(plugin_key)
        if plugin_key and self.plugin_has_registered(plugin_key):
            if endpoint and self.plugin_has_endpoint(plugin_key, endpoint):
                del self.__store[plugin_key][endpoint]
                self.client.log("info", f"[APIRegistry] Endpoint '{endpoint}' was un-registered under ownership of '{plugin_key}'")
            else:
                del self.__store[plugin_key]
                self.client.log("info", f"[APIRegistry] '{plugin_key}' had it API endpoints unloaded")

    def register(self, plugin_key:str, endpoint:str, callback: Callable, requires_auth:bool, cached:bool = False, gui:str = "", description:str = "", action:str = "", danger:bool = False, accepts_files:bool = False) -> tuple[APIEndpoint, bool]:
        if not self.plugin_has_registered(plugin_key):
            self.__store.setdefault(plugin_key, {})

        overlapping_key = self.get_endpoint(endpoint)
        if overlapping_key:
            if self.plugin_has_endpoint(plugin_key, endpoint):
                self.client.log("info", f"[APIRegistry] Endpoint '{endpoint}' is already registered under ownership of '{plugin_key}'")
                return self.__store[plugin_key][endpoint], False
            else:
                self.client.log("warning", f"[APIRegistry] Failed to register endpoint '{endpoint}' under ownership '{plugin_key}' due to Overlapping Endpoints. Endpoint '{endpoint}' owned by '{overlapping_key[0]}'")
                return None, False
        
        

        # gui/description threaded through - the constructor took them but
        # register() was still building the endpoint without them, so every
        # page a plugin declared arrived with an empty label and was filtered
        # straight back out of the index.
        api_endpoint = APIEndpoint(plugin_key, endpoint, requires_auth, cached,
                                   callback, gui=gui, description=description,
                                   action=action, danger=danger,
                                   accepts_files=accepts_files)
        self.__store[plugin_key][endpoint] = api_endpoint
        self.client.log("info", f"[APIRegistry] Endpoint '{endpoint}' is registered under ownership of '{plugin_key}'")
        return api_endpoint, True