from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path


@dataclass
class AppSettings:
    desktop_shortcut: bool = True
    start_menu_shortcut: bool = True


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
    )


def save_settings(path: Path, settings: AppSettings) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(asdict(settings), handle, indent=2)
    except Exception:
        # Failing to persist settings should never crash the app.
        return
