from __future__ import annotations

from datetime import datetime, timedelta
import unittest

from assistant_app.models import ProductionLogAutomation, ProductionLogAutomationRun
from assistant_app.production_log_automation import (
    automation_is_due,
    latest_scheduled_occurrence,
    source_window_start,
)


def make_automation(**overrides) -> ProductionLogAutomation:
    values = {
        "id": 1,
        "client_id": 1,
        "name": "Morning import",
        "email_folder": "Mailbox/Inbox",
        "email_subject_contains": "Daily",
        "email_subject_exact": False,
        "email_sender_contains": "sender@example.com",
        "email_body_contains": "",
        "required_category": "",
        "completed_category": "",
        "attachment_pattern": "*.csv",
        "routing_column": "Channel",
        "update_mode": "append_empty",
        "source_sort_column": "",
        "source_date_column": "",
        "target_date_column": "A",
        "scheduled_time": "08:00",
        "weekdays": list(range(7)),
        "lookback_days": 1,
        "catch_up": True,
        "retry_minutes": 15,
        "paused": False,
        "created_at": datetime(2026, 8, 1, 12, 0),
        "updated_at": None,
    }
    values.update(overrides)
    return ProductionLogAutomation(**values)


def make_run(scheduled_for: datetime, status: str, started_at: datetime) -> ProductionLogAutomationRun:
    return ProductionLogAutomationRun(
        id=1,
        automation_id=1,
        trigger_type="scheduled",
        scheduled_for=scheduled_for,
        status=status,
        started_at=started_at,
        completed_at=started_at,
        attachments_processed=0,
        rows_written=0,
        cells_written=0,
        message="",
    )


class ProductionLogSchedulingTests(unittest.TestCase):
    def test_daily_run_catches_up_after_computer_starts_late(self) -> None:
        automation = make_automation()
        now = datetime(2026, 8, 4, 10, 30)
        self.assertEqual(automation_is_due(automation, [], now), datetime(2026, 8, 4, 8, 0))

    def test_successful_scheduled_occurrence_does_not_repeat(self) -> None:
        automation = make_automation()
        now = datetime(2026, 8, 4, 10, 30)
        scheduled = datetime(2026, 8, 4, 8, 0)
        run = make_run(scheduled, "success", datetime(2026, 8, 4, 8, 1))
        self.assertIsNone(automation_is_due(automation, [run], now))

    def test_no_data_retries_after_configured_interval(self) -> None:
        automation = make_automation(retry_minutes=15)
        scheduled = datetime(2026, 8, 4, 8, 0)
        run = make_run(scheduled, "no_data", datetime(2026, 8, 4, 8, 5))
        self.assertIsNone(automation_is_due(automation, [run], datetime(2026, 8, 4, 8, 19)))
        self.assertEqual(
            automation_is_due(automation, [run], datetime(2026, 8, 4, 8, 20)),
            scheduled,
        )

    def test_paused_automation_is_not_due(self) -> None:
        automation = make_automation(paused=True)
        self.assertIsNone(automation_is_due(automation, [], datetime(2026, 8, 4, 10, 0)))

    def test_monday_profile_is_caught_up_on_tuesday(self) -> None:
        automation = make_automation(weekdays=[0], created_at=datetime(2026, 8, 1, 12, 0))
        now = datetime(2026, 8, 4, 9, 0)  # Tuesday
        self.assertEqual(latest_scheduled_occurrence(automation, now), datetime(2026, 8, 3, 8, 0))

    def test_three_prior_days_begin_on_friday_for_monday_run(self) -> None:
        automation = make_automation(weekdays=[0], lookback_days=3)
        scheduled = datetime(2026, 8, 3, 8, 0)  # Monday
        self.assertEqual(source_window_start(automation, scheduled), datetime(2026, 7, 31, 0, 0))

    def test_non_catch_up_profile_only_runs_near_scheduled_time(self) -> None:
        automation = make_automation(catch_up=False)
        self.assertEqual(
            automation_is_due(automation, [], datetime(2026, 8, 4, 8, 1)),
            datetime(2026, 8, 4, 8, 0),
        )
        self.assertIsNone(automation_is_due(automation, [], datetime(2026, 8, 4, 8, 3)))


if __name__ == "__main__":
    unittest.main()
