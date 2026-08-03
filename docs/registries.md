# Registries

Registries manage and store extendable, plugin-ownable objects — things like API endpoints or pages, that a plugin registers and expects to have cleaned up automatically when it's unloaded or reloaded.

Nine concrete registries currently exist, and `BookmarkStore` sits alongside
them below. They are not all shaped the same way.

## `APIRegistry` — `self.client.API`

**Two things, one registry.** It holds the HTTP endpoints a plugin serves
*and* the API classes it provides — the weather client, the RSS parser.

Both have an owner, so a plugin that unloads takes its objects with it rather
than leaving them behind for anything still holding a reference to call into.

```python
# An API class, with an owner
self.client.API.register_api("myplugin", "weather", OpenMeteoAPI(self, client))

# Reading one, from anywhere
api = self.client.API["weather"]          # KeyError if absent
api = self.client.API.get("weather")      # None if absent
if "weather" in self.client.API: ...
```

Registering a key another plugin already owns is refused and logged rather
than silently winning — two plugins fighting over one key is much harder to
find than a warning at startup. `replace=True` is the deliberate way past it.

`unregister("myplugin")` with no endpoint name drops **both** the plugin's
endpoints and its API classes, which is what a plugin unloading wants.
`unregister_api("myplugin", "weather")` drops one, and refuses if the plugin
does not own it.

## `APIRegistry` endpoints and `PageRegistry`

These two share the same shape:

```python
registry.register(owner, key, ...)
registry.unregister(owner, key="")
```

`owner` is the plugin's key (or `"client"` for things the Client itself owns, like `#root` and `#settings`). Registering something under your plugin's key means `PluginManager.unload_plugin()` cleans it up automatically when your plugin is unloaded or reloaded — you should not need to manually remove anything you registered this way (see Plugin Lifecycle → `unload()`).

```python
# APIRegistry — self.client.API
self.client.API.register("myplugin", "my_endpoint", self.my_callback, False, False)
self.client.API.unregister("myplugin", "my_endpoint")

# PageRegistry — self.client.PAGES (wrapped by add_page, see [Pages](pages.md))
self.client.add_page("#mypage", "My Page", MyPage, owner="myplugin")
self.client.PAGES.unregister("myplugin", "#mypage")
```

## `PublicRegistry`

This one is shaped differently — `expose` / `unexpose` rather than `register` / `unregister`, since it's not registering a discrete thing with a lifecycle so much as just making a variable or object visible to everyone else:

```python
self.client.public.expose(owner, name, value, overwrite=False)
self.client.public.unexpose(owner, name)
```

```python
# PublicRegistry — self.client.public
self.client.public.expose("myplugin", "my_shared_state", self.my_shared_state)

# elsewhere, any other plugin can read it directly:
self.client.public.my_shared_state
```

Like the other two, anything exposed under your plugin's key is cleared automatically on unload via `self.client.public.clear(owner)` — you don't need to call `unexpose` yourself during a normal teardown.

That cleanup is keyed on the string you passed as `owner`, so it has to be **your plugin's own key from `plugin.toml`**. Expose under anything else and `clear()` never finds it: the name stays on the registry after your plugin unloads, still bound to an instance that has gone, and `names_for()` reports nothing for you on the plugin details page.

