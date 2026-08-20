import os
import sys
import gc
import time
import json
import inspect
import tomllib
import importlib.util as ILUtil
from pathlib import Path
from types import ModuleType
from typing import Callable

from src.enums import Asset

from src.settings import Settings, scrub_secrets

from src.plugin.template import Plugin
from src.plugin.carryover import PluginCarryover
# NOT `as deps` - resolve_load_order() has a local `deps`, which would shadow it.
from src.plugin import dependencies as pipdeps
from src.ui.icons import is_icon_path


#Words that make a setting a credential rather than a preference. A log is
#read by whoever is debugging, pasted into an issue, and kept for a week -
#none of which is a place for a token.
_SECRET_WORDS = ("password", "secret", "token", "api_key", "apikey",
                 "credential", "passphrase", "private")


def _setting_leaves(node, prefix: str = "") -> dict:
    """
    Every setting's value, by dotted path.

    A settings file is groups of entries and each entry is a small dict with
    a `value` in it, so the leaves are what to compare - comparing the dicts
    would report a change every time a description was reworded.
    """
    found = {}
    if not isinstance(node, dict):
        return found
    if "value" in node and "type" in node:
        found[prefix] = node.get("value")
        return found
    for key, child in node.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        found.update(_setting_leaves(child, path))
    return found


def _shown(dotted: str, value) -> str:
    """One value, short enough for a log line and never a credential."""
    if any(word in str(dotted).lower() for word in _SECRET_WORDS):
        return "***" if value not in (None, "", []) else "empty"
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _setting_changes(before: dict, after: dict) -> list:
    """
    `(dotted, was, now)` for every setting whose value moved.

    Both sides are read the same way, so a setting an update ADDED shows as
    arriving and one it removed shows as going - which is the other thing
    this file's write-back has been quietly doing.
    """
    old, new = _setting_leaves(before), _setting_leaves(after)
    changes = []
    for dotted in sorted(set(old) | set(new)):
        was = old.get(dotted, "<not set>")
        now = new.get(dotted, "<removed>")
        if was != now:
            changes.append((dotted, was, now))
    return changes


class PendingPlugin:

	def __init__(self, key: str, name: str, path: Path, config: dict,
				 missing: list[str], requirements: list[str], icon=None):
		self.key = key
		self.name = name
		self.path = path
		self.config = config
		self.missing = missing
		self.requirements = requirements
		self.icon = icon
		self.declined = False
		self.error: str | None = None

	def __repr__(self):
		return f"PendingPlugin({self.key}, missing={self.missing})"

class ConflictingPlugin:
	"""
	A plugin folder that will never load, because its key is already taken.

	Keyed by FOLDER rather than by key, which is the whole point: the key is
	the thing it collides on, so two records under it would be one record.

	It used to be dropped with a line in the log. That is the correct decision
	and the wrong way to report it: the folder is on disk, it looks installed,
	and every list said nothing about it - so the answer to "why is my plugin
	not there" was a warning nobody was going to scroll back for.
	"""

	def __init__(self, folder: str, key: str, name: str, path: Path,
				 config: dict, blocked_by: Path, icon=None):
		self.folder = folder
		self.key = key
		self.name = name
		self.path = path
		self.config = config
		self.blocked_by = blocked_by
		self.icon = icon

	@property
	def bundled_winner(self) -> bool:
		"""Whether the folder that won is one that ships with the app."""
		try:
			return "assets" in Path(self.blocked_by).parts and \
				   "bundled" in Path(self.blocked_by).parts
		except Exception:
			return False

	def __repr__(self):
		return f"ConflictingPlugin({self.folder}, key={self.key})"


