from functools import wraps
from collections import defaultdict

_registry = defaultdict(lambda: {"before": [], "after": []})

def mixin(target_key: str, plugin_key: str, when: str = "before"):
	assert when in ("before", "after"), "when must be 'before' or 'after'"

	def decorator(func):
		_registry[target_key][when].append((func, plugin_key))
		return func

	return decorator

def mixin_target(key: str):
	def decorator(func):
		func._mixin_key = key
		return func

	return decorator


class MixinManager:
	def __init__(self, client):
		self.client = client
		self._patched_targets = {}

	def _run_hook(self, f, plugin, when, target, obj, args, kwargs):
		"""
		One hook, and never more than one thing going wrong.

		Hooks used to be called in a bare loop, so the first one to raise
		took everything after it with it. A `before` hook raising also meant
		the target itself never ran - one plugin's bad line and the page it
		was decorating did not build.

		The damage was invisible as well as wide. Registrations that come
		after the failure simply never happen, and nothing anywhere says a
		hook was skipped: the CoreWidgetsBundle registers its five widget
		templates at the END of its `sub.home` hook, so anything raising
		above them leaves the home page with no sticker, sticky note,
		bookmark, checklist or weather-event widget and no error to explain
		it.

		So each hook is isolated and every failure is written down, naming
		the plugin and the target - the two facts needed to know whose it is
		and what it was decorating.
		"""
		try:
			owner = self.client.PLUGIN.plugins[plugin]
		except KeyError:
			self.client.log(
				"error",
				f"[Mixins] {target}: '{plugin}' declares a hook here "
				f"({when}) but is not a loaded plugin - it was skipped.")
			return
		try:
			f(owner, obj or self.client.PLUGIN, *args, **kwargs)
		except Exception as e:
			self.client.log(
				"error",
				f"[Mixins] {target}: the '{when}' hook from '{plugin}' raised "
				f"{type(e).__name__}: {e} - it did not finish, so anything it "
				f"had left to register is missing.",
				include_traceback=True)

	def _make_wrapper(self, attr, hooks, is_class_method=False):

		target = getattr(attr, "_mixin_key", None) or getattr(attr, "__name__", "?")

		@wraps(attr)
		def wrapper(*args, **kwargs):
			# First arg is usually the instance (self) or class
			obj = args[0] if args else None

			for f, plugin in hooks["before"]:
				self._run_hook(f, plugin, "before", target, obj, args, kwargs)

			result = attr(*args, **kwargs)

			for f, plugin in hooks["after"]:
				self._run_hook(f, plugin, "after", target, obj, args, kwargs)

			return result

		wrapper._is_mixin_wrapped = True
		wrapper._mixin_original = attr
		wrapper._mixin_key = getattr(attr, "_mixin_key", None)
		return wrapper

	def apply_mixins_to(self, obj_or_cls):
		for attr_name in dir(obj_or_cls):
			attr = getattr(obj_or_cls, attr_name)

			if not callable(attr):
				continue

			if getattr(attr, "_is_mixin_wrapped", False):
				continue

			if not hasattr(attr, "_mixin_key"):
				continue

			hooks = _registry.get(attr._mixin_key)
			if not hooks:
				continue

			# Wrap and patch
			is_instance  = not isinstance(obj_or_cls, type)  # instance vs class
			wrapper = self._make_wrapper(attr, hooks, is_instance)

			# Logged, because a mixin that never fires is otherwise completely
			# silent - there is no error, no warning, and nothing on screen.
			# Whether a target was wrapped at all, and with how many hooks, is
			# the first thing anybody needs to know.
			try:
				owners = [k for _, k in hooks["before"] + hooks["after"]]
				self.client.log("debug",
					f"[Mixins] wrapped {attr._mixin_key} "
					f"({len(owners)} hook(s): {', '.join(owners) or 'none'})")
			except Exception:
				pass

			setattr(obj_or_cls, attr_name, wrapper)
			self._patched_targets[(obj_or_cls, attr_name)] = attr
		
		return obj_or_cls

	def plugin_has_mixins_on(self, plugin_key: str, obj_or_cls) -> bool:
		for attr_name in dir(obj_or_cls):
			attr = getattr(obj_or_cls, attr_name)

			if not callable(attr):
				continue

			mixin_key = getattr(attr, "_mixin_key", None)
			if not mixin_key:
				continue

			hooks = _registry.get(mixin_key)
			if not hooks:
				continue

			# Look through before/after hooks for this plugin
			for when in ("before", "after"):
				for _, p in hooks[when]:
					if p == plugin_key:
						return True

		return False

	def mixin_count(self, plugin_key:str):
		total = 0
		for target, hooks in _registry.items():
			before = [(f, p) for f, p in hooks["before"] if p == plugin_key]
			after = [(f, p) for f, p in hooks["after"] if p == plugin_key]
			total += len(before) + len(after)
		return total

	def mixins_for(self, plugin_key: str) -> list[tuple[str, str]]:
		"""(target_key, when) pairs a plugin has attached, sorted."""
		out = []
		for target, hooks in _registry.items():
			for when in ("before", "after"):
				for _, p in hooks[when]:
					if p == plugin_key:
						out.append((target, when))
		return sorted(out)

	def remove_plugin_mixins(self, plugin_key: str):
		for target, hooks in _registry.items():
			hooks["before"] = [(f, p) for f, p in hooks["before"] if p != plugin_key]
			hooks["after"] = [(f, p) for f, p in hooks["after"] if p != plugin_key]

		# Restore all patched targets to originals
		patched = list(self._patched_targets.items())
		self._patched_targets.clear()
		for (obj, attr_name), original in patched:
			setattr(obj, attr_name, original)

		# Reapply with updated registry
		for obj, _ in {k for k, _ in patched}:  # only unique objs
			self.apply_mixins_to(obj)

	def clear_all(self):
		for (obj, attr_name), original in self._patched_targets.items():
			setattr(obj, attr_name, original)
		self._patched_targets.clear()
		_registry.clear()
