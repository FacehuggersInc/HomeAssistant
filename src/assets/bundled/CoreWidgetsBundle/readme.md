### A bundle of widgets and other UI objects
- Handles the home page (sub home and sub tiles pages)
- Gives *Widgets* and *Tiles* of out the box
- Handles all of the *core* experience 

## Docs in this folder

| File                        | About                                                                                                 |
|-----------------------------|-------------------------------------------------------------------------------------------------------|
| `docs/action-tile.md`       | The action tile — pointing one at anything registered, and deciding how it looks from what came back. |
| `docs/stickers.md`          | The sticker widget and its library.                                                                   |
| `docs/transient-widgets.md` | Widgets placed by something happening rather than by somebody arranging their screen.                 |

For the frameworks these sit in rather than these particular tiles, see the
panel's own `docs/widgets.md` and `docs/tiles.md`.


## What it exposes

Under the key `corewidgetsbundle`, on the public registry:

| Name                           | Is                     | For                                                                                                                              |
|--------------------------------|------------------------|----------------------------------------------------------------------------------------------------------------------------------|
| `timers`                       | A dict of callables    | `start`, `cancel`, `cancel_all`, `cancel_matching`, `find`, `get`, `running`, `all`, and `describe` for saying a duration aloud. |
| `stickers`                     | A dict of callables    | Placing and listing stickers. See `docs/stickers.md`.                                                                            |
| `notification_history`         | The history manager    | Reading and reopening past notifications.                                                                                        |
| `cwb_widgets`, `cwb_sub_pages` | Registration helpers   | How other plugins add to the home page.                                                                                          |
| `cwb_wallpaper`                | The wallpaper controls | Setting what the home page sits on.                                                                                              |

Reach them through `client.public`, guarded with `public.has(name)`, and
declare `corewidgetsbundle` in your `dependencies`. See "Reaching another
plugin" in the panel's own `docs/plugins.md`.

## Two tiles worth knowing about

**Bookmark** and **action** are both `MULTIPLE`: the panel entry is a template
and every one placed is a copy with its own key and its own settings. Both are
`EDITABLE`, so a pencil appears on the chrome when they are selected and opens
what they were set up with rather than running them.

The action tile is the general one. It can be pointed at anything callable that
any registry knows about, which makes it powerful and makes it possible to
point it at something unsuitable — `docs/action-tile.md` says what it can and
cannot sensibly do.
