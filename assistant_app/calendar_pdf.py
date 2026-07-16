from __future__ import annotations

import calendar
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable


WEEKDAY_NAMES = ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")


@dataclass(frozen=True, slots=True)
class CalendarPdfEntry:
    day: date
    text: str
    color: str


def downloads_pdf_path(calendar_name: str, month: date) -> Path:
    """Return a non-colliding PDF path in the current user's Downloads folder."""
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
    """Open a file with the operating system's registered default application."""
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def create_month_calendar_pdf(
    output_path: Path,
    month: date,
    calendar_name: str,
    entries: Iterable[CalendarPdfEntry],
) -> None:
    """Create a landscape, single-page, six-week month calendar PDF."""
    try:
        from reportlab.lib.colors import Color, HexColor, black, white
        from reportlab.lib.pagesizes import landscape, letter
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from reportlab.pdfgen.canvas import Canvas
    except ImportError as exc:
        raise RuntimeError(
            "PDF export requires ReportLab. Reinstall or update Personal Assistant and try again."
        ) from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    page_width, page_height = landscape(letter)
    canvas = Canvas(str(output_path), pagesize=(page_width, page_height))
    canvas.setTitle(f"{month:%B %Y} - {calendar_name}")
    canvas.setAuthor("Personal Assistant")

    margin = 24.0
    title_height = 51.0
    weekday_height = 21.0
    grid_top = page_height - margin - title_height - weekday_height
    grid_bottom = margin
    cell_width = (page_width - (2 * margin)) / 7
    cell_height = (grid_top - grid_bottom) / 6
    border = Color(0.76, 0.78, 0.81)
    muted = Color(0.45, 0.47, 0.50)
    outside_fill = Color(0.965, 0.965, 0.965)

    canvas.setFillColor(black)
    canvas.setFont("Helvetica-Bold", 22)
    canvas.drawCentredString(page_width / 2, page_height - margin - 19, month.strftime("%B %Y").upper())
    canvas.setFillColor(muted)
    canvas.setFont("Helvetica", 9)
    canvas.drawCentredString(page_width / 2, page_height - margin - 34, calendar_name)

    for column, weekday in enumerate(WEEKDAY_NAMES):
        x = margin + (column * cell_width)
        canvas.setFillColor(Color(0.93, 0.94, 0.95))
        canvas.setStrokeColor(border)
        canvas.rect(x, grid_top, cell_width, weekday_height, fill=1, stroke=1)
        canvas.setFillColor(black)
        canvas.setFont("Helvetica-Bold", 7)
        canvas.drawCentredString(x + (cell_width / 2), grid_top + 7, weekday)

    entries_by_day: dict[date, list[CalendarPdfEntry]] = {}
    for entry in entries:
        entries_by_day.setdefault(entry.day, []).append(entry)

    weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(month.year, month.month)
    # A fixed six-row layout keeps every exported month visually consistent.
    while len(weeks) < 6:
        next_start = weeks[-1][-1].toordinal() + 1
        weeks.append([date.fromordinal(next_start + offset) for offset in range(7)])

    for row, week in enumerate(weeks[:6]):
        y = grid_top - ((row + 1) * cell_height)
        for column, day in enumerate(week):
            x = margin + (column * cell_width)
            in_month = day.month == month.month
            canvas.setFillColor(white if in_month else outside_fill)
            canvas.setStrokeColor(border)
            canvas.rect(x, y, cell_width, cell_height, fill=1, stroke=1)
            canvas.setFillColor(black if in_month else muted)
            canvas.setFont("Helvetica-Bold", 8)
            canvas.drawString(x + 4, y + cell_height - 11, str(day.day))
            if not in_month:
                continue

            row_height = 12.0
            event_y = y + cell_height - 25
            max_rows = max(1, int((cell_height - 29) // row_height))
            day_entries = entries_by_day.get(day, [])
            visible_entries = day_entries[:max_rows]
            if len(day_entries) > max_rows:
                visible_entries = day_entries[: max_rows - 1]

            for entry in visible_entries:
                try:
                    fill = HexColor(entry.color)
                except (ValueError, TypeError):
                    fill = HexColor("#607D8B")
                luminance = (0.299 * fill.red) + (0.587 * fill.green) + (0.114 * fill.blue)
                foreground = black if luminance > 0.73 else white
                canvas.setFillColor(fill)
                canvas.roundRect(x + 3, event_y - 2, cell_width - 6, 10, 2, fill=1, stroke=0)
                canvas.setFillColor(foreground)
                canvas.setFont("Helvetica-Bold", 6.5)
                available_width = cell_width - 12
                label = _ellipsize(entry.text, available_width, stringWidth)
                canvas.drawString(x + 6, event_y + 0.3, label)
                event_y -= row_height

            remaining = len(day_entries) - len(visible_entries)
            if remaining > 0:
                canvas.setFillColor(muted)
                canvas.setFont("Helvetica-Oblique", 6.5)
                canvas.drawString(x + 5, event_y + 1, f"+{remaining} more")

    canvas.showPage()
    canvas.save()


def _ellipsize(text: str, max_width: float, string_width) -> str:
    cleaned = " ".join(text.split())
    if string_width(cleaned, "Helvetica-Bold", 6.5) <= max_width:
        return cleaned
    suffix = "..."
    while cleaned and string_width(cleaned + suffix, "Helvetica-Bold", 6.5) > max_width:
        cleaned = cleaned[:-1]
    return cleaned.rstrip() + suffix
