from __future__ import annotations

import ctypes
import shutil
import subprocess
import sys
import threading
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Callable, List, Optional
import tkinter as tk
from ctypes import wintypes
from tkinter import messagebox, ttk

from .calendar_tab import CalendarTab
from .contact_tab import ContactTab
from .database import Database
from .log_tab import LogTab
from .scrum_tab import ScrumTab
from .system_notifications import SystemNotifier
from .notifications import NotificationManager, NotificationPayload
from .environment import APP_NAME, ensure_user_data_dir, legacy_project_root
from .settings_store import AppSettings, JiraSettings, load_settings, save_settings
from .settings_tab import SettingsTab
from .special_features import (
    SPECIAL_FEATURES,
    describe_special_features,
    normalize_special_code,
    resolve_feature_keys_for_code,
    sanitize_special_feature_keys,
)
from .jira_client import JiraClient
from .jira_service import JiraService
from .shortcuts import (
    create_desktop_shortcut,
    remove_desktop_shortcut,
    desktop_shortcut_exists,
    create_start_menu_shortcut,
    remove_start_menu_shortcut,
    start_menu_shortcut_exists,
    create_startup_shortcut,
    remove_startup_shortcut,
    startup_shortcut_exists,
)
from .version import __version__
from . import updater
from . import utils
from .theme import ThemePalette, get_theme, THEMES


def _desktop_work_area(default_width: int, default_height: int) -> tuple[int, int, int, int]:
    if sys.platform == "win32":
        rect = wintypes.RECT()
        try:
            success = ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
            if success:
                return rect.left, rect.top, rect.right, rect.bottom
        except (AttributeError, OSError):
            pass
    return 0, 0, default_width, default_height


