from __future__ import annotations

import calendar
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from math import ceil
from pathlib import Path
from typing import Any, Iterable, Literal


WEEKDAY_NAMES = ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")
CalendarPdfMode = Literal["adaptive", "large_format"]


@dataclass(frozen=True, slots=True)
class CalendarPdfEntry:
    day: date
    text: str
    color: str
    overview_text: str | None = None


@dataclass(frozen=True, slots=True)
class _CalendarPdfLayout:
    page_width: float
    page_height: float
    margin: float
    weekday_height: float
    grid_top: float
    grid_bottom: float
    cell_width: float
    border: Any
    muted: Any
    outside_fill: Any
    black: Any
    white: Any
    hex_color: Any
    string_width: Any


def downloads_pdf_path(calendar_name: str, month: date) -> Path:
    downloads = Path.home() / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", calendar_name.strip()).strip("._")
    safe_name = (safe_name or "Calendar")[:120]
    reserved_names = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
    }
    if safe_name.upper() in reserved_names:
        safe_name = f"{safe_name}_Calendar"
    base = downloads / f"{safe_name}_{month:%Y-%m}.pdf"
    if not base.exists():
        return base
    for suffix in range(2, 1000):
        candidate = base.with_name(f"{base.stem}_{suffix}{base.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError("Could not choose an available calendar PDF filename.")


def open_in_default_viewer(path: Path) -> None:
    if sys.platform == "win32":
        getattr(os, "startfile")(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def create_month_calendar_pdf(
    output_path: Path,
    month: date,
    calendar_name: str,
    entries: Iterable[CalendarPdfEntry],
    mode: CalendarPdfMode = "adaptive",
) -> None:
    try:
        from reportlab.lib.colors import Color, HexColor, black, white
        from reportlab.lib.pagesizes import landscape, letter
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from reportlab.pdfgen.canvas import Canvas
    except ImportError as exc:
        raise RuntimeError(
            "PDF export requires ReportLab. Reinstall or update Personal Assistant and try again."
        ) from exc

    margin = 24.0
    title_height = 51.0
    weekday_height = 21.0
    grid_bottom = 31.0
    entries_by_day: dict[date, list[CalendarPdfEntry]] = {}
    for entry in entries:
        entries_by_day.setdefault(entry.day, []).append(entry)

    weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(month.year, month.month)
    while len(weeks) < 6:
        next_start = weeks[-1][-1].toordinal() + 1
        weeks.append([date.fromordinal(next_start + offset) for offset in range(7)])

    base_width, base_height = landscape(letter)
    if mode == "large_format":
        largest_day = max(
            (len(entries_by_day.get(day, [])) for week in weeks[:6] for day in week if day.month == month.month),
            default=0,
        )
        required_cell_height = 29 + (12 * max(1, largest_day))
        required_height = (
            margin
            + title_height
            + weekday_height
            + grid_bottom
            + (6 * required_cell_height)
        )
        page_scale = max(1.0, required_height / base_height)
    elif mode == "adaptive":
        largest_day = 0
        page_scale = 1.0
    else:
        raise ValueError(f"Unsupported calendar PDF mode: {mode}")

    page_width = base_width * page_scale
    page_height = base_height * page_scale
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas = Canvas(str(output_path), pagesize=(page_width, page_height))
    canvas.setTitle(f"{month:%B %Y} - {calendar_name}")
    canvas.setAuthor("Personal Assistant")

    grid_top = page_height - margin - title_height - weekday_height
    cell_width = (page_width - (2 * margin)) / 7
    border = Color(0.76, 0.78, 0.81)
    muted = Color(0.45, 0.47, 0.50)
    outside_fill = Color(0.965, 0.965, 0.965)
    layout = _CalendarPdfLayout(
        page_width=page_width,
        page_height=page_height,
        margin=margin,
        weekday_height=weekday_height,
        grid_top=grid_top,
        grid_bottom=grid_bottom,
        cell_width=cell_width,
        border=border,
        muted=muted,
        outside_fill=outside_fill,
        black=black,
        white=white,
        hex_color=HexColor,
        string_width=stringWidth,
    )

    if mode == "large_format":
        _draw_calendar_page(
            canvas,
            month,
            calendar_name,
            weeks[:6],
            entries_by_day,
            0,
            max(1, largest_day),
            {},
            1,
            1,
            True,
            layout,
        )
        canvas.showPage()
        canvas.save()
        return

    overview_cell_height = (grid_top - grid_bottom) / 6
    weekly_cell_height = grid_top - grid_bottom
    overview_capacity = max(1, int((overview_cell_height - 29) // 12))
    weekly_capacity = max(1, int((weekly_cell_height - 29) // 12))
    detail_pages: list[tuple[int, int, int]] = []
    week_page_numbers: dict[int, int] = {}
    for week_index, week in enumerate(weeks[:6]):
        largest_day = max(len(entries_by_day.get(day, [])) for day in week)
        if largest_day <= overview_capacity:
            continue
        page_count = ceil(largest_day / weekly_capacity)
        week_page_numbers[week_index] = len(detail_pages) + 2
        for detail_index in range(page_count):
            detail_pages.append((week_index, detail_index, page_count))

    total_pages = len(detail_pages) + 1
    _draw_calendar_page(
        canvas,
        month,
        calendar_name,
        weeks[:6],
        entries_by_day,
        0,
        overview_capacity,
        week_page_numbers,
        1,
        total_pages,
        True,
        layout,
    )
    canvas.showPage()

    for page_number, (week_index, detail_index, page_count) in enumerate(detail_pages, start=2):
        week = weeks[week_index]
        detail_name = f"{calendar_name} | {_week_label(week)}"
        if page_count > 1:
            detail_name = f"{detail_name} | Part {detail_index + 1} of {page_count}"
        _draw_calendar_page(
            canvas,
            month,
            detail_name,
            [week],
            entries_by_day,
            detail_index * weekly_capacity,
            weekly_capacity,
            {},
            page_number,
            total_pages,
            False,
            layout,
        )
        canvas.showPage()

    canvas.save()


def _draw_calendar_page(
    canvas,
    month,
    subtitle,
    weeks,
    entries_by_day,
    entry_offset,
    entry_capacity,
    week_page_numbers,
    page_number,
    total_pages,
    use_overview_text,
    layout: _CalendarPdfLayout,
) -> None:
    canvas.setFillColor(layout.white)
    canvas.rect(0, 0, layout.page_width, layout.page_height, fill=1, stroke=0)
    canvas.setFillColor(layout.black)
    canvas.setFont("Helvetica-Bold", 22)
    canvas.drawCentredString(
        layout.page_width / 2,
        layout.page_height - layout.margin - 19,
        month.strftime("%B %Y").upper(),
    )
    canvas.setFillColor(layout.muted)
    canvas.setFont("Helvetica", 9)
    subtitle_text = _ellipsize(
        subtitle,
        layout.page_width - (2 * layout.margin),
        layout.string_width,
        "Helvetica",
        9,
    )
    canvas.drawCentredString(
        layout.page_width / 2,
        layout.page_height - layout.margin - 34,
        subtitle_text,
    )

    for column, weekday in enumerate(WEEKDAY_NAMES):
        x = layout.margin + (column * layout.cell_width)
        canvas.setFillColor(layout.hex_color("#EDEFF2"))
        canvas.setStrokeColor(layout.border)
        canvas.rect(
            x,
            layout.grid_top,
            layout.cell_width,
            layout.weekday_height,
            fill=1,
            stroke=1,
        )
        canvas.setFillColor(layout.black)
        canvas.setFont("Helvetica-Bold", 7)
        canvas.drawCentredString(x + (layout.cell_width / 2), layout.grid_top + 7, weekday)

    cell_height = (layout.grid_top - layout.grid_bottom) / len(weeks)
    for row, week in enumerate(weeks):
        y = layout.grid_top - ((row + 1) * cell_height)
        for column, day in enumerate(week):
            x = layout.margin + (column * layout.cell_width)
            in_month = day.month == month.month
            canvas.setFillColor(layout.white if in_month else layout.outside_fill)
            canvas.setStrokeColor(layout.border)
            canvas.rect(x, y, layout.cell_width, cell_height, fill=1, stroke=1)
            canvas.setFillColor(layout.black if in_month else layout.muted)
            canvas.setFont("Helvetica-Bold", 8)
            canvas.drawString(x + 4, y + cell_height - 11, str(day.day))
            if not in_month:
                continue

            day_entries = entries_by_day.get(day, [])
            visible_entries = day_entries[entry_offset : entry_offset + entry_capacity]
            remaining = len(day_entries) - entry_offset - len(visible_entries)
            if week_page_numbers and len(day_entries) > entry_capacity:
                visible_entries = visible_entries[: max(0, entry_capacity - 1)]
                remaining = len(day_entries) - len(visible_entries)
            event_y = y + cell_height - 25
            for entry in visible_entries:
                _draw_entry(canvas, entry, x, event_y, use_overview_text, layout)
                event_y -= 12

            if remaining > 0 and week_page_numbers:
                detail_page = week_page_numbers.get(row)
                suffix = f" - see page {detail_page}" if detail_page else ""
                canvas.setFillColor(layout.muted)
                canvas.setFont("Helvetica-Oblique", 6.5)
                canvas.drawString(x + 5, event_y + 1, f"+{remaining} more{suffix}")

    canvas.setFillColor(layout.muted)
    canvas.setFont("Helvetica", 7)
    canvas.drawCentredString(layout.page_width / 2, 16, f"Page {page_number} of {total_pages}")


def _draw_entry(canvas, entry, x, event_y, use_overview_text, layout: _CalendarPdfLayout) -> None:
    try:
        fill = layout.hex_color(entry.color)
    except (ValueError, TypeError):
        fill = layout.hex_color("#607D8B")
    luminance = (0.299 * fill.red) + (0.587 * fill.green) + (0.114 * fill.blue)
    foreground = layout.black if luminance > 0.73 else layout.white
    canvas.setFillColor(fill)
    canvas.roundRect(x + 3, event_y - 2, layout.cell_width - 6, 10, 2, fill=1, stroke=0)
    canvas.setFillColor(foreground)
    canvas.setFont("Helvetica-Bold", 6.5)
    text = entry.overview_text if use_overview_text and entry.overview_text is not None else entry.text
    label = _ellipsize(text, layout.cell_width - 12, layout.string_width)
    canvas.drawString(x + 6, event_y + 0.3, label)


def _week_label(week: list[date]) -> str:
    start = week[0]
    end = week[-1]
    if start.year != end.year:
        return f"Week of {start:%B %d, %Y} - {end:%B %d, %Y}"
    if start.month != end.month:
        return f"Week of {start:%B %d} - {end:%B %d, %Y}"
    return f"Week of {start:%B %d}-{end:%d, %Y}"


def _ellipsize(
    text: str,
    max_width: float,
    string_width,
    font_name: str = "Helvetica-Bold",
    font_size: float = 6.5,
) -> str:
    cleaned = " ".join(text.split())
    if string_width(cleaned, font_name, font_size) <= max_width:
        return cleaned
    suffix = "..."
    while cleaned and string_width(cleaned + suffix, font_name, font_size) > max_width:
        cleaned = cleaned[:-1]
    return cleaned.rstrip() + suffix
