from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from src.main import Client


class QuickAccessEntry:
	"""
	One button in the quick settings panel.

	The entry is a description, not a widget. The panel builds a button from
	it every time it opens, so an entry registered while the panel is closed
	still appears, and an entry whose owner unloaded simply stops being built.
	Holding live widgets here instead was what made the old per-page drawer
	controls awkward to move between pages.
	"""

	def __init__(
		self,
		owner:    str,
		key:      str,
		label:    str,
		icon:     str,
		on_press: Callable,
		on_state: Optional[Callable] = None,
		order:    int  = 100,
		enabled:  bool = True,
	):
		self.owner    = owner
		self.key      = key
		self.label    = label
		self.icon     = icon
		self.on_press = on_press
		# Optional; returns True when the button should read as "on". Buttons
		# without one are momentary rather than toggles.
		self.on_state = on_state
		self.order    = order
		self.enabled  = enabled

	@property
	def uid(self) -> str:
		return f"{self.owner}.{self.key}"

	def active(self) -> bool:
		if not callable(self.on_state):
			return False
		try:
			return bool(self.on_state())
		except Exception:
			return False

	def press(self) -> None:
		if callable(self.on_press):
			self.on_press()


class QuickAccessRegistry:
	"""
	Global registry of quick access buttons, owned by the client.

	Deliberately not per page. The buttons used to live on each page's drawer,
	which meant the same control had to be added to every page that wanted it
	and vanished on the ones that did not. Registration here is once, from
	anywhere, and the panel is reachable from every page.
	"""

	def __init__(self, client):
		self.client: Client = client
		self.store: dict[str, dict[str, QuickAccessEntry]] = {}   # {owner: {key: entry}}
		self._listeners: list[Callable] = []

	## LOOKUP

	def has(self, owner: str, key: str) -> bool:
		owned = self.store.get(owner)
		return bool(owned and key in owned)

	def get(self, owner: str, key: str) -> Optional[QuickAccessEntry]:
		return self.store.get(owner, {}).get(key)

	def entries_for(self, owner: str) -> list[QuickAccessEntry]:
		return list(self.store.get(owner, {}).values())

	def entries(self) -> list[QuickAccessEntry]:
		"""Everything registered, in display order."""
		out: list[QuickAccessEntry] = []
		for owned in self.store.values():
			out.extend(owned.values())
		out.sort(key=lambda e: (e.order, e.label.lower()))
		return out

	## REGISTER / UNREGISTER

	def register(
		self,
		owner:    str,
		key:      str,
		label:    str,
		icon:     str,
		on_press: Callable,
		on_state: Callable = None,
		order:    int  = 100,
		enabled:  bool = True,
	) -> QuickAccessEntry:
		self.store.setdefault(owner, {})

		if key in self.store[owner]:
			self.client.log("warning", f"[QuickAccess] '{owner}' re-registered '{key}' "
										"- replacing the existing entry.")

		entry = QuickAccessEntry(owner, key, label, icon, on_press,
								 on_state=on_state, order=order, enabled=enabled)
		self.store[owner][key] = entry
		self.client.log("info", f"[QuickAccess] Registered '{entry.uid}' ({label}).")
		self.changed()
		return entry

	def unregister(self, owner: str, key: str = None) -> None:
		"""Drop one entry, or every entry belonging to an owner."""
		if owner not in self.store:
			return
		if key is None:
			count = len(self.store[owner])
			del self.store[owner]
			if count:
				self.client.log("info", f"[QuickAccess] Removed {count} entr"
										f"{'y' if count == 1 else 'ies'} for '{owner}'.")
		else:
			if self.store[owner].pop(key, None) is None:
				return
			if not self.store[owner]:
				del self.store[owner]
		self.changed()

	def set_enabled(self, owner: str, key: str, enabled: bool) -> None:
		entry = self.get(owner, key)
		if entry is not None and entry.enabled != bool(enabled):
			entry.enabled = bool(enabled)
			self.changed()

	## CHANGE NOTIFICATION

	def subscribe(self, callback: Callable) -> None:
		if callback not in self._listeners:
			self._listeners.append(callback)

	def unsubscribe(self, callback: Callable) -> None:
		if callback in self._listeners:
			self._listeners.remove(callback)

	def changed(self) -> None:
		"""Tell anything showing these entries to rebuild."""
		for callback in list(self._listeners):
			try:
				callback()
			except Exception as e:
				# A listener that throws is dropped rather than left to throw
				# on every future registration.
				self._listeners.remove(callback)
				self.client.log("warning", f"[QuickAccess] Listener failed and was "
											f"removed: {e}")
