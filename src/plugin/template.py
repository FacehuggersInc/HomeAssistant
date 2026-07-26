from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.main import Client
    from src.settings import Settings

class Plugin:
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