class PluginManager():
	def __init__(self, client, dirs:list[Asset]):
		self.client = client
		self.dirs = dirs
		self.plugins = Settings()
		self.registered : dict[str, Path] = {}
		# Folders that will never load because their key is already taken.
		# Keyed by folder name - see ConflictingPlugin.
		self.conflicts : dict[str, ConflictingPlugin] = {}

		self.pending : dict[str, PendingPlugin] = {}

	## LOADER
	def get_data_path(self) -> str:
		return self.client.DATA

	def load_plugin(self, plugin_path:Path):
		module = None   # must be bound: the path check below may not run
		if plugin_path.is_dir() and (plugin_path / "main.py").exists():
			# plugin_folder/__init__.py
			try:
				module = self.import_module_from_path(plugin_path / "main.py")
			except Exception as e:
				self.client.log(
					"error",
					f"[PluginManager] Plugin at '{plugin_path.name}' failed to import: {e}"
				)
				return
		if module:	self.register_plugin_classes(module, plugin_path)

	def scan_plugin_toml(self, plugin_path: Path) -> dict | None:
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
					winner = scanned[key]["_scan_path"]
					self.client.log("warning", f"[PluginManager] Duplicate plugin key '{key}' found at '{plugin_path}' — keeping the first one scanned ('{winner}')")
					# Remembered, not merely logged. See ConflictingPlugin.
					self.conflicts[plugin_path.name] = ConflictingPlugin(
						folder     = plugin_path.name,
						key        = key,
						name       = config["plugin"].get("name", plugin_path.name),
						path       = plugin_path,
						config     = config,
						blocked_by = winner,
						icon       = config["plugin"].get("icon", None),
					)
					continue

				# Registered for every scanned plugin, including ones held back
				# below - a plugin blocked on a package should still show its key
				# fields, since the credential is usually needed anyway.
				self.register_secrets(key, config)

				requirements = pipdeps.requirements_of(config)
				if requirements:
					unmet = pipdeps.missing(requirements)
					if unmet:
						self.pending[key] = PendingPlugin(
							key          = key,
							name         = config["plugin"].get("name", key),
							path         = plugin_path,
							config       = config,
							missing      = unmet,
							requirements = requirements,
							icon         = config["plugin"].get("icon", None),
						)
						self.client.log(
							"warning",
							f"[PluginManager] Plugin '{key}' held back — missing packages: {', '.join(unmet)}"
						)
						continue

				scanned[key] = config

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

		remaining = dict(dependencies)   # mutable copy we'll shrink
		scheduled: list[str] = []

		while remaining:
			ready = [k for k, deps in remaining.items() if not deps]
			if not ready:
				cycle_keys = list(remaining.keys())
				self.client.log("warning", f"[PluginManager] Circular or unresolvable plugin dependency detected among: {cycle_keys} — loading in best-effort order")
				ready = sorted(remaining.keys(), key=lambda k: (len(remaining[k]), get_order(k), k))[:1]

			ready.sort(key=lambda k: (get_order(k), k))
			for key in ready:
				scheduled.append(key)
				del remaining[key]

			for deps in remaining.values():
				deps.difference_update(ready)

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
			self.client.log("warning", f"[PluginManager] Could not create an import spec for {qualified_name}.")
			return None

		# Create module and assign to sys.modules with correct qualified name
		module = ILUtil.module_from_spec(spec)
		sys.modules[qualified_name] = module

		# Register the plugin package, and make sure it can find submodules.
		#
		# A relative import inside a plugin - `from .api.feeds_page import ...`
		# - resolves against the package's __path__, not against sys.path, and
		# sys.path is popped below. So a package registered without a __path__
		# works for anything imported while the module is executing and fails
		# for anything imported later, from inside a request handler. That is
		# the shape of "No module named 'FeedReader.api.feeds_page'" from an
		# endpoint on a plugin that loaded perfectly.
		#
		# Re-registered when an existing entry has no __path__, since a broken
		# one is worse than none: the import machinery stops there.
		existing = sys.modules.get(plugin_folder_name)
		if existing is None or not getattr(existing, "__path__", None):
			init_file = plugin_dir / "__init__.py"
			package_spec = ILUtil.spec_from_file_location(
				plugin_folder_name, init_file,
				submodule_search_locations=[str(plugin_dir)])
			if package_spec and package_spec.loader:
				package_module = ILUtil.module_from_spec(package_spec)
				sys.modules[plugin_folder_name] = package_module
				try:
					package_spec.loader.exec_module(package_module)
				except FileNotFoundError:
					# No __init__.py at all. The module object still carries
					# the search path, which is the part that matters.
					pass

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

					config = self.load_toml(plugin_path, plugin_name)
					config['path'] = plugin_path / "plugin.toml"
					if not config: return

					if config.get("plugin"):
						readme = config["plugin"].get("readme")
						if readme:
							config["plugin"]["readme"] = self.combine_paths(plugin_path.as_posix(), readme)
						else:
							for candidate in ("README.md", "readme.md", "Readme.md", "README.MD"):
								found = plugin_path / candidate
								if found.exists():
									config["plugin"]["readme"] = str(found)
									break

						icon_value = config["plugin"].get("icon")
						if icon_value and is_icon_path(icon_value):
							config["plugin"]["icon"] = self.combine_paths(plugin_path.as_posix(), icon_value)
						elif not icon_value:
							config["plugin"]["icon"] = "extension"

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

					# Said at load, not when somebody presses the button.
					#
					# A file the plugin loads with sibling() and which is not
					# on this install is otherwise a 500 the first time that
					# button is used - possibly weeks after the incomplete
					# install, and looking like the button is broken rather
					# than the copy being short of a file.
					try:
						absent = plugin_instance.verify_siblings()
					except Exception:
						absent = []
					for path in absent:
						self.client.log(
							"error",
							f"[PluginManager] {key} expects {path}, which is "
							f"not here. Anything needing it will fail.")

					return

				except Exception as e:
					self.client.log("error", f"[PluginManager] Failed to instantiate '{plugin_name}' : {e}")
	


	## UN-LOADER
	def _accepts_carryover(self, bound_method) -> bool:
		try:
			sig = inspect.signature(bound_method)
			return len(sig.parameters) >= 1
		except (TypeError, ValueError):
			return False

	def _merge_into_template(self, template: dict, values: dict) -> dict:
		"""
		The template's shape, carrying the values it still has a place for.

		A key the template no longer declares is dropped; one it has gained
		keeps its default. Only `value` is taken across - a description or a
		set of options changed by an update should be the update's.
		"""
		if not isinstance(template, dict) or not isinstance(values, dict):
			return template

		out = {}
		for key, node in template.items():
			held = values.get(key)
			if isinstance(node, dict) and "type" in node and "value" in node:
				out[key] = dict(node)
				if isinstance(held, dict) and "value" in held:
					out[key]["value"] = held["value"]
			elif isinstance(node, dict):
				out[key] = self._merge_into_template(node, held if isinstance(held, dict) else {})
			else:
				out[key] = node
		return out

	def unload_plugin(self, plugin_key: str, quick:bool = False, carryover=None) -> bool:
		# 1. Find the plugin instance
		plugin = self.plugins.get(plugin_key)
		if not plugin:
			self.client.log("warning", f"[PluginManager] Plugin '{plugin_key}' not found when trying to unload it.")
			return False

		if not quick and carryover is None:
			dependants = self.get_dependants(plugin_key)
			if dependants:
				self.client.log("warning", f"[PluginManager] Refused to unload '{plugin_key}' — still required by currently loaded plugin(s): {dependants}")
				return False

		self.client.iterate_event_callables("on_plugin_unload", plugin_key, True)

		if hasattr(plugin, "unload") and callable(plugin.unload):
			try:
				if carryover is not None and self._accepts_carryover(plugin.unload):
					plugin.unload(carryover)
				else:
					plugin.unload()
			except Exception as e:
				self.client.log("error", f"[PluginManager] Error during the unloading of a hook : {plugin_key}", include_traceback = True)

		# Services this plugin registered.
		#
		# `carryover` is the loader's own discriminator between a plugin
		# coming straight back and a plugin going away, so it is what decides
		# whether a service that asked to survive a reload gets to. On a
		# genuine unload everything stops, with no opt-out.
		try:
			self.client.SERVICES.unregister(
				plugin_key, reloading=carryover is not None)
		except Exception as e:
			self.client.log("warning",
				f"[PluginManager] Could not release '{plugin_key}' services: {e}")

		# 3. Save Plugin Settings
		if hasattr(plugin, "settings"):
			path = plugin.config["settings"]["path"]
			# Scrubbed: a plugin's own settings.json is written here on every
			# unload, and a credential must never land in it.
			values = scrub_secrets(plugin.settings.to_dict())

			# Shaped by what is on disk now, not by what was in memory.
			#
			# This file is both the template and the store. An update replaces
			# it, but the running app still holds the previous shape - so the
			# write-back on exit put every removed setting straight back, and a
			# setting deleted in an update reappeared on the next launch
			# looking like the update had not applied.
			#
			# Re-reading first means the new template wins on structure and the
			# old memory only supplies values for keys that still exist.
			template = {}
			try:
				with open(path, "r") as current_file:
					template = json.load(current_file)
				values = self._merge_into_template(template, values)
			except Exception as e:
				self.client.log("warning",
					f"[PluginManager] Could not re-read '{plugin_key}' settings "
					f"before saving, writing what was in memory: {e}")

			# What actually changed, before it is written over.
			#
			# "Settings Saved" is true of a save that changed nothing and of
			# one that changed six things, and the log could not tell them
			# apart - so a setting that moved on its own, or one somebody set
			# and forgot, left no trace at all.
			changes = _setting_changes(template, values)

			with open(path, "w") as jsonfile:
				json.dump(values, jsonfile, indent = 4)

			if changes:
				for dotted, before, after in changes:
					self.client.log(
						"info",
						f"[PluginManager] '{plugin_key}' {dotted}: "
						f"{_shown(dotted, before)} -> {_shown(dotted, after)}")
				self.client.log(
					"info",
					f"[PluginManager] '{plugin_key}' Settings Saved "
					f"({len(changes)} changed).")
			else:
				self.client.log(
					"info",
					f"[PluginManager] '{plugin_key}' Settings Saved "
					f"(nothing changed).")

		# etc. If not Quick Unloading; Remove Mixins, Remove from Plugin Registry | essentially this is for hot reloading, if quick is true, its because the app is shutting down
		if not quick:
			# Restore & rewrap all mixin targets
			self.client.MIXINS.remove_plugin_mixins( plugin_key )

			# etc a. Auto Unload Registered API Endpoints
			self.client.API.unregister(plugin_key)

			self.client.PAGES.unregister(plugin_key)

			# Entries are descriptions, not widgets, so a stale one would keep
			# rendering a button whose callback points into an unloaded module.
			self.client.QUICK.unregister(plugin_key)

			# etc b. Remove from plugin registry
			#
			# ...and put it back on the PENDING list on the way out.
			#
			# An unloaded plugin used to disappear entirely: it is gone from
			# `plugins` and was never in `pending`, so it vanished from the
			# settings list, from the dashboard, and from every count - with
			# no way to load it again short of restarting the panel. Unloading
			# something should stop it, not hide it.
			try:
				config = plugin.config.to_dict() if hasattr(plugin.config, "to_dict") \
					else dict(plugin.config)
			except Exception:
				config = {}
			path = self.registered.get(plugin_key)
			if path is not None and plugin_key not in self.pending:
				section = config.get("plugin") or {}
				self.pending[plugin_key] = PendingPlugin(
					key          = plugin_key,
					name         = section.get("name", plugin_key),
					path         = path,
					config       = config,
					# Nothing is missing - it ran a moment ago. This is
					# "installed and stopped", which is a different state
					# from "held back for a package" and reads the same way
					# on the list: present, and not running.
					missing      = [],
					requirements = [],
					icon         = section.get("icon", None),
				)
			del self.plugins[plugin_key]
			self.client.SKILLS.un_register( plugin_key )
			self.client.public.clear( plugin_key )
			# Forgets the declaration only. The stored value stays, so a
			# reload does not silently require retyping the credential.
			self.client.SECRETS.unregister(plugin_key)

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
			self.client.iterate_event_callables("on_plugin_reloading", plugin_key)

			previous_page = self.client.PAGE.name if self.client.PAGE else "#root"

			plugin_display_name = self.plugin_name(plugin_key)

			carryover = PluginCarryover()

			self.unload_plugin( plugin_key, carryover=carryover )

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

			if not carryover.get("handled_navigation", False):
				reload_page = previous_page if self.client.PAGES.has_page(previous_page) else "#root"
				self.client.goto(reload_page, override = True)

			if self.client.BUILT and hasattr(reloaded_plugin, "built"):
				reloaded_plugin.built()
			
			time.sleep(1)

			if self._accepts_carryover(reloaded_plugin.reload):
				reloaded_plugin.reload(carryover)
			else:
				reloaded_plugin.reload()

			# Anything held across the gap that the new instance did not
			# register again. Held services are opt-in and re-adopting one is
			# the plugin's job; this is what stops forgetting from leaving a
			# process running against a module nobody can reach.
			try:
				self.client.SERVICES.reap(plugin_key)
			except Exception as e:
				self.client.log("warning",
					f"[PluginManager] Could not reap '{plugin_key}' services: {e}")
			
			self.client.simple_notify(
				"extension",
				"Plugin Manager",
				f"'{self.plugin_name(reloaded_plugin)}' has been Reloaded."
			)


	
	## SECRETS

	def register_secrets(self, plugin_key: str, config: dict) -> list[str]:
		"""
		Declare a plugin's secret KEY NAMES. Accepts either shape:

			[secrets]
			keys = ["EXAMPLE_API_KEY"]

			[plugin]
			secrets = ["EXAMPLE_API_KEY"]

		Values never appear in a toml - only names.
		"""
		names = []

		def collect(entry):
			if isinstance(entry, str):
				names.append(entry)
			elif isinstance(entry, (list, tuple)):
				names.extend(str(k) for k in entry)

		section = (config or {}).get("secrets")
		if isinstance(section, dict):
			collect(section.get("keys", section.get("key")))
		else:
			collect(section)

		plugin_section = (config or {}).get("plugin", {})
		if isinstance(plugin_section, dict):
			collect(plugin_section.get("secrets"))

		if not names:
			return []
		return self.client.SECRETS.register_many(plugin_key, names)

	## REGISTRATIONS

	def registrations(self, plugin_key: str) -> list[tuple[str, list[str]]]:
		"""
		Everything a plugin currently owns, as (registry name, entries).

		Only non-empty registries are returned, in a fixed order. Each list is
		already formatted for display - the settings page renders it verbatim.
		"""
		client = self.client
		groups: list[tuple[str, list[str]]] = []

		def add(name: str, entries):
			entries = [e for e in entries if e]
			if entries:
				groups.append((name, entries))

		try:
			add("Pages", [
				f"{entry.key}   {entry.display}" if entry.display and entry.display != entry.key
				else entry.key
				for entry in client.PAGES.entries_for(plugin_key)
			])
		except Exception:
			pass

		try:
			add("API Endpoints", [f"/public/{name}" for name in
								  client.API.endpoints_for(plugin_key)])
		except Exception:
			pass

		try:
			add("Public Registry", client.public.names_for(plugin_key))
		except Exception:
			pass

		try:
			add("Skills", sorted(
				getattr(skill, "key", str(skill))
				for skill in client.SKILLS.registered.get(plugin_key, [])
			))
		except Exception:
			pass

		try:
			# Running or not, and flagged when it outlived its owner. A thing
			# that can survive its plugin has to be visible somewhere, or
			# opting out of cleanup is a leak nobody audits.
			add("Services", [
				"   ".join(part for part in (
					entry.name,
					f"({entry.kind})",
					"running" if entry.is_active() else "stopped",
					"ORPHANED" if entry.orphaned else "",
				) if part)
				for entry in client.SERVICES.entries_for(plugin_key)
			])
		except Exception:
			pass

		try:
			# What this plugin supplies for somebody else, as opposed to what
			# it runs. A claim outlives nothing - it is released on unload -
			# but which plugin is currently providing speech recognition is
			# exactly the thing somebody goes looking for.
			add("Provides", [
				f"{held.name}   {held.description}".rstrip()
				for held in client.SERVICES.providers_for(plugin_key)
			])
		except Exception:
			pass

		try:
			add("Mixins", [f"{target}   ({when})" for target, when in
						   client.MIXINS.mixins_for(plugin_key)])
		except Exception:
			pass

		try:
			# Names and whether they are set - never the values.
			add("Secrets", [
				f"{name}   ({client.SECRETS.status(name)})"
				for name in client.SECRETS.keys_for(plugin_key)
			])
		except Exception:
			pass

		try:
			add("Pip Packages", self.plugin_requirements(plugin_key))
		except Exception:
			pass

		return groups

	def registration_count(self, plugin_key: str) -> int:
		return sum(len(entries) for _, entries in self.registrations(plugin_key))

	## DEPENDENCIES

	def conflicting_plugins(self) -> list:
		"""Folders held back by a key clash, newest scan last."""
		return sorted(self.conflicts.values(), key=lambda c: c.name.lower())

	def pending_plugins(self, include_declined: bool = True) -> list[PendingPlugin]:
		items = [p for p in self.pending.values()
				 if include_declined or not p.declined]
		return sorted(items, key=lambda p: p.name.lower())

	def plugin_requirements(self, plugin_key: str) -> list[str]:
		if plugin_key in self.pending:
			return list(self.pending[plugin_key].requirements)
		plugin = self.plugins.get(plugin_key, None)
		if plugin is None or not hasattr(plugin, "config"):
			return []
		try:
			return pipdeps.requirements_of(plugin.config.to_dict())
		except AttributeError:
			return pipdeps.requirements_of(plugin.config)

	def other_plugin_requirements(self, exclude_key: str) -> list[str]:
		out: list[str] = []
		for _, key in self.get_plugins():
			if key != exclude_key:
				out.extend(self.plugin_requirements(key))
		for key, pending in self.pending.items():
			if key != exclude_key:
				out.extend(pending.requirements)
		return out

	def install_pending(self, plugin_key: str,
						log: Callable[[str], None] = None) -> tuple[bool, str]:
		pending = self.pending.get(plugin_key)
		if not pending:
			return False, f"'{plugin_key}' is not waiting on any packages"

		def _log(msg: str) -> None:
			self.client.log("info", f"[PluginManager][pip] {msg}")
			if log:
				log(msg)

		try:
			ok, output = pipdeps.install(pending.missing, _log)
		except pipdeps.DependencyError as e:
			pending.error = str(e)
			self.client.log("error", f"[PluginManager] {e}")
			return False, str(e)

		if not ok:
			pending.error = "pip install failed — see the log for details"
			return False, output

		still_missing = pipdeps.missing(pending.requirements)
		if still_missing:
			pending.missing = still_missing
			pending.error = f"still missing after install: {', '.join(still_missing)}"
			return False, pending.error

		self.client.call_on_ui(lambda: self.load_pending_plugin(plugin_key))
		return True, output

	def discover(self, plugin_path) -> str:
		"""
		Notice a plugin folder that arrived after startup. Returns its key.

		The scan that fills `pending` runs once, at boot, over the folders
		that existed then. A plugin uploaded while the panel is running is on
		disk and invisible to everything - `load_pending_plugin` refuses it
		because it is not pending, and it does not appear in any list until
		the next restart.

		Returns "" when the folder is not a plugin, which is a fact about the
		folder rather than an error: the caller has just written it and wants
		to know whether it can be offered.
		"""
		from pathlib import Path
		plugin_path = Path(plugin_path)
		if not plugin_path.is_dir() or not (plugin_path / "main.py").exists():
			return ""

		config = self.scan_plugin_toml(plugin_path)
		if config is None:
			self.client.log("warning", f"[PluginManager] '{plugin_path.name}' "
									   f"has no readable plugin.toml.")
			return ""

		key = config["plugin"]["key"]
		if key in self.plugins:
			running = getattr(self, "registered", {}).get(key)
			if running is None or Path(running).name == plugin_path.name:
				# The same folder, or a folder that cannot be identified. Its
				# files may have changed, which is a reload rather than a
				# discovery.
				#
				# An unknown path is treated as the same folder on purpose: a
				# false conflict blocks a plugin that works, and a missed one
				# is caught by the scan on the next start.
				return key
			# A DIFFERENT folder claiming a key that is already running. The
			# scan gives the key to whichever folder is read first, so this
			# one can never load - recorded as a conflict so it is listed and
			# explained rather than silently ignored.
			self.conflicts[plugin_path.name] = ConflictingPlugin(
				folder     = plugin_path.name,
				key        = key,
				name       = config["plugin"].get("name", plugin_path.name),
				path       = plugin_path,
				config     = config,
				blocked_by = running or Path(key),
				icon       = config["plugin"].get("icon", None),
			)
			self.client.log(
				"warning",
				f"[PluginManager] '{plugin_path.name}' claims the key '{key}', "
				f"which is already loaded from "
				f"'{Path(running).name if running else key}'. It cannot load.")
			return ""

		if key in self.pending:
			pending = self.pending[key]
			if Path(pending.path).name != plugin_path.name:
				self.conflicts[plugin_path.name] = ConflictingPlugin(
					folder     = plugin_path.name,
					key        = key,
					name       = config["plugin"].get("name", plugin_path.name),
					path       = plugin_path,
					config     = config,
					blocked_by = pending.path,
					icon       = config["plugin"].get("icon", None),
				)
				self.client.log(
					"warning",
					f"[PluginManager] '{plugin_path.name}' claims the key "
					f"'{key}', which belongs to '{Path(pending.path).name}'.")
				return ""

		self.register_secrets(key, config)
		requirements = pipdeps.requirements_of(config)
		self.pending[key] = PendingPlugin(
			key          = key,
			name         = config["plugin"].get("name", key),
			path         = plugin_path,
			config       = config,
			missing      = pipdeps.missing(requirements) if requirements else [],
			requirements = requirements,
			icon         = config["plugin"].get("icon", None),
		)
		self.client.log("info", f"[PluginManager] Found a new plugin "
								f"'{key}' at '{plugin_path.name}'.")
		return key

	def load_pending_plugin(self, plugin_key: str) -> bool:
		pending = self.pending.get(plugin_key)
		if not pending:
			return False

		try:
			self.load_plugin(pending.path)
		except Exception as e:
			pending.error = f"failed to import: {e}"
			self.client.log("error", f"[PluginManager] Plugin '{plugin_key}' failed to import after install: {e}")
			return False

		plugin = self.plugins.get(plugin_key, None)
		if plugin is None:
			pending.error = "module imported but registered no Plugin subclass"
			self.client.log("error", f"[PluginManager] {pending.error} ('{plugin_key}')")
			return False

		del self.pending[plugin_key]

		try:
			if self._accepts_carryover(plugin.load):
				plugin.load(None)
			else:
				plugin.load()
			self.client.MIXINS.apply_mixins_to(plugin)
			if self.client.BUILT and hasattr(plugin, "built"):
				plugin.built()
		except Exception as e:
			self.client.log("error", f"[PluginManager] Plugin '{plugin_key}' failed during load after install: {e}")
			self.client.simple_notify("error", "Plugin Manager",
									  f"'{pending.name}' installed but failed to start.")
			return False

		self.client.log("info", f"[PluginManager] Plugin '{plugin_key}' installed and loaded.")
		self.client.simple_notify("extension", "Plugin Manager",
								  f"'{pending.name}' installed and loaded.")
		return True

	def uninstall_plugin_packages(self, plugin_key: str,
								  log: Callable[[str], None] = None) -> tuple[bool, str]:
		specs = self.plugin_requirements(plugin_key)
		if not specs:
			return False, "This plugin does not declare any pip requirements."

		removable, kept = pipdeps.removable_for(specs, self.other_plugin_requirements(plugin_key))

		def _log(msg: str) -> None:
			self.client.log("info", f"[PluginManager][pip] {msg}")
			if log:
				log(msg)

		for name, reason in kept.items():
			_log(f"keeping {name} — {reason}")

		if not removable:
			return False, "Nothing to remove — every package is still needed elsewhere."

		try:
			ok, output = pipdeps.uninstall(removable, _log)
		except pipdeps.DependencyError as e:
			self.client.log("error", f"[PluginManager] {e}")
			return False, str(e)

		if not ok:
			return False, output

		# Unload last: if pip failed we leave the plugin alone entirely.
		if self.has_plugin(plugin_key):
			self.client.call_on_ui(lambda: self._unload_after_uninstall(plugin_key, removable))

		return True, output

	def _unload_after_uninstall(self, plugin_key: str, removed: list[str]) -> None:
		name = self.plugin_name(plugin_key) or plugin_key
		path = self.registered.get(plugin_key)
		config = None
		plugin = self.plugins.get(plugin_key, None)
		if plugin is not None and hasattr(plugin, "config"):
			try:
				config = plugin.config.to_dict()
			except AttributeError:
				config = None

		if not self.unload_plugin(plugin_key):
			self.client.simple_notify(
				"error", "Plugin Manager",
				f"Removed {len(removed)} package(s), but '{name}' could not be unloaded."
			)
			return

		if path is not None and config is not None:
			specs = pipdeps.requirements_of(config)
			self.pending[plugin_key] = PendingPlugin(
				key          = plugin_key,
				name         = name,
				path         = path,
				config       = config,
				missing      = pipdeps.missing(specs),
				requirements = specs,
				icon         = config.get("plugin", {}).get("icon", None),
			)
			self.pending[plugin_key].declined = True

		self.client.simple_notify(
			"extension", "Plugin Manager",
			f"Removed {len(removed)} package(s) and unloaded '{name}'."
		)
		if self.client.PAGE and getattr(self.client.PAGE, "name", "") == "#settings":
			self.client.goto("#settings", override=True)

	## MANAGEMENT
	def has_plugin(self, plugin_key:str) -> bool:
		plugin = self.plugins.get(plugin_key, None)
		if plugin != None:
			return True
		
		return False

	def get_dependencies(self, plugin_key: str) -> list[str]:
		plugin = self.plugins.get(plugin_key)
		if not plugin:
			return []
		return list(plugin.config.get_path("plugin.dependencies", []) or [])

	def get_dependants(self, plugin_key: str) -> list[str]:
		dependants = []
		for other_key, other_plugin in self.plugins.items():
			if other_key == plugin_key:
				continue
			deps = other_plugin.config.get_path("plugin.dependencies", []) or []
			if plugin_key in deps:
				dependants.append(other_key)
		return dependants

	def can_unload(self, plugin_key: str) -> bool:
		return len(self.get_dependants(plugin_key)) == 0

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
		for plugin, key in self.get_plugins():
			self.unload_plugin(key, quick=True)