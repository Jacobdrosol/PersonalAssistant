from __future__ import annotations

import calendar as cal
import json
from collections import defaultdict
from textwrap import shorten
from dataclasses import dataclass
from datetime import datetime, timedelta, date
from pathlib import Path
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox
from tkinter import ttk
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .database import Database
from .models import Calendar, Event, EventOverride, ProductionCalendar
from . import utils
from .theme import ThemePalette

WEEKDAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
REPEAT_OPTIONS = [
    ("None", "none"),
    ("Daily", "daily"),
    ("Weekly", "weekly"),
    ("Monthly", "monthly"),
    ("Yearly", "yearly"),
]

CUSTOMIZED_OCCURRENCE_MARK = "\u270E"  # matches the calendar grid indicator


@dataclass
class DayCell:
    frame: tk.Frame
    day_label: tk.Label
    events_container: tk.Frame
    date: Optional[date] = None


@dataclass
class DayOccurrence:
    occurrence: datetime
    event: Event
    override: Optional[EventOverride]


class CalendarTab(ttk.Frame):
    def __init__(self, master: tk.Misc, db: Database, theme: ThemePalette):
        super().__init__(master)
        self.db = db
        self.theme = theme
        self.configure(padding=(16, 16))
        self.current_month = datetime.now().date().replace(day=1)
        self.selected_day = datetime.now().date()
        self.production_calendars: List[ProductionCalendar] = []
        self.current_production_id: Optional[int] = None
        self.calendars: List[Calendar] = []
        self.visible_calendar_ids: set[int] = set()
        self.events: List[Event] = []
        self.occurrences_by_day: Dict[date, List[Tuple[datetime, Event]]] = defaultdict(list)
        self.calendar_vars: Dict[int, tk.BooleanVar] = {}
        self.day_cells: List[DayCell] = []
        self.selected_cell: Optional[DayCell] = None
        self._suspend_production_callback = False
        self._modal_overlay: tk.Frame | None = None
        self._modal_panel: tk.Frame | None = None
        self.month_label: Optional[ttk.Label] = None
        self.calendars_frame: Optional[ttk.Frame] = None
        self.day_value_label: Optional[ttk.Label] = None
        self.day_events_tree: Optional[ttk.Treeview] = None
        self._day_occurrence_index: Dict[str, DayOccurrence] = {}
        self._calendar_checkbuttons: List[ttk.Checkbutton] = []
        self._calendar_edit_buttons: List[ttk.Button] = []
        self._interactive_buttons: List[tk.Widget] = []
        self._interactive_comboboxes: List[ttk.Combobox] = []
        self._interactive_treeviews: List[ttk.Treeview] = []
        self.search_var: tk.StringVar | None = None
        self.search_entry: ttk.Entry | None = None
        self._search_popup: tk.Toplevel | None = None
        self._search_listbox: tk.Listbox | None = None
        self._search_result_events: List[Optional[Event]] = []

        self._assign_palette_colors()

        self._build_ui()
        self.refresh()

    def _assign_palette_colors(self) -> None:
        palette = self.theme
        self.bg_color = palette.surface_bg
        self.sidebar_bg = palette.card_bg
        self.cell_bg = palette.calendar_cell_bg
        self.cell_selected_bg = palette.calendar_cell_selected_bg
        self.outside_month_color = palette.calendar_outside_text
        self.text_color = palette.text_primary
        self.secondary_text_color = palette.text_secondary
        self.list_bg = palette.list_bg
        self.list_fg = palette.text_primary
        self.list_selected_bg = palette.list_selected_bg
        self.list_selected_fg = palette.list_selected_fg

    def apply_theme(self, theme: ThemePalette) -> None:
        self.theme = theme
        self._assign_palette_colors()
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self._destroy_search_popup()
        for child in self.winfo_children():
            child.destroy()

        selector = ttk.Frame(self)
        selector.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(selector, text="Production Calendar", style="SidebarHeading.TLabel").pack(side=tk.LEFT)
        self.production_combo = ttk.Combobox(selector, state="readonly", width=28)
        self.production_combo.pack(side=tk.LEFT, padx=(12, 0))
        self.production_combo.bind("<<ComboboxSelected>>", self._on_production_selected)

        self.production_color_patch = tk.Canvas(selector, width=20, height=20, highlightthickness=0, bg=self.bg_color)
        self.production_color_patch.pack(side=tk.LEFT, padx=(12, 0))
        self.production_color_patch.create_rectangle(0, 0, 20, 20, fill="#4F75FF", outline="")

        ttk.Button(selector, text="New...", command=self.add_production_calendar).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Button(selector, text="Edit...", command=self.edit_current_production_calendar).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(selector, text="Export...", command=self.export_current_production_calendar).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(selector, text="Import...", command=self.import_production_calendar).pack(side=tk.LEFT, padx=(6, 0))
        self._create_search_bar(selector)

        paned = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        container = ttk.Frame(paned)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        sidebar_outer = ttk.Frame(paned)
        sidebar_outer.columnconfigure(0, weight=1)
        sidebar_outer.rowconfigure(0, weight=1)

        paned.add(container, weight=3)
        paned.add(sidebar_outer, weight=2)

        # Left: calendar grid --------------------------------------------------
        left = ttk.Frame(container)
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(left)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        toolbar.columnconfigure(2, weight=1)

        self.prev_btn = ttk.Button(toolbar, text="<", width=3, command=self.go_to_previous_month)
        self.prev_btn.grid(row=0, column=0, padx=(0, 6))
        self.next_btn = ttk.Button(toolbar, text=">", width=3, command=self.go_to_next_month)
        self.next_btn.grid(row=0, column=1, padx=(0, 6))

        self.month_label = ttk.Label(toolbar, text="", style="CalendarHeading.TLabel")
        self.month_label.grid(row=0, column=2)

        self.today_btn = ttk.Button(toolbar, text="Today", command=self.go_to_today)
        self.today_btn.grid(row=0, column=3, padx=6)

        self.add_event_button = ttk.Button(toolbar, text="Add Event", command=self.add_event_for_selected_day)
        self.add_event_button.grid(row=0, column=4)

        self.recap_button = ttk.Button(toolbar, text="Generate Recap", command=self.open_recap_dialog)
        self.recap_button.grid(row=0, column=5, padx=(12, 0))

        grid_frame = ttk.Frame(left)
        grid_frame.grid(row=1, column=0, sticky="nsew")
        for c in range(7):
            grid_frame.columnconfigure(c, weight=1, uniform="day")
        for r in range(6):
            grid_frame.rowconfigure(r + 1, weight=1, uniform="dayrow")

        # Header row with weekday names
        for col, name in enumerate(WEEKDAY_NAMES):
            header = tk.Label(
                grid_frame,
                text=name,
                bg=self.bg_color,
                fg=self.secondary_text_color,
                padx=4,
                pady=4,
                font=("Segoe UI", 10, "bold"),
            )
            header.grid(row=0, column=col, sticky="nsew", padx=1, pady=1)

        # Create day cells (6x7)
        self.day_cells = []
        for row in range(6):
            for col in range(7):
                frame = tk.Frame(grid_frame, bg=self.cell_bg, bd=0, highlightthickness=0)
                frame.grid(row=row + 1, column=col, sticky="nsew", padx=1, pady=1)
                frame.bind("<Button-1>", lambda e, idx=len(self.day_cells): self._on_cell_click(idx))

                day_label = tk.Label(
                    frame,
                    text="",
                    anchor="nw",
                    bg=self.cell_bg,
                    fg=self.text_color,
                    font=("Segoe UI", 11, "bold"),
                    padx=6,
                    pady=4,
                )
                day_label.pack(fill=tk.X)
                day_label.bind("<Button-1>", lambda e, idx=len(self.day_cells): self._on_cell_click(idx))

                events_container = tk.Frame(frame, bg=self.cell_bg)
                events_container.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

                events_container.bind("<Button-1>", lambda e, idx=len(self.day_cells): self._on_cell_click(idx))

                cell = DayCell(frame=frame, day_label=day_label, events_container=events_container)
                self.day_cells.append(cell)

        sidebar = ttk.Frame(sidebar_outer, padding=(12, 0))
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar_outer.columnconfigure(0, weight=1)
        sidebar_outer.rowconfigure(0, weight=1)

        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(0, weight=0)
        sidebar.rowconfigure(1, weight=1, minsize=16)
        sidebar.rowconfigure(2, weight=0)

        self.sidebar = sidebar

        top_container = ttk.Frame(sidebar)
        top_container.grid(row=0, column=0, sticky="ew")
        top_container.columnconfigure(0, weight=1)

        calendars_label = ttk.Label(top_container, text="Calendars", style="SidebarHeading.TLabel")
        calendars_label.grid(row=0, column=0, sticky="w")

        self.calendars_frame = ttk.Frame(top_container)
        self.calendars_frame.grid(row=1, column=0, sticky="ew", pady=(6, 12))
        self.calendars_frame.columnconfigure(1, weight=1)

        add_calendar_btn = ttk.Button(top_container, text="Add Calendar", command=self.add_calendar)
        add_calendar_btn.grid(row=2, column=0, sticky="ew")

        spacer = ttk.Frame(sidebar)
        spacer.grid(row=1, column=0, sticky="nsew")

        selected_container = ttk.Frame(sidebar, padding=(0, 0))
        selected_container.grid(row=2, column=0, sticky="sew", pady=(24, 0))
        selected_container.columnconfigure(0, weight=1)
        selected_container.rowconfigure(2, weight=1, minsize=260)

        day_label = ttk.Label(selected_container, text="Selected Day", style="SidebarHeading.TLabel")
        day_label.grid(row=0, column=0, sticky="w")

        self.day_value_label = ttk.Label(selected_container, text="", style="SelectedDay.TLabel")
        self.day_value_label.grid(row=1, column=0, sticky="w", pady=(0, 8))

        columns = ("time", "title", "calendar")
        self.day_events_tree = ttk.Treeview(
            selected_container,
            columns=columns,
            show="headings",
            selectmode="browse",
            height=16,
        )
        self.day_events_tree.heading("time", text="Time")
        self.day_events_tree.heading("title", text="Title")
        self.day_events_tree.heading("calendar", text="Calendar")
        self.day_events_tree.column("time", width=80, anchor="w")
        self.day_events_tree.column("title", width=180, anchor="w")
        self.day_events_tree.column("calendar", width=140, anchor="w")
        self.day_events_tree.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        self.day_events_tree.bind("<Double-1>", lambda e: self.edit_selected_event())

        buttons = ttk.Frame(selected_container)
        buttons.grid(row=3, column=0, sticky="ew")
        self.day_add_btn = ttk.Button(buttons, text="Add", command=self.add_event_for_selected_day)
        self.day_add_btn.grid(row=0, column=0, padx=(0, 6))
        self.day_customize_btn = ttk.Button(buttons, text="Customize", command=self.customize_selected_occurrence)
        self.day_customize_btn.grid(row=0, column=1, padx=(0, 6))
        self.day_edit_btn = ttk.Button(buttons, text="Edit", command=self.edit_selected_event)
        self.day_edit_btn.grid(row=0, column=2, padx=(0, 6))
        self.day_delete_btn = ttk.Button(buttons, text="Delete", command=self.delete_selected_event)
        self.day_delete_btn.grid(row=0, column=3)

        for idx in range(4):
            buttons.columnconfigure(idx, weight=1)

        self._interactive_buttons = [
            self.prev_btn,
            self.next_btn,
            self.today_btn,
            self.add_event_button,
            self.recap_button,
            self.day_add_btn,
            self.day_customize_btn,
            self.day_edit_btn,
            self.day_delete_btn,
        ]
        self._interactive_comboboxes = [self.production_combo]
        self._interactive_treeviews = [self.day_events_tree]

        self.after(150, lambda: self._init_paned_position(paned))

    def _init_paned_position(self, paned: ttk.Panedwindow) -> None:
        width = paned.winfo_width()
        if width <= 1:
            self.after(150, lambda: self._init_paned_position(paned))
            return
        sidebar_min = 320
        left_min = 520
        ideal = int(width * 0.58)
        if width <= left_min + sidebar_min:
            target = max(int(width * 0.55), width - sidebar_min)
        else:
            target = max(left_min, min(ideal, width - sidebar_min))
        target = max(220, min(target, width - 160))
        try:
            paned.sashpos(0, target)
        except tk.TclError:
            pass

    def _destroy_search_popup(self) -> None:
        if self._search_popup is not None:
            try:
                self._search_popup.destroy()
            except tk.TclError:
                pass
        self._search_popup = None
        self._search_listbox = None
        self._search_result_events = []

    def _create_search_bar(self, parent: tk.Widget) -> None:
        search_container = ttk.Frame(parent)
        search_container.pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(search_container, text="Search", style="SidebarHeading.TLabel").pack(side=tk.LEFT, padx=(0, 6))
        self.search_var = tk.StringVar()
        entry = ttk.Entry(search_container, textvariable=self.search_var, width=44)
        entry.pack(side=tk.LEFT)
        self.search_entry = entry
        self.search_var.trace_add("write", lambda *_: self._update_search_results())
        entry.bind("<Escape>", lambda _: self._clear_search_results(clear_text=True))
        entry.bind("<Return>", lambda _: self._activate_first_search_result())
        entry.bind("<Down>", self._focus_search_results)
        entry.bind("<FocusOut>", lambda _: self.after(120, self._maybe_hide_search_popup))
        self._initialize_search_popup()

    def _initialize_search_popup(self) -> None:
        if self._search_popup is not None:
            return
        popup = tk.Toplevel(self)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.transient(self.winfo_toplevel())
        popup.attributes("-topmost", True)
        listbox = tk.Listbox(
            popup,
            activestyle="none",
            exportselection=False,
            bg=self.list_bg,
            fg=self.list_fg,
            selectbackground=self.list_selected_bg,
            selectforeground=self.list_selected_fg,
            highlightthickness=1,
            bd=0,
            relief="solid",
            font=("Segoe UI", 10),
        )
        listbox.pack(fill=tk.BOTH, expand=True)
        listbox.bind("<ButtonRelease-1>", lambda _: self._handle_search_result_click())
        listbox.bind("<Return>", lambda _: self._handle_search_result_click())
        listbox.bind("<Escape>", lambda _: self._clear_search_results())
        listbox.bind("<FocusOut>", lambda _: self.after(120, self._maybe_hide_search_popup))
        listbox.bind("<Up>", self._search_listbox_nav_up)
        listbox.bind("<Down>", self._search_listbox_nav_down)
        self._search_popup = popup
        self._search_listbox = listbox

    def _focus_search_results(self, event: tk.Event) -> str:
        if self._search_listbox and self._search_popup and self._search_popup.state() != "withdrawn":
            self._search_listbox.focus_set()
            if self._search_listbox.size():
                self._search_listbox.selection_clear(0, tk.END)
                self._search_listbox.selection_set(0)
                self._search_listbox.activate(0)
        return "break"

    def _search_listbox_nav_up(self, event: tk.Event) -> str:
        if not self._search_listbox:
            return "break"
        selection = self._search_listbox.curselection()
        if selection and selection[0] == 0:
            if self.search_entry:
                self.search_entry.focus_set()
                self.search_entry.icursor(tk.END)
            return "break"
        return None

    def _search_listbox_nav_down(self, event: tk.Event) -> None:
        return None

    def _update_search_results(self) -> None:
        if not self.search_var or not self.search_entry:
            return
        query = self.search_var.get().strip()
        if not query:
            self._hide_search_popup()
            return
        key = query.lower()
        matches: List[Event] = []
        seen_ids: set[int] = set()
        for event in self.events:
            if event.id in seen_ids:
                continue
            title = (event.title or "").lower()
            if key in title:
                matches.append(event)
                seen_ids.add(event.id)
            if len(matches) >= 15:
                break
        if not matches:
            self._populate_search_results([(None, f'No results for "{query}"')])
        else:
            items = []
            for ev in matches:
                calendar_name = ev.calendar_name or "Unknown"
                items.append((ev, f"{ev.title} — {calendar_name}"))
            self._populate_search_results(items)

    def _populate_search_results(self, items: List[tuple[Optional[Event], str]]) -> None:
        if not self._search_popup or not self._search_listbox or not self.search_entry:
            return
        self._search_listbox.delete(0, tk.END)
        self._search_result_events = []
        for event_obj, label in items:
            self._search_listbox.insert(tk.END, label)
            self._search_result_events.append(event_obj)
        count = len(items)
        if count == 0:
            self._hide_search_popup()
            return
        height_rows = max(1, min(count, 8))
        self._search_listbox.configure(height=height_rows)
        entry_width = self.search_entry.winfo_width()
        x = self.search_entry.winfo_rootx()
        y = self.search_entry.winfo_rooty() + self.search_entry.winfo_height()
        self._search_popup.geometry(f"{entry_width}x{height_rows * 24}+{x}+{y}")
        self._search_popup.deiconify()
        self._search_popup.lift()
        self._search_listbox.selection_clear(0, tk.END)
        self._search_listbox.selection_set(0)
        self._search_listbox.activate(0)

    def _handle_search_result_click(self) -> None:
        if not self._search_listbox:
            return
        selection = self._search_listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        event_obj = self._search_result_events[idx] if idx < len(self._search_result_events) else None
        if event_obj is not None:
            self.edit_event(event_obj)
        self._hide_search_popup()
        if self.search_entry:
            self.search_entry.focus_set()
            self.search_entry.icursor(tk.END)

    def _activate_first_search_result(self) -> None:
        if not self._search_listbox or not self._search_result_events:
            return
        if self._search_listbox.size() == 0:
            return
        self._search_listbox.selection_set(0)
        self._handle_search_result_click()

    def _hide_search_popup(self) -> None:
        if self._search_popup:
            self._search_popup.withdraw()
        if self._search_listbox:
            self._search_listbox.selection_clear(0, tk.END)

    def _maybe_hide_search_popup(self) -> None:
        widget = self.focus_get()
        if widget not in {self.search_entry, self._search_listbox}:
            self._hide_search_popup()

    def _clear_search_results(self, clear_text: bool = False) -> None:
        if clear_text and self.search_var is not None:
            self.search_var.set("")
        self._hide_search_popup()

    # ---------------------------------------------------------------- Refresh
    def refresh(self) -> None:
        self._load_production_calendars()
        if self.current_production_id is None:
            self.calendars = []
            self.visible_calendar_ids = set()
            self.events = []
            self.occurrences_by_day = defaultdict(list)
            self._populate_calendar()
            self._rebuild_calendar_filters()
            self._update_selected_day_label()
            self._populate_day_events()
            self._clear_search_results()
            return
        self._load_calendars()
        self._load_events()
        self._populate_calendar()
        self._rebuild_calendar_filters()
        self._update_selected_day_label()
        self._populate_day_events()
        self._clear_search_results()

    def _load_production_calendars(self) -> None:
        try:
            productions = self.db.get_production_calendars()
        except Exception:
            productions = []
        self.production_calendars = productions
        if self.current_production_id is not None and not any(
            pc.id == self.current_production_id for pc in productions
        ):
            self.current_production_id = None
        if productions and self.current_production_id is None:
            self.current_production_id = productions[0].id
        self._update_production_selector()

    def _load_calendars(self) -> None:
        self.calendars = self.db.get_calendars(production_calendar_id=self.current_production_id)
        self.visible_calendar_ids = {cal.id for cal in self.calendars if cal.is_visible}
        # ensure selected calendars exist
        if not self.visible_calendar_ids and self.calendars:
            self.visible_calendar_ids.add(self.calendars[0].id)

    def _update_production_selector(self) -> None:
        if not hasattr(self, "production_combo"):
            return
        names = [pc.name for pc in self.production_calendars]
        current_index = None
        for idx, pc in enumerate(self.production_calendars):
            if pc.id == self.current_production_id:
                current_index = idx
                break
        self._suspend_production_callback = True
        self.production_combo["values"] = names
        if current_index is not None:
            self.production_combo.current(current_index)
        elif names:
            self.production_combo.current(0)
            self.current_production_id = self.production_calendars[0].id
        else:
            self.production_combo.set("")
            self.current_production_id = None
        self._suspend_production_callback = False
        self._update_production_color_patch()

    def _update_production_color_patch(self) -> None:
        if not hasattr(self, "production_color_patch"):
            return
        current = self._current_production()
        color = current.color if current else self.theme.border
        self.production_color_patch.delete("all")
        self.production_color_patch.create_rectangle(0, 0, 20, 20, fill=color, outline="")

    def _current_production(self) -> Optional[ProductionCalendar]:
        return next((pc for pc in self.production_calendars if pc.id == self.current_production_id), None)

    def _set_modal_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for widget in self._interactive_buttons:
            widget.configure(state=state)
        combo_state = "readonly" if enabled else "disabled"
        for combo in self._interactive_comboboxes:
            combo.configure(state=combo_state)
        select_mode = "browse" if enabled else "none"
        for tree in self._interactive_treeviews:
            tree.configure(selectmode=select_mode)
        for check in self._calendar_checkbuttons:
            check.configure(state="normal" if enabled else "disabled")
        for btn in self._calendar_edit_buttons:
            btn.configure(state=state)

    def _on_production_selected(self, event: tk.Event) -> None:
        if self._suspend_production_callback:
            return
        name = self.production_combo.get()
        selected = next((pc for pc in self.production_calendars if pc.name == name), None)
        if selected and selected.id != self.current_production_id:
            self.current_production_id = selected.id
            self.refresh()

    def add_production_calendar(self) -> None:
        self._open_production_calendar_panel(None, allow_delete=False)

    def edit_current_production_calendar(self) -> None:
        current = self._current_production()
        if current is None:
            messagebox.showinfo("Production Calendars", "Create a production calendar first.")
            return
        allow_delete = len(self.production_calendars) > 1
        self._open_production_calendar_panel(current, allow_delete=allow_delete)

    def _open_production_calendar_panel(
        self,
        production: Optional[ProductionCalendar],
        allow_delete: bool,
    ) -> None:
        def builder(parent: tk.Frame) -> tk.Frame:
            return ProductionCalendarPanel(
                parent,
                production=production,
                allow_delete=allow_delete,
                on_submit=lambda payload: self._handle_production_submit(production, payload),
                on_delete=lambda: self._handle_production_delete(production),
                on_cancel=self._close_modal,
            )

        self._open_modal(builder)

    def _handle_production_submit(
        self,
        production: Optional[ProductionCalendar],
        payload: dict[str, object],
    ) -> None:
        try:
            if production is None:
                new_id = self.db.create_production_calendar(
                    name=str(payload["name"]),
                    color=str(payload["color"]),
                )
                self.current_production_id = new_id
            else:
                self.db.update_production_calendar(
                    production.id,
                    name=str(payload["name"]),
                    color=str(payload["color"]),
                )
        except Exception as exc:
            messagebox.showerror("Error", f"Could not save production calendar: {exc}", parent=self)
            return
        self._close_modal()
        self.refresh()

    def _handle_production_delete(self, production: Optional[ProductionCalendar]) -> None:
        if production is None:
            self._close_modal()
            return
        try:
            self.db.delete_production_calendar(production.id)
        except ValueError as exc:
            warning_text = (
                "Please remove or reassign calendars before deleting this production calendar.\n\n"
                "This action cannot be reversed. Delete this production calendar and all of its contents?"
            )
            proceed = messagebox.askyesno("Delete Production Calendar", warning_text, icon="warning", parent=self)
            if not proceed:
                return
            confirm = messagebox.askyesno(
                "Confirm Permanent Delete",
                "Are you sure? This will permanently delete the production calendar plus every calendar and event inside it.",
                icon="warning",
                parent=self,
            )
            if not confirm:
                return
            try:
                self.db.delete_production_calendar(production.id, force=True)
            except Exception as final_exc:
                messagebox.showerror(
                    "Error",
                    f"Could not delete production calendar: {final_exc}",
                    parent=self,
                )
                return
            else:
                messagebox.showinfo(
                    "Production Calendar Deleted",
                    "The production calendar and all of its calendars/events were deleted.",
                    parent=self,
                )
                self._close_modal()
                self.current_production_id = None
                self.refresh()
                return
        except Exception as exc:
            messagebox.showerror("Error", f"Could not delete production calendar: {exc}", parent=self)
            return
        self._close_modal()
        self.current_production_id = None
        self.refresh()

    def export_current_production_calendar(self) -> None:
        production = self._current_production()
        if production is None:
            messagebox.showinfo("Export Production Calendar", "Select a production calendar first.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Export Production Calendar",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile=f"{production.name.replace(' ', '_')}.json",
        )
        if not path:
            return
        try:
            payload = self.db.export_production_calendar(production.id)
        except Exception as exc:
            messagebox.showerror("Export Failed", str(exc), parent=self)
            return
        try:
            Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
            messagebox.showinfo("Export Complete", f"Exported '{production.name}'.", parent=self)
        except Exception as exc:
            messagebox.showerror("Export Failed", str(exc), parent=self)

    def import_production_calendar(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Import Production Calendar",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            content = Path(path).read_text(encoding="utf-8")
            payload = json.loads(content)
        except Exception as exc:
            messagebox.showerror("Import Failed", f"Could not read file: {exc}", parent=self)
            return
        try:
            new_id = self.db.import_production_calendar(payload)
        except Exception as exc:
            messagebox.showerror("Import Failed", str(exc), parent=self)
            return
        self.current_production_id = new_id
        self.refresh()
        messagebox.showinfo("Import Complete", "Production calendar imported successfully.", parent=self)

    def _open_modal(self, panel_builder: Callable[[tk.Frame], tk.Frame]) -> None:
        self._close_modal()
        overlay = tk.Frame(self, bg=self.bg_color)
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        overlay.lift()
        panel = panel_builder(overlay)
        self._modal_overlay = overlay
        self._modal_panel = panel
        self._set_modal_enabled(False)

    def _close_modal(self) -> None:
        if self._modal_panel is not None:
            self._modal_panel.destroy()
            self._modal_panel = None
        if self._modal_overlay is not None:
            self._modal_overlay.destroy()
            self._modal_overlay = None
        self._set_modal_enabled(True)

    def _load_events(self) -> None:
        if not self.calendars:
            self.events = []
            return
        events = self.db.get_events(calendar_ids=self.visible_calendar_ids)
        self.events = events

    def _populate_calendar(self) -> None:
        for cell in self.day_cells:
            cell.date = None
            cell.day_label.configure(text="", fg=self.text_color, bg=self.cell_bg)
            for widget in cell.events_container.winfo_children():
                widget.destroy()
            cell.frame.configure(bg=self.cell_bg)
            cell.events_container.configure(bg=self.cell_bg)

        month_start = self.current_month
        cal_obj = cal.Calendar(firstweekday=6)
        weeks = cal_obj.monthdatescalendar(month_start.year, month_start.month)
        if self.month_label is not None:
            self.month_label.configure(text=month_start.strftime("%B %Y"))
        self.occurrences_by_day = defaultdict(list)

        if self.events:
            start_dt = datetime.combine(weeks[0][0], datetime.min.time())
            end_dt = datetime.combine(weeks[-1][-1], datetime.max.time())
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
            for occs in self.occurrences_by_day.values():
                occs.sort(key=lambda item: item.occurrence)

        for idx, day in enumerate(d for week in weeks for d in week):
            if idx >= len(self.day_cells):
                break
            cell = self.day_cells[idx]
            cell.date = day
            in_month = day.month == month_start.month
            fg_color = self.text_color if in_month else self.outside_month_color
            bg_color = self.cell_bg
            cell.day_label.configure(text=str(day.day), fg=fg_color, bg=bg_color)
            cell.frame.configure(bg=bg_color)
            cell.events_container.configure(bg=bg_color)

            for widget in cell.events_container.winfo_children():
                widget.destroy()

            occurrences = self.occurrences_by_day.get(day, [])
            for occ_entry in occurrences[:4]:
                occurrence = occ_entry.occurrence
                event = occ_entry.event
                override = occ_entry.override
                label_bg = (
                    override.calendar_color
                    if override and override.calendar_color
                    else event.calendar_color
                    or "#607D8B"
                )
                fg = utils.ideal_text_color(label_bg)
                display_title = override.title if override and override.title else event.title
                time_str = occurrence.strftime("%H:%M")
                text = f"{time_str} {display_title}" if occurrence.time() != datetime.min.time() else display_title
                if self._is_customized_occurrence(occ_entry):
                    text += f" {CUSTOMIZED_OCCURRENCE_MARK}"
                display_text = shorten(text, width=32, placeholder="...")
                ev_label = tk.Label(
                    cell.events_container,
                    text=display_text,
                    anchor="w",
                    bg=label_bg,
                    fg=fg,
                    font=("Segoe UI", 9, "bold"),
                    padx=4,
                    pady=1,
                )
                ev_label.pack(fill=tk.X, pady=1)
                ev_label.bind("<Button-1>", lambda e, date_obj=day: self.select_day(date_obj))
                ev_label.bind("<Double-1>", lambda e, entry=occ_entry: self._open_occurrence_customizer(entry))

            if len(occurrences) > 4:
                more_label = tk.Label(
                    cell.events_container,
                    text=f"+{len(occurrences) - 4}",
                    anchor="w",
                    bg=bg_color,
                    fg=self.secondary_text_color,
                    font=("Segoe UI", 9, "italic"),
                )
                more_label.pack(fill=tk.X, pady=1)
                more_label.bind("<Button-1>", lambda e, date_obj=day: self.select_day(date_obj))

        self._highlight_selected_day()

    def _rebuild_calendar_filters(self) -> None:
        if self.calendars_frame is None:
            return
        for child in self.calendars_frame.winfo_children():
            child.destroy()
        self.calendar_vars.clear()
        self._calendar_checkbuttons = []
        self._calendar_edit_buttons = []
        for idx, calendar_model in enumerate(self.calendars):
            color_patch = tk.Canvas(
                self.calendars_frame,
                width=20,
                height=20,
                bg=self.bg_color,
                highlightthickness=0,
            )
            color_patch.grid(row=idx, column=0, padx=(0, 6), pady=2)
            color_patch.create_rectangle(0, 0, 20, 20, fill=calendar_model.color, outline="")

            var = tk.BooleanVar(value=calendar_model.id in self.visible_calendar_ids)
            check = ttk.Checkbutton(
                self.calendars_frame,
                text=calendar_model.name,
                variable=var,
                command=lambda cid=calendar_model.id, v=var: self.toggle_calendar(cid, v.get()),
            )
            check.grid(row=idx, column=1, sticky="w", pady=2)
            self._calendar_checkbuttons.append(check)

            edit_btn = ttk.Button(
                self.calendars_frame,
                text="Edit",
                width=6,
                command=lambda cal=calendar_model: self.edit_calendar(cal),
            )
            edit_btn.grid(row=idx, column=2, padx=(6, 0), pady=2, sticky="e")
            self._calendar_edit_buttons.append(edit_btn)

            self.calendar_vars[calendar_model.id] = var

    def _populate_day_events(self) -> None:
        tree = getattr(self, "day_events_tree", None)
        if tree is None:
            return
        for item in tree.get_children():
            tree.delete(item)
        day = self.selected_day
        occurrences = self.occurrences_by_day.get(day, [])
        self._day_occurrence_index = {}
        for occ_entry in occurrences:
            time_str = occ_entry.occurrence.strftime("%I:%M %p").lstrip("0")
            iid = f"{occ_entry.event.id}:{occ_entry.occurrence.isoformat()}"
            title_text = (
                occ_entry.override.title
                if occ_entry.override and occ_entry.override.title
                else occ_entry.event.title
            )
            if self._is_customized_occurrence(occ_entry):
                title_text = f"{title_text} {CUSTOMIZED_OCCURRENCE_MARK}"
            tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(time_str, title_text, occ_entry.event.calendar_name),
            )
            self._day_occurrence_index[iid] = occ_entry

    def _is_customized_occurrence(self, occ_entry: DayOccurrence) -> bool:
        override = occ_entry.override
        if override is None:
            return False
        fields = (
            override.title,
            override.description,
            override.calendar_color,
            override.note,
        )
        for value in fields:
            if isinstance(value, str):
                if value.strip():
                    return True
            elif value:
                return True
        return False

    def _highlight_selected_day(self) -> None:
        if not self.day_cells:
            return
        for cell in self.day_cells:
            bg = self.cell_selected_bg if cell.date == self.selected_day else self.cell_bg
            fg = self.text_color if cell.date and cell.date.month == self.current_month.month else self.outside_month_color
            cell.frame.configure(bg=bg)
            cell.day_label.configure(bg=bg, fg=fg)
            cell.events_container.configure(bg=bg)
            for child in cell.events_container.winfo_children():
                if isinstance(child, tk.Label) and child.cget("text").startswith("+"):
                    child.configure(bg=bg)

    def _update_selected_day_label(self) -> None:
        label = getattr(self, "day_value_label", None)
        if label is None:
            return
        label.configure(text=self.selected_day.strftime("%A, %B %d, %Y"))

    # ---------------------------------------------------------------- Events
    def _on_cell_click(self, index: int) -> None:
        if index >= len(self.day_cells):
            return
        cell = self.day_cells[index]
        if cell.date:
            self.select_day(cell.date)

    def select_day(self, day: date) -> None:
        self.selected_day = day
        self._highlight_selected_day()
        self._update_selected_day_label()
        self._populate_day_events()

    def toggle_calendar(self, calendar_id: int, visible: bool) -> None:
        if visible:
            self.visible_calendar_ids.add(calendar_id)
        else:
            self.visible_calendar_ids.discard(calendar_id)
        try:
            self.db.update_calendar(calendar_id, is_visible=visible)
        except Exception:
            pass
        self.refresh()

    def go_to_previous_month(self) -> None:
        prev_month = utils.add_months(datetime.combine(self.current_month, datetime.min.time()), -1).date()
        self.current_month = prev_month.replace(day=1)
        if self.selected_day.month != self.current_month.month:
            self.selected_day = self.current_month
        self.refresh()

    def go_to_next_month(self) -> None:
        next_month = utils.add_months(datetime.combine(self.current_month, datetime.min.time()), 1).date()
        self.current_month = next_month.replace(day=1)
        if self.selected_day.month != self.current_month.month:
            self.selected_day = self.current_month
        self.refresh()

    def go_to_today(self) -> None:
        today = datetime.now().date()
        self.current_month = today.replace(day=1)
        self.selected_day = today
        self.refresh()

    def open_recap_dialog(self) -> None:
        production = self._current_production()
        if production is None:
            messagebox.showinfo("Recap", "Select a production calendar first.")
            return
        if not self.calendars:
            messagebox.showinfo("Recap", "Add at least one calendar to this production calendar to generate recaps.")
            return
        default_start = datetime.combine(self.selected_day, datetime.min.time())
        default_end = default_start + timedelta(hours=23, minutes=59)
        def builder(parent: tk.Frame) -> tk.Frame:
            return RecapRangePanel(
                parent,
                default_start=default_start,
                default_end=default_end,
                calendars=self.calendars,
                selected_day=self.selected_day,
                on_generate=lambda start, end, calendar_ids: self._handle_recap_range(
                    start,
                    end,
                    production,
                    calendar_ids,
                ),
                on_cancel=self._close_modal,
            )
        self._open_modal(builder)

    def _handle_recap_range(
        self,
        start: datetime,
        end: datetime,
        production: ProductionCalendar,
        calendar_ids: Optional[List[int]],
    ) -> None:
        self._close_modal()
        self._show_recap_report(start, end, production, calendar_ids)

    def _show_recap_report(
        self,
        start: datetime,
        end: datetime,
        production: ProductionCalendar,
        calendar_ids: Optional[List[int]],
    ) -> None:
        resolved_calendar_ids = (
            [cal.id for cal in self.calendars] if calendar_ids is None else calendar_ids
        )
        if not resolved_calendar_ids:
            messagebox.showinfo("Recap", "Select at least one calendar to generate a recap.")
            return
        try:
            events = self.db.get_events(calendar_ids=resolved_calendar_ids)
        except Exception as exc:
            messagebox.showerror("Recap", f"Could not load events: {exc}")
            return
        entries: List[Tuple[datetime, datetime, Event]] = []
        for event in events:
            for occurrence in event.occurrences_between(start, end):
                end_time = occurrence + timedelta(minutes=event.duration_minutes)
                entries.append((occurrence, end_time, event))
        entries.sort(
            key=lambda item: (
                item[0],
                item[2].calendar_name.lower(),
                item[2].title.lower(),
            )
        )
        report_text = self._format_recap_report(entries, production, start, end)
        def builder(parent: tk.Frame) -> tk.Frame:
            return RecapReportPanel(
                parent,
                production_name=production.name,
                report_text=report_text,
                on_close=self._close_modal,
            )
        self._open_modal(builder)

    def _format_recap_report(
        self,
        entries: List[Tuple[datetime, datetime, Event]],
        production: ProductionCalendar,
        start: datetime,
        end: datetime,
    ) -> str:
        start_label = start.strftime("%Y-%m-%d %I:%M %p").lstrip("0")
        end_label = end.strftime("%Y-%m-%d %I:%M %p").lstrip("0")
        lines: List[str] = [
            f"Production Calendar: {production.name}",
            f"Range: {start_label} to {end_label}",
            f"Total items: {len(entries)}",
            "",
        ]
        if not entries:
            lines.append("No scheduled items in this range.")
            return "\n".join(lines)
        for index, (occurrence, end_time, event) in enumerate(entries, start=1):
            start_str = occurrence.strftime("%Y-%m-%d %I:%M %p").lstrip("0")
            end_str = end_time.strftime("%Y-%m-%d %I:%M %p").lstrip("0")
            lines.append(f"{index}. {start_str} - {end_str} | {event.calendar_name} | {event.title}")
            description = (event.description or "").strip()
            if description:
                for desc_line in description.splitlines():
                    lines.append(f"   {desc_line}")
            lines.append("")
        if lines[-1] == "":
            lines.pop()
        return "\n".join(lines)

    def add_event_for_selected_day(self) -> None:
        if not self.calendars:
            messagebox.showinfo("No Calendars", "Please add a calendar first.")
            return
        self._open_event_editor(default_date=self.selected_day)

    def edit_selected_event(self) -> None:
        occ_entry = self._get_selected_occurrence()
        if occ_entry is None:
            return
        self.edit_event(occ_entry.event)

    def customize_selected_occurrence(self) -> None:
        occ_entry = self._get_selected_occurrence()
        if occ_entry is None:
            messagebox.showinfo("Customize Event", "Select an event first.", parent=self)
            return
        self._open_occurrence_customizer(occ_entry)

    def _open_occurrence_customizer(self, occ_entry: DayOccurrence) -> None:
        occurrence_date = occ_entry.occurrence.date()

        def builder(parent: tk.Frame) -> tk.Frame:
            override = self.db.get_event_override(occ_entry.event.id, occurrence_date)
            return EventOccurrencePanel(
                parent,
                event=occ_entry.event,
                occurrence=occ_entry.occurrence,
                override=override,
                on_submit=lambda payload: self._handle_occurrence_override_submit(
                    occ_entry.event, occurrence_date, payload
                ),
                on_clear=lambda: self._handle_occurrence_override_clear(
                    occ_entry.event, occurrence_date
                ),
                on_cancel=self._close_modal,
            )

        self._open_modal(builder)

    def _handle_occurrence_override_submit(
        self,
        event: Event,
        occurrence_date: date,
        payload: dict[str, Optional[str]],
    ) -> None:
        try:
            self.db.upsert_event_override(
                event_id=event.id,
                occurrence_date=occurrence_date,
                title=payload.get("title") or None,
                description=payload.get("description") or None,
                calendar_color=payload.get("color") or None,
                note=payload.get("note") or None,
            )
        except Exception as exc:
            messagebox.showerror("Customize Event", f"Could not save customization: {exc}", parent=self)
            return
        self._close_modal()
        self.refresh()
        self.select_day(occurrence_date)

    def _handle_occurrence_override_clear(self, event: Event, occurrence_date: date) -> None:
        try:
            self.db.delete_event_override(event.id, occurrence_date)
        except Exception as exc:
            messagebox.showerror("Customize Event", f"Could not clear customization: {exc}", parent=self)
            return
        self._close_modal()
        self.refresh()
        self.select_day(occurrence_date)

    def edit_event(self, event: Event) -> None:
        self._open_event_editor(event=event)

    def _open_event_editor(self, *, event: Optional[Event] = None, default_date: Optional[date] = None) -> None:
        if not self.calendars:
            messagebox.showinfo("No Calendars", "Please add a calendar first.")
            return
        def builder(parent: tk.Frame) -> tk.Frame:
            return EventEditorPanel(
                parent,
                calendars=self.calendars,
                event=event,
                default_date=default_date or self.selected_day,
                on_submit=lambda payload: self._handle_event_submission(event, payload),
                on_cancel=self._close_modal,
            )
        self._open_modal(builder)

    def _handle_event_submission(self, event: Optional[Event], payload: dict[str, object]) -> None:
        try:
            if event is None:
                self.db.create_event(**payload)
                reselect_id = None
            else:
                self.db.update_event(event.id, **payload)
                reselect_id = event.id
        except Exception as exc:
            messagebox.showerror("Error", f"Could not save event: {exc}", parent=self)
            return
        self._close_modal()
        self.refresh()
        if reselect_id is not None:
            self._select_event_in_day(reselect_id)

    def _select_event_in_day(self, event_id: int) -> None:
        tree = getattr(self, "day_events_tree", None)
        if tree is None:
            return
        for iid, occ_entry in getattr(self, "_day_occurrence_index", {}).items():
            if occ_entry.event.id == event_id:
                tree.selection_set(iid)
                tree.see(iid)
                break

    def delete_selected_event(self) -> None:
        occ_entry = self._get_selected_occurrence()
        if occ_entry is None:
            return
        if messagebox.askyesno("Delete Event", f"Delete '{occ_entry.event.title}' from all future occurrences?"):
            self.db.delete_event(occ_entry.event.id)
            self.refresh()
            self.select_day(self.selected_day)

    def _get_selected_occurrence(self) -> Optional[DayOccurrence]:
        tree = getattr(self, "day_events_tree", None)
        if tree is None:
            return None
        selection = tree.selection()
        if not selection:
            return None
        iid = selection[0]
        return self._day_occurrence_index.get(iid)

    def add_calendar(self) -> None:
        production = self._current_production()
        if production is None or self.current_production_id is None:
            messagebox.showinfo("Production Calendars", "Select a production calendar first.")
            return
        self._open_calendar_editor(cal=None, production=production)

    def edit_calendar(self, calendar_model: Calendar) -> None:
        allow_delete = len(self.calendars) > 1
        production = self._current_production()
        if production is None:
            messagebox.showinfo("Production Calendars", "Select a production calendar first.")
            return
        self._open_calendar_editor(cal=calendar_model, production=production, allow_delete=allow_delete)

    def _open_calendar_editor(
        self,
        *,
        cal: Optional[Calendar],
        production: ProductionCalendar,
        allow_delete: bool = False,
    ) -> None:
        def builder(parent: tk.Frame) -> tk.Frame:
            return CalendarEditorPanel(
                parent,
                calendar=cal,
                production_name=production.name,
                allow_delete=allow_delete,
                on_submit=lambda payload: self._handle_calendar_submit(cal, payload),
                on_delete=lambda: self._handle_calendar_delete(cal),
                on_cancel=self._close_modal,
            )

        self._open_modal(builder)

    def _handle_calendar_submit(self, cal: Optional[Calendar], payload: dict[str, object]) -> None:
        try:
            if cal is None:
                if self.current_production_id is None:
                    raise ValueError("No production calendar selected.")
                self.db.create_calendar(
                    name=str(payload["name"]),
                    color=str(payload["color"]),
                    production_calendar_id=self.current_production_id,
                    is_visible=True,
                )
            else:
                self.db.update_calendar(
                    cal.id,
                    name=str(payload["name"]),
                    color=str(payload["color"]),
                )
        except Exception as exc:
            messagebox.showerror("Error", f"Could not save calendar: {exc}", parent=self)
            return
        self._close_modal()
        self.refresh()

    def _handle_calendar_delete(self, cal: Optional[Calendar]) -> None:
        if cal is None:
            self._close_modal()
            return
        if len(self.calendars) <= 1:
            messagebox.showinfo("Calendar", "At least one calendar must remain.")
            return
        if not messagebox.askyesno("Delete Calendar", "Delete this calendar and its events?", parent=self):
            return
        try:
            self.visible_calendar_ids.discard(cal.id)
            self.db.delete_calendar(cal.id)
        except Exception as exc:
            messagebox.showerror("Error", f"Could not delete calendar: {exc}", parent=self)
            return
        self._close_modal()
        self.refresh()


