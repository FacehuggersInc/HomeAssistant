# Settings

Two layers, and they are not the same thing:

* **Client settings** live in the user data directory and are described by
  `src/assets/data/new-template.json`. These are the app's own settings.
* **Plugin settings** live in each plugin's `settings.json` and appear in the
  Settings page under that plugin.

Both use the same declaration format and the same widget builders.

---

## Reading and writing

```python
value = self.client.setting("assistant.model.value", "tiny.en")
```

Always pass a default. A read that misses returns it rather than raising,
which means a setting that never arrived degrades to a working default instead
of a crash - and also means a **missing setting is silent**, so check the
Settings page if something appears to do nothing.

Attribute access works too, and is what most of the codebase uses:

```python
self.client.SETTINGS.application.window.size.value
self.client.SETTINGS.home.widget_margin.value
```

Writing is the same path in reverse:

```python
self.client.SETTINGS.home.pinned.value = "/path/to/image.png"
```

From a plugin, prefer `self.option("general.enabled", True)` - it reads from
your own `settings.json` and keeps you out of the client's tree.

Writes are not thread-safe. Do them on the UI thread, or hold
`client.SETTINGS_LOCK`. See [Threading](threading.md).

---

## Declaring a setting

```json
{
  "general": {
    "enabled": {
      "type": "bool",
      "default": true,
      "value": true,
      "description": "Whether the plugin does anything at all."
    },
    "poll_interval": {
      "type": "int",
      "default": 30,
      "value": 30,
      "suffix": "sec",
      "description": "How often to refresh. Lower costs more requests."
    }
  }
}
```

`default` and `value` are both required. `default` is what Reset returns to;
`value` is what is in force. The top-level keys become sections on the page.

`description` is not optional in spirit - it is the only explanation the person
using the panel will ever see, and it should say what the setting *does to
them*, not what it sets.

### Types

| Type | Widget |
|---|---|
| `bool` | Toggle. |
| `int`, `float` | Numeric field, opens the numpad. |
| `string` | Text field, opens the QWERTY keyboard. |
| `body` | Multi-line text field. |
| `enum` | Dropdown. Requires an `options` list. |
| `path` | Text field with a Browse button. |
| `secret` | Masked field backed by `.env`. Requires an `env` key. |
| `list[int]`, `list[float]` | Comma-separated numeric field. |

`hidden: true` stores a value without rendering a field for it:

```json
"default_location": {
  "type": "string", "default": "", "value": "", "hidden": true,
  "description": "Set with the Choose on a map button above."
}
```

For a plugin that draws its own control and would otherwise show a raw text box
beside a proper picker. The value still saves, migrates and reads back exactly
as any other — it simply has no field. Pair it with a block added through
`insert_plugin_block()` so there is something to set it with.

`suffix` adds a unit label (`"sec"`, `"ms"`, `"hrs"`, `"px"`).

### `enum`

```json
"model": {
  "type": "enum",
  "default": "tiny.en",
  "value": "tiny.en",
  "options": ["tiny.en", "base.en", "small.en"],
  "description": "Larger is more accurate and slower."
}
```

### `secret`

The value is **not** stored in `settings.json`. It goes in `.env`, and the
declaration only names the variable:

```json
"openai_key": {
  "type": "secret",
  "env": "OPENAI_API_KEY",
  "value": "",
  "description": "Stored in your .env file, not here."
}
```

See [Registries](registries.md) for how the scoping works - a plugin can only
read secrets it declared.

---

## Migration

Settings added by an update are folded into the existing data file at startup.
The template and the data file are compared on every launch: new keys arrive at
their defaults, values you have changed are kept, and keys the template no
longer declares are dropped.

The same applies to bundled plugin `settings.json` files, which are merged
rather than replaced when an update is applied - so your configured values
survive, and a new setting arrives at its default.

**This matters when copying files over an install by hand.** A hand-copied
`settings.json` replaces the file outright, including any `value` you had
changed.

---

## Reacting to changes

```python
def load(self, carryover=None):
    self.client.subscribe_to_event("on_settings_saved", self.on_saved)

def on_saved(self, event=None):
    self.interval = int(self.option("general.poll_interval", 30))
```

Fires once when the Settings page is saved, not per field. See
[Events](events.md).

Some client settings restart a subsystem on save rather than being re-read -
changing the assistant's model, microphone or wake word restarts the assistant,
and changing the update check interval restarts the checker.

---

## Adding cards to the Settings page

A plugin can add its own content to a settings category rather than only
declaring fields. See [Features](features.md) for `new_category`,
`insert_block` and `new_settings_list`.
