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
			if not os.path.exists(filepath): return None
			with open(filepath, "r", encoding="utf-8") as f:
				data = json.load(f)
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


## SETTING GROUPS
#
# A `group` setting is a dropdown that decides which OTHER settings in its
# block are worth showing.
#
# It exists because a block full of settings that only matter in one
# configuration reads as a block full of settings. `audio.speech` carries a
# host and a port that mean nothing unless the voice is on another machine,
# and nothing on the page said so - somebody sets them, nothing changes, and
# the setting looks broken rather than inapplicable.
#
#     "tts_where": {
#         "type": "group",
#         "value": "socket",
#         "groups": {
#             "local":      ["tts_voice", "tts_language"],
#             "subprocess": ["tts_voice", "tts_language", "tts_port"],
#             "socket":     ["tts_host", "tts_port"]
#         }
#     }
#
# **Hiding is all it does.** Every setting keeps its value and stays readable
# and writable by name whatever is selected - `client.setting()` does not know
# groups exist. A setting named in two groups is one setting, so choosing
# another group that also names it finds the value that was already there.
# One left behind in a group nobody is looking at keeps its value too, and
# finds it again when that group comes back.
#
# There is deliberately no default. The value IS the selection, and a group
# setting with nothing selected is a dropdown with nothing in it.


GROUP_TYPE = "group"


def is_group(setting) -> bool:
    """Whether a setting node is a group selector."""
    try:
        return str(setting.get("type", "")).strip().lower() == GROUP_TYPE
    except Exception:
        return False


def group_names(setting) -> list:
    """
    The groups it offers, in the order they were written.

    Taken from the groups themselves rather than a separate `options` list.
    Two lists that have to agree are two lists that eventually do not, and the
    one that disagrees would offer a choice that shows nothing.
    """
    try:
        groups = setting.get("groups") or {}
        return [str(name) for name in groups.keys()]
    except Exception:
        return []


def chosen_group(setting) -> str:
    """
    Which group is selected, falling back to the first one.

    A value naming a group that no longer exists is what a plugin update
    leaves behind, and the alternative to falling back is a page showing
    nothing at all with no way to pick anything.
    """
    names = group_names(setting)
    if not names:
        return ""
    try:
        value = str(setting.get("value", "") or "")
    except Exception:
        value = ""
    return value if value in names else names[0]


def members(setting, name: str = None) -> list:
    """The keys one group names. The selected one by default."""
    try:
        groups = setting.get("groups") or {}
    except Exception:
        return []
    wanted = name if name is not None else chosen_group(setting)
    return [str(key) for key in (groups.get(wanted) or [])]


def owned(setting) -> set:
    """
    Every key named by ANY group.

    These are drawn under the selector rather than in their own place, so the
    block does not show a setting twice - once where it was written and once
    inside the group that claims it.
    """
    try:
        groups = setting.get("groups") or {}
    except Exception:
        return set()
    out = set()
    for keys in groups.values():
        for key in (keys or []):
            out.add(str(key))
    return out


def shared(setting) -> set:
    """Keys more than one group names. Their value carries across a switch."""
    try:
        groups = setting.get("groups") or {}
    except Exception:
        return set()
    seen, twice = set(), set()
    for keys in groups.values():
        for key in set(keys or []):
            key = str(key)
            if key in seen:
                twice.add(key)
            seen.add(key)
    return twice


def plan(block: dict) -> dict:
    """
    How one block of settings should be drawn.

    Answers `{"order": [...], "owned": {key: selector_key}, "groups": {...}}`
    where `order` is the keys to draw at the top level, with each group
    selector followed by nothing - its members are drawn by the selector
    itself, from `groups`.

    A pure function of the block, so what the page will show can be checked
    without building a page.
    """
    if not isinstance(block, dict):
        return {"order": [], "owned": {}, "groups": {}}

    selectors = [key for key, node in block.items()
                 if isinstance(node, dict) and is_group(node)]

    claimed = {}
    for selector in selectors:
        for key in owned(block[selector]):
            # First selector wins. Two groups claiming one setting is a
            # mistake in the schema, and drawing it under both would put the
            # same control on screen twice.
            claimed.setdefault(key, selector)

    order, groups, everything = [], {}, {}

    # EVERY selector gets its members worked out, including one that is itself
    # a member of another. Groups stack - a backend chooses whether there is a
    # voice at all, and where it runs only means anything once that has said
    # yes - and a nested selector skipped here is one drawn as an empty
    # dropdown with its own settings nowhere.
    for key in selectors:
        node = block[key]
        groups[key] = [k for k in members(node)
                       if k in block and claimed.get(k) == key]
        # Every member of every group, once each, in the order they are first
        # named, with the groups that claim them. The page builds all of them
        # and hides the ones not currently chosen, so switching costs a
        # visibility change rather than a rebuild.
        seen, out = set(), []
        for name in group_names(node):
            for member in members(node, name):
                if member not in block or claimed.get(member) != key:
                    continue
                if member in seen:
                    continue
                seen.add(member)
                out.append((member, tuple(
                    other for other in group_names(node)
                    if member in members(node, other))))
        everything[key] = out

    for key, node in block.items():
        if not isinstance(node, dict):
            continue
        if key in claimed and claimed[key] != key:
            continue
        order.append(key)
    return {"order": order, "owned": claimed, "groups": groups,
            "all": everything}


def visible_members(chosen: str, all_members: list) -> list:
    """
    Which of a selector's members belong on screen for `chosen`.

    Every member is built, and the ones not claimed by the current group are
    hidden rather than destroyed - so switching costs a visibility change
    rather than a rebuild of the page somebody is reading.

    Here rather than inside the page so the rule can be run: a closure over a
    list of widgets can only be checked by reading it.
    """
    out = []
    for entry in (all_members or []):
        try:
            member, belongs = entry
        except (TypeError, ValueError):
            continue
        if chosen in (belongs or ()):
            out.append(member)
    return out


def missing_members(block: dict) -> list:
    """
    Group members that are not in the block. `(selector, group, key)`.

    A name that does not resolve is a setting somebody expected to see and
    will not, with nothing to say why - so it is worth reporting rather than
    quietly drawing one fewer control.
    """
    out = []
    if not isinstance(block, dict):
        return out
    for key, node in block.items():
        if not isinstance(node, dict) or not is_group(node):
            continue
        for name in group_names(node):
            for member in members(node, name):
                if member not in block:
                    out.append((key, name, member))
    return out
