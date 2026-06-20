from functools import wraps
from collections import defaultdict

_registry = defaultdict(lambda: {"before": [], "after": []})

def mixin(target_key: str, plugin_key: str, when: str = "before"):
	"""Attach mixin to a function under a target key"""
	assert when in ("before", "after"), "when must be 'before' or 'after'"

	def decorator(func):
		_registry[target_key][when].append((func, plugin_key))
		return func

	return decorator

def mixin_target(key: str):
	"""Mark a method as a mixin attach point under a key."""
	def decorator(func):
		func._mixin_key = key
		return func

	return decorator


class MixinManager:
	def __init__(self, client):
		self.client = client
		self._patched_targets = {}

	def _make_wrapper(self, attr, hooks, is_class_method=False):
		"""Create a wrapper with before/after hooks attached"""

		@wraps(attr)
		def wrapper(*args, **kwargs):
			# First arg is usually the instance (self) or class
			obj = args[0] if args else None

			for f, plugin in hooks["before"]:
				f(
					self.client.PLUGIN.plugins[plugin],
					obj or self.client.PLUGIN,  # fall back only if no obj
					*args,
					**kwargs
				)

			result = attr(*args, **kwargs)

			for f, plugin in hooks["after"]:
				f(
					self.client.PLUGIN.plugins[plugin],
					obj or self.client.PLUGIN,
					*args,
					**kwargs
				)

			return result

		wrapper._is_mixin_wrapped = True
		wrapper._mixin_original = attr
		wrapper._mixin_key = getattr(attr, "_mixin_key", None)
		return wrapper

	def apply_mixins_to(self, obj_or_cls):
		"""Patch mixin targets on a class or instance"""
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

			setattr(obj_or_cls, attr_name, wrapper)
			self._patched_targets[(obj_or_cls, attr_name)] = attr
		
		return obj_or_cls

	def plugin_has_mixins_on(self, plugin_key: str, obj_or_cls) -> bool:
		"""
		Check if a plugin has applied any mixins to a given class or instance.
		"""
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

	def remove_plugin_mixins(self, plugin_key: str):
		"""Remove all mixins contributed by a plugin"""
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
		"""Reset everything (for shutdown/restart)"""
		for (obj, attr_name), original in self._patched_targets.items():
			setattr(obj, attr_name, original)
		self._patched_targets.clear()
		_registry.clear()
