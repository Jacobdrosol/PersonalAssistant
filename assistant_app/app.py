from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, List, Optional
import tkinter as tk
from tkinter import messagebox, ttk

from .calendar_tab import CalendarTab
from .contact_tab import ContactTab
from .database import Database
from .log_tab import LogTab
from .plugins import EmailIngestManager
from .scrum_tab import ScrumTab
from .ui.views.email_ingest import EmailIngestView
from .ui.views.sql_assist import SqlAssistView
from .system_notifications import SystemNotifier
from .notifications import NotificationManager, NotificationPayload
from .environment import APP_NAME, ensure_user_data_dir, legacy_project_root
from .settings_store import AppSettings, load_settings, save_settings
from .settings_tab import SettingsTab
from .shortcuts import (
    create_desktop_shortcut,
    remove_desktop_shortcut,
    desktop_shortcut_exists,
    create_start_menu_shortcut,
    remove_start_menu_shortcut,
    start_menu_shortcut_exists,
)
from .version import __version__
from . import updater


class PersonalAssistantApp(tk.Tk):
    def __init__(self, db_path: Path, data_root: Path, settings: AppSettings, settings_path: Path) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.configure(bg="#111219")

        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        default_w = min(1380, max(1100, screen_w - 160))
        default_h = min(900, max(760, screen_h - 200))
        self.geometry(f"{default_w}x{default_h}")
        min_w = max(960, min(default_w, screen_w - 240))
        min_h = max(700, min(default_h, screen_h - 220))
        self.minsize(min_w, min_h)

        self.project_root = Path(__file__).resolve().parent.parent
        self.data_root = data_root
        self.settings = settings
        self.settings_path = settings_path
        self._icon_path = self._ensure_icon_file()
        self.email_manager = EmailIngestManager(self.data_root)
        self.db = Database(db_path)
        self.system_notifier = SystemNotifier()
        self._configure_styles()
        self._apply_window_icon()

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        manage_shortcuts = self._should_manage_shortcut()
        self.settings_tab_frame = ttk.Frame(self.notebook, style="TFrame")
        self.settings_tab = SettingsTab(
            self.settings_tab_frame,
            desktop_enabled=self.settings.desktop_shortcut and manage_shortcuts,
            start_menu_enabled=self.settings.start_menu_shortcut and manage_shortcuts,
            daily_notifications_enabled=self.settings.daily_update_notifications,
            on_setting_toggle=self._handle_setting_toggle,
            app_version=__version__,
        )
        self.settings_tab.pack(fill=tk.BOTH, expand=True)
        self.settings_tab_frame.place_forget()

        self.calendar_tab = CalendarTab(self.notebook, self.db)
        self.scrum_tab = ScrumTab(self.notebook, self.db)
        self.log_tab = LogTab(self.notebook, self.db)
        self.email_tab = EmailIngestView(self.notebook, self.email_manager)
        self.sql_assist_tab = SqlAssistView(self.notebook, self.db)
        self.contact_tab = ContactTab(self.notebook, self.data_root, app_version=__version__)

        self.notebook.add(self.calendar_tab, text="Production Calendar")
        self.notebook.add(self.log_tab, text="Daily Update Log")
        self.notebook.add(self.scrum_tab, text="Tasks Board")
        self.notebook.add(self.email_tab, text="Email Ingest")
        self.notebook.add(self.sql_assist_tab, text="SQL Assist")
        self.notebook.add(self.contact_tab, text="Contact Support")

        self._last_notebook_tab = self.notebook.select()
        self._settings_visible = False
        self.notebook.bind("<<NotebookTabChanged>>", self._record_last_notebook_tab)

        self.settings_button = ttk.Button(
            self.notebook,
            text="Settings",
            style="SettingsTabInactive.TButton",
            command=self._toggle_settings_view,
            cursor="hand2",
        )
        self.notebook.bind("<Configure>", self._position_settings_button)
        self.after(50, self._position_settings_button)
        self._sync_settings_button_state()

        self.notifications: List[NotificationWindow] = []
        self.notification_manager = NotificationManager(self.db, self._handle_notification)
        self.notification_manager.set_standing_reminders_enabled(self.settings.daily_update_notifications)
        self.after(1000, self.notification_manager.start)
        self.after(2000, self._check_for_updates_async)
        self.after(250, self._ensure_shortcuts)

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
            "SettingsTabInactive.TButton",
            background="#1d1f2f",
            foreground=text_secondary,
            padding=(16, 4, 16, 4),
            relief="raised",
            borderwidth=1,
        )
        style.map(
            "SettingsTabInactive.TButton",
            background=[("pressed", "#2b2c42"), ("active", "#2b2c42")],
        )
        style.configure(
            "SettingsTabActive.TButton",
            background="#2b2c42",
            foreground=text_primary,
            padding=(16, 10, 16, 10),
            relief="sunken",
            borderwidth=1,
        )
        style.map(
            "SettingsTabActive.TButton",
            background=[("active", "#2b2c42")],
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
        summary_lines.append("Install now? The app will download the update, close, and you'll reopen it manually once finished.")
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
            "Personal Assistant will close so the update can be installed.\nAfter it finishes, reopen the app from your shortcut.",
            parent=self,
        )
        self.after(100, self.on_close)

    def _ensure_icon_file(self) -> Optional[Path]:
        icon_path = self.data_root / "personal_assistant.ico"
        if icon_path.exists():
            return icon_path
        candidates: List[Path] = []
        if hasattr(sys, "_MEIPASS"):
            candidates.append(Path(sys._MEIPASS) / "personal_assistant.ico")
        executable_dir = Path(sys.executable).resolve().parent
        candidates.append(executable_dir / "personal_assistant.ico")
        candidates.append(self.project_root / "assets" / "personal_assistant.ico")
        for candidate in candidates:
            if candidate.exists():
                try:
                    icon_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(candidate, icon_path)
                    return icon_path
                except Exception:
                    continue
        return icon_path if icon_path.exists() else None

    def _apply_window_icon(self) -> None:
        icon = self._icon_path
        if icon and icon.exists():
            try:
                self.iconbitmap(str(icon))
            except Exception:
                pass

    def _should_manage_shortcut(self) -> bool:
        return sys.platform.startswith("win") and bool(getattr(sys, "frozen", False))

    def _ensure_shortcuts(self) -> None:
        if not self._should_manage_shortcut():
            self.settings.desktop_shortcut = False
            self.settings.start_menu_shortcut = False
            self.settings_tab.update_shortcut_state("desktop", False)
            self.settings_tab.update_shortcut_state("start_menu", False)
            save_settings(self.settings_path, self.settings)
            return
        icon = self._icon_path or self._ensure_icon_file()
        if icon is not None and icon.exists():
            self._icon_path = icon
            self._apply_window_icon()
        target = Path(sys.executable).resolve()
        desktop_exists = desktop_shortcut_exists()
        start_exists = start_menu_shortcut_exists()
        if self.settings.desktop_shortcut and not desktop_exists:
            if self._create_shortcut("desktop", target):
                desktop_exists = True
        elif not self.settings.desktop_shortcut and desktop_exists:
            if self._remove_shortcut("desktop"):
                desktop_exists = False
        if self.settings.start_menu_shortcut and not start_exists:
            if self._create_shortcut("start_menu", target):
                start_exists = True
        elif not self.settings.start_menu_shortcut and start_exists:
            if self._remove_shortcut("start_menu"):
                start_exists = False
        self.settings.desktop_shortcut = desktop_exists
        self.settings.start_menu_shortcut = start_exists
        self.settings_tab.update_shortcut_state("desktop", desktop_exists)
        self.settings_tab.update_shortcut_state("start_menu", start_exists)
        save_settings(self.settings_path, self.settings)

    def _create_shortcut(self, kind: str, target: Path) -> bool:
        icon = self._icon_path or self._ensure_icon_file()
        if icon is not None and icon.exists():
            self._icon_path = icon
            self._apply_window_icon()
        label = "Desktop Shortcut" if kind == "desktop" else "Start Menu Shortcut"
        if icon is None or not icon.exists():
            messagebox.showerror(
                label,
                "Unable to locate the application icon for the shortcut.",
                parent=self,
            )
            return False
        if kind == "desktop":
            success = create_desktop_shortcut(target, icon)
        else:
            success = create_start_menu_shortcut(target, icon)
        if not success:
            messagebox.showerror(label, f"Unable to create the {label.lower()}.", parent=self)
        return success

    def _remove_shortcut(self, kind: str) -> bool:
        if kind == "desktop":
            return remove_desktop_shortcut()
        return remove_start_menu_shortcut()

    def _handle_setting_toggle(self, kind: str, enabled: bool) -> None:
        if kind == "daily_notifications":
            self.settings.daily_update_notifications = bool(enabled)
            self.notification_manager.set_standing_reminders_enabled(bool(enabled))
            self.settings_tab.update_daily_notification_state(bool(enabled))
            save_settings(self.settings_path, self.settings)
            return

        label = "Desktop" if kind == "desktop" else "Start Menu"
        if not self._should_manage_shortcut():
            messagebox.showinfo(
                f"{label} Shortcut",
                f"{label} shortcuts are only available in the packaged application.",
                parent=self,
            )
            self.settings_tab.update_shortcut_state(kind, False)
            if kind == "desktop":
                self.settings.desktop_shortcut = False
            else:
                self.settings.start_menu_shortcut = False
            save_settings(self.settings_path, self.settings)
            return
        target = Path(sys.executable).resolve()
        if enabled:
            success = self._create_shortcut(kind, target)
            if success:
                if kind == "desktop":
                    self.settings.desktop_shortcut = True
                else:
                    self.settings.start_menu_shortcut = True
        else:
            success = self._remove_shortcut(kind)
            if not success:
                messagebox.showerror(
                    f"{label} Shortcut",
                    f"Unable to remove the {label.lower()} shortcut.",
                    parent=self,
                )
                if kind == "desktop":
                    self.settings.desktop_shortcut = True
                else:
                    self.settings.start_menu_shortcut = True
            else:
                if kind == "desktop":
                    self.settings.desktop_shortcut = False
                else:
                    self.settings.start_menu_shortcut = False
        self.settings_tab.update_shortcut_state("desktop", desktop_shortcut_exists())
        self.settings_tab.update_shortcut_state("start_menu", start_menu_shortcut_exists())
        save_settings(self.settings_path, self.settings)

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
            if self.email_tab.is_locked():
                self.email_tab.notify_locked()
                return "break"
            self.email_tab.create_new_config()
            return "break"
        return None

    def _shortcut_email_save(self, event: tk.Event) -> Optional[str]:
        if self._email_tab_active():
            if self.email_tab.is_locked():
                self.email_tab.notify_locked()
                return "break"
            self.email_tab.save_config()
            return "break"
        return None

    def _shortcut_email_run(self, event: tk.Event) -> Optional[str]:
        if self._email_tab_active():
            if self.email_tab.is_locked():
                self.email_tab.notify_locked()
                return "break"
            self.email_tab.run_now()
            return "break"
        return None

    def _shortcut_email_open(self, event: tk.Event) -> Optional[str]:
        if self._email_tab_active():
            if self.email_tab.is_locked():
                self.email_tab.notify_locked()
                return "break"
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

    def _position_settings_button(self, event: Optional[tk.Event] = None) -> None:
        if not hasattr(self, "settings_button"):
            return
        try:
            self.settings_button.place(relx=1.0, x=-4, y=2, anchor="ne")
            self.settings_button.lift()
        except tk.TclError:
            pass
        self._place_settings_overlay()

    def _place_settings_overlay(self) -> None:
        if not self._settings_visible:
            return
        try:
            self.notebook.update_idletasks()
            self.settings_tab_frame.update_idletasks()
        except tk.TclError:
            pass
        offset = self._compute_notebook_content_offset()
        height = max(0, self.notebook.winfo_height() - offset)
        params = {
            "in_": self.notebook,
            "relx": 0.0,
            "x": 0,
            "y": offset,
            "relwidth": 1.0,
        }
        if height > 0:
            params["height"] = height
        else:
            params["relheight"] = 1.0
        try:
            self.settings_tab_frame.place(**params)
            self.settings_tab_frame.lift()
        except tk.TclError:
            pass

    def _compute_notebook_content_offset(self) -> int:
        try:
            current_id = self.notebook.select()
            if current_id:
                widget = self.nametowidget(current_id)
                notebook_y = self.notebook.winfo_rooty()
                widget_y = widget.winfo_rooty()
                if widget_y >= notebook_y:
                    return widget_y - notebook_y
        except Exception:
            pass
        # Fallback to a reasonable default tab height.
        return 36

    def _record_last_notebook_tab(self, event: Optional[tk.Event] = None) -> None:
        current = self.notebook.select()
        if self._settings_visible:
            self.settings_tab_frame.place_forget()
            self._settings_visible = False
            self._last_notebook_tab = current
            self._sync_settings_button_state()
            self._position_settings_button()
            try:
                self.settings_button.state(["!pressed"])
            except tk.TclError:
                pass
            return
        self._last_notebook_tab = current
        self._sync_settings_button_state()

    def _toggle_settings_view(self) -> None:
        if self._settings_visible:
            self._hide_settings_view()
        else:
            self._show_settings_view()

    def _show_settings_view(self) -> None:
        self._last_notebook_tab = self.notebook.select()
        self._settings_visible = True
        self._place_settings_overlay()
        self._sync_settings_button_state()
        self._position_settings_button()
        try:
            self.settings_button.state(["pressed"])
        except tk.TclError:
            pass

    def _hide_settings_view(self) -> None:
        if self._last_notebook_tab:
            try:
                self.notebook.select(self._last_notebook_tab)
            except tk.TclError:
                pass
        self.settings_tab_frame.place_forget()
        self._settings_visible = False
        self._sync_settings_button_state()
        self._position_settings_button()
        try:
            self.settings_button.state(["!pressed"])
        except tk.TclError:
            pass
        try:
            self.settings_tab_frame.lower()
        except tk.TclError:
            pass

    def _sync_settings_button_state(self) -> None:
        if not hasattr(self, "settings_button"):
            return
        style_name = "SettingsTabActive.TButton" if self._settings_visible else "SettingsTabInactive.TButton"
        self.settings_button.configure(style=style_name)

    def remove_notification(self, window: "NotificationWindow") -> None:
        if window in self.notifications:
            self.notifications.remove(window)
        self._rearrange_notifications()

    def on_close(self) -> None:
        self.notification_manager.stop()
        self.db.close()
        save_settings(self.settings_path, self.settings)
        self.destroy()

