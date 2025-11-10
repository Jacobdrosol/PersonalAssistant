from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional


class JiraTabView(ttk.Frame):
    """Placeholder JIRA tab guarded by a lightweight PIN gate."""

    _PIN_CODE = "12345"

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, padding=(24, 24))
        self._locked = True
        self._lock_overlay: Optional[tk.Frame] = None
        self._pin_var = tk.StringVar(value="")
        self._lock_error_var = tk.StringVar(value="")
        self._pin_entry: Optional[ttk.Entry] = None
        self._build_placeholder()
        self.after(0, self._show_lock_overlay)

    def _build_placeholder(self) -> None:
        container = ttk.Frame(self)
        container.pack(fill=tk.BOTH, expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        hero = ttk.Frame(container)
        hero.grid(row=0, column=0, sticky="nsew")
        ttk.Label(hero, text="JIRA Workspace", style="SidebarHeading.TLabel").pack(anchor="w")
        ttk.Label(
            hero,
            text=(
                "Track upcoming sprints, coordinate ownership, and keep stakeholders aligned — "
                "all without leaving your Personal Assistant."
            ),
            wraplength=640,
            justify="left",
        ).pack(anchor="w", pady=(8, 0))

    # ------------------------------------------------------------------ Lock overlay
    def _show_lock_overlay(self) -> None:
        if not self._locked or self._lock_overlay is not None:
            return
        overlay = tk.Frame(self, bg="#111219")
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._lock_overlay = overlay

        card = ttk.Frame(overlay, padding=24)
        card.place(relx=0.5, rely=0.5, anchor="center")
        ttk.Label(card, text="JIRA", style="SidebarHeading.TLabel").pack(anchor="center")
        ttk.Label(
            card,
            text="The JIRA tab is still under construction!",
            wraplength=420,
            justify="center",
        ).pack(anchor="center", pady=(8, 0))
        ttk.Label(
            card,
            text=(
                "The all new JIRA tab will incorporate your project management tracking seemlessly into your current "
                "work flow through connected API keys to track tickets, stories, and so much more, their descriptions, "
                "priorities, T.E.D.'s and more to confidently continue your work in a fast paced environment without "
                "losing track of any tickets."
            ),
            wraplength=420,
            justify="center",
        ).pack(anchor="center", pady=(12, 16))
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
        if self._pin_var.get() == self._PIN_CODE:
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
        messagebox.showinfo("JIRA", "Enter the PIN to unlock this tab.", parent=self)
        self.focus_lock_entry()

    def focus_lock_entry(self) -> None:
        if self._pin_entry is not None:
            self._pin_entry.focus_set()


__all__ = ["JiraTabView"]
