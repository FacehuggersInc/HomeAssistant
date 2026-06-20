from src import *


class PluginCarryover:
	"""
	A persistence container created fresh for ONE reload cycle.

	reload_plugin() creates exactly one of these, passes it into the
	OLD plugin instance's unload(), then passes the SAME object into
	the NEW plugin instance's load() and reload(). This lets a plugin
	stash anything it wants to survive being destroyed and recreated —
	open connections, in-memory caches, runtime state that shouldn't be
	written to settings.json, etc.

	This is intentionally a plain key-value bag, not a typed/structured
	object — different plugins need to carry over completely different
	kinds of things, so the container itself shouldn't have an opinion
	about what's inside it.

	Reserved keys
	-------------
	"handled_navigation" : bool
		Set this to True from unload() if your plugin wants to take
		responsibility for navigation itself once it's reloaded, and
		does NOT want PluginManager.reload_plugin()'s own fallback
		navigation to run at all. unload() is the only lifecycle hook
		that runs BEFORE reload_plugin()'s fallback client.goto() call
		— setting this flag anywhere later (load(), reload()) is too
		late to suppress that call, since it will have already run.

		When set, reload_plugin() skips its own goto() entirely and
		leaves the page exactly as your reload() (or load(), or
		built()) chooses to leave it — including showing #root with a
		custom message via the data dict, if that's what you want
		shown while the rest of your plugin finishes settling in.

	Usage in a plugin
	------------------
		def unload(self, carryover: PluginCarryover = None):
			if carryover:
				carryover.set("my_cache", self.my_cache)
				carryover.set("connection", self.connection)
			# do NOT close/stop things you stashed above — the whole
			# point is they survive into the next load()

		def load(self, carryover: PluginCarryover = None):
			if carryover and carryover.has("my_cache"):
				self.my_cache = carryover.get("my_cache")
			else:
				self.my_cache = {}   # first-ever load, nothing to restore

	Note: carryover is ALWAYS None on the very first load when the
	application starts — there is nothing to carry over yet, since no
	plugin instance has ever been unloaded. It's also never passed
	during the quiet shutdown unload (PluginManager.unload_plugins(),
	used when the whole app is closing) — there is no future load() to
	hand it to in that case, so creating one would be pointless.
	"""

	def __init__(self):
		self.store: dict = {}

	def set(self, key: str, value) -> None:
		self.store[key] = value

	def get(self, key: str, default=None):
		return self.store.get(key, default)

	def has(self, key: str) -> bool:
		return key in self.store

	def pop(self, key: str, default=None):
		"""Retrieve and remove a value in one step — useful for one-shot handoffs."""
		return self.store.pop(key, default)

	def clear(self) -> None:
		self.store.clear()

	def keys(self) -> list[str]:
		return list(self.store.keys())