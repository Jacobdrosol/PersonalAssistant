from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from datetime import datetime, time as dt_time
from typing import Callable, Optional

from . import utils


class TimeInput(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        *,
        initial: datetime | dt_time | str | None = None,
        use_24_hour: Optional[bool] = None,
        entry_width: int = 8,
    ) -> None:
        super().__init__(master)
        self._use_24_hour = utils.use_24_hour_time() if use_24_hour is None else bool(use_24_hour)
        self.time_var = tk.StringVar()
        self.ampm_var = tk.StringVar(value="AM")

        self.entry = ttk.Entry(self, textvariable=self.time_var, width=entry_width)
        self.entry.grid(row=0, column=0, sticky="ew")
        self.ampm_combo = ttk.Combobox(
            self,
            values=("AM", "PM"),
            textvariable=self.ampm_var,
            state="readonly",
            width=4,
        )
        self.ampm_combo.grid(row=0, column=1, padx=(6, 0))
        self.columnconfigure(0, weight=1)

        self._apply_mode()
        if initial is not None:
            self.set(initial)

    def _apply_mode(self) -> None:
        if self._use_24_hour:
            self.ampm_combo.grid_remove()
        else:
            self.ampm_combo.grid()

    def set_use_24_hour(self, use_24_hour: bool) -> None:
        use_24_hour = bool(use_24_hour)
        if use_24_hour == self._use_24_hour:
            return
        parsed = self._parse_current_time()
        self._use_24_hour = use_24_hour
        self._apply_mode()
        if parsed is not None:
            self.set(parsed)

    def _parse_current_time(self) -> Optional[dt_time]:
        raw = self.get()
        if not raw:
            return None
        try:
            return utils.parse_time_string(raw, self._use_24_hour)
        except ValueError:
            return None

    def set(self, value: datetime | dt_time | str) -> None:
        if value is None:
            self.time_var.set("")
            return
        if isinstance(value, datetime):
            parsed = value.time()
        elif isinstance(value, dt_time):
            parsed = value
        else:
            text = str(value).strip()
            if not text:
                self.time_var.set("")
                return
            try:
                parsed = utils.parse_time_string(text, self._use_24_hour)
            except ValueError:
                self.time_var.set(text)
                return
        if self._use_24_hour:
            self.time_var.set(utils.format_time(parsed, True))
        else:
            display = utils.format_time(parsed, False)
            if " " in display:
                time_part, period = display.rsplit(" ", 1)
                self.time_var.set(time_part)
                self.ampm_var.set(period)
            else:
                self.time_var.set(display)

    def get(self) -> str:
        text = (self.time_var.get() or "").strip()
        if not text:
            return ""
        if self._use_24_hour:
            return text
        upper = text.upper()
        if "AM" in upper or "PM" in upper:
            return text
        period = (self.ampm_var.get() or "AM").strip().upper()
        if period not in {"AM", "PM"}:
            period = "AM"
        return f"{text} {period}"

    def bind_entry(self, sequence: str, func: Callable, add: str | None = None) -> str:
        return self.entry.bind(sequence, func, add=add)

    def configure_state(self, state: str) -> None:
        """Set the input state ('normal' or 'disabled') for the entry and AM/PM toggle."""
        self.entry.configure(state=state)
        combo_state = "readonly" if state != "disabled" else "disabled"
        self.ampm_combo.configure(state=combo_state)


__all__ = ["TimeInput"]
