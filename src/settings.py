import os, json
from pathlib import Path
from collections.abc import MutableMapping

class Settings(MutableMapping):
	def __init__(self, *args, **kwargs):
		self._store = {}
		self._extensions = {}

		#Process dict Into _store
		for key, value in list(dict(*args, **kwargs).items()):
			value, alt_key = self.__normalize_value(key, value)
			setattr(self, alt_key, value)
			self._store[alt_key] = value

	## EXTENSIONS
	def __normalize_value(self, key, value):
		# Handle extensions
		if isinstance(value, str) and key.startswith("::ext?"):
			ext_name = key.split("?", 1)[1]
			self._extensions[ext_name] = value
			settings = self.__load_extension(value)
			if settings:
				return settings, ext_name

		# Normal conversions
		if isinstance(value, dict):
			return Settings(value), key
		elif isinstance(value, list):
			return self.__convert_list(value), key
		elif isinstance(value, str) and not value:
			return " ", key
		return value, key

	def __load_extension(self, filepath):
		try:
			print(f"  ? Loading -> {filepath}")
			if not os.path.exists(filepath): return None
			with open(filepath, "r", encoding="utf-8") as f:
				data = json.load(f)
				print(f"    ? data -> {data}")
				return Settings(data)
		except Exception as e:
			return None

	## PATH-ING
	def set_path(self, path: str, value):
		keys = path.split(".")
		current = self
		for key in keys[:-1]:
			if key not in current._store or not isinstance(current._store[key], Settings):
				current[key] = Settings()
			current = current[key]

		current[keys[-1]] = value

	def get_path(self, path: str, default=None):
		keys = path.split(".")
		current = self
		for key in keys:
			if key not in current._store:
				return default
			current = current[key]

		return current

	## MAPPING
	def __getitem__(self, key):
		return self._store[key]

	def __setitem__(self, key, value):
		value, alt_key = self.__normalize_value(key, value)
		setattr(self, alt_key, value)
		self._store[alt_key] = value

	def __delitem__(self, key):
		if key in self._store:
			del self._store[key]
			if hasattr(self, key):
				delattr(self, key)

	def __iter__(self):
		return iter(self._store)

	def __len__(self):
		return len(self._store)

	## HELPERS
	def __convert_list(self, items):
		converted_list = []
		for item in items:
			if isinstance(item, dict):
				item = Settings(item)
			elif isinstance(item, list):
				item = self.__convert_list(item)
			converted_list.append(item)
		return converted_list

	def __un_convert_list(self, items):
		converted_list = []
		for item in items:
			if isinstance(item, Settings):
				item = item.to_dict()
			elif isinstance(item, list):
				item = self.__un_convert_list(item)
			converted_list.append(item)
		return converted_list

	## CONVERT
	def to_dict(self):
		result = {}
		for key, value in self._store.items():
			if key in self._extensions:
				value = self._extensions[key]
				key = f"::ext?{key}"
				
			if isinstance(value, Settings):
				result[key] = value.to_dict()
			elif isinstance(value, list):
				result[key] = self.__un_convert_list(value)
			else:
				result[key] = value
		return result


def scrub_secrets(data):
    """
    Blank the value of every `secret` setting, in place, recursively.

    Belt and braces. The secret field never writes to `value`, but a plugin
    author can put a key straight into their shipped settings.json, and both
    settings files are dumped to disk on every save and rendered wholesale in
    the UI. Anything typed as `secret` gets emptied on the way out, so a
    credential cannot reach either file by accident.
    """
    if isinstance(data, dict):
        if str(data.get("type", "")).lower() == "secret":
            data["value"] = ""
            data.pop("default", None)
            return data
        for value in data.values():
            scrub_secrets(value)
    elif isinstance(data, list):
        for item in data:
            scrub_secrets(item)
    return data


def secret_keys(data, found=None):
    """Every env key named by a `secret` setting."""
    found = [] if found is None else found
    if isinstance(data, dict):
        if str(data.get("type", "")).lower() == "secret":
            key = str(data.get("env", "") or data.get("key", "")).strip()
            if key:
                found.append(key)
            return found
        for value in data.values():
            secret_keys(value, found)
    elif isinstance(data, list):
        for item in data:
            secret_keys(item, found)
    return found
