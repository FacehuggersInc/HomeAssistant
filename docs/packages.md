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

Run it with `bash startup.sh`, not `./startup.sh`. A zip extracted by a file
manager arrives without the executable bit — `extractall` and most graphical
tools drop permissions whatever the archive says — and that is a confusing
first thing to hit.

Every step of the script is checked and says what it wants. `set -e` alone
stops at the first bad line and leaves somebody at a prompt wondering which
line it was, which is the difference between "no `.venv` appeared and nothing
failed visibly" and "install `python3-venv`". The whole run is written to
`setup.log` beside the script.

A `.venv` that exists with no interpreter in it is rebuilt rather than skipped.
That state is worse than having none: a test for the folder passes, creation is
skipped, and the failure lands on the last line pointing nowhere near the
cause.

See [Voice assistant](assistant.md#speaking-somewhere-else).

## The judge server

The panel ships this one too. `Settings -> Assistant -> Wake -> judge_where`
set to `socket` sends an utterance to another machine and takes back one word;
the package is what sets that machine up.

Built with the port and the model already filled in, and a README that names
the four settings to change at the panel end. It carries
`judge-socket-process.py`, `judge_protocol.py`, `judge_prompt.py`,
`requirements.txt`, a `startup.sh` and `startup.bat`, and a README.

**Two files are copied from this tree rather than written out again.** The
wire format for the reason the speech server copies its own, and the PROMPT
for a reason of its own: two prompts is two behaviours, and the one nobody is
watching is the one that drifts. A judge on another machine that has been
asked a slightly different question is a judge that disagrees with the panel
for a reason nobody can see.

It asks for the fp16 build where the panel runs int8. A machine reached over
a socket is not holding a screen and a microphone, and the larger build judges
a little better for memory nobody is short of.

**If that machine is off, the panel is not broken.** Every utterance falls
back to the rules, which is how the panel behaves with the judge turned off.

See [was anybody talking to the panel](assistant.md#was-anybody-talking-to-the-panel).

## The methods

| Method                                 | Does                             |
|----------------------------------------|----------------------------------|
| `register(owner, key, name, builder…)` | Offer one.                       |
| `unregister(owner, key="")`            | Give back what an owner offered. |
| `all(search="")`                       | Every match, by name.            |
| `get(key)` / `for_owner(owner)`        | One, or an owner's.              |
| `build(key)`                           | `(folder, zip bytes)`.           |
