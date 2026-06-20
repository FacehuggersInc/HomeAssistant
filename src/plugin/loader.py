from src import os, time, json, ILUtil, Path, sys, ModuleType, tomllib
import gc
import inspect

from src.enums import Asset

from src.settings import Settings

from src.plugin.template import Plugin
from src.plugin.carryover import PluginCarryover

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

	def scan_plugin_toml(self, plugin_path: Path) -> dict | None:
		"""
		Read ONLY plugin.toml — no module import, no code execution.
		Used by resolve_load_order() to build the dependency graph
		before any plugin code actually runs. This has to be a separate,
		lighter pass than load_toml() because load_toml() is normally
		called AFTER a plugin's module is already imported (it wants
		the Python class name for log messages) — but dependency
		ordering has to be decided BEFORE any plugin is imported at all,
		so nothing here can depend on that having happened yet.

		Returns the raw toml dict (or None if missing/invalid), plus the
		plugin_path attached under "_scan_path" for the caller's
		convenience.
		"""
		toml_path = plugin_path / "plugin.toml"
		if not toml_path.exists():
			return None
		try:
			with open(toml_path, "rb") as f:
				config = tomllib.load(f)
		except Exception as e:
			self.client.log("warning", f"[PluginManager] Failed to pre-scan '{plugin_path.name}/plugin.toml': {e}")
			return None

		plugin_section = config.get("plugin")
		if not plugin_section or "key" not in plugin_section:
			return None

		config["_scan_path"] = plugin_path
		return config

	def resolve_load_order(self, plugin_dirs: list[Path]) -> list[Path]:
		"""
		Pre-scan every plugin.toml across all plugin directories (no
		module imports yet) and compute a load order that respects:

		  1. dependencies — a plugin listed in another plugin's
		     `dependencies` array loads first, whenever possible.
		  2. order — an integer tiebreaker among plugins with no
		     dependency relationship to each other; lower loads first.
		     Defaults to 0 if omitted.

		plugin.toml shape:

		    [plugin]
		    name = "My Plugin"
		    key  = "myplugin"
		    order = 10                       # optional, default 0
		    dependencies = ["otherplugin"]   # optional, default []

		Implementation: Kahn's algorithm (BFS topological sort). At each
		step, every plugin with zero remaining unmet dependencies is a
		candidate; candidates are sorted by `order` (then by key, for a
		stable result when order ties) before being added to the
		schedule, which is what makes `order` act as a tiebreaker
		rather than an absolute ranking — a real dependency edge always
		wins over `order` alone.

		Plugins with a missing/invalid plugin.toml, or a dependency that
		never resolves to a real plugin, are scheduled last (in folder
		order) with a warning rather than being dropped — a plugin
		failing to declare itself correctly shouldn't silently prevent
		every OTHER plugin from loading.

		A circular dependency is also not fatal: whatever's left over
		once no more zero-dependency candidates exist is appended in
		whatever order remains, with a warning identifying the cycle as
		best as can be determined.
		"""
		# 1. Pre-scan every plugin folder across every plugin directory
		scanned: dict[str, dict] = {}      # key -> toml config
		unscannable: list[Path] = []       # paths with no valid plugin.toml

		for plugin_dir in plugin_dirs:
			if not plugin_dir.exists():
				continue
			for plugin_path in plugin_dir.iterdir():
				if plugin_path.name.endswith(".DISABLED"):
					self.client.log("info", f"[PluginManager] Plugin '{plugin_path.name}' was not loaded due to '.DISABLED' tag")
					continue
				if not plugin_path.is_dir() or not (plugin_path / "main.py").exists():
					continue

				config = self.scan_plugin_toml(plugin_path)
				if config is None:
					unscannable.append(plugin_path)
					continue

				key = config["plugin"]["key"]
				if key in scanned:
					self.client.log("warning", f"[PluginManager] Duplicate plugin key '{key}' found at '{plugin_path}' — keeping the first one scanned ('{scanned[key]['_scan_path']}')")
					continue
				scanned[key] = config

		# 2. Build the dependency graph
		# dependencies[key] = set of keys that must load before `key`
		dependencies: dict[str, set] = {}
		for key, config in scanned.items():
			deps = config.get("plugin", {}).get("dependencies", []) or []
			resolved_deps = set()
			for dep in deps:
				if dep in scanned:
					resolved_deps.add(dep)
				else:
					self.client.log("warning", f"[PluginManager] Plugin '{key}' depends on '{dep}', which was not found — ignoring that dependency")
			dependencies[key] = resolved_deps

		def get_order(key: str) -> int:
			return int(scanned[key].get("plugin", {}).get("order", 0) or 0)

		# 3. Kahn's algorithm — repeatedly take all currently-resolvable
		# plugins (zero remaining unmet dependencies), sorted by order
		# then key for a stable, predictable result among ties.
		remaining = dict(dependencies)   # mutable copy we'll shrink
		scheduled: list[str] = []

		while remaining:
			ready = [k for k, deps in remaining.items() if not deps]
			if not ready:
				# Circular dependency — nothing left has zero unmet deps.
				# Break the deadlock by scheduling whatever has the FEWEST
				# remaining unmet deps (best-effort) rather than refusing
				# to load anything at all.
				cycle_keys = list(remaining.keys())
				self.client.log("warning", f"[PluginManager] Circular or unresolvable plugin dependency detected among: {cycle_keys} — loading in best-effort order")
				ready = sorted(remaining.keys(), key=lambda k: (len(remaining[k]), get_order(k), k))[:1]

			ready.sort(key=lambda k: (get_order(k), k))
			for key in ready:
				scheduled.append(key)
				del remaining[key]

			for deps in remaining.values():
				deps.difference_update(ready)

		# 4. Build the final path list: resolved plugins in dependency
		# order, then anything that failed to scan at all (folder order,
		# since there's no metadata to sort those by)
		ordered_paths = [scanned[key]["_scan_path"] for key in scheduled]
		ordered_paths.extend(unscannable)

		if scheduled:
			self.client.log("info", f"[PluginManager] Resolved plugin load order: {scheduled}")

		return ordered_paths

	def load_plugins_from_directories(self, plugin_dirs: list[Path]):
		ordered_paths = self.resolve_load_order(plugin_dirs)
		for plugin_path in ordered_paths:
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
					config['path'] = plugin_path / "plugin.toml"
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
	def _accepts_carryover(self, bound_method) -> bool:
		"""
		True if a plugin's load()/reload()/unload() method accepts a
		positional argument for carryover, beyond self. Lets plugins
		written before PluginCarryover existed keep their old
		zero-argument signatures working unchanged — we only pass
		carryover through if the method actually has a parameter slot
		for it.
		"""
		try:
			sig = inspect.signature(bound_method)
			return len(sig.parameters) >= 1
		except (TypeError, ValueError):
			return False

	def unload_plugin(self, plugin_key: str, quick:bool = False, carryover=None) -> bool:
		# 1. Find the plugin instance
		plugin = self.plugins.get(plugin_key)
		if not plugin:
			self.client.log("warning", f"[PluginManager] Plugin '{plugin_key}' not found when trying to unload it.")
			return False

		# 2. Call shutdown/unload hook if available
		# carryover is the PluginCarryover created by reload_plugin() for
		# this one reload cycle, or None for a normal/shutdown unload
		# (see PluginCarryover's docstring). Plugins whose unload() was
		# written before this existed still work unchanged — the call
		# below only passes carryover if the plugin's unload() actually
		# accepts a parameter for it.
		if hasattr(plugin, "unload") and callable(plugin.unload):
			try:
				if carryover is not None and self._accepts_carryover(plugin.unload):
					plugin.unload(carryover)
				else:
					plugin.unload()
			except Exception as e:
				self.client.log("error", f"[PluginManager] Error during the unloading of a hook : {plugin_key}", include_traceback = True)

		# 3. Save Plugin Settings
		if hasattr(plugin, "settings"):
			path = plugin.config["settings"]["path"]
			with open(path, "w") as jsonfile:
				json.dump(plugin.settings.to_dict(), jsonfile, indent = 4)
			self.client.log("info", f"[PluginManager] '{plugin_key}' Settings Saved.")

		# etc. If not Quick Unloading; Remove Mixins, Remove from Plugin Registry | essentially this is for hot reloading, if quick is true, its because the app is shutting down
		if not quick:
			# Restore & rewrap all mixin targets
			self.client.MIXINS.remove_plugin_mixins( plugin_key )

			# etc a. Auto Unload Registered API Endpoints
			self.client.API_REGISTRY.unregister(plugin_key)

			# etc a2. Auto Unload Registered Pages
			# This is what fixes the leftover blank window during hot
			# reload — pages registered with this plugin as owner
			# (e.g. add_page(..., owner=plugin_key)) are now torn down
			# automatically here, the same way API endpoints already
			# were. Without this, a plugin's old page instance/class
			# stayed registered and alive (just hidden) indefinitely,
			# even after the plugin module itself was removed from
			# sys.modules below.
			self.client.PAGES.unregister(plugin_key)

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
			# Tell every OTHER plugin that this one is about to be
			# unloaded, BEFORE any teardown actually happens. The event
			# data is just the plugin's key — any plugin that depends on
			# or cooperates with the one being reloaded (shared state via
			# self.client.public, a feature it registered onto another
			# plugin's page, etc.) gets a chance to react (pause, detach,
			# show its own message) while the reloading plugin is still
			# fully intact, rather than discovering it's gone after the
			# fact with no warning.
			self.client.iterate_event_callables("on_plugin_reloading", plugin_key)

			# Remember what page we were on BEFORE unloading. unload_plugin
			# now properly destroys the current page if it belongs to this
			# plugin (see PageRegistry._destroy_instance_if_current), which
			# sets self.client.PAGE to None — reading PAGE.name AFTER
			# unloading would crash exactly in the common case of reloading
			# a plugin while looking at one of its own pages.
			previous_page = self.client.PAGE.name if self.client.PAGE else "#root"

			# unload_plugin() deletes this plugin from self.plugins, so
			# its display name has to be captured BEFORE that happens —
			# self.plugin_name(plugin_key) would otherwise raise a KeyError
			# once we try to build the "Reloading '...'" message below.
			plugin_display_name = self.plugin_name(plugin_key)

			# One PluginCarryover per reload cycle. The OLD plugin
			# instance's unload() gets it first to stash whatever it
			# wants to survive — the NEW instance's load() and reload()
			# get the exact same object back afterward. See
			# src/plugin/carryover.py for the full contract.
			carryover = PluginCarryover()

			self.unload_plugin( plugin_key, carryover=carryover )

			# Show a clearly DIFFERENT message than the generic "no home
			# page installed" one while this plugin is actually mid-reload
			# — there's a real gap here (the sleep below, plus
			# load_plugin() reading the module from disk) where the old
			# page is already destroyed and the new one doesn't exist yet.
			# Without this, that gap either showed a stale frame or the
			# same "nothing registered" message you'd see if the plugin
			# were permanently gone, which made it impossible to tell the
			# two situations apart at a glance.
			self.client.goto("#root", data={
				"title": f"Reloading '{plugin_display_name or plugin_key}'…",
				"body":  "This plugin is being reloaded and will be back shortly.",
				"show_hint": False,
			}, override=True)

			time.sleep(1)
			self.load_plugin( plugin_path )

			reloaded_plugin = self.plugins[plugin_key]
			if self._accepts_carryover(reloaded_plugin.load):
				reloaded_plugin.load(carryover)
			else:
				reloaded_plugin.load()

			# handled_navigation lets a plugin's unload() opt out of the
			# fallback navigation below entirely — useful if load()/built()
			# already moved somewhere specific and the fallback would just
			# undo that. See PluginCarryover's docstring for the full
			# contract; this is the ONLY point where it's checked, since
			# unload() is the only hook that runs before this.
			if not carryover.get("handled_navigation", False):
				# If the page we were on no longer exists (it belonged to
				# this plugin and got torn down above, and the plugin's
				# fresh load() hasn't re-registered it under the same key
				# for some reason), fall back to root rather than calling
				# goto() with a stale key.
				reload_page = previous_page if self.client.PAGES.has_page(previous_page) else "#root"
				self.client.goto(reload_page, override = True)

			if self.client.BUILT and hasattr(reloaded_plugin, "built"):
				reloaded_plugin.built()
			
			time.sleep(1)

			if self._accepts_carryover(reloaded_plugin.reload):
				reloaded_plugin.reload(carryover)
			else:
				reloaded_plugin.reload()
			
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
			self.client.MIXINS.apply_mixins_to( plugin )

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