class PersonalAssistantApp(tk.Tk):
    def __init__(self, db_path: Path, data_root: Path, settings: AppSettings, settings_path: Path) -> None:
        super().__init__()
        self.title(APP_NAME)

        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        default_w = min(1380, max(1100, screen_w - 160))
        default_h = min(900, max(760, screen_h - 200))
        self.geometry(f"{default_w}x{default_h}")
        min_w = max(960, min(default_w, screen_w - 240))
        min_h = max(700, min(default_h, screen_h - 220))
        self.minsize(min_w, min_h)

        self.project_root = Path(__file__).resolve().parent.parent
        self.data_root = data_root
        self.logs_dir = self.data_root / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        self.settings_path = settings_path
        utils.set_use_24_hour_time(self.settings.use_24_hour_time)
        self._special_feature_keys = sanitize_special_feature_keys(self.settings.special_features)
        if self._special_feature_keys != self.settings.special_features:
            self.settings.special_features = self._special_feature_keys
            save_settings(self.settings_path, self.settings)
        self._special_tab_cache = {}
        self._special_tab_placeholders = {}
        self.theme_name = settings.theme if settings.theme in THEMES else "dark"
        self.theme: ThemePalette = get_theme(self.theme_name)
        self._icon_path = self._ensure_icon_file()
        self.db = Database(db_path)
        self.system_notifier = SystemNotifier()
        self._production_log_scheduler_running = False
        self.after(5_000, self._poll_production_log_automations)
        self.configure(bg=self.theme.window_bg)
        self._configure_styles(self.theme)
        self._apply_window_icon()
        self.jira_service = JiraService(
            lambda: self.settings.jira,
            debug_log_path=self.logs_dir / "jira_debug.log",
        )

        self.main_frame = ttk.Frame(self, style="TFrame")
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        self._build_tab_bar(self.main_frame)
        self.notebook = ttk.Notebook(self.main_frame, style="AppHidden.TNotebook")
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.settings_tab_frame: Optional[ttk.Frame] = None
        self.settings_tab: Optional[SettingsTab] = None

        self.calendar_tab = CalendarTab(self.notebook, self.db, self.theme)
        self._core_tab_cache: dict[str, tk.Misc] = {"calendar": self.calendar_tab}
        self._core_tab_placeholders: dict[str, tk.Misc] = {}
        self._core_tab_labels = {
            "calendar": "Production Calendar",
            "log": "Daily Update Log",
            "scrum": "Tasks Board",
            "contact": "Contact Support",
        }
        self._base_tab_order = ["calendar", "log", "scrum", "contact"]
        self._current_tab_key = "calendar"
        self._sync_notebook_tabs()

        self._last_notebook_tab = self.notebook.select()
        self._settings_visible = False
        self.notebook.bind("<<NotebookTabChanged>>", self._record_last_notebook_tab)
        self.after(50, self._position_settings_button)
        self._sync_settings_button_state()

        self.notifications: List[NotificationWindow] = []
        self.notification_manager = NotificationManager(self.db, self._handle_notification)
        start_time = self._coerce_time_to_dt(self.settings.daily_update_start, "08:00")
        end_time = self._coerce_time_to_dt(self.settings.daily_update_end, "17:00")
        self.notification_manager.configure_daily_log_hours(start_time, end_time)
        self.notification_manager.set_standing_reminders_enabled(self.settings.daily_update_notifications)
        self.after(1000, self.notification_manager.start)
        self.after(2000, self._check_for_updates_async)
        self.after(250, self._ensure_shortcuts)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------------------------------------------------------- Styles
    def _configure_styles(self, palette: ThemePalette) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TFrame", background=palette.surface_bg)
        style.configure("TNotebook", background=palette.window_bg, borderwidth=0)
        style.configure("TNotebook.Tab", background=palette.surface_alt_bg, foreground=palette.text_secondary, padding=(16, 4))
        style.map(
            "TNotebook.Tab",
            background=[("selected", palette.card_alt_bg)],
            foreground=[("selected", palette.text_primary)],
            padding=[("selected", (16, 10))],
        )
        style.configure("TLabel", background=palette.surface_bg, foreground=palette.text_primary, font=("Segoe UI", 10))
        style.configure("CalendarHeading.TLabel", font=("Segoe UI", 14, "bold"), foreground=palette.text_primary, background=palette.surface_bg)
        style.configure("SidebarHeading.TLabel", font=("Segoe UI", 12, "bold"), foreground=palette.accent, background=palette.surface_bg)
        style.configure("SelectedDay.TLabel", font=("Segoe UI", 11), foreground=palette.text_secondary, background=palette.surface_bg)
        style.configure(
            "TButton",
            background=palette.list_alt_bg,
            foreground=palette.text_primary,
            padding=(12, 6),
            bordercolor=palette.border,
        )
        style.map(
            "TButton",
            background=[("pressed", palette.list_selected_bg), ("active", palette.list_selected_bg)],
        )
        style.configure(
            "SettingsTabInactive.TButton",
            background=palette.surface_alt_bg,
            foreground=palette.text_secondary,
            padding=(16, 4, 16, 4),
            relief="raised",
            borderwidth=1,
        )
        style.map(
            "SettingsTabInactive.TButton",
            background=[("pressed", palette.card_alt_bg), ("active", palette.card_alt_bg)],
        )
        style.configure(
            "SettingsTabActive.TButton",
            background=palette.card_alt_bg,
            foreground=palette.text_primary,
            padding=(16, 10, 16, 10),
            relief="sunken",
            borderwidth=1,
        )
        style.map(
            "SettingsTabActive.TButton",
            background=[("active", palette.card_alt_bg)],
        )
        style.configure(
            "Treeview",
            background=palette.list_bg,
            fieldbackground=palette.list_bg,
            foreground=palette.text_primary,
            borderwidth=0,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Treeview.Heading",
            background=palette.list_alt_bg,
            foreground=palette.text_secondary,
            font=("Segoe UI", 10, "bold"),
        )
        style.map("Treeview", background=[("selected", palette.list_selected_bg)], foreground=[("selected", palette.list_selected_fg)])
        style.configure(
            "Danger.TButton",
            background=palette.danger_bg,
            foreground=palette.danger_fg,
            padding=(12, 6),
        )
        style.map(
            "Danger.TButton",
            background=[("active", palette.danger_bg), ("pressed", palette.danger_bg)],
        )

        style.layout("AppHidden.TNotebook", [("Notebook.client", {"sticky": "nswe"})])
        style.layout("AppHidden.TNotebook.Tab", [])
        style.configure("AppHidden.TNotebook", background=palette.window_bg, borderwidth=0, tabmargins=0)

        style.configure(
            "TabBar.TButton",
            background=palette.surface_alt_bg,
            foreground=palette.text_secondary,
            padding=(14, 6),
            bordercolor=palette.border,
            relief="raised",
            borderwidth=1,
        )
        style.map(
            "TabBar.TButton",
            background=[("pressed", palette.card_alt_bg), ("active", palette.card_alt_bg)],
            foreground=[("pressed", palette.text_primary), ("active", palette.text_primary)],
        )
        style.configure(
            "TabBarActive.TButton",
            background=palette.card_alt_bg,
            foreground=palette.text_primary,
            padding=(16, 10),
            bordercolor=palette.border,
            relief="raised",
            borderwidth=1,
        )
        style.map(
            "TabBarActive.TButton",
            background=[("active", palette.card_alt_bg)],
        )
        style.configure(
            "TabBarArrow.TButton",
            background=palette.surface_alt_bg,
            foreground=palette.text_secondary,
            padding=(8, 4),
            bordercolor=palette.border,
        )
        style.map(
            "TabBarArrow.TButton",
            background=[("pressed", palette.card_alt_bg), ("active", palette.card_alt_bg)],
            foreground=[("pressed", palette.text_primary), ("active", palette.text_primary)],
        )

    def _build_tab_bar(self, parent: tk.Misc) -> None:
        self.tabbar = ttk.Frame(parent, style="TFrame", padding=(0, 4, 0, 0))
        self.tabbar.pack(fill=tk.X, side=tk.TOP)
        self.tabbar.columnconfigure(1, weight=1)

        self.tabs_left_button = ttk.Button(
            self.tabbar,
            text="<",
            width=3,
            style="TabBarArrow.TButton",
            command=lambda: self._scroll_tabs(-1),
        )
        self.tabs_left_button.grid(row=0, column=0, padx=(6, 2), pady=4)

        self.tabs_canvas = tk.Canvas(
            self.tabbar,
            height=36,
            highlightthickness=0,
            bd=0,
            bg=self.theme.surface_bg,
        )
        self.tabs_canvas.grid(row=0, column=1, sticky="ew", pady=(1, 0))
        self.tabs_inner = ttk.Frame(self.tabs_canvas, padding=(2, 0))
        self._tabs_window_id = self.tabs_canvas.create_window((0, 0), window=self.tabs_inner, anchor="w")

        self.tabs_right_button = ttk.Button(
            self.tabbar,
            text=">",
            width=3,
            style="TabBarArrow.TButton",
            command=lambda: self._scroll_tabs(1),
        )
        self.tabs_right_button.grid(row=0, column=2, padx=(2, 2), pady=4)

        self.settings_button = ttk.Button(
            self.tabbar,
            text="Settings",
            style="SettingsTabInactive.TButton",
            command=self._toggle_settings_view,
            cursor="hand2",
        )
        self.settings_button.grid(row=0, column=3, padx=(6, 8), pady=4, sticky="e")

        self.tabs_inner.bind("<Configure>", self._on_tabs_frame_configure)
        self.tabs_canvas.bind("<Configure>", self._on_tabs_canvas_configure)

        self._tab_buttons: dict[str, ttk.Button] = {}
        self._tab_order_widgets: list[str] = []

    def _ensure_settings_tab(self) -> SettingsTab:
        if self.settings_tab is not None:
            return self.settings_tab
        manage_shortcuts = self._should_manage_shortcut()
        self.settings_tab_frame = ttk.Frame(self.notebook, style="TFrame")
        self.settings_tab = SettingsTab(
            self.settings_tab_frame,
            desktop_enabled=self.settings.desktop_shortcut and manage_shortcuts,
            start_menu_enabled=self.settings.start_menu_shortcut and manage_shortcuts,
            startup_enabled=self.settings.launch_at_startup and manage_shortcuts,
            daily_notifications_enabled=self.settings.daily_update_notifications,
            daily_start=self.settings.daily_update_start,
            daily_end=self.settings.daily_update_end,
            use_24_hour_time=self.settings.use_24_hour_time,
            on_setting_toggle=self._handle_setting_toggle,
            on_hours_change=self._handle_daily_hours_change,
            on_theme_change=self._handle_theme_change,
            on_time_format_change=self._handle_time_format_change,
            on_jira_settings_change=self._handle_jira_settings_update,
            on_jira_test_connection=self._handle_jira_test_connection,
            special_features=describe_special_features(self._special_feature_keys),
            on_special_code_submit=self._handle_special_code_submit,
            on_special_feature_disable=self._handle_special_feature_disable,
            show_jira_section="jira" in self._special_feature_keys,
            theme_name=self.theme_name,
            app_version=__version__,
            jira_settings=self.settings.jira,
        )
        self.settings_tab.pack(fill=tk.BOTH, expand=True)
        self.settings_tab_frame.place_forget()
        return self.settings_tab

    def _on_tabs_frame_configure(self, _event: Optional[tk.Event] = None) -> None:
        if not hasattr(self, "tabs_canvas"):
            return
        self.tabs_canvas.configure(scrollregion=self.tabs_canvas.bbox("all"))
        self._update_tab_scroll_controls()

    def _on_tabs_canvas_configure(self, _event: Optional[tk.Event] = None) -> None:
        if not hasattr(self, "tabs_canvas"):
            return
        self._update_tab_scroll_controls()

    def _scroll_tabs(self, direction: int) -> None:
        if not hasattr(self, "tabs_canvas"):
            return
        region = self.tabs_canvas.bbox("all")
        if not region:
            return
        total_width = region[2] - region[0]
        view_width = max(self.tabs_canvas.winfo_width(), 1)
        if total_width <= view_width:
            self.tabs_canvas.xview_moveto(0.0)
            self._update_tab_scroll_controls()
            return
        shift_px = min(160, total_width - view_width)
        shift = shift_px / total_width
        start, _end = self.tabs_canvas.xview()
        max_start = max(0.0, 1.0 - (view_width / total_width))
        next_start = start + shift if direction > 0 else start - shift
        next_start = max(0.0, min(max_start, next_start))
        self.tabs_canvas.xview_moveto(next_start)
        self._update_tab_scroll_controls()

    def _update_tab_scroll_controls(self) -> None:
        if not hasattr(self, "tabs_canvas"):
            return
        region = self.tabs_canvas.bbox("all")
        if not region:
            self.tabs_left_button.state(["disabled"])
            self.tabs_right_button.state(["disabled"])
            return
        total_width = region[2] - region[0]
        view_width = max(self.tabs_canvas.winfo_width(), 1)
        if total_width <= view_width + 2:
            self.tabs_left_button.state(["disabled"])
            self.tabs_right_button.state(["disabled"])
            self.tabs_canvas.xview_moveto(0.0)
            return
        start, end = self.tabs_canvas.xview()
        if start <= 0.001:
            self.tabs_left_button.state(["disabled"])
        else:
            self.tabs_left_button.state(["!disabled"])
        if end >= 0.999:
            self.tabs_right_button.state(["disabled"])
        else:
            self.tabs_right_button.state(["!disabled"])

    def _sync_tab_buttons(self, tabs: list[tuple[str, Optional[tk.Misc], str]]) -> None:
        if not hasattr(self, "tabs_inner"):
            return
        for widget in self.tabs_inner.winfo_children():
            widget.destroy()
        self._tab_buttons.clear()
        self._tab_order_widgets = []
        for key, _widget, label in tabs:
            btn = ttk.Button(
                self.tabs_inner,
                text=label,
                style="TabBar.TButton",
                command=lambda target_key=key: self._select_tab_key(target_key),
            )
            btn.pack(side=tk.LEFT, padx=(0, 6), pady=(1, 0))
            self._tab_buttons[key] = btn
            self._tab_order_widgets.append(key)
        self._update_tab_button_styles()
        self.after(0, self._scroll_active_tab_into_view)

    def _select_tab(self, widget: tk.Misc) -> None:
        key = self._tab_key_for_widget(widget) or self._core_key_for_widget(widget) or self._special_key_for_widget(widget)
        if key is None:
            return
        self._select_tab_key(key)

    def _select_tab_key(self, key: str) -> None:
        widget = self._realized_widget_for_key(key)
        if widget is None:
            if key in self._core_tab_labels:
                widget = self._realize_core_tab(key)
            else:
                widget = self._realize_special_tab(key)
        if widget is None:
            return
        self._current_tab_key = key
        self._sync_notebook_tabs(preferred_widget=widget)
        self._update_tab_button_styles()
        self._scroll_active_tab_into_view()

    def _update_tab_button_styles(self) -> None:
        if not hasattr(self, "_tab_buttons"):
            return
        current_key = self._current_tab_key
        for key, btn in self._tab_buttons.items():
            style = "TabBarActive.TButton" if key == current_key else "TabBar.TButton"
            btn.configure(style=style)

    def _scroll_active_tab_into_view(self) -> None:
        if not hasattr(self, "tabs_canvas"):
            return
        button = self._tab_buttons.get(self._current_tab_key)
        if button is None:
            return
        region = self.tabs_canvas.bbox("all")
        if not region:
            return
        total_width = region[2] - region[0]
        if total_width <= 0:
            return
        window_pos = self.tabs_canvas.coords(self._tabs_window_id)
        offset_x = window_pos[0] if window_pos else 0
        btn_left = offset_x + button.winfo_x()
        btn_right = btn_left + button.winfo_width()
        view_left = self.tabs_canvas.canvasx(0)
        view_right = view_left + self.tabs_canvas.winfo_width()
        if btn_left < view_left:
            self.tabs_canvas.xview_moveto(max(0.0, btn_left / total_width))
        elif btn_right > view_right:
            target_left = max(0.0, min(1.0, (btn_right - self.tabs_canvas.winfo_width()) / total_width))
            self.tabs_canvas.xview_moveto(target_left)
        self._update_tab_scroll_controls()

    def _compute_tab_order(self) -> list[str]:
        order = list(self._base_tab_order)
        pending = [key for key in self._special_feature_keys if key not in order]
        inserted = set(order)
        progress = True
        while pending and progress:
            progress = False
            for key in list(pending):
                feature = SPECIAL_FEATURES.get(key)
                if not feature or not feature.is_tab_feature():
                    pending.remove(key)
                    continue
                insert_after = feature.insert_after
                if insert_after and insert_after not in inserted:
                    continue
                if key in inserted:
                    pending.remove(key)
                    continue
                if insert_after and insert_after in order:
                    order.insert(order.index(insert_after) + 1, key)
                else:
                    order.append(key)
                inserted.add(key)
                pending.remove(key)
                progress = True
        for key in pending:
            if key not in order:
                order.append(key)
        return order

    def _get_core_tab(self, key: str) -> Optional[tuple[tk.Misc, str]]:
        label = self._core_tab_labels.get(key)
        if label is None:
            return None
        return self._core_tab_cache.get(key), label

    def _core_key_for_widget(self, widget: tk.Misc | None) -> Optional[str]:
        if widget is None:
            return None
        for key, placeholder in self._core_tab_placeholders.items():
            if widget == placeholder and key not in self._core_tab_cache:
                return key
        return None

    def _realize_core_tab(self, key: str) -> Optional[tk.Misc]:
        widget = self._core_tab_cache.get(key)
        if widget is not None:
            return widget
        if key == "log":
            widget = LogTab(self.notebook, self.db)
        elif key == "scrum":
            widget = ScrumTab(self.notebook, self.db, self.theme)
        elif key == "contact":
            widget = ContactTab(self.notebook, self.data_root, app_version=__version__)
        else:
            return None
        self._core_tab_cache[key] = widget
        return widget

    def _get_special_tab(self, key: str) -> Optional[tuple[tk.Misc, str]]:
        feature = SPECIAL_FEATURES.get(key)
        if not feature or not feature.is_tab_feature():
            return None
        return self._special_tab_cache.get(key), feature.tab_label or feature.title

    def _special_key_for_widget(self, widget: tk.Misc | None) -> Optional[str]:
        if widget is None:
            return None
        for key, placeholder in self._special_tab_placeholders.items():
            if widget == placeholder and key not in self._special_tab_cache:
                return key
        return None

    def _realize_special_tab(self, key: str) -> Optional[tk.Misc]:
        widget = self._special_tab_cache.get(key)
        if widget is not None:
            return widget
        feature = SPECIAL_FEATURES.get(key)
        if not feature or not feature.is_tab_feature() or feature.tab_builder is None:
            return None
        widget = feature.tab_builder(self)
        self._special_tab_cache[key] = widget
        return widget

    def _realized_widget_for_key(self, key: str) -> Optional[tk.Misc]:
        if key in self._core_tab_labels:
            return self._core_tab_cache.get(key)
        return self._special_tab_cache.get(key)

    def _tab_key_for_widget(self, widget: tk.Misc | None) -> Optional[str]:
        if widget is None:
            return None
        for key, tab in self._core_tab_cache.items():
            if widget == tab:
                return key
        for key, tab in self._special_tab_cache.items():
            if widget == tab:
                return key
        return None

    def _sync_notebook_tabs(self, preferred_widget: Optional[tk.Misc] = None) -> None:
        desired_keys = self._compute_tab_order()
        desired_tabs: list[tuple[str, Optional[tk.Misc], str]] = []
        for key in desired_keys:
            core = self._get_core_tab(key)
            if core is not None:
                widget, label = core
                desired_tabs.append((key, widget, label))
                continue
            special = self._get_special_tab(key)
            if special:
                widget, label = special
                desired_tabs.append((key, widget, label))

        for tab_id in self.notebook.tabs():
            try:
                self.notebook.forget(tab_id)
            except tk.TclError:
                continue

        active_widget = preferred_widget or self._realized_widget_for_key(self._current_tab_key)
        if active_widget is None:
            for key, widget, _label in desired_tabs:
                if widget is not None:
                    active_widget = widget
                    self._current_tab_key = key
                    break

        active_label = None
        for key, widget, label in desired_tabs:
            if widget == active_widget:
                active_label = label
                self._current_tab_key = key
                break

        if active_widget is not None:
            try:
                self.notebook.add(active_widget, text=active_label or "")
                self.notebook.select(active_widget)
            except tk.TclError:
                pass

        try:
            self._last_notebook_tab = self.notebook.select()
            selected_widget = self.nametowidget(self._last_notebook_tab) if self._last_notebook_tab else None
            selected_key = self._tab_key_for_widget(selected_widget)
            if selected_key is not None:
                self._current_tab_key = selected_key
        except tk.TclError:
            self._last_notebook_tab = None
        self._sync_tab_buttons(desired_tabs)

    def _check_for_updates_async(self) -> None:
        if not updater.should_check_for_updates():
            return
        thread = threading.Thread(target=self._check_for_updates_worker, daemon=True)
        thread.start()

    def _check_for_updates_worker(self) -> None:
        info = updater.check_for_update(__version__)
        if info is None:
            return
        self.after(0, lambda: self._prompt_update(info))

    def _prompt_update(self, info: "updater.AvailableUpdate") -> None:
        summary_lines = [f"A new version ({info.version}) is available."]
        notes = (info.notes or "").strip()
        if notes:
            summary_lines.append("")
            max_preview = 800
            preview = notes if len(notes) <= max_preview else notes[: max_preview - 3] + "..."
            summary_lines.append(preview)
        summary_lines.append("")
        summary_lines.append("Install now? The app will download the update, close, and you'll reopen it manually once finished.")
        if not messagebox.askyesno("Update Available", "\n".join(summary_lines), parent=self):
            return
        self._begin_update_install(info)

    def _begin_update_install(self, info: "updater.AvailableUpdate") -> None:
        progress_window = UpdateProgressWindow(self, info, self.theme)

        def worker() -> None:
            try:
                updater.prepare_and_schedule_restart(info, progress_window.report_progress)
            except updater.UpdateError as exc:
                self.after(
                    0,
                    lambda: (
                        progress_window.close(),
                        messagebox.showerror("Update Failed", str(exc), parent=self),
                    ),
                )
                return
            self.after(0, lambda: progress_window.mark_complete(self._restart_for_update))

        threading.Thread(target=worker, daemon=True).start()

    def _restart_for_update(self) -> None:
        messagebox.showinfo(
            "Update Ready",
            "Personal Assistant will close so the update can be installed.\nAfter it finishes, reopen the app from your shortcut.",
            parent=self,
        )
        self.after(100, self.on_close)

    def _ensure_icon_file(self) -> Optional[Path]:
        icon_path = self.data_root / "personal_assistant.ico"
        if icon_path.exists():
            return icon_path
        candidates: List[Path] = []
        if hasattr(sys, "_MEIPASS"):
            candidates.append(Path(sys._MEIPASS) / "personal_assistant.ico")
        executable_dir = Path(sys.executable).resolve().parent
        candidates.append(executable_dir / "personal_assistant.ico")
        candidates.append(self.project_root / "assets" / "personal_assistant.ico")
        for candidate in candidates:
            if candidate.exists():
                try:
                    icon_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(candidate, icon_path)
                    return icon_path
                except Exception:
                    continue
        return icon_path if icon_path.exists() else None

    def _apply_window_icon(self) -> None:
        icon = self._icon_path
        if icon and icon.exists():
            try:
                self.iconbitmap(str(icon))
            except Exception:
                pass

    def _should_manage_shortcut(self) -> bool:
        return sys.platform.startswith("win") and bool(getattr(sys, "frozen", False))

    def _ensure_shortcuts(self) -> None:
        if not self._should_manage_shortcut():
            if self.settings_tab is not None:
                self.settings_tab.update_shortcut_state("desktop", False)
                self.settings_tab.update_shortcut_state("start_menu", False)
                self.settings_tab.update_shortcut_state("startup", False)
            return
        icon = self._icon_path or self._ensure_icon_file()
        if icon is not None and icon.exists():
            self._icon_path = icon
            self._apply_window_icon()
        target = Path(sys.executable).resolve()
        desktop_exists = desktop_shortcut_exists()
        start_exists = start_menu_shortcut_exists()
        startup_exists = startup_shortcut_exists()
        if self.settings.desktop_shortcut and not desktop_exists:
            if self._create_shortcut("desktop", target):
                desktop_exists = True
        elif not self.settings.desktop_shortcut and desktop_exists:
            if self._remove_shortcut("desktop"):
                desktop_exists = False
        if self.settings.start_menu_shortcut and not start_exists:
            if self._create_shortcut("start_menu", target):
                start_exists = True
        elif not self.settings.start_menu_shortcut and start_exists:
            if self._remove_shortcut("start_menu"):
                start_exists = False
        if self.settings.launch_at_startup and not startup_exists:
            if self._create_shortcut("startup", target):
                startup_exists = True
        elif not self.settings.launch_at_startup and startup_exists:
            if self._remove_shortcut("startup"):
                startup_exists = False
        self.settings.desktop_shortcut = desktop_exists
        self.settings.start_menu_shortcut = start_exists
        self.settings.launch_at_startup = startup_exists
        if self.settings_tab is not None:
            self.settings_tab.update_shortcut_state("desktop", desktop_exists)
            self.settings_tab.update_shortcut_state("start_menu", start_exists)
            self.settings_tab.update_shortcut_state("startup", startup_exists)
        save_settings(self.settings_path, self.settings)

    def _create_shortcut(self, kind: str, target: Path) -> bool:
        icon = self._icon_path or self._ensure_icon_file()
        if icon is not None and icon.exists():
            self._icon_path = icon
            self._apply_window_icon()
        labels = {
            "desktop": "Desktop Shortcut",
            "start_menu": "Start Menu Shortcut",
            "startup": "Windows Startup",
        }
        label = labels.get(kind, "Shortcut")
        if icon is None or not icon.exists():
            messagebox.showerror(
                label,
                "Unable to locate the application icon for the shortcut.",
                parent=self,
            )
            return False
        if kind == "desktop":
            success = create_desktop_shortcut(target, icon)
        elif kind == "start_menu":
            success = create_start_menu_shortcut(target, icon)
        else:
            success = create_startup_shortcut(target, icon)
        if not success:
            messagebox.showerror(label, f"Unable to create the {label.lower()}.", parent=self)
        return success

    def _remove_shortcut(self, kind: str) -> bool:
        if kind == "desktop":
            return remove_desktop_shortcut()
        if kind == "start_menu":
            return remove_start_menu_shortcut()
        return remove_startup_shortcut()

    def _handle_setting_toggle(self, kind: str, enabled: bool) -> None:
        if kind == "daily_notifications":
            self.settings.daily_update_notifications = bool(enabled)
            self.notification_manager.set_standing_reminders_enabled(bool(enabled))
            if enabled:
                start_time = self._coerce_time_to_dt(self.settings.daily_update_start, "08:00")
                end_time = self._coerce_time_to_dt(self.settings.daily_update_end, "17:00")
            self.notification_manager.configure_daily_log_hours(start_time, end_time)
            if self.settings_tab is not None:
                self.settings_tab.update_daily_notification_state(bool(enabled))
            save_settings(self.settings_path, self.settings)
            return

        labels = {"desktop": "Desktop", "start_menu": "Start Menu", "startup": "Windows Startup"}
        label = labels.get(kind, "Application")
        if not self._should_manage_shortcut():
            messagebox.showinfo(
                f"{label} Shortcut",
                f"{label} shortcuts are only available in the packaged application.",
                parent=self,
            )
            if self.settings_tab is not None:
                self.settings_tab.update_shortcut_state(kind, False)
            return
        target = Path(sys.executable).resolve()
        if enabled:
            success = self._create_shortcut(kind, target)
            if success:
                if kind == "desktop":
                    self.settings.desktop_shortcut = True
                elif kind == "start_menu":
                    self.settings.start_menu_shortcut = True
                else:
                    self.settings.launch_at_startup = True
        else:
            success = self._remove_shortcut(kind)
            if not success:
                messagebox.showerror(
                    f"{label} Shortcut",
                    f"Unable to remove the {label.lower()} shortcut.",
                    parent=self,
                )
                if kind == "desktop":
                    self.settings.desktop_shortcut = True
                elif kind == "start_menu":
                    self.settings.start_menu_shortcut = True
                else:
                    self.settings.launch_at_startup = True
            else:
                if kind == "desktop":
                    self.settings.desktop_shortcut = False
                elif kind == "start_menu":
                    self.settings.start_menu_shortcut = False
                else:
                    self.settings.launch_at_startup = False
        if self.settings_tab is not None:
            self.settings_tab.update_shortcut_state("desktop", desktop_shortcut_exists())
            self.settings_tab.update_shortcut_state("start_menu", start_menu_shortcut_exists())
            self.settings_tab.update_shortcut_state("startup", startup_shortcut_exists())
        save_settings(self.settings_path, self.settings)

    def _handle_daily_hours_change(self, start_text: str, end_text: str) -> None:
        settings_tab = self._ensure_settings_tab()
        try:
            start_time = self._parse_time_string(start_text)
            end_time = self._parse_time_string(end_text)
        except ValueError as exc:
            messagebox.showerror("Daily Update Log Reminders", str(exc), parent=self)
            settings_tab.update_daily_hours(
                self.settings.daily_update_start,
                self.settings.daily_update_end,
            )
            return
        self.settings.daily_update_start = self._format_time_storage(start_time)
        self.settings.daily_update_end = self._format_time_storage(end_time)
        save_settings(self.settings_path, self.settings)
        self.notification_manager.configure_daily_log_hours(start_time, end_time)
        settings_tab.update_daily_hours(
            self.settings.daily_update_start,
            self.settings.daily_update_end,
        )

    def _handle_theme_change(self, theme_name: str) -> None:
        normalized = theme_name.lower()
        if normalized not in THEMES:
            normalized = "dark"
        if normalized == self.theme_name:
            return
        self.theme_name = normalized
        self.theme = get_theme(normalized)
        self.settings.theme = normalized
        self._configure_styles(self.theme)
        self._apply_theme_to_children()
        save_settings(self.settings_path, self.settings)

    def _handle_time_format_change(self, use_24_hour: bool) -> None:
        self.settings.use_24_hour_time = bool(use_24_hour)
        utils.set_use_24_hour_time(self.settings.use_24_hour_time)
        save_settings(self.settings_path, self.settings)
        if self.settings_tab is not None:
            self.settings_tab.update_time_format(self.settings.use_24_hour_time)
        self._apply_time_format_to_children()

    def _apply_special_feature_keys(self, keys: list[str]) -> None:
        cleaned = sanitize_special_feature_keys(keys)
        if cleaned != self._special_feature_keys:
            self._special_feature_keys = cleaned
            self.settings.special_features = cleaned
            save_settings(self.settings_path, self.settings)
        self._sync_notebook_tabs()
        if self.settings_tab is not None:
            self.settings_tab.update_special_features(describe_special_features(self._special_feature_keys))
            self.settings_tab.update_jira_section_visibility("jira" in self._special_feature_keys)

    def _handle_special_code_submit(self, code: str) -> None:
        normalized = normalize_special_code(code)
        if not normalized:
            self.settings_tab.update_special_code_status("Enter a code to unlock features.", False)
            return
        feature_keys = resolve_feature_keys_for_code(normalized)
        if not feature_keys:
            self.settings_tab.update_special_code_status("That code did not unlock any features.", False)
            return
        new_keys = [key for key in feature_keys if key not in self._special_feature_keys]
        if not new_keys:
            self.settings_tab.update_special_code_status("Those features are already enabled.", True)
            self.settings_tab.clear_special_code_entry()
            return
        self._apply_special_feature_keys(self._special_feature_keys + new_keys)
        enabled_names = ", ".join(SPECIAL_FEATURES[key].title for key in new_keys if key in SPECIAL_FEATURES)
        self.settings_tab.update_special_code_status(f"Unlocked: {enabled_names}.", True)
        self.settings_tab.clear_special_code_entry()

    def _handle_special_feature_disable(self, key: str) -> None:
        if key not in self._special_feature_keys:
            return
        remaining = [item for item in self._special_feature_keys if item != key]
        self._apply_special_feature_keys(remaining)
        feature = SPECIAL_FEATURES.get(key)
        if feature:
            self.settings_tab.update_special_code_status(f"{feature.title} removed.", True)

    def _handle_jira_settings_update(self, jira_settings: JiraSettings) -> None:
        self.settings.jira = jira_settings
        save_settings(self.settings_path, self.settings)
        jira_tab = self._special_tab_cache.get("jira")
        if jira_tab is not None and hasattr(jira_tab, "on_settings_updated"):
            jira_tab.on_settings_updated()

    def _handle_jira_test_connection(self, jira_settings: JiraSettings) -> None:
        self.settings_tab.update_jira_status("Testing Jira connection...", None)
        self._handle_jira_settings_update(jira_settings)
        if not jira_settings.email or not jira_settings.api_token:
            self.settings_tab.update_jira_status("Enter email and API token before testing.", False)
            return
        if not jira_settings.base_url:
            self.settings_tab.update_jira_status("Specify a Jira base URL.", False)
            return
        client = JiraClient.from_settings(jira_settings)
        success, message = client.test_connection()
        self.settings_tab.update_jira_status(message, success)

    def _apply_theme_to_children(self) -> None:
        self.configure(bg=self.theme.window_bg)
        if self.settings_tab_frame is not None:
            self.settings_tab_frame.configure(style="TFrame")
        if self.settings_tab is not None:
            self.settings_tab.update_theme_selection(self.theme_name)
        if hasattr(self, "tabs_canvas"):
            self.tabs_canvas.configure(bg=self.theme.surface_bg)
        for tab in self._core_tab_cache.values():
            if hasattr(tab, "apply_theme"):
                tab.apply_theme(self.theme)
        for tab in self._special_tab_cache.values():
            if hasattr(tab, "apply_theme"):
                tab.apply_theme(self.theme)
        for window in list(self.notifications):
            if hasattr(window, "apply_theme"):
                window.apply_theme(self.theme)
        self._sync_settings_button_state()
        self._update_tab_button_styles()

    def _apply_time_format_to_children(self) -> None:
        for tab in self._core_tab_cache.values():
            if hasattr(tab, "apply_time_format"):
                tab.apply_time_format(self.settings.use_24_hour_time)
            elif hasattr(tab, "refresh"):
                tab.refresh()
        for tab in self._special_tab_cache.values():
            if hasattr(tab, "apply_time_format"):
                tab.apply_time_format(self.settings.use_24_hour_time)

    def _parse_time_string(self, value: str) -> dt_time:
        return utils.parse_time_string(value)

    def _coerce_time_to_dt(self, value: str, fallback: str) -> dt_time:
        try:
            return self._parse_time_string(value)
        except ValueError:
            return self._parse_time_string(fallback)

    @staticmethod
    def _format_time_storage(value: dt_time) -> str:
        return f"{value.hour:02d}:{value.minute:02d}"

    def _handle_notification(self, payload: NotificationPayload) -> None:
        self.after(0, lambda: self.show_notification(payload))

    # ---------------------------------------------------------------- Events
    def _poll_production_log_automations(self) -> None:
        try:
            if self._production_log_scheduler_running or "production_log" not in self._special_feature_keys:
                return
            from .production_log_automation import (
                ProductionLogAutomationRunner,
                find_due_automations,
            )

            decisions = find_due_automations(self.db, datetime.now())
            if not decisions:
                return
            self._production_log_scheduler_running = True

            def runner() -> None:
                service = ProductionLogAutomationRunner(self.db)
                successes: list[str] = []
                failures: list[str] = []
                for decision in decisions:
                    try:
                        result = service.run(
                            decision.automation,
                            scheduled_for=decision.scheduled_for,
                            trigger_type="scheduled",
                        )
                    except Exception as exc:
                        failures.append(f"{decision.automation.name}: {exc}")
                    else:
                        if result.status == "success":
                            successes.append(f"{decision.automation.name}: {result.message}")
                self.after(0, lambda: self._finish_production_log_scheduler(successes, failures))

            threading.Thread(target=runner, name="production-log-app-scheduler", daemon=True).start()
        finally:
            self.after(60_000, self._poll_production_log_automations)

    def _finish_production_log_scheduler(self, successes: list[str], failures: list[str]) -> None:
        self._production_log_scheduler_running = False
        if successes:
            self.system_notifier.notify("Production Log Automation", " | ".join(successes))
        if failures:
            self.system_notifier.notify("Production Log Automation Failed", " | ".join(failures))
        production_tab = self._special_tab_cache.get("production_log")
        if production_tab is not None and hasattr(production_tab, "refresh_automation_status"):
            try:
                production_tab.refresh_automation_status()
            except Exception:
                pass

    def show_notification(self, payload: NotificationPayload) -> None:
        body_text = payload.body.strip() if payload.body else ""
        fallback = utils.format_time(payload.occurs_at)
        self.system_notifier.notify(payload.title, body_text or fallback)
        window = NotificationWindow(self, payload, self.theme)
        self.notifications.append(window)
        self._rearrange_notifications()

    def _rearrange_notifications(self) -> None:
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        work_left, work_top, work_right, work_bottom = _desktop_work_area(screen_width, screen_height)
        work_height = work_bottom - work_top
        padding = 20
        window_width = 320
        bottom_offset = padding

        for window in list(self.notifications):
            if not window.winfo_exists():
                self.notifications.remove(window)
                continue
            window.update_idletasks()
            window_height = min(
                max(160, window.winfo_reqheight()),
                work_height - (2 * padding),
            )
            x = max(work_left + padding, work_right - window_width - padding)
            y = work_bottom - window_height - bottom_offset
            window.geometry(f"{window_width}x{window_height}+{x}+{y}")
            bottom_offset += window_height + 10

    def _position_settings_button(self, event: Optional[tk.Event] = None) -> None:
        if self._settings_visible:
            self._place_settings_overlay()

    def _place_settings_overlay(self) -> None:
        if not self._settings_visible:
            return
        self._ensure_settings_tab()
        if self.settings_tab_frame is None:
            return
        offset = self._compute_notebook_content_offset()
        height = max(0, self.notebook.winfo_height() - offset)
        params = {
            "in_": self.notebook,
            "relx": 0.0,
            "x": 0,
            "y": offset,
            "relwidth": 1.0,
        }
        if height > 0:
            params["height"] = height
        else:
            params["relheight"] = 1.0
        try:
            self.settings_tab_frame.place(**params)
            self.settings_tab_frame.lift()
        except tk.TclError:
            pass

    def _compute_notebook_content_offset(self) -> int:
        return 0

    def _record_last_notebook_tab(self, event: Optional[tk.Event] = None) -> None:
        current = self.notebook.select()
        if self._settings_visible:
            if self.settings_tab_frame is not None:
                self.settings_tab_frame.place_forget()
            self._settings_visible = False
            self._last_notebook_tab = current
            self._sync_settings_button_state()
            self._position_settings_button()
            try:
                self.settings_button.state(["!pressed"])
            except tk.TclError:
                pass
            self._update_tab_button_styles()
            self._scroll_active_tab_into_view()
            return
        self._last_notebook_tab = current
        self._sync_settings_button_state()
        self._update_tab_button_styles()
        self._scroll_active_tab_into_view()

    def _toggle_settings_view(self) -> None:
        if self._settings_visible:
            self._hide_settings_view()
        else:
            self._show_settings_view()

    def _show_settings_view(self) -> None:
        self._ensure_settings_tab()
        self._last_notebook_tab = self.notebook.select()
        self._settings_visible = True
        self._place_settings_overlay()
        self._sync_settings_button_state()
        self._position_settings_button()
        try:
            self.settings_button.state(["pressed"])
        except tk.TclError:
            pass

    def _hide_settings_view(self) -> None:
        if self._last_notebook_tab:
            try:
                self.notebook.select(self._last_notebook_tab)
            except tk.TclError:
                pass
        if self.settings_tab_frame is not None:
            self.settings_tab_frame.place_forget()
        self._settings_visible = False
        self._sync_settings_button_state()
        self._position_settings_button()
        try:
            self.settings_button.state(["!pressed"])
        except tk.TclError:
            pass
        try:
            if self.settings_tab_frame is not None:
                self.settings_tab_frame.lower()
        except tk.TclError:
            pass

    def _sync_settings_button_state(self) -> None:
        if not hasattr(self, "settings_button"):
            return
        style_name = "SettingsTabActive.TButton" if self._settings_visible else "SettingsTabInactive.TButton"
        self.settings_button.configure(style=style_name)

    def remove_notification(self, window: "NotificationWindow") -> None:
        if window in self.notifications:
            self.notifications.remove(window)
        self._rearrange_notifications()

    def on_close(self) -> None:
        self.notification_manager.stop()
        self.db.close()
        save_settings(self.settings_path, self.settings)
        self.destroy()

