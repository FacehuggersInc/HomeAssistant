# RSS Feeds API and Idle Panels

- REQUIRES: "IdleRandomTriggers" Plugin (bundled)
- Add a feed two ways: call `add_rss_feed(plugin_key, url, transformer)`, or drop a `{"url":..., "transformer":...}` JSON file into `RSSFeeds/` in the app's working directory (auto-loaded under the `rssfeeds` key).
- `transformer` maps feedparser's data into `{"title": ..., "items": [{"id", "title", "published", "summary", "author"}]}`. Use `"COMPACT"` paths to map a sub-transformer over a list (e.g. `entries` → `items`).
- `transformer` is optional — if omitted, one is auto-inferred from the feed's own data the first time it's used (checking which of the expected keys are actually present) and cached for reuse.
- `id`, `title`, `summary` are expected per item; `published`/`author`/feed `title` are optional and just get omitted from the panel if missing.