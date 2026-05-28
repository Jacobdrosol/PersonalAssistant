from __future__ import annotations

import calendar as cal
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from textwrap import shorten
from typing import Dict, Iterable, List, Optional

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import utils
from .database import Database
from .environment import APP_NAME, ensure_user_data_dir
from .models import Calendar, Event, EventOverride, ProductionCalendar
from .settings_store import load_settings
from .version import __version__

WEEKDAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
CUSTOMIZED_OCCURRENCE_MARK = "*"


@dataclass(slots=True)
class DayOccurrence:
    occurrence: datetime
    event: Event
    override: Optional[EventOverride]


@dataclass(slots=True)
class EventRegion:
    rect: QRect
    entry: DayOccurrence


class CalendarCanvas(QWidget):
    daySelected = Signal(object)
    occurrenceActivated = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.current_month = datetime.now().date().replace(day=1)
        self.selected_day = datetime.now().date()
        self.days: List[date] = []
        self.occurrences_by_day: Dict[date, List[DayOccurrence]] = defaultdict(list)
        self.cell_regions: List[tuple[QRect, date]] = []
        self.event_regions: List[EventRegion] = []
        self.setMinimumSize(QSize(520, 420))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

        self.window_bg = QColor("#171821")
        self.cell_bg = QColor("#232337")
        self.cell_selected_bg = QColor("#31314a")
        self.border = QColor("#2c2f45")
        self.text = QColor("#E8EAF6")
        self.text_secondary = QColor("#9FA8DA")
        self.outside_text = QColor("#61647a")

    def set_month_data(
        self,
        *,
        current_month: date,
        selected_day: date,
        days: List[date],
        occurrences_by_day: Dict[date, List[DayOccurrence]],
    ) -> None:
        self.current_month = current_month
        self.selected_day = selected_day
        self.days = days
        self.occurrences_by_day = occurrences_by_day
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.fillRect(self.rect(), self.window_bg)
        self.cell_regions = []
        self.event_regions = []
        if not self.days:
            return

        width = max(self.width(), 1)
        height = max(self.height(), 1)
        header_height = 30
        grid_height = max(1, height - header_height)
        cell_width = width / 7
        cell_height = grid_height / 6

        painter.setPen(QPen(self.border))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        for col, name in enumerate(WEEKDAY_NAMES):
            x0 = round(col * cell_width)
            x1 = round((col + 1) * cell_width)
            rect = QRect(x0, 0, x1 - x0, header_height)
            painter.fillRect(rect, self.window_bg)
            painter.drawRect(rect)
            painter.setPen(self.text_secondary)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, name)
            painter.setPen(QPen(self.border))

        for index, day in enumerate(self.days):
            row = index // 7
            col = index % 7
            x0 = round(col * cell_width)
            x1 = round((col + 1) * cell_width)
            y0 = round(header_height + row * cell_height)
            y1 = round(header_height + (row + 1) * cell_height)
            rect = QRect(x0, y0, x1 - x0, y1 - y0)
            bg = self.cell_selected_bg if day == self.selected_day else self.cell_bg
            painter.fillRect(rect, bg)
            painter.setPen(QPen(self.border))
            painter.drawRect(rect)
            painter.setPen(self.text if day.month == self.current_month.month else self.outside_text)
            painter.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
            painter.drawText(rect.adjusted(6, 5, -4, -4), Qt.AlignmentFlag.AlignTop, str(day.day))
            self.cell_regions.append((rect, day))
            self._draw_day_occurrences(painter, day, rect)

    def _draw_day_occurrences(self, painter: QPainter, day: date, cell_rect: QRect) -> None:
        occurrences = self.occurrences_by_day.get(day, [])
        if not occurrences:
            return
        row_height = 18
        gap = 2
        y = cell_rect.top() + 30
        max_bottom = cell_rect.bottom() - 4
        available_rows = max(0, int((max_bottom - y + gap) // (row_height + gap)))
        visible_count = min(4, available_rows, len(occurrences))
        text_width = max(12, int((cell_rect.width() - 18) / 7))
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        for entry in occurrences[:visible_count]:
            event = entry.event
            override = entry.override
            color = (
                override.calendar_color
                if override and override.calendar_color
                else event.calendar_color
                or "#607D8B"
            )
            bg = QColor(color)
            fg = QColor(utils.ideal_text_color(color))
            rect = QRect(cell_rect.left() + 4, y, max(1, cell_rect.width() - 8), row_height)
            painter.fillRect(rect, bg)
            painter.setPen(fg)
            title = override.title if override and override.title else event.title
            time_text = utils.format_time(entry.occurrence)
            label = f"{time_text} {title}" if entry.occurrence.time() != datetime.min.time() else title
            if self._is_customized_occurrence(entry):
                label += f" {CUSTOMIZED_OCCURRENCE_MARK}"
            painter.drawText(
                rect.adjusted(4, 0, -4, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                shorten(label, width=text_width, placeholder="..."),
            )
            self.event_regions.append(EventRegion(rect=rect, entry=entry))
            y = rect.bottom() + 1 + gap

        remaining = len(occurrences) - visible_count
        if remaining > 0:
            painter.setPen(self.text_secondary)
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(
                QRect(cell_rect.left() + 8, min(y, max_bottom), cell_rect.width() - 16, row_height),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                f"+{remaining}",
            )

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        day = self._day_at(event.position().toPoint())
        if day is not None:
            self.selected_day = day
            self.update()
            self.daySelected.emit(day)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        point = event.position().toPoint()
        for region in self.event_regions:
            if region.rect.contains(point):
                self.occurrenceActivated.emit(region.entry)
                return

    def _day_at(self, point: QPoint) -> Optional[date]:
        for rect, day in self.cell_regions:
            if rect.contains(point):
                return day
        return None

    @staticmethod
    def _is_customized_occurrence(entry: DayOccurrence) -> bool:
        override = entry.override
        if override is None:
            return False
        fields = (override.title, override.description, override.calendar_color, override.note)
        return any(isinstance(value, str) and value.strip() for value in fields) or override.manual_schedule is not None


class CalendarPage(QWidget):
    def __init__(self, db: Database, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.db = db
        self.current_month = datetime.now().date().replace(day=1)
        self.selected_day = datetime.now().date()
        self.production_calendars: List[ProductionCalendar] = []
        self.current_production_id: Optional[int] = None
        self.calendars: List[Calendar] = []
        self.visible_calendar_ids: set[int] = set()
        self.events: List[Event] = []
        self.occurrences_by_day: Dict[date, List[DayOccurrence]] = defaultdict(list)
        self._calendar_checks: Dict[int, QCheckBox] = {}
        self._suspend_production_change = False
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        selector = QHBoxLayout()
        selector.setSpacing(8)
        selector.addWidget(QLabel("Production Calendar"))
        self.production_combo = QComboBox()
        self.production_combo.setMinimumWidth(260)
        self.production_combo.currentIndexChanged.connect(self._on_production_selected)
        selector.addWidget(self.production_combo)
        selector.addStretch(1)
        root.addLayout(selector)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        toolbar = QHBoxLayout()
        self.prev_button = QPushButton("<")
        self.next_button = QPushButton(">")
        self.today_button = QPushButton("Today")
        self.month_label = QLabel("")
        self.month_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self.month_label.font()
        font.setPointSize(14)
        font.setBold(True)
        self.month_label.setFont(font)
        self.prev_button.clicked.connect(self.go_to_previous_month)
        self.next_button.clicked.connect(self.go_to_next_month)
        self.today_button.clicked.connect(self.go_to_today)
        toolbar.addWidget(self.prev_button)
        toolbar.addWidget(self.next_button)
        toolbar.addWidget(self.month_label, 1)
        toolbar.addWidget(self.today_button)
        left_layout.addLayout(toolbar)

        self.calendar_canvas = CalendarCanvas()
        self.calendar_canvas.daySelected.connect(self.select_day)
        self.calendar_canvas.occurrenceActivated.connect(self._show_occurrence_detail)
        left_layout.addWidget(self.calendar_canvas, 1)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 0, 0, 0)
        right_layout.setSpacing(10)
        right_layout.addWidget(QLabel("Calendars"))

        self.calendar_filter_area = QScrollArea()
        self.calendar_filter_area.setWidgetResizable(True)
        self.calendar_filter_body = QWidget()
        self.calendar_filter_layout = QVBoxLayout(self.calendar_filter_body)
        self.calendar_filter_layout.setContentsMargins(0, 0, 0, 0)
        self.calendar_filter_layout.addStretch(1)
        self.calendar_filter_area.setWidget(self.calendar_filter_body)
        right_layout.addWidget(self.calendar_filter_area, 1)

        self.day_label = QLabel("")
        right_layout.addWidget(self.day_label)
        self.day_events_tree = QTreeWidget()
        self.day_events_tree.setHeaderLabels(["Time", "Title", "Calendar"])
        self.day_events_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.day_events_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.day_events_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        right_layout.addWidget(self.day_events_tree, 2)
        splitter.addWidget(right)
        splitter.setSizes([900, 420])

    def refresh(self) -> None:
        self._load_production_calendars()
        if self.current_production_id is None:
            self.calendars = []
            self.visible_calendar_ids = set()
            self.events = []
            self.occurrences_by_day = defaultdict(list)
            self._render_calendar()
            self._rebuild_calendar_filters()
            self._populate_day_events()
            return
        self._load_calendars()
        self._load_events()
        self._build_occurrences()
        self._render_calendar()
        self._rebuild_calendar_filters()
        self._populate_day_events()

    def _load_production_calendars(self) -> None:
        self.production_calendars = self.db.get_production_calendars()
        if self.current_production_id is not None and not any(
            item.id == self.current_production_id for item in self.production_calendars
        ):
            self.current_production_id = None
        if self.production_calendars and self.current_production_id is None:
            self.current_production_id = self.production_calendars[0].id
        self._update_production_selector()

    def _update_production_selector(self) -> None:
        self._suspend_production_change = True
        self.production_combo.clear()
        for production in self.production_calendars:
            self.production_combo.addItem(production.name, production.id)
        if self.current_production_id is not None:
            for index in range(self.production_combo.count()):
                if self.production_combo.itemData(index) == self.current_production_id:
                    self.production_combo.setCurrentIndex(index)
                    break
        self._suspend_production_change = False

    def _on_production_selected(self, index: int) -> None:
        if self._suspend_production_change or index < 0:
            return
        production_id = self.production_combo.itemData(index)
        if production_id != self.current_production_id:
            self.current_production_id = int(production_id)
            self.refresh()

    def _load_calendars(self) -> None:
        self.calendars = self.db.get_calendars(production_calendar_id=self.current_production_id)
        self.visible_calendar_ids = {item.id for item in self.calendars if item.is_visible}
        if not self.visible_calendar_ids and self.calendars:
            self.visible_calendar_ids.add(self.calendars[0].id)

    def _load_events(self) -> None:
        self.events = self.db.get_events(calendar_ids=self.visible_calendar_ids) if self.visible_calendar_ids else []

    def _build_occurrences(self) -> None:
        weeks = cal.Calendar(firstweekday=6).monthdatescalendar(self.current_month.year, self.current_month.month)
        start_dt = datetime.combine(weeks[0][0], datetime.min.time())
        end_dt = datetime.combine(weeks[-1][-1], datetime.max.time())
        self.occurrences_by_day = defaultdict(list)
        overrides = self.db.get_event_overrides(
            (event.id for event in self.events),
            start_dt.date(),
            end_dt.date(),
        )
        for event in self.events:
            for occurrence in event.occurrences_between(start_dt, end_dt):
                key = (event.id, occurrence.date())
                self.occurrences_by_day[occurrence.date()].append(
                    DayOccurrence(
                        occurrence=occurrence,
                        event=event,
                        override=overrides.get(key),
                    )
                )
        for entries in self.occurrences_by_day.values():
            entries.sort(key=lambda item: item.occurrence)

    def _render_calendar(self) -> None:
        weeks = cal.Calendar(firstweekday=6).monthdatescalendar(self.current_month.year, self.current_month.month)
        days = [day for week in weeks for day in week]
        self.month_label.setText(self.current_month.strftime("%B %Y"))
        self.calendar_canvas.set_month_data(
            current_month=self.current_month,
            selected_day=self.selected_day,
            days=days,
            occurrences_by_day=self.occurrences_by_day,
        )
        self._update_selected_day_label()

    def _rebuild_calendar_filters(self) -> None:
        while self.calendar_filter_layout.count() > 0:
            item = self.calendar_filter_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._calendar_checks = {}
        for calendar_model in self.calendars:
            check = QCheckBox(calendar_model.name)
            check.setChecked(calendar_model.id in self.visible_calendar_ids)
            check.setStyleSheet(f"QCheckBox {{ color: {calendar_model.color}; }}")
            check.stateChanged.connect(
                lambda state, calendar_id=calendar_model.id: self._toggle_calendar(calendar_id, state == Qt.CheckState.Checked.value)
            )
            self._calendar_checks[calendar_model.id] = check
            self.calendar_filter_layout.addWidget(check)
        self.calendar_filter_layout.addStretch(1)

    def _toggle_calendar(self, calendar_id: int, visible: bool) -> None:
        if visible:
            self.visible_calendar_ids.add(calendar_id)
        else:
            self.visible_calendar_ids.discard(calendar_id)
        try:
            self.db.update_calendar(calendar_id, is_visible=visible)
        except Exception:
            pass
        self._load_events()
        self._build_occurrences()
        self._render_calendar()
        self._populate_day_events()

    def select_day(self, day_value: object) -> None:
        if isinstance(day_value, date):
            self.selected_day = day_value
            self.calendar_canvas.selected_day = day_value
            self.calendar_canvas.update()
            self._update_selected_day_label()
            self._populate_day_events()

    def _update_selected_day_label(self) -> None:
        self.day_label.setText(self.selected_day.strftime("%A, %B %d, %Y"))

    def _populate_day_events(self) -> None:
        self.day_events_tree.clear()
        for entry in self.occurrences_by_day.get(self.selected_day, []):
            title = entry.override.title if entry.override and entry.override.title else entry.event.title
            if CalendarCanvas._is_customized_occurrence(entry):
                title = f"{title} {CUSTOMIZED_OCCURRENCE_MARK}"
            item = QTreeWidgetItem(
                [
                    utils.format_time(entry.occurrence),
                    title,
                    entry.event.calendar_name,
                ]
            )
            self.day_events_tree.addTopLevelItem(item)

    def go_to_previous_month(self) -> None:
        self.current_month = utils.add_months(datetime.combine(self.current_month, datetime.min.time()), -1).date()
        self.current_month = self.current_month.replace(day=1)
        if self.selected_day.month != self.current_month.month:
            self.selected_day = self.current_month
        self.refresh()

    def go_to_next_month(self) -> None:
        self.current_month = utils.add_months(datetime.combine(self.current_month, datetime.min.time()), 1).date()
        self.current_month = self.current_month.replace(day=1)
        if self.selected_day.month != self.current_month.month:
            self.selected_day = self.current_month
        self.refresh()

    def go_to_today(self) -> None:
        today = datetime.now().date()
        self.current_month = today.replace(day=1)
        self.selected_day = today
        self.refresh()

    def _show_occurrence_detail(self, entry: object) -> None:
        if not isinstance(entry, DayOccurrence):
            return
        title = entry.override.title if entry.override and entry.override.title else entry.event.title
        body = entry.override.description if entry.override and entry.override.description else entry.event.description
        QMessageBox.information(
            self,
            "Calendar Event",
            f"{title}\n\n{utils.format_datetime(entry.occurrence)}\n\n{body or 'No description.'}",
        )


class QtPersonalAssistant(QMainWindow):
    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self.db = Database(db_path)
        self.setWindowTitle(f"{APP_NAME} Qt Preview")
        self.resize(1380, 900)
        self.setMinimumSize(960, 700)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        self.nav = QListWidget()
        self.nav.setFixedHeight(42)
        self.nav.setFlow(QListWidget.Flow.LeftToRight)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.addItem(QListWidgetItem("Production Calendar"))
        self.nav.setCurrentRow(0)
        layout.addWidget(self.nav)
        self.calendar_page = CalendarPage(self.db)
        layout.addWidget(self.calendar_page, 1)
        self.setCentralWidget(central)

        self.statusBar().showMessage(f"Version {__version__}")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.db.close()
        super().closeEvent(event)


def _apply_palette(app: QApplication) -> None:
    app.setStyleSheet(
        """
        QMainWindow, QWidget {
            background: #171821;
            color: #E8EAF6;
            font-family: Segoe UI;
            font-size: 10pt;
        }
        QPushButton, QComboBox, QTreeWidget, QListWidget {
            background: #222338;
            color: #E8EAF6;
            border: 1px solid #2c2f45;
            padding: 6px;
        }
        QPushButton:hover {
            background: #31314a;
        }
        QHeaderView::section {
            background: #222338;
            color: #9FA8DA;
            border: 1px solid #2c2f45;
            padding: 5px;
        }
        QSplitter::handle {
            background: #2c2f45;
        }
        """
    )


def main() -> None:
    data_root = ensure_user_data_dir()
    settings_path = data_root / "settings.json"
    load_settings(settings_path)
    app = QApplication(sys.argv)
    _apply_palette(app)
    window = QtPersonalAssistant(data_root / "assistant.db")
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
