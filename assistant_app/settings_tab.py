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
        on_shortcut_toggle: Callable[[str, bool], None],
    ) -> None:
        super().__init__(master, padding=(20, 20))
        self._callback = on_shortcut_toggle
        self.desktop_var = tk.BooleanVar(value=desktop_enabled)
        self.start_menu_var = tk.BooleanVar(value=start_menu_enabled)

        ttk.Label(self, text="Settings", style="SidebarHeading.TLabel").pack(anchor="w")
        body = ttk.Frame(self, padding=(0, 12))
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Checkbutton(
            body,
            text="Show desktop shortcut",
            variable=self.desktop_var,
            command=lambda: self._on_shortcut_toggled("desktop"),
        ).pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(
            body,
            text="Show Start Menu shortcut",
            variable=self.start_menu_var,
            command=lambda: self._on_shortcut_toggled("start_menu"),
        ).pack(anchor="w", pady=(6, 0))

    def _on_shortcut_toggled(self, kind: str) -> None:
        value = bool(self.desktop_var.get()) if kind == "desktop" else bool(self.start_menu_var.get())
        self._callback(kind, value)

    def update_shortcut_state(self, kind: str, enabled: bool) -> None:
        if kind == "desktop":
            self.desktop_var.set(enabled)
        else:
            self.start_menu_var.set(enabled)
