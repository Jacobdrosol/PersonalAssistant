from __future__ import annotations

import tkinter as tk
from datetime import date, datetime
import calendar as cal

from tkinter import ttk
from typing import Callable, Optional

from .settings_store import (
    DEFAULT_JIRA_BASE_URL,
    JiraSettings,
    normalize_jira_base_url,
)
from .special_features import SpecialFeature


class SettingsTab(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        *,
        desktop_enabled: bool,
        start_menu_enabled: bool,
        daily_notifications_enabled: bool,
        daily_start: str,
        daily_end: str,
        on_setting_toggle: Callable[[str, bool], None],
        on_hours_change: Callable[[str, str], None],
        on_theme_change: Callable[[str], None],
        on_jira_settings_change: Callable[[JiraSettings], None],
        on_jira_test_connection: Callable[[JiraSettings], None],
        special_features: list[SpecialFeature],
        on_special_code_submit: Callable[[str], None],
        on_special_feature_disable: Callable[[str], None],
        show_jira_section: bool,
        theme_name: str,
        app_version: str,
        jira_settings: JiraSettings,
    ) -> None:
        super().__init__(master, padding=(20, 20))
        self._callback = on_setting_toggle
        self._hours_callback = on_hours_change
        self._theme_callback = on_theme_change
        self._jira_settings_callback = on_jira_settings_change
        self._jira_test_callback = on_jira_test_connection
        self._special_code_callback = on_special_code_submit
        self._special_disable_callback = on_special_feature_disable
        self.desktop_var = tk.BooleanVar(value=desktop_enabled)
        self.start_menu_var = tk.BooleanVar(value=start_menu_enabled)
        self.daily_notifications_var = tk.BooleanVar(value=daily_notifications_enabled)
        self.daily_start_var = tk.StringVar(value=daily_start)
        self.daily_end_var = tk.StringVar(value=daily_end)
        self.theme_var = tk.StringVar(value=theme_name if theme_name in {"dark", "light"} else "dark")
        self.jira_use_default_var = tk.BooleanVar(value=jira_settings.use_default_base)
        self.jira_base_var = tk.StringVar(value=jira_settings.base_url or DEFAULT_JIRA_BASE_URL)
        self.jira_email_var = tk.StringVar(value=jira_settings.email)
        self.jira_token_var = tk.StringVar(value=jira_settings.api_token)
        self.jira_status_var = tk.StringVar(value="")
        self._jira_custom_base_value = "" if jira_settings.use_default_base else jira_settings.base_url
        self._jira_status_label: Optional[ttk.Label] = None
        self._token_date_picker: Optional["InlineDatePicker"] = None
        self.jira_token_expires_var = tk.StringVar(value=jira_settings.token_expires)
        self.special_code_var = tk.StringVar(value="")
        self.special_status_var = tk.StringVar(value="")
        self._special_status_label: Optional[ttk.Label] = None
        self._special_features_container: Optional[ttk.Frame] = None
        self._jira_section: Optional[ttk.Frame] = None

        ttk.Label(self, text="Settings", style="SidebarHeading.TLabel").pack(anchor="w")
        body = ttk.Frame(self, padding=(0, 12))
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Checkbutton(
            body,
            text="Show desktop shortcut",
            variable=self.desktop_var,
            command=lambda: self._on_setting_toggled("desktop"),
        ).pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(
            body,
            text="Show Start Menu shortcut",
            variable=self.start_menu_var,
            command=lambda: self._on_setting_toggled("start_menu"),
        ).pack(anchor="w", pady=(6, 0))
        reminders_check = ttk.Checkbutton(
            body,
            text="Daily Update Log reminders",
            variable=self.daily_notifications_var,
            command=lambda: self._on_setting_toggled("daily_notifications"),
        )
        reminders_check.pack(anchor="w", pady=(12, 0))

        self.daily_hours_frame = ttk.Frame(body, padding=(24, 6))
        hours_row = ttk.Frame(self.daily_hours_frame)
        hours_row.pack(anchor="w", pady=(2, 2))
        ttk.Label(hours_row, text="Work hours:", style="SidebarHeading.TLabel").pack(side=tk.LEFT)
        ttk.Label(hours_row, text="Start").pack(side=tk.LEFT, padx=(12, 2))
        self.daily_start_entry = ttk.Entry(hours_row, textvariable=self.daily_start_var, width=10)
        self.daily_start_entry.pack(side=tk.LEFT)
        ttk.Label(hours_row, text="End").pack(side=tk.LEFT, padx=(12, 2))
        self.daily_end_entry = ttk.Entry(hours_row, textvariable=self.daily_end_var, width=10)
        self.daily_end_entry.pack(side=tk.LEFT)
        for widget in (self.daily_start_entry, self.daily_end_entry):
            widget.bind("<FocusOut>", self._on_hours_changed)
            widget.bind("<Return>", self._on_hours_changed)
        ttk.Label(
            self.daily_hours_frame,
            text='Hourly notifications to update your "Daily Update Log" and half-hour notifications at the end of your workday to send it.',
            wraplength=360,
        ).pack(anchor="w", pady=(4, 0))
        self._update_daily_hours_visibility()

        theme_frame = ttk.Frame(body, padding=(0, 12))
        theme_frame.pack(fill=tk.X, anchor="w")
        ttk.Label(theme_frame, text="Theme", style="SidebarHeading.TLabel").pack(anchor="w")
        theme_options = ttk.Frame(theme_frame, padding=(24, 4))
        theme_options.pack(anchor="w", fill=tk.X)
        ttk.Radiobutton(
            theme_options,
            text="Dark",
            value="dark",
            variable=self.theme_var,
            command=self._on_theme_changed,
        ).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Radiobutton(
            theme_options,
            text="Light",
            value="light",
            variable=self.theme_var,
            command=self._on_theme_changed,
        ).pack(side=tk.LEFT)

        self._build_special_features_section(body, special_features)
        self._build_jira_section(body, show_jira_section)

        footer = ttk.Frame(self)
        footer.pack(fill=tk.BOTH, expand=True)
        footer.grid_columnconfigure(0, weight=1)
        footer.grid_rowconfigure(1, weight=1)

        version_label = ttk.Label(footer, text=f"Version: {app_version}")
        version_label.grid(row=1, column=0, sticky="se", padx=4, pady=4)

    def _on_setting_toggled(self, kind: str) -> None:
        if kind == "desktop":
            value = bool(self.desktop_var.get())
        elif kind == "start_menu":
            value = bool(self.start_menu_var.get())
        else:
            value = bool(self.daily_notifications_var.get())
            self._update_daily_hours_visibility()
        self._callback(kind, value)

    def update_shortcut_state(self, kind: str, enabled: bool) -> None:
        if kind == "desktop":
            self.desktop_var.set(enabled)
        else:
            self.start_menu_var.set(enabled)

    def update_daily_notification_state(self, enabled: bool) -> None:
        self.daily_notifications_var.set(enabled)
        self._update_daily_hours_visibility()

    def update_daily_hours(self, start: str, end: str) -> None:
        self.daily_start_var.set(start)
        self.daily_end_var.set(end)

    def update_theme_selection(self, theme_name: str) -> None:
        self.theme_var.set(theme_name if theme_name in {"dark", "light"} else "dark")

    def _on_hours_changed(self, _event: object) -> None:
        if not bool(self.daily_notifications_var.get()):
            return
        if self._hours_callback:
            self._hours_callback(self.daily_start_var.get().strip(), self.daily_end_var.get().strip())

    def _on_theme_changed(self) -> None:
        if self._theme_callback:
            self._theme_callback(self.theme_var.get())

    def _update_daily_hours_visibility(self) -> None:
        if bool(self.daily_notifications_var.get()):
            self.daily_hours_frame.pack(anchor="w", fill=tk.X, padx=(12, 0))
        else:
            self.daily_hours_frame.pack_forget()

    # ------------------------------------------------------------------ Special features helpers
    def _build_special_features_section(self, parent: ttk.Frame, features: list[SpecialFeature]) -> None:
        section = ttk.Frame(parent, padding=(0, 12))
        section.pack(fill=tk.X, anchor="w")
        ttk.Label(section, text="Special Features", style="SidebarHeading.TLabel").pack(anchor="w")
        ttk.Label(
            section,
            text="Enter the special features unlock code to enable additional features in the application.",
            wraplength=420,
        ).pack(anchor="w", pady=(2, 8))

        code_row = ttk.Frame(section, padding=(24, 0))
        code_row.pack(fill=tk.X, anchor="w")
        ttk.Label(code_row, text="Unlock code").pack(side=tk.LEFT)
        code_entry = ttk.Entry(code_row, textvariable=self.special_code_var, width=16)
        code_entry.pack(side=tk.LEFT, padx=(8, 6))
        code_entry.bind("<Return>", lambda _event: self._submit_special_code())
        ttk.Button(code_row, text="Unlock", command=self._submit_special_code).pack(side=tk.LEFT)

        status_label = ttk.Label(section, textvariable=self.special_status_var)
        status_label.pack(anchor="w", padx=24, pady=(4, 0))
        self._special_status_label = status_label

        features_frame = ttk.Frame(section, padding=(24, 8))
        features_frame.pack(fill=tk.X, anchor="w")
        self._special_features_container = features_frame
        self._render_special_features(features)

    def _submit_special_code(self) -> None:
        code = self.special_code_var.get().strip()
        if not code:
            self.update_special_code_status("Enter a code to unlock features.", False)
            return
        if self._special_code_callback:
            self._special_code_callback(code)

    def _render_special_features(self, features: list[SpecialFeature]) -> None:
        container = self._special_features_container
        if container is None:
            return
        for widget in container.winfo_children():
            widget.destroy()

        if not features:
            ttk.Label(container, text="No special features enabled yet.", wraplength=420).pack(anchor="w")
            return

        for feature in features:
            row = ttk.Frame(container)
            row.pack(fill=tk.X, anchor="w", pady=(4, 6))

            header = ttk.Frame(row)
            header.pack(fill=tk.X, anchor="w")
            ttk.Label(header, text=feature.title, font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
            ttk.Button(
                header,
                text="Remove",
                command=lambda key=feature.key: self._disable_special_feature(key),
                width=10,
            ).pack(side=tk.RIGHT)
            ttk.Label(row, text=feature.description, wraplength=420).pack(anchor="w", pady=(2, 0))

    def _disable_special_feature(self, key: str) -> None:
        if self._special_disable_callback:
            self._special_disable_callback(key)

    def update_special_features(self, features: list[SpecialFeature]) -> None:
        self._render_special_features(features)

    def update_special_code_status(self, message: str, success: Optional[bool] = None) -> None:
        self.special_status_var.set(message)
        if self._special_status_label is None:
            return
        if success is True:
            self._special_status_label.configure(foreground="#4CAF50")
        elif success is False:
            self._special_status_label.configure(foreground="#F36C6C")
        else:
            self._special_status_label.configure(foreground="")

    def clear_special_code_entry(self) -> None:
        self.special_code_var.set("")

    # ------------------------------------------------------------------ JIRA settings helpers
    def _build_jira_section(self, parent: ttk.Frame, show_section: bool) -> None:
        section = ttk.Frame(parent, padding=(0, 12))
        self._jira_section = section
        if show_section:
            section.pack(fill=tk.X, anchor="w")
        ttk.Label(section, text="JIRA Integration", style="SidebarHeading.TLabel").pack(anchor="w")
        ttk.Label(
            section,
            text="Connect your Jira account so the assistant can pull assigned and watched issues.",
            wraplength=420,
        ).pack(anchor="w", pady=(2, 8))

        base_mode = ttk.Frame(section, padding=(24, 0))
        base_mode.pack(fill=tk.X, anchor="w")
        ttk.Radiobutton(
            base_mode,
            text=f"Use CDS Global Jira ({DEFAULT_JIRA_BASE_URL})",
            variable=self.jira_use_default_var,
            value=True,
            command=self._update_jira_base_state,
        ).pack(anchor="w")
        ttk.Radiobutton(
            base_mode,
            text="Use custom Jira URL",
            variable=self.jira_use_default_var,
            value=False,
            command=self._update_jira_base_state,
        ).pack(anchor="w")

        base_row = ttk.Frame(section, padding=(24, 6))
        base_row.pack(fill=tk.X, anchor="w")
        ttk.Label(base_row, text="Base URL").grid(row=0, column=0, sticky="w")
        self.jira_base_entry = ttk.Entry(base_row, textvariable=self.jira_base_var, width=44)
        self.jira_base_entry.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        base_row.columnconfigure(0, weight=1)

        credentials_frame = ttk.Frame(section, padding=(24, 6))
        credentials_frame.pack(fill=tk.X, anchor="w")
        ttk.Label(credentials_frame, text="Jira Email").grid(row=0, column=0, sticky="w")
        ttk.Entry(credentials_frame, textvariable=self.jira_email_var, width=44).grid(
            row=1, column=0, sticky="ew", pady=(2, 6)
        )
        ttk.Label(credentials_frame, text="API Token").grid(row=2, column=0, sticky="w")
        ttk.Entry(credentials_frame, textvariable=self.jira_token_var, width=44, show="*").grid(
            row=3, column=0, sticky="ew", pady=(2, 0)
        )
        credentials_frame.columnconfigure(0, weight=1)
        ttk.Label(
            credentials_frame,
            text="Tokens may last up to 1 year. Enter the expiration date so the assistant can remind you.",
            wraplength=420,
        ).grid(row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Label(credentials_frame, text="Token Expiration (YYYY-MM-DD)").grid(row=5, column=0, sticky="w", pady=(8, 0))
        expiry_row = ttk.Frame(credentials_frame)
        expiry_row.grid(row=6, column=0, sticky="ew", pady=(2, 0))
        expiry_row.columnconfigure(0, weight=1)
        self.jira_token_expiry_entry = ttk.Entry(expiry_row, textvariable=self.jira_token_expires_var, width=32)
        self.jira_token_expiry_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(expiry_row, text="Pick", width=6, command=self._open_token_date_picker).grid(
            row=0, column=1, padx=(6, 0)
        )
        ttk.Label(
            credentials_frame,
            text="Create tokens via Atlassian account → Security → API tokens.",
            wraplength=420,
        ).grid(row=7, column=0, sticky="w", pady=(6, 0))

        buttons = ttk.Frame(section, padding=(24, 8))
        buttons.pack(anchor="w")
        ttk.Button(buttons, text="Save Jira Settings", command=self._save_jira_settings).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Test Connection", command=self._test_jira_connection).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        status_label = ttk.Label(section, textvariable=self.jira_status_var)
        status_label.pack(anchor="w", padx=24, pady=(4, 0))
        self._jira_status_label = status_label
        self._update_jira_base_state()

    def update_jira_section_visibility(self, enabled: bool) -> None:
        if self._jira_section is None:
            return
        if enabled:
            if not self._jira_section.winfo_ismapped():
                self._jira_section.pack(fill=tk.X, anchor="w")
        else:
            if self._jira_section.winfo_ismapped():
                self._jira_section.pack_forget()

    def _collect_jira_payload(self) -> JiraSettings:
        base_url = self.jira_base_var.get().strip()
        use_default = bool(self.jira_use_default_var.get())
        if use_default:
            base_url = DEFAULT_JIRA_BASE_URL
        else:
            base_url = normalize_jira_base_url(base_url)
            if base_url == DEFAULT_JIRA_BASE_URL:
                use_default = True
        return JiraSettings(
            base_url=base_url,
            use_default_base=use_default,
            email=self.jira_email_var.get().strip(),
            api_token=self.jira_token_var.get().strip(),
            token_expires=self.jira_token_expires_var.get().strip(),
        )

    def _save_jira_settings(self) -> None:
        payload = self._collect_jira_payload()
        if self._jira_settings_callback:
            self._jira_settings_callback(payload)
        self.update_jira_status("Jira settings saved.", True)

    def _test_jira_connection(self) -> None:
        payload = self._collect_jira_payload()
        if self._jira_settings_callback:
            self._jira_settings_callback(payload)
        if self._jira_test_callback:
            self._jira_test_callback(payload)

    def _update_jira_base_state(self) -> None:
        use_default = bool(self.jira_use_default_var.get())
        is_entry_normal = self.jira_base_entry.cget("state") == "normal"
        if use_default:
            if is_entry_normal:
                self._jira_custom_base_value = self.jira_base_var.get().strip()
            self.jira_base_var.set(DEFAULT_JIRA_BASE_URL)
            self.jira_base_entry.configure(state="disabled")
        else:
            custom_value = self._jira_custom_base_value or ""
            self.jira_base_var.set(custom_value or DEFAULT_JIRA_BASE_URL)
            self.jira_base_entry.configure(state="normal")

    def update_jira_status(self, message: str, success: Optional[bool] = None) -> None:
        self.jira_status_var.set(message)
        if self._jira_status_label is None:
            return
        if success is True:
            self._jira_status_label.configure(foreground="#4CAF50")
        elif success is False:
            self._jira_status_label.configure(foreground="#F36C6C")
        else:
            self._jira_status_label.configure(foreground="")

    def _open_token_date_picker(self) -> None:
        if self._token_date_picker is not None:
            return
        current = None
        raw = self.jira_token_expires_var.get().strip()
        if raw:
            try:
                current = datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                current = None
        picker = InlineDatePicker(
            self,
            current=current,
            on_select=self._apply_token_expiration,
            on_close=self._close_token_date_picker,
        )
        self._token_date_picker = picker

    def _apply_token_expiration(self, selected: Optional[date]) -> None:
        self.jira_token_expiry_entry.delete(0, tk.END)
        if selected:
            self.jira_token_expiry_entry.insert(0, selected.isoformat())
            self.jira_token_expires_var.set(selected.isoformat())
        else:
            self.jira_token_expires_var.set("")
        self._close_token_date_picker()

    def _close_token_date_picker(self) -> None:
        if self._token_date_picker is not None:
            self._token_date_picker.destroy()
            self._token_date_picker = None


class InlineDatePicker(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        *,
        current: Optional[date],
        on_select: Callable[[Optional[date]], None],
        on_close: Callable[[], None],
    ) -> None:
        super().__init__(master)
        self.title("Select Date")
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)
        self._on_select = on_select
        self._on_close = on_close
        try:
            bg_color = master.cget("background")
        except tk.TclError:
            bg_color = "#1d1e2c"
        self.configure(bg=bg_color)
        self.protocol("WM_DELETE_WINDOW", self._close)

        today = date.today()
        self._today = today
        if current is None:
            current = today
        self._current_month = date(current.year, current.month, 1)

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)

        header = ttk.Frame(frame)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Button(header, text="◀", width=3, command=lambda: self._shift_month(-1)).pack(side=tk.LEFT)
        self._month_label = ttk.Label(header, text="")
        self._month_label.pack(side=tk.LEFT, expand=True)
        ttk.Button(header, text="▶", width=3, command=lambda: self._shift_month(1)).pack(side=tk.RIGHT)

        self._calendar_frame = ttk.Frame(frame)
        self._calendar_frame.grid(row=1, column=0, pady=(8, 0))

        actions = ttk.Frame(frame)
        actions.grid(row=2, column=0, sticky="e", pady=(10, 0))
        ttk.Button(actions, text="Clear", command=lambda: self._finish(None)).pack(side=tk.LEFT)
        ttk.Button(actions, text="Today", command=lambda: self._finish(today)).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(actions, text="Close", command=self._close).pack(side=tk.LEFT, padx=(6, 0))

        self._render_calendar()

    def _shift_month(self, delta: int) -> None:
        month = self._current_month.month - 1 + delta
        year = self._current_month.year + month // 12
        month = month % 12 + 1
        self._current_month = date(year, month, 1)
        self._render_calendar()

    def _render_calendar(self) -> None:
        for widget in self._calendar_frame.winfo_children():
            widget.destroy()
        self._month_label.configure(text=self._current_month.strftime("%B %Y"))
        cal_obj = cal.Calendar(firstweekday=6)
        weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        for idx, name in enumerate(weekdays):
            ttk.Label(self._calendar_frame, text=name, width=4, anchor="center").grid(row=0, column=idx, pady=(0, 4))

        for row, week in enumerate(cal_obj.monthdatescalendar(self._current_month.year, self._current_month.month), start=1):
            for col, day in enumerate(week):
                state = tk.NORMAL if day.month == self._current_month.month else tk.DISABLED
                btn = ttk.Button(
                    self._calendar_frame,
                    text=f"{day.day:02d}",
                    width=4,
                    state=state,
                    command=lambda d=day: self._finish(d),
                )
                btn.grid(row=row, column=col, padx=1, pady=1)

    def _finish(self, value: Optional[date]) -> None:
        self._on_select(value)

    def _close(self) -> None:
        self._on_close()
