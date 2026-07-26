from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.main import Client


class PageEntry:

	def __init__(self, owner: str, key: str, display: str, page_class):
		self.owner   : str = owner
		self.key     : str = key
		self.display : str = display
		self.page_class = page_class
		self.instance   = None   # live QWidget, set while this page is the one on screen


class PageRegistry:

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