from __future__ import annotations

import csv
import json
import re
import sqlite3
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
    SqlSavedQuery,
)


class SqlAssistView(ttk.Frame):
    """SQL Assist workspace guarded by a simple PIN until the feature ships."""

    def __init__(self, master: tk.Misc, db: Database) -> None:
        super().__init__(master, padding=(16, 16))
        self.db = db

        self.instances: list[SqlInstance] = []
        self.current_instance_id: Optional[int] = None
        self._last_import_backup: Optional[dict[str, object]] = None
        self._last_import_instance_id: Optional[int] = None

        self.instance_var = tk.StringVar()
        self.summary_var = tk.StringVar(value="Select or import an instance to begin.")
        self.search_field_var = tk.StringVar(value="Table")
        self.search_text_var = tk.StringVar(value="")
        self.data_source_search_var = tk.StringVar(value="")
        self.query_search_var = tk.StringVar(value="")
        self.query_name_var = tk.StringVar()
        self.query_description_var = tk.StringVar()
        self.query_validation_var = tk.StringVar(value="")

        self.updated_var = tk.StringVar(value="Last updated: --")

        self._all_tables: list[SqlTable] = []
        self._base_table_count = 0
        self._base_column_count = 0
        self._table_by_id: dict[int, SqlTable] = {}
        self._tree_table_map: dict[str, SqlTable] = {}
        self._all_data_sources: List[SqlDataSource] = []
        self._source_item_map: Dict[str, SqlDataSource] = {}
        self.saved_queries: List[SqlSavedQuery] = []
        self._filtered_queries: List[SqlSavedQuery] = []
        self.current_query_id: Optional[int] = None
        self.query_dirty = False
        self._query_validation_after: Optional[str] = None
        self._query_diagnostics: list[tuple[str, str]] = []
        self._suspend_query_events = False

        self._build_ui()
        self.data_source_search_var.trace_add("write", lambda *_: self._apply_data_source_filter())
        self.search_text_var.trace_add("write", lambda *_: self._apply_table_filter())
        self.query_search_var.trace_add("write", lambda *_: self._apply_query_filter())
        self.query_name_var.trace_add("write", lambda *_: self._mark_query_dirty())
        self.query_description_var.trace_add("write", lambda *_: self._mark_query_dirty())
        self._update_query_controls()
        self.after(0, self._initialize_contents)

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

        query_tab = ttk.Frame(self.notebook)
        query_tab.columnconfigure(1, weight=1)
        query_tab.columnconfigure(2, weight=0)
        query_tab.rowconfigure(1, weight=1)
        self.notebook.add(query_tab, text="Query Generation")
        self.query_tab = query_tab

        query_list_frame = ttk.Frame(query_tab)
        query_list_frame.grid(row=0, column=0, rowspan=3, sticky="nsw", padx=(0, 12))
        ttk.Label(query_list_frame, text="Saved Queries").pack(anchor="w")
        self.query_search_entry = ttk.Entry(query_list_frame, textvariable=self.query_search_var, width=24)
        self.query_search_entry.pack(fill=tk.X, pady=(4, 4))
        self.query_listbox = tk.Listbox(query_list_frame, height=20, exportselection=False)
        self.query_listbox.pack(fill=tk.BOTH, expand=True)
        self.query_listbox.bind("<<ListboxSelect>>", self._on_select_query)
        list_btns = ttk.Frame(query_list_frame)
        list_btns.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(list_btns, text="New", command=self._new_query).pack(side=tk.LEFT)
        self.delete_query_btn = ttk.Button(list_btns, text="Delete", command=self._delete_query, state=tk.DISABLED)
        self.delete_query_btn.pack(side=tk.LEFT, padx=(6, 0))

        details_frame = ttk.Frame(query_tab)
        details_frame.grid(row=0, column=1, sticky="new")
        details_frame.columnconfigure(1, weight=1)
        ttk.Label(details_frame, text="Name:").grid(row=0, column=0, sticky="w")
        self.query_name_entry = ttk.Entry(details_frame, textvariable=self.query_name_var)
        self.query_name_entry.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Label(details_frame, text="Description:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.query_description_entry = ttk.Entry(details_frame, textvariable=self.query_description_var)
        self.query_description_entry.grid(row=1, column=1, sticky="ew", padx=(4, 0))

        text_frame = ttk.Frame(query_tab)
        text_frame.grid(row=1, column=1, sticky="nsew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        self.query_text = tk.Text(text_frame, wrap="word", undo=True, height=18)
        self.query_text.grid(row=0, column=0, sticky="nsew")
        query_scroll = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.query_text.yview)
        query_scroll.grid(row=0, column=1, sticky="ns")
        self.query_text.configure(yscrollcommand=query_scroll.set)
        self.query_text.bind("<<Modified>>", self._on_query_text_modified)

        query_buttons = ttk.Frame(query_tab)
        query_buttons.grid(row=2, column=1, sticky="w", pady=(6, 0))
        self.save_query_btn = ttk.Button(query_buttons, text="Save", command=self._save_query)
        self.save_query_btn.grid(row=0, column=0)
        self.save_as_query_btn = ttk.Button(
            query_buttons, text="Save As", command=lambda: self._save_query(save_as=True)
        )
        self.save_as_query_btn.grid(row=0, column=1, padx=(6, 0))
        self.validate_query_btn = ttk.Button(query_buttons, text="Validate", command=self._validate_query)
        self.validate_query_btn.grid(row=0, column=2, padx=(6, 0))

        self.query_validation_label = ttk.Label(query_tab, textvariable=self.query_validation_var, anchor="w")
        self.query_validation_label.grid(row=3, column=1, sticky="ew", pady=(6, 0))

        diag_frame = ttk.Frame(query_tab)
        diag_frame.grid(row=4, column=1, sticky="nsew", pady=(4, 0))
        diag_frame.columnconfigure(0, weight=1)
        self.query_diag_tree = ttk.Treeview(
            diag_frame,
            columns=("type", "message"),
            show="headings",
            height=5,
            selectmode="browse",
        )
        self.query_diag_tree.heading("type", text="Type")
        self.query_diag_tree.heading("message", text="Message")
        self.query_diag_tree.column("type", width=80, anchor="center")
        self.query_diag_tree.column("message", width=480, anchor="w")
        diag_scroll = ttk.Scrollbar(diag_frame, orient=tk.VERTICAL, command=self.query_diag_tree.yview)
        diag_scroll.grid(row=0, column=1, sticky="ns")
        self.query_diag_tree.configure(yscrollcommand=diag_scroll.set)
        self.query_diag_tree.grid(row=0, column=0, sticky="nsew")
        self.query_diag_tree.tag_configure("error", foreground="#FF6B6B")
        self.query_diag_tree.tag_configure("warning", foreground="#FFB74D")

        palette_frame = ttk.Frame(query_tab)
        palette_frame.grid(row=0, column=2, rowspan=3, sticky="ns", padx=(12, 0))
        palette_frame.rowconfigure(1, weight=1)
        ttk.Label(palette_frame, text="Tables & Columns").grid(row=0, column=0, sticky="w")
        self.table_palette = ttk.Treeview(palette_frame, show="tree", height=22)
        self.table_palette.grid(row=1, column=0, sticky="nsew")
        palette_scroll = ttk.Scrollbar(palette_frame, orient=tk.VERTICAL, command=self.table_palette.yview)
        palette_scroll.grid(row=1, column=1, sticky="ns")
        self.table_palette.configure(yscrollcommand=palette_scroll.set)
        self.table_palette.bind("<Double-1>", self._on_palette_double_click)

    def _initialize_contents(self) -> None:
        self.refresh_instances()
        self._refresh_data_sources()
        self._refresh_saved_queries()
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
                self._populate_table_palette()
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
            self._populate_table_palette()

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
        self._refresh_saved_queries()

    def _select_instance_by_name(self, name: str) -> None:
        for instance in self.instances:
            if instance.name == name:
                self.current_instance_id = instance.id
                self._load_tables(instance.id)
                self._refresh_saved_queries(select_id=None)
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
        self._refresh_saved_queries(select_id=None)
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
        self._populate_table_palette()

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

    def _populate_table_palette(self) -> None:
        if not hasattr(self, "table_palette"):
            return
        self.table_palette.delete(*self.table_palette.get_children())
        for table in self._all_tables:
            parent = self.table_palette.insert("", tk.END, text=table.name, tags=("table",))
            for column in sorted(table.columns, key=lambda col: col.name.lower()):
                self.table_palette.insert(parent, tk.END, text=column.name, tags=("column",))

    # ------------------------------------------------------------------ Saved query management
    def _refresh_saved_queries(self, select_id: Optional[int] = None) -> None:
        if self.current_instance_id is None:
            self.saved_queries = []
            self._filtered_queries = []
            self.query_listbox.delete(0, tk.END)
            self.query_listbox.configure(state=tk.DISABLED)
            self.current_query_id = None
            self._load_query_details(None)
            self._update_query_controls()
            return
        target_id = select_id if select_id is not None else self.current_query_id
        try:
            self.saved_queries = self.db.get_sql_saved_queries(self.current_instance_id)
        except Exception as exc:  # pragma: no cover - UI error path
            self.saved_queries = []
            messagebox.showerror("Saved Queries", f"Unable to load saved queries: {exc}", parent=self)
            target_id = None
        self._apply_query_filter(select_id=target_id)
        if (
            target_id
            and all(query.id != target_id for query in self._filtered_queries)
            and self.query_search_var.get().strip()
        ):
            # Clear any active filter that is hiding the newly saved query.
            self.query_search_var.set("")
            self._apply_query_filter(select_id=target_id)
        self.query_listbox.update_idletasks()

    def _apply_query_filter(self, select_id: Optional[int] = None) -> None:
        search = self.query_search_var.get().strip().lower()
        self.query_listbox.configure(state=tk.NORMAL)
        self.query_listbox.delete(0, tk.END)
        self._filtered_queries = []
        for query in self.saved_queries:
            haystacks = [query.name.lower()]
            if query.description:
                haystacks.append(query.description.lower())
            if not search or any(search in hay for hay in haystacks):
                self._filtered_queries.append(query)
                label = query.name if not query.description else f"{query.name} — {query.description}"
                self.query_listbox.insert(tk.END, label)
        has_results = bool(self._filtered_queries)
        if not has_results:
            placeholder = "No saved queries yet." if not search else "No matching queries."
            self.query_listbox.insert(tk.END, placeholder)
            self.query_listbox.configure(state=tk.DISABLED)
        else:
            self.query_listbox.configure(state=tk.NORMAL)
        target_id = select_id if select_id is not None else (
            self.current_query_id if self.current_query_id in {q.id for q in self._filtered_queries} else None
        )
        self._select_query_by_id(target_id)
        if not self._filtered_queries:
            self.current_query_id = None
            self._load_query_details(None)
        self._update_query_controls()

    def _on_select_query(self, event: Optional[tk.Event]) -> None:
        if self._suspend_query_events:
            return
        selection = self.query_listbox.curselection()
        if not selection:
            return
        index = selection[0]
        if index >= len(self._filtered_queries):
            return
        query = self._filtered_queries[index]
        if self.query_dirty and (self.current_query_id != query.id):
            if not self._confirm_discard_query_changes():
                self._select_query_by_id(self.current_query_id)
                return
        self._load_query_details(query)
        self._select_query_by_id(query.id)

    def _select_query_by_id(self, query_id: Optional[int]) -> None:
        self._suspend_query_events = True
        try:
            self.query_listbox.selection_clear(0, tk.END)
            if query_id is None:
                return
            for index, query in enumerate(self._filtered_queries):
                if query.id == query_id:
                    self.query_listbox.selection_set(index)
                    self.query_listbox.see(index)
                    break
        finally:
            self._suspend_query_events = False
        self._update_query_controls()

    def _load_query_details(self, query: Optional[SqlSavedQuery]) -> None:
        self._suspend_query_events = True
        try:
            if query is None:
                self.query_name_var.set("")
                self.query_description_var.set("")
                self.query_text.delete("1.0", tk.END)
            else:
                self.query_name_var.set(query.name)
                self.query_description_var.set(query.description or "")
                self.query_text.delete("1.0", tk.END)
                self.query_text.insert("1.0", query.content)
        finally:
            self._suspend_query_events = False
        self.current_query_id = query.id if query else None
        self.query_dirty = False
        self.query_text.edit_modified(False)
        self._set_query_diagnostics([])
        if query and query.updated_at:
            self._set_query_validation(f"Loaded query. Last saved {query.updated_at}.", status="info")
        elif query:
            self._set_query_validation("Loaded query.", status="info")
        else:
            self._set_query_validation("Ready to draft a new query.", status="info")
        self._update_query_controls()

    def _new_query(self) -> None:
        if not self._confirm_discard_query_changes():
            return
        self.query_listbox.selection_clear(0, tk.END)
        self._load_query_details(None)

    def _delete_query(self) -> None:
        selection = self.query_listbox.curselection()
        if not selection or selection[0] >= len(self._filtered_queries):
            return
        query = self._filtered_queries[selection[0]]
        if not messagebox.askyesno(
            "Delete Saved Query",
            f"Delete '{query.name}'?",
            parent=self,
        ):
            return
        try:
            self.db.delete_sql_saved_query(query.id)  # type: ignore[arg-type]
        except Exception as exc:  # pragma: no cover - UI path
            messagebox.showerror("Delete Failed", str(exc), parent=self)
            return
        self.current_query_id = None
        self._load_query_details(None)
        self._refresh_saved_queries()

    def _save_query(self, save_as: bool = False) -> None:
        name = self.query_name_var.get().strip()
        description = self.query_description_var.get().strip() or None
        content = self._get_query_text()
        if not name:
            messagebox.showerror("Save Query", "Give the query a name before saving.", parent=self)
            self.query_name_entry.focus_set()
            return
        if not content:
            messagebox.showerror("Save Query", "Add some SQL before saving the query.", parent=self)
            self.query_text.focus_set()
            return
        if self.current_instance_id is None:
            messagebox.showinfo(
                "Save Query",
                "Select a SQL instance before saving queries.",
                parent=self,
            )
            return
        try:
            if save_as or self.current_query_id is None:
                query_id = self.db.create_sql_saved_query(
                    name,
                    description,
                    content,
                    self.current_instance_id,
                )
                self.current_query_id = query_id
            else:
                query_id = self.current_query_id
                self.db.update_sql_saved_query(query_id, name, description, content)  # type: ignore[arg-type]
        except sqlite3.IntegrityError:
            messagebox.showerror(
                "Save Query",
                "A saved query with that name already exists. Choose a different name.",
                parent=self,
            )
            return
        except ValueError as exc:
            messagebox.showerror("Save Query", str(exc), parent=self)
            return
        except Exception as exc:  # pragma: no cover - UI path
            messagebox.showerror("Save Query", f"Unable to save query: {exc}", parent=self)
            return
        try:
            saved = self.db.get_sql_saved_query(self.current_query_id)  # type: ignore[arg-type]
        except Exception:
            saved = SqlSavedQuery(
                id=self.current_query_id,
                instance_id=self.current_instance_id,
                name=name,
                description=description,
                content=content,
                updated_at=None,
            )
        self.query_dirty = False
        self._set_query_validation("Query saved.", status="success")
        self._refresh_saved_queries(select_id=saved.id if saved else self.current_query_id)
        self._load_query_details(saved)

    def _validate_query(self) -> None:
        sql_text = self._get_query_text()
        if not sql_text:
            self._set_query_diagnostics([])
            self._set_query_validation("Enter SQL before running validation.", status="warning")
            return
        diagnostics: list[tuple[str, str]] = [
            ("warning", warning) for warning in self._collect_query_warnings(sql_text)
        ]
        for message in self._collect_missing_reference_errors(sql_text):
            diagnostics.append(("error", message))
        try:
            connection = sqlite3.connect(":memory:")
            try:
                self._seed_validation_schema(connection)
                statements = list(self._split_sql_statements(sql_text))
                if not statements:
                    statements = [sql_text]
                for statement in statements:
                    if not statement.strip():
                        continue
                    try:
                        connection.execute(f"EXPLAIN {statement}")
                    except sqlite3.Error as exc:
                        diagnostics.append(("error", self._clean_sqlite_error(str(exc))))
            finally:
                connection.close()
        except sqlite3.Error as exc:
            diagnostics.append(("error", self._clean_sqlite_error(str(exc))))
        errors, warnings = self._set_query_diagnostics(diagnostics)
        if errors or warnings:
            self._apply_validation_summary(errors, warnings)
        else:
            self._set_query_validation(
                "Validation passed. No issues detected (SQLite syntax check).",
                status="success",
            )

    def _get_query_text(self) -> str:
        return self.query_text.get("1.0", tk.END).strip()

    def _mark_query_dirty(self, *_: object) -> None:
        if self._suspend_query_events:
            return
        self.query_dirty = True
        self._set_query_diagnostics([])
        self._set_query_validation("Unsaved changes. Save to keep this version.", status="warning")
        self._update_query_controls()

    def _update_query_controls(self) -> None:
        has_instance = self.current_instance_id is not None
        name_present = bool(self.query_name_var.get().strip())
        sql_present = bool(self._get_query_text())
        if hasattr(self, "save_query_btn"):
            can_save = (
                has_instance
                and (self.query_dirty or self.current_query_id is None)
                and name_present
                and sql_present
            )
            self.save_query_btn.configure(state=tk.NORMAL if can_save else tk.DISABLED)
        if hasattr(self, "save_as_query_btn"):
            self.save_as_query_btn.configure(
                state=tk.NORMAL if has_instance and name_present and sql_present else tk.DISABLED
            )
        if hasattr(self, "validate_query_btn"):
            self.validate_query_btn.configure(state=tk.NORMAL if sql_present else tk.DISABLED)
        has_selection = bool(self.query_listbox.curselection()) and has_instance
        self.delete_query_btn.configure(state=tk.NORMAL if has_selection else tk.DISABLED)

    def _set_query_diagnostics(self, diagnostics: list[tuple[str, str]]) -> tuple[int, int]:
        normalized: list[tuple[str, str]] = []
        for severity, message in diagnostics:
            sev = severity.lower().strip()
            if sev not in {"error", "warning"}:
                sev = "warning"
            normalized.append((sev, message))
        self._query_diagnostics = normalized
        if hasattr(self, "query_diag_tree"):
            tree = self.query_diag_tree
            tree.delete(*tree.get_children())
            for severity, message in normalized:
                display = severity.capitalize()
                tree.insert("", tk.END, values=(display, message), tags=(severity,))
        errors = sum(1 for severity, _ in normalized if severity == "error")
        warnings = sum(1 for severity, _ in normalized if severity == "warning")
        return errors, warnings

    def _apply_validation_summary(self, errors: int, warnings: int) -> None:
        parts: List[str] = []
        if errors:
            parts.append(f"{errors} error{'s' if errors != 1 else ''}")
        if warnings:
            parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
        summary = " and ".join(parts) if len(parts) == 2 else (parts[0] if parts else "")
        status = "error" if errors else "warning"
        message = f"Validation found {summary}. Review the list below." if summary else "Validation reported issues."
        self._set_query_validation(message, status=status)

    def _collect_query_warnings(self, sql_text: str) -> List[str]:
        warnings: List[str] = []
        lowered = sql_text.lower()
        if "select *" in lowered:
            warnings.append("Avoid SELECT *; list only the columns you need.")
        trimmed = sql_text.strip()
        if trimmed:
            first_token = re.split(r"\s+", trimmed, 1)[0].lower()
            if first_token in {"update", "delete"} and " where " not in lowered:
                warnings.append(f"{first_token.upper()} statement without a WHERE clause may affect every row.")
        return warnings

    def _collect_missing_reference_errors(self, sql_text: str) -> List[str]:
        if not self._all_tables:
            return []
        tables_by_lower: Dict[str, SqlTable] = {table.name.lower(): table for table in self._all_tables}
        alias_lookup: Dict[str, str] = {}
        errors: List[str] = []
        seen_messages: set[str] = set()
        table_pattern = re.compile(r"\b(from|join)\s+([A-Za-z0-9_]+)(?:\s+(?:as\s+)?([A-Za-z0-9_]+))?", re.IGNORECASE)
        for match in table_pattern.finditer(sql_text):
            table_name = match.group(2)
            alias = match.group(3)
            table_key = table_name.lower()
            alias_lookup.setdefault(table_key, table_key)
            if alias:
                alias_lookup[alias.lower()] = table_key
            if table_key not in tables_by_lower:
                message = f"Table '{table_name}' is not defined for this instance."
                if message not in seen_messages:
                    errors.append(message)
                    seen_messages.add(message)
        column_pattern = re.compile(r"([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)")
        for table_token, column_name in column_pattern.findall(sql_text):
            table_key = table_token.lower()
            resolved_table = alias_lookup.get(table_key)
            if resolved_table is None:
                if table_key not in tables_by_lower:
                    message = f"Table or alias '{table_token}' is not recognized."
                    if message not in seen_messages:
                        errors.append(message)
                        seen_messages.add(message)
                continue
            table_obj = tables_by_lower.get(resolved_table)
            if table_obj is None:
                continue
            column_key = column_name.lower()
            if not any((col.name or "").lower() == column_key for col in table_obj.columns):
                message = f"Column '{table_token}.{column_name}' is not defined."
                if message not in seen_messages:
                    errors.append(message)
                    seen_messages.add(message)
        return errors

    @staticmethod
    def _clean_sqlite_error(message: str) -> str:
        cleaned = " ".join(message.split())
        return cleaned or "SQLite reported an unknown error."

    def _seed_validation_schema(self, connection: sqlite3.Connection) -> None:
        if not self._all_tables:
            return
        for table in self._all_tables:
            table_name = table.name.strip()
            if not table_name:
                continue
            seen_columns: set[str] = set()
            column_defs: List[str] = []
            for column in table.columns:
                column_name = (column.name or "").strip()
                if not column_name:
                    continue
                key = column_name.lower()
                if key in seen_columns:
                    continue
                seen_columns.add(key)
                column_defs.append(f"\"{column_name}\" TEXT")
            if not column_defs:
                column_defs.append("\"placeholder\" TEXT")
            ddl = f"CREATE TABLE IF NOT EXISTS \"{table_name}\" ({', '.join(column_defs)})"
            try:
                connection.execute(ddl)
            except sqlite3.Error:
                continue

    @staticmethod
    def _split_sql_statements(sql_text: str) -> List[str]:
        statements: List[str] = []
        buffer: List[str] = []
        for line in sql_text.splitlines():
            buffer.append(line)
            candidate = "\n".join(buffer)
            if sqlite3.complete_statement(candidate):
                statements.append(candidate.strip())
                buffer = []
        leftover = "\n".join(buffer).strip()
        if leftover:
            statements.append(leftover)
        return statements

    def _set_query_validation(self, message: str, *, status: str = "info") -> None:
        palette = {
            "info": "#9FA8DA",
            "success": "#7ed321",
            "warning": "#FFB74D",
            "error": "#FF6B6B",
        }
        self.query_validation_var.set(message)
        color = palette.get(status, palette["info"])
        try:
            self.query_validation_label.configure(foreground=color)
        except tk.TclError:
            pass

    def _on_query_text_modified(self, event: Optional[tk.Event]) -> None:
        if not self.query_text.edit_modified():
            return
        self.query_text.edit_modified(False)
        if self._suspend_query_events:
            return
        self._mark_query_dirty()

    def _insert_into_query(self, snippet: str) -> None:
        if not snippet:
            return
        self.query_text.insert(tk.INSERT, snippet)
        self.query_text.focus_set()

    def _on_palette_double_click(self, event: tk.Event) -> None:
        item = self.table_palette.focus()
        if not item:
            return
        label = self.table_palette.item(item, "text")
        parent = self.table_palette.parent(item)
        if parent:
            table_name = self.table_palette.item(parent, "text")
            snippet = f"{table_name}.{label} "
        else:
            snippet = f"{label} "
        self._insert_into_query(snippet)

    def _confirm_discard_query_changes(self) -> bool:
        if not self.query_dirty:
            return True
        return messagebox.askyesno(
            "Discard Changes?",
            "You have unsaved query changes. Discard them?",
            parent=self,
        )

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





