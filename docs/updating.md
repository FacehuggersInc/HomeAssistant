# Updating

Updates are staged while the app runs and applied while it is stopped. The
app never overwrites its own files mid-session.

```text
app running  ->  download + extract to .update-staging/   (nothing installed is touched)
             ->  exit 42
launcher     ->  back up every file it is about to replace
             ->  apply .update-staging/ over the install
             ->  relaunch
             ->  new version starts OK  ->  discard backup
             ->  new version crashes    ->  roll back, relaunch old version
```

Trigger an update either from the API (`GET /update?id=<client id>`) or from
the command line:

```bash
python app.py update          # stage an update, then exit
python app.py apply-update    # apply a staged update in place (no launcher)
```

## What an update will not touch

`.env`, `.venv/`, `plugins/`, `logs/` and `startup.log` are never overwritten.
Your settings live outside the install directory entirely (see
`get_data_dir()` in `src/constants.py`), so they are never at risk.

Bundled plugin `settings.json` files are **merged** rather than replaced: your
existing `value` entries are kept, and any new settings the update introduces
arrive at their defaults. A setting removed by the update goes away.

Note that `startup.sh`, `startup.bat` and `launcher.py` *are* updated. If an
update replaces `launcher.py`, the launcher exits 44 and the shell wrapper
re-runs it so the new code takes effect immediately.

## Exit codes

`app.py` communicates with the launcher through its exit code:

| Code | Meaning |
|------|---------|
| 0    | Clean shutdown. Do not relaunch. |
| 42   | An update is staged. Apply it, then relaunch. |
| 43   | Relaunch as-is (`client.restart()`). |
| 44   | *(launcher -> wrapper)* `launcher.py` updated itself; re-run it. |
| any other | Crash. Handled by the crash policy below. |

Running `app.py` without the launcher still works -- it detects that nothing
is supervising it and relaunches itself instead of exiting with a code
nothing would act on.

## Crash behaviour

Under **Application -> Updates** in Settings:

* `restart_on_crash` -- whether the launcher restarts the app after a crash.
  Turn it off and a crash simply stops the app.
* `max_restart_attempts` -- consecutive restarts before giving up, so a
  genuinely broken build cannot boot loop. Backoff doubles each attempt, to
  a maximum of 30 seconds.
* `crash_window` -- if the app ran longer than this before dying, the attempt
  counter resets. A session that ran for hours and then crashed is not a boot
  loop.
* `update_grace_period` -- a freshly applied update that crashes inside this
  window is rolled back automatically.

The launcher reads these straight out of the settings JSON with the `json`
module rather than through Dynaconf, since it runs before the virtualenv is
known to be usable. A missing or malformed settings file falls back to
defaults rather than refusing to start.

---