class UpdateProgressWindow(tk.Toplevel):
    def __init__(self, master: PersonalAssistantApp, update_info: "updater.AvailableUpdate") -> None:
        super().__init__(master)
        self.master = master
        self.configure(bg="#1d1e2c")
        self.resizable(False, False)
        self.transient(master)
        self.title("Installing Update")
        self.progress_mode = "indeterminate"

        container = ttk.Frame(self, padding=20)
        container.pack(fill=tk.BOTH, expand=True)

        title = update_info.release_name or f"Version {update_info.version}"
        ttk.Label(container, text=f"Updating to {title}", style="SidebarHeading.TLabel").pack(anchor="w")

        self.status_var = tk.StringVar(value="Preparing download...")
        ttk.Label(container, textvariable=self.status_var, wraplength=320).pack(anchor="w", pady=(10, 6))

        self.instructions_var = tk.StringVar(
            value="Once the download finishes, Personal Assistant will close so the update can be installed. Reopen it from your shortcut afterwards."
        )
        ttk.Label(container, textvariable=self.instructions_var, wraplength=320, foreground="#9FA8DA").pack(anchor="w", pady=(0, 12))

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
            self.status_var.set("Download complete. Closing to install update...")
            self.instructions_var.set("Personal Assistant will close now and finish installing the update. Reopen it from your shortcut once the window disappears.")
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