class UpdateProgressWindow(tk.Toplevel):
    def __init__(self, master: PersonalAssistantApp, update_info: "updater.AvailableUpdate", theme: ThemePalette) -> None:
        super().__init__(master)
        self.master = master
        self.theme = theme
        self.configure(bg=self.theme.card_bg)
        self.resizable(False, False)
        self.transient(master)
        self.title("Installing Update")
        self.progress_mode = "indeterminate"

        container = ttk.Frame(self, padding=20)
        container.pack(fill=tk.BOTH, expand=True)

        title = update_info.release_name or f"Version {update_info.version}"
        ttk.Label(container, text=f"Updating to {title}", style="SidebarHeading.TLabel").pack(anchor="w")

        self.status_var = tk.StringVar(value="Preparing download...")
        ttk.Label(container, textvariable=self.status_var, wraplength=320).pack(anchor="w", pady=(10, 6))

        self.instructions_var = tk.StringVar(
            value="Once the download finishes, Personal Assistant will close so the update can be installed. Reopen it from your shortcut afterwards."
        )
        ttk.Label(container, textvariable=self.instructions_var, wraplength=320, foreground=self.theme.text_secondary).pack(anchor="w", pady=(0, 12))

        self.progress = ttk.Progressbar(container, mode="indeterminate", length=320)
        self.progress.pack(fill=tk.X)
        self.progress.start(10)

        self.percent_var = tk.StringVar(value="")
        ttk.Label(container, textvariable=self.percent_var, foreground=self.theme.text_secondary).pack(anchor="e", pady=(6, 0))

        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self.attributes("-topmost", True)
        self.after(100, self.lift)
        self._center_on_master()

    def _center_on_master(self) -> None:
        self.update_idletasks()
        width = max(360, self.winfo_width())
        height = max(160, self.winfo_height())
        master = self.master
        master.update_idletasks()
        x = master.winfo_rootx() + max(0, (master.winfo_width() - width) // 2)
        y = master.winfo_rooty() + max(0, (master.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def report_progress(self, downloaded: int, total: int) -> None:
        def _apply() -> None:
            if total <= 0:
                if self.progress_mode != "indeterminate":
                    self.progress_mode = "indeterminate"
                    self.progress.configure(mode="indeterminate")
                    self.progress.start(10)
                    self.percent_var.set("")
                self.status_var.set("Downloading update...")
                return
            if self.progress_mode != "determinate":
                self.progress_mode = "determinate"
                self.progress.stop()
                self.progress.configure(mode="determinate", maximum=max(total, 1))
            clamped = max(0, min(downloaded, total))
            self.progress["value"] = clamped
            percent = (clamped / total) * 100 if total else 0
            self.percent_var.set(f"{percent:.0f}%")
            self.status_var.set("Downloading update...")

        self.after(0, _apply)

    def mark_complete(self, callback: Callable[[], None]) -> None:
        def _apply() -> None:
            if self.progress_mode == "indeterminate":
                self.progress.stop()
                self.progress.configure(mode="determinate", maximum=1, value=1)
            else:
                self.progress["value"] = self.progress["maximum"]
            self.progress_mode = "determinate"
            self.percent_var.set("100%")
            self.status_var.set("Download complete. Closing to install update...")
            self.instructions_var.set("Personal Assistant will close now and finish installing the update. Reopen it from your shortcut once the window disappears.")
            self.after(800, lambda: (self.close(), callback()))

        self.after(0, _apply)

    def close(self) -> None:
        try:
            self.progress.stop()
        except Exception:
            pass
        if self.winfo_exists():
            self.destroy()


class NotificationWindow(tk.Toplevel):
    def __init__(self, master: PersonalAssistantApp, payload: NotificationPayload, theme: ThemePalette) -> None:
        super().__init__(master)
        self.master = master
        self.payload = payload
        self.theme = theme
        self.configure(bg=self.theme.notification_bg)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self._body_label: ttk.Label | None = None
        self._time_label: ttk.Label | None = None

        frame = ttk.Frame(self, padding=14)
        frame.pack(fill=tk.BOTH, expand=True)

        header_text = "Reminder" if payload.kind == "event" else payload.title
        ttk.Label(frame, text=header_text, style="SidebarHeading.TLabel").pack(anchor="w")
        if payload.kind == "event":
            ttk.Label(frame, text=payload.title, font=("Segoe UI", 11, "bold"), wraplength=280).pack(anchor="w", pady=(4, 0))

        self._time_label = ttk.Label(frame, text=self._derive_time_text(payload), foreground=self.theme.text_secondary)
        self._time_label.pack(anchor="w", pady=(2, 6))
        body_text = self._derive_body_text(payload)
        if body_text:
            self._body_label = ttk.Label(frame, text=body_text, wraplength=280, foreground=self.theme.notification_body)
            self._body_label.pack(anchor="w")

        self._dismiss_button = ttk.Button(frame, text="Dismiss", command=self.dismiss)
        self._dismiss_button.pack(anchor="e", pady=(10, 0))
        self.after(1000 * 15, self.dismiss)

    def _derive_time_text(self, payload: NotificationPayload) -> str:
        if payload.kind == "event" and (payload.body or "").startswith("All day"):
            return "All day"
        return utils.format_time(payload.occurs_at)

    def _derive_body_text(self, payload: NotificationPayload) -> str:
        if payload.kind == "event":
            body = payload.body or ""
            if body.startswith("All day"):
                parts = body.split(" - ", 1)
                return parts[1] if len(parts) > 1 else ""
            parts = body.split(" - ", 1)
            if len(parts) > 1:
                return parts[1]
            return parts[0]
        return payload.body or ""

    def dismiss(self) -> None:
        if self.winfo_exists():
            self.destroy()
            self.master.remove_notification(self)

    def apply_theme(self, theme: ThemePalette) -> None:
        self.theme = theme
        self.configure(bg=self.theme.notification_bg)
        if self._time_label:
            self._time_label.configure(foreground=self.theme.text_secondary)
        if self._body_label:
            self._body_label.configure(foreground=self.theme.notification_body)


def _ensure_installed_binary(data_root: Path) -> None:
    if not getattr(sys, "frozen", False):
        return

    expected_exe = data_root / "PersonalAssistant.exe"
    current_exe = Path(sys.executable).resolve()
    version_file = data_root / "app_version.txt"

    def _write_version_file() -> None:
        try:
            version_file.write_text(__version__, encoding="utf-8")
        except Exception:
            pass

    def _copy_icon(source: Path) -> None:
        icon_source = source.with_name("personal_assistant.ico")
        if not icon_source.exists():
            return
        icon_target = data_root / "personal_assistant.ico"
        try:
            icon_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(icon_source, icon_target)
        except Exception:
            pass

    if current_exe == expected_exe:
        _write_version_file()
        icon_path = data_root / "personal_assistant.ico"
        if not icon_path.exists():
            _copy_icon(current_exe)
        return

    expected_exe.parent.mkdir(parents=True, exist_ok=True)

    installed_version_key: Optional[tuple[int, ...]] = None
    if version_file.exists():
        try:
            installed_version_key = _parse_version(version_file.read_text(encoding="utf-8"))
        except Exception:
            installed_version_key = None

    current_version_key = _parse_version(__version__)

    def _launch_installed() -> None:
        args = sys.argv[1:]
        subprocess.Popen([str(expected_exe), *args])
        sys.exit(0)

    if expected_exe.exists():
        if installed_version_key and installed_version_key >= current_version_key:
            _launch_installed()
            return
        if not installed_version_key:
            try:
                if expected_exe.stat().st_mtime >= current_exe.stat().st_mtime:
                    _launch_installed()
                    return
            except OSError:
                _launch_installed()
                return

    try:
        shutil.copy2(current_exe, expected_exe)
    except Exception:
        if expected_exe.exists():
            _launch_installed()
        return

    _copy_icon(current_exe)
    _write_version_file()
    _launch_installed()


def _parse_version(value: str) -> tuple[int, ...]:
    cleaned = (value or "").strip().lower()
    if cleaned.startswith("v"):
        cleaned = cleaned[1:]
    tokens: list[int] = []
    for part in cleaned.replace("-", ".").split("."):
        part = part.strip()
        if not part:
            continue
        digits = "".join(ch for ch in part if ch.isdigit())
        if digits:
            tokens.append(int(digits))
    return tuple(tokens) if tokens else (0,)


def _migrate_legacy_data(data_root: Path) -> None:
    legacy_root = legacy_project_root()
    legacy_db = legacy_root / "assistant_app" / "assistant.db"
    target_db = data_root / "assistant.db"
    if legacy_db.exists() and not target_db.exists():
        target_db.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_db, target_db)

    legacy_runs = legacy_root / "data" / "email_runs"
    target_runs = data_root / "email_runs"
    if legacy_runs.exists() and not target_runs.exists():
        try:
            shutil.copytree(legacy_runs, target_runs)
        except FileExistsError:
            pass
        else:
            _rewrite_email_run_paths(target_runs)


def _rewrite_email_run_paths(base_dir: Path) -> None:
    try:
        import yaml  # type: ignore
    except ImportError:
        return
    for run_dir in base_dir.iterdir():
        if not run_dir.is_dir():
            continue
        config_path = run_dir / "config.yaml"
        if not config_path.exists():
            continue
        try:
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        shard_path = (run_dir / "shards").resolve()
        summaries_path = (run_dir / "summaries").resolve()
        data["shard_path"] = str(shard_path)
        data["summaries_path"] = str(summaries_path)
        try:
            config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        except Exception:
            continue


def main() -> None:
    data_root = ensure_user_data_dir()
    _ensure_installed_binary(data_root)
    _migrate_legacy_data(data_root)
    settings_path = data_root / "settings.json"
    settings = load_settings(settings_path)
    db_path = data_root / "assistant.db"
    app = PersonalAssistantApp(db_path, data_root, settings, settings_path)
    app.mainloop()


__all__ = ["main", "PersonalAssistantApp"]







