from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Dict, Optional

from ...database import Database
from ...models import SqlInstance, SqlTable


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

        self._build_ui()
        self.after(0, self._show_lock_overlay)

    # ------------------------------------------------------------------ UI construction
    def _build_ui(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill=tk.X)

        ttk.Label(header, text="Instance", style="SidebarHeading.TLabel").pack(side=tk.LEFT)

        self.instance_combo = ttk.Combobox(
            header,
            textvariable=self.instance_var,
            state="readonly",
            width=36,
        )
        self.instance_combo.pack(side=tk.LEFT, padx=(10, 6))
        self.instance_combo.bind("<<ComboboxSelected>>", lambda _: self._select_instance_by_name(self.instance_var.get()))

        ttk.Button(header, text="Add", command=self._create_instance).pack(side=tk.LEFT)
        self.delete_btn = ttk.Button(
            header,
            text="Delete",
            command=self._delete_instance,
            state=tk.DISABLED,
        )
        self.delete_btn.pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(header, text="Import...", command=self._import_instance).pack(side=tk.LEFT, padx=(6, 0))
        self.export_btn = ttk.Button(header, text="Export...", command=self._export_instance, state=tk.DISABLED)
        self.export_btn.pack(side=tk.LEFT, padx=(6, 0))

        controls = ttk.Frame(self)
        controls.pack(fill=tk.X, pady=(12, 8))

        self.import_tables_btn = ttk.Button(
            controls,
            text="Import Tables & Columns",
            command=self._import_tables_from_csv,
            state=tk.DISABLED,
        )
        self.import_tables_btn.pack(side=tk.LEFT)
        self.undo_btn = ttk.Button(
            controls,
            text="Undo Last Import",
            command=self._undo_last_import,
            state=tk.DISABLED,
        )
        self.undo_btn.pack(side=tk.LEFT, padx=(6, 0))

        ttk.Label(controls, textvariable=self.summary_var).pack(side=tk.RIGHT)

        container = ttk.Frame(self)
        container.pack(fill=tk.BOTH, expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(container, show="tree")
        self.tree.heading("#0", text="Tables & Columns")
        self.tree.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        scrollbar = ttk.Scrollbar(container, orient=tk.VERTICAL, command=self.tree.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=scrollbar.set)

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
        self.instance_combo.focus_set()

    # ------------------------------------------------------------------ Data management
    def refresh_instances(self, *, select_instance: Optional[int] = None) -> None:
        self.instances = self.db.get_sql_instances()
        names = [instance.name for instance in self.instances]
        self.instance_combo["values"] = names

        target_id = select_instance or self.current_instance_id
        if target_id is not None:
            for instance in self.instances:
                if instance.id == target_id:
                    self.instance_var.set(instance.name)
                    self.current_instance_id = instance.id
                    self._load_tables(instance.id)
                    break
            else:
                self.current_instance_id = None
        elif names:
            self.instance_var.set(names[0])
            self.current_instance_id = self.instances[0].id
            self._load_tables(self.current_instance_id)
        else:
            self.instance_var.set("")
            self.current_instance_id = None
            self._populate_tree([])

        has_instance = self.current_instance_id is not None
        self.export_btn.configure(state=tk.NORMAL if has_instance else tk.DISABLED)
        self.import_tables_btn.configure(state=tk.NORMAL if has_instance else tk.DISABLED)
        self.delete_btn.configure(state=tk.NORMAL if has_instance else tk.DISABLED)
        if has_instance and self._last_import_instance_id == self.current_instance_id and self._last_import_backup:
            self.undo_btn.configure(state=tk.NORMAL)
        else:
            self.undo_btn.configure(state=tk.DISABLED)
        if not has_instance:
            self.summary_var.set("Create or import an instance to begin.")

    def _select_instance_by_name(self, name: str) -> None:
        for instance in self.instances:
            if instance.name == name:
                self.current_instance_id = instance.id
                self._load_tables(instance.id)
                self.export_btn.configure(state=tk.NORMAL)
                self.import_tables_btn.configure(state=tk.NORMAL)
                return
        self.current_instance_id = None
        self._populate_tree([])
        self.export_btn.configure(state=tk.DISABLED)
        self.import_tables_btn.configure(state=tk.DISABLED)
        self.delete_btn.configure(state=tk.DISABLED)
        self.undo_btn.configure(state=tk.DISABLED)

    def _load_tables(self, instance_id: int) -> None:
        tables = self.db.get_sql_tables_with_columns(instance_id)
        self._populate_tree(tables)

    def _populate_tree(self, tables: list[SqlTable]) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        total_tables = len(tables)
        total_columns = 0
        for table in tables:
            parent = self.tree.insert("", tk.END, text=table.name, open=False)
            ordered_columns = sorted(table.columns, key=lambda value: value.lower())
            total_columns += len(ordered_columns)
            for column in ordered_columns:
                self.tree.insert(parent, tk.END, text=column)
        if total_tables == 0:
            self.summary_var.set("No tables imported yet for this instance.")
        else:
            self.summary_var.set(f"{total_tables} table(s) • {total_columns} column(s)")

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
        self._load_tables(self.current_instance_id)
        if new_tables or new_columns:
            self._last_import_backup = backup
            self._last_import_instance_id = self.current_instance_id
            self.undo_btn.configure(state=tk.NORMAL)
        else:
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
