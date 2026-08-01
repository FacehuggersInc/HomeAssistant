"""
Choosing which sticker to stick on the calendar.

The bundle's library, not one of the calendar's own: a sticker uploaded from a
phone should be usable wherever stickers go, and a second folder would be a
second place to look when one is missing.
"""

from __future__ import annotations

from typing import Callable, TYPE_CHECKING

from src.ui.grid_dialog import ItemGridDialog, GridItem

if TYPE_CHECKING:
    from src.main import Client


def choose_sticker(client: "Client", entries: list, on_chosen: Callable) -> None:
    """Show the library, and hand back the filename of whichever is picked."""
    items = []
    for entry in entries:
        kind = getattr(entry, "kind", "still")
        if kind == "video":
            # Nothing draws a video into a day box, and offering one that
            # cannot be placed is a choice that fails after it is made.
            continue
        items.append(GridItem(
            key=entry.name,
            label=entry.label,
            preview=str(entry.path),
            animated=(kind == "animated"),
            badge="" if kind == "still" else kind,
            icon="mdi.image-outline",
            data=entry,
        ))

    if not items:
        client.simple_notify("mdi.sticker-emoji", "Stickers",
                             "There are no images in the library yet.")
        return

    def chosen(item) -> None:
        name = getattr(getattr(item, "data", None), "name", None) or item.key
        on_chosen(name)

    client.dialog(ItemGridDialog(
        client,
        title="Put a sticker on the calendar",
        body="Pick one, then drag it onto the day you want it on.",
        items=items,
        on_chosen=chosen,
        choose_text="Use this",
        empty_text="There are no images in the library yet.",
        search_hint="Search stickers",
    ))
