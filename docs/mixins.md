# Mixins

Mixins are one of the core extension systems of the application.

They allow plugins to inject functionality into existing systems without modifying the original source code.

Mixins work by wrapping functions before or after they execute.


## `@mixin_target()`

`mixin_target()` marks a function as available for plugins to hook into.

Example:

```python
@mixin_target("refresh_weather")
def refresh_weather(self):

    ...
```


## `mixin()`

`mixin()` attaches functionality to an existing mixin target.

```python
mixin(
    key="refresh_weather",
    plugin="mypluginkey",
    when="before"
)

mixin(
    key="refresh_weather",
    plugin="mypluginkey",
    when="after"
)
```

* `before` runs before the original function
* `after` runs after the original function

Use Mixins whenever you need to extend existing behavior.

Avoid directly modifying another system whenever possible.

Or feel free to directly add mixin_targets to functions you feel do not need new source code, but you want to extend.

Mixins have a args layout that needs to be followed.
```python
@mixin("refresh_weather", "mypluginkey", "before")
def function_thats_mixing(self, self_obj_from_class, *args_from_mixed_func):
    pass
```

you get 3+ args from the mixin wrapper.
* `self`: this is your plugin instance
* `self_obj_from_class`: this is the Class Instance from the function that mixin refers too
```python
class DummyClass:
    @mixin_target("mixin_key")
    def targeted_func(dummy_class_self, arg1, arg2):
        pass


# ...and inside your plugin
class Plugin:
    @mixin("mixin_key", "mypluginkey", "before")
    def new_mixin(self, dummy_class_self, *args, **kwargs):
        # self          - your plugin
        # dummy_class_self - the object whose method was called
        # *args         - whatever the original was called with
        pass
```
* `*args`: the given args to that targeted mixin function


## Available targets

A method can only be extended if it was declared with `@mixin_target`. These
are the ones that exist:

### Client

| Target | When it runs |
|---|---|
| `client.__init__` | The client is constructed. Registries exist; no UI does. |
| `client.build` | The window is built and shown. |
| `client.build.setup` | Inside `build`, before quick settings and the page host are raised. |
| `client.configure` | The window is configured or reconfigured. |
| `client.goto` | Any page navigation. |
| `client.update` | The client tick. |
| `client.start_update` | An app update begins staging. |
| `client.load` | Any JSON read through the client. |
| `client.dump` | Any JSON write through the client. |
| `client.cleanup` | Shutdown, before plugins are unloaded. |

### Pages

| Target | When it runs |
|---|---|
| `home.__init__` | The home page is built. |
| `sub.home.__init__` | The widget sub-page is built. |
| `sub.tiles.__init__` | The tile sub-page is built. |
| `settings.__init__` | The settings page is built. |
| `settings.setup.tab.generation` | The settings navigation is built. |
| `settings.setup.setting.generation` | Settings widgets are generated. |
| `settings.timeout` | Settings idles back to the home page. |
| `settings.save` | Settings are saved. |

The page targets are the ones plugins reach for most. `sub.home.__init__` and
`sub.tiles.__init__` are how widgets and tiles get registered, because they
fire whenever the page is built — at startup and again after a reload, which a
one-shot call in `built()` does not.

### Adding a target

Decorate the method you want to open up:

```python
from src.mixins import mixin_target

class MyPage(PageFramework):

    @mixin_target("mypage.__init__")
    def __init__(self, client, data=None):
        ...
```

Name it `owner.method`, matching the convention above. A target is a public
commitment — anything mixed onto it breaks when the signature changes, so open
up the points you intend to keep rather than every method you happen to have.
