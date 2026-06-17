from src.ui.page import PageFramework


class SettingsPage(PageFramework):
    """
    SettingsPage — stub implementation.
    Awaiting PyQt rewrite. Boots cleanly with a blank page.
    """

    def __init__(self, client, data=None):
        super().__init__(key="#settings", client=client, data=data)
        self.setStyleSheet(f"background-color: #0d0d0d;")
        client.log("info", "[SettingsPage] Stub loaded — awaiting PyQt rewrite.")

    def start(self):
        super().start()

    def stop(self):
        super().stop()