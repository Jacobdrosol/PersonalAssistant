from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import patch

from assistant_app.new_outlook_ui import (
    NewOutlookUiSource,
    _folder_display_name,
    _parse_outlook_display_datetime,
)


class NewOutlookUiParsingTests(unittest.TestCase):
    def test_folder_display_name_removes_accessibility_status(self) -> None:
        self.assertEqual(_folder_display_name("RU selected 1 unread", ""), "RU")
        self.assertEqual(_folder_display_name("", "RU - 2 items"), "RU")

    def test_outlook_display_datetime_parses_weekday_prefix(self) -> None:
        self.assertEqual(
            _parse_outlook_display_datetime("Tue 8/4/2026 3:13 PM"),
            datetime(2026, 8, 4, 15, 13),
        )

    def test_outlook_display_datetime_rejects_unknown_text(self) -> None:
        self.assertIsNone(_parse_outlook_display_datetime("Yesterday afternoon"))

    @patch("assistant_app.new_outlook_ui.win32gui.MessageBox")
    def test_control_notice_is_shown_only_once_per_source_instance(self, message_box) -> None:
        source = NewOutlookUiSource()

        source._warn_before_control(123)
        source._warn_before_control(123)

        message_box.assert_called_once()


if __name__ == "__main__":
    unittest.main()
