from src import *

class Plugin:
	def __init__(self):
		self.client : Client

	def load(self):
		"""
		When your Plugin is being Loaded, this is when you have access to self.client
		"""
		pass

	def built(self):
		"""When the Application is fully built, this will be called"""
		pass

	def unload(self):
		"""When the Application is Closed or your Plugin is Reloaded"""
		pass
