# Mixins

Mixins are one of the core extension systems of the application.

They allow plugins to inject functionality into existing systems without modifying the original source code.

Mixins work by wrapping functions before or after they execute.

---

## `@mixin_target()`

`mixin_target()` marks a function as available for plugins to hook into.

Example:

```python
@mixin_target("refresh_weather")
def refresh_weather(self):

    ...
```

---

## `mixin()`

`mixin()` attaches functionality to an existing mixin target.

```python
mixin(
    key="refresh_weather",
    plugin="mypluginkey,
    when="before"
)

mixin(
    key="refresh_weather",
    plugin="mypluginkey,
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

... inside your plugin

class Plugin:
    @mixin("mixin_key", "mypluginkey", "before")
    def new_mixin(self, dummy_class_self, (arg1, arg2)):
        pass
```
* `*args`: the given args to that targeted mixin function

---
