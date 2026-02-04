from __future__ import annotations

from datetime import datetime, time as dt_time, timedelta
import calendar


_USE_24_HOUR_TIME = True


def set_use_24_hour_time(value: bool) -> None:
    global _USE_24_HOUR_TIME
    _USE_24_HOUR_TIME = bool(value)


def use_24_hour_time() -> bool:
    return _USE_24_HOUR_TIME


def time_input_hint(use_24_hour: bool | None = None) -> str:
    return "HH:MM"


def format_time(value: datetime | dt_time, use_24_hour: bool | None = None, *, include_seconds: bool = False) -> str:
    use_24_hour = _USE_24_HOUR_TIME if use_24_hour is None else bool(use_24_hour)
    time_value = value.time() if isinstance(value, datetime) else value
    if use_24_hour:
        fmt = "%H:%M:%S" if include_seconds else "%H:%M"
        return time_value.strftime(fmt)
    fmt = "%I:%M:%S %p" if include_seconds else "%I:%M %p"
    return time_value.strftime(fmt).lstrip("0")


def format_datetime(
    value: datetime,
    use_24_hour: bool | None = None,
    *,
    date_format: str = "%Y-%m-%d",
    include_seconds: bool = False,
) -> str:
    date_part = value.strftime(date_format)
    time_part = format_time(value, use_24_hour, include_seconds=include_seconds)
    return f"{date_part} {time_part}".strip()


def parse_time_string(value: str, use_24_hour: bool | None = None) -> dt_time:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Time value cannot be blank.")
    normalized = cleaned.upper()
    formats_12 = ("%I:%M %p", "%I %p", "%I:%M%p", "%I%p")
    formats_24 = ("%H:%M", "%H")
    if use_24_hour is True:
        formats = formats_24 + formats_12
    elif use_24_hour is False:
        formats = formats_12 + formats_24
    else:
        formats = formats_12 + formats_24
    for fmt in formats:
        try:
            parsed = datetime.strptime(normalized if "%p" in fmt else cleaned, fmt).time()
            return parsed.replace(second=0, microsecond=0)
        except ValueError:
            continue
    raise ValueError(f"Invalid time value '{value}'.")


def format_time_string(value: str, use_24_hour: bool | None = None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    try:
        parsed = parse_time_string(text, use_24_hour)
    except ValueError:
        return text
    return format_time(parsed, use_24_hour)


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
