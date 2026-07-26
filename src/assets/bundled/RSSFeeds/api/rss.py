import copy
import json
import sys
from feedparser import parse, FeedParserDict

class RSSFeedAPI():
    ITEM_FIELD_CANDIDATES = {
        "id":        ["id", "guid", "link"],
        "title":     ["title"],
        "published": ["published", "pubDate", "updated", "date"],
        "summary":   ["summary", "description", "subtitle", "content.0.value"],
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
        headers = headers or {}
        feed: FeedParserDict = parse(url, request_headers=headers, sanitize_html=True)
        data: dict = dict(feed)
        if transformer:
            new = self.transform(data, copy.deepcopy(transformer))
            return new, data
        else:
            return data, None

    def _resolve_path(self, obj, path: str):
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