class CalendarEditorPanel(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        calendar: Optional[Calendar],
        production_name: str,
        allow_delete: bool,
        on_submit: Callable[[dict[str, object]], None],
        on_delete: Callable[[], None],
        on_cancel: Callable[[], None],
    ) -> None:
        super().__init__(parent, bg="#1d1e2c", bd=1, relief="ridge")
        self._on_submit = on_submit
        self._on_delete = on_delete
        self._on_cancel = on_cancel
        self.place(relx=0.5, rely=0.5, anchor="center")

        default_name = calendar.name if calendar else ""
        default_color = calendar.color if calendar else "#4F75FF"

        container = ttk.Frame(self, padding=16)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)

        header = ttk.Frame(container)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(
            header,
            text="Edit Calendar" if calendar else "New Calendar",
            style="SidebarHeading.TLabel",
        ).pack(side=tk.LEFT)
        ttk.Button(header, text="Close", command=self._cancel).pack(side=tk.RIGHT)

        ttk.Label(container, text="Name").grid(row=1, column=0, sticky="w")
        self.name_var = tk.StringVar(value=default_name)
        name_entry = ttk.Entry(container, textvariable=self.name_var, width=30)
        name_entry.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        name_entry.focus_set()

        ttk.Label(
            container,
            text=f"Production Calendar: {production_name}",
            foreground="#9FA8DA",
        ).grid(row=3, column=0, sticky="w", pady=(0, 12))

        ttk.Label(container, text="Color").grid(row=4, column=0, sticky="w")
        color_row = ttk.Frame(container)
        color_row.grid(row=5, column=0, sticky="w")
        self.color_var = tk.StringVar(value=default_color)
        self.color_preview = tk.Label(color_row, width=4, height=2, bg=default_color, relief="groove")
        self.color_preview.pack(side=tk.LEFT)
        ttk.Button(color_row, text="Choose...", command=self._choose_color).pack(side=tk.LEFT, padx=(8, 0))

        button_row = ttk.Frame(container)
        button_row.grid(row=6, column=0, sticky="e", pady=(18, 0))
        ttk.Button(button_row, text="Cancel", command=self._cancel).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(button_row, text="Save", command=self._save).pack(side=tk.RIGHT)
        if allow_delete and calendar is not None:
            ttk.Button(button_row, text="Delete", command=self._delete).pack(side=tk.LEFT)

    def _choose_color(self) -> None:
        current = self.color_var.get() or "#4F75FF"
        color = colorchooser.askcolor(initialcolor=current)[1]
        if color:
            self.color_var.set(color)
            self.color_preview.configure(bg=color)

    def _save(self) -> None:
        name = self.name_var.get().strip()
        if not name:
            messagebox.showerror("Invalid Name", "Calendar name is required.", parent=self)
            return
        color = self.color_var.get() or "#4F75FF"
        self._on_submit({"name": name, "color": color})

    def _delete(self) -> None:
        if messagebox.askyesno("Delete Calendar", "Delete this calendar and its events?", parent=self):
            self._on_delete()

    def _cancel(self) -> None:
        self._on_cancel()


