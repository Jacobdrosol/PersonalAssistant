from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, time as dt_time
import threading
import time as time_module
from typing import Callable, Dict, Iterable, List

from .database import Database
from .models import Event


@dataclass(frozen=True)
class NotificationPayload:
    title: str
    body: str
    occurs_at: datetime
    kind: str
    metadata: Dict[str, object] | None = None


@dataclass(frozen=True)
class StandingReminder:
    name: str
    title: str
    body: str
    start_time: dt_time
    end_time: dt_time
    interval_minutes: int
    weekdays: tuple[int, ...]


class NotificationManager:
    def __init__(self, db: Database, callback: Callable[[NotificationPayload], None]) -> None:
        self.db = db
        self.callback = callback
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._notified: Dict[str, datetime] = {}
        self._standing_reminders: List[StandingReminder] = [
            StandingReminder(
                name="update-log-hourly",
                title="Update Log",
                body="Quick check-in for your Daily Update Log.",
                start_time=dt_time(hour=8, minute=0),
                end_time=dt_time(hour=15, minute=0),
                interval_minutes=60,
                weekdays=(0, 1, 2, 3, 4),
            ),
            StandingReminder(
                name="update-log-evening",
                title="Update Log",
                body="Wrap up your Daily Update Log before heading out.",
                start_time=dt_time(hour=16, minute=0),
                end_time=dt_time(hour=18, minute=0),
                interval_minutes=30,
                weekdays=(0, 1, 2, 3, 4),
            ),
        ]

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="NotificationManager")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            now = datetime.now()
            try:
                events = self.db.get_events()
                self._process_event_reminders(events, now)
                self._process_scrum_alerts(now)
                self._process_standing_reminders(now)
                self._prune_old(now)
            except Exception:
                time_module.sleep(5)
                continue
            for _ in range(10):
                if self._stop_event.is_set():
                    break
                time_module.sleep(3)

    def _process_event_reminders(self, events: Iterable[Event], now: datetime) -> None:
        window_start = now - timedelta(minutes=2)
        window_end = now + timedelta(minutes=1)
        for event in events:
            for occurrence, reminder_time in event.reminder_moments_between(window_start, window_end):
                key = f"event:{event.id}:{occurrence.isoformat()}"
                if reminder_time <= now and key not in self._notified:
                    body = self._format_event_body(event, occurrence)
                    metadata: Dict[str, object] = {
                        "event_id": event.id,
                        "calendar_id": event.calendar_id,
                        "calendar_color": event.calendar_color,
                    }
                    payload = NotificationPayload(
                        title=event.title,
                        body=body,
                        occurs_at=occurrence,
                        kind="event",
                        metadata=metadata,
                    )
                    self._emit(payload, key, now)

    def _process_standing_reminders(self, now: datetime) -> None:
        window_start = now - timedelta(minutes=2)
        today = now.date()
        for spec in self._standing_reminders:
            if now.weekday() not in spec.weekdays:
                continue
            start_dt = datetime.combine(today, spec.start_time)
            end_dt = datetime.combine(today, spec.end_time)
            if end_dt < start_dt:
                end_dt += timedelta(days=1)
            current = start_dt
            while current <= end_dt:
                key = f"standing:{spec.name}:{current.isoformat()}"
                if window_start <= current <= now and key not in self._notified:
                    payload = NotificationPayload(
                        title=spec.title,
                        body=spec.body,
                        occurs_at=current,
                        kind="standing",
                        metadata={"name": spec.name},
                    )
                    self._emit(payload, key, now)
                current += timedelta(minutes=spec.interval_minutes)

    def _process_scrum_alerts(self, now: datetime) -> None:
        try:
            tasks = self.db.fetch_scrum_tasks_for_alert(now)
        except Exception:
            return
        if not tasks:
            return
        alerted_ids: List[int] = []
        for task, severity in tasks:
            if task.target_date:
                target_str = task.target_date.isoformat()
                if getattr(task, 'require_time', None):
                    target_str = f"{target_str} {task.require_time}"
            else:
                target_str = ''
            key = f"scrum:{task.id}:{severity}:{target_str}"
            if key in self._notified:
                continue
            if severity == 'overdue':
                body = f"Target date {target_str or 'unknown'} has passed."
            else:
                body = f"Due by {target_str or 'unknown'}."
            payload = NotificationPayload(
                title=f"{'Overdue' if severity == 'overdue' else 'Due Soon'} - {task.title}",
                body=body,
                occurs_at=now,
                kind='scrum',
                metadata={
                    'task_id': task.id,
                    'severity': severity,
                    'target_date': target_str or None,
                },
            )
            self._emit(payload, key, now)
            alerted_ids.append(task.id)
        if alerted_ids:
            self.db.mark_scrum_tasks_alerted(alerted_ids, now)

    def _emit(self, payload: NotificationPayload, key: str, timestamp: datetime) -> None:
        self._notified[key] = timestamp
        try:
            self.callback(payload)
        except Exception:
            pass

    def _format_event_body(self, event: Event, occurrence: datetime) -> str:
        components: List[str] = []
        if occurrence.time() == datetime.min.time():
            components.append("All day")
        else:
            components.append(occurrence.strftime("%I:%M %p").lstrip("0"))
        if event.description:
            components.append(event.description)
        return " - ".join(comp for comp in components if comp)

    def _prune_old(self, now: datetime) -> None:
        expired = [key for key, ts in self._notified.items() if ts < now - timedelta(days=2)]
        for key in expired:
            self._notified.pop(key, None)


__all__ = ["NotificationManager", "NotificationPayload"]

