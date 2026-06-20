
class PublicRegistry:
    """Central registry for plugin-exposed classes, variables and etc."""

    def __init__(self):
        self.exposed: dict[str, list[str]] = {}

    def has(self, name: str) -> bool:
        return hasattr(self, name)

    def expose(self, plugin: str, name: str, value, overwrite: bool = False):
        if hasattr(self, name) and not overwrite:
            print(f"PublicRegistry.expose cannot expose {name}, it's already exposed")
        self.exposed.setdefault(plugin, [])
        if name not in self.exposed[plugin]:
            self.exposed[plugin].append(name)
        setattr(self, name, value)

    def unexpose(self, plugin: str, name: str):
        if plugin in self.exposed and name in self.exposed[plugin]:
            delattr(self, name)
            self.exposed[plugin].remove(name)

    def clear(self, plugin: str):
        if plugin not in self.exposed:
            return
        for key in self.exposed[plugin]:
            if hasattr(self, key):
                delattr(self, key)
        del self.exposed[plugin]

    def list(self, plugin: str = None) -> dict:
        if plugin:
            return {name: getattr(self, name) for name in self.exposed.get(plugin, [])}
        return {p: [n for n in names] for p, names in self.exposed.items()}
