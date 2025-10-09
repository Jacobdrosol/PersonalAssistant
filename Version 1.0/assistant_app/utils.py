from __future__ import annotations

from datetime import datetime, timedelta
import calendar


def to_iso(dt: datetime) -> str:
    """Return datetime in ISO-8601 string without microseconds."""
    return dt.replace(microsecond=0).isoformat()


def from_iso(value: str | None) -> datetime | None:
    """Parse ISO-8601 datetime string safely."""
    if value is None:
        return None
    return datetime.fromisoformat(value)


def add_months(dt: datetime, months: int) -> datetime:
    """Advance datetime by a number of whole months."""
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(dt.day, last_day)
    return dt.replace(year=year, month=month, day=day)


def add_years(dt: datetime, years: int) -> datetime:
    """Advance datetime by a number of whole years."""
    try:
        return dt.replace(year=dt.year + years)
    except ValueError:
        # Handles February 29th on non-leap years by pinning to Feb 28th
        return dt.replace(month=2, day=28, year=dt.year + years)


def clamp(dt: datetime, start: datetime, end: datetime) -> datetime:
    """Clamp datetime within [start, end]."""
    if dt < start:
        return start
    if dt > end:
        return end
    return dt


def minutes_between(start: datetime, end: datetime) -> int:
    """Return total minutes between two datetimes."""
    return int((end - start).total_seconds() // 60)


def floor_to_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip('#')
    if len(color) == 3:
        color = ''.join(ch * 2 for ch in color)
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))


def ideal_text_color(color: str) -> str:
    r, g, b = hex_to_rgb(color)
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return "#000000" if luminance > 186 else "#FFFFFF"
