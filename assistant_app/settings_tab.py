from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable


class SettingsTab(ttk.Frame):
    def __init__(self, master: tk.Misc, *, shortcut_enabled: bool, on_desktop_shortcut_toggle: Callable[[bool], None]):
        super().__init__(master, padding=(20, 20))
        self._callback = on_desktop_shortcut_toggle
        self.desktop_var = tk.BooleanVar(value=shortcut_enabled)

        ttk.Label(self, text="Settings", style="SidebarHeading.TLabel").pack(anchor="w")
        body = ttk.Frame(self, padding=(0, 12))
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Checkbutton(
            body,
            text="Show desktop shortcut",
            variable=self.desktop_var,
            command=self._on_shortcut_toggled,
        ).pack(anchor="w", pady=(6, 0))

    def _on_shortcut_toggled(self) -> None:
        value = bool(self.desktop_var.get())
        self._callback(value)

    def update_shortcut_state(self, enabled: bool) -> None:
        self.desktop_var.set(enabled)
