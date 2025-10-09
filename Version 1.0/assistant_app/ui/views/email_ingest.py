from __future__ import annotations

import json
import os
import queue
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, simpledialog
from tkinter import ttk
from typing import Callable, List, Optional
from ...plugins import (
    ConfigPersistenceError,
    EmailIngestManager,
    EmailIngestResult,
    EmailRunConfig,
    OutlookUnavailableError,
)


class EmailIngestView(ttk.Frame):
    def __init__(self, master: tk.Misc, manager: EmailIngestManager):
        super().__init__(master, padding=(16, 16))
        self.manager = manager
        self.current_config: Optional[EmailRunConfig] = None
        self.available_folders: List[str] = []
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self._configs: List[EmailRunConfig] = []
        self._runner: Optional[threading.Thread] = None
        self._cancel_requested = False
        self._config_error_shown = False
        self._build_ui()
        self._show_brief_summary("No summary yet", "")
        self.after(200, self._drain_log_queue)
        self._refresh_dependency_status()
        self.refresh_configs()

    # ------------------------------------------------------------------ UI setup
    def _build_ui(self) -> None:
        container = ttk.Frame(self)
        container.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(container)
        left.grid(row=0, column=0, sticky="ns")
        ttk.Label(left, text="Run Configurations", style="SidebarHeading.TLabel").pack(anchor="w")

        list_frame = ttk.Frame(left)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 12))
        self.config_list = tk.Listbox(list_frame, height=12, exportselection=False)
        self.config_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.config_list.bind("<<ListboxSelect>>", lambda _: self._on_select_config())
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.config_list.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.config_list.configure(yscrollcommand=scrollbar.set)

        ttk.Button(left, text="New", command=self.create_new_config).pack(fill=tk.X)
        ttk.Button(left, text="Reload", command=self.refresh_configs).pack(fill=tk.X, pady=(6, 0))

        main = ttk.Frame(container)
        main.grid(row=0, column=1, sticky="nsew", padx=(16, 0))
        main.columnconfigure(1, weight=1)

        ttk.Label(main, text="Run Name").grid(row=0, column=0, sticky="w")
        self.run_name_var = tk.StringVar()
        ttk.Entry(main, textvariable=self.run_name_var).grid(row=0, column=1, sticky="ew", padx=(12, 0))

        ttk.Label(main, text="Description").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.description_var = tk.StringVar()
        ttk.Entry(main, textvariable=self.description_var).grid(row=1, column=1, sticky="ew", padx=(12, 0))

        ttk.Label(main, text="Outlook Profile").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.profile_var = tk.StringVar()
        ttk.Entry(main, textvariable=self.profile_var).grid(row=2, column=1, sticky="ew", padx=(12, 0))

        ttk.Label(main, text="Shard Label").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.shard_label_var = tk.StringVar()
        ttk.Entry(main, textvariable=self.shard_label_var).grid(row=3, column=1, sticky="ew", padx=(12, 0))

        ttk.Label(main, text="Folders").grid(row=4, column=0, sticky="nw", pady=(12, 0))
        folder_frame = ttk.Frame(main)
        folder_frame.grid(row=4, column=1, sticky="nsew", padx=(12, 0))
        folder_frame.columnconfigure(0, weight=1)
        folder_frame.rowconfigure(0, weight=1)

        self.folder_list = tk.Listbox(folder_frame, selectmode=tk.MULTIPLE, height=8, exportselection=False)
        self.folder_list.grid(row=0, column=0, sticky="nsew")
        folder_scroll = ttk.Scrollbar(folder_frame, orient=tk.VERTICAL, command=self.folder_list.yview)
        folder_scroll.grid(row=0, column=1, sticky="ns")
        self.folder_list.configure(yscrollcommand=folder_scroll.set)

        ttk.Button(folder_frame, text="Refresh Folders", command=self.refresh_folders).grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )

        self.include_subfolders_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(main, text="Include subfolders", variable=self.include_subfolders_var).grid(
            row=5, column=1, sticky="w", pady=(12, 0)
        )

        self.summarize_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(main, text="Summarize after ingest", variable=self.summarize_var).grid(
            row=6, column=1, sticky="w", pady=(4, 0)
        )

        self.dependency_label_var = tk.StringVar(value="")
        ttk.Label(main, textvariable=self.dependency_label_var, foreground="#F4B942").grid(
            row=7, column=1, sticky="w", pady=(4, 0)
        )

        button_bar = ttk.Frame(main)
        button_bar.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        for idx in range(5):
            button_bar.columnconfigure(idx, weight=1)

        self.save_button = ttk.Button(button_bar, text="Save Run Config", command=self.save_config)
        self.save_button.grid(row=0, column=0, sticky="ew")
        self.run_button = ttk.Button(button_bar, text="Run Now", command=self.run_now)
        self.run_button.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.cancel_button = ttk.Button(button_bar, text="Cancel Run", command=self.cancel_run, state=tk.DISABLED)
        self.cancel_button.grid(row=0, column=2, sticky="ew", padx=(8, 0))
        ttk.Button(button_bar, text="Open Shard Folder", command=self.open_shard_folder).grid(
            row=0, column=3, sticky="ew", padx=(8, 0)
        )
        ttk.Button(button_bar, text="View Last Summary", command=self.view_last_summary).grid(
            row=0, column=4, sticky="ew", padx=(8, 0)
        )

        ttk.Button(main, text="Install Dependencies", command=self.install_dependencies).grid(
            row=9, column=1, sticky="w", pady=(12, 0)
        )

        ttk.Label(main, text="Status").grid(row=10, column=0, sticky="nw", pady=(16, 0))
        status_frame = ttk.Frame(main)
        status_frame.grid(row=10, column=1, sticky="nsew", padx=(12, 0))
        status_frame.columnconfigure(0, weight=1)
        status_frame.rowconfigure(0, weight=1)
        self.status_text = tk.Text(status_frame, height=10, wrap="word", state="disabled")
        self.status_text.grid(row=0, column=0, sticky="nsew")
        status_scroll = ttk.Scrollbar(status_frame, orient=tk.VERTICAL, command=self.status_text.yview)
        status_scroll.grid(row=0, column=1, sticky="ns")
        self.status_text.configure(yscrollcommand=status_scroll.set)

        ttk.Label(main, text="Latest Briefing").grid(row=11, column=0, sticky="nw", pady=(16, 0))
        brief_frame = ttk.Frame(main)
        brief_frame.grid(row=11, column=1, sticky="nsew", padx=(12, 0))
        brief_frame.columnconfigure(0, weight=1)
        brief_frame.rowconfigure(1, weight=1)
        self.latest_summary_label = ttk.Label(brief_frame, text="No summary yet")
        self.latest_summary_label.grid(row=0, column=0, sticky="w")
        self.latest_summary_text = tk.Text(brief_frame, height=12, wrap="word", state="disabled")
        self.latest_summary_text.grid(row=1, column=0, sticky="nsew")
        brief_scroll = ttk.Scrollbar(brief_frame, orient=tk.VERTICAL, command=self.latest_summary_text.yview)
        brief_scroll.grid(row=1, column=1, sticky="ns")
        self.latest_summary_text.configure(yscrollcommand=brief_scroll.set)
        ttk.Button(brief_frame, text="Copy Briefing", command=self.copy_briefing).grid(
            row=2, column=0, sticky="e", pady=(8, 0)
        )

        main.rowconfigure(10, weight=1)
        main.rowconfigure(11, weight=1)

    # ------------------------------------------------------------------ Config management
    def refresh_configs(self, focus_run_id: Optional[str] = None) -> None:
        self.config_list.delete(0, tk.END)
        try:
            self._configs = self.manager.list_configs()
        except ConfigPersistenceError as exc:
            self._log(f"Config error: {exc}")
            if not self._config_error_shown:
                messagebox.showerror("Config", str(exc), parent=self)
                self._config_error_shown = True
            self._configs = []
            return
        self._config_error_shown = False
        target_run_id = focus_run_id or (self.current_config.run_id if self.current_config else None)
        target_index = 0
        for index, config in enumerate(self._configs):
            display = f"{config.run_id}"
            if config.description:
                display += f" - {config.description}"
            self.config_list.insert(tk.END, display)
            if target_run_id and config.run_id == target_run_id:
                target_index = index
        if self._configs:
            self.config_list.selection_set(target_index)
            self._load_config(self._configs[target_index])
        else:
            self._load_config(None)

    def _on_select_config(self) -> None:
        selection = self.config_list.curselection()
        if not selection:
            return
        index = selection[0]
        if 0 <= index < len(self._configs):
            self._load_config(self._configs[index])

    def _load_config(self, config: Optional[EmailRunConfig]) -> None:
        self.current_config = config
        if config is None:
            self.run_name_var.set("")
            self.description_var.set("")
            self.profile_var.set("")
            self.shard_label_var.set(datetime.now().strftime("%Y-%m"))
            self.include_subfolders_var.set(True)
            self.summarize_var.set(True)
            self.folder_list.selection_clear(0, tk.END)
            self._load_latest_brief(None)
            return
        self.run_name_var.set(config.run_id)
        self.description_var.set(config.description)
        self.profile_var.set(config.profile_name or "")
        self.shard_label_var.set(config.next_shard_label or datetime.now().strftime("%Y-%m"))
        self.include_subfolders_var.set(config.include_subfolders)
        self.summarize_var.set(config.summarize_after_ingest)
        self._sync_folder_selection(config.include_folders)
        if config.last_ingested:
            self._log(f"Last ingested: {config.last_ingested:%Y-%m-%d %H:%M}")
        self._load_latest_brief(config)

    def _sync_folder_selection(self, selected_paths: List[str]) -> None:
        if not self.available_folders:
            return
        self.folder_list.selection_clear(0, tk.END)
        lower_map = {path.lower(): idx for idx, path in enumerate(self.available_folders)}
        for path in selected_paths:
            idx = lower_map.get(path.lower())
            if idx is not None:
                self.folder_list.selection_set(idx)

    def create_new_config(self) -> None:
        run_name = simpledialog.askstring("New Run", "Enter run name:", parent=self)
        if not run_name:
            return
        run_name = run_name.strip().replace(" ", "_")
        config = self.manager.create_default_config(run_name)
        try:
            self.manager.save_config(config)
        except ConfigPersistenceError as exc:
            messagebox.showerror("Save Failed", str(exc), parent=self)
            return
        self.refresh_configs(config.run_id)
        self._log(f"Created new config {config.run_id}")

    def save_config(self) -> None:
        config = self._collect_form_data()
        if not config:
            return
        try:
            self.manager.save_config(config)
        except ConfigPersistenceError as exc:
            messagebox.showerror("Save Failed", str(exc), parent=self)
            return
        self._log(f"Saved configuration: {config.run_id}")
        self.refresh_configs(config.run_id)

    def _collect_form_data(self) -> Optional[EmailRunConfig]:
        run_name = self.run_name_var.get().strip()
        if not run_name:
            messagebox.showwarning("Validation", "Run name is required.", parent=self)
            return None
        run_name = run_name.replace(" ", "_")
        config = self.current_config or self.manager.create_default_config(run_name)
        config.run_id = run_name
        config.description = self.description_var.get().strip()
        config.profile_name = self.profile_var.get().strip() or None
        config.next_shard_label = self.shard_label_var.get().strip() or None
        config.include_subfolders = self.include_subfolders_var.get()
        config.summarize_after_ingest = self.summarize_var.get()
        if self.folder_list.size() == 0:
            # No folder list has been loaded yet; keep whatever was previously saved.
            selected_folders: Optional[List[str]] = None
        else:
            selection_indices = self.folder_list.curselection()
            selected = [self.available_folders[i] for i in selection_indices]
            selected_folders = selected if selection_indices else []
        if selected_folders is not None:
            config.include_folders = selected_folders
        return config

    # ------------------------------------------------------------------ Outlook folders
    def refresh_folders(self) -> None:
        profile_name = self.profile_var.get().strip() or None

        def worker() -> None:
            try:
                self.log_queue.put("Connecting to Outlook...")
                def progress(message: str) -> None:
                    self.log_queue.put(message)
                folders = self.manager.list_outlook_folders(profile_name, progress=progress)
                self.log_queue.put(f"Received {len(folders)} folders from Outlook")
            except OutlookUnavailableError as exc:
                self.log_queue.put(f"Outlook error: {exc}")
                self._async(lambda exc=exc: messagebox.showerror("Outlook", str(exc), parent=self))
                return
            except Exception as exc:
                self.log_queue.put(f"Refresh failed: {exc}")
                self._async(lambda exc=exc: messagebox.showerror("Refresh Folders", str(exc), parent=self))
                return
            self._async(lambda: self._update_folder_list(folders))

        self.log_queue.put(f"Refreshing folders (profile: {profile_name or 'default'})...")
        threading.Thread(target=worker, daemon=True).start()

    def _update_folder_list(self, folders: List[str]) -> None:
        self.available_folders = folders
        self.folder_list.delete(0, tk.END)
        for folder in folders:
            self.folder_list.insert(tk.END, folder)
        if self.current_config:
            self._sync_folder_selection(self.current_config.include_folders)
        self._log(f"Loaded {len(folders)} folders")

    # ------------------------------------------------------------------ Dependency helpers
    def _refresh_dependency_status(self) -> None:
        report = self.manager.dependency_report()
        if report.available:
            self.dependency_label_var.set("Summarizer ready")
        else:
            missing = ", ".join(report.missing)
            self.dependency_label_var.set(f"Missing packages: {missing}")

    def install_dependencies(self) -> None:
        if self._runner and self._runner.is_alive():
            messagebox.showinfo("Busy", "Wait for the current operation to finish.", parent=self)
            return

        def observer(line: str) -> None:
            self.log_queue.put(line)

        def worker() -> None:
            exit_code = self.manager.install_dependencies(observer)
            if exit_code == 0:
                self.log_queue.put("Dependency install complete")
            else:
                self.log_queue.put(f"Dependency install failed (code {exit_code})")
            self._async(self._refresh_dependency_status)

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ Run execution
    def run_now(self) -> None:
        if self._runner and self._runner.is_alive():
            messagebox.showinfo("In Progress", "A run is already in progress.", parent=self)
            return
        config = self._collect_form_data()
        if not config:
            return
        if not config.include_folders:
            proceed = messagebox.askyesno(
                "No Folders",
                "No folders selected. Run anyway?",
                parent=self,
            )
            if not proceed:
                return

        run_id = config.run_id
        self._cancel_requested = False
        self._toggle_actions(False)
        self._log(f"Starting run {run_id}")

        def progress(message: str) -> None:
            self.log_queue.put(message)

        def worker() -> None:
            try:
                result = self.manager.run_now(config, progress)
            except OutlookUnavailableError as exc:
                self.log_queue.put(f"Run failed: {exc}")
                self._async(lambda: self._handle_run_exception("Outlook", str(exc), run_id))
                return
            except Exception as exc:  # pragma: no cover - runtime safety
                self.log_queue.put(f"Run failed: {exc}")
                self._async(lambda: self._handle_run_exception("Run Failed", str(exc), run_id))
                return
            self._async(lambda: self._handle_run_result(result, run_id))

        self._runner = threading.Thread(target=worker, daemon=True)
        self._runner.start()

    def cancel_run(self) -> None:
        if not self._runner or not self._runner.is_alive():
            return
        if self._cancel_requested:
            return
        self._cancel_requested = True
        self.manager.cancel_current_run()
        self.cancel_button.configure(state=tk.DISABLED)
        self.log_queue.put("Cancellation requested. Waiting for current operations to finish...")

    def _handle_run_result(self, result: EmailIngestResult, run_id: str) -> None:
        if result.cancelled:
            self.log_queue.put(
                f"Run cancelled -> {result.inserted} emails ingested, {result.summarized} summaries."
            )
        else:
            self.log_queue.put(
                f"Run complete -> {result.inserted} emails ingested, {result.summarized} summaries."
            )
        if result.summary_path and not result.cancelled:
            self.log_queue.put(f"Summary saved to {result.summary_path}")
        if result.report_path:
            self.log_queue.put(f"Run report saved to {result.report_path}")
        if result.newest_timestamp:
            self.log_queue.put(f"Last ingested updated to {result.newest_timestamp:%Y-%m-%d %H:%M}")
        title = f"Run {result.run_token or run_id} - {result.completed_at.strftime('%Y-%m-%d %H:%M') if result.completed_at else 'completed'}"
        self._show_brief_summary(title, result.brief_summary or '')
        self._runner = None
        self._cancel_requested = False
        self._toggle_actions(True)
        self.refresh_configs(run_id)

    def _handle_run_exception(self, title: str, message: str, run_id: str) -> None:
        self._runner = None
        self._cancel_requested = False
        self._toggle_actions(True)
        messagebox.showerror(title, message, parent=self)
        self.refresh_configs(run_id)

    def _toggle_actions(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        self.run_button.configure(state=state)
        self.save_button.configure(state=state)
        if hasattr(self, 'cancel_button'):
            self.cancel_button.configure(state=tk.DISABLED if enabled else tk.NORMAL)
        if enabled:
            self.cancel_button.configure(state=tk.DISABLED)
        else:
            self.cancel_button.configure(state=tk.NORMAL)

    # ------------------------------------------------------------------ Utilities
    def _drain_log_queue(self) -> None:
        drained = False
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            drained = True
            self._log(message)
        if drained:
            self.status_text.see(tk.END)
        self.after(200, self._drain_log_queue)

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.status_text.configure(state="normal")
        self.status_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.status_text.configure(state="disabled")

    def _show_brief_summary(self, title: str, content: str) -> None:
        self.latest_summary_label.configure(text=title or "Latest Briefing")
        self.latest_summary_text.configure(state="normal")
        self.latest_summary_text.delete("1.0", tk.END)
        cleaned = content.strip() if content else ""
        self.latest_summary_text.insert(tk.END, cleaned or "(no summary available)")
        self.latest_summary_text.configure(state="disabled")

    def _load_latest_brief(self, config: Optional[EmailRunConfig]) -> None:
        if not config:
            self._show_brief_summary("No summary available", "")
            return
        runs_dir = config.run_dir / "runs"
        if not runs_dir.exists():
            self._show_brief_summary("No summary available", "")
            return
        reports = sorted(runs_dir.glob("run_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not reports:
            self._show_brief_summary("No summary available", "")
            return
        latest = reports[0]
        try:
            data = json.loads(latest.read_text(encoding="utf-8"))
        except Exception as exc:
            self._show_brief_summary("Unable to load last summary", str(exc))
            return
        title = f"Run {data.get('run_token', latest.stem)} — {data.get('completed_at', 'unknown finish')}"
        summary_text = data.get("brief_summary") or "(no summary available)"
        self._show_brief_summary(title, summary_text)

    def open_shard_folder(self) -> None:
        config = self.current_config or self._collect_form_data()
        if not config:
            return
        folder = config.shard_dir
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(folder)  # type: ignore[attr-defined]
        except AttributeError:
            messagebox.showinfo("Open Folder", str(folder), parent=self)

    def view_last_summary(self) -> None:
        config = self.current_config or self._collect_form_data()
        if not config:
            return
        summaries_dir = config.summaries_dir
        if not summaries_dir.exists():
            messagebox.showinfo("Summaries", "No summaries available yet.", parent=self)
            return
        summaries = sorted(summaries_dir.glob("summary_*.txt"))
        if not summaries:
            messagebox.showinfo("Summaries", "No summaries available yet.", parent=self)
            return
        latest = summaries[-1]
        try:
            os.startfile(latest)  # type: ignore[attr-defined]
        except AttributeError:
            messagebox.showinfo("Summary", str(latest), parent=self)

    def copy_briefing(self) -> None:
        content = self.latest_summary_text.get("1.0", tk.END).strip()
        if not content:
            messagebox.showinfo("Copy Briefing", "No summary available to copy.", parent=self)
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(content)
            self._log("Briefing copied to clipboard.")
        except Exception as exc:
            messagebox.showerror("Copy Failed", str(exc), parent=self)

    def _async(self, func: Callable[[], None]) -> None:
        self.after(0, func)



