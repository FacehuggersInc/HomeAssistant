import copy
import json
import re
import sys
import urllib.request
from feedparser import parse, FeedParserDict


class _BrowserRequest(urllib.request.BaseHandler):
    """
    Strip the headers feedparser adds that a browser never sends.

    `A-IM: feed` is RFC 3229 delta encoding. Nothing but a feed reader has
    ever sent it, so it identifies the request as a tool however carefully the
    rest of the headers are set - and feedparser adds it after the caller's
    own headers are applied, so passing one cannot override it.

    A handler is the supported way in: feedparser takes `handlers` and runs
    them on the way out.
    """

    #handlers run in order; this one only edits, so it can go early
    handler_order = 100
    STRIP = ("A-IM",)

    def http_request(self, request):
        for name in self.STRIP:
            if request.has_header(name.capitalize()):
                request.remove_header(name.capitalize())
        return request

    https_request = http_request


class RSSFeedAPI():
    def __init__(self):
        # Paths a transformer asked for that a feed did not have. Collected
        # rather than printed: it is normal, and one line per entry per field
        # would bury everything else in the log.
        self.missing_paths = set()

    ITEM_FIELD_CANDIDATES = {
        "id":        ["id", "guid", "link"],
        "title":     ["title"],
        "published": ["published", "pubDate", "updated", "date"],
        "summary":   ["summary", "description", "subtitle", "content.0.value"],
        "author":    ["author", "author_detail.name", "authors.0.name", "dc_creator"],
    }

    FEED_TITLE_CANDIDATES = ["title", "subtitle"]

    #Where an image hides in a feed entry, in the order worth trying.
    #
    #Not part of ITEM_FIELD_CANDIDATES because none of these is a plain path:
    #media namespaces put the URL on an attribute of a list entry, enclosures
    #have to be filtered by MIME type, and a great many feeds - Steam's daily
    #deals among them - have no image field at all and simply embed an <img>
    #in the description HTML.
    IMAGE_ATTRS = ("url", "href", "src")

    @staticmethod
    def _first_url(value):
        """A URL out of whatever shape a feed put it in."""
        if not value:
            return None
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, dict):
            for attr in RSSFeedAPI.IMAGE_ATTRS:
                found = value.get(attr)
                if isinstance(found, str) and found.strip():
                    return found.strip()
            return None
        if isinstance(value, (list, tuple)):
            for item in value:
                found = RSSFeedAPI._first_url(item)
                if found:
                    return found
        return None

    @staticmethod
    def _image_from_html(html: str):
        """The first <img src> in a blob of HTML."""
        if not html or not isinstance(html, str):
            return None
        match = re.search(r"""<img[^>]+src\s*=\s*["']([^"']+)["']""",
                          html, re.I)
        return match.group(1).strip() if match else None

    @staticmethod
    def _all_images_from_html(html: str) -> list:
        """Every <img src> in a blob of HTML, in order."""
        if not html or not isinstance(html, str):
            return []
        return [m.strip() for m in re.findall(
            r"""<img[^>]+src\s*=\s*["']([^"']+)["']""", html, re.I) if m.strip()]

    def extract_images(self, entry) -> list:
        """
        Every picture in an entry, best first, without duplicates.

        Declared images come before scraped ones, because a feed that bothers
        to declare a thumbnail has chosen a better one than whatever happens
        to be first in its description.
        """
        if not isinstance(entry, (dict, FeedParserDict)):
            return []

        found = []

        def keep(url):
            url = str(url or "").strip()
            # Same picture at a different size is still the same picture, so
            # the query string is ignored when comparing.
            if not url:
                return
            key = url.split("?")[0]
            if any(existing.split("?")[0] == key for existing in found):
                return
            found.append(url)

        for key in ("media_thumbnail", "media_content"):
            value = entry.get(key)
            if isinstance(value, (list, tuple)):
                for item in value:
                    keep(self._first_url(item))
            else:
                keep(self._first_url(value))

        # Enclosures and links, filtered to things that are actually images -
        # a podcast enclosure is an mp3 and would be a broken picture.
        for key in ("enclosures", "links"):
            for candidate in (entry.get(key) or []):
                if not isinstance(candidate, (dict, FeedParserDict)):
                    continue
                kind = str(candidate.get("type") or "")
                if kind.startswith("image/"):
                    keep(candidate.get("href") or candidate.get("url"))

        for key in ("summary", "description"):
            for url in self._all_images_from_html(entry.get(key)):
                keep(url)

        for block in (entry.get("content") or []):
            if isinstance(block, (dict, FeedParserDict)):
                for url in self._all_images_from_html(block.get("value")):
                    keep(url)

        return found

    def extract_image(self, entry) -> str:
        """The best single picture for an entry, or ""."""
        images = self.extract_images(entry)
        return images[0] if images else ""

    ## ── Reddit ───────────────────────────────────────────────────────────────
    #
    # Reddit's Atom feeds parse cleanly - every field is inferred correctly -
    # but what comes out is unusable as-is:
    #
    #   * the author is "/u/name", which is noise in a tag
    #   * the body is Reddit's own chrome. A link post's entire content is a
    #     table holding a thumbnail, "submitted by /u/name", "[link]" and
    #     "[comments]" - so once the picture is lifted out to the top of the
    #     panel there is nothing left but three words of furniture
    #   * thumbnails are tiny. `?width=140` stretched across a 780px panel is
    #     a blurry mess, and the same URL will serve a larger one for asking

    REDDIT_HOSTS = ("reddit.com", "redd.it")
    #what to ask Reddit's preview host for
    REDDIT_IMAGE_WIDTH = 960

    @staticmethod
    def looks_like_reddit(data: dict) -> bool:
        """Whether a parsed feed came from Reddit."""
        for source in (data.get("href", ""),
                       (data.get("feed") or {}).get("link", ""),
                       (data.get("feed") or {}).get("id", "")):
            if any(host in str(source) for host in RSSFeedAPI.REDDIT_HOSTS):
                return True
        for entry in (data.get("entries") or [])[:3]:
            if any(host in str(entry.get("link") or "")
                   for host in RSSFeedAPI.REDDIT_HOSTS):
                return True
        return False

    @staticmethod
    def tidy_author(author: str) -> str:
        """'/u/name' -> 'name'. Harmless anywhere else."""
        text = str(author or "").strip()
        for prefix in ("/u/", "u/", "/user/"):
            if text.lower().startswith(prefix):
                return text[len(prefix):].strip()
        return text

    @classmethod
    def widen_reddit_image(cls, url: str) -> str:
        """
        Ask Reddit's preview host for a bigger copy.

        The width is a query parameter it honours, and the signature covers
        the path rather than the size - so raising it is safe. Only raised,
        never lowered: a feed that already offers 1200px should keep it.
        """
        text = str(url or "")
        if "redd.it" not in text or "width=" not in text:
            return text
        def bump(match):
            try:
                current = int(match.group(1))
            except ValueError:
                return match.group(0)
            if current >= cls.REDDIT_IMAGE_WIDTH:
                return match.group(0)
            return f"width={cls.REDDIT_IMAGE_WIDTH}"
        text = re.sub(r"width=(\d+)", bump, text)
        # The paired height would now be wrong, and Reddit works it out from
        # the width alone when it is absent.
        return re.sub(r"[&?]height=\d+", "", text)

    #Reddit's own furniture, in the order it has to be removed
    _REDDIT_CHROME = (
        # the SC_OFF/SC_ON markers around user-written markdown
        (r"<!--\s*SC_(?:OFF|ON)\s*-->", ""),
        # "submitted by /u/name", including the link around the name.
        #
        # The gap has to allow entities as well as whitespace: Reddit writes
        # "submitted by &#32; <a ...>" and a plain \s* stops at the &#32;,
        # leaving the author's name behind as the entire article body.
        (r"submitted\s+by(?:\s|&#32;|&nbsp;)*(?:<a[^>]*>.*?</a>)?", ""),
        # the [link] and [comments] anchors
        (r"<a[^>]*>\s*\[(?:link|comments)\]\s*</a>", ""),
        (r"\[(?:link|comments)\]", ""),
    )

    @classmethod
    def clean_reddit_html(cls, html: str) -> str:
        """
        Strip Reddit's furniture, leaving whatever the person actually wrote.

        The table wrapper goes too: it exists to put the thumbnail beside the
        text, and the panel already shows the thumbnail across the top.
        """
        text = str(html or "")
        if not text:
            return ""

        for pattern, replacement in cls._REDDIT_CHROME:
            text = re.sub(pattern, replacement, text, flags=re.I | re.S)

        # Unwrap the layout table rather than deleting it - deleting takes the
        # self-post body with it on the posts that have one.
        text = re.sub(r"</?(?:table|tbody|thead|tr|td|th)[^>]*>", " ", text,
                      flags=re.I)
        # The anchor whose only content is the thumbnail. The panel shows
        # that picture across its top, so what is left here is an invisible
        # link the size of nothing.
        text = re.sub(r"<a[^>]*>\s*(?:<img[^>]*>\s*)+</a>", "", text,
                      flags=re.I)
        # Anchors left wrapping nothing once their label was removed.
        text = re.sub(r"<a[^>]*>\s*</a>", "", text, flags=re.I)
        text = re.sub(r"(?:\s|&#32;|&nbsp;)+", " ", text)
        text = re.sub(r"(?:<br\s*/?>\s*)+", "<br>", text, flags=re.I)
        text = re.sub(r"^(?:\s|<br>)+|(?:\s|<br>)+$", "", text, flags=re.I)
        return text.strip()

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
                            # A field the feed did not send. Normal - feeds
                            # are inconsistent and a transformer is inferred
                            # from a sample - so it is recorded rather than
                            # printed, and the key is simply left out.
                            self.missing_paths.add(".".join(str(p) for p in path))
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

    #Sent on every fetch.
    #
    #A User-Agent alone is not enough. Hosts behind a bot filter look at the
    #whole request: a browser sends an Accept list, a language, an encoding
    #and the Sec-Fetch set, and a request carrying only a User-Agent is an
    #obvious tool however that agent is spelled. Reddit answers 403 to one and
    #200 to the other from the same address.
    #
    #feedparser does not raise on an HTTP error either - it returns a result
    #with a status and no entries - so without checking, a refused feed is
    #indistinguishable from an empty one.
    USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

    DEFAULT_HEADERS = {
        "User-Agent": USER_AGENT,
        # Feeds first, since that is honestly what is wanted, but the browser
        # types too - some filters reject a request that asks only for XML.
        "Accept": ("application/atom+xml,application/rss+xml,application/xml;"
                   "q=0.9,text/xml;q=0.9,text/html;q=0.8,*/*;q=0.7"),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }


    #statuses worth naming when a fetch comes back empty
    STATUS_REASONS = {
        403: "refused the request (403)",
        404: "does not exist (404)",
        410: "has been removed (410)",
        429: "is rate limiting us (429)",
        500: "had a server error (500)",
        503: "is unavailable (503)",
    }

    def fetch_problem(self, feed) -> str:
        """
        Why a fetch came back with nothing, or "" if it did not.

        feedparser reports failure in the return value rather than by raising,
        so without this a blocked or missing feed is indistinguishable from an
        empty one - which is exactly how a feed can be subscribed, look fine,
        and never once appear.
        """
        entries = feed.get("entries") or []
        if entries:
            return ""

        status = feed.get("status")
        if status and status != 200:
            return self.STATUS_REASONS.get(status, f"answered {status}")

        exception = feed.get("bozo_exception")
        if exception is not None:
            return f"could not be read ({type(exception).__name__}: {exception})"

        return "returned no items"

    def transform_data(self, data: dict, transformer: dict) -> dict:
        """
        Apply a transformer to a feed already fetched.

        `parse()` with a transformer refetches. Inferring one and then using it
        therefore costs two requests for every feed, back to back - which is
        enough on its own to trip Reddit's rate limiter on the very first load.
        """
        new = self.transform(dict(data), copy.deepcopy(transformer))
        self._attach_extras(new, data)
        return new

    def _attach_extras(self, new: dict, data: dict) -> None:
        """
        Images, links and Reddit tidying, matched back by position.

        Kept out of the transformer because none of it is a path: images hide
        in four different places, and a transformer follows paths.
        """
        entries = data.get("entries") or []
        items = new.get("items") if isinstance(new, dict) else None
        reddit = self.looks_like_reddit(data)

        if isinstance(items, list):
            for index, item in enumerate(items):
                if not isinstance(item, dict) or index >= len(entries):
                    continue
                images = self.extract_images(entries[index])
                if reddit:
                    images = [self.widen_reddit_image(u) for u in images]
                item.setdefault("images", images)
                item.setdefault("image", images[0] if images else "")
                item.setdefault("link", str(entries[index].get("link") or ""))

                if item.get("author"):
                    item["author"] = self.tidy_author(item["author"])
                if reddit and item.get("summary"):
                    item["summary"] = self.clean_reddit_html(item["summary"])

        if reddit and isinstance(new, dict):
            new["title"] = self.tidy_feed_title(new.get("title"), data)

    def parse(self, url: str, headers: dict = None, transformer: dict = None) -> tuple[dict, dict]:
        # A feed's own headers win, so one that needs something specific can
        # say so in its file without every other feed inheriting it.
        headers = {**self.DEFAULT_HEADERS, **(headers or {})}
        feed: FeedParserDict = parse(url, request_headers=headers,
                                     handlers=[_BrowserRequest()],
                                     sanitize_html=True)
        data: dict = dict(feed)
        if not transformer:
            return data, None

        new = self.transform(data, copy.deepcopy(transformer))
        self._attach_extras(new, data)
        return new, data

    @staticmethod
    def tidy_feed_title(title: str, data: dict) -> str:
        """'top scoring links : anime' -> 'r/anime'."""
        text = str(title or "").strip()
        tags = (data.get("feed") or {}).get("tags") or []
        for tag in tags:
            label = str((tag or {}).get("label") or "")
            if label.startswith("r/"):
                return label
        match = re.search(r"\br/(\w+)", text)
        if match:
            return f"r/{match.group(1)}"
        match = re.search(r":\s*(\w+)\s*$", text)
        if match:
            return f"r/{match.group(1)}"
        return text

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