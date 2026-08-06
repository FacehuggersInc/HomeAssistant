# Installing and updating a plugin remotely

A **bundled** plugin is part of the app and updates with it. A plugin in
`plugins/` is yours, and the Plugins section of the dashboard is how you
install, update and retrieve one over the network.

Three sibling pages behind a sticky sub-nav:

| Page      | What it is for                                                               |
|-----------|------------------------------------------------------------------------------|
| Installed | What is running; load / unload / reload / download for the ones not bundled. |
| Create    | A starting folder, handed back as a zip.                                     |
| Upload    | Send a zip, see what it would do, then say yes.                              |

## Who may

`plugins` is a **user permission**, granted per device on the panel itself:
**Settings → Users**, the menu on a device's card. Being approved gets a
device in; this decides whether it may touch the plugins at all. No device
holds it until it is granted, whenever it was approved - a permission that
arrives switched on for everybody is not a permission.

Granted only at the panel, and never over the API. This is the permission that
lets a device put code on the machine, and the one moment somebody is
definitely in the room is the moment they are standing at it. Turning it *on*
asks for confirmation; turning it off does not - taking a capability away is
not a thing to be talked out of.

Which devices hold it is shown on the card itself, not only inside the menu. A
capability that is visible only after opening something is one nobody audits.

The drawer entry is hidden for a device without it. A link that leads to a
refusal is a door with a lock and no handle: it says the room exists and then
stops you, every time you look.

## Two questions, asked separately

Uploading and installing new code are different decisions, so they have
different answers:

* **An update** to a plugin already installed needs the permission and
  nothing else. That plugin was accepted once; asking again every time it
  gains a bug fix trains people to press yes without reading.
* **A plugin that is not here yet** also needs somebody at the panel to agree.
  The request waits five minutes and then expires, and a "yes" tapped after
  that installs nothing.

## Nothing is written until you have seen what would change

An upload happens twice: once to say what it *would* do, and once to do it.
The preview lists files that would be **replaced** separately from files that
would be **new** - the question being answered is "what am I about to lose",
and mixing the two makes forty additions look like forty losses.

Between the two, the plan is held with a fingerprint of the folder it was
planned against. If anything in that folder changes in the meantime the apply
is refused: the yes was given to a list describing a folder that no longer
exists. A plan is also single use, because a token that still works after
being applied is a back button installing something twice.

**The files change immediately; the running plugin does not.** A plugin that
is loaded keeps running the code it was loaded with until it is reloaded, and
the preview says so rather than leaving somebody to wonder why nothing
happened.

So the page after an upload **offers** the last step rather than taking it:
*Reload it now* for a plugin that is running, *Load it now* for one that is
not, and *Not now* beside both. Running new code is the decision this whole
section exists to put in somebody's hands, and doing it automatically at the
end would hand it straight back.

A plugin installed while the panel is running is on disk and unknown to the
loader - the scan that finds plugins runs once, at boot. `PLUGIN.discover()`
is how a folder that arrived afterwards becomes loadable without a restart.

## A key that is already taken

Plugin folders are scanned bundled-first, so a plugin in `plugins/` whose key
matches a bundled one **never loads** - the bundled plugin wins. The bundled
one is part of the app and other plugins depend on it.

A clash is refused at every point it can be, rather than left to be discovered
after the folder is on disk:

| Where                | What happens                                                                                               |
|----------------------|------------------------------------------------------------------------------------------------------------|
| **Create**           | The form refuses the key and names the folder holding it, before any zip is handed over.                   |
| **Upload**           | `plan()` is given the keys in use and refuses. Nothing is written.                                         |
| **The panel dialog** | Where a clash appears between upload and approval, the dialog warns that installing will not make it load. |
| **On approval**      | The gate re-checks the key and refuses rather than writing a folder that cannot load.                      |
| **`discover()`**     | Refuses to make it loadable and records it as a conflict.                                                  |

`plan(files, name, dir, taken={key: folder})` takes the keys in use as an
argument rather than looking them up, so `install.py` stays a folder, a zip
and a set of rules. Loaded, stopped and already-conflicting plugins all hold
their key: all three are found by the scan, and the first one wins.

