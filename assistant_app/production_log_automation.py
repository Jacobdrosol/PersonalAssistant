from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
import json
from pathlib import Path
from typing import Optional, Sequence

from .database import Database
from .models import ProductionLogAutomation, ProductionLogAutomationRun
from .production_log_engine import (
    ImportResult,
    DateMatchedProductionLogUpdater,
    OutlookCsvSource,
    ProductionLogError,
    ProductionLogUpdater,
    SheetImportRule,
)


@dataclass(slots=True)
class AutomationDecision:
    automation: ProductionLogAutomation
    scheduled_for: datetime


@dataclass(slots=True)
class AutomationExecutionResult:
    status: str
    attachments_processed: int
    import_result: ImportResult
    message: str


def normalize_scheduled_time(value: str) -> str:
    parts = str(value or "").strip().split(":")
    if len(parts) != 2:
        raise ValueError("Time must use HH:MM format.")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError("Time must use HH:MM format.") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Enter a valid time between 00:00 and 23:59.")
    return f"{hour:02d}:{minute:02d}"


def latest_scheduled_occurrence(
    automation: ProductionLogAutomation, now: datetime
) -> Optional[datetime]:
    scheduled_text = normalize_scheduled_time(automation.scheduled_time)
    hour, minute = (int(item) for item in scheduled_text.split(":"))
    selected_days = set(automation.weekdays)
    for offset in range(0, 8):
        candidate_date = now.date() - timedelta(days=offset)
        if candidate_date.weekday() not in selected_days:
            continue
        candidate = datetime.combine(candidate_date, time(hour=hour, minute=minute))
        if candidate > now:
            continue
        if candidate.date() < automation.created_at.date():
            return None
        return candidate
    return None


def automation_is_due(
    automation: ProductionLogAutomation,
    runs: Sequence[ProductionLogAutomationRun],
    now: datetime,
) -> Optional[datetime]:
    if automation.paused:
        return None
    scheduled_for = latest_scheduled_occurrence(automation, now)
    if scheduled_for is None:
        return None
    if not automation.catch_up and now - scheduled_for > timedelta(minutes=2):
        return None
    slot_runs = [item for item in runs if _same_minute(item.scheduled_for, scheduled_for)]
    if any(item.status == "success" for item in slot_runs):
        return None
    if any(item.status == "running" and now - item.started_at < timedelta(hours=2) for item in slot_runs):
        return None
    if slot_runs:
        latest_attempt = max(slot_runs, key=lambda item: item.started_at)
        if now - latest_attempt.started_at < timedelta(minutes=automation.retry_minutes):
            return None
    return scheduled_for


def find_due_automations(db: Database, now: Optional[datetime] = None) -> list[AutomationDecision]:
    current = now or datetime.now()
    decisions: list[AutomationDecision] = []
    for automation in db.get_production_log_automations():
        try:
            scheduled_for = automation_is_due(
                automation,
                db.get_production_log_automation_runs(automation.id, limit=100),
                current,
            )
        except ValueError:
            continue
        if scheduled_for is not None:
            decisions.append(AutomationDecision(automation=automation, scheduled_for=scheduled_for))
    return decisions


def source_window_start(automation: ProductionLogAutomation, scheduled_for: datetime) -> datetime:
    return datetime.combine(
        scheduled_for.date() - timedelta(days=automation.lookback_days),
        time.min,
    )


