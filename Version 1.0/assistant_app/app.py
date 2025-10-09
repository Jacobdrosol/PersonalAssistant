from __future__ import annotations

from pathlib import Path
from typing import List, Optional
import tkinter as tk
from tkinter import ttk

from .calendar_tab import CalendarTab
from .database import Database
from .log_tab import LogTab
from .plugins import EmailIngestManager
from .scrum_tab import ScrumTab
from .ui.views.email_ingest import EmailIngestView
from .system_notifications import SystemNotifier
from .notifications import NotificationManager, NotificationPayload


class PersonalAssistantApp(tk.Tk):
    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self.title("Personal Assistant")
        self.geometry("1280x820")
        self.configure(bg="#111219")
        self.minsize(1024, 720)

        self.project_root = Path(__file__).resolve().parent.parent
        self.email_manager = EmailIngestManager(self.project_root)
        self.db = Database(db_path)
        self.system_notifier = SystemNotifier()
        self._configure_styles()

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.calendar_tab = CalendarTab(self.notebook, self.db)
        self.scrum_tab = ScrumTab(self.notebook, self.db)
        self.log_tab = LogTab(self.notebook, self.db)
        self.email_tab = EmailIngestView(self.notebook, self.email_manager)

        self.notebook.add(self.calendar_tab, text="Production Calendar")
        self.notebook.add(self.log_tab, text="Daily Update Log")
        self.notebook.add(self.scrum_tab, text="Tasks Board")
        self.notebook.add(self.email_tab, text="Email Ingest")

        self.notifications: List[NotificationWindow] = []
        self.notification_manager = NotificationManager(self.db, self._handle_notification)
        self.after(1000, self.notification_manager.start)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------------------------------------------------------- Styles
    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        dark_bg = "#171821"
        darker_bg = "#111219"
        accent = "#4F75FF"
        text_primary = "#E8EAF6"
        text_secondary = "#9FA8DA"

        style.configure("TFrame", background=dark_bg)
        style.configure("TNotebook", background=darker_bg, borderwidth=0)
        style.configure("TNotebook.Tab", background="#1d1f2f", foreground=text_secondary, padding=(16, 4))
        style.map(
            "TNotebook.Tab",
            background=[("selected", "#2b2c42")],
            foreground=[("selected", text_primary)],
            padding=[("selected", (16, 10))],
        )
        style.configure("TLabel", background=dark_bg, foreground=text_primary, font=("Segoe UI", 10))
        style.configure("CalendarHeading.TLabel", font=("Segoe UI", 14, "bold"), foreground=text_primary, background=dark_bg)
        style.configure("SidebarHeading.TLabel", font=("Segoe UI", 12, "bold"), foreground=accent, background=dark_bg)
        style.configure("SelectedDay.TLabel", font=("Segoe UI", 11), foreground=text_secondary, background=dark_bg)
        style.configure("TButton", background="#2a2d3e", foreground=text_primary, padding=(12, 6))
        style.map(
            "TButton",
            background=[("pressed", "#3a3d55"), ("active", "#3a3d55")],
        )
        style.configure(
            "Treeview",
            background="#1c1d2b",
            fieldbackground="#1c1d2b",
            foreground=text_primary,
            borderwidth=0,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Treeview.Heading",
            background="#222338",
            foreground=text_secondary,
            font=("Segoe UI", 10, "bold"),
        )
        style.map("Treeview", background=[("selected", "#3a3d55")])


    def _register_email_shortcuts(self) -> None:
        bindings = {
            "<Control-n>": self._shortcut_email_new,
            "<Control-s>": self._shortcut_email_save,
            "<Control-r>": self._shortcut_email_run,
            "<Control-o>": self._shortcut_email_open,
        }
        for sequence, handler in bindings.items():
            self.bind_all(sequence, handler)

    def _email_tab_active(self) -> bool:
        current = self.notebook.select()
        return bool(current) and current == str(self.email_tab)

    def _shortcut_email_new(self, event: tk.Event) -> Optional[str]:
        if self._email_tab_active():
            self.email_tab.create_new_config()
            return "break"
        return None

    def _shortcut_email_save(self, event: tk.Event) -> Optional[str]:
        if self._email_tab_active():
            self.email_tab.save_config()
            return "break"
        return None

    def _shortcut_email_run(self, event: tk.Event) -> Optional[str]:
        if self._email_tab_active():
            self.email_tab.run_now()
            return "break"
        return None

    def _shortcut_email_open(self, event: tk.Event) -> Optional[str]:
        if self._email_tab_active():
            self.email_tab.open_shard_folder()
            return "break"
        return None


    def _handle_notification(self, payload: NotificationPayload) -> None:
        self.after(0, lambda: self.show_notification(payload))

    # ---------------------------------------------------------------- Events
    def show_notification(self, payload: NotificationPayload) -> None:
        body_text = payload.body.strip() if payload.body else ""
        fallback = payload.occurs_at.strftime("%I:%M %p").lstrip("0")
        self.system_notifier.notify(payload.title, body_text or fallback)
        window = NotificationWindow(self, payload)
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

    def remove_notification(self, window: "NotificationWindow") -> None:
        if window in self.notifications:
            self.notifications.remove(window)
        self._rearrange_notifications()

    def on_close(self) -> None:
        self.notification_manager.stop()
        self.db.close()
        self.destroy()



class NotificationWindow(tk.Toplevel):
    def __init__(self, master: PersonalAssistantApp, payload: NotificationPayload) -> None:
        super().__init__(master)
        self.master = master
        self.payload = payload
        self.configure(bg="#1f2030")
        self.overrideredirect(True)
        self.attributes("-topmost", True)

        frame = ttk.Frame(self, padding=14)
        frame.pack(fill=tk.BOTH, expand=True)

        header_text = "Reminder" if payload.kind == "event" else payload.title
        ttk.Label(frame, text=header_text, style="SidebarHeading.TLabel").pack(anchor="w")
        if payload.kind == "event":
            ttk.Label(frame, text=payload.title, font=("Segoe UI", 11, "bold"), wraplength=280).pack(anchor="w", pady=(4, 0))

        ttk.Label(frame, text=self._derive_time_text(payload), foreground="#9FA8DA").pack(anchor="w", pady=(2, 6))
        body_text = self._derive_body_text(payload)
        if body_text:
            ttk.Label(frame, text=body_text, wraplength=280, foreground="#B0B4C7").pack(anchor="w")

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





def main() -> None:
    base_dir = Path(__file__).resolve().parent
    db_path = base_dir / "assistant.db"
    app = PersonalAssistantApp(db_path)
    app.mainloop()


__all__ = ["main", "PersonalAssistantApp"]