class ProductionCalendarPanel(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        production: Optional[ProductionCalendar],
        allow_delete: bool,
        on_submit: Callable[[dict[str, object]], None],
        on_delete: Callable[[], None],
        on_cancel: Callable[[], None],
    ) -> None:
        super().__init__(parent, bg="#1d1e2c", bd=1, relief="ridge")
        self._on_submit = on_submit
        self._on_delete = on_delete
        self._on_cancel = on_cancel
        self.place(relx=0.5, rely=0.5, anchor="center")

        default_name = production.name if production else ""
        default_color = production.color if production else "#4F75FF"

        container = ttk.Frame(self, padding=16)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)

        header = ttk.Frame(container)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(
            header,
            text="Edit Production Calendar" if production else "New Production Calendar",
            style="SidebarHeading.TLabel",
        ).pack(side=tk.LEFT)
        ttk.Button(header, text="Close", command=self._cancel).pack(side=tk.RIGHT)

        ttk.Label(container, text="Name").grid(row=1, column=0, sticky="w")
        self.name_var = tk.StringVar(value=default_name)
        name_entry = ttk.Entry(container, textvariable=self.name_var, width=32)
        name_entry.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        name_entry.focus_set()

        ttk.Label(container, text="Accent Color").grid(row=3, column=0, sticky="w")
        color_row = ttk.Frame(container)
        color_row.grid(row=4, column=0, sticky="w")
        self.color_var = tk.StringVar(value=default_color)
        self.color_preview = tk.Label(color_row, width=4, height=2, bg=default_color, relief="groove")
        self.color_preview.pack(side=tk.LEFT)
        ttk.Button(color_row, text="Choose...", command=self._choose_color).pack(side=tk.LEFT, padx=(8, 0))

        button_row = ttk.Frame(container)
        button_row.grid(row=5, column=0, sticky="e", pady=(18, 0))
        ttk.Button(button_row, text="Cancel", command=self._cancel).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(button_row, text="Save", command=self._save).pack(side=tk.RIGHT)
        if allow_delete and production is not None:
            ttk.Button(button_row, text="Delete", command=self._delete).pack(side=tk.LEFT)

    def _choose_color(self) -> None:
        current = self.color_var.get() or "#4F75FF"
        color = colorchooser.askcolor(initialcolor=current)[1]
        if color:
            self.color_var.set(color)
            self.color_preview.configure(bg=color)

    def _save(self) -> None:
        name = self.name_var.get().strip()
        if not name:
            messagebox.showerror("Invalid Name", "Name is required.", parent=self)
            return
        color = self.color_var.get() or "#4F75FF"
        self._on_submit({"name": name, "color": color})

    def _delete(self) -> None:
        if messagebox.askyesno("Delete Production Calendar", "Delete this production calendar?", parent=self):
            self._on_delete()

    def _cancel(self) -> None:
        self._on_cancel()


