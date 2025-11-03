from __future__ import annotations

import json
import sqlite3
from datetime import datetime, date
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import utils
from .models import (
    Calendar,
    Event,
    LogEntry,
    ProductionCalendar,
    ScrumNote,
    ScrumTask,
    SqlInstance,
    SqlColumn,
    SqlTable,
    SqlDataSource,
    SqlDataSourceJoin,
    SqlDataSourceExpression,
    SqlDataSourceDetail,
    SqlSavedQuery,
)

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
            self._ensure_sql_assist_schema()

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

    @staticmethod
    def _int_to_bool(value: Optional[int]) -> Optional[bool]:
        if value is None:
            return None
        return bool(value)

    @staticmethod
    def _bool_to_int(value: Optional[bool]) -> Optional[int]:
        if value is None:
            return None
        return 1 if value else 0

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

    def _ensure_sql_assist_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sql_instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS sql_tables (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER NOT NULL REFERENCES sql_instances(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                description TEXT,
                UNIQUE(instance_id, name)
            );

            CREATE TABLE IF NOT EXISTS sql_columns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_id INTEGER NOT NULL REFERENCES sql_tables(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                description TEXT,
                UNIQUE(table_id, name)
            );

            CREATE TABLE IF NOT EXISTS sql_data_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL UNIQUE,
                description TEXT,
                is_base INTEGER NOT NULL DEFAULT 0,
                is_in_error INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                parent_source TEXT,
                select_set TEXT,
                updated_at TEXT,
                updated_by TEXT,
                is_visible INTEGER,
                visible_updated_at TEXT,
                visible_updated_by TEXT
            );

            CREATE TABLE IF NOT EXISTS sql_data_source_joins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES sql_data_sources(id) ON DELETE CASCADE,
                alias TEXT,
                sequence TEXT,
                description TEXT,
                join_object TEXT,
                join_type TEXT,
                row_expected INTEGER,
                join_index TEXT,
                is_base_join INTEGER,
                join_in_error INTEGER,
                join_error_message TEXT,
                updated_at TEXT,
                updated_by TEXT,
                comment TEXT,
                relate_sequence TEXT,
                relate_alias TEXT,
                relate_name TEXT,
                clause_updated_at TEXT,
                clause_updated_by TEXT
            );

            CREATE TABLE IF NOT EXISTS sql_data_source_expressions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES sql_data_sources(id) ON DELETE CASCADE,
                expression_name TEXT,
                select_json_id TEXT,
                note TEXT,
                validated_field_name TEXT,
                is_csharp_valid INTEGER,
                is_sql_valid INTEGER,
                updated_at TEXT,
                updated_by TEXT
            );

            CREATE TABLE IF NOT EXISTS sql_saved_queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                content TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        self._conn.commit()
        self._ensure_column("sql_instances", "updated_at", "TEXT")
        self._ensure_column("sql_tables", "description", "TEXT")
        self._ensure_column("sql_columns", "description", "TEXT")
        self._ensure_column("sql_data_sources", "description", "TEXT")
        self._ensure_column("sql_data_sources", "is_base", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("sql_data_sources", "is_in_error", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("sql_data_sources", "error_message", "TEXT")
        self._ensure_column("sql_data_sources", "parent_source", "TEXT")
        self._ensure_column("sql_data_sources", "select_set", "TEXT")
        self._ensure_column("sql_data_sources", "updated_at", "TEXT")
        self._ensure_column("sql_data_sources", "updated_by", "TEXT")
        self._ensure_column("sql_data_sources", "is_visible", "INTEGER")
        self._ensure_column("sql_data_sources", "visible_updated_at", "TEXT")
        self._ensure_column("sql_data_sources", "visible_updated_by", "TEXT")
        self._ensure_column("sql_data_source_joins", "sequence", "TEXT")
        self._ensure_column("sql_data_source_joins", "description", "TEXT")
        self._ensure_column("sql_data_source_joins", "join_object", "TEXT")
        self._ensure_column("sql_data_source_joins", "join_type", "TEXT")
        self._ensure_column("sql_data_source_joins", "row_expected", "INTEGER")
        self._ensure_column("sql_data_source_joins", "join_index", "TEXT")
        self._ensure_column("sql_data_source_joins", "is_base_join", "INTEGER")
        self._ensure_column("sql_data_source_joins", "join_in_error", "INTEGER")
        self._ensure_column("sql_data_source_joins", "join_error_message", "TEXT")
        self._ensure_column("sql_data_source_joins", "updated_at", "TEXT")
        self._ensure_column("sql_data_source_joins", "updated_by", "TEXT")
        self._ensure_column("sql_data_source_joins", "comment", "TEXT")
        self._ensure_column("sql_data_source_joins", "relate_sequence", "TEXT")
        self._ensure_column("sql_data_source_joins", "relate_alias", "TEXT")
        self._ensure_column("sql_data_source_joins", "relate_name", "TEXT")
        self._ensure_column("sql_data_source_joins", "clause_updated_at", "TEXT")
        self._ensure_column("sql_data_source_joins", "clause_updated_by", "TEXT")
        self._ensure_column("sql_data_source_expressions", "expression_name", "TEXT")
        self._ensure_column("sql_data_source_expressions", "select_json_id", "TEXT")
        self._ensure_column("sql_data_source_expressions", "note", "TEXT")
        self._ensure_column("sql_data_source_expressions", "validated_field_name", "TEXT")
        self._ensure_column("sql_data_source_expressions", "is_csharp_valid", "INTEGER")
        self._ensure_column("sql_data_source_expressions", "is_sql_valid", "INTEGER")
        self._ensure_column("sql_data_source_expressions", "updated_at", "TEXT")
        self._ensure_column("sql_data_source_expressions", "updated_by", "TEXT")
        self._ensure_column("sql_saved_queries", "description", "TEXT")
        self._ensure_column("sql_saved_queries", "content", "TEXT")
        self._ensure_column("sql_saved_queries", "updated_at", "TEXT")
        with self._lock:
            self._conn.execute(
                """
                UPDATE sql_instances
                SET updated_at = COALESCE(updated_at, created_at, ?)
                WHERE updated_at IS NULL
                """,
                (utils.to_iso(datetime.now()),),
            )
            self._conn.commit()

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

    def clear_log_entries(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM log_entries")
            self._conn.commit()

    # SQL Assist operations -------------------------------------------------
    def get_sql_instances(self) -> List[SqlInstance]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, updated_at, created_at FROM sql_instances ORDER BY name COLLATE NOCASE"
            ).fetchall()
        instances: List[SqlInstance] = []
        for row in rows:
            updated = utils.from_iso(row["updated_at"])
            if updated is None:
                updated = utils.from_iso(row["created_at"])
            instances.append(SqlInstance(id=row["id"], name=row["name"], updated_at=updated))
        return instances

    def create_sql_instance(self, name: str) -> int:
        name = name.strip()
        if not name:
            raise ValueError("Instance name cannot be empty.")
        timestamp = utils.to_iso(datetime.now())
        with self._lock:
            try:
                cursor = self._conn.execute(
                    "INSERT INTO sql_instances (name, created_at, updated_at) VALUES (?, ?, ?)",
                    (name, timestamp, timestamp),
                )
            except sqlite3.IntegrityError as exc:  # pragma: no cover - UI handles messaging
                raise ValueError("An instance with that name already exists.") from exc
            self._conn.commit()
            return cursor.lastrowid

    def delete_sql_instance(self, instance_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sql_instances WHERE id = ?", (instance_id,))
            self._conn.commit()

    def get_sql_tables_with_columns(self, instance_id: int) -> List[SqlTable]:
        with self._lock:
            # Ensure legacy databases have the description columns before querying.
            self._ensure_column("sql_tables", "description", "TEXT")
            self._ensure_column("sql_columns", "description", "TEXT")
            rows = self._conn.execute(
                """
                SELECT t.id AS table_id,
                       t.name AS table_name,
                       t.description AS table_description,
                       c.name AS column_name,
                       c.description AS column_description
                FROM sql_tables AS t
                LEFT JOIN sql_columns AS c ON c.table_id = t.id
                WHERE t.instance_id = ?
                ORDER BY LOWER(t.name), LOWER(c.name)
                """,
                (instance_id,),
            ).fetchall()
        tables: dict[int, SqlTable] = {}
        for row in rows:
            table_id = row["table_id"]
            table = tables.get(table_id)
            if table is None:
                table = SqlTable(
                    id=table_id,
                    name=row["table_name"],
                    description=row["table_description"],
                    columns=[],
                )
                tables[table_id] = table
            column_name = row["column_name"]
            if column_name:
                table.columns.append(
                    SqlColumn(
                        name=column_name,
                        description=row["column_description"],
                    )
                )
        return sorted(tables.values(), key=lambda t: t.name.lower())

    def get_sql_data_sources(self) -> List[SqlDataSource]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, title, description, is_base, is_in_error, error_message,
                       parent_source, select_set, updated_at, updated_by, is_visible,
                       visible_updated_at, visible_updated_by
                FROM sql_data_sources
                ORDER BY LOWER(title)
                """
            ).fetchall()
        sources: List[SqlDataSource] = []
        for row in rows:
            sources.append(
                SqlDataSource(
                    id=row["id"],
                    title=row["title"],
                    description=row["description"],
                    is_base=bool(row["is_base"]),
                    is_in_error=bool(row["is_in_error"]),
                    error_message=row["error_message"],
                    parent_source=row["parent_source"],
                    select_set=row["select_set"],
                    updated_at=row["updated_at"],
                    updated_by=row["updated_by"],
                    is_visible=self._int_to_bool(row["is_visible"]),
                    visible_updated_at=row["visible_updated_at"],
                    visible_updated_by=row["visible_updated_by"],
                )
            )
        return sources

    def get_sql_data_source_details(self, source_id: int) -> SqlDataSourceDetail:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, title, description, is_base, is_in_error, error_message,
                       parent_source, select_set, updated_at, updated_by, is_visible,
                       visible_updated_at, visible_updated_by
                FROM sql_data_sources
                WHERE id = ?
                """,
                (source_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"SQL data source {source_id} not found")
            joins_rows = self._conn.execute(
                """
                SELECT id, alias, sequence, description, join_object, join_type,
                       row_expected, join_index, is_base_join, join_in_error,
                       join_error_message, updated_at, updated_by, comment,
                       relate_sequence, relate_alias, relate_name,
                       clause_updated_at, clause_updated_by
                FROM sql_data_source_joins
                WHERE source_id = ?
                ORDER BY COALESCE(sequence, ''), COALESCE(alias, '')
                """,
                (source_id,),
            ).fetchall()
            expr_rows = self._conn.execute(
                """
                SELECT id, expression_name, select_json_id, note, validated_field_name,
                       is_csharp_valid, is_sql_valid, updated_at, updated_by
                FROM sql_data_source_expressions
                WHERE source_id = ?
                ORDER BY COALESCE(expression_name, ''), id
                """,
                (source_id,),
            ).fetchall()
        source = SqlDataSource(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            is_base=bool(row["is_base"]),
            is_in_error=bool(row["is_in_error"]),
            error_message=row["error_message"],
            parent_source=row["parent_source"],
            select_set=row["select_set"],
            updated_at=row["updated_at"],
            updated_by=row["updated_by"],
            is_visible=self._int_to_bool(row["is_visible"]),
            visible_updated_at=row["visible_updated_at"],
            visible_updated_by=row["visible_updated_by"],
        )
        joins: List[SqlDataSourceJoin] = []
        for jr in joins_rows:
            joins.append(
                SqlDataSourceJoin(
                    id=jr["id"],
                    source_id=source_id,
                    alias=jr["alias"],
                    sequence=jr["sequence"],
                    description=jr["description"],
                    join_object=jr["join_object"],
                    join_type=jr["join_type"],
                    row_expected=self._int_to_bool(jr["row_expected"]),
                    join_index=jr["join_index"],
                    is_base_join=self._int_to_bool(jr["is_base_join"]),
                    join_in_error=self._int_to_bool(jr["join_in_error"]),
                    join_error_message=jr["join_error_message"],
                    updated_at=jr["updated_at"],
                    updated_by=jr["updated_by"],
                    comment=jr["comment"],
                    relate_sequence=jr["relate_sequence"],
                    relate_alias=jr["relate_alias"],
                    relate_name=jr["relate_name"],
                    clause_updated_at=jr["clause_updated_at"],
                    clause_updated_by=jr["clause_updated_by"],
                )
            )
        expressions: List[SqlDataSourceExpression] = []
        for er in expr_rows:
            expressions.append(
                SqlDataSourceExpression(
                    id=er["id"],
                    source_id=source_id,
                    expression_name=er["expression_name"],
                    select_json_id=er["select_json_id"],
                    note=er["note"],
                    validated_field_name=er["validated_field_name"],
                    is_csharp_valid=self._int_to_bool(er["is_csharp_valid"]),
                    is_sql_valid=self._int_to_bool(er["is_sql_valid"]),
                    updated_at=er["updated_at"],
                    updated_by=er["updated_by"],
                )
            )
        return SqlDataSourceDetail(source=source, joins=joins, expressions=expressions)

    def replace_sql_data_sources(self, bundles: List[SqlDataSourceDetail]) -> None:
        with self._lock:
            existing_rows = self._conn.execute(
                "SELECT id, title FROM sql_data_sources"
            ).fetchall()
            existing = {row["title"]: row["id"] for row in existing_rows}
            incoming_titles = {bundle.source.title for bundle in bundles}
            for bundle in bundles:
                src = bundle.source
                existing_id = existing.get(src.title)
                is_visible = self._bool_to_int(src.is_visible)
                data = (
                    src.description,
                    self._bool_to_int(src.is_base),
                    self._bool_to_int(src.is_in_error),
                    src.error_message,
                    src.parent_source,
                    src.select_set,
                    src.updated_at,
                    src.updated_by,
                    is_visible,
                    src.visible_updated_at,
                    src.visible_updated_by,
                )
                if existing_id is not None:
                    self._conn.execute(
                        """
                        UPDATE sql_data_sources
                        SET description = ?,
                            is_base = ?,
                            is_in_error = ?,
                            error_message = ?,
                            parent_source = ?,
                            select_set = ?,
                            updated_at = ?,
                            updated_by = ?,
                            is_visible = ?,
                            visible_updated_at = ?,
                            visible_updated_by = ?
                        WHERE id = ?
                        """,
                        data + (existing_id,),
                    )
                    source_id = existing_id
                    self._conn.execute("DELETE FROM sql_data_source_joins WHERE source_id = ?", (source_id,))
                    self._conn.execute("DELETE FROM sql_data_source_expressions WHERE source_id = ?", (source_id,))
                else:
                    cursor = self._conn.execute(
                        """
                        INSERT INTO sql_data_sources (
                            title, description, is_base, is_in_error, error_message,
                            parent_source, select_set, updated_at, updated_by,
                            is_visible, visible_updated_at, visible_updated_by
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (src.title,) + data,
                    )
                    source_id = cursor.lastrowid
                for join in bundle.joins:
                    self._conn.execute(
                        """
                        INSERT INTO sql_data_source_joins (
                            source_id, alias, sequence, description, join_object, join_type,
                            row_expected, join_index, is_base_join, join_in_error,
                            join_error_message, updated_at, updated_by, comment,
                            relate_sequence, relate_alias, relate_name,
                            clause_updated_at, clause_updated_by
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            source_id,
                            join.alias,
                            join.sequence,
                            join.description,
                            join.join_object,
                            join.join_type,
                            self._bool_to_int(join.row_expected),
                            join.join_index,
                            self._bool_to_int(join.is_base_join),
                            self._bool_to_int(join.join_in_error),
                            join.join_error_message,
                            join.updated_at,
                            join.updated_by,
                            join.comment,
                            join.relate_sequence,
                            join.relate_alias,
                            join.relate_name,
                            join.clause_updated_at,
                            join.clause_updated_by,
                        ),
                    )
                for expr in bundle.expressions:
                    self._conn.execute(
                        """
                        INSERT INTO sql_data_source_expressions (
                            source_id, expression_name, select_json_id, note,
                            validated_field_name, is_csharp_valid, is_sql_valid,
                            updated_at, updated_by
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            source_id,
                            expr.expression_name,
                            expr.select_json_id,
                            expr.note,
                            expr.validated_field_name,
                            self._bool_to_int(expr.is_csharp_valid),
                            self._bool_to_int(expr.is_sql_valid),
                            expr.updated_at,
                            expr.updated_by,
                        ),
                    )
            for title, source_id in existing.items():
                if title not in incoming_titles:
                    self._conn.execute("DELETE FROM sql_data_sources WHERE id = ?", (source_id,))
            self._conn.commit()

    # Saved query operations -------------------------------------------------
    def get_sql_saved_queries(self) -> List[SqlSavedQuery]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, description, content, updated_at FROM sql_saved_queries ORDER BY LOWER(name)"
            ).fetchall()
        return [
            SqlSavedQuery(
                id=row["id"],
                name=row["name"],
                description=row["description"],
                content=row["content"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def get_sql_saved_query(self, query_id: int) -> SqlSavedQuery:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name, description, content, updated_at FROM sql_saved_queries WHERE id = ?",
                (query_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Saved query not found.")
        return SqlSavedQuery(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            content=row["content"],
            updated_at=row["updated_at"],
        )

    def create_sql_saved_query(self, name: str, description: Optional[str], content: str) -> int:
        if not name.strip():
            raise ValueError("Query name cannot be empty.")
        now = utils.to_iso(datetime.now())
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO sql_saved_queries (name, description, content, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (name.strip(), description, content, now),
            )
            self._conn.commit()
            return cursor.lastrowid

    def update_sql_saved_query(
        self,
        query_id: int,
        name: str,
        description: Optional[str],
        content: str,
    ) -> None:
        if not name.strip():
            raise ValueError("Query name cannot be empty.")
        now = utils.to_iso(datetime.now())
        with self._lock:
            self._conn.execute(
                """
                UPDATE sql_saved_queries
                SET name = ?, description = ?, content = ?, updated_at = ?
                WHERE id = ?
                """,
                (name.strip(), description, content, now, query_id),
            )
            self._conn.commit()

    def delete_sql_saved_query(self, query_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sql_saved_queries WHERE id = ?", (query_id,))
            self._conn.commit()
    def export_sql_instance(self, instance_id: int) -> dict[str, object]:
        with self._lock:
            instance = self._conn.execute(
                "SELECT id, name, updated_at FROM sql_instances WHERE id = ?",
                (instance_id,),
            ).fetchone()
            if instance is None:
                raise ValueError("SQL instance not found.")
            rows = self._conn.execute(
                """
                SELECT t.name AS table_name,
                       t.description AS table_description,
                       c.name AS column_name,
                       c.description AS column_description
                FROM sql_tables AS t
                LEFT JOIN sql_columns AS c ON c.table_id = t.id
                WHERE t.instance_id = ?
                ORDER BY LOWER(t.name), LOWER(c.name)
                """,
                (instance_id,),
            ).fetchall()
        tables: dict[str, dict[str, object]] = {}
        for row in rows:
            entry = tables.setdefault(
                row["table_name"],
                {"description": row["table_description"], "columns": []},
            )
            column_name = row["column_name"]
            if column_name:
                columns = entry["columns"]
                if not any(existing["name"] == column_name for existing in columns):
                    columns.append(
                        {
                            "name": column_name,
                            "description": row["column_description"] or "",
                        }
                    )
        return {
            "schema": "sql_assist/v1",
            "name": instance["name"],
            "updated_at": instance["updated_at"],
            "tables": [
                {
                    "name": table_name,
                    "description": entry.get("description"),
                    "columns": entry["columns"],
                }
                for table_name, entry in tables.items()
            ],
        }

    def import_sql_instance(
        self,
        payload: dict[str, object],
        *,
        replace_existing: bool = False,
    ) -> int:
        schema = str(payload.get("schema", ""))
        if schema != "sql_assist/v1":
            raise ValueError("Unsupported SQL assist export format.")
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("Imported instance is missing a name.")
        tables_payload = payload.get("tables")
        if not isinstance(tables_payload, list):
            raise ValueError("Imported instance is missing table definitions.")
        now_iso = utils.to_iso(datetime.now())
        restored_updated_at = payload.get("updated_at")
        restored_dt = None
        if isinstance(restored_updated_at, str) and restored_updated_at:
            try:
                restored_dt = utils.from_iso(restored_updated_at)
            except Exception:
                restored_dt = None
        updated_iso = utils.to_iso(restored_dt) if restored_dt else now_iso

        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM sql_instances WHERE name = ?",
                (name,),
            ).fetchone()
            if existing and replace_existing:
                self._conn.execute(
                    "DELETE FROM sql_instances WHERE id = ?",
                    (existing["id"],),
                )
                existing = None
            if existing and not replace_existing:
                raise ValueError("An instance with that name already exists.")
            cursor = self._conn.execute(
                "INSERT INTO sql_instances (name, created_at, updated_at) VALUES (?, ?, ?)",
                (name, now_iso, updated_iso),
            )
            instance_id = cursor.lastrowid
            for item in tables_payload:
                if not isinstance(item, dict):
                    continue
                table_name = str(item.get("name", "")).strip()
                if not table_name:
                    continue
                table_description = str(item.get("description", "")).strip() or None
                table_cursor = self._conn.execute(
                    "INSERT INTO sql_tables (instance_id, name, description) VALUES (?, ?, ?)",
                    (instance_id, table_name, table_description),
                )
                table_id = table_cursor.lastrowid
                for column in item.get("columns") or []:
                    if isinstance(column, dict):
                        column_name = str(column.get("name", "")).strip()
                        description = str(column.get("description") or "").strip() or None
                    else:
                        column_name = str(column).strip()
                        description = None
                    if not column_name:
                        continue
                    self._conn.execute(
                        "INSERT INTO sql_columns (table_id, name, description) VALUES (?, ?, ?)",
                        (table_id, column_name, description),
                    )
            self._conn.commit()
            return instance_id

    def ingest_sql_table_columns(
        self,
        instance_id: int,
        table_columns: Dict[str, set[str]],
    ) -> tuple[int, int]:
        new_tables = 0
        new_columns = 0
        with self._lock:
            table_rows = self._conn.execute(
                "SELECT id, name FROM sql_tables WHERE instance_id = ?",
                (instance_id,),
            ).fetchall()
            table_ids = {row["name"]: row["id"] for row in table_rows}
            column_map: Dict[int, set[str]] = {}
            if table_ids:
                column_rows = self._conn.execute(
                    """
                    SELECT t.name AS table_name, c.name AS column_name
                    FROM sql_tables AS t
                    JOIN sql_columns AS c ON c.table_id = t.id
                    WHERE t.instance_id = ?
                    """,
                    (instance_id,),
                ).fetchall()
                for row in column_rows:
                    table_id = table_ids.get(row["table_name"])
                    if table_id is None:
                        continue
                    column_map.setdefault(table_id, set()).add(row["column_name"])
            for table_name, columns in table_columns.items():
                normalized_name = table_name.strip()
                if not normalized_name:
                    continue
                table_id = table_ids.get(normalized_name)
                if table_id is None:
                    cursor = self._conn.execute(
                        "INSERT INTO sql_tables (instance_id, name) VALUES (?, ?)",
                        (instance_id, normalized_name),
                    )
                    table_id = cursor.lastrowid
                    table_ids[normalized_name] = table_id
                    column_map[table_id] = set()
                    new_tables += 1
                existing_columns = column_map.setdefault(table_id, set())
                for column_name in columns:
                    normalized_column = column_name.strip()
                    if not normalized_column or normalized_column in existing_columns:
                        continue
                    self._conn.execute(
                        "INSERT INTO sql_columns (table_id, name) VALUES (?, ?)",
                        (table_id, normalized_column),
                    )
                    existing_columns.add(normalized_column)
                    new_columns += 1
            if new_tables or new_columns:
                self._conn.execute(
                    "UPDATE sql_instances SET updated_at = ? WHERE id = ?",
                    (utils.to_iso(datetime.now()), instance_id),
                )
            self._conn.commit()
        return new_tables, new_columns


__all__ = ["Database"]
