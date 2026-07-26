class PluginCarryover:

	def __init__(self):
		self.store: dict = {}

	def set(self, key: str, value) -> None:
		self.store[key] = value

	def get(self, key: str, default=None):
		return self.store.get(key, default)

	def has(self, key: str) -> bool:
		return key in self.store

	def pop(self, key: str, default=None):
		return self.store.pop(key, default)

	def clear(self) -> None:
		self.store.clear()

	def keys(self) -> list[str]:
		return list(self.store.keys())