from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, time as dt_time, date as dt_date
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


class NotificationManager:
    def __init__(self, db: Database, callback: Callable[[NotificationPayload], None]) -> None:
        self.db = db
        self.callback = callback
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._notified: Dict[str, datetime] = {}
        self._standing_reminders_enabled = True
        self._work_start = dt_time(hour=8, minute=0)
        self._work_end = dt_time(hour=17, minute=0)
        self._weekday_targets: tuple[int, ...] = (0, 1, 2, 3, 4)
        self._hourly_body = 'Hourly reminder to update your "Daily Update Log".'
        self._send_body = 'Reminder to send your "Daily Update Log".'

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

    def set_standing_reminders_enabled(self, enabled: bool) -> None:
        self._standing_reminders_enabled = bool(enabled)

    def configure_daily_log_hours(self, start_time: dt_time, end_time: dt_time) -> None:
        self._work_start = start_time.replace(second=0, microsecond=0)
        self._work_end = end_time.replace(second=0, microsecond=0)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            now = datetime.now()
            try:
                events = self.db.get_events()
                self._process_event_reminders(events, now)
                self._process_scrum_alerts(now)
                if self._standing_reminders_enabled:
                    self._process_daily_log_reminders(now)
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

    def _process_daily_log_reminders(self, now: datetime) -> None:
        if now.weekday() not in self._weekday_targets:
            return
        window_start = now - timedelta(minutes=2)
        for reminder_time, phase in self._build_daily_schedule(now.date()):
            key = f"standing:daily-log-{phase}:{reminder_time.isoformat()}"
            if window_start <= reminder_time <= now and key not in self._notified:
                payload = NotificationPayload(
                    title="Daily Update Log",
                    body=self._send_body if phase == "send" else self._hourly_body,
                    occurs_at=reminder_time,
                    kind="standing",
                    metadata={"name": f"daily-log-{phase}"},
                )
                self._emit(payload, key, now)

    def _build_daily_schedule(self, today: dt_date) -> List[tuple[datetime, str]]:
        start_dt = datetime.combine(today, self._work_start)
        end_dt = datetime.combine(today, self._work_end)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
        slots: List[tuple[datetime, str]] = []
        first_hour = start_dt.replace(minute=0, second=0, microsecond=0)
        if first_hour <= start_dt:
            first_hour += timedelta(hours=1)
        last_hour = end_dt - timedelta(hours=1)
        current = first_hour
        while current <= last_hour:
            slots.append((current, "hourly"))
            current += timedelta(hours=1)
        send_time = end_dt
        send_warning = end_dt - timedelta(minutes=30)
        if send_warning >= start_dt:
            slots.append((send_warning, "send"))
        slots.append((send_time, "send"))
        return slots

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

