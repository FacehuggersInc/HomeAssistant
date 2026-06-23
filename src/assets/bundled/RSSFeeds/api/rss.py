import copy
import json
import sys
from feedparser import parse, FeedParserDict


class RSSFeedAPI():
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
        if transformer:
            new = self.transform(data, transformer)
            return new, data
        else:
            return data, None