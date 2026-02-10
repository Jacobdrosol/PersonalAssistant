from __future__ import annotations

from typing import Optional

import tkinter as tk
from tkinter import messagebox, ttk


class ExportValidatorView(ttk.Frame):
    _PIN_CODE = "12345"

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, padding=(16, 16))
        self._locked = True
        self._lock_overlay: Optional[tk.Frame] = None
        self._pin_entry: Optional[ttk.Entry] = None
        self._pin_var = tk.StringVar(value="")
        self._lock_error_var = tk.StringVar(value="")

        header = ttk.Frame(self)
        header.pack(fill=tk.X)
        ttk.Label(header, text="Export Validator", style="SidebarHeading.TLabel").pack(side=tk.LEFT)

        description = ttk.Label(
            self,
            text=(
                "A validation tool where configurations may be loaded in for a production instance via "
                "uploading the instance's xml, these configurations may be updated at any time, validate "
                "exported configurations against the configurations stored in the database for a production "
                "instance."
            ),
            wraplength=780,
            justify="left",
        )
        description.pack(anchor="w", pady=(12, 4))

        ttk.Label(self, text="Coming soon.", style="SelectedDay.TLabel").pack(anchor="w")
        self.after(0, self._show_lock_overlay)

    # ------------------------------------------------------------------ Lock overlay
    def _show_lock_overlay(self) -> None:
        if not self._locked or self._lock_overlay is not None:
            return
        overlay = tk.Frame(self, bg="#111219")
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._lock_overlay = overlay
        card = ttk.Frame(overlay, padding=24)
        card.place(relx=0.5, rely=0.5, anchor="center")
        ttk.Label(
            card,
            text="Export Validator tab is under development.",
            style="SidebarHeading.TLabel",
            wraplength=420,
            justify="center",
        ).pack(anchor="center")
        ttk.Label(
            card,
            text=(
                "A validation tool where configurations may be loaded in for a production instance via "
                "uploading the instance's xml, these configurations may be updated at any time, validate "
                "exported configurations against the configurations stored in the database for a production "
                "instance."
            ),
            wraplength=420,
            justify="center",
        ).pack(anchor="center", pady=(12, 20))
        ttk.Label(card, text="Enter PIN to unlock:", justify="center").pack(anchor="center")
        self._pin_var.set("")
        validate = (self.register(self._validate_pin), "%P")
        entry = ttk.Entry(
            card,
            show="*",
            textvariable=self._pin_var,
            justify="center",
            width=12,
            validate="key",
            validatecommand=validate,
        )
        entry.pack(anchor="center", pady=(6, 0))
        entry.bind("<Return>", self._attempt_unlock)
        entry.focus_set()
        self._pin_entry = entry
        ttk.Button(card, text="Unlock", command=self._attempt_unlock).pack(anchor="center", pady=(10, 0))
        ttk.Label(card, textvariable=self._lock_error_var, foreground="#F36C6C").pack(anchor="center", pady=(8, 0))

    def _validate_pin(self, proposed: str) -> bool:
        if not proposed:
            return True
        if not proposed.isdigit():
            return False
        return len(proposed) <= len(self._PIN_CODE)

    def _attempt_unlock(self, event: Optional[tk.Event] = None) -> Optional[str]:
        value = self._pin_var.get()
        if value == self._PIN_CODE:
            self._unlock()
            return "break"
        self._lock_error_var.set("Incorrect PIN. Try again.")
        self._pin_var.set("")
        if self._pin_entry is not None:
            self._pin_entry.focus_set()
        return "break"

    def _unlock(self) -> None:
        self._locked = False
        if self._lock_overlay is not None:
            self._lock_overlay.destroy()
            self._lock_overlay = None
        self._lock_error_var.set("")

    def is_locked(self) -> bool:
        return self._locked

    def focus_lock_entry(self) -> None:
        if self._pin_entry is not None:
            self._pin_entry.focus_set()

    def notify_locked(self) -> None:
        messagebox.showinfo("Export Validator", "Enter the PIN to unlock this tab.", parent=self)
        self.focus_lock_entry()