A folder that reaches disk anyway - one added by hand, or two uploaded before
either loaded - is kept as a `ConflictingPlugin` and listed in both places,
the Plugins page and the panel's settings, as **CONFLICT**. It carries no
buttons: loading will not work and installing packages will not help. The card
names the key it collided on and what holds it, and says the fix is to change
the key in its own `plugin.toml` or remove the folder.

Records are keyed by **folder**, not by key. The key is the thing it collides
on, so a record filed under it would be the same record as the plugin that
won - and in the settings nav it would land on top of it.

## Stopped is not gone

`unload_plugin()` moves a plugin onto the pending list with nothing missing.
Unloading stops it; it does not hide it, and it stays startable without a
restart.

That gives the settings card three states to tell apart:

| Badge         | Button  | Means                                     |
|---------------|---------|-------------------------------------------|
| NOT INSTALLED | Install | Packages are missing. pip has work to do. |
| STOPPED       | Load    | Complete, and not running.                |
| CONFLICT      | none    | The key belongs to something else.        |

The nav says so too: a plugin that is present and not running reads as
`Anime · stopped`, or `· blocked` for a conflict, dimmed and italic. A list
where a loaded plugin and an unloaded one look identical has to be opened
entry by entry to find out which is which.

A stopped or blocked plugin's page shows what it is and, where there is
something to do, offers it. There are no settings to edit and no registries
listed, because a plugin that is not running has registered nothing.

## Versions

`version` in `[plugin]` is offered on the Create form **and on the Upload
form**, shown beside the key on the panel's own plugin card, and shown on the
upload preview as `v0.1.0 → v0.2.0`. Where an upload carries the same version
as the copy installed, the preview says so: that is either a mistake or a
rebuild, and it is the only clue available that two zips differ.

The field on the Upload form is for the partial case: a zip of only the files
you changed carries no `plugin.toml`, and a version that never moves tells
nobody anything. Given a version, the upload rewrites the **installed**
`plugin.toml`, and the preview lists it among the changed files.

`install.set_version()` edits the file with a regex rather than re-serialising
it. A toml round trip through a parser and a writer drops every comment, and a
plugin's toml is mostly comments - the `install_once` block is four lines of
explanation and one of syntax. Changing a version should not cost somebody
their notes.

## Getting one back out

Every non-bundled plugin has a **Download** on its card: the folder as it is
on disk, zipped, as its contents rather than as the folder - the same shape
Upload accepts and Create hands out. Take what is installed, change something,
send it back.

Bundled plugins have no download. They ship with the app and a project update
replaces them, so a copy of one is a thing that quietly stops matching what is
installed.

## What an update respects

The same rules a project update follows, so a plugin author's expectations do
not change with how the code arrived:

* **`[update] install_once`** files are written when absent and left alone
  forever after. This wins over everything below, including the settings
  merge - merging is this module's idea of what is polite, and `install_once`
  is the author saying do not touch it.
* **`settings.json` is merged**: structure and new keys from the incoming
  version, values from the installed one.
* **Identical files are not written.** Not an optimisation: copying takes the
  source's permissions and a file out of a zip has none.
* A zip may contain **only the files that changed**. Anything it does not
  mention is left alone, and the rules above are read from the installed
  `plugin.toml` when the zip carries none.

## What is refused

Path traversal, absolute paths, drive letters, symbolic links, archives that
unpack to more than a plugin should be, a new plugin with no `plugin.toml` or
no `main.py`, a `plugin.toml` with no `[plugin] key`, and a zip whose key
differs from the plugin already in that folder - that last one is a different
plugin wearing another's name, and applying it leaves a folder whose files and
settings belong to two plugins at once.

A folder name with a separator in it is refused rather than reduced to its
last part. Taking the basename is safe and silent: the upload lands somewhere
nobody named and nothing says so.

## The plugins folder is guarded

`Asset(...).mark_guarded()` is a third flag beside uploadable and deletable.
Uploadable asks "may this be added to from the API"; guarded asks "is what
lands here **run**". A sound or a wallpaper is data and the worst a bad one
does is look wrong. The generic `/upload` routes refuse a guarded asset in
both directions - that path flattens a zip to its basenames, which is right
for a folder of sounds and would put every file of a plugin in `plugins/` with
its folders gone.

The plugins asset is deliberately **not** deletable. Adding a plugin is undone
by removing it; emptying that folder from a phone takes every plugin with it.
