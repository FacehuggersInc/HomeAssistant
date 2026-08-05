"""
Going somewhere: a page of the panel, or a saved site.
"""

from __future__ import annotations

from src.assistant.skill import Skill


def build(plugin, wake: str, key: str) -> list:
    """The skills in this group, wired to `plugin`'s handlers."""
    return [
        Skill(
                        wake_word=wake, skill_key="go-to-page", plugin_key=key,
                        examples=[
                            "show the calendar", "open the calendar", "go to settings",
                            "show me the home page", "open settings", "go home",
                            "take me home", "show the clock", "open the web page",
                        ],
                        arguments={
                            "page_name": [
                                [{"LOWER": {"IN": ["the", "me", "to"]}, "OP": "*"},
                                 {"IS_ALPHA": True, "OP": "{1,3}"}],
                            ]
                        },
                        func=plugin.go_to_page,
                    ),
        Skill(
                        wake_word=wake, skill_key="open-bookmark", plugin_key=key,
                        examples=[
                            "open scryfall", "open my scryfall bookmark",
                            "go to scryfall", "open the bookmark for scryfall",
                            "bring up scryfall", "open bookmark scryfall",
                        ],
                        arguments={
                            # Everything after the verb, however long.
                            #
                            # A bookmark is named by whoever saved it and the title
                            # comes from the page, so it can be anything - "Scryfall"
                            # or "Advanced Search - Scryfall". Anchoring on the verb
                            # and taking the rest is the only shape that works; the
                            # matching happens in the handler, against the list.
                            "wanted": [
                                [{"LOWER": {"IN": ["open", "goto", "launch"]}},
                                 {"IS_ALPHA": True, "OP": "+"}],
                                [{"LOWER": "go"}, {"LOWER": "to"},
                                 {"IS_ALPHA": True, "OP": "+"}],
                            ]
                        },
                        func=plugin.open_bookmark,
                    ),
    ]
