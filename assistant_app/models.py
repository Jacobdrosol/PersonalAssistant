from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, date
import math
from typing import Iterable, List, Optional

from . import utils

RepeatType = str


@dataclass(slots=True)
class ProductionCalendar:
    id: int
    name: str
    color: str


@dataclass(slots=True)
class Calendar:
    id: int
    name: str
    color: str
    is_visible: bool
    production_calendar_id: int


@dataclass(slots=True)
class Event:
    id: int
    calendar_id: int
    calendar_name: str
    calendar_color: str
    title: str
    description: str
    start_time: datetime
    duration_minutes: int
    repeat: RepeatType
    repeat_interval: int
    repeat_until: Optional[datetime]
    reminder_minutes_before: Optional[int]
    manual_schedule: bool

    @property
    def end_time(self) -> datetime:
        return self.start_time + timedelta(minutes=self.duration_minutes)

    def occurrences_between(self, window_start: datetime, window_end: datetime) -> List[datetime]:
        """Return occurrence start times inside the window (inclusive)."""
        if window_end < window_start:
            return []
        occurrences: List[datetime] = []
        if self.repeat == "none":
            if window_start <= self.start_time <= window_end:
                occurrences.append(self.start_time)
            return occurrences
        repeat_until = self.repeat_until or window_end
        if repeat_until < window_start:
            return []
        current = self._first_occurrence_at_or_after(window_start)
        if current is None:
            return []
        while current <= window_end and current <= repeat_until:
            occurrences.append(current)
            next_occurrence = self._advance(current)
            if next_occurrence == current:
                break
            current = next_occurrence
        return occurrences

    def reminder_moments_between(self, window_start: datetime, window_end: datetime) -> Iterable[tuple[datetime, datetime]]:
        """Yield pairs of (occurrence_start, reminder_time) within window."""
        minutes_before = self.reminder_minutes_before or 0
        if minutes_before < 0:
            minutes_before = 0
        occurrence_window_start = window_start - timedelta(minutes=minutes_before)
        for occurrence in self.occurrences_between(occurrence_window_start, window_end):
            reminder_time = occurrence - timedelta(minutes=minutes_before)
            if window_start <= reminder_time <= window_end:
                yield occurrence, reminder_time

    def _first_occurrence_at_or_after(self, target: datetime) -> Optional[datetime]:
        if self.start_time >= target:
            return self.start_time
        if self.repeat == "none":
            return None
        if self.repeat == "daily":
            interval_minutes = self.repeat_interval * 24 * 60
            delta_minutes = max(0, (target - self.start_time).total_seconds() / 60)
            steps = math.ceil(delta_minutes / interval_minutes)
            return self.start_time + timedelta(minutes=steps * interval_minutes)
        if self.repeat == "weekly":
            interval_minutes = self.repeat_interval * 7 * 24 * 60
            delta_minutes = max(0, (target - self.start_time).total_seconds() / 60)
            steps = math.ceil(delta_minutes / interval_minutes)
            return self.start_time + timedelta(minutes=steps * interval_minutes)
        if self.repeat == "monthly":
            current = self.start_time
            while current < target:
                current = self._advance(current)
            return current
        if self.repeat == "yearly":
            current = self.start_time
            while current < target:
                current = self._advance(current)
            return current
        return None

    def _advance(self, current: datetime) -> datetime:
        if self.repeat == "daily":
            return current + timedelta(days=self.repeat_interval)
        if self.repeat == "weekly":
            return current + timedelta(weeks=self.repeat_interval)
        if self.repeat == "monthly":
            return utils.add_months(current, self.repeat_interval)
        if self.repeat == "yearly":
            return utils.add_years(current, self.repeat_interval)
        return current


@dataclass(slots=True)
class EventOverride:
    id: int
    event_id: int
    occurrence_date: date
    title: Optional[str]
    description: Optional[str]
    calendar_color: Optional[str]
    note: Optional[str]
    manual_schedule: Optional[bool]


@dataclass(slots=True)
class LogEntry:
    id: int
    parent_id: Optional[int]
    content: str
    position: int
    created_at: datetime


@dataclass(slots=True)
class ScrumTask:
    id: int
    title: str
    description: str
    status: str
    priority: str
    created_at: datetime
    target_date: Optional[date]
    require_time: Optional[str]
    tags: List[str]
    collaborators: List[str]
    order_index: int
    last_alerted_at: Optional[datetime]


