from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path


@dataclass
class AppSettings:
    desktop_shortcut: bool = True
    start_menu_shortcut: bool = True
    daily_update_notifications: bool = True
    daily_update_start: str = "08:00"
    daily_update_end: str = "17:00"
    theme: str = "dark"


def _coerce_time_string(value: str | None, fallback: str) -> str:
    if not value or not isinstance(value, str):
        return fallback
    parts = value.strip().split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return fallback
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return fallback
    return f"{hour:02d}:{minute:02d}"


def load_settings(path: Path) -> AppSettings:
    if not path.exists():
        return AppSettings()
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return AppSettings()
    if not isinstance(payload, dict):
        return AppSettings()
    return AppSettings(
        desktop_shortcut=bool(payload.get("desktop_shortcut", True)),
        start_menu_shortcut=bool(payload.get("start_menu_shortcut", True)),
        daily_update_notifications=bool(payload.get("daily_update_notifications", True)),
        daily_update_start=_coerce_time_string(payload.get("daily_update_start"), "08:00"),
        daily_update_end=_coerce_time_string(payload.get("daily_update_end"), "17:00"),
        theme=str(payload.get("theme", "dark")).lower() if payload.get("theme") else "dark",
    )


def save_settings(path: Path, settings: AppSettings) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(asdict(settings), handle, indent=2)
    except Exception:
        # Failing to persist settings should never crash the app.
        return
