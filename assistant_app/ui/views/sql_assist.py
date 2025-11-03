from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Dict, List, Optional

from ...database import Database
from ...models import (
    SqlColumn,
    SqlInstance,
    SqlTable,
    SqlDataSource,
    SqlDataSourceJoin,
    SqlDataSourceExpression,
    SqlDataSourceDetail,
)


class SqlAssistView(ttk.Frame):
    """SQL Assist workspace guarded by a simple PIN until the feature ships."""

    _PIN_CODE = "12345"

    def __init__(self, master: tk.Misc, db: Database) -> None:
        super().__init__(master, padding=(16, 16))
        self.db = db

        self._locked = True
        self._lock_overlay: Optional[tk.Frame] = None
        self._pin_entry: Optional[ttk.Entry] = None
        self._pin_var = tk.StringVar(value="")
        self._lock_error_var = tk.StringVar(value="")

        self.instances: list[SqlInstance] = []
        self.current_instance_id: Optional[int] = None
        self._last_import_backup: Optional[dict[str, object]] = None
        self._last_import_instance_id: Optional[int] = None

        self.instance_var = tk.StringVar()
        self.summary_var = tk.StringVar(value="Unlock the tab to manage SQL metadata.")
        self.search_field_var = tk.StringVar(value="Table")
        self.search_text_var = tk.StringVar(value="")
        self.data_source_search_var = tk.StringVar(value="")

        self.updated_var = tk.StringVar(value="Last updated: --")

        self._all_tables: list[SqlTable] = []
        self._base_table_count = 0
        self._base_column_count = 0
        self._table_by_id: dict[int, SqlTable] = {}
        self._tree_table_map: dict[str, SqlTable] = {}
        self._all_data_sources: List[SqlDataSource] = []
        self._source_item_map: Dict[str, SqlDataSource] = {}

        self._build_ui()
        self.data_source_search_var.trace_add("write", lambda *_: self._apply_data_source_filter())
        self.search_text_var.trace_add("write", lambda *_: self._apply_table_filter())
        self.after(0, self._show_lock_overlay)

    # ------------------------------------------------------------------ UI construction
    def _build_ui(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill=tk.X)
        header.columnconfigure(0, weight=1)

        controls_frame = ttk.Frame(header)
        controls_frame.grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.updated_var).grid(row=0, column=1, sticky="e")

        ttk.Label(controls_frame, text="Instance", style="SidebarHeading.TLabel").pack(side=tk.LEFT)

        self.instance_combo = ttk.Combobox(
            controls_frame,
            textvariable=self.instance_var,
            state="readonly",
            width=36,
        )
        self.instance_combo.pack(side=tk.LEFT, padx=(10, 6))
        self.instance_combo.bind("<<ComboboxSelected>>", lambda _: self._select_instance_by_name(self.instance_var.get()))

        ttk.Button(controls_frame, text="Add", command=self._create_instance).pack(side=tk.LEFT)
        self.delete_btn = ttk.Button(
            controls_frame,
            text="Delete",
            command=self._delete_instance,
            state=tk.DISABLED,
        )
        self.delete_btn.pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(controls_frame, text="Import...", command=self._import_instance).pack(side=tk.LEFT, padx=(6, 0))
        self.export_btn = ttk.Button(controls_frame, text="Export...", command=self._export_instance, state=tk.DISABLED)
        self.export_btn.pack(side=tk.LEFT, padx=(6, 0))

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        tables_tab = ttk.Frame(self.notebook)
        tables_tab.columnconfigure(0, weight=1)
        tables_tab.rowconfigure(2, weight=1)
        self.notebook.add(tables_tab, text="Tables & Columns")

        controls = ttk.Frame(tables_tab)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        controls.columnconfigure(2, weight=1)

        self.import_tables_btn = ttk.Button(
            controls,
            text="Import Tables & Columns",
            command=self._import_tables_from_csv,
            state=tk.DISABLED,
        )
        self.import_tables_btn.grid(row=0, column=0, sticky="w")
        self.undo_btn = ttk.Button(
            controls,
            text="Undo Last Import",
            command=self._undo_last_import,
            state=tk.DISABLED,
        )
        self.undo_btn.grid(row=0, column=1, padx=(6, 0), sticky="w")

        ttk.Label(controls, textvariable=self.summary_var).grid(row=0, column=2, sticky="e")

        search_frame = ttk.Frame(tables_tab)
        search_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        search_frame.columnconfigure(2, weight=1)
        ttk.Label(search_frame, text="Search:").grid(row=0, column=0, padx=(0, 6))
        self.search_field_combo = ttk.Combobox(
            search_frame,
            textvariable=self.search_field_var,
            values=("Table", "Column", "Description"),
            state="readonly",
            width=12,
        )
        self.search_field_combo.grid(row=0, column=1, padx=(0, 6))
        self.search_field_combo.bind("<<ComboboxSelected>>", lambda _: self._apply_table_filter())
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_text_var, width=32)
        self.search_entry.grid(row=0, column=2, sticky="ew")
        self.search_entry.bind("<KeyRelease>", lambda _: self._apply_table_filter())
        self.clear_search_btn = ttk.Button(search_frame, text="Clear", command=self._clear_search)
        self.clear_search_btn.grid(row=0, column=3, padx=(6, 0))
        self.search_field_combo.configure(state="disabled")
        self.search_entry.configure(state=tk.DISABLED)
        self.clear_search_btn.configure(state=tk.DISABLED)

        container = ttk.Frame(tables_tab)
        container.grid(row=2, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(container, show="tree")
        self.tree.heading("#0", text="Tables & Columns")
        self.tree.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        scrollbar = ttk.Scrollbar(container, orient=tk.VERTICAL, command=self.tree.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.sources_tab = ttk.Frame(self.notebook)
        self.sources_tab.columnconfigure(0, weight=1)
        self.sources_tab.rowconfigure(2, weight=1)
        self.notebook.add(self.sources_tab, text="Data Sources")

        sources_controls = ttk.Frame(self.sources_tab)
        sources_controls.grid(row=0, column=0, sticky="ew")
        self.import_sources_btn = ttk.Button(
            sources_controls,
            text="Import Data Sources",
            command=self._import_data_sources,
        )
        self.import_sources_btn.grid(row=0, column=0, padx=(0, 6))
        self.refresh_sources_btn = ttk.Button(
            sources_controls,
            text="Refresh",
            command=self._refresh_data_sources,
        )
        self.refresh_sources_btn.grid(row=0, column=1, padx=(0, 6))

        sources_search = ttk.Frame(self.sources_tab)
        sources_search.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        sources_search.columnconfigure(1, weight=1)
        ttk.Label(sources_search, text="Search:").grid(row=0, column=0, padx=(0, 6))
        self.data_source_search_entry = ttk.Entry(
            sources_search, textvariable=self.data_source_search_var, width=40
        )
        self.data_source_search_entry.grid(row=0, column=1, sticky="ew")
        self.data_source_search_entry.bind("<KeyRelease>", lambda _: self._apply_data_source_filter())
        self.data_source_search_clear = ttk.Button(
            sources_search, text="Clear", command=self._clear_data_source_search
        )
        self.data_source_search_clear.grid(row=0, column=2, padx=(6, 0))

        sources_container = ttk.Frame(self.sources_tab)
        sources_container.grid(row=2, column=0, sticky="nsew")
        sources_container.columnconfigure(0, weight=1)
        sources_container.rowconfigure(0, weight=1)

        self.sources_tree = ttk.Treeview(
            sources_container,
            columns=("title", "base", "updated", "status"),
            show="headings",
            height=12,
        )
        self.sources_tree.heading("title", text="Title")
        self.sources_tree.heading("base", text="Base")
        self.sources_tree.heading("updated", text="Last Updated")
        self.sources_tree.heading("status", text="Status")
        self.sources_tree.column("title", width=260, anchor="w")
        self.sources_tree.column("base", width=70, anchor="center")
        self.sources_tree.column("updated", width=160, anchor="center")
        self.sources_tree.column("status", width=260, anchor="w")
        self.sources_tree.bind("<Double-1>", self._open_data_source_detail)
        self.sources_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sources_scroll = ttk.Scrollbar(sources_container, orient=tk.VERTICAL, command=self.sources_tree.yview)
        sources_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.sources_tree.configure(yscrollcommand=sources_scroll.set)

        self.query_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.query_tab, text="Query Generation")
        ttk.Label(
            self.query_tab,
            text="Query templates and generation tools are coming soon.",
            wraplength=520,
            justify="center",
        ).pack(expand=True, pady=40, padx=24)

    # ------------------------------------------------------------------ Lock overlay
    def _show_lock_overlay(self) -> None:
        if not self._locked:
            return
        if self._lock_overlay is not None:
            self._lock_overlay.destroy()
        overlay = tk.Frame(self, bg="#111219")
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        overlay.lift()
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
        self.refresh_instances()
        self._refresh_data_sources()
        self.instance_combo.focus_set()

    # ------------------------------------------------------------------ Data management
    def refresh_instances(self, *, select_instance: Optional[int] = None) -> None:
        self.instances = self.db.get_sql_instances()
        names = [instance.name for instance in self.instances]
        self.instance_combo["values"] = names

        target_id = select_instance or self.current_instance_id
        selected_instance: Optional[SqlInstance] = None
        if target_id is not None:
            for instance in self.instances:
                if instance.id == target_id:
                    self.instance_var.set(instance.name)
                    self.current_instance_id = instance.id
                    selected_instance = instance
                    self._load_tables(instance.id)
                    break
            else:
                self.current_instance_id = None
                selected_instance = None
                self._all_tables = []
                self._base_table_count = 0
                self._base_column_count = 0
                self._populate_tree([])
        elif names:
            selected_instance = self.instances[0]
            self.instance_var.set(selected_instance.name)
            self.current_instance_id = selected_instance.id
            self._load_tables(self.current_instance_id)
        else:
            self.instance_var.set("")
            self.current_instance_id = None
            self._all_tables = []
            self._table_by_id = {}
            self._tree_table_map = {}
            self._base_table_count = 0
            self._base_column_count = 0
            self._populate_tree([])

        has_instance = self.current_instance_id is not None
        self.export_btn.configure(state=tk.NORMAL if has_instance else tk.DISABLED)
        self.import_tables_btn.configure(state=tk.NORMAL if has_instance else tk.DISABLED)
        self.delete_btn.configure(state=tk.NORMAL if has_instance else tk.DISABLED)
        if has_instance and self._last_import_instance_id == self.current_instance_id and self._last_import_backup:
            self.undo_btn.configure(state=tk.NORMAL)
        else:
            self.undo_btn.configure(state=tk.DISABLED)
        entry_state = tk.NORMAL if has_instance else tk.DISABLED
        combo_state = "readonly" if has_instance else "disabled"
        self.search_entry.configure(state=entry_state)
        self.clear_search_btn.configure(state=tk.NORMAL if has_instance else tk.DISABLED)
        self.search_field_combo.configure(state=combo_state)
        if not has_instance:
            if self.search_text_var.get():
                self.search_text_var.set("")
            self.summary_var.set("Create or import an instance to begin.")
        self._update_instance_header(selected_instance if has_instance else None)

    def _select_instance_by_name(self, name: str) -> None:
        for instance in self.instances:
            if instance.name == name:
                self.current_instance_id = instance.id
                self._load_tables(instance.id)
                self.export_btn.configure(state=tk.NORMAL)
                self.import_tables_btn.configure(state=tk.NORMAL)
                if (
                    self._last_import_backup
                    and self._last_import_instance_id == self.current_instance_id
                ):
                    self.undo_btn.configure(state=tk.NORMAL)
                else:
                    self.undo_btn.configure(state=tk.DISABLED)
                self._update_instance_header(instance)
                return
        self.current_instance_id = None
        self._all_tables = []
        self._table_by_id = {}
        self._tree_table_map = {}
        self._base_table_count = 0
        self._base_column_count = 0
        self._populate_tree([])
        self.export_btn.configure(state=tk.DISABLED)
        self.import_tables_btn.configure(state=tk.DISABLED)
        self.delete_btn.configure(state=tk.DISABLED)
        self.undo_btn.configure(state=tk.DISABLED)
        self._update_instance_header(None)

    def _load_tables(self, instance_id: int) -> None:
        tables = self.db.get_sql_tables_with_columns(instance_id)
        self._all_tables = tables
        self._table_by_id = {table.id: table for table in tables}
        self._base_table_count = len(tables)
        self._base_column_count = sum(len(table.columns) for table in tables)
        self._apply_table_filter()

    def _apply_table_filter(self, *_: object) -> None:
        if self.current_instance_id is None:
            self._populate_tree([])
            return
        base_tables = self._all_tables or []
        if not base_tables:
            self._populate_tree([])
            if self.search_text_var.get().strip():
                self.summary_var.set("No matching results.")
            else:
                self.summary_var.set("No tables imported yet for this instance.")
            return
        query = self.search_text_var.get().strip().lower()
        mode = self.search_field_var.get()
        filtered_tables: list[SqlTable] = []
        if not query:
            filtered_tables = base_tables
        else:
            if mode == "Table":
                for table in base_tables:
                    if query in table.name.lower():
                        filtered_tables.append(table)
            elif mode == "Column":
                for table in base_tables:
                    matches = [
                        SqlColumn(name=col.name, description=col.description)
                        for col in table.columns
                        if query in col.name.lower()
                    ]
                    if matches:
                        filtered_tables.append(
                            SqlTable(
                                id=table.id,
                                name=table.name,
                                description=table.description,
                                columns=matches,
                            )
                        )
            else:
                for table in base_tables:
                    table_desc = (table.description or "").lower()
                    if table_desc and query in table_desc:
                        filtered_tables.append(table)
                        continue
                    matches = [
                        SqlColumn(name=col.name, description=col.description)
                        for col in table.columns
                        if (col.description or "").lower().find(query) != -1
                    ]
                    if matches:
                        filtered_tables.append(
                            SqlTable(
                                id=table.id,
                                name=table.name,
                                description=table.description,
                                columns=matches,
                            )
                        )
        self._populate_tree(filtered_tables)
        if not query:
            if self._base_table_count == 0:
                self.summary_var.set("No tables imported yet for this instance.")
            else:
                self.summary_var.set(f"{self._base_table_count} table(s) | {self._base_column_count} column(s)")
            return
        filtered_table_count = len(filtered_tables)
        filtered_column_count = sum(len(table.columns) for table in filtered_tables)
        if filtered_table_count == 0:
            self.summary_var.set(
                f"No matching results (from {self._base_table_count} table(s) / {self._base_column_count} column(s))"
            )
        else:
            self.summary_var.set(
                f"{filtered_table_count} table(s) | {filtered_column_count} column(s) (filtered from {self._base_table_count} / {self._base_column_count})"
            )

    def _clear_search(self) -> None:
        if self.search_text_var.get():
            self.search_text_var.set("")
        elif self.current_instance_id is not None:
            self._apply_table_filter()
        if self.current_instance_id is not None:
            self.search_entry.focus_set()


    def _populate_tree(self, tables: list[SqlTable]) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._tree_table_map = {}
        for table in tables:
            base_table = self._table_by_id.get(table.id, table)
            parent = self.tree.insert("", tk.END, text=table.name, open=False)
            self._tree_table_map[parent] = base_table
            ordered_columns = sorted(table.columns, key=lambda value: value.name.lower())
            for column in ordered_columns:
                label = column.name
                if column.description:
                    label = f"{column.name} - {column.description}"
                self.tree.insert(parent, tk.END, text=label)

    def _refresh_data_sources(self) -> None:
        try:
            self._all_data_sources = self.db.get_sql_data_sources()
        except Exception as exc:
            self._all_data_sources = []
            messagebox.showerror("Data Sources", f"Unable to load data sources: {exc}", parent=self)
        self._apply_data_source_filter()

    def _apply_data_source_filter(self) -> None:
        search = self.data_source_search_var.get().strip().lower()
        filtered: List[SqlDataSource] = []
        for source in self._all_data_sources:
            haystacks = [source.title.lower()]
            if source.description:
                haystacks.append(source.description.lower())
            if not search or any(search in hay for hay in haystacks):
                filtered.append(source)

        for item in self.sources_tree.get_children():
            self.sources_tree.delete(item)
        self._source_item_map.clear()

        if not self._all_data_sources:
            self.data_source_search_entry.configure(state=tk.DISABLED)
            self.data_source_search_clear.configure(state=tk.DISABLED)
        else:
            self.data_source_search_entry.configure(state=tk.NORMAL)
            self.data_source_search_clear.configure(state=tk.NORMAL)

        for source in filtered:
            status_parts: List[str] = []
            if source.is_in_error:
                status_parts.append(
                    f"Error: {source.error_message}" if source.error_message else "Error"
                )
            if source.parent_source:
                status_parts.append(f"Parent: {source.parent_source}")
            if source.is_visible is not None:
                status_parts.append(f"Visible: {self._format_bool(source.is_visible)}")

            updated_display = self._format_timestamp(source.updated_at, source.updated_by)
            item = self.sources_tree.insert(
                "",
                tk.END,
                values=(
                    source.title,
                    self._format_bool(source.is_base),
                    updated_display,
                    "; ".join(status_parts),
                ),
            )
            self._source_item_map[item] = source

        if not filtered and self._all_data_sources:
            # No matches for current search
            self.sources_tree.insert(
                "",
                tk.END,
                values=("No matching data sources found.", "", "", ""),
            )

    def _clear_data_source_search(self) -> None:
        if self.data_source_search_var.get():
            self.data_source_search_var.set("")
        else:
            self._apply_data_source_filter()

    def _open_data_source_detail(self, event: tk.Event) -> None:
        item_id = self.sources_tree.focus()
        if not item_id:
            return
        source = self._source_item_map.get(item_id)
        if source is None:
            return
        try:
            detail = self.db.get_sql_data_source_details(source.id)  # type: ignore[arg-type]
        except Exception as exc:
            messagebox.showerror("Data Source", f"Unable to load details: {exc}", parent=self)
            return
        self._show_data_source_detail(detail)

    def _show_data_source_detail(self, detail: SqlDataSourceDetail) -> None:
        source = detail.source
        dialog = tk.Toplevel(self)
        dialog.title(source.title or "Data Source Detail")
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()

        container = ttk.Frame(dialog, padding=16)
        container.pack(fill=tk.BOTH, expand=True)

        ttk.Label(container, text=source.title, style="SidebarHeading.TLabel").pack(anchor="w")

        description = (source.description or "").strip()
        if description:
            ttk.Label(container, text=description, wraplength=560, justify="left").pack(anchor="w", pady=(6, 12))

        info_items = [
            ("Is Base Data Source", self._format_bool(source.is_base)),
            ("Parent Data Source", source.parent_source or "N/A"),
            ("Select Set", source.select_set or "N/A"),
            ("Visible", self._format_bool(source.is_visible) if source.is_visible is not None else "Unknown"),
            ("Last Updated", self._format_timestamp(source.updated_at, source.updated_by)),
        ]
        if source.is_in_error:
            info_items.append(("Error", source.error_message or "Yes"))

        info_frame = ttk.Frame(container)
        info_frame.pack(fill=tk.X, pady=(0, 12))
        for label, value in info_items:
            ttk.Label(info_frame, text=f"{label}: {value}").pack(anchor="w")

        notebook = ttk.Notebook(container)
        notebook.pack(fill=tk.BOTH, expand=True)

        joins_tab = ttk.Frame(notebook)
        joins_tab.columnconfigure(0, weight=1)
        joins_tab.rowconfigure(0, weight=1)
        notebook.add(joins_tab, text="Joins")

        joins_columns = (
            "alias",
            "object",
            "type",
            "index",
            "row_expected",
            "base_join",
            "updated",
            "status",
        )
        joins_tree = ttk.Treeview(joins_tab, columns=joins_columns, show="headings", height=12)
        headings = {
            "alias": "Alias",
            "object": "Join Object",
            "type": "Relationship",
            "index": "Join Index",
            "row_expected": "Row Expected",
            "base_join": "Base Join",
            "updated": "Updated",
            "status": "Status",
        }
        for key, title in headings.items():
            joins_tree.heading(key, text=title)
            joins_tree.column(key, anchor="w")
        joins_tree.column("alias", width=140)
        joins_tree.column("object", width=160)
        joins_tree.column("type", width=140)
        joins_tree.column("index", width=90)
        joins_tree.column("row_expected", width=110, anchor="center")
        joins_tree.column("base_join", width=90, anchor="center")
        joins_tree.column("updated", width=160, anchor="center")
        joins_tree.column("status", width=220)
        joins_tree.grid(row=0, column=0, sticky="nsew")
        joins_scroll = ttk.Scrollbar(joins_tab, orient=tk.VERTICAL, command=joins_tree.yview)
        joins_scroll.grid(row=0, column=1, sticky="ns")
        joins_tree.configure(yscrollcommand=joins_scroll.set)

        if detail.joins:
            for join in detail.joins:
                status_bits = []
                if join.join_in_error:
                    status_bits.append(
                        f"Error: {join.join_error_message}" if join.join_error_message else "Error"
                    )
                if join.comment:
                    status_bits.append(f"Comment: {join.comment}")
                if join.relate_alias or join.relate_name:
                    relate = f"{join.relate_alias or ''} {join.relate_name or ''}".strip()
                    status_bits.append(f"Relate: {relate}")
                joins_tree.insert(
                    "",
                    tk.END,
                    values=(
                        join.alias or "",
                        join.join_object or "",
                        join.join_type or "",
                        join.join_index or "",
                        self._format_bool(join.row_expected),
                        self._format_bool(join.is_base_join),
                        self._format_timestamp(join.updated_at, join.updated_by),
                        "; ".join(status_bits),
                    ),
                )
        else:
            joins_tree.insert("", tk.END, values=("No joins available.", "", "", "", "", "", "", ""))

        expr_tab = ttk.Frame(notebook)
        expr_tab.columnconfigure(0, weight=1)
        expr_tab.rowconfigure(0, weight=1)
        notebook.add(expr_tab, text="Expressions")

        expr_columns = (
            "name",
            "validated",
            "csharp",
            "sql",
            "updated",
            "notes",
        )
        expr_tree = ttk.Treeview(expr_tab, columns=expr_columns, show="headings", height=12)
        expr_tree.heading("name", text="Expression Name")
        expr_tree.heading("validated", text="Validated Field")
        expr_tree.heading("csharp", text="C# Valid")
        expr_tree.heading("sql", text="SQL Valid")
        expr_tree.heading("updated", text="Updated")
        expr_tree.heading("notes", text="Notes")
        expr_tree.column("name", width=200, anchor="w")
        expr_tree.column("validated", width=180, anchor="w")
        expr_tree.column("csharp", width=90, anchor="center")
        expr_tree.column("sql", width=90, anchor="center")
        expr_tree.column("updated", width=160, anchor="center")
        expr_tree.column("notes", width=240, anchor="w")
        expr_tree.grid(row=0, column=0, sticky="nsew")
        expr_scroll = ttk.Scrollbar(expr_tab, orient=tk.VERTICAL, command=expr_tree.yview)
        expr_scroll.grid(row=0, column=1, sticky="ns")
        expr_tree.configure(yscrollcommand=expr_scroll.set)

        if detail.expressions:
            for expr in detail.expressions:
                expr_tree.insert(
                    "",
                    tk.END,
                    values=(
                        expr.expression_name or "",
                        expr.validated_field_name or "",
                        self._format_bool(expr.is_csharp_valid),
                        self._format_bool(expr.is_sql_valid),
                        self._format_timestamp(expr.updated_at, expr.updated_by),
                        expr.note or "",
                    ),
                )
        else:
            expr_tree.insert("", tk.END, values=("No expressions available.", "", "", "", "", ""))

        ttk.Button(container, text="Close", command=dialog.destroy).pack(anchor="e", pady=(12, 0))
        dialog.bind("<Escape>", lambda _: dialog.destroy())

    def _parse_data_source_csv(self, path: Path) -> List[SqlDataSourceDetail]:
        records: Dict[str, SqlDataSourceDetail] = {}
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            for row_index, row in enumerate(reader):
                if row_index == 0:
                    continue
                if not row or not any(cell.strip() for cell in row):
                    continue
                if len(row) < 39:
                    row = row + [""] * (39 - len(row))
                else:
                    row = row[:39]

                title = row[0].strip()
                if not title:
                    continue

                base_flag = self._parse_bool_str(row[3])
                error_flag = self._parse_bool_str(row[4])
                visible_flag = self._parse_bool_str(row[28])

                bundle = records.get(title)
                if bundle is None:
                    source = SqlDataSource(
                        id=None,
                        title=title,
                        description=self._clean_text(row[2]),
                        is_base=base_flag if base_flag is not None else False,
                        is_in_error=error_flag if error_flag is not None else False,
                        error_message=self._clean_text(row[5]),
                        parent_source=self._default_text(row[6], default="N/A"),
                        select_set=self._default_text(row[7], default="N/A"),
                        updated_at=self._clean_text(row[8]),
                        updated_by=self._clean_text(row[9]),
                        is_visible=visible_flag,
                        visible_updated_at=self._clean_text(row[29]),
                        visible_updated_by=self._clean_text(row[30]),
                    )
                    bundle = SqlDataSourceDetail(source=source, joins=[], expressions=[])
                    records[title] = bundle
                else:
                    source = bundle.source
                    description = self._clean_text(row[2])
                    if description:
                        source.description = description
                    if base_flag is not None:
                        source.is_base = base_flag
                    if error_flag is not None:
                        source.is_in_error = error_flag
                    error_message = self._clean_text(row[5])
                    if error_message:
                        source.error_message = error_message
                    parent_source = self._clean_text(row[6])
                    if parent_source:
                        source.parent_source = parent_source
                    select_set = self._clean_text(row[7])
                    if select_set:
                        source.select_set = select_set
                    updated_at = self._clean_text(row[8])
                    if updated_at:
                        source.updated_at = updated_at
                    updated_by = self._clean_text(row[9])
                    if updated_by:
                        source.updated_by = updated_by
                    if visible_flag is not None:
                        source.is_visible = visible_flag
                    visible_updated_at = self._clean_text(row[29])
                    if visible_updated_at:
                        source.visible_updated_at = visible_updated_at
                    visible_updated_by = self._clean_text(row[30])
                    if visible_updated_by:
                        source.visible_updated_by = visible_updated_by

                join_fields = row[10:28]
                if any(cell.strip() for cell in join_fields):
                    bundle.joins.append(
                        SqlDataSourceJoin(
                            id=None,
                            source_id=None,
                            alias=self._clean_text(row[10]),
                            sequence=self._clean_text(row[11]),
                            description=self._clean_text(row[12]),
                            join_object=self._clean_text(row[13]),
                            join_type=self._clean_text(row[14]),
                            row_expected=self._parse_bool_str(row[15]),
                            join_index=self._clean_text(row[16]),
                            is_base_join=self._parse_bool_str(row[17]),
                            join_in_error=self._parse_bool_str(row[18]),
                            join_error_message=self._clean_text(row[19]),
                            updated_at=self._clean_text(row[20]),
                            updated_by=self._clean_text(row[21]),
                            comment=self._clean_text(row[22]),
                            relate_sequence=self._clean_text(row[23]),
                            relate_alias=self._clean_text(row[24]),
                            relate_name=self._clean_text(row[25]),
                            clause_updated_at=self._clean_text(row[26]),
                            clause_updated_by=self._clean_text(row[27]),
                        )
                    )

                expression_fields = row[31:39]
                if any(cell.strip() for cell in expression_fields):
                    bundle.expressions.append(
                        SqlDataSourceExpression(
                            id=None,
                            source_id=None,
                            expression_name=self._clean_text(row[31]),
                            select_json_id=self._clean_text(row[32]),
                            note=self._clean_text(row[33]),
                            validated_field_name=self._clean_text(row[34]),
                            is_csharp_valid=self._parse_bool_str(row[35]),
                            is_sql_valid=self._parse_bool_str(row[36]),
                            updated_at=self._clean_text(row[37]),
                            updated_by=self._clean_text(row[38]),
                        )
                    )

        for bundle in records.values():
            if not bundle.source.parent_source:
                bundle.source.parent_source = "N/A"
            if not bundle.source.select_set:
                bundle.source.select_set = "N/A"

        return list(records.values())

    @staticmethod
    def _clean_text(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @staticmethod
    def _default_text(value: Optional[str], default: str = "") -> str:
        cleaned = SqlAssistView._clean_text(value)
        return cleaned if cleaned is not None else default

    @staticmethod
    def _parse_bool_str(value: Optional[str]) -> Optional[bool]:
        if value is None:
            return None
        value = value.strip().lower()
        if not value:
            return None
        if value in {"y", "yes", "true", "1", "t"}:
            return True
        if value in {"n", "no", "false", "0", "f"}:
            return False
        return None

    @staticmethod
    def _format_timestamp(timestamp: Optional[str], user_tag: Optional[str]) -> str:
        ts = (timestamp or "").strip()
        user = (user_tag or "").strip()
        if ts and user:
            return f"{ts} ({user})"
        if ts:
            return ts
        if user:
            return user
        return ""

    @staticmethod
    def _format_bool(value: Optional[bool]) -> str:
        if value is None:
            return ""
        return "Yes" if value else "No"
    # ------------------------------------------------------------------ Instance actions
    def _create_instance(self) -> None:
        name = simpledialog.askstring("New Instance", "Instance name:", parent=self)
        if not name:
            return
        try:
            new_id = self.db.create_sql_instance(name)
        except ValueError as exc:
            messagebox.showerror("Create Instance", str(exc), parent=self)
            return
        self.refresh_instances(select_instance=new_id)
        messagebox.showinfo("Instance Created", f"Instance '{name}' is ready.", parent=self)
        self._last_import_backup = None
        self._last_import_instance_id = None
        self.data_sources: List[SqlDataSource] = []
        self._filtered_data_sources: List[SqlDataSource] = []
        self._source_item_map: Dict[str, SqlDataSource] = {}
        self._all_data_sources: List[SqlDataSource] = []

    def _delete_instance(self) -> None:
        if self.current_instance_id is None:
            return
        instance = next((inst for inst in self.instances if inst.id == self.current_instance_id), None)
        if instance is None:
            return
        if not messagebox.askyesno(
            "Delete Instance",
            f"Delete SQL instance '{instance.name}'? This cannot be undone.",
            parent=self,
        ):
            return
        try:
            self.db.delete_sql_instance(self.current_instance_id)
        except Exception as exc:  # pragma: no cover
            messagebox.showerror("Delete Failed", str(exc), parent=self)
            return
        self._last_import_backup = None
        self._last_import_instance_id = None
        self.refresh_instances()
        self._update_instance_header(None)
        messagebox.showinfo("Delete Complete", f"Instance '{instance.name}' has been removed.", parent=self)

    def _import_instance(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Import SQL Instance",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - UI
            messagebox.showerror("Import Failed", f"Could not read file: {exc}", parent=self)
            return
        try:
            new_id = self.db.import_sql_instance(payload)
        except ValueError as exc:
            if "already exists" not in str(exc):
                messagebox.showerror("Import Failed", str(exc), parent=self)
                return
            overwrite = messagebox.askyesno(
                "Instance Exists",
                "An instance with that name already exists. Replace it?",
                parent=self,
            )
            if not overwrite:
                return
            try:
                new_id = self.db.import_sql_instance(payload, replace_existing=True)
            except Exception as inner_exc:  # pragma: no cover - UI
                messagebox.showerror("Import Failed", str(inner_exc), parent=self)
                return
        except Exception as exc:  # pragma: no cover
            messagebox.showerror("Import Failed", str(exc), parent=self)
            return
        self.refresh_instances(select_instance=new_id)
        messagebox.showinfo("Import Complete", "Instance imported successfully.", parent=self)
        self._last_import_backup = None
        self._last_import_instance_id = None

    def _export_instance(self) -> None:
        if self.current_instance_id is None:
            return
        try:
            payload = self.db.export_sql_instance(self.current_instance_id)
        except Exception as exc:  # pragma: no cover
            messagebox.showerror("Export Failed", str(exc), parent=self)
            return
        default_name = payload["name"].replace(" ", "_")
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Export SQL Instance",
            defaultextension=".json",
            initialfile=f"{default_name}.json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:  # pragma: no cover
            messagebox.showerror("Export Failed", f"Could not write file: {exc}", parent=self)
            return
        messagebox.showinfo("Export Complete", "Instance exported successfully.", parent=self)

    # ------------------------------------------------------------------ Table import
    def _import_data_sources(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Import Data Sources",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            bundles = self._parse_data_source_csv(Path(path))
        except Exception as exc:
            messagebox.showerror("Import Failed", f"Could not read file: {exc}", parent=self)
            return
        if not bundles:
            messagebox.showinfo("Data Sources", "No data sources were found in the CSV.", parent=self)
            return
        try:
            self.db.replace_sql_data_sources(bundles)
        except Exception as exc:
            messagebox.showerror("Import Failed", str(exc), parent=self)
            return
        messagebox.showinfo(
            "Import Complete",
            f"Imported {len(bundles)} data source(s).",
            parent=self,
        )
        self._refresh_data_sources()
    def _import_tables_from_csv(self) -> None:
        if self.current_instance_id is None:
            messagebox.showinfo("SQL Assist", "Select an instance before importing tables.", parent=self)
            return
        path = filedialog.askopenfilename(
            parent=self,
            title="Import Tables & Columns",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        table_mapping: Dict[str, set[str]] = defaultdict(set)
        rows_loaded = 0
        header_skipped = False
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle)
                for row_number, row in enumerate(reader, start=1):
                    trimmed = [cell.strip() for cell in row]
                    if not any(trimmed):
                        continue
                    if not header_skipped:
                        header_skipped = True
                        continue  # skip header row
                    if len(trimmed) > 2 and any(trimmed[2:]):
                        messagebox.showerror(
                            "Import Failed",
                            f"Row {row_number} has data outside columns A and B. "
                            "Please remove extra columns and try again.",
                            parent=self,
                        )
                        return
                    table_name = trimmed[0] if trimmed else ""
                    column_name = trimmed[1] if len(trimmed) > 1 else ""
                    if not table_name:
                        continue
                    rows_loaded += 1
                    if column_name:
                        table_mapping[table_name].add(column_name)
                    else:
                        table_mapping.setdefault(table_name, set())
        except Exception as exc:  # pragma: no cover
            messagebox.showerror("Import Failed", f"Could not read CSV: {exc}", parent=self)
            return
        if not table_mapping:
            messagebox.showinfo("Import Tables", "No tables or columns found in the CSV.", parent=self)
            return
        try:
            backup = self.db.export_sql_instance(self.current_instance_id)
        except Exception as exc:  # pragma: no cover
            messagebox.showerror("Import Failed", f"Could not snapshot current instance: {exc}", parent=self)
            return
        new_tables, new_columns = self.db.ingest_sql_table_columns(self.current_instance_id, table_mapping)
        if new_tables or new_columns:
            self._last_import_backup = backup
            self._last_import_instance_id = self.current_instance_id
            self.undo_btn.configure(state=tk.NORMAL)
            self.refresh_instances(select_instance=self.current_instance_id)
        else:
            self.refresh_instances(select_instance=self.current_instance_id)
            messagebox.showinfo(
                "Import Complete",
                "No changes detected. Tables and columns already existed.",
                parent=self,
            )
            return
        messagebox.showinfo(
            "Import Complete",
            (
                f"Processed {rows_loaded} row(s).\n"
                f"Added {new_tables} new table(s) and {new_columns} new column(s)."
            ),
            parent=self,
        )

    def _undo_last_import(self) -> None:
        if (
            self.current_instance_id is None
            or self._last_import_backup is None
            or self._last_import_instance_id != self.current_instance_id
        ):
            messagebox.showinfo("Undo Import", "There is no import to undo for this instance.", parent=self)
            self.undo_btn.configure(state=tk.DISABLED)
            return
        try:
            restored_id = self.db.import_sql_instance(
                self._last_import_backup,
                replace_existing=True,
            )
        except Exception as exc:  # pragma: no cover
            messagebox.showerror("Undo Failed", str(exc), parent=self)
            return
        self.refresh_instances(select_instance=restored_id)
        self._last_import_backup = None
        self._last_import_instance_id = None
        self.undo_btn.configure(state=tk.DISABLED)
        messagebox.showinfo("Undo Complete", "The instance has been restored to its previous state.", parent=self)

    def _on_tree_double_click(self, event: tk.Event) -> None:
        item_id = self.tree.focus()
        if not item_id:
            return
        table = self._tree_table_map.get(item_id)
        if table is None:
            return
        self._show_table_details(table)

    def _show_table_details(self, table: SqlTable) -> None:
        detail = tk.Toplevel(self)
        detail.title(f"{table.name} Details")
        detail.transient(self.winfo_toplevel())
        detail.grab_set()
        container = ttk.Frame(detail, padding=16)
        container.pack(fill=tk.BOTH, expand=True)

        ttk.Label(container, text=table.name, style="SidebarHeading.TLabel").pack(anchor="w")

        description = (table.description or "").strip()
        if not description:
            description = "No description provided."
        ttk.Label(container, text="Description:").pack(anchor="w", pady=(12, 4))
        ttk.Label(container, text=description, wraplength=520, justify="left").pack(anchor="w")

        ttk.Label(container, text="Columns:").pack(anchor="w", pady=(16, 6))
        columns_frame = ttk.Frame(container)
        columns_frame.pack(fill=tk.BOTH, expand=True)

        columns_tree = ttk.Treeview(columns_frame, columns=("column", "description"), show="headings", height=12)
        columns_tree.heading("column", text="Column")
        columns_tree.heading("description", text="Description")
        columns_tree.column("column", width=200, anchor="w")
        columns_tree.column("description", width=320, anchor="w")
        vsb = ttk.Scrollbar(columns_frame, orient=tk.VERTICAL, command=columns_tree.yview)
        columns_tree.configure(yscrollcommand=vsb.set)
        columns_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        for column in sorted(table.columns, key=lambda col: col.name.lower()):
            columns_tree.insert("", tk.END, values=(column.name, column.description or ""))

        ttk.Button(container, text="Close", command=detail.destroy).pack(anchor="e", pady=(12, 0))
        detail.bind("<Escape>", lambda _: detail.destroy())

    def _update_instance_header(self, instance: Optional[SqlInstance]) -> None:
        if instance and instance.updated_at:
            self.updated_var.set(instance.updated_at.strftime("Last updated: %Y-%m-%d %H:%M"))
        elif instance:
            self.updated_var.set("Last updated: --")
        else:
            self.updated_var.set("Last updated: --")

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