def _ensure_installed_binary(data_root: Path) -> None:
    if not getattr(sys, "frozen", False):
        return

    expected_exe = data_root / "PersonalAssistant.exe"
    current_exe = Path(sys.executable).resolve()
    version_file = data_root / "app_version.txt"

    def _write_version_file() -> None:
        try:
            version_file.write_text(__version__, encoding="utf-8")
        except Exception:
            pass

    def _copy_icon(source: Path) -> None:
        icon_source = source.with_name("personal_assistant.ico")
        if not icon_source.exists():
            return
        icon_target = data_root / "personal_assistant.ico"
        try:
            icon_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(icon_source, icon_target)
        except Exception:
            pass

    if current_exe == expected_exe:
        _write_version_file()
        icon_path = data_root / "personal_assistant.ico"
        if not icon_path.exists():
            _copy_icon(current_exe)
        return

    expected_exe.parent.mkdir(parents=True, exist_ok=True)

    installed_version_key: Optional[tuple[int, ...]] = None
    if version_file.exists():
        try:
            installed_version_key = _parse_version(version_file.read_text(encoding="utf-8"))
        except Exception:
            installed_version_key = None

    current_version_key = _parse_version(__version__)

    def _launch_installed() -> None:
        args = sys.argv[1:]
        subprocess.Popen([str(expected_exe), *args])
        sys.exit(0)

    if expected_exe.exists():
        if installed_version_key and installed_version_key >= current_version_key:
            _launch_installed()
            return
        if not installed_version_key:
            try:
                if expected_exe.stat().st_mtime >= current_exe.stat().st_mtime:
                    _launch_installed()
                    return
            except OSError:
                _launch_installed()
                return

    try:
        shutil.copy2(current_exe, expected_exe)
    except Exception:
        if expected_exe.exists():
            _launch_installed()
        return

    _copy_icon(current_exe)
    _write_version_file()
    _launch_installed()


def _parse_version(value: str) -> tuple[int, ...]:
    cleaned = (value or "").strip().lower()
    if cleaned.startswith("v"):
        cleaned = cleaned[1:]
    tokens: list[int] = []
    for part in cleaned.replace("-", ".").split("."):
        part = part.strip()
        if not part:
            continue
        digits = "".join(ch for ch in part if ch.isdigit())
        if digits:
            tokens.append(int(digits))
    return tuple(tokens) if tokens else (0,)


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
    _ensure_installed_binary(data_root)
    _migrate_legacy_data(data_root)
    settings_path = data_root / "settings.json"
    settings = load_settings(settings_path)
    db_path = data_root / "assistant.db"
    app = PersonalAssistantApp(db_path, data_root, settings, settings_path)
    app.mainloop()


__all__ = ["main", "PersonalAssistantApp"]







