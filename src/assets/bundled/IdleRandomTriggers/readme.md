### Allows for random triggers/callbacks after the Application goes idle, then will rotate though randomly everything registered to it. Can auto handle panels passed into it and will auto close them.

## Pages that refuse triggers

Two ways a page is skipped:

* a plugin registers the page key when it adds its builder, which needs that
  plugin to know this one exists.
* the page carries `blocks_idle_triggers = True`, and is skipped without
  either plugin referencing the other. The night clock uses this - a
  screensaver over a screensaver is nobody's idea of restful.

Both are checked in `check_time_update` **and** in `on_interaction_timeout`.
Arming in one and unwinding in the other meant a trigger could be built and
dismissed inside a single frame on a page that never wanted it.

This is not `blocks_idle`, which stops the idle clock altogether. A page that
refuses triggers still goes idle and still times out normally.

## Sprints

Rotating forever meant the panel never settled, which is the one thing an idle
screen is supposed to do.

| Setting | Default | Meaning |
|---|---|---|
| `rotate_time` | 60000ms | How long each panel stays up. |
| `sprint_items` | 4 | How many in a row before a pause. `0` never pauses. |
| `sprint_break` | 300000ms | How long the screen is left alone between runs. |

A break dismisses whatever is up and stops rotating. When it ends the rotation
only resumes **if the panel is still idle** - somebody walking past during a
break stops it, and it should stay stopped. An interaction cancels the break
outright rather than leaving a timer to fire into a screen somebody is using.
