# Packages

Everything another machine needs, built when it is asked for.

`client.PACKAGES` holds them. A package is not a zip sitting in the tree
waiting to be served — it is a **builder**, a function that runs at the moment
of download and answers with the files.

That difference is the whole point. The panel knows its own address, the port
it is configured to talk to, the voice that is chosen and the model that is
set, so what comes out is already pointed at the right place and its README
can name the settings to change at both ends. A static archive knows none of
that, and every one of those details is a step somebody would otherwise get
wrong once and then spend an evening finding.

Setting up a second machine is what this exists to shorten.

---

## Registering one

```python
def load(self, carryover=None):
    self.client.PACKAGES.register(
        self.plugin_key(), "feed-worker", "Feed worker",
        self.build_worker,
        description="Runs the feed polling somewhere with a real disk.",
        version="1.0",
        contents=("worker.py", "requirements.txt", "startup.sh"),
    )
```

The builder takes nothing and answers `(folder, {relpath: bytes})` — the same
shape [the plugin skeleton](plugins.md) produces, so `as_zip()` is shared and a
package can be edited and sent back like any other folder.

`contents` is what is inside, in words. It is shown on the card **before**
anything is downloaded: a package is code somebody will run on another
computer, and "trust me" is not a reasonable thing to ask of that.

Owner-keyed like every registry. A key another owner holds is refused and
logged; the same owner may re-register, which is how a reloaded plugin puts
its builder back. Packages are released on unload, reload or not — a builder
closes over the instance that made it, so one held across a reload would build
from the module that was replaced.

## What a builder must not do

The registry checks the files before they are zipped, because they are about
to be unpacked by somebody on a machine this panel has no other reach into:

| Refused                    | Because                                    |
|----------------------------|--------------------------------------------|
| A path starting `/`        | It escapes the folder it is unpacked into. |
| A path containing `..`     | The same, one directory at a time.         |
| Anything that is not bytes | It cannot be written to an archive.        |
| An empty result            | There is nothing to send.                  |

A builder that raises is reported as that package failing rather than as the
page failing.

## The page

`/packages`, under the **plugins** permission — the builders come from
plugins, and the output is code that will be run somewhere.

Searchable across name, description, owner and contents. Each card names the
package, its version, who supplies it, what it does and what is inside, and
downloads as `<folder>.zip`.

## The speech server

The panel ships one. `Settings -> Audio -> Speech -> where` set to `socket`
sends text to another machine and takes back audio; the package is what sets
that machine up.

Built with the port and voice already filled in, so the README can say what to
type rather than describing where to find it. It carries
`tts-socket-process.py`, `tts_protocol.py`, `requirements.txt`, a
`startup.sh` and `startup.bat` that make their own virtual environment on the
first run, and a README.

**`tts_protocol.py` is copied from this tree rather than written out again.**
A wire format described in two files is a wire format that disagrees with
itself the first time either end changes, and here the two ends are on
different machines and are updated at different times. Re-download the package
after updating the panel rather than editing either copy.

See [Voice assistant](assistant.md#speaking-somewhere-else).

## The methods

| Method                                 | Does                              |
|----------------------------------------|-----------------------------------|
| `register(owner, key, name, builder…)` | Offer one.                        |
| `unregister(owner, key="")`            | Give back what an owner offered.  |
| `all(search="")`                        | Every match, by name.             |
| `get(key)` / `for_owner(owner)`         | One, or an owner's.               |
| `build(key)`                            | `(folder, zip bytes)`.            |