See [Reaching another plugin](plugins.md#reaching-another-plugin) for when to use this rather than an import.

---

# Reading settings from a thread

`client.SETTINGS` is a Dynaconf object, and Dynaconf's `reload()` empties its
store before repopulating it. A read from another thread landing in that gap
raises `AttributeError: 'Settings' object has no attribute 'APPLICATION'` --
intermittent, and only when a save happens to coincide with a background read.

Saving therefore goes through `client.apply_settings(values)`, which uses
`update()` instead: it only adds and overwrites, so no section is ever
momentarily absent.

From a background thread -- `on_update`, `on_collection`, a worker of your own
-- read through the accessor rather than touching `SETTINGS` directly:

```python
margin = self.client.setting("home.layout.widget_margin.value", 28)
```

`setting()` takes a dotted path and a default, holds the settings lock, and
returns the default rather than raising if anything on the path is missing.
Direct `client.SETTINGS.x.y` access is still fine on the UI thread.



## `SecretRegistry` — `self.client.SECRETS`

API keys and anything else that should not sit in a settings file. Values live
in `.env` at the install root; the registry only knows the **names**.

A plugin declares the keys it needs in `plugin.toml`, or by registering them:

```python
self.client.SECRETS.register("myplugin", "MYSERVICE_KEY",
                             label="MyService API key")
```

Reads are **scoped to the owner**. A plugin can only read a key it declared:

```python
key = self.client.SECRETS.get_for("myplugin", "MYSERVICE_KEY", "")
```

| Method                            | Does                                             |
|-----------------------------------|--------------------------------------------------|
| `register(owner, key, label="")`  | Declare a key.                                   |
| `get_for(owner, key, default="")` | Read, scoped.                                    |
| `set_for(owner, key, value)`      | Write to `.env`.                                 |
| `is_set(key)`                     | Whether it has a value.                          |
| `is_declared(key)`                | Whether anything declared it.                    |
| `status(key)`                     | A human-readable state for the Settings page.    |
| `masked(key)`                     | The value with most of it replaced by asterisks. |
| `keys_for(owner)`                 | What an owner declared.                          |
| `clear(key)`                      | Remove it from `.env`.                           |

Never log a secret, and never put one in a notification. Use `masked()` when
you need to show that a key is present.

Declaring a key with a `secret` setting type gives you a masked field in
Settings for free — see [Settings](settings.md).


## `QuickAccessRegistry` — `self.client.QUICK`

Buttons in the [quick settings](quick-settings.md) panel. Owner-scoped like
the others, and cleared automatically when a plugin unloads.

```python
self.client.QUICK.register(
    "myplugin", "porch", "Porch", Icons.LIGHTBULB,
    on_press = self.toggle_porch,
    on_state = lambda: self.porch_on,
)
```

Full detail on that page.

## `UserRegistry` — `self.client.USERS`

Approved devices and their tokens. A device asks at `/access/request`, a dialog
appears on the panel, and an allowed device gets a token of its own — so
revoking one does not affect the others.

Covered in full on [Users](users.md).

## `BookmarkStore` — `self.client.BOOKMARKS`

Saved web pages, in the user's data directory. Client-owned: the web page
belongs to the client and its toolbar does too, so a list of addresses that
disappears when somebody unloads a plugin is not a bookmark list.

```python
client.BOOKMARKS.add(url, title="...", icon=view.icon())
client.BOOKMARKS.all()          # newest first
client.BOOKMARKS.has(url)
client.BOOKMARKS.remove(url)
client.BOOKMARKS.icon_path(mark)
```

Saving the same address twice is a **correction**, not a duplicate — pressing
the star again on a page whose title has since loaded should update it. Capped
at 60; past that a grid stops being something to glance at.

Icons are written as PNGs beside the list, named from a hash of the address.
Hashed rather than sanitised: a filename made by replacing awkward characters
collides the moment two pages differ only in one of them. Removing a bookmark
takes its icon with it.

### `client.choose_bookmark(on_chosen)`

Opens the picker and calls `on_chosen(url)`. On the client because a widget, a
tile and anything added later all want the same one, and three copies would be
three chances to disagree about what a bookmark looks like.

With none saved it opens the browser's home page instead — a dialog saying
"nothing here" and closing again leaves somebody exactly where they were.

## `AudioRegistry` — `self.client.AUDIO`

Sounds by name. A plugin registers a key against a file in **`.audio/`** at the
repository root and asks for the key; nothing outside the registry knows where a sound lives or
what format it is in.

```python
client.AUDIO.register("myplugin", "ping", "ping.wav", volume=0.7)
client.AUDIO.play("ping")
client.AUDIO.play("timer_alarm", for_seconds=20)   # repeat until told to stop
client.AUDIO.stop("timer_alarm")
```

`.audio` is **not in git**. A sound licensed for personal use can sit there
without ever being committed or shipped — attribution obligations are triggered
by distribution, and this never distributes them. Anybody cloning this gets a
panel that is quiet.

A key can have **several files**, and one is picked at random:

```python
client.AUDIO.register("client", "tap",
                      ["tap-1", "tap-2", "tap-3"], volume=0.25)
```

**Names carry no extension.** A bare name matches whatever format is actually
there — `tap-1.oga`, `tap-1.wav`, `tap-1.flac` — so whoever puts sounds in
`.audio` does not have to convert them to whatever the registration guessed.
Sounds are downloaded rather than authored, and the person downloading them does
not choose the container. A missing variation is skipped, so two of three taps
is fine.

An extension that *is* given has to be one this can open; that is a mistake
rather than a wildcard.

A tap making exactly the same noise a hundred times an hour stops being feedback
and becomes a tic. Random rather than round-robin — a cycle of three is a
pattern, and a pattern is what variations exist to avoid. Never the same one
twice running.

`.audio` is registered as an **uploadable folder asset**, so it appears on
`/upload` beside stickers and wallpapers. Putting a file in is all it takes —
the registry looks there by key, and no endpoint of its own is needed.

**A key with no file is silent** and says so once. Sounds are content and arrive
later than the code that plays them, so a key registers whether or not anything
is behind it — a panel nobody has put sounds into should be quiet, not broken.
`missing()` lists the keys still waiting.

Playback is on a worker. A timer's alarm repeating for twenty seconds must not
be twenty seconds of frozen screen, and every caller is on the UI thread. The
wait between repeats is interruptible, so `stop()` takes effect part way through
rather than after the current repeat finishes.

Registering a key against a file this cannot open is refused **at registration**,
not when the timer goes off at six in the morning.

## `PlayerRegistry` — `self.client.PLAYER`

What is playing, whatever is playing it. Backends register and publish; one is
active at a time, and commands go to that one. The now-playing card reads this
rather than any particular plugin, so a card written once works for a web
player, a system player, or anything added later.

Covered in full on [Media playback](player.md).

## `CancelRegistry` — `self.client.CANCEL`

What "stop" means at this moment. Anything cancellable registers its own
keywords, whether it is currently active, and a priority — so "stop" reaches
the music while "nevermind" closes the answer panel, without either having to
know the other exists.

Covered in full on [Cancelling](cancel.md).
