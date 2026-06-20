from src import *

class APIEndpoint():
    def __init__(self, owner:str, key:str, authed:bool, cached:bool, callback:Callable):
        self.owner : str = owner
        self.key : str = key
        self.authed : bool = authed
        self.cached : bool = cached
        self.__callback : Callable = callback
        self.data = None

    def call(self, *args, **kwargs) -> tuple[any, int]:
        data = self.__callback(*args, **kwargs)
        if isinstance(data, tuple):
            if self.cached:
                if not self.data:
                    self.data = data[0]

                return self.data, data[1]
            else:
                return data
        else:
            return data, 0


class APIRegistry():
    def __init__(self, client):
        self.client : Client = client
        self.__store = {}

    def plugin_has_registered(self, plugin_key:str) -> bool:
        endpoints = self.__store.get(plugin_key, None)
        if endpoints:
            return True
        
        return False

    def plugin_has_endpoint(self, plugin_key:str, endpoint:str):
        endpoints = self.__store.get(plugin_key, None)
        if endpoints:
            api_endpoint : APIEndpoint = endpoints.get(endpoint, None)
            if api_endpoint != None: return True
        
        return False

    def get_endpoint(self, endpoint:str) -> tuple[str, APIEndpoint]:
        for plugin_key in self.__store:
            if endpoint in self.__store[plugin_key]:
                return plugin_key, self.__store[plugin_key][endpoint]
        
        return None

    def unregister(self, plugin_key:str, endpoint:str = ""):
        """Dynamic Unloading of a Endpoint"""
        if plugin_key and self.plugin_has_registered(plugin_key):
            if endpoint and self.plugin_has_endpoint(plugin_key, endpoint):
                del self.__store[plugin_key][endpoint]
                self.client.log("info", f"[APIRegistry] Endpoint '{endpoint}' was un-registered under ownership of '{plugin_key}'")
            else:
                del self.__store[plugin_key]
                self.client.log("info", f"[APIRegistry] '{plugin_key}' had it API endpoints unloaded")

    def register(self, plugin_key:str, endpoint:str, callback: Callable, requires_auth:bool, cached:bool = False) -> tuple[APIEndpoint, bool]:
        """Creates and Stores an APIEndpoint. Returns the endpoint and if it was Just registered"""
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
        
        

        api_endpoint = APIEndpoint(plugin_key, endpoint, requires_auth, cached, callback)
        self.__store[plugin_key][endpoint] = api_endpoint
        self.client.log("info", f"[APIRegistry] Endpoint '{endpoint}' is registered under ownership of '{plugin_key}'")
        return api_endpoint, True