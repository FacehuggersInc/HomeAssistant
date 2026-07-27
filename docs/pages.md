# Pages

Pages own UI systems and features to interact with them.

Pages should be responsible for organizing and displaying content.

Pages often expose Features that plugins can interact with.

Examples from `CoreWidgetsBundle`:

```text
HomePage

SubHomePage

SubTilesPage
```

Pages may own systems such as:

* WidgetFramework
* TileGrid
* TilePanel
* Sub page navigation

Pages own UI.

Plugins extend Pages.

---
