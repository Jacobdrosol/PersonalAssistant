from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional


class SqlAssistView(ttk.Frame):
    """Placeholder SQL Assist tab that remains locked behind a PIN until ready."""

    _PIN_CODE = "12345"

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, padding=(16, 16))
        self._locked = True
        self._lock_overlay: Optional[tk.Frame] = None
        self._pin_entry: Optional[ttk.Entry] = None
        self._pin_var = tk.StringVar(value="")
        self._lock_error_var = tk.StringVar(value="")
        self._build_placeholder_ui()
        self.after(0, self._show_lock_overlay)

    # ------------------------------------------------------------------ UI
    def _build_placeholder_ui(self) -> None:
        container = ttk.Frame(self)
        container.pack(fill=tk.BOTH, expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)

        ttk.Label(
            container,
            text="SQL Assist",
            style="SidebarHeading.TLabel",
            anchor="center",
            justify="center",
        ).grid(row=0, column=0, pady=(0, 16))

        ttk.Label(
            container,
            text="Tools for managing SQL workflows are on the way.",
            justify="center",
            wraplength=560,
        ).grid(row=1, column=0, sticky="n")

    # ------------------------------------------------------------------ Lock overlay
    def _show_lock_overlay(self) -> None:
        if not self._locked:
            return
        if self._lock_overlay is not None:
            self._lock_overlay.destroy()
        overlay = tk.Frame(self, bg="#111219")
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._lock_overlay = overlay

        card = ttk.Frame(overlay, padding=24)
        card.place(relx=0.5, rely=0.5, anchor="center")
        ttk.Label(
            card,
            text="The SQL Assist tab is still under construction!",
            style="SidebarHeading.TLabel",
            justify="center",
            wraplength=420,
        ).pack(anchor="center")
        ttk.Label(
            card,
            text=(
                "We are currently working to offer users the ability to keep track of their SQL database, "
                "naming conventions, properties, and more to assist when writing queries."
            ),
            justify="center",
            wraplength=420,
        ).pack(anchor="center", pady=(12, 20))
        ttk.Label(card, text="Enter PIN to unlock:", justify="center").pack(anchor="center")

        self._pin_var.set("")
        validate = (self.register(self._validate_pin), "%P")
        entry = ttk.Entry(
            card,
            show="*",
            width=12,
            justify="center",
            textvariable=self._pin_var,
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

    # ------------------------------------------------------------------ External helpers
    def is_locked(self) -> bool:
        return self._locked

    def notify_locked(self) -> None:
        messagebox.showinfo("SQL Assist", "Enter the PIN to unlock this tab.", parent=self)
        self.focus_lock_entry()

    def focus_lock_entry(self) -> None:
        if self._pin_entry is not None:
            self._pin_entry.focus_set()


__all__ = ["SqlAssistView"]
