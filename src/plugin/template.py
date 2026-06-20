from src import *

class Plugin:
	def __init__(self):
		self.client : Client #Client Access
		self.settings: Settings #The Public settings for this plugin, gets thrown into the Settings page and is editable from Users
		self.config : Settings #From the Toml file in the Plugin Folder. Your local and private settings

	def load(self):
		"""
		When your Plugin is being Loaded, this is when you have access to self.client
		"""
		pass

	def reload(self):
		"""After your plugin is reloaded (after another load). You dont need to trigger the load function again."""

	def built(self):
		"""When the Application is fully built, this will be called"""
		pass

	def unload(self):
		"""When the Application is Closed or your Plugin is Reloaded"""
		pass
