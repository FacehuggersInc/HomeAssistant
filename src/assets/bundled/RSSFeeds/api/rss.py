import copy
import json
import sys
from feedparser import parse, FeedParserDict

class RSSFeedAPI():
    ITEM_FIELD_CANDIDATES = {
        # 'id' is RSS/Atom's own item id; 'guid' is the older RSS tag
        # name some parsers leave un-normalized; 'link' is the one
        # thing virtually every entry has, used as a last-resort
        # unique-ish fallback.
        "id":        ["id", "guid", "link"],
        "title":     ["title"],
        # Atom's <updated> is required, <published> is optional — some
        # feeds only ever set the former. 'pubDate'/'date' cover feeds
        # feedparser didn't fully normalize.
        "published": ["published", "pubDate", "updated", "date"],
        # RSS uses <description>, Atom uses <summary> or full <content>
        # (a list of {"value": ...} blocks); 'subtitle' is a rarer
        # stand-in some templates reuse for a short blurb.
        "summary":   ["summary", "description", "subtitle", "content.0.value"],
        # A bare <author> string is the simple case. Atom can instead
        # give a structured author_detail dict or a list of authors;
        # 'dc_creator' covers Dublin Core feeds.
        "author":    ["author", "author_detail.name", "authors.0.name", "dc_creator"],
    }

    FEED_TITLE_CANDIDATES = ["title", "subtitle"]

    def transform(self, data: dict, transformer: dict) -> dict:
        for key in list(transformer.keys()):
            if not transformer.get(key) or not isinstance(transformer[key], str):
                continue  # Only supports strings for path following, dicts are used as sub-transformers (still skipped)

            path: list[str] = transformer[key].split(".")
            mode = "NORMAL"
            pointer = data  # Reset starting point to data

            for i, path_key in enumerate(path):
                match mode:
                    case "NORMAL":
                        if path_key.isnumeric():
                            path_key = int(path_key)

                        match path_key:
                            case "COMPACT":
                                mode = "COMPACT"
                                continue

                        value = None
                        try:
                            value = pointer[path_key]
                        except Exception:
                            print(f"[RSSFeedAPI.transform] Could not get '{path_key}' within {type(pointer)}. ({path})")
                            pointer = None
                            break
                        pointer = value

                    case "COMPACT":
                        iterable = pointer
                        sub_transformer = transformer.get(path_key)
                        del transformer[path_key]

                        if isinstance(iterable, list) and sub_transformer:
                            value = []
                            for item in iterable:
                                new_sub_transform = self.transform(item, copy.deepcopy(sub_transformer))
                                value.append(new_sub_transform)

                            max_keys = 0
                            for val in value:
                                if len(val) > max_keys:
                                    max_keys = len(val)

                            if max_keys == 1:
                                value = [v[list(v.keys())[0]] for v in value]

                            pointer = value
                        else:
                            pointer = None

            transformer[key] = pointer
        return transformer

    def parse(self, url: str, headers: dict = None, transformer: dict = None) -> tuple[dict, dict]:
        """Returns the transformed data and the original data, each value in the transformer is a path to a value in the data given by feedparser. An example is 'path.to.list.3.id'"""
        headers = headers or {}
        feed: FeedParserDict = parse(url, request_headers=headers, sanitize_html=True)
        data: dict = dict(feed)
        with open("test.json", "w") as jfile:
            json.dump( data, jfile, indent=4 )
        if transformer:
            new = self.transform(data, copy.deepcopy(transformer))
            return new, data
        else:
            return data, None

    def _resolve_path(self, obj, path: str):
        """
        Plain dot-path lookup against a single object — used only to test
        whether a candidate field is actually present (and non-empty) on
        a sample entry while inferring a transformer. This is a simple
        read, not transform()'s path language: no COMPACT support, since
        we're only ever probing one entry/feed dict at a time here.
        """
        pointer = obj
        for segment in path.split("."):
            if isinstance(pointer, list):
                if not segment.isnumeric():
                    return None
                index = int(segment)
                if index >= len(pointer):
                    return None
                pointer = pointer[index]
            elif isinstance(pointer, dict):
                if segment not in pointer:
                    return None
                pointer = pointer[segment]
            else:
                return None
        return pointer

    def infer_transformer(self, data: dict) -> dict:
        """
        Best-effort transformer for a feed that wasn't given one.

        Rather than checking one literal key per field, every canonical
        item field (id/title/published/summary/author) has a short list
        of candidate paths covering the common RSS/Atom/RDF templates —
        see ITEM_FIELD_CANDIDATES. Each candidate is tried against a
        small sample of entries (not just the first one, since optional
        fields are sometimes missing from an individual item even when
        the feed as a whole consistently provides them) and the
        candidate that actually resolves to a non-empty value on the
        most sampled entries wins.

        Meant to be called once per feed and cached afterwards — see
        RSSFeedsPlugin.build_new_feed_panel().
        """
        entries = data.get('entries') or []
        sample = entries[:5]  # a few entries, in case the first one happens to be missing an otherwise-common field

        entry_map = {}
        for key, candidates in self.ITEM_FIELD_CANDIDATES.items():
            best_path, best_score = None, 0
            for path in candidates:
                score = sum(1 for entry in sample if self._resolve_path(entry, path))
                if score > best_score:
                    best_path, best_score = path, score
            if best_path:
                entry_map[key] = best_path

        transformer = {
            "items": "entries.COMPACT.entry_map",
            "entry_map": entry_map,
        }

        feed = data.get('feed')
        if isinstance(feed, dict):
            for path in self.FEED_TITLE_CANDIDATES:
                if self._resolve_path(feed, path):
                    transformer["title"] = f"feed.{path}"
                    break

        return transformer