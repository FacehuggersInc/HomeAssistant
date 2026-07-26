from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.main import Client
    from src.settings import Settings

class Plugin:
	"""Base class for plugins. See the README for the lifecycle."""

	def plugin_key(self) -> str:
		try:
			return str(self.config["plugin"]["key"])
		except Exception:
			return ""

	def secret(self, key: str, default: str = "") -> str:
		"""
		Value of a secret THIS plugin declared in its plugin.toml.

		Asking for another plugin's key returns the default. Use this rather
		than client.SECRETS or os.getenv, so a key edited in Settings takes
		effect without a restart.
		"""
		return self.client.SECRETS.get_for(self.plugin_key(), key, default)

	def set_secret(self, key: str, value: str) -> bool:
		"""Write a secret this plugin declared. Refused for anything else."""
		return self.client.SECRETS.set_for(self.plugin_key(), key, value)

	def has_secret(self, key: str) -> bool:
		return bool(self.secret(key))

	def __init__(self):
		self.client : Client #Client Access
		self.settings: Settings #The Public settings for this plugin, gets thrown into the Settings page and is editable from Users
		self.config : Settings #From the Toml file in the Plugin Folder. Your local and private settings

	def load(self, carryover=None):
		pass

	def reload(self, carryover=None):
		pass

	def built(self):
		pass

	def unload(self, carryover=None):
		pass