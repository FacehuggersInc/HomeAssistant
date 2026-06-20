from src import *

class Plugin:
	def __init__(self):
		self.client : Client #Client Access
		self.settings: Settings #The Public settings for this plugin, gets thrown into the Settings page and is editable from Users
		self.config : Settings #From the Toml file in the Plugin Folder. Your local and private settings

	def load(self, carryover=None):
		"""
		When your Plugin is being Loaded, this is when you have access to self.client

		carryover is a PluginCarryover instance ONLY during a hot reload
		(see PluginManager.reload_plugin) — it's the same object your
		previous instance's unload() received, letting you restore
		anything you stashed there. It is always None on the very first
		load when the application starts, since nothing has been
		unloaded yet to carry anything over from.
		"""
		pass

	def reload(self, carryover=None):
		"""
		After your plugin is reloaded (after another load). You dont need to trigger the load function again.

		Receives the SAME PluginCarryover instance load() did for this
		reload cycle, in case you'd rather restore state here instead
		of (or in addition to) in load().
		"""

	def built(self):
		"""When the Application is fully built, this will be called"""
		pass

	def unload(self, carryover=None):
		"""
		When the Application is Closed or your Plugin is Reloaded

		carryover is a PluginCarryover instance ONLY when this unload is
		part of a hot reload (see PluginManager.reload_plugin) — it is
		None when the whole application is shutting down, since there
		is no future load() to hand anything to in that case. Use
		carryover.set(key, value) to stash anything you want your next
		instance's load()/reload() to receive back.

		Do not stop/close whatever you stash in carryover — the entire
		point is that it survives into the next instance. Only clean up
		things you are NOT carrying over.
		"""
		pass