class EventOccurrencePanel(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        event: Event,
        occurrence: datetime,
        override: Optional[EventOverride],
        on_submit: Callable[[dict[str, Optional[str]]], None],
        on_clear: Callable[[], None],
        on_cancel: Callable[[], None],
    ) -> None:
        super().__init__(parent, bg="#1d1e2c", bd=1, relief="ridge")
        self._event = event
        self._occurrence = occurrence
        self._override = override
        self._on_submit = on_submit
        self._on_clear = on_clear
        self._on_cancel = on_cancel
        self._color_value = override.calendar_color if override else ""

        self.place(relx=0.5, rely=0.5, anchor="center")

        container = ttk.Frame(self, padding=16)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(1, weight=1)

        ttk.Label(
            container,
            text="Customize Occurrence",
            style="SidebarHeading.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        ttk.Label(container, text="Event").grid(row=1, column=0, sticky="w", pady=(12, 0))
        ttk.Label(
            container,
            text=f"{event.title} — {occurrence.strftime('%A, %B %d, %Y %I:%M %p').lstrip('0')}",
        ).grid(row=1, column=1, sticky="w", pady=(12, 0))

        ttk.Label(container, text="Custom Title").grid(row=2, column=0, sticky="w", pady=(12, 0))
        self.title_var = tk.StringVar(value=(override.title if override and override.title else ""))
        ttk.Entry(container, textvariable=self.title_var).grid(row=2, column=1, sticky="ew", pady=(12, 0))

        ttk.Label(container, text="Custom Color").grid(row=3, column=0, sticky="w", pady=(12, 0))
        color_row = ttk.Frame(container)
        color_row.grid(row=3, column=1, sticky="w", pady=(12, 0))
        self.color_preview = tk.Label(
            color_row,
            width=4,
            height=2,
            relief="groove",
            bg=self._color_value or event.calendar_color or "#607D8B",
        )
        self.color_preview.pack(side=tk.LEFT)
        ttk.Button(color_row, text="Choose...", command=self._pick_color).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(color_row, text="Clear", command=self._clear_color).pack(side=tk.LEFT, padx=(6, 0))

        ttk.Label(container, text="Notes").grid(row=4, column=0, sticky="nw", pady=(12, 0))
        self.note_text = tk.Text(container, height=5, wrap="word", bg="#ffffff", fg="#1c1d2b")
        if override and override.note:
            self.note_text.insert("1.0", override.note)
        self.note_text.grid(row=4, column=1, sticky="ew", pady=(12, 0))

        button_row = ttk.Frame(container)
        button_row.grid(row=5, column=0, columnspan=2, sticky="e", pady=(18, 0))
        ttk.Button(button_row, text="Cancel", command=self._on_cancel).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(button_row, text="Save", command=self._save).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(button_row, text="Clear Override", command=self._clear_override).pack(side=tk.LEFT)

    def _pick_color(self) -> None:
        initial = self._color_value or self._event.calendar_color or "#607D8B"
        color = colorchooser.askcolor(initialcolor=initial)[1]
        if color:
            self._color_value = color
            self.color_preview.configure(bg=color)

    def _clear_color(self) -> None:
        self._color_value = ""
        self.color_preview.configure(bg=self._event.calendar_color or "#607D8B")

    def _save(self) -> None:
        title = self.title_var.get().strip()
        note = self.note_text.get("1.0", tk.END).strip()
        payload = {
            "title": title or None,
            "description": None,
            "color": self._color_value or None,
            "note": note or None,
        }
        if not payload["title"] and not payload["note"] and payload["color"] is None:
            self._on_clear()
        else:
            self._on_submit(payload)

    def _clear_override(self) -> None:
        if messagebox.askyesno("Clear Customization", "Remove the customizations for this occurrence?", parent=self):
            self._on_clear()

class EventEditorPanel(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        calendars: Iterable[Calendar],
        event: Optional[Event],
        default_date: date,
        on_submit: Callable[[dict[str, object]], None],
        on_cancel: Callable[[], None],
    ) -> None:
        super().__init__(parent, bg="#1d1e2c", bd=1, relief="ridge")
        self.calendars = list(calendars)
        self.event = event
        self._on_submit = on_submit
        self._on_cancel = on_cancel
        self.place(relx=0.5, rely=0.5, anchor="center")

        container = ttk.Frame(self, padding=16)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.columnconfigure(1, weight=1)

        header = ttk.Frame(container)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        ttk.Label(
            header,
            text="Edit Event" if event else "New Event",
            style="SidebarHeading.TLabel",
        ).pack(side=tk.LEFT)
        ttk.Button(header, text="Close", command=self._cancel).pack(side=tk.RIGHT)

        ttk.Label(container, text="Title").grid(row=1, column=0, sticky="w")
        self.title_var = tk.StringVar(value=event.title if event else "")
        title_entry = ttk.Entry(container, textvariable=self.title_var, width=40)
        title_entry.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        title_entry.focus_set()

        ttk.Label(container, text="Calendar").grid(row=3, column=0, sticky="w")
        self.calendar_var = tk.StringVar()
        calendar_names = [cal.name for cal in self.calendars]
        self.calendar_combo = ttk.Combobox(container, values=calendar_names, textvariable=self.calendar_var, state="readonly")
        if calendar_names:
            default_index = 0
            if event:
                for idx, cal in enumerate(self.calendars):
                    if cal.id == event.calendar_id:
                        default_index = idx
                        break
            self.calendar_combo.current(default_index)
            self.calendar_var.set(calendar_names[default_index])
        self.calendar_combo.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        ttk.Label(container, text="Date (YYYY-MM-DD)").grid(row=5, column=0, sticky="w")
        self.date_var = tk.StringVar(value=(event.start_time.strftime("%Y-%m-%d") if event else default_date.strftime("%Y-%m-%d")))
        ttk.Entry(container, textvariable=self.date_var).grid(row=6, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(container, text="Time (HH:MM)").grid(row=5, column=1, sticky="w")
        default_time = event.start_time.strftime("%H:%M") if event else datetime.now().strftime("%H:%M")
        self.time_var = tk.StringVar(value=default_time)
        ttk.Entry(container, textvariable=self.time_var).grid(row=6, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(container, text="Duration (minutes)").grid(row=7, column=0, sticky="w")
        self.duration_var = tk.StringVar(value=str(event.duration_minutes if event else 60))
        ttk.Entry(container, textvariable=self.duration_var).grid(row=8, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(container, text="Reminder (minutes before)").grid(row=7, column=1, sticky="w")
        reminder_default = event.reminder_minutes_before if event and event.reminder_minutes_before is not None else 15
        self.reminder_var = tk.StringVar(value=str(reminder_default))
        ttk.Entry(container, textvariable=self.reminder_var).grid(row=8, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(container, text="Repeat").grid(row=9, column=0, sticky="w")
        self.repeat_var = tk.StringVar()
        labels = [label for label, _ in REPEAT_OPTIONS]
        repeat_combo = ttk.Combobox(container, values=labels, textvariable=self.repeat_var, state="readonly")
        repeat_label = next((label for label, value in REPEAT_OPTIONS if event and value == event.repeat), "None")
        self.repeat_var.set(repeat_label)
        repeat_combo.current(labels.index(repeat_label))
        repeat_combo.grid(row=10, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(container, text="Repeat every (interval)").grid(row=9, column=1, sticky="w")
        self.repeat_interval_var = tk.StringVar(value=str(event.repeat_interval if event else 1))
        ttk.Entry(container, textvariable=self.repeat_interval_var).grid(row=10, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(container, text="Repeat until (YYYY-MM-DD)").grid(row=11, column=0, sticky="w")
        repeat_until_value = (
            event.repeat_until.strftime("%Y-%m-%d") if event and event.repeat_until else ""
        )
        self.repeat_until_var = tk.StringVar(value=repeat_until_value)
        ttk.Entry(container, textvariable=self.repeat_until_var).grid(row=12, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(container, text="Description").grid(row=11, column=1, sticky="w")
        self.description_text = tk.Text(container, height=5, width=40)
        self.description_text.grid(row=12, column=1, sticky="ew", pady=(0, 8))
        if event:
            self.description_text.insert("1.0", event.description)

        button_row = ttk.Frame(container)
        button_row.grid(row=13, column=0, columnspan=2, sticky="e")
        ttk.Button(button_row, text="Cancel", command=self._cancel).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(button_row, text="Save", command=self._save).pack(side=tk.RIGHT)

    def _save(self) -> None:
        try:
            title = self.title_var.get().strip()
            if not title:
                raise ValueError("Title is required.")
            calendar_name = self.calendar_var.get()
            calendar_model = next((c for c in self.calendars if c.name == calendar_name), None)
            if calendar_model is None:
                raise ValueError("Pick a calendar.")
            start_date = datetime.strptime(self.date_var.get().strip(), "%Y-%m-%d").date()
            start_time = datetime.strptime(self.time_var.get().strip(), "%H:%M").time()
            start_datetime = datetime.combine(start_date, start_time)
            duration_minutes = max(1, int(self.duration_var.get().strip()))
            reminder_minutes_before = int(self.reminder_var.get().strip() or 0)
            repeat_label = self.repeat_var.get()
            repeat_value = next((value for label, value in REPEAT_OPTIONS if label == repeat_label), "none")
            repeat_interval = max(1, int(self.repeat_interval_var.get().strip() or 1))
            repeat_until_value = self.repeat_until_var.get().strip()
            repeat_until_date = datetime.strptime(repeat_until_value, "%Y-%m-%d").date() if repeat_until_value else None
            description = self.description_text.get("1.0", tk.END).strip()
        except ValueError as exc:
            messagebox.showerror("Invalid Data", str(exc), parent=self)
            return

        payload: dict[str, object] = {
            "calendar_id": calendar_model.id,
            "title": title,
            "description": description,
            "start_time": start_datetime,
            "duration_minutes": duration_minutes,
            "repeat": repeat_value,
            "repeat_interval": repeat_interval,
            "repeat_until": datetime.combine(repeat_until_date, datetime.min.time()) if repeat_until_date else None,
            "reminder_minutes_before": reminder_minutes_before,
        }
        self._on_submit(payload)

    def _cancel(self) -> None:
        self._on_cancel()


class RecapRangePanel(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        default_start: datetime,
        default_end: datetime,
        calendars: List[Calendar],
        selected_day: date,
        on_generate: Callable[[datetime, datetime, Optional[List[int]]], None],
        on_cancel: Callable[[], None],
    ) -> None:
        super().__init__(parent, bg="#1d1e2c", bd=1, relief="ridge")
        self._on_generate = on_generate
        self._on_cancel = on_cancel
        self._calendars = calendars
        self._anchor_day = selected_day
        self._calendar_vars: Dict[int, tk.BooleanVar] = {}
        self._calendar_menu: tk.Menu | None = None
        self.calendar_mode_var = tk.StringVar(value="all")
        self.calendar_button_var = tk.StringVar(value="All calendars selected")
        self.place(relx=0.5, rely=0.5, anchor="center")

        container = ttk.Frame(self, padding=16)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.columnconfigure(1, weight=1)

        header = ttk.Frame(container)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        ttk.Label(header, text="Generate Recap", style="SidebarHeading.TLabel").pack(side=tk.LEFT)
        ttk.Button(header, text="Close", command=self._cancel).pack(side=tk.RIGHT)

        ttk.Label(container, text="Start Date (YYYY-MM-DD)").grid(row=1, column=0, sticky="w")
        self.start_date_var = tk.StringVar(value=default_start.strftime("%Y-%m-%d"))
        ttk.Entry(container, textvariable=self.start_date_var, width=18).grid(row=2, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(container, text="Start Time (HH:MM)").grid(row=1, column=1, sticky="w")
        self.start_time_var = tk.StringVar(value=default_start.strftime("%H:%M"))
        ttk.Entry(container, textvariable=self.start_time_var, width=12).grid(row=2, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(container, text="End Date (YYYY-MM-DD)").grid(row=3, column=0, sticky="w")
        self.end_date_var = tk.StringVar(value=default_end.strftime("%Y-%m-%d"))
        ttk.Entry(container, textvariable=self.end_date_var, width=18).grid(row=4, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(container, text="End Time (HH:MM)").grid(row=3, column=1, sticky="w")
        self.end_time_var = tk.StringVar(value=default_end.strftime("%H:%M"))
        ttk.Entry(container, textvariable=self.end_time_var, width=12).grid(row=4, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(container, text="Calendars").grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))
        calendar_mode_frame = ttk.Frame(container)
        calendar_mode_frame.grid(row=6, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(
            calendar_mode_frame,
            text="All calendars",
            value="all",
            variable=self.calendar_mode_var,
            command=self._sync_calendar_controls,
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            calendar_mode_frame,
            text="Selected calendars",
            value="selected",
            variable=self.calendar_mode_var,
            command=self._sync_calendar_controls,
        ).pack(side=tk.LEFT, padx=(12, 0))

        self.calendar_dropdown_frame = ttk.Frame(container)
        self.calendar_dropdown_frame.grid(row=7, column=0, columnspan=2, sticky="w")
        self.calendar_dropdown_button = tk.Menubutton(
            self.calendar_dropdown_frame,
            textvariable=self.calendar_button_var,
            indicatoron=True,
            borderwidth=1,
            relief=tk.RAISED,
            width=32,
        )
        self.calendar_dropdown_button.pack(side=tk.LEFT, pady=(2, 0))
        self._build_calendar_menu()
        self.calendar_dropdown_frame.grid_remove()

        button_row = ttk.Frame(container)
        button_row.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(button_row, text="Overnight Recap", command=self._overnight_recap).pack(side=tk.LEFT)
        ttk.Button(button_row, text="Cancel", command=self._cancel).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(button_row, text="Generate", command=self._generate).pack(side=tk.RIGHT)

    def _generate(self) -> None:
        try:
            start_date = datetime.strptime(self.start_date_var.get().strip(), "%Y-%m-%d").date()
            start_time = datetime.strptime(self.start_time_var.get().strip(), "%H:%M").time()
            end_date = datetime.strptime(self.end_date_var.get().strip(), "%Y-%m-%d").date()
            end_time = datetime.strptime(self.end_time_var.get().strip(), "%H:%M").time()
        except ValueError:
            messagebox.showerror("Invalid Input", "Use YYYY-MM-DD for dates and HH:MM for times.", parent=self)
            return
        start = datetime.combine(start_date, start_time)
        end = datetime.combine(end_date, end_time)
        if end < start:
            messagebox.showerror("Invalid Range", "End must be after the start.", parent=self)
            return
        ok, calendar_ids = self._resolve_calendar_ids()
        if not ok:
            return
        self._on_generate(start, end, calendar_ids)

    def _cancel(self) -> None:
        self._on_cancel()

    def _build_calendar_menu(self) -> None:
        menu = tk.Menu(self.calendar_dropdown_button, tearoff=False)
        for calendar in self._calendars:
            var = tk.BooleanVar(value=True)
            self._calendar_vars[calendar.id] = var
            menu.add_checkbutton(label=calendar.name, variable=var, command=self._update_calendar_menu_label)
        self._calendar_menu = menu
        self.calendar_dropdown_button["menu"] = self._calendar_menu
        self._update_calendar_menu_label()

    def _update_calendar_menu_label(self) -> None:
        selected_ids = [cal_id for cal_id, var in self._calendar_vars.items() if var.get()]
        if not self._calendar_vars:
            label = "No calendars available"
        elif len(selected_ids) == len(self._calendar_vars):
            label = "All calendars selected"
        elif not selected_ids:
            label = "Select calendars"
        else:
            selected_names = [cal.name for cal in self._calendars if cal.id in selected_ids]
            if len(selected_names) <= 2:
                label = ", ".join(selected_names)
            else:
                label = f"{len(selected_names)} calendars selected"
        self.calendar_button_var.set(label)

    def _sync_calendar_controls(self) -> None:
        if self.calendar_mode_var.get() == "selected":
            self.calendar_dropdown_frame.grid()
        else:
            self.calendar_dropdown_frame.grid_remove()

    def _resolve_calendar_ids(self) -> Tuple[bool, Optional[List[int]]]:
        if self.calendar_mode_var.get() == "all":
            return True, None
        selected_ids = [cal_id for cal_id, var in self._calendar_vars.items() if var.get()]
        if not selected_ids:
            messagebox.showerror("Recap", "Select at least one calendar.", parent=self)
            return False, None
        return True, selected_ids

    def _overnight_recap(self) -> None:
        self._apply_overnight_preset()
        self._generate()

    def _apply_overnight_preset(self) -> None:
        anchor = self._anchor_day
        start_date = anchor - timedelta(days=1)
        self.start_date_var.set(start_date.strftime("%Y-%m-%d"))
        self.start_time_var.set("17:00")
        self.end_date_var.set(anchor.strftime("%Y-%m-%d"))
        self.end_time_var.set("07:00")


class RecapReportPanel(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        production_name: str,
        report_text: str,
        on_close: Callable[[], None],
    ) -> None:
        super().__init__(parent, bg="#1d1e2c", bd=1, relief="ridge")
        self.report_text = report_text
        self._on_close = on_close
        self.place(relx=0.5, rely=0.5, anchor="center")
        container = ttk.Frame(self, padding=16)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)

        header = ttk.Frame(container)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(header, text=f"Recap • {production_name}", style="SidebarHeading.TLabel").pack(side=tk.LEFT)
        ttk.Button(header, text="Close", command=self._close).pack(side=tk.RIGHT)

        text_frame = ttk.Frame(container)
        text_frame.grid(row=1, column=0, sticky="nsew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)

        self.text_widget = tk.Text(
            text_frame,
            wrap="word",
            height=24,
            width=82,
            background="#1c1d2b",
            foreground="#E8EAF6",
            insertbackground="#E8EAF6",
        )
        self.text_widget.grid(row=0, column=0, sticky="nsew")
        self.text_widget.insert("1.0", report_text)
        self.text_widget.configure(state="disabled")

        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.text_widget.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.text_widget.configure(yscrollcommand=scrollbar.set)

        button_row = ttk.Frame(container)
        button_row.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(button_row, text="Export...", command=self._export).pack(side=tk.LEFT)
        ttk.Button(button_row, text="Copy", command=self._copy).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(button_row, text="Close", command=self._close).pack(side=tk.RIGHT)

    def _export(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Export Recap",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=f"recap_{datetime.now():%Y%m%d_%H%M}.txt",
        )
        if not path:
            return
        try:
            Path(path).write_text(self.report_text, encoding="utf-8")
            messagebox.showinfo("Export Complete", "Recap exported successfully.", parent=self)
        except Exception as exc:
            messagebox.showerror("Export Failed", str(exc), parent=self)

    def _copy(self) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(self.report_text)
            messagebox.showinfo("Recap", "Recap copied to clipboard.", parent=self)
        except Exception as exc:
            messagebox.showerror("Copy Failed", str(exc), parent=self)

    def _close(self) -> None:
        self._on_close()
