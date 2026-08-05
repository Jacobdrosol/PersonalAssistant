from __future__ import annotations

import unittest
from unittest.mock import patch

from assistant_app.ui.views.production_log import ProductionLogView, SheetMappingManager


class _Value:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value


class ProductionLogViewValidationTests(unittest.TestCase):
    def _view_with_rows(self, header: str, data_start: str) -> ProductionLogView:
        view = ProductionLogView.__new__(ProductionLogView)
        view.header_row_var = _Value(header)
        view.data_start_row_var = _Value(data_start)
        return view

    def test_sheet_rows_are_configured_per_selected_sheet(self) -> None:
        view = self._view_with_rows("10", "11")
        self.assertEqual(view._sheet_row_numbers(), (10, 11))

    def test_production_log_has_no_secondary_pin_lock(self) -> None:
        view = ProductionLogView.__new__(ProductionLogView)
        self.assertFalse(view.is_locked())

    def test_data_start_must_follow_header(self) -> None:
        view = self._view_with_rows("10", "10")
        with self.assertRaisesRegex(ValueError, "after the header"):
            view._sheet_row_numbers()

    def test_route_values_accept_commas_semicolons_and_lines(self) -> None:
        view = ProductionLogView.__new__(ProductionLogView)
        view.route_values_var = _Value("MAIL, EMAIL;GIFT\nAUTO")
        self.assertEqual(view._parse_route_values(), ["MAIL", "EMAIL", "GIFT", "AUTO"])

    @patch("assistant_app.ui.views.production_log.os.startfile", create=True)
    def test_setup_guide_button_opens_the_markdown_guide(self, startfile) -> None:
        view = ProductionLogView.__new__(ProductionLogView)

        view._open_setup_guide()

        opened_path = str(startfile.call_args.args[0])
        self.assertTrue(opened_path.endswith("docs\\production-log-setup-guide.md"))

    def test_mapping_manager_accepts_column_labels_or_plain_letters(self) -> None:
        self.assertEqual(SheetMappingManager._destination_column("B - Dry Run ID"), "B")
        self.assertEqual(SheetMappingManager._destination_column("aa"), "AA")


if __name__ == "__main__":
    unittest.main()
