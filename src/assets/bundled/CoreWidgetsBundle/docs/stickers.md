# Stickers

Images and GIFs from a folder, stuck on the home screen — chosen at the panel
or sent from a phone.

> **Provided by a plugin.** Everything here comes from `corewidgetsbundle`.
> Anything reaching for it should check `client.public.has("stickers")` first.

---

## The library

A folder in the user data directory:

```
<data dir>/stickers/
```

There rather than in the install tree, because anything written inside the
install is wiped when an update is unpacked over it. It is registered as an
asset under the key `stickers`, which is what makes thumbnails reachable at
`/asset/stickers/<name>?token=…`.

| Kind | Extensions | On the panel |
|---|---|---|
| still | `png` `jpg` `jpeg` `bmp` | drawn as a pixmap |
| animated | `gif` `webp` | played with `QMovie` |
| video | `mp4` `webm` `mov` `m4v` | **stored, not yet playable** |

**Video is accepted but cannot be shown yet.** `QMovie` decodes image formats,
not video — playing one needs QtMultimedia, which is a bigger change than it
looks on a panel this size. Uploads are kept rather than refused so the library
is not lossy, they appear in both pickers labelled as video, and placing one is
refused with a reason.

**GIF is the only guaranteed animated format.** Qt ships a WebP plugin, but
whether that build animates WebP or returns a single frame varies. The widget
checks `frameCount()` and falls back to a still rather than showing nothing,
logging why at `debug`. To find out what your panel does:

```python
from PyQt6.QtGui import QMovie
m = QMovie("some_animated.webp"); print(m.isValid(), m.frameCount())
```

### Rules on the way in

The store owns these, so the panel and the API cannot disagree:

* unknown extensions are ignored, not stored
* 12MB per file
* filenames are stripped to their basename and sanitised — an upload arrives
  from the network, and `../../etc/passwd` is a filename
* a second `cat.gif` becomes `cat-2.gif`. It never overwrites: somebody has the
  first one on their home screen

---

## At the panel

**Widgets panel → Sticker → Add.** That opens the library in a searchable grid;
picking one places it.

Stickers added this way are **permanent** — saved in `widget_layout.json` with
which image they are, so they come back after a restart. Hold one to move,
resize, rotate or remove it, the same as any other widget.

`StickerWidget` is `MULTIPLE`, like the sticky note: the panel entry is a
template and every Add makes another sticker with its own key.

### The chooser hook

Any widget can ask something before the panel adds it:

```python
@classmethod
def choose_before_add(cls, client, then):
    client.dialog(MyDialog(client, on_chosen=lambda v: then(thing=v)))
```

`WidgetFramework` defers building the copy until `then(**kwargs)` is called, and
those keywords go to the widget's constructor. Cancelling simply never calls
back, so nothing is placed — which is why this is a hook rather than
"place it, then edit it".

---

## From a phone

`/public/sticker_add?token=…` is one page that does the whole job: upload
something, pick from the library, choose where it lands and how long it stays.
It is listed on the panel's index as **Stickers**.

| Field | Meaning |
|---|---|
| upload | Anything the store accepts. Validated before it is written. |
| sticker | Which one to place. |
| quadrant | One of nine regions — corners, edges, middle. |
| mode | `permanent` or `temporary`. |
| timeout | Seconds, for a temporary one, from 1 up. `0` means until it is removed. |
| scale | Small, Normal, Large, Huge, or an exact longest edge in pixels. |
| delete_after | Temporary only. Deletes the **file** when the sticker goes. Off by default. |

`delete_after` is for the throwaway case - something sent to the panel for a
minute that should not accumulate in the library. It only applies to a
temporary sticker: a permanent one deleting its own source would break every
other copy of it on screen, so the flag is ignored there whatever is passed.
The file goes when the sticker does, by timeout or by being dismissed early.

Selecting a sticker also offers **Delete** - in both the panel's picker and the
page - which removes it from the library outright. Both ask first.

