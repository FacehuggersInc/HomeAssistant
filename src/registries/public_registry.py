class PublicRegistry:

    def __init__(self, log=None):
        self.exposed: dict[str, list[str]] = {}
        self._log = log

    def _warn(self, message: str) -> None:
        if self._log is not None:
            try:
                self._log("warning", f"[PublicRegistry] {message}")
                return
            except Exception:
                pass
        print(f"[PublicRegistry] {message}")

    def owner_of(self, name: str):
        for plugin, names in self.exposed.items():
            if name in names:
                return plugin
        return None

    def has(self, name: str) -> bool:
        return self.owner_of(name) is not None

    def expose(self, plugin: str, name: str, value, overwrite: bool = False) -> bool:
        # The registry's own methods and attributes live in the same namespace as what it holds
        if name.startswith("_") or name == "exposed" or hasattr(type(self), name):
            self._warn(f"'{name}' is a reserved name - {plugin} cannot expose it.")
            return False
        owner = self.owner_of(name)
        if owner is not None and owner != plugin:
            self._warn(f"'{name}' is already exposed by {owner} - refusing {plugin}.")
            return False
        if owner == plugin and not overwrite:
            self._warn(f"'{name}' is already exposed by {plugin} - pass overwrite=True to replace it.")
            return False
        self.exposed.setdefault(plugin, [])
        if name not in self.exposed[plugin]:
            self.exposed[plugin].append(name)
        setattr(self, name, value)
        return True

    def unexpose(self, plugin: str, name: str):
        if plugin in self.exposed and name in self.exposed[plugin]:
            delattr(self, name)
            self.exposed[plugin].remove(name)

    def clear(self, plugin: str):
        if plugin not in self.exposed:
            return
        for key in self.exposed[plugin]:
            if key in self.__dict__:
                delattr(self, key)
        del self.exposed[plugin]

    def names_for(self, plugin: str) -> list[str]:
        return sorted(self.exposed.get(plugin, []))

    def list(self, plugin: str = None) -> dict:
        if plugin:
            return {name: getattr(self, name) for name in self.exposed.get(plugin, [])}
        return {p: [n for n in names] for p, names in self.exposed.items()}
