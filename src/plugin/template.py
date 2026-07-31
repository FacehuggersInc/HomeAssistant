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

	def sibling(self, dotted: str):
		"""
		A module from this plugin's own folder, by name.

			render_page = self.sibling("api.feeds_page").render_page

		Use this instead of `from .api.thing import x` for anything imported
		LATE - from inside a request handler or a button press rather than at
		module level.

		A relative import resolves through sys.modules and the package's
		__path__, which the loader sets up. That works, but it depends on the
		plugin having been registered as a package in the way this particular
		install arranged - and when it is not, the failure arrives as
		"No module named 'X.api.thing'" from inside an endpoint, long after
		the plugin loaded perfectly. Loading by file path asks the filesystem
		instead, which is a question with one answer.
		"""
		import importlib.util as _util
		import sys as _sys
		from pathlib import Path

		here = Path(_sys.modules[type(self).__module__].__file__).parent
		parts = str(dotted or "").split(".")
		target = here.joinpath(*parts).with_suffix(".py")
		if not target.is_file():
			raise ImportError(
				f"{type(self).__name__} has no {dotted}: {target} is not on "
				f"this install. The file ships with the plugin, so it is "
				f"missing rather than optional - re-extract or re-pull.")

		# Cached under a name of its own, so two calls hand back one module
		# and anything it holds at module level survives between them.
		key = f"__plugin_sibling__{here.name}.{dotted}"
		if key in _sys.modules:
			return _sys.modules[key]

		spec = _util.spec_from_file_location(key, target)
		module = _util.module_from_spec(spec)
		_sys.modules[key] = module
		spec.loader.exec_module(module)
		return module

	def verify_siblings(self) -> list:
		"""
		Every file this plugin loads with sibling(), checked once at load.

		Read out of its own source rather than from a list somebody maintains,
		because a list somebody maintains is a list that goes stale.

		The point is timing. A missing file is otherwise a 500 the first time
		somebody presses the button that needs it - which may be weeks after
		the install that dropped it, and looks like the button being broken
		rather than the install being incomplete.
		"""
		import re as _re
		import sys as _sys
		from pathlib import Path

		try:
			source_file = Path(_sys.modules[type(self).__module__].__file__)
		except (KeyError, AttributeError, TypeError):
			return []

		here = source_file.parent
		try:
			source = source_file.read_text(encoding="utf-8")
		except OSError:
			return []

		missing = []
		for dotted in set(_re.findall(r"sibling\(\s*[\"\']([\w.]+)[\"\']",
									  source)):
			target = here.joinpath(*dotted.split(".")).with_suffix(".py")
			if not target.is_file():
				missing.append(str(target))
		return sorted(missing)

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