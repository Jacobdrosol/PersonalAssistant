from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Callable, List, Optional
import tkinter as tk
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
)
from .version import __version__
from . import updater
from .theme import ThemePalette, get_theme, THEMES


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
        self._special_feature_keys = sanitize_special_feature_keys(self.settings.special_features)
        if self._special_feature_keys != self.settings.special_features:
            self.settings.special_features = self._special_feature_keys
            save_settings(self.settings_path, self.settings)
        self._special_tab_cache = {}
        self.theme_name = settings.theme if settings.theme in THEMES else "dark"
        self.theme: ThemePalette = get_theme(self.theme_name)
        self._icon_path = self._ensure_icon_file()
        self.db = Database(db_path)
        self.system_notifier = SystemNotifier()
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

        manage_shortcuts = self._should_manage_shortcut()
        self.settings_tab_frame = ttk.Frame(self.notebook, style="TFrame")
        self.settings_tab = SettingsTab(
            self.settings_tab_frame,
            desktop_enabled=self.settings.desktop_shortcut and manage_shortcuts,
            start_menu_enabled=self.settings.start_menu_shortcut and manage_shortcuts,
            daily_notifications_enabled=self.settings.daily_update_notifications,
            daily_start=self.settings.daily_update_start,
            daily_end=self.settings.daily_update_end,
            on_setting_toggle=self._handle_setting_toggle,
            on_hours_change=self._handle_daily_hours_change,
            on_theme_change=self._handle_theme_change,
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

        self.calendar_tab = CalendarTab(self.notebook, self.db, self.theme)
        self.scrum_tab = ScrumTab(self.notebook, self.db, self.theme)
        self.log_tab = LogTab(self.notebook, self.db)
        self.contact_tab = ContactTab(self.notebook, self.data_root, app_version=__version__)

        self._core_tabs = {
            "calendar": (self.calendar_tab, "Production Calendar"),
            "log": (self.log_tab, "Daily Update Log"),
            "scrum": (self.scrum_tab, "Tasks Board"),
            "contact": (self.contact_tab, "Contact Support"),
        }
        self._base_tab_order = ["calendar", "log", "scrum", "contact"]
        self._sync_notebook_tabs()

        self._last_notebook_tab = self.notebook.select()
        self._settings_visible = False
        self.notebook.bind("<<NotebookTabChanged>>", self._record_last_notebook_tab)
        self.notebook.bind("<Configure>", self._position_settings_button)
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

        self._tab_buttons: dict[tk.Misc, ttk.Button] = {}
        self._tab_order_widgets: list[tk.Misc] = []

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

    def _sync_tab_buttons(self, tabs: list[tuple[tk.Misc, str]]) -> None:
        if not hasattr(self, "tabs_inner"):
            return
        for widget in self.tabs_inner.winfo_children():
            widget.destroy()
        self._tab_buttons.clear()
        self._tab_order_widgets = []
        for widget, label in tabs:
            btn = ttk.Button(
                self.tabs_inner,
                text=label,
                style="TabBar.TButton",
                command=lambda target=widget: self._select_tab(target),
            )
            btn.pack(side=tk.LEFT, padx=(0, 6), pady=(1, 0))
            self._tab_buttons[widget] = btn
            self._tab_order_widgets.append(widget)
        self._update_tab_button_styles()
        self.after(0, self._scroll_active_tab_into_view)

    def _select_tab(self, widget: tk.Misc) -> None:
        try:
            self.notebook.select(widget)
        except tk.TclError:
            return
        self._update_tab_button_styles()
        self._scroll_active_tab_into_view()

    def _update_tab_button_styles(self) -> None:
        if not hasattr(self, "_tab_buttons"):
            return
        try:
            current_id = self.notebook.select()
            current_widget = self.nametowidget(current_id) if current_id else None
        except tk.TclError:
            current_widget = None
        for widget, btn in self._tab_buttons.items():
            style = "TabBarActive.TButton" if widget == current_widget else "TabBar.TButton"
            btn.configure(style=style)

    def _scroll_active_tab_into_view(self) -> None:
        if not hasattr(self, "tabs_canvas"):
            return
        try:
            current_id = self.notebook.select()
            current_widget = self.nametowidget(current_id) if current_id else None
        except tk.TclError:
            current_widget = None
        if current_widget is None:
            return
        button = self._tab_buttons.get(current_widget)
        if button is None:
            return
        self.tabs_canvas.update_idletasks()
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

    def _get_special_tab(self, key: str) -> Optional[tuple[tk.Misc, str]]:
        feature = SPECIAL_FEATURES.get(key)
        if not feature or not feature.is_tab_feature():
            return None
        widget = self._special_tab_cache.get(key)
        if widget is None:
            widget = feature.tab_builder(self)
            self._special_tab_cache[key] = widget
        return widget, feature.tab_label or feature.title

    def _sync_notebook_tabs(self) -> None:
        desired_keys = self._compute_tab_order()
        desired_tabs: list[tuple[tk.Misc, str]] = []
        for key in desired_keys:
            if key in self._core_tabs:
                desired_tabs.append(self._core_tabs[key])
                continue
            special = self._get_special_tab(key)
            if special:
                desired_tabs.append(special)

        try:
            current_id = self.notebook.select()
            current_widget = self.nametowidget(current_id) if current_id else None
        except tk.TclError:
            current_widget = None

        for tab_id in self.notebook.tabs():
            try:
                self.notebook.forget(tab_id)
            except tk.TclError:
                continue

        desired_widgets = [widget for widget, _ in desired_tabs]
        desired_set = set(desired_widgets)

        for widget, label in desired_tabs:
            try:
                self.notebook.add(widget, text=label)
            except tk.TclError:
                continue

        if current_widget in desired_set:
            try:
                self.notebook.select(current_widget)
            except tk.TclError:
                pass
        elif desired_tabs:
            try:
                self.notebook.select(desired_tabs[0][0])
            except tk.TclError:
                pass

        try:
            self._last_notebook_tab = self.notebook.select()
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
            self.settings_tab.update_shortcut_state("desktop", False)
            self.settings_tab.update_shortcut_state("start_menu", False)
            return
        icon = self._icon_path or self._ensure_icon_file()
        if icon is not None and icon.exists():
            self._icon_path = icon
            self._apply_window_icon()
        target = Path(sys.executable).resolve()
        desktop_exists = desktop_shortcut_exists()
        start_exists = start_menu_shortcut_exists()
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
        self.settings.desktop_shortcut = desktop_exists
        self.settings.start_menu_shortcut = start_exists
        self.settings_tab.update_shortcut_state("desktop", desktop_exists)
        self.settings_tab.update_shortcut_state("start_menu", start_exists)
        save_settings(self.settings_path, self.settings)

    def _create_shortcut(self, kind: str, target: Path) -> bool:
        icon = self._icon_path or self._ensure_icon_file()
        if icon is not None and icon.exists():
            self._icon_path = icon
            self._apply_window_icon()
        label = "Desktop Shortcut" if kind == "desktop" else "Start Menu Shortcut"
        if icon is None or not icon.exists():
            messagebox.showerror(
                label,
                "Unable to locate the application icon for the shortcut.",
                parent=self,
            )
            return False
        if kind == "desktop":
            success = create_desktop_shortcut(target, icon)
        else:
            success = create_start_menu_shortcut(target, icon)
        if not success:
            messagebox.showerror(label, f"Unable to create the {label.lower()}.", parent=self)
        return success

    def _remove_shortcut(self, kind: str) -> bool:
        if kind == "desktop":
            return remove_desktop_shortcut()
        return remove_start_menu_shortcut()

    def _handle_setting_toggle(self, kind: str, enabled: bool) -> None:
        if kind == "daily_notifications":
            self.settings.daily_update_notifications = bool(enabled)
            self.notification_manager.set_standing_reminders_enabled(bool(enabled))
            if enabled:
                start_time = self._coerce_time_to_dt(self.settings.daily_update_start, "08:00")
                end_time = self._coerce_time_to_dt(self.settings.daily_update_end, "17:00")
                self.notification_manager.configure_daily_log_hours(start_time, end_time)
            self.settings_tab.update_daily_notification_state(bool(enabled))
            save_settings(self.settings_path, self.settings)
            return

        label = "Desktop" if kind == "desktop" else "Start Menu"
        if not self._should_manage_shortcut():
            messagebox.showinfo(
                f"{label} Shortcut",
                f"{label} shortcuts are only available in the packaged application.",
                parent=self,
            )
            self.settings_tab.update_shortcut_state(kind, False)
            return
        target = Path(sys.executable).resolve()
        if enabled:
            success = self._create_shortcut(kind, target)
            if success:
                if kind == "desktop":
                    self.settings.desktop_shortcut = True
                else:
                    self.settings.start_menu_shortcut = True
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
                else:
                    self.settings.start_menu_shortcut = True
            else:
                if kind == "desktop":
                    self.settings.desktop_shortcut = False
                else:
                    self.settings.start_menu_shortcut = False
        self.settings_tab.update_shortcut_state("desktop", desktop_shortcut_exists())
        self.settings_tab.update_shortcut_state("start_menu", start_menu_shortcut_exists())
        save_settings(self.settings_path, self.settings)

    def _handle_daily_hours_change(self, start_text: str, end_text: str) -> None:
        try:
            start_time = self._parse_time_string(start_text)
            end_time = self._parse_time_string(end_text)
        except ValueError as exc:
            messagebox.showerror("Daily Update Log Reminders", str(exc), parent=self)
            self.settings_tab.update_daily_hours(
                self.settings.daily_update_start,
                self.settings.daily_update_end,
            )
            return
        self.settings.daily_update_start = self._format_time_storage(start_time)
        self.settings.daily_update_end = self._format_time_storage(end_time)
        save_settings(self.settings_path, self.settings)
        self.notification_manager.configure_daily_log_hours(start_time, end_time)
        self.settings_tab.update_daily_hours(
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

    def _apply_special_feature_keys(self, keys: list[str]) -> None:
        cleaned = sanitize_special_feature_keys(keys)
        if cleaned != self._special_feature_keys:
            self._special_feature_keys = cleaned
            self.settings.special_features = cleaned
            save_settings(self.settings_path, self.settings)
        self._sync_notebook_tabs()
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
        self.settings_tab_frame.configure(style="TFrame")
        self.settings_tab.update_theme_selection(self.theme_name)
        if hasattr(self, "tabs_canvas"):
            self.tabs_canvas.configure(bg=self.theme.surface_bg)
        for tab, _label in self._core_tabs.values():
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

    def _parse_time_string(self, value: str) -> dt_time:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Time value cannot be blank.")
        normalized = cleaned.upper()
        for fmt in ("%I:%M %p", "%I %p", "%I:%M%p", "%I%p"):
            try:
                return datetime.strptime(normalized, fmt).time().replace(second=0, microsecond=0)
            except ValueError:
                continue
        for fmt in ("%H:%M", "%H"):
            try:
                return datetime.strptime(cleaned, fmt).time().replace(second=0, microsecond=0)
            except ValueError:
                continue
        raise ValueError(f"Invalid time value '{value}'. Use formats like 8:00 AM or 17:00.")

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
    def show_notification(self, payload: NotificationPayload) -> None:
        body_text = payload.body.strip() if payload.body else ""
        fallback = payload.occurs_at.strftime("%I:%M %p").lstrip("0")
        self.system_notifier.notify(payload.title, body_text or fallback)
        window = NotificationWindow(self, payload, self.theme)
        self.notifications.append(window)
        self._rearrange_notifications()

    def _rearrange_notifications(self) -> None:
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        padding = 20
        window_width = 320
        window_height = 140

        for index, window in enumerate(list(self.notifications)):
            if not window.winfo_exists():
                self.notifications.remove(window)
                continue
            x = screen_width - window_width - padding
            y = screen_height - (index + 1) * (window_height + 10) - padding
            window.geometry(f"{window_width}x{window_height}+{x}+{y}")

    def _position_settings_button(self, event: Optional[tk.Event] = None) -> None:
        self._place_settings_overlay()
        self._update_tab_scroll_controls()

    def _place_settings_overlay(self) -> None:
        if not self._settings_visible:
            return
        try:
            self.notebook.update_idletasks()
            self.settings_tab_frame.update_idletasks()
        except tk.TclError:
            pass
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
        self.settings_tab_frame.place_forget()
        self._settings_visible = False
        self._sync_settings_button_state()
        self._position_settings_button()
        try:
            self.settings_button.state(["!pressed"])
        except tk.TclError:
            pass
        try:
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

        ttk.Button(frame, text="Dismiss", command=self.dismiss).pack(anchor="e", pady=(10, 0))
        self.after(1000 * 15, self.dismiss)

    def _derive_time_text(self, payload: NotificationPayload) -> str:
        if payload.kind == "event" and (payload.body or "").startswith("All day"):
            return "All day"
        return payload.occurs_at.strftime("%I:%M %p").lstrip("0")

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