@dataclass(slots=True)
class ScrumNote:
    id: int
    task_id: int
    content: str
    created_at: datetime


@dataclass(slots=True)
class SqlInstance:
    id: int
    name: str
    updated_at: Optional[datetime]


@dataclass(slots=True)
class SqlColumn:
    name: str
    description: Optional[str]


@dataclass(slots=True)
class SqlTable:
    id: Optional[int]
    name: str
    description: Optional[str]
    columns: List[SqlColumn]


@dataclass(slots=True)
class SqlDataSourceJoin:
    id: Optional[int]
    source_id: Optional[int]
    alias: Optional[str]
    sequence: Optional[str]
    description: Optional[str]
    join_object: Optional[str]
    join_type: Optional[str]
    row_expected: Optional[bool]
    join_index: Optional[str]
    is_base_join: Optional[bool]
    join_in_error: Optional[bool]
    join_error_message: Optional[str]
    updated_at: Optional[str]
    updated_by: Optional[str]
    comment: Optional[str]
    relate_sequence: Optional[str]
    relate_alias: Optional[str]
    relate_name: Optional[str]
    clause_updated_at: Optional[str]
    clause_updated_by: Optional[str]


@dataclass(slots=True)
class SqlDataSourceExpression:
    id: Optional[int]
    source_id: Optional[int]
    expression_name: Optional[str]
    select_json_id: Optional[str]
    note: Optional[str]
    validated_field_name: Optional[str]
    is_csharp_valid: Optional[bool]
    is_sql_valid: Optional[bool]
    updated_at: Optional[str]
    updated_by: Optional[str]


@dataclass(slots=True)
class SqlDataSource:
    id: Optional[int]
    title: str
    description: Optional[str]
    is_base: bool
    is_in_error: bool
    error_message: Optional[str]
    parent_source: Optional[str]
    select_set: Optional[str]
    updated_at: Optional[str]
    updated_by: Optional[str]
    is_visible: Optional[bool]
    visible_updated_at: Optional[str]
    visible_updated_by: Optional[str]


@dataclass(slots=True)
class SqlDataSourceDetail:
    source: SqlDataSource
    joins: List[SqlDataSourceJoin]
    expressions: List[SqlDataSourceExpression]


@dataclass(slots=True)
class SqlSavedQuery:
    id: Optional[int]
    instance_id: Optional[int]
    name: str
    description: Optional[str]
    content: str
    updated_at: Optional[str]


@dataclass(slots=True)
class JiraProject:
    key: str
    name: str
    project_id: str
    project_type: Optional[str]


@dataclass(slots=True)
class JiraIssue:
    key: str
    summary: str
    status: str
    priority: str
    issue_type: str
    project_key: str
    project_name: str
    updated: Optional[datetime]
    created: Optional[datetime]
    due_date: Optional[date]
    assignee: Optional[str]
    reporter: Optional[str]
    description: Optional[str]
    url: str
    is_assigned: bool
    is_watched: bool


@dataclass(slots=True)
class IssueClient:
    id: int
    name: str


@dataclass(slots=True)
class IssueItem:
    id: int
    client_id: int
    publication_code: str
    issue_name: str
    issue_number: Optional[str]
    trial_date: Optional[date]
    update_date: Optional[date]
    created_at: datetime
    updated_at: Optional[datetime]


@dataclass(slots=True)
class IssueNote:
    id: int
    item_id: int
    content: str
    created_at: datetime
    updated_at: Optional[datetime]


@dataclass(slots=True)
class IssuePublication:
    id: int
    client_id: int
    publication_code: str
    color: str
    is_visible: bool


@dataclass(slots=True)
class ProductionLogClient:
    id: int
    name: str
    workbook_path: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]


@dataclass(slots=True)
class ProductionLogSheetConfig:
    id: int
    client_id: int
    sheet_name: str
    template_key: Optional[str]
    header_row: int
    data_start_row: int
    column_mappings: dict[str, str]


@dataclass(slots=True)
class ExportValidatorInstance:
    id: int
    name: str
    created_at: datetime
    updated_at: Optional[datetime]


@dataclass(slots=True)
class ExportValidatorConfig:
    id: int
    instance_id: int
    item_type: str
    source_filename: Optional[str]
    xml_content: str
    stored_at: datetime
