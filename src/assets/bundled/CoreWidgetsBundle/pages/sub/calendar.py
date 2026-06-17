from src.ui.page import SubPageFramework


class SubCalendarPage(SubPageFramework):
    """SubCalendarPage — stub. Awaiting full PyQt6 implementation."""

    def __init__(self, client, page=None):
        super().__init__(client=client, key="calendar", coord=(0, 1))
        self.setStyleSheet("background-color: #0d0d0d;")