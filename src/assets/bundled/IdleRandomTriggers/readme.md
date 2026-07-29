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
