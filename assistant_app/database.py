from __future__ import annotations

import json
import sqlite3
from datetime import datetime, date
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import utils
from .models import Calendar, Event, LogEntry, ProductionCalendar, ScrumNote, ScrumTask

MISSING = object()
SCRUM_STATUSES: Tuple[str, ...] = ("todo", "doing", "review", "done")
SCRUM_PRIORITIES: Tuple[str, ...] = ("Critical", "Major", "Medium", "Minor", "Unknown")


class Database:
    def __init__(self, db_path: Path | str):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = RLock()
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS production_calendars (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    color TEXT NOT NULL DEFAULT '#4F75FF'
                );

                CREATE TABLE IF NOT EXISTS calendars (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    production_calendar_id INTEGER REFERENCES production_calendars(id),
                    name TEXT NOT NULL UNIQUE,
                    color TEXT NOT NULL,
                    is_visible INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    calendar_id INTEGER NOT NULL REFERENCES calendars(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    description TEXT,
                    start_time TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL DEFAULT 60,
                    repeat TEXT NOT NULL DEFAULT 'none',
                    repeat_interval INTEGER NOT NULL DEFAULT 1,
                    repeat_until TEXT,
                    reminder_minutes_before INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS log_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_id INTEGER REFERENCES log_entries(id) ON DELETE CASCADE,
                    content TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._ensure_column("calendars", "production_calendar_id", "INTEGER")
            self._conn.commit()
            default_pc_id = self._ensure_default_production_calendar()
            self._ensure_default_calendar(default_pc_id)
            self._ensure_scrum_schema()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        existing = {
            row["name"]
            for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _ensure_default_production_calendar(self) -> int:
        cursor = self._conn.execute(
            "SELECT id, color FROM production_calendars ORDER BY id LIMIT 1"
        )
        row = cursor.fetchone()
        if row is None:
            cursor = self._conn.execute(
                "INSERT INTO production_calendars (name, color) VALUES (?, ?)",
                ("The New Republic - RU-ACC", "#4F75FF"),
            )
            self._conn.commit()
            prod_id = cursor.lastrowid
        else:
            prod_id = row["id"]
            if row["color"] is None:
                self._conn.execute(
                    "UPDATE production_calendars SET color = '#4F75FF' WHERE id = ?",
                    (prod_id,),
                )
                self._conn.commit()
        self._conn.execute(
            "UPDATE calendars SET production_calendar_id = ? WHERE production_calendar_id IS NULL",
            (prod_id,),
        )
        self._conn.commit()
        return prod_id

    def _ensure_default_calendar(self, production_calendar_id: int) -> None:
        cursor = self._conn.execute("SELECT COUNT(*) AS total FROM calendars")
        count = cursor.fetchone()[0]
        if count == 0:
            self._conn.execute(
                "INSERT INTO calendars (name, color, is_visible, production_calendar_id) VALUES (?, ?, 1, ?)",
                ("Personal", "#4F75FF", production_calendar_id),
            )
            self._conn.commit()

    def _ensure_scrum_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scrum_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'todo',
                priority TEXT NOT NULL DEFAULT 'Unknown',
                created_at TEXT NOT NULL,
                target_date TEXT,
                require_time TEXT,
                tags TEXT,
                collaborators TEXT,
                order_index INTEGER NOT NULL DEFAULT 0,
                last_alerted_at TEXT
            );

            CREATE TABLE IF NOT EXISTS scrum_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL REFERENCES scrum_tasks(id) ON DELETE CASCADE,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                position INTEGER NOT NULL
            );
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_scrum_tasks_status_order ON scrum_tasks(status, order_index, id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_scrum_notes_task_position ON scrum_notes(task_id, position)"
        )
        self._ensure_column("scrum_tasks", "priority", "TEXT NOT NULL DEFAULT 'Unknown'")
        self._ensure_column("scrum_tasks", "require_time", "TEXT")
        self._conn.commit()

    def _normalize_scrum_status(self, status: str) -> str:
        normalized = status.strip().lower()
        if normalized not in SCRUM_STATUSES:
            raise ValueError(f"Unknown scrum status '{status}'")
        return normalized

    def _normalize_priority(self, priority: str) -> str:
        cleaned = priority.strip().capitalize()
        for option in SCRUM_PRIORITIES:
            if option.lower() == cleaned.lower():
                return option
        raise ValueError(f"Unknown priority '{priority}'")

    def _serialize_list(self, values: Iterable[str] | None) -> str:
        payload = [item.strip() for item in (values or []) if item and item.strip()]
        return json.dumps(payload, ensure_ascii=False)

    def _deserialize_list(self, raw: Any) -> List[str]:
        if not raw:
            return []
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(item) for item in data if isinstance(item, str)]
        except Exception:
            pass
        return []

    def _row_to_scrum_task(self, row: sqlite3.Row) -> ScrumTask:
        target_date_value = row["target_date"]
        target_date_parsed = date.fromisoformat(target_date_value) if target_date_value else None
        last_alerted_raw = row["last_alerted_at"]
        last_alerted = utils.from_iso(last_alerted_raw) if last_alerted_raw else None
        return ScrumTask(
            id=row["id"],
            title=row["title"],
            description=row["description"] or "",
            status=row["status"],
            priority=row["priority"] or "Unknown",
            created_at=datetime.fromisoformat(row["created_at"]),
            target_date=target_date_parsed,
            require_time=row["require_time"],
            tags=self._deserialize_list(row["tags"]),
            collaborators=self._deserialize_list(row["collaborators"]),
            order_index=row["order_index"],
            last_alerted_at=last_alerted,
        )

    def _next_scrum_order(self, status: str) -> int:
        cursor = self._conn.execute(
            "SELECT COALESCE(MAX(order_index), -1) + 1 AS next_index FROM scrum_tasks WHERE status = ?",
            (status,),
        )
        value = cursor.fetchone()["next_index"]
        return int(value)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # Calendar operations -------------------------------------------------
    def get_production_calendars(self) -> List[ProductionCalendar]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, color FROM production_calendars ORDER BY name"
            ).fetchall()
        return [
            ProductionCalendar(id=row["id"], name=row["name"], color=row["color"])
            for row in rows
        ]

    def create_production_calendar(self, name: str, color: str) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO production_calendars (name, color) VALUES (?, ?)",
                (name.strip(), color),
            )
            self._conn.commit()
            return cursor.lastrowid

    def update_production_calendar(
        self,
        production_calendar_id: int,
        *,
        name: Optional[str] = MISSING,
        color: Optional[str] = MISSING,
    ) -> None:
        fields: List[str] = []
        values: List[object] = []
        if name is not MISSING:
            fields.append("name = ?")
            values.append(name.strip())
        if color is not MISSING:
            fields.append("color = ?")
            values.append(color)
        if not fields:
            return
        values.append(production_calendar_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE production_calendars SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            self._conn.commit()

    def delete_production_calendar(self, production_calendar_id: int) -> None:
        with self._lock:
            count_row = self._conn.execute(
                "SELECT COUNT(*) AS total FROM calendars WHERE production_calendar_id = ?",
                (production_calendar_id,),
            ).fetchone()
            if count_row and count_row["total"]:
                raise ValueError("Remove or reassign calendars before deleting this production calendar.")
            self._conn.execute(
                "DELETE FROM production_calendars WHERE id = ?",
                (production_calendar_id,),
            )
            self._conn.commit()

    def _delete_production_calendar_force(self, production_calendar_id: int) -> None:
        self._conn.execute(
            "DELETE FROM calendars WHERE production_calendar_id = ?",
            (production_calendar_id,),
        )
        self._conn.execute(
            "DELETE FROM production_calendars WHERE id = ?",
            (production_calendar_id,),
        )

    def export_production_calendar(self, production_calendar_id: int) -> dict[str, object]:
        with self._lock:
            prod = self._conn.execute(
                "SELECT id, name, color FROM production_calendars WHERE id = ?",
                (production_calendar_id,),
            ).fetchone()
            if prod is None:
                raise ValueError("Production calendar not found.")
            calendars = self._conn.execute(
                "SELECT id, name, color, is_visible FROM calendars WHERE production_calendar_id = ? ORDER BY name",
                (production_calendar_id,),
            ).fetchall()
            payload_calendars: List[dict[str, object]] = []
            for cal_row in calendars:
                events = self._conn.execute(
                    """
                    SELECT title, description, start_time, duration_minutes,
                           repeat, repeat_interval, repeat_until, reminder_minutes_before
                    FROM events
                    WHERE calendar_id = ?
                    ORDER BY start_time
                    """,
                    (cal_row["id"],),
                ).fetchall()
                payload_calendars.append(
                    {
                        "name": cal_row["name"],
                        "color": cal_row["color"],
                        "is_visible": bool(cal_row["is_visible"]),
                        "events": [
                            {
                                "title": event_row["title"],
                                "description": event_row["description"] or "",
                                "start_time": event_row["start_time"],
                                "duration_minutes": event_row["duration_minutes"],
                                "repeat": event_row["repeat"],
                                "repeat_interval": event_row["repeat_interval"],
                                "repeat_until": event_row["repeat_until"],
                                "reminder_minutes_before": event_row["reminder_minutes_before"],
                            }
                            for event_row in events
                        ],
                    }
                )
            payload: dict[str, object] = {
                "schema": "production_calendar/v1",
                "exported_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                "name": prod["name"],
                "color": prod["color"],
                "calendars": payload_calendars,
            }
            return payload

    def import_production_calendar(self, payload: dict[str, object]) -> int:
        schema = str(payload.get("schema", "production_calendar/v1"))
        if schema != "production_calendar/v1":
            raise ValueError("Unsupported production calendar export format.")
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("Imported production calendar is missing a name.")
        color = str(payload.get("color") or "#4F75FF")
        calendars_payload = payload.get("calendars") or []
        if not isinstance(calendars_payload, list):
            raise ValueError("Invalid calendar dataset.")
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM production_calendars WHERE name = ?",
                (name,),
            ).fetchone()
            if existing is not None:
                self._delete_production_calendar_force(existing["id"])
            cursor = self._conn.execute(
                "INSERT INTO production_calendars (name, color) VALUES (?, ?)",
                (name, color),
            )
            production_calendar_id = cursor.lastrowid
            for calendar_item in calendars_payload:
                cal_name = str(calendar_item.get("name", "")).strip()
                if not cal_name:
                    raise ValueError("Calendar entry missing a name.")
                cal_color = str(calendar_item.get("color") or "#4F75FF")
                is_visible = bool(calendar_item.get("is_visible", True))
                cursor = self._conn.execute(
                    "INSERT INTO calendars (name, color, is_visible, production_calendar_id) VALUES (?, ?, ?, ?)",
                    (cal_name, cal_color, 1 if is_visible else 0, production_calendar_id),
                )
                new_calendar_id = cursor.lastrowid
                for event_item in calendar_item.get("events", []):
                    title = str(event_item.get("title", "")).strip()
                    if not title:
                        raise ValueError(f"Event missing a title in calendar '{cal_name}'.")
                    description = str(event_item.get("description", "")).strip()
                    start_time_raw = event_item.get("start_time")
                    if not start_time_raw:
                        raise ValueError(f"Event '{title}' is missing start_time.")
                    try:
                        start_time = datetime.fromisoformat(str(start_time_raw))
                    except ValueError as exc:
                        raise ValueError(f"Invalid start_time for event '{title}': {exc}") from exc
                    duration_minutes = int(event_item.get("duration_minutes", 60))
                    repeat_value = str(event_item.get("repeat", "none"))
                    repeat_interval = max(1, int(event_item.get("repeat_interval", 1)))
                    repeat_until_raw = event_item.get("repeat_until")
                    repeat_until_dt: Optional[datetime]
                    if repeat_until_raw:
                        try:
                            repeat_until_dt = datetime.fromisoformat(str(repeat_until_raw))
                        except ValueError as exc:
                            raise ValueError(f"Invalid repeat_until for event '{title}': {exc}") from exc
                    else:
                        repeat_until_dt = None
                    reminder_minutes_before = event_item.get("reminder_minutes_before")
                    reminder_value: Optional[int]
                    if reminder_minutes_before is None:
                        reminder_value = None
                    else:
                        reminder_value = int(reminder_minutes_before)
                    self._conn.execute(
                        """
                        INSERT INTO events (
                            calendar_id, title, description, start_time, duration_minutes,
                            repeat, repeat_interval, repeat_until, reminder_minutes_before
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_calendar_id,
                            title,
                            description,
                            utils.to_iso(start_time),
                            duration_minutes,
                            repeat_value,
                            repeat_interval,
                            utils.to_iso(repeat_until_dt) if repeat_until_dt else None,
                            reminder_value,
                        ),
                    )
            self._conn.commit()
            return production_calendar_id

    def get_calendars(self, production_calendar_id: Optional[int] = None) -> List[Calendar]:
        query = "SELECT id, name, color, is_visible, production_calendar_id FROM calendars"
        params: List[object] = []
        if production_calendar_id is not None:
            query += " WHERE production_calendar_id = ?"
            params.append(production_calendar_id)
        query += " ORDER BY name"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [
            Calendar(
                id=row["id"],
                name=row["name"],
                color=row["color"],
                is_visible=bool(row["is_visible"]),
                production_calendar_id=row["production_calendar_id"],
            )
            for row in rows
        ]

    def create_calendar(self, name: str, color: str, *, production_calendar_id: int, is_visible: bool = True) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO calendars (name, color, is_visible, production_calendar_id) VALUES (?, ?, ?, ?)",
                (name.strip(), color, 1 if is_visible else 0, production_calendar_id),
            )
            self._conn.commit()
            return cursor.lastrowid

    def update_calendar(
        self,
        calendar_id: int,
        *,
        name: Optional[str] = MISSING,
        color: Optional[str] = MISSING,
        is_visible: Optional[bool] = MISSING,
        production_calendar_id: Optional[int] = MISSING,
    ) -> None:
        fields: List[str] = []
        values: List[object] = []
        if name is not MISSING:
            fields.append("name = ?")
            values.append(name.strip())
        if color is not MISSING:
            fields.append("color = ?")
            values.append(color)
        if is_visible is not MISSING:
            fields.append("is_visible = ?")
            values.append(1 if is_visible else 0)
        if production_calendar_id is not MISSING:
            fields.append("production_calendar_id = ?")
            values.append(production_calendar_id)
        if not fields:
            return
        values.append(calendar_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE calendars SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            self._conn.commit()

    def delete_calendar(self, calendar_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM calendars WHERE id = ?", (calendar_id,))
            self._conn.commit()

    # Event operations ----------------------------------------------------
    def get_events(self, calendar_ids: Optional[Iterable[int]] = None) -> List[Event]:
        query = (
            "SELECT e.id, e.calendar_id, c.name AS calendar_name, c.color AS calendar_color, "
            "e.title, e.description, e.start_time, e.duration_minutes, e.repeat, e.repeat_interval, "
            "e.repeat_until, e.reminder_minutes_before "
            "FROM events e JOIN calendars c ON e.calendar_id = c.id"
        )
        params: List[object] = []
        if calendar_ids:
            ids = list(calendar_ids)
            placeholders = ",".join("?" for _ in ids)
            query += f" WHERE e.calendar_id IN ({placeholders})"
            params.extend(ids)
        query += " ORDER BY e.start_time"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        events: List[Event] = []
        for row in rows:
            events.append(
                Event(
                    id=row["id"],
                    calendar_id=row["calendar_id"],
                    calendar_name=row["calendar_name"],
                    calendar_color=row["calendar_color"],
                    title=row["title"],
                    description=row["description"] or "",
                    start_time=datetime.fromisoformat(row["start_time"]),
                    duration_minutes=row["duration_minutes"],
                    repeat=row["repeat"],
                    repeat_interval=row["repeat_interval"],
                    repeat_until=utils.from_iso(row["repeat_until"]),
                    reminder_minutes_before=row["reminder_minutes_before"],
                )
            )
        return events

    def get_event(self, event_id: int) -> Optional[Event]:
        with self._lock:
            row = self._conn.execute(
                "SELECT e.id, e.calendar_id, c.name AS calendar_name, c.color AS calendar_color, "
                "e.title, e.description, e.start_time, e.duration_minutes, e.repeat, e.repeat_interval, "
                "e.repeat_until, e.reminder_minutes_before "
                "FROM events e JOIN calendars c ON e.calendar_id = c.id WHERE e.id = ?",
                (event_id,),
            ).fetchone()
        if row is None:
            return None
        return Event(
            id=row["id"],
            calendar_id=row["calendar_id"],
            calendar_name=row["calendar_name"],
            calendar_color=row["calendar_color"],
            title=row["title"],
            description=row["description"] or "",
            start_time=datetime.fromisoformat(row["start_time"]),
            duration_minutes=row["duration_minutes"],
            repeat=row["repeat"],
            repeat_interval=row["repeat_interval"],
            repeat_until=utils.from_iso(row["repeat_until"]),
            reminder_minutes_before=row["reminder_minutes_before"],
        )

    def create_event(
        self,
        *,
        calendar_id: int,
        title: str,
        description: str,
        start_time: datetime,
        duration_minutes: int,
        repeat: str,
        repeat_interval: int,
        repeat_until: Optional[datetime],
        reminder_minutes_before: Optional[int],
    ) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO events (
                    calendar_id, title, description, start_time, duration_minutes,
                    repeat, repeat_interval, repeat_until, reminder_minutes_before
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    calendar_id,
                    title.strip(),
                    description.strip(),
                    utils.to_iso(start_time),
                    duration_minutes,
                    repeat,
                    max(1, repeat_interval),
                    utils.to_iso(repeat_until) if repeat_until else None,
                    reminder_minutes_before,
                ),
            )
            self._conn.commit()
            return cursor.lastrowid

    def update_event(
        self,
        event_id: int,
        *,
        calendar_id: Optional[int] = MISSING,
        title: Optional[str] = MISSING,
        description: Optional[str] = MISSING,
        start_time: Optional[datetime] = MISSING,
        duration_minutes: Optional[int] = MISSING,
        repeat: Optional[str] = MISSING,
        repeat_interval: Optional[int] = MISSING,
        repeat_until: Optional[datetime | None] = MISSING,
        reminder_minutes_before: Optional[int | None] = MISSING,
    ) -> None:
        fields: List[str] = []
        values: List[object] = []
        if calendar_id is not MISSING:
            fields.append("calendar_id = ?")
            values.append(calendar_id)
        if title is not MISSING:
            fields.append("title = ?")
            values.append(title.strip())
        if description is not MISSING:
            fields.append("description = ?")
            values.append(description.strip())
        if start_time is not MISSING:
            fields.append("start_time = ?")
            values.append(utils.to_iso(start_time))
        if duration_minutes is not MISSING:
            fields.append("duration_minutes = ?")
            values.append(duration_minutes)
        if repeat is not MISSING:
            fields.append("repeat = ?")
            values.append(repeat)
        if repeat_interval is not MISSING:
            fields.append("repeat_interval = ?")
            values.append(max(1, repeat_interval))
        if repeat_until is not MISSING:
            fields.append("repeat_until = ?")
            values.append(utils.to_iso(repeat_until) if repeat_until else None)
        if reminder_minutes_before is not MISSING:
            fields.append("reminder_minutes_before = ?")
            values.append(reminder_minutes_before)
        if not fields:
            return
        values.append(event_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE events SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            self._conn.commit()

    def delete_event(self, event_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
            self._conn.commit()

    # Scrum operations ----------------------------------------------------
    def get_scrum_tasks(self) -> List[ScrumTask]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, description, status, priority, created_at, target_date, require_time, tags, collaborators, "
                "order_index, last_alerted_at "
                "FROM scrum_tasks "
                "ORDER BY CASE status "
                "WHEN 'todo' THEN 0 WHEN 'doing' THEN 1 WHEN 'review' THEN 2 WHEN 'done' THEN 3 ELSE 4 END, "
                "order_index, id"
            ).fetchall()
        return [self._row_to_scrum_task(row) for row in rows]

    def get_scrum_task(self, task_id: int) -> Optional[ScrumTask]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, title, description, status, priority, created_at, target_date, require_time, tags, collaborators, "
                "order_index, last_alerted_at "
                "FROM scrum_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        return self._row_to_scrum_task(row) if row else None

    def create_scrum_task(
        self,
        *,
        title: str,
        description: str,
        status: str = "todo",
        priority: str = "Unknown",
        target_date: Optional[date] = None,
        require_time: Optional[str] = None,
        tags: Optional[Iterable[str]] = None,
        collaborators: Optional[Iterable[str]] = None,
    ) -> int:
        normalized_status = self._normalize_scrum_status(status)
        normalized_priority = self._normalize_priority(priority)
        created_at = datetime.now()
        target_date_text = target_date.isoformat() if target_date else None
        tags_text = self._serialize_list(tags)
        collaborators_text = self._serialize_list(collaborators)
        with self._lock:
            order_index = self._next_scrum_order(normalized_status)
            cursor = self._conn.execute(
                """
                INSERT INTO scrum_tasks (
                    title, description, status, priority, created_at, target_date, require_time,
                    tags, collaborators, order_index, last_alerted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    title.strip(),
                    description.strip(),
                    normalized_status,
                    normalized_priority,
                    created_at.isoformat(timespec="seconds"),
                    target_date_text,
                    require_time.strip() if require_time else None,
                    tags_text,
                    collaborators_text,
                    order_index,
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def update_scrum_task(
        self,
        task_id: int,
        *,
        title: Optional[str] = MISSING,
        description: Optional[str] = MISSING,
        status: Optional[str] = MISSING,
        priority: Optional[str] = MISSING,
        target_date: Optional[date | None] = MISSING,
        require_time: Optional[str | None] = MISSING,
        tags: Optional[Iterable[str]] = MISSING,
        collaborators: Optional[Iterable[str]] = MISSING,
        order_index: Optional[int] = MISSING,
        last_alerted_at: Optional[datetime | None] = MISSING,
    ) -> None:
        fields: List[str] = []
        values: List[object] = []
        new_status: Optional[str] = None
        if title is not MISSING:
            fields.append("title = ?")
            values.append(title.strip())
        if description is not MISSING:
            fields.append("description = ?")
            values.append(description.strip())
        if status is not MISSING:
            new_status = self._normalize_scrum_status(status)
            fields.append("status = ?")
            values.append(new_status)
        if priority is not MISSING:
            normalized_priority = self._normalize_priority(priority)
            fields.append("priority = ?")
            values.append(normalized_priority)
        if target_date is not MISSING:
            fields.append("target_date = ?")
            values.append(target_date.isoformat() if target_date else None)
        if require_time is not MISSING:
            cleaned_time = require_time.strip() if isinstance(require_time, str) and require_time.strip() else None
            fields.append("require_time = ?")
            values.append(cleaned_time)
        if tags is not MISSING:
            fields.append("tags = ?")
            values.append(self._serialize_list(tags))
        if collaborators is not MISSING:
            fields.append("collaborators = ?")
            values.append(self._serialize_list(collaborators))
        if order_index is not MISSING:
            fields.append("order_index = ?")
            values.append(order_index)
        elif new_status is not None:
            # When moving to a new column without explicit order, append to bottom.
            with self._lock:
                next_index = self._next_scrum_order(new_status)
            fields.append("order_index = ?")
            values.append(next_index)
        if last_alerted_at is not MISSING:
            fields.append("last_alerted_at = ?")
            values.append(utils.to_iso(last_alerted_at) if last_alerted_at else None)
        if not fields:
            return
        values.append(task_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE scrum_tasks SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            self._conn.commit()

    def reorder_scrum_tasks(self, updates: Iterable[Tuple[int, int]]) -> None:
        with self._lock:
            self._conn.executemany(
                "UPDATE scrum_tasks SET order_index = ? WHERE id = ?",
                [(order_index, task_id) for task_id, order_index in updates],
            )
            self._conn.commit()

    def delete_scrum_task(self, task_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM scrum_tasks WHERE id = ?", (task_id,))
            self._conn.commit()

    def get_scrum_notes(self, task_id: int) -> List[ScrumNote]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, task_id, content, created_at
                FROM scrum_notes
                WHERE task_id = ?
                ORDER BY position
                """,
                (task_id,),
            ).fetchall()
        return [
            ScrumNote(
                id=row["id"],
                task_id=row["task_id"],
                content=row["content"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def create_scrum_note(self, task_id: int, content: str) -> int:
        created_at = datetime.now()
        with self._lock:
            cursor = self._conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM scrum_notes WHERE task_id = ?",
                (task_id,),
            )
            position = cursor.fetchone()[0]
            cursor = self._conn.execute(
                "INSERT INTO scrum_notes (task_id, content, created_at, position) VALUES (?, ?, ?, ?)",
                (
                    task_id,
                    content.strip(),
                    created_at.isoformat(timespec="seconds"),
                    position,
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def update_scrum_note(self, note_id: int, content: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE scrum_notes SET content = ? WHERE id = ?",
                (content.strip(), note_id),
            )
            self._conn.commit()

    def delete_scrum_note(self, note_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM scrum_notes WHERE id = ?", (note_id,))
            self._conn.commit()

    def fetch_scrum_tasks_for_alert(self, now: datetime) -> List[Tuple[ScrumTask, str]]:
        today = now.date()
        day_start = datetime.combine(today, datetime.min.time())
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, description, status, priority, created_at, target_date, require_time, tags, collaborators, "
                "order_index, last_alerted_at "
                "FROM scrum_tasks WHERE target_date IS NOT NULL AND status != 'done'"
            ).fetchall()
        result: List[Tuple[ScrumTask, str]] = []
        for row in rows:
            task = self._row_to_scrum_task(row)
            if task.target_date is None:
                continue
            delta = (task.target_date - today).days
            severity: Optional[str]
            if delta < 0:
                severity = "overdue"
            elif delta <= 1:
                severity = "due_soon"
            else:
                severity = None
            if severity is None:
                continue
            if task.last_alerted_at and task.last_alerted_at >= day_start:
                continue
            result.append((task, severity))
        return result

    def mark_scrum_tasks_alerted(self, task_ids: Iterable[int], timestamp: datetime) -> None:
        ids = [int(task_id) for task_id in task_ids]
        if not ids:
            return
        with self._lock:
            self._conn.executemany(
                "UPDATE scrum_tasks SET last_alerted_at = ? WHERE id = ?",
                [(utils.to_iso(timestamp), task_id) for task_id in ids],
            )
            self._conn.commit()

    # Log operations ------------------------------------------------------
    def get_log_entries(self) -> List[LogEntry]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, parent_id, content, position, created_at FROM log_entries ORDER BY position"
            ).fetchall()
        return [
            LogEntry(
                id=row["id"],
                parent_id=row["parent_id"],
                content=row["content"],
                position=row["position"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def create_log_entry(self, content: str, parent_id: Optional[int]) -> int:
        with self._lock:
            cursor = self._conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM log_entries")
            position = cursor.fetchone()[0]
            cursor = self._conn.execute(
                "INSERT INTO log_entries (parent_id, content, position, created_at) VALUES (?, ?, ?, ?)",
                (
                    parent_id,
                    content.strip(),
                    position,
                    utils.to_iso(datetime.now()),
                ),
            )
            self._conn.commit()
            return cursor.lastrowid

    def update_log_entry(self, entry_id: int, content: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE log_entries SET content = ? WHERE id = ?",
                (content.strip(), entry_id),
            )
            self._conn.commit()

    def delete_log_entry(self, entry_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM log_entries WHERE id = ?", (entry_id,))
            self._conn.commit()


__all__ = ["Database"]