class ProductionLogAutomationRunner:
    def __init__(self, db: Database) -> None:
        self.db = db

    def run(
        self,
        automation: ProductionLogAutomation,
        *,
        scheduled_for: datetime,
        trigger_type: str,
    ) -> AutomationExecutionResult:
        run_id = self.db.start_production_log_automation_run(
            automation.id,
            trigger_type=trigger_type,
            scheduled_for=scheduled_for,
        )
        combined = ImportResult()
        processed = 0
        try:
            client = self.db.get_production_log_client(automation.client_id)
            if client is None:
                raise ProductionLogError("The automation's client no longer exists.")
            if not client.workbook_path:
                raise ProductionLogError("Select a production log workbook for this client.")
            rules = [
                SheetImportRule(
                    sheet_name=config.sheet_name,
                    data_start_row=config.data_start_row,
                    destination_columns=config.column_mappings,
                    source_columns=config.source_mappings,
                    route_values=config.route_values,
                )
                for config in self.db.get_production_log_sheet_configs(client.id)
            ]
            if automation.update_mode == "match_date":
                updater = DateMatchedProductionLogUpdater(
                    Path(client.workbook_path),
                    routing_column=automation.routing_column,
                    source_date_column=automation.source_date_column,
                    source_sort_column=automation.source_sort_column,
                    target_date_column=automation.target_date_column,
                    rules=rules,
                )
            else:
                updater = ProductionLogUpdater(
                    Path(client.workbook_path),
                    automation.routing_column,
                    rules,
                )
            window_start = source_window_start(automation, scheduled_for)
            window_end = datetime.now()
            outlook_source = OutlookCsvSource()
            attachments = outlook_source.fetch_pending(
                folder_path=automation.email_folder,
                subject_contains=automation.email_subject_contains,
                subject_exact=automation.email_subject_exact,
                sender_contains=automation.email_sender_contains,
                body_contains=automation.email_body_contains,
                attachment_pattern=automation.attachment_pattern,
                required_category=automation.required_category,
                already_processed=(
                    None
                    if automation.required_category
                    else lambda message_id, name: (
                        self.db.is_production_log_automation_attachment_processed(automation.id, message_id, name)
                        or self.db.is_production_log_attachment_processed(client.id, message_id, name)
                    )
                ),
                # A required category is the durable backlog marker. In that mode,
                # do not discard older messages after vacation or a powered-off PC.
                received_since=None if automation.required_category else window_start,
                received_until=None if automation.required_category else window_end,
                limit=100,
            )
            if not attachments:
                message = (
                    f"No new matching attachments from {window_start:%Y-%m-%d %H:%M} "
                    f"through {window_end:%Y-%m-%d %H:%M}."
                )
                self.db.finish_production_log_automation_run(run_id, status="no_data", message=message)
                return AutomationExecutionResult("no_data", 0, combined, message)
            message_groups: dict[str, list] = {}
            for attachment in attachments:
                message_groups.setdefault(attachment.message_id, []).append(attachment)
            for message_id, message_attachments in message_groups.items():
                message_results: list[tuple[object, ImportResult]] = []
                for attachment in message_attachments:
                    result = updater.import_bytes(attachment.content)
                    message_results.append((attachment, result))
                    processed += 1
                    _merge_results(combined, result)
                if automation.required_category or automation.completed_category:
                    outlook_source.mark_message_updated(
                        message_id,
                        remove_category=automation.required_category,
                        add_category=automation.completed_category,
                    )
                for attachment, result in message_results:
                    self.db.mark_production_log_automation_attachment_processed(
                        automation.id,
                        attachment.message_id,
                        attachment.attachment_name,
                        result.rows_written,
                    )
                    self.db.mark_production_log_attachment_processed(
                        client.id,
                        attachment.message_id,
                        attachment.attachment_name,
                        result.rows_written,
                    )
            report_path = self._write_report(automation, combined, scheduled_for)
            message = (
                f"Processed {processed} attachment(s); wrote {combined.rows_written} row(s) "
                f"and {combined.cells_written} cell(s); skipped {combined.rows_skipped} row(s)."
            )
            if report_path is not None:
                message += f" Report: {report_path}"
            self.db.finish_production_log_automation_run(
                run_id,
                status="success",
                attachments_processed=processed,
                rows_written=combined.rows_written,
                cells_written=combined.cells_written,
                message=message,
            )
            return AutomationExecutionResult("success", processed, combined, message)
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            self.db.finish_production_log_automation_run(
                run_id,
                status="failed",
                attachments_processed=processed,
                rows_written=combined.rows_written,
                cells_written=combined.cells_written,
                message=message,
            )
            raise

    def _write_report(
        self,
        automation: ProductionLogAutomation,
        result: ImportResult,
        scheduled_for: datetime,
    ) -> Optional[Path]:
        if not result.issues:
            return None
        safe_name = "".join(character if character.isalnum() or character in "-_" else "_" for character in automation.name)
        report_dir = self.db.path.parent / "production_log_reports" / safe_name
        report_dir.mkdir(parents=True, exist_ok=True)
        created_at = datetime.now()
        report_path = report_dir / f"{created_at:%Y%m%d_%H%M%S_%f}_exceptions.json"
        payload = {
            "automation": automation.name,
            "scheduled_for": scheduled_for.isoformat(),
            "created_at": created_at.isoformat(),
            "source_rows": result.source_rows,
            "rows_written": result.rows_written,
            "cells_written": result.cells_written,
            "rows_skipped": result.rows_skipped,
            "issues": [
                {
                    "reason": issue.reason,
                    "sheet_name": issue.sheet_name,
                    "source_row_number": issue.source_row_number,
                    "source_row": issue.source_row,
                    "target_row_number": issue.target_row_number,
                    "target_values": issue.target_values,
                    "target_row": issue.target_row,
                }
                for issue in result.issues
            ],
        }
        report_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return report_path


def _same_minute(left: datetime, right: datetime) -> bool:
    return left.replace(second=0, microsecond=0) == right.replace(second=0, microsecond=0)


def _merge_results(target: ImportResult, source: ImportResult) -> None:
    target.source_rows += source.source_rows
    target.rows_written += source.rows_written
    target.cells_written += source.cells_written
    target.rows_skipped += source.rows_skipped
    target.unrouted_rows += source.unrouted_rows
    target.sheets_updated.update(source.sheets_updated)
    target.issues.extend(source.issues)
