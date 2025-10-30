from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Callable, List, Optional
import tkinter as tk
from tkinter import messagebox, ttk

from .calendar_tab import CalendarTab
from .database import Database
from .log_tab import LogTab
from .plugins import EmailIngestManager
from .scrum_tab import ScrumTab
from .ui.views.email_ingest import EmailIngestView
from .system_notifications import SystemNotifier
from .notifications import NotificationManager, NotificationPayload
from .environment import APP_NAME, ensure_user_data_dir, legacy_project_root
from .version import __version__
from . import updater


class PersonalAssistantApp(tk.Tk):
    def __init__(self, db_path: Path, data_root: Path) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1280x820")
        self.configure(bg="#111219")
        self.minsize(1024, 720)

        self.project_root = Path(__file__).resolve().parent.parent
        self.data_root = data_root
        self.email_manager = EmailIngestManager(self.data_root)
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
        self.after(2000, self._check_for_updates_async)

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
        summary_lines.append("Install now? The app will download the update and restart.")
        if not messagebox.askyesno("Update Available", "\n".join(summary_lines), parent=self):
            return
        self._begin_update_install(info)

    def _begin_update_install(self, info: "updater.AvailableUpdate") -> None:
        progress_window = UpdateProgressWindow(self, info)

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
            "The application will close now to complete the update.",
            parent=self,
        )
        self.after(100, self.on_close)

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

class UpdateProgressWindow(tk.Toplevel):
    def __init__(self, master: PersonalAssistantApp, update_info: "updater.AvailableUpdate") -> None:
        super().__init__(master)
        self.master = master
        self.configure(bg="#1d1e2c")
        self.resizable(False, False)
        self.transient(master)
        self.title("Updating Personal Assistant")
        self.progress_mode = "indeterminate"

        container = ttk.Frame(self, padding=20)
        container.pack(fill=tk.BOTH, expand=True)

        title = update_info.release_name or f"Version {update_info.version}"
        ttk.Label(container, text=f"Downloading {title}", style="SidebarHeading.TLabel").pack(anchor="w")

        self.status_var = tk.StringVar(value="Preparing download...")
        ttk.Label(container, textvariable=self.status_var, wraplength=320).pack(anchor="w", pady=(10, 12))

        self.progress = ttk.Progressbar(container, mode="indeterminate", length=320)
        self.progress.pack(fill=tk.X)
        self.progress.start(10)

        self.percent_var = tk.StringVar(value="")
        ttk.Label(container, textvariable=self.percent_var, foreground="#9FA8DA").pack(anchor="e", pady=(6, 0))

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
            self.status_var.set("Download complete. Restarting to apply update...")
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
    _migrate_legacy_data(data_root)
    db_path = data_root / "assistant.db"
    app = PersonalAssistantApp(db_path, data_root)
    app.mainloop()


__all__ = ["main", "PersonalAssistantApp"]





