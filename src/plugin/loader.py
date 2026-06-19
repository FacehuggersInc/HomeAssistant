from src import os, time, json, ILUtil, Path, sys, ModuleType, tomllib
import gc

from src.enums import Asset

from src.settings import Settings

from src.plugin.template import Plugin

class PluginManager():
	def __init__(self, client, dirs:list[Asset]):
		self.client = client
		self.dirs = dirs
		self.plugins = Settings()
		self.registered : dict[str, Path] = {}

	## LOADER
	def get_data_path(self) -> str:
		return self.client.DATA

	def load_plugin(self, plugin_path:Path):
		if plugin_path.is_dir() and (plugin_path / "main.py").exists():
			# plugin_folder/__init__.py
			module = self.import_module_from_path(plugin_path / "main.py")
		if module:	self.register_plugin_classes(module, plugin_path)

	def load_plugins_from_directories(self, plugin_dirs: list[Path]):
		for plugin_dir in plugin_dirs:
			if not plugin_dir.exists(): continue
			for plugin_path in plugin_dir.iterdir():
				if plugin_path.name.endswith(".DISABLED"):
					self.client.log("info", f"[PluginManager] Plugin '{plugin_path.name}' was not loaded due to '.DISABLED' tag")
					continue
				self.load_plugin( plugin_path )

	def import_module_from_path(self, py_file: Path) -> ModuleType | None:
		plugin_dir = py_file.parent
		plugin_folder_name = plugin_dir.name

		# Ensure __init__.py exists
		init_file = plugin_dir / "__init__.py"
		if not init_file.exists():
			init_file.touch()

		sys.path.insert(0, str(plugin_dir.parent))
		qualified_name = f"{plugin_folder_name}.main"

		# Build the spec
		spec = ILUtil.spec_from_file_location(qualified_name, py_file)
		if not spec or not spec.loader:
			print(f"Failed to create spec for {qualified_name}")
			return None

		# Create module and assign to sys.modules with correct qualified name
		module = ILUtil.module_from_spec(spec)
		sys.modules[qualified_name] = module

		# Also register the plugin package if not already present
		if plugin_folder_name not in sys.modules:
			package_spec = ILUtil.spec_from_file_location(plugin_folder_name, plugin_dir / "__init__.py")
			if package_spec and package_spec.loader:
				package_module = ILUtil.module_from_spec(package_spec)
				sys.modules[plugin_folder_name] = package_module
				package_spec.loader.exec_module(package_module)

		spec.loader.exec_module(module)

		sys.path.pop(0)
		return module

	def load_toml(self, plugin_path:Path, plugin_name:str) -> dict:
		if (plugin_path / "plugin.toml").exists():
			try:
				with open(plugin_path / "plugin.toml", "rb") as f:
					config = tomllib.load(f)

				keys = ["plugin"]
				
				has_keys = 0
				for key in config.keys():
					if key in keys: has_keys += 1
				if has_keys == len(keys):
					minimum_attr = ["name", "key"]
					has_minimum = 0
					for key in config["plugin"].keys():
						if key in minimum_attr: has_minimum += 1

					if has_minimum == len(minimum_attr):
						return config
					else:
						return None
				else:
					return None
				
			except Exception as e:
				self.client.log("error", f"[PluginManager] the plugin's '{plugin_name}' plugin.toml file associated with it failed to load: {e}")
				return None

		else:
			self.client.log("warning", f"[PluginManager] plugin '{plugin_name}' has no plugin.toml file associated with it. Cannot Load Plugin!")
			return None

	def has_inheritance_check(self, plugin) -> bool:
		return issubclass(plugin, Plugin) and type(plugin) is not Plugin

	def combine_paths(self, path1: str, path2: str) -> str:
		# Normalize separators
		p1 = Path(path1.strip().replace("\\", os.sep).replace("/", os.sep))
		p2 = Path(path2.strip().replace("\\", os.sep).replace("/", os.sep))

		# If p2 is absolute, it takes precedence
		if p2.is_absolute():
			return str(p2.resolve())

		# Break into parts
		parts1 = list(p1.parts)
		parts2 = list(p2.parts)

		# Try to find the overlap
		overlap_index = None
		for i in range(len(parts1)):
			if parts2 and parts1[i:] == parts2[: len(parts1) - i]:
				overlap_index = i
				break

		if overlap_index is not None:
			combined = Path(*parts1[:overlap_index]) / Path(*parts2)
		else:
			combined = p1 / p2

		return str(combined.resolve())

	def register_plugin_classes(self, module: ModuleType, plugin_path:Path):
		for attr_name in dir(module):
			attr = getattr(module, attr_name)

			# Must be a class
			if isinstance(attr, type) and self.has_inheritance_check(attr) and not "Plugin" == attr_name:
				plugin_name:str = attr_name
				try:
					
					#Instantiate
					plugin_instance = attr()

					#Load Config
					config = self.load_toml(plugin_path, plugin_name)
					if not config: return

					#Get / Load Settings Template 
					if config.get("settings") and config["settings"].get("path"):
						path = config["settings"]["path"]
						path = self.combine_paths(plugin_path.as_posix(), path)
						config["settings"]["path"] = path

						with open(path, "r") as settings_file:
							settings = json.load(settings_file)
							setattr(plugin_instance, "settings", Settings( settings ))
							self.client.log("info", f"[PluginManager][{plugin_name}] Settings Were Loaded ({config["settings"]["path"]})")

					setattr(plugin_instance, "config", Settings( config ))
					setattr(plugin_instance, "client", self.client)
					
					key = config["plugin"]["key"]
					self.plugins[ key ] = plugin_instance
					self.registered[key] = plugin_path

					self.client.log("info", f"[PluginManager] Loaded key:{key}, class:{plugin_name}, name:{config["plugin"]["name"]}")

					return

				except Exception as e:
					self.client.log("error", f"[PluginManager] Failed to instantiate '{plugin_name}' : {e}")
	


	## UN-LOADER
	def unload_plugin(self, plugin_key: str, quick:bool = False) -> bool:
		# 1. Find the plugin instance
		plugin = self.plugins.get(plugin_key)
		if not plugin:
			self.client.log("warning", f"[PluginManager] Plugin '{plugin_key}' not found when trying to unload it.")
			return False

		# 2. Call shutdown/unload hook if available
		if hasattr(plugin, "unload") and callable(plugin.unload):
			try:
				plugin.unload()
			except Exception as e:
				self.client.log("error", f"[PluginManager] Error during the unloading of a hook : {plugin_key} : {e}")

		# 3. Save Plugin Settings
		if hasattr(plugin, "settings"):
			path = plugin.config["settings"]["path"]
			with open(path, "w") as jsonfile:
				json.dump(plugin.settings.to_dict(), jsonfile, indent = 4)
			print(f"{plugin_key} : saved settings")

		# etc. If not Quick Unloading; Remove Mixins, Remove from Plugin Registry | essentially this is for hot reloading, if quick is true, its because the app is shutting down
		if not quick:
			# Restore & rewrap all mixin targets
			self.client.mixin_manager.remove_plugin_mixins( plugin_key )

			# etc a. Auto Unload Registered API Endpoints
			self.client.API_REGISTRY.unregister(plugin_key)

			# etc b. Remove from plugin registry
			del self.plugins[plugin_key]
			self.client.SKILLS.un_register( plugin_key )
			self.client.public.clear( plugin_key )

			# etc c. Remove from sys.modules
			module_name = plugin.__class__.__module__  # e.g. "myplugin.main"
			base_name = module_name.split(".")[0]      # e.g. "myplugin"

			to_remove = [name for name in list(sys.modules.keys()) if name == base_name or name.startswith(base_name + ".")]
			for name in to_remove:
				sys.modules.pop(name, None)

			gc.collect()

			self.client.log("info", f"[PluginManager] Successfully unloaded '{plugin_key}'")

		return True

	def reload_plugin(self, plugin_key:str):
		plugin_path : Path = self.registered.get(plugin_key)
		if plugin_path and plugin_path.exists() and self.plugins.get(plugin_key):
			self.unload_plugin( plugin_key )
			time.sleep(1)
			self.load_plugin( plugin_path )

			reloaded_plugin = self.plugins[plugin_key]
			reloaded_plugin.load()

			#Reloading to re-assign mixins
			reload_page = None
			if self.client.mixin_manager.plugin_has_mixins_on( plugin_key, self.client ):
				self.client.restart()

			elif self.client.mixin_manager.plugin_has_mixins_on( plugin_key, self.client.PAGE ):
				reload_page = "#"
			else:
				if self.client.PAGE.name == "#":
					for sub in self.client.PAGE.sub_pages:
						if self.client.mixin_manager.plugin_has_mixins_on( plugin_key, sub ):
							reload_page = "#"
							break

			if not reload_page == None:
				self.client.goto(reload_page, override = True)
				if self.client.BUILT and hasattr(reloaded_plugin, "built"):
					reloaded_plugin.built()
			
			time.sleep(1)
			
			self.client.simple_notify(
				"extension",
				"Plugin Manager",
				f"'{self.plugin_name(reloaded_plugin)}' has been Reloaded."
			)


	
	## MANAGEMENT
	def has_plugin(self, plugin_key:str) -> bool:
		plugin = self.plugins.get(plugin_key, None)
		if plugin != None:
			return True
		
		return False

	def get_plugins(self) -> list[tuple[Plugin, str]]:
		return [(self.plugins[key], key) for key in self.plugins.keys()]

	def plugin_key(self, plugin:str|Plugin) -> str:
		return self.get_config_value(plugin, "plugin.key")

	def plugin_name(self, plugin:str|Plugin):
		return self.get_config_value(plugin, "plugin.name")

	def get_config_value(self, plugin:str|Plugin, path:str):
		paths = path.split(".")
		if isinstance(plugin, str):
			header = self.plugins[plugin].config[paths[0]]
		else:
			header = plugin.config[paths[0]]
		for path in paths[1:]:
			header = header[path]
		return header

	def load_plugins(self):
		self.client.log("info", "[PluginManager] Loading Plugins ...")
		self.load_plugins_from_directories(self.dirs)
		for plugin, key in self.get_plugins():
			plugin.load()

		for plugin, key in self.get_plugins():
			self.client.mixin_manager.apply_mixins_to( plugin )

	def build_plugins(self):
		self.client.log("info", "[PluginManager] Building Plugins ...")
		for plugin, key in self.get_plugins():
			try:
				plugin.built()
			except Exception as e:
				self.client.log("warning", f"[PluginManager] Plugin '{key}' failed to build: {e}")

	def unload_plugins(self):
		"""A Function to Call Unload and to Save Settings on ALL Plugins. Not for Hot Reloading All Plugins!"""
		for plugin, key in self.get_plugins():
			self.unload_plugin(key, quick=True)