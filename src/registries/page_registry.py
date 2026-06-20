from src import *


class PageEntry:
	"""
	One registered page. Holds the un-built page CLASS (not an instance)
	plus bookkeeping needed for hot reload and ownership cleanup.

	The live instance (when this page is the one currently on screen) is
	tracked here too, so registry-driven cleanup (unregister) can tell
	whether THIS page is the one currently showing and tear it down
	properly instead of leaving an orphaned hidden QWidget behind.
	"""

	def __init__(self, owner: str, key: str, display: str, page_class):
		self.owner   : str = owner
		self.key     : str = key
		self.display : str = display
		self.page_class = page_class
		self.instance   = None   # live QWidget, set while this page is the one on screen


class PageRegistry:
	"""
	Registers pages under an owning plugin key, the same way APIRegistry
	registers API endpoints. This is what Client.goto() uses to switch
	between pages, and what lets PluginManager.unload_plugin() clean up
	every page a plugin registered automatically rather than leaving
	orphaned page classes/instances behind.

	"#root" and "#settings" are registered under owner "client" since
	they belong to the Client itself, not any plugin.
	"""

	def __init__(self, client):
		self.client : Client = client
		self.store: dict[str, dict[str, PageEntry]] = {}   # {owner: {key: PageEntry}}

	## LOOKUP

	def plugin_has_registered(self, owner: str) -> bool:
		return bool(self.store.get(owner))

	def plugin_has_page(self, owner: str, key: str) -> bool:
		owned = self.store.get(owner)
		return bool(owned and key in owned)

	def has_page(self, key: str) -> bool:
		return self.get_entry(key) is not None

	def get_entry(self, key: str):
		"""Find a PageEntry by key regardless of which plugin owns it."""
		for owner in self.store:
			if key in self.store[owner]:
				return self.store[owner][key]
		return None

	def get_owner(self, key: str):
		entry = self.get_entry(key)
		return entry.owner if entry else None

	def keys(self) -> list[str]:
		out = []
		for owner in self.store:
			out.extend(self.store[owner].keys())
		return out

	def entries_for(self, owner: str) -> list[PageEntry]:
		return list(self.store.get(owner, {}).values())

	## REGISTER / UNREGISTER

	def register(self, owner: str, key: str, display: str, page_class) -> tuple[PageEntry, bool]:
		"""
		Register a page CLASS under an owning plugin key. Returns the
		entry and whether it was newly registered (False if a page with
		this key already exists, regardless of owner — keys must be
		globally unique the same way API endpoints are).
		"""
		self.store.setdefault(owner, {})

		existing = self.get_entry(key)
		if existing:
			if existing.owner == owner:
				self.client.log("info", f"[PageRegistry] Page '{key}' is already registered under ownership of '{owner}'")
				return existing, False
			else:
				self.client.log("warning", f"[PageRegistry] Failed to register page '{key}' under ownership '{owner}' due to overlapping keys. Page '{key}' owned by '{existing.owner}'")
				return existing, False

		entry = PageEntry(owner, key, display, page_class)
		self.store[owner][key] = entry
		self.client.log("info", f"[PageRegistry] Page '{key}' is registered under ownership of '{owner}'")
		return entry, True

	def unregister(self, owner: str, key: str = "") -> None:
		"""
		Unregister one page (key given) or every page owned by a plugin
		(key omitted) — same shape as APIRegistry.unregister(). If the
		page being removed is the one currently on screen, its live
		instance is torn down first so nothing orphaned is left behind.
		"""
		if not owner or not self.plugin_has_registered(owner):
			return

		if key and self.plugin_has_page(owner, key):
			self._destroy_instance_if_current(self.store[owner][key])
			del self.store[owner][key]
			self.client.log("info", f"[PageRegistry] Page '{key}' was un-registered under ownership of '{owner}'")
			if not self.store[owner]:
				del self.store[owner]
		elif not key:
			for entry in list(self.store.get(owner, {}).values()):
				self._destroy_instance_if_current(entry)
			if owner in self.store:
				del self.store[owner]
			self.client.log("info", f"[PageRegistry] '{owner}' had its pages unloaded")

	def _destroy_instance_if_current(self, entry: PageEntry) -> None:
		"""
		If this entry's page is the one currently showing on screen,
		properly tear it down instead of leaving a hidden, orphaned
		QWidget alive. This is the fix for the leftover blank window
		seen during plugin hot reload — the old page instance used to
		only ever get hidden, never destroyed, so it stayed alive (just
		invisible) as a child of page_host indefinitely.
		"""
		if entry.instance is not None and self.client.PAGE is entry.instance:
			if hasattr(entry.instance, "stop"):
				try:
					entry.instance.stop()
				except Exception as e:
					self.client.log("warning", f"[PageRegistry] Error stopping page '{entry.key}' during unregister: {e}")
			entry.instance.setParent(None)
			entry.instance.deleteLater()
			self.client.PAGE = None
		entry.instance = None