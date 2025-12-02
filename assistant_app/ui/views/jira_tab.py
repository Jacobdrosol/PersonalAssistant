from __future__ import annotations

import threading
import webbrowser
from datetime import datetime, date
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable, Dict, List, Optional

from ...jira_service import JiraService, JiraServiceError
from ...models import JiraIssue, JiraProject
from ...theme import ThemePalette


class JiraTabView(ttk.Frame):
    """Interactive Jira workspace with filtering and detail view."""

    _PIN_CODE = "12345"

    def __init__(
        self,
        master: tk.Misc,
        *,
        service: JiraService,
        theme: ThemePalette,
        open_settings: Callable[[], None],
    ) -> None:
        super().__init__(master, padding=(16, 16))
        self.service = service
        self.theme = theme
        self._open_settings = open_settings
        self._log_path = service.debug_log_path()
        self._issues: List[JiraIssue] = []
        self._filtered: List[JiraIssue] = []
        self._project_choices: Dict[str, Optional[str]] = {"All Projects": None}
        self._issue_map: Dict[str, JiraIssue] = {}
        self._refresh_thread: Optional[threading.Thread] = None
        self._selected_issue: Optional[JiraIssue] = None
        self._locked = True
        self._lock_overlay: Optional[tk.Frame] = None
        self._pin_entry: Optional[ttk.Entry] = None

        self.status_var = tk.StringVar(value="Connect your Jira account in Settings to begin.")
        self.last_sync_var = tk.StringVar(value="")
        self.assigned_var = tk.BooleanVar(value=True)
        self.watched_var = tk.BooleanVar(value=True)
        self.project_var = tk.StringVar(value="All Projects")
        self.search_var = tk.StringVar(value="")
        self._pin_var = tk.StringVar(value="")
        self._lock_error_var = tk.StringVar(value="")

        self._build_ui()
        self.on_settings_updated()
        self.after(0, self._show_lock_overlay)

    # ------------------------------------------------------------------ UI construction
    def _build_ui(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill=tk.X)
        ttk.Label(header, textvariable=self.status_var).pack(side=tk.LEFT, anchor="w")
        ttk.Label(header, textvariable=self.last_sync_var, foreground="#8F9BB3").pack(side=tk.LEFT, padx=(12, 0))
        header_buttons = ttk.Frame(header)
        header_buttons.pack(side=tk.RIGHT)
        self.refresh_btn = ttk.Button(header_buttons, text="Refresh", command=self._refresh_async)
        self.refresh_btn.pack(side=tk.LEFT)
        ttk.Button(header_buttons, text="Settings", command=self._open_settings).pack(side=tk.LEFT, padx=(6, 0))

        self.config_frame = ttk.Frame(self, padding=(12, 10))
        ttk.Label(
            self.config_frame,
            text="Configure Jira integration in Settings to enable this tab.",
            style="SidebarHeading.TLabel",
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(self.config_frame, text="Open Settings", command=self._open_settings).pack(side=tk.LEFT)

        filters = ttk.Frame(self, padding=(0, 10))
        filters.pack(fill=tk.X, pady=(12, 6))
        ttk.Checkbutton(
            filters,
            text="Assigned to me",
            variable=self.assigned_var,
            command=self._apply_filters,
        ).pack(side=tk.LEFT)
        ttk.Checkbutton(
            filters,
            text="Watched issues",
            variable=self.watched_var,
            command=self._apply_filters,
        ).pack(side=tk.LEFT, padx=(12, 0))

        ttk.Label(filters, text="Project").pack(side=tk.LEFT, padx=(20, 4))
        self.project_combo = ttk.Combobox(filters, textvariable=self.project_var, state="readonly", width=28)
        self.project_combo.pack(side=tk.LEFT)
        self.project_combo.bind("<<ComboboxSelected>>", lambda _: self._apply_filters())

        ttk.Label(filters, text="Search").pack(side=tk.LEFT, padx=(20, 4))
        search_entry = ttk.Entry(filters, textvariable=self.search_var, width=32)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        search_entry.bind("<KeyRelease>", lambda _: self._apply_filters())

        paned = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        list_frame = ttk.Frame(paned)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            list_frame,
            columns=("key", "summary", "project", "status", "priority", "updated", "due", "source"),
            show="headings",
            selectmode="browse",
            height=18,
        )
        self.tree.heading("key", text="Key")
        self.tree.heading("summary", text="Summary")
        self.tree.heading("project", text="Project")
        self.tree.heading("status", text="Status")
        self.tree.heading("priority", text="Priority")
        self.tree.heading("updated", text="Updated")
        self.tree.heading("due", text="Due")
        self.tree.heading("source", text="Source")
        self.tree.column("key", width=80, anchor="w")
        self.tree.column("summary", width=260, anchor="w")
        self.tree.column("project", width=150, anchor="w")
        self.tree.column("status", width=120, anchor="w")
        self.tree.column("priority", width=100, anchor="w")
        self.tree.column("updated", width=150, anchor="w")
        self.tree.column("due", width=110, anchor="w")
        self.tree.column("source", width=140, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _: self._on_tree_select())
        self.tree.bind("<Double-1>", lambda _: self._open_selected_issue())
        paned.add(list_frame, weight=3)

        detail_frame = ttk.Frame(paned, padding=(12, 0))
        detail_frame.columnconfigure(0, weight=1)
        self.detail_title = ttk.Label(detail_frame, text="Select an issue to view details.", style="SidebarHeading.TLabel")
        self.detail_title.grid(row=0, column=0, sticky="w")
        self.detail_meta = ttk.Label(detail_frame, text="", wraplength=360, justify="left")
        self.detail_meta.grid(row=1, column=0, sticky="w", pady=(4, 6))
        self.detail_text = tk.Text(detail_frame, height=18, wrap="word")
        self.detail_text.grid(row=2, column=0, sticky="nsew")
        self.detail_text.configure(state=tk.DISABLED)
        detail_frame.rowconfigure(2, weight=1)
        action_row = ttk.Frame(detail_frame)
        action_row.grid(row=3, column=0, sticky="e", pady=(6, 0))
        self.open_btn = ttk.Button(action_row, text="Open in Jira", command=self._open_selected_issue, state=tk.DISABLED)
        self.open_btn.pack(side=tk.RIGHT)
        paned.add(detail_frame, weight=2)

        self.apply_theme(self.theme)

    # ------------------------------------------------------------------ Public hooks
    def apply_theme(self, theme: ThemePalette) -> None:
        self.theme = theme
        self.detail_text.configure(
            bg=theme.entry_bg,
            fg=theme.entry_fg,
            insertbackground=theme.entry_fg,
        )

    def on_settings_updated(self) -> None:
        if self._locked:
            self._set_locked_state()
            return
        if self.service.is_configured():
            if self.config_frame.winfo_ismapped():
                self.config_frame.pack_forget()
            self.refresh_btn.configure(state=tk.NORMAL)
            self.status_var.set("Ready. Click Refresh to sync Jira issues.")
        else:
            self._show_configuration_message()
            self.status_var.set("Configure Jira integration in Settings to enable this tab.")
            self.refresh_btn.configure(state=tk.DISABLED)
            self._clear_results()

    # ------------------------------------------------------------------ Refresh workflow
    def _refresh_async(self) -> None:
        if self._locked:
            self.notify_locked()
            return
        if not self.service.is_configured():
            self._show_configuration_message()
            messagebox.showinfo("Jira", "Configure Jira integration in Settings first.", parent=self)
            return
        if self._refresh_thread and self._refresh_thread.is_alive():
            return
        self._set_status("Refreshing Jira issues...", pending=True)
        self.refresh_btn.configure(state=tk.DISABLED)
        thread = threading.Thread(target=self._refresh_worker, daemon=True)
        thread.start()
        self._refresh_thread = thread

    def _refresh_worker(self) -> None:
        try:
            issues, projects = self.service.refresh()
        except JiraServiceError as exc:
            message = str(exc)
            self.after(0, lambda msg=message: self._handle_refresh_error(msg))
            return
        self.after(0, lambda: self._apply_refresh_results(issues, projects))

    def _apply_refresh_results(self, issues: List[JiraIssue], projects: List[JiraProject]) -> None:
        self._issues = issues
        self._populate_projects(projects)
        self._apply_filters()
        last_sync = self.service.last_sync()
        if last_sync:
            self.last_sync_var.set(f"Last synced {last_sync.strftime('%Y-%m-%d %H:%M')}")
        self._set_status(f"Loaded {len(issues)} Jira issues.", pending=False)
        self.refresh_btn.configure(state=tk.NORMAL)

    def _handle_refresh_error(self, message: str) -> None:
        self.refresh_btn.configure(state=tk.NORMAL)
        self._set_status(f"Failed to refresh: {message}", pending=False, error=True)
        details = message
        if self._log_path is not None:
            details += f"\n\nRequest/response details logged at:\n{self._log_path}"
        messagebox.showerror("Jira", details, parent=self)

    def _set_status(self, message: str, *, pending: bool, error: bool = False) -> None:
        self.status_var.set(message)
        if pending:
            self.status_var.set(f"{message} (please wait)")
        if error:
            self.last_sync_var.set("")

    def _populate_projects(self, projects: List[JiraProject]) -> None:
        self._project_choices = {"All Projects": None}
        for project in projects:
            label = f"{project.name} ({project.key})"
            self._project_choices[label] = project.key
        values = list(self._project_choices.keys())
        self.project_combo.configure(values=values)
        if self.project_var.get() not in values:
            self.project_var.set("All Projects")

    def _apply_filters(self) -> None:
        issues = list(self._issues)
        project_label = self.project_var.get()
        project_key = self._project_choices.get(project_label)
        search = self.search_var.get().strip().lower()
        include_assigned = bool(self.assigned_var.get())
        include_watched = bool(self.watched_var.get())

        def include(issue: JiraIssue) -> bool:
            if project_key and issue.project_key != project_key:
                return False
            if include_assigned and include_watched:
                pass
            elif include_assigned:
                if not issue.is_assigned:
                    return False
            elif include_watched:
                if not issue.is_watched:
                    return False
            else:
                # No filter selected means show all.
                pass
            if search:
                haystack = " ".join(
                    part
                    for part in (
                        issue.key,
                        issue.summary,
                        issue.project_name,
                        issue.status,
                        issue.priority,
                        issue.assignee or "",
                        issue.reporter or "",
                    )
                    if part
                ).lower()
                if search not in haystack:
                    return False
            return True

        filtered = [issue for issue in issues if include(issue)]
        self._filtered = filtered
        self._reload_tree()

    def _reload_tree(self) -> None:
        self._issue_map.clear()
        self.tree.delete(*self.tree.get_children())
        for issue in self._filtered:
            iid = issue.key
            self._issue_map[iid] = issue
            self.tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(
                    issue.key,
                    issue.summary,
                    f"{issue.project_name} ({issue.project_key})",
                    issue.status,
                    issue.priority,
                    self._format_datetime(issue.updated),
                    self._format_date(issue.due_date),
                    self._format_source(issue),
                ),
            )
        if self._filtered:
            first = self._filtered[0].key
            self.tree.selection_set(first)
            self.tree.focus(first)
            self._show_issue_detail(self._filtered[0])
        else:
            self._show_issue_detail(None)

    def _on_tree_select(self) -> None:
        selection = self.tree.selection()
        if not selection:
            self._show_issue_detail(None)
            return
        issue = self._issue_map.get(selection[0])
        self._show_issue_detail(issue)

    def _show_issue_detail(self, issue: Optional[JiraIssue]) -> None:
        self._selected_issue = issue
        if issue is None:
            self.detail_title.configure(text="Select an issue to view details.")
            self.detail_meta.configure(text="")
            self._set_detail_text("")
            self.open_btn.configure(state=tk.DISABLED)
            return
        self.detail_title.configure(text=f"{issue.key} · {issue.summary}")
        meta_parts = []
        if issue.status:
            meta_parts.append(f"Status: {issue.status}")
        if issue.priority:
            meta_parts.append(f"Priority: {issue.priority}")
        if issue.assignee:
            meta_parts.append(f"Assignee: {issue.assignee}")
        if issue.due_date:
            meta_parts.append(f"Due: {issue.due_date.isoformat()}")
        if issue.updated:
            meta_parts.append(f"Updated: {self._format_datetime(issue.updated)}")
        self.detail_meta.configure(text=" · ".join(meta_parts))
        description = issue.description or "(no description provided)"
        self._set_detail_text(description.strip())
        self.open_btn.configure(state=tk.NORMAL)

    def _set_detail_text(self, text: str) -> None:
        self.detail_text.configure(state=tk.NORMAL)
        self.detail_text.delete("1.0", tk.END)
        self.detail_text.insert("1.0", text.strip())
        self.detail_text.configure(state=tk.DISABLED)

    def _open_selected_issue(self) -> None:
        if self._locked:
            self.notify_locked()
            return
        if not self._selected_issue:
            return
        webbrowser.open(self._selected_issue.url)

    def _show_configuration_message(self) -> None:
        if not self.config_frame.winfo_ismapped():
            self.config_frame.pack(fill=tk.X, pady=(12, 0))

    def _clear_results(self) -> None:
        self._issues = []
        self._filtered = []
        self._issue_map.clear()
        self.tree.delete(*self.tree.get_children())
        self._show_issue_detail(None)
        self.last_sync_var.set("")

    # ------------------------------------------------------------------ Formatting helpers
    @staticmethod
    def _format_datetime(value: Optional[datetime]) -> str:
        if value is None:
            return "--"
        return value.strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def _format_date(value: Optional[date]) -> str:
        if value is None:
            return "--"
        return value.isoformat()

    @staticmethod
    def _format_source(issue: JiraIssue) -> str:
        sources = []
        if issue.is_assigned:
            sources.append("Assigned")
        if issue.is_watched:
            sources.append("Watched")
        return " & ".join(sources) if sources else "Other"

    # ------------------------------------------------------------------ Lock overlay
    def _show_lock_overlay(self) -> None:
        if not self._locked or self._lock_overlay is not None:
            return
        overlay = tk.Frame(self, bg=self.theme.window_bg)
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        overlay.lift()
        self._lock_overlay = overlay
        card = ttk.Frame(overlay, padding=24)
        card.place(relx=0.5, rely=0.5, anchor="center")
        ttk.Label(
            card,
            text="JIRA tab is under development.",
            style="SidebarHeading.TLabel",
            wraplength=420,
            justify="center",
        ).pack(anchor="center")
        ttk.Label(
            card,
            text="JIRA tab will allow you to use their special authorized API keys to integrate ticket details directly into your workflow.",
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
        self.on_settings_updated()

    def _set_locked_state(self) -> None:
        self.status_var.set("This tab is under development. Enter the PIN to unlock.")
        self.last_sync_var.set("")
        self.refresh_btn.configure(state=tk.DISABLED)

    def is_locked(self) -> bool:
        return self._locked

    def focus_lock_entry(self) -> None:
        if self._pin_entry is not None:
            self._pin_entry.focus_set()

    def notify_locked(self) -> None:
        messagebox.showinfo("Jira", "Enter the PIN to unlock this tab.", parent=self)
        self.focus_lock_entry()


__all__ = ["JiraTabView"]
