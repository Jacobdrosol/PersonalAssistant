from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable


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
        theme_name: str,
        app_version: str,
    ) -> None:
        super().__init__(master, padding=(20, 20))
        self._callback = on_setting_toggle
        self._hours_callback = on_hours_change
        self._theme_callback = on_theme_change
        self.desktop_var = tk.BooleanVar(value=desktop_enabled)
        self.start_menu_var = tk.BooleanVar(value=start_menu_enabled)
        self.daily_notifications_var = tk.BooleanVar(value=daily_notifications_enabled)
        self.daily_start_var = tk.StringVar(value=daily_start)
        self.daily_end_var = tk.StringVar(value=daily_end)
        self.theme_var = tk.StringVar(value=theme_name if theme_name in {"dark", "light"} else "dark")

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