The form is re-rendered with whatever was last submitted, so placing three
stickers in the same corner at the same size does not mean setting the same
three controls three times. A sticker deleted since the last submit is
deselected rather than left chosen.

**Permanent from the phone is the same as permanent at the panel** — it goes
through the widgets path and is saved. **Temporary** goes through the
[transient API](transient-widgets.md), which never persists, so a temporary
sticker is gone after a restart whether its timeout elapsed or not.

The quadrant applies either way: a permanent sticker is placed where it was
asked for rather than at the widget's default anchor, and never overlapping
something already there.

### As JSON

| Endpoint | Does |
|---|---|
| `GET /public/sticker_list` | The library. |
| `GET /public/sticker_place` | Place one. `sticker=`, `quadrant=`, `mode=`, `timeout=`, or `x=`/`y=`. |
| `GET /public/sticker_remove` | Delete one from the library. `key=`. |
| `GET|POST /public/sticker_add` | The page, and what it posts back to. |

```bash
curl "http://panel:5000/public/sticker_place?token=...\
&sticker=happy-cat.gif&quadrant=top-right&mode=temporary&timeout=120"
```

### Uploads to a plugin endpoint

`sticker_add` receives its file because it registered with `accepts_files`:

```python
client.API_REGISTRY.register(
    "myplugin", "my_upload", self.handler,
    requires_auth=True, accepts_files=True)

def handler(self, files=None, **params):
    upload = files.get("file") if files else None
```

Opt-in rather than always. Handing every endpoint a `files` keyword would give
each one an unexpected argument and a `TypeError` — the same trap `id` and
`token` already sprang.

---

## The grid dialog

The picker is `src/ui/grid_dialog.py`, and it is not sticker-specific. Anything
with more items than a list can show and a name worth searching is the same
dialog:

```python
from src.ui.grid_dialog import ItemGridDialog, GridItem

client.dialog(ItemGridDialog(
    client, title="Choose an icon",
    items=[GridItem(key=k, label=l, preview=path) for ...],
    on_chosen=lambda item: ...,
))
```

`sorts` is a list of `(key, label, keyfunc)`, rendered as a row of buttons -
a dropdown on a touch panel is two taps and a small list to aim at. A key
ending `za` or `_desc` reverses its function's ordering, so one keyfunc covers
both directions. Sorting is applied on every rebuild, so it survives a search
rather than only ordering the initial list.

`on_delete(item)` adds a confirmed Delete button, enabled only when something
is selected. Return `False` to say it did not work; the dialog drops the item
from its own list otherwise, so nothing has to be re-fetched.

Pass `items` for a fixed set, or `on_search(text) -> items` for a source that
answers queries itself — which is how a search API would drop in behind the
same dialog later. `GridItem` takes a `preview` path or a `pixmap`, so a source
with URLs and no local files works without touching the filesystem.

Picking is two steps — tap to select, button to confirm. A grid of small tiles
is exactly where a mis-tap happens on a touch screen, and this one puts
something on somebody's home screen.

Animated previews are driven by `QMovie` with the scaling set on the movie
rather than the label, and **every one is stopped when the dialog closes** —
otherwise a closed dialog carries on decoding frames.

Four things about it are load-bearing on a touch panel, and each was a bug
first:

* **The search field takes no focus.** A `QLineEdit` is the first focusable
  thing in a dialog, so Qt hands it focus on show — and a field that opens the
  keyboard on focus greets you with a keyboard over a grid you have not seen.
  It is `NoFocus`, and a tap is the only way in.
* **The scroll viewport is transparent.** A `QScrollArea`'s viewport is a
  separate widget that fills itself by default; left alone it is a white block
  behind the tiles.
* **Dragging scrolls.** `QScroller` on the viewport, as everywhere else in this
  app.
* **A drag is not a tap.** A tile only selects if the finger moved less than
  `DRAG_SLOP` pixels, or flicking through the grid picks whatever was under it.
