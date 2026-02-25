from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import html
import json
from pathlib import Path
import re
import traceback
from typing import Callable, Iterable, Optional
from xml.etree import ElementTree as ET

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from ... import utils
from ...database import Database
from ...export_validator_engine import (
    ExportValidationError,
    collect_records_from_xml_text,
    get_file_type as rule_file_type,
    load_rule,
    load_export_types_from_file,
    run_validation,
)
from ...models import ExportValidatorConfig, ExportValidatorConfigRecord, ExportValidatorInstance
from ...theme import ThemePalette

ITEM_TYPES: list[tuple[str, str]] = [
    ("promotions", "Promotions"),
    ("select_sets", "Select Sets"),
    ("jobstreams", "Jobstreams"),
    ("http_endpoints", "HTTP Endpoints"),
    ("agreement_choices", "Agreement Choices"),
    ("subscription_choices", "Subscription Choices"),
    ("item_choices", "Item Choices"),
    ("promotion_offers", "Promotion Offers"),
    ("data_quality_tests", "Data Quality Tests"),
    ("workflow_rules", "Workflow Rules"),
    ("scripts", "Scripts"),
    ("system_option_values", "System Option Values"),
    ("extended_distribution_staging_tables", "Extended Distribution Staging Tables"),
]

ITEM_TYPE_SAMPLE_FILES = {
    "agreement_choices": "AGREEMENT CHOICE.xml",
    "data_quality_tests": "DATA QUALITY TEST.xml",
}

ITEM_TYPE_RULES: dict[str, str] = {
    "promotions": "Promotion",
    "select_sets": "Select Sets",
    "jobstreams": "Jobstreams",
    "http_endpoints": "HTTP Endpoints",
    "agreement_choices": "Agreement Choices",
    "subscription_choices": "Subscription Choices",
    "item_choices": "Item Choices",
    "promotion_offers": "Promotion Offers",
    "data_quality_tests": "DQT",
    "workflow_rules": "Workflow Rules",
    "scripts": "Scripts",
    "system_option_values": "System Option Values",
    "extended_distribution_staging_tables": "Extended Distribution Staging Tables",
}

ITEM_TYPE_IMPORT_ALIASES: dict[str, tuple[str, ...]] = {
    "agreement_choices": ("agreement choices", "agreement choice", "agrchoice", "agr choice"),
    "data_quality_tests": ("dqt", "data quality tests", "data quality test", "automated test", "autotest"),
    "extended_distribution_staging_tables": (
        "extended distribution staging tables",
        "extended distribution staging table",
        "exdiststagingtables",
        "extendeddistributionstagingtable",
    ),
    "http_endpoints": ("http endpoints", "http endpoint", "httpendpoint"),
    "item_choices": ("item choices", "item choice", "itemchoice"),
    "jobstreams": ("jobstreams", "jobstream"),
    "promotions": ("promotions", "promotion"),
    "promotion_offers": ("promotion offers", "promotion offer"),
    "scripts": ("scripts", "script"),
    "select_sets": ("select sets", "select set", "selectset"),
    "subscription_choices": ("subscription choices", "subscription choice", "subchoice"),
    "system_option_values": (
        "system option values",
        "system option value",
        "sysoptval",
        "sys opt val",
    ),
    "workflow_rules": ("workflow rules", "workflow rule", "workflow"),
}

VALIDATION_MODES = (
    "Mass (1:1)",
    "Compressed (candidate subset)",
)


class ExportValidatorView(ttk.Frame):
    _PIN_CODE = "12345"

    def __init__(self, master: tk.Misc, db: Database, theme: ThemePalette) -> None:
        super().__init__(master, padding=(16, 16))
        self.db = db
        self.theme = theme

        self._locked = True
        self._lock_overlay: Optional[tk.Frame] = None
        self._pin_entry: Optional[ttk.Entry] = None
        self._pin_var = tk.StringVar(value="")
        self._lock_error_var = tk.StringVar(value="")

        self.instances: list[ExportValidatorInstance] = []
        self.current_instance_id: Optional[int] = None
        self.configs: dict[str, list[ExportValidatorConfig]] = {}
        self.record_counts: dict[str, int] = {}
        self.selected_item_type: Optional[str] = None
        self._inventory_path = Path("Sample_Exports_Field_Inventory.md")
        self._inventory_specs: dict[str, dict[str, object]] = {}
        self._inventory_mtime: Optional[float] = None
        self._rules_path = Path(__file__).resolve().parents[2] / "export_rules.json"
        self._export_rules: dict[str, object] = {}

        self.instance_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Select or create an instance to begin.")
        self.validation_mode_var = tk.StringVar(value=VALIDATION_MODES[0])

        self._configure_styles()
        self.configure(style="ExportValidator.Root.TFrame")
        self._load_export_rules()
        self._build_ui()
        self._load_instances()
        self.after(0, self._show_lock_overlay)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        hero = ttk.Frame(self, style="ExportValidator.Hero.TFrame", padding=(16, 12))
        hero.pack(fill=tk.X)
        ttk.Label(hero, text="Export Validator", style="ExportValidator.Title.TLabel").pack(
            side=tk.LEFT, anchor="w"
        )
        ttk.Label(hero, textvariable=self.status_var, style="ExportValidator.Badge.TLabel").pack(
            side=tk.RIGHT, padx=(12, 0)
        )

        body = ttk.Frame(self, style="ExportValidator.Root.TFrame")
        body.pack(fill=tk.BOTH, expand=True, pady=(14, 0))

        paned = ttk.Panedwindow(body, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(paned, style="ExportValidator.Root.TFrame")
        left.columnconfigure(0, weight=1)

        right = ttk.Frame(paned, style="ExportValidator.Root.TFrame")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        paned.add(left, weight=1)
        paned.add(right, weight=2)

        instance_card = ttk.Frame(left, style="ExportValidator.Card.TFrame", padding=(16, 14))
        instance_card.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(instance_card, text="Instance Setup", style="ExportValidator.Section.TLabel").pack(
            anchor="w"
        )

        instance_row = ttk.Frame(instance_card, style="ExportValidator.Card.TFrame")
        instance_row.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(instance_row, text="Instance", style="ExportValidator.Card.TLabel").pack(side=tk.LEFT)
        self.instance_combo = ttk.Combobox(
            instance_row, textvariable=self.instance_var, state="readonly", width=28
        )
        self.instance_combo.pack(side=tk.LEFT, padx=(8, 6))
        self.instance_combo.bind("<<ComboboxSelected>>", self._on_instance_selected)
        ttk.Button(instance_row, text="New...", command=self._new_instance).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(instance_row, text="Rename...", command=self._rename_instance).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(instance_row, text="Delete", command=self._delete_instance).pack(side=tk.LEFT)

        config_card = ttk.Frame(left, style="ExportValidator.Card.TFrame", padding=(16, 14))
        config_card.pack(fill=tk.BOTH, expand=True)
        config_card.columnconfigure(0, weight=1)
        ttk.Label(config_card, text="Configurations", style="ExportValidator.Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            config_card,
            text="Select an item type and load configuration files (XML/CSV).",
            style="ExportValidator.BodyMuted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 8))

        self.config_tree = ttk.Treeview(
            config_card,
            columns=("status", "updated", "file"),
            show="tree headings",
            height=14,
            style="ExportValidator.Treeview",
        )
        self.config_tree.grid(row=2, column=0, sticky="nsew")
        config_card.rowconfigure(2, weight=1)
        config_scroll = ttk.Scrollbar(config_card, orient=tk.VERTICAL, command=self.config_tree.yview)
        config_scroll.grid(row=2, column=1, sticky="ns")
        self.config_tree.configure(yscrollcommand=config_scroll.set)

        self.config_tree.heading("#0", text="Item Type")
        self.config_tree.heading("status", text="Status")
        self.config_tree.heading("updated", text="Updated")
        self.config_tree.heading("file", text="File")
        self.config_tree.column("#0", width=220, anchor="w")
        self.config_tree.column("status", width=90, anchor="center")
        self.config_tree.column("updated", width=160, anchor="w")
        self.config_tree.column("file", width=200, anchor="w")
        self.config_tree.bind("<<TreeviewSelect>>", self._on_item_selected)
        self.config_tree.bind("<Double-1>", self._on_config_double_click)

        button_row = ttk.Frame(config_card, style="ExportValidator.Card.TFrame")
        button_row.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(button_row, text="Import Config...", command=self._import_config).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(button_row, text="Import Folder...", command=self._import_config_folder).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(button_row, text="Replace Config Set...", command=self._replace_config).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(button_row, text="View Loaded...", command=self._view_loaded_configs).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(button_row, text="Validate Export...", command=self._validate_export).pack(
            side=tk.LEFT
        )
        ttk.Button(button_row, text="Scan Samples...", command=self._scan_samples_folder).pack(
            side=tk.LEFT, padx=(6, 0)
        )

        report_card = ttk.Frame(right, style="ExportValidator.Card.TFrame", padding=(16, 14))
        report_card.grid(row=0, column=0, sticky="nsew")
        report_card.columnconfigure(0, weight=1)
        report_card.rowconfigure(2, weight=1)
        ttk.Label(report_card, text="Validation Report", style="ExportValidator.Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )

        mode_row = ttk.Frame(report_card, style="ExportValidator.Card.TFrame")
        mode_row.grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Label(mode_row, text="Mode", style="ExportValidator.Card.TLabel").pack(side=tk.LEFT)
        self.validation_mode_combo = ttk.Combobox(
            mode_row,
            textvariable=self.validation_mode_var,
            state="readonly",
            width=28,
            values=VALIDATION_MODES,
        )
        self.validation_mode_combo.pack(side=tk.LEFT, padx=(8, 0))
        self.validation_mode_combo.current(0)

        self.report_text = tk.Text(
            report_card,
            wrap="word",
            height=24,
            width=72,
            background="#1c1d2b",
            foreground="#E8EAF6",
            insertbackground="#E8EAF6",
        )
        self.report_text.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        report_scroll = ttk.Scrollbar(report_card, orient=tk.VERTICAL, command=self.report_text.yview)
        report_scroll.grid(row=2, column=1, sticky="ns", pady=(8, 0))
        self.report_text.configure(yscrollcommand=report_scroll.set)
        self._set_report_text("Load a configuration and run validation to see results here.")

    # ------------------------------------------------------------------ Instance management
    def _load_instances(self) -> None:
        self.instances = self.db.get_export_validator_instances()
        names = [instance.name for instance in self.instances]
        self.instance_combo["values"] = names
        if self.current_instance_id and any(i.id == self.current_instance_id for i in self.instances):
            current = next(i for i in self.instances if i.id == self.current_instance_id)
            self.instance_combo.set(current.name)
        elif names:
            self.instance_combo.current(0)
            self.current_instance_id = self.instances[0].id
        else:
            self.instance_combo.set("")
            self.current_instance_id = None
        self._sync_instance_state()

    def _sync_instance_state(self) -> None:
        self.configs = {}
        self.record_counts = {}
        if self.current_instance_id is None:
            self.status_var.set("Select or create an instance to begin.")
            self._refresh_config_tree()
            return
        configs = self.db.get_export_validator_configs(self.current_instance_id)
        grouped: dict[str, list[ExportValidatorConfig]] = {}
        for config in configs:
            grouped.setdefault(config.item_type, []).append(config)
        self.configs = grouped
        self.record_counts = self.db.get_export_validator_record_counts(self.current_instance_id)
        total = sum(len(items) for items in grouped.values())
        total_records = sum(self.record_counts.values())
        self.status_var.set(
            f"Ready to import, replace, or validate. Files: {total} Records: {total_records}"
        )
        self._refresh_config_tree()

    def _on_instance_selected(self, _event: object) -> None:
        name = self.instance_var.get()
        match = next((i for i in self.instances if i.name == name), None)
        if match:
            self.current_instance_id = match.id
        self._sync_instance_state()

    def _load_export_rules(self) -> None:
        try:
            self._export_rules = load_export_types_from_file(self._rules_path)
        except ExportValidationError:
            self._export_rules = {}

    def _rule_name_for_item_type(self, item_type: str) -> str:
        return ITEM_TYPE_RULES.get(item_type, self._label_for_item(item_type))

    def _validation_mode(self) -> str:
        value = self.validation_mode_var.get().strip().lower()
        if value.startswith("compressed"):
            return "compressed"
        return "strict"

    def _picker_file_type(self, item_type: str) -> str:
        rule_name = self._rule_name_for_item_type(item_type)
        if not self._export_rules:
            self._load_export_rules()
        if not self._export_rules:
            return "xml"
        return rule_file_type(self._export_rules, rule_name)

    def _prompt_item_type(self, title: str) -> Optional[str]:
        selected = tk.StringVar(value="")
        labels = [label for _, label in ITEM_TYPES]
        key_by_label = {label: key for key, label in ITEM_TYPES}

        overlay = tk.Frame(self, bg="#111219")
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        overlay.lift()

        card = ttk.Frame(overlay, style="ExportValidator.Card.TFrame", padding=(14, 12))
        card.place(relx=0.5, rely=0.5, anchor="center")
        ttk.Label(card, text=title, style="ExportValidator.Section.TLabel").pack(anchor="w")
        ttk.Label(card, text="Config type", style="ExportValidator.Card.TLabel").pack(anchor="w", pady=(8, 0))
        combo = ttk.Combobox(card, state="readonly", values=labels, width=36, textvariable=selected)
        combo.pack(anchor="w", pady=(6, 12))

        if self.selected_item_type:
            current_label = self._label_for_item(self.selected_item_type)
            if current_label in labels:
                combo.set(current_label)
        if not combo.get() and labels:
            combo.current(0)

        result: dict[str, Optional[str]] = {"item_type": None}

        def finish(value: Optional[str]) -> None:
            result["item_type"] = value
            if overlay.winfo_exists():
                overlay.destroy()

        def on_ok() -> None:
            label = selected.get().strip()
            finish(key_by_label.get(label))

        def on_cancel() -> None:
            finish(None)

        button_row = ttk.Frame(card, style="ExportValidator.Card.TFrame")
        button_row.pack(anchor="e")
        ttk.Button(button_row, text="Cancel", command=on_cancel).pack(side=tk.RIGHT)
        ttk.Button(button_row, text="Continue", command=on_ok).pack(side=tk.RIGHT, padx=(0, 8))

        overlay.bind("<Escape>", lambda _e: on_cancel())
        combo.bind("<Return>", lambda _e: on_ok())
        combo.focus_set()
        self.wait_window(overlay)
        return result["item_type"]

    @staticmethod
    def _normalize_lookup(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", text.lower())

    def _build_alias_index(self) -> list[tuple[str, str]]:
        aliases: list[tuple[str, str]] = []
        for item_type, names in ITEM_TYPE_IMPORT_ALIASES.items():
            for name in names:
                normalized = self._normalize_lookup(name)
                if normalized:
                    aliases.append((normalized, item_type))
        aliases.sort(key=lambda pair: len(pair[0]), reverse=True)
        return aliases

    def _guess_item_type_from_file(self, root_folder: Path, file_path: Path) -> Optional[str]:
        alias_index = self._build_alias_index()
        candidate_parts: list[str] = []
        relative = None
        try:
            relative = file_path.relative_to(root_folder)
        except Exception:
            relative = file_path

        if relative.parts:
            candidate_parts.append(relative.parts[0])
        candidate_parts.append(file_path.parent.name)
        candidate_parts.append(file_path.stem)
        candidate_parts.append(file_path.name)

        for part in candidate_parts:
            normalized_part = self._normalize_lookup(part)
            if not normalized_part:
                continue
            for alias, item_type in alias_index:
                if (
                    normalized_part == alias
                    or normalized_part.startswith(alias)
                    or alias in normalized_part
                ):
                    return item_type
        return None

    def _record_key_token(self, item_type: str, key_values: tuple[str, ...]) -> str:
        if not key_values:
            return ""
        # Select Sets should match by select name during import conflict checks.
        if item_type == "select_sets":
            first = key_values[0].strip()
            if first:
                return first
        return "\u001f".join(key_values)

    def _new_instance(self) -> None:
        name = simpledialog.askstring("New Instance", "Instance name:", parent=self)
        if not name:
            return
        try:
            new_id = self.db.create_export_validator_instance(name)
        except ValueError as exc:
            messagebox.showerror("Instance", str(exc), parent=self)
            return
        self.current_instance_id = new_id
        self._load_instances()

    def _rename_instance(self) -> None:
        if self.current_instance_id is None:
            return
        current = next((i for i in self.instances if i.id == self.current_instance_id), None)
        if current is None:
            return
        name = simpledialog.askstring("Rename Instance", "New name:", initialvalue=current.name, parent=self)
        if not name:
            return
        try:
            self.db.update_export_validator_instance(self.current_instance_id, name)
        except ValueError as exc:
            messagebox.showerror("Instance", str(exc), parent=self)
            return
        self._load_instances()

    def _delete_instance(self) -> None:
        if self.current_instance_id is None:
            return
        current = next((i for i in self.instances if i.id == self.current_instance_id), None)
        if current is None:
            return
        confirm = messagebox.askyesno(
            "Delete Instance",
            f"Delete instance '{current.name}' and all its configurations? This cannot be undone.",
            parent=self,
        )
        if not confirm:
            return
        self.db.delete_export_validator_instance(self.current_instance_id)
        self.current_instance_id = None
        self._load_instances()

    # ------------------------------------------------------------------ Config list
    def _refresh_config_tree(self) -> None:
        self.config_tree.delete(*self.config_tree.get_children())
        for key, label in ITEM_TYPES:
            configs_for_type = self.configs.get(key, [])
            record_count = self.record_counts.get(key, 0)
            if configs_for_type:
                latest = configs_for_type[0]
                if record_count:
                    status = f"{record_count} records"
                else:
                    status = f"{len(configs_for_type)} files"
                updated = utils.format_datetime(latest.stored_at)
                filename = latest.source_filename or ""
            else:
                status = "Not loaded" if record_count == 0 else f"{record_count} records"
                updated = ""
                filename = ""
            self.config_tree.insert(
                "",
                tk.END,
                iid=key,
                text=label,
                values=(status, updated, filename),
            )
        if self.selected_item_type and self.config_tree.exists(self.selected_item_type):
            self.config_tree.selection_set(self.selected_item_type)

    def _on_item_selected(self, _event: object) -> None:
        selection = self.config_tree.selection()
        if not selection:
            self.selected_item_type = None
            return
        self.selected_item_type = selection[0]

    def _on_config_double_click(self, event: tk.Event) -> None:
        row_id = self.config_tree.identify_row(event.y)
        if row_id:
            self.config_tree.selection_set(row_id)
            self.selected_item_type = row_id
            self._run_guarded(
                "View Config Records",
                lambda: self._open_config_records_view(initial_item_type=row_id),
            )

    # ------------------------------------------------------------------ Actions
    def _run_guarded(self, label: str, action: Callable[[], None]) -> None:
        try:
            action()
        except Exception as exc:
            details = traceback.format_exc()
            self._set_report_text(details)
            messagebox.showerror(
                "Export Validator",
                f"{label} failed: {exc}",
                parent=self,
            )

    def _view_loaded_configs(self) -> None:
        self._run_guarded(
            "View Config Records",
            lambda: self._open_config_records_view(initial_item_type=self.selected_item_type),
        )

    def _export_config_source_file(self, item_type: str, source_filename: Optional[str]) -> None:
        if self.current_instance_id is None:
            messagebox.showinfo("Export Validator", "Select an instance first.", parent=self)
            return
        config = self.db.get_export_validator_config_by_source(
            self.current_instance_id, item_type, source_filename
        )
        if config is None:
            messagebox.showerror(
                "Export Validator",
                "Could not find stored config source content for this selection.",
                parent=self,
            )
            return

        file_type = self._picker_file_type(item_type)
        default_ext = ".csv" if file_type == "csv" else ".xml"
        default_name = Path(config.source_filename or "").name
        if not default_name:
            default_name = f"{self._label_for_item(item_type).replace(' ', '_')}{default_ext}"
        if not Path(default_name).suffix:
            default_name = f"{default_name}{default_ext}"

        save_path = filedialog.asksaveasfilename(
            parent=self,
            title=f"Export {self._label_for_item(item_type)} Config",
            initialfile=default_name,
            defaultextension=default_ext,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
            if file_type == "csv"
            else [("XML files", "*.xml"), ("All files", "*.*")],
        )
        if not save_path:
            return
        try:
            Path(save_path).write_text(config.xml_content, encoding="utf-8")
        except Exception as exc:
            messagebox.showerror(
                "Export Validator",
                f"Could not export config: {exc}",
                parent=self,
            )
            return
        messagebox.showinfo("Export Validator", "Config exported.", parent=self)

    def _open_config_records_view(self, initial_item_type: Optional[str] = None) -> None:
        if self.current_instance_id is None:
            messagebox.showinfo("Export Validator", "Select an instance first.", parent=self)
            return

        item_type = initial_item_type
        if not item_type:
            item_type = self._prompt_item_type("Select Config Type")
        if not item_type:
            return

        label_for_type = {key: label for key, label in ITEM_TYPES}
        type_label = label_for_type.get(item_type, item_type)
        records = self.db.get_export_validator_config_records(self.current_instance_id, item_type)

        overlay = tk.Frame(self, bg="#111219")
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        overlay.lift()

        card = ttk.Frame(overlay, style="ExportValidator.Card.TFrame", padding=(12, 12))
        card.place(relx=0.5, rely=0.5, relwidth=0.94, relheight=0.90, anchor="center")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(2, weight=1)

        header = ttk.Frame(card, style="ExportValidator.Card.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=f"{type_label} Config Records", style="ExportValidator.Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )

        def close_modal() -> None:
            if overlay.winfo_exists():
                overlay.destroy()

        def export_selected() -> None:
            selection = config_tree.selection()
            if not selection:
                messagebox.showinfo(
                    "Export Validator",
                    "Select a config first.",
                    parent=self,
                )
                return
            record = row_map.get(selection[0])
            if record is None:
                messagebox.showerror(
                    "Export Validator",
                    "Could not resolve selected config.",
                    parent=self,
                )
                return
            self._export_config_source_file(item_type, record.source_filename)

        ttk.Button(header, text="Export Selected...", command=export_selected).grid(
            row=0, column=1, sticky="e", padx=(0, 8)
        )
        ttk.Button(header, text="Close", command=close_modal).grid(row=0, column=2, sticky="e")

        search_row = ttk.Frame(card, style="ExportValidator.Card.TFrame")
        search_row.grid(row=1, column=0, sticky="ew", pady=(8, 10))
        search_row.columnconfigure(1, weight=1)
        ttk.Label(search_row, text="Search", style="ExportValidator.Card.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        search_var = tk.StringVar(value="")
        search_entry = ttk.Entry(
            search_row,
            textvariable=search_var,
            width=42,
        )
        search_entry.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Label(
            search_row,
            text="Filter by config name or source file",
            style="ExportValidator.BodyMuted.TLabel",
        ).grid(row=0, column=2, sticky="w")

        body = ttk.Frame(card, style="ExportValidator.Card.TFrame")
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=2)
        body.columnconfigure(1, weight=3)
        body.rowconfigure(0, weight=1)

        list_frame = ttk.Frame(body, style="ExportValidator.Card.TFrame")
        list_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(1, weight=1)
        ttk.Label(list_frame, text="Configs", style="ExportValidator.Card.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )

        config_tree = ttk.Treeview(
            list_frame,
            columns=("source", "stored"),
            show="tree headings",
            style="ExportValidator.Treeview",
            height=16,
        )
        config_tree.grid(row=1, column=0, sticky="nsew")
        config_tree.heading("#0", text="Config Name")
        config_tree.heading("source", text="Source File")
        config_tree.heading("stored", text="Stored")
        config_tree.column("#0", width=220, anchor="w")
        config_tree.column("source", width=260, anchor="w")
        config_tree.column("stored", width=145, anchor="w")
        config_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=config_tree.yview)
        config_scroll.grid(row=1, column=1, sticky="ns")
        config_tree.configure(yscrollcommand=config_scroll.set)

        detail_frame = ttk.Frame(body, style="ExportValidator.Card.TFrame")
        detail_frame.grid(row=0, column=1, sticky="nsew")
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(2, weight=1)
        detail_frame.rowconfigure(3, weight=0)

        selected_info = tk.StringVar(value="Select a config to inspect fields.")
        record_info = tk.StringVar(value=f"Loaded configs: {len(records)}")
        ttk.Label(detail_frame, textvariable=selected_info, style="ExportValidator.BodyMuted.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(detail_frame, textvariable=record_info, style="ExportValidator.BodyMuted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(4, 8)
        )

        fields_text = tk.Text(
            detail_frame,
            wrap="none",
            height=20,
            background="#1c1d2b",
            foreground="#E8EAF6",
            insertbackground="#E8EAF6",
        )
        fields_text.grid(row=2, column=0, sticky="nsew")
        fields_y = ttk.Scrollbar(detail_frame, orient=tk.VERTICAL, command=fields_text.yview)
        fields_y.grid(row=2, column=1, sticky="ns")
        fields_x = ttk.Scrollbar(detail_frame, orient=tk.HORIZONTAL, command=fields_text.xview)
        fields_x.grid(row=3, column=0, sticky="ew")
        fields_text.configure(yscrollcommand=fields_y.set, xscrollcommand=fields_x.set)

        row_map: dict[str, ExportValidatorConfigRecord] = {}

        def refresh_rows() -> None:
            row_map.clear()
            config_tree.delete(*config_tree.get_children())
            query = search_var.get().strip().lower()
            filtered: list[ExportValidatorConfigRecord] = []
            for record in records:
                source = (record.source_filename or "").lower()
                if query and query not in record.key_display.lower() and query not in source:
                    continue
                filtered.append(record)

            for index, record in enumerate(filtered):
                row_id = f"record_{index}"
                row_map[row_id] = record
                config_tree.insert(
                    "",
                    tk.END,
                    iid=row_id,
                    text=record.key_display,
                    values=(
                        record.source_filename or "<unknown>",
                        utils.format_datetime(record.stored_at),
                    ),
                )

            if filtered:
                first_id = next(iter(row_map.keys()))
                config_tree.selection_set(first_id)
                config_tree.focus(first_id)
                on_select()
            else:
                selected_info.set("No configs match your search.")
                record_info.set(f"Loaded configs: {len(records)}")
                fields_text.configure(state="normal")
                fields_text.delete("1.0", tk.END)
                fields_text.insert("1.0", "No matching configs.")
                fields_text.configure(state="disabled")

        def format_fields(record: ExportValidatorConfigRecord) -> str:
            try:
                payload = json.loads(record.record_payload)
            except Exception:
                return "Could not parse stored config payload."

            compare_fields = payload.get("compare_fields") if isinstance(payload, dict) else []
            variants = payload.get("records") if isinstance(payload, dict) else []
            if not isinstance(compare_fields, list):
                compare_fields = []
            if not isinstance(variants, list):
                variants = []

            lines = [
                f"Config Name: {record.key_display}",
                f"Source File: {record.source_filename or '<unknown>'}",
                f"Stored: {utils.format_datetime(record.stored_at)}",
                f"Variants: {len(variants)}",
                "",
            ]
            if not variants:
                lines.append("No field values stored.")
                return "\n".join(lines)

            def is_blankish(value: object) -> bool:
                if value is None:
                    return True
                if isinstance(value, str):
                    trimmed = value.strip()
                    if not trimmed:
                        return True
                    return trimmed.lower() in {"null", "none"}
                return False

            for idx, variant in enumerate(variants, start=1):
                variant_lines: list[str] = []
                if len(variants) > 1:
                    variant_lines.append(f"Variant {idx}")
                    variant_lines.append("-" * 20)
                if isinstance(variant, dict):
                    fields_order = compare_fields if compare_fields else sorted(variant.keys())
                    for field_name in fields_order:
                        value = variant.get(field_name, "")
                        if is_blankish(value):
                            continue
                        variant_lines.append(f"{field_name}: {value}")
                else:
                    if not is_blankish(variant):
                        variant_lines.append(str(variant))
                if not variant_lines:
                    continue
                lines.extend(variant_lines)
                lines.append("")

            if lines and lines[-1] == "":
                lines.pop()
            if len(lines) <= 4:
                lines.append("")
                lines.append("No populated fields for this config.")
            return "\n".join(lines).rstrip()

        def on_select(_event: Optional[object] = None) -> None:
            selection = config_tree.selection()
            if not selection:
                return
            row_id = selection[0]
            record = row_map.get(row_id)
            if record is None:
                return
            selected_info.set(
                f"Config: {record.key_display}  |  Source: {record.source_filename or '<unknown>'}"
            )
            record_info.set(f"Loaded configs: {len(records)}")
            fields_text.configure(state="normal")
            fields_text.delete("1.0", tk.END)
            fields_text.insert("1.0", format_fields(record))
            fields_text.configure(state="disabled")

        config_tree.bind("<<TreeviewSelect>>", on_select)
        search_var.trace_add("write", lambda *_args: refresh_rows())
        overlay.bind("<Escape>", lambda _e: close_modal())
        search_entry.focus_set()
        refresh_rows()

        self.wait_window(overlay)

    def _import_config(self) -> None:
        self._run_guarded(
            "Import Config",
            lambda: self._load_config(replace=False, prompt_for_type=True),
        )

    def _replace_config(self) -> None:
        self._run_guarded(
            "Replace Config Set",
            lambda: self._load_config(replace=True, prompt_for_type=True),
        )

    def _import_config_folder(self) -> None:
        if self.current_instance_id is None:
            messagebox.showinfo("Export Validator", "Select an instance first.", parent=self)
            return
        folder = filedialog.askdirectory(parent=self, title="Select Configuration Folder")
        if not folder:
            return

        root = Path(folder)
        files = sorted(
            [
                path
                for path in root.rglob("*")
                if path.is_file() and path.suffix.lower() in {".xml", ".csv"}
            ]
        )
        if not files:
            messagebox.showinfo(
                "Export Validator",
                "No XML/CSV files found in the selected folder.",
                parent=self,
            )
            return

        imported_by_type: dict[str, int] = defaultdict(int)
        skipped_unmapped: list[Path] = []
        failed_files: list[str] = []

        for path in files:
            item_type = self._guess_item_type_from_file(root, path)
            if not item_type:
                skipped_unmapped.append(path)
                continue

            expected_type = self._picker_file_type(item_type)
            suffix = path.suffix.lower()
            if expected_type == "csv" and suffix != ".csv":
                skipped_unmapped.append(path)
                continue
            if expected_type == "xml" and suffix != ".xml":
                skipped_unmapped.append(path)
                continue

            file_text = self._read_file_text(str(path))
            if file_text is None:
                failed_files.append(f"{path.name}: could not read")
                continue

            if expected_type == "xml":
                try:
                    ET.fromstring(file_text)
                except ET.ParseError as exc:
                    failed_files.append(f"{path.name}: invalid XML ({exc})")
                    continue

            try:
                source_name = str(path.relative_to(root)).replace("\\", "/")
                self.db.upsert_export_validator_config(
                    instance_id=self.current_instance_id,
                    item_type=item_type,
                    source_filename=source_name,
                    xml_content=file_text,
                )
                imported_by_type[item_type] += 1
            except Exception as exc:
                failed_files.append(f"{path.name}: {exc}")

        self._sync_instance_state()
        lines = [
            "Export Validator Config Folder Import",
            f"Folder: {root}",
            f"Files scanned: {len(files)}",
            f"Files imported: {sum(imported_by_type.values())}",
            "",
            "Imported by type:",
        ]
        if imported_by_type:
            for item_type in sorted(imported_by_type.keys()):
                lines.append(f"  {self._label_for_item(item_type)}: {imported_by_type[item_type]}")
        else:
            lines.append("  None")

        if skipped_unmapped:
            lines.append("")
            lines.append(f"Skipped (unmapped or wrong file type): {len(skipped_unmapped)}")
            for path in skipped_unmapped[:25]:
                lines.append(f"  - {path.name}")
            if len(skipped_unmapped) > 25:
                lines.append(f"  ...and {len(skipped_unmapped) - 25} more")

        if failed_files:
            lines.append("")
            lines.append(f"Failed imports: {len(failed_files)}")
            for failure in failed_files[:25]:
                lines.append(f"  - {failure}")
            if len(failed_files) > 25:
                lines.append(f"  ...and {len(failed_files) - 25} more")

        self._set_report_text("\n".join(lines))
        messagebox.showinfo(
            "Export Validator",
            (
                f"Folder import complete.\n"
                f"Imported: {sum(imported_by_type.values())}\n"
                f"Skipped: {len(skipped_unmapped)}\n"
                f"Failed: {len(failed_files)}"
            ),
            parent=self,
        )

    def _load_config(self, *, replace: bool, prompt_for_type: bool) -> None:
        if self.current_instance_id is None:
            messagebox.showinfo("Export Validator", "Select an instance first.", parent=self)
            return

        item_type = self.selected_item_type
        if prompt_for_type:
            picked_type = self._prompt_item_type("Select Config Type")
            if not picked_type:
                return
            item_type = picked_type
            self.selected_item_type = picked_type

        if not item_type:
            messagebox.showinfo("Export Validator", "Select an item type first.", parent=self)
            return
        if self.config_tree.exists(item_type):
            self.config_tree.selection_set(item_type)
            self.config_tree.focus(item_type)

        existing_configs = self.configs.get(item_type, [])
        if replace and existing_configs:
            confirm = messagebox.askyesno(
                "Replace Configuration",
                (
                    f"Replace all {len(existing_configs)} loaded configuration file(s) "
                    "for this item type with the new file?"
                ),
                parent=self,
            )
            if not confirm:
                return
        file_type = self._picker_file_type(item_type)
        path = filedialog.askopenfilename(
            parent=self,
            title=f"Select {'CSV' if file_type == 'csv' else 'XML'} Configuration",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
            if file_type == "csv"
            else [("XML files", "*.xml"), ("All files", "*.*")],
        )
        if not path:
            return
        file_text = self._read_file_text(path)
        if file_text is None:
            return
        if file_type == "xml" and not self._parse_xml(file_text):
            return

        source_name = Path(path).name
        records_saved = 0
        records_skipped = 0
        records_invalid = 0
        records_failed = 0
        overwrite_existing = False

        if file_type == "xml":
            if not self._export_rules:
                self._load_export_rules()
            if not self._export_rules:
                messagebox.showerror(
                    "Export Validator",
                    f"Rules file not available: {self._rules_path}",
                    parent=self,
                )
                return
            try:
                rule = load_rule(self._export_rules, self._rule_name_for_item_type(item_type))
                collection = collect_records_from_xml_text(file_text, rule)
            except ExportValidationError as exc:
                messagebox.showerror("Export Validator", f"Invalid rule setup: {exc}", parent=self)
                return
            except ET.ParseError as exc:
                messagebox.showerror("Export Validator", f"Invalid XML file: {exc}", parent=self)
                return

            if collection.total_records == 0:
                messagebox.showwarning(
                    "Export Validator",
                    "No records were found for this config type using the configured rule.",
                    parent=self,
                )
                return

            existing_map = self.db.get_export_validator_record_keys(self.current_instance_id, item_type)
            incoming = sorted(collection.records.items(), key=lambda pair: " | ".join(pair[0]))
            conflicts: list[tuple[str, str, list[object]]] = []
            for key_values, records_for_key in incoming:
                key_token = self._record_key_token(item_type, key_values)
                if key_token in existing_map:
                    conflicts.append((key_token, records_for_key[0].key_display, records_for_key))

            if conflicts and not replace:
                preview = "\n".join(f"- {entry[1]}" for entry in conflicts[:10])
                if len(conflicts) > 10:
                    preview = f"{preview}\n...and {len(conflicts) - 10} more"
                overwrite_existing = messagebox.askyesno(
                    "Overwrite Existing Config Records?",
                    (
                        f"{len(conflicts)} matching record key(s) already exist.\n\n"
                        "Import will continue either way.\n"
                        "Choose Yes to overwrite matching records, or No to skip them.\n\n"
                        f"{preview}"
                    ),
                    parent=self,
                )

            if replace:
                self.db.delete_export_validator_config_records_for_item_type(
                    self.current_instance_id, item_type
                )
                overwrite_existing = True

            for key_values, records_for_key in incoming:
                key_token = self._record_key_token(item_type, key_values)
                if not key_token.strip():
                    records_invalid += 1
                    continue
                exists = key_token in existing_map
                if exists and not overwrite_existing:
                    records_skipped += 1
                    continue
                payload = {
                    "compare_fields": rule.compare_fields,
                    "records": [record.fields for record in records_for_key],
                }
                try:
                    self.db.upsert_export_validator_config_record(
                        instance_id=self.current_instance_id,
                        item_type=item_type,
                        record_key=key_token,
                        key_display=records_for_key[0].key_display,
                        record_payload=json.dumps(payload, ensure_ascii=True),
                        source_filename=source_name,
                    )
                    records_saved += 1
                except Exception:
                    records_failed += 1

        try:
            if replace:
                self.db.delete_export_validator_configs_for_item_type(
                    self.current_instance_id, item_type
                )
            self.db.upsert_export_validator_config(
                instance_id=self.current_instance_id,
                item_type=item_type,
                source_filename=Path(path).name,
                xml_content=file_text,
            )
        except Exception as exc:
            messagebox.showerror("Export Validator", f"Could not save configuration: {exc}", parent=self)
            return
        self._sync_instance_state()
        if file_type == "xml":
            messagebox.showinfo(
                "Export Validator",
                (
                    f"Configuration imported.\n"
                    f"Records saved: {records_saved}\n"
                    f"Records skipped (existing): {records_skipped}\n"
                    f"Records skipped (invalid key): {records_invalid}\n"
                    f"Records failed to save: {records_failed}"
                ),
                parent=self,
            )
        else:
            messagebox.showinfo("Export Validator", "Configuration saved.", parent=self)

    def _validate_export(self) -> None:
        if self.current_instance_id is None:
            messagebox.showinfo("Export Validator", "Select an instance first.", parent=self)
            return
        item_type = self.selected_item_type
        if not item_type:
            messagebox.showinfo("Export Validator", "Select an item type first.", parent=self)
            return
        configs_for_type = self.configs.get(item_type, [])
        if not configs_for_type:
            messagebox.showinfo(
                "Export Validator",
                "Load a configuration for this item type before validating.",
                parent=self,
            )
            return
        config = configs_for_type[0]
        if len(configs_for_type) > 1:
            messagebox.showinfo(
                "Export Validator",
                (
                    "Multiple configuration files are loaded for this type. "
                    "Current validation uses the most recently imported config. "
                    "Aggregation across all files is the next step."
                ),
                parent=self,
            )
        file_type = self._picker_file_type(item_type)
        path = filedialog.askopenfilename(
            parent=self,
            title=f"Select {'CSV' if file_type == 'csv' else 'XML'} Export to Validate",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
            if file_type == "csv"
            else [("XML files", "*.xml"), ("All files", "*.*")],
        )
        if not path:
            return
        candidate_text = self._read_file_text(path)
        if candidate_text is None:
            return
        if file_type == "xml" and not self._parse_xml(candidate_text):
            return
        report = self._build_validation_report(config, Path(path).name, candidate_text)
        self._set_report_text(report)

    def _scan_samples_folder(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="Select XML Samples Folder")
        if not folder:
            return
        xml_files = sorted(
            [path for path in Path(folder).iterdir() if path.is_file() and path.suffix.lower() == ".xml"]
        )
        if not xml_files:
            messagebox.showinfo("Export Validator", "No XML files found in that folder.", parent=self)
            return
        report = self._build_sample_inventory_report(Path(folder), xml_files)
        self._set_report_text(report)

    # ------------------------------------------------------------------ Validation helpers
    def _read_file_text(self, path: str) -> Optional[str]:
        try:
            return Path(path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                return Path(path).read_text(encoding="utf-16")
            except Exception as exc:
                messagebox.showerror("Export Validator", f"Could not read file: {exc}", parent=self)
                return None
        except Exception as exc:
            messagebox.showerror("Export Validator", f"Could not read file: {exc}", parent=self)
            return None

    def _parse_xml(self, xml_text: str) -> bool:
        try:
            ET.fromstring(xml_text)
            return True
        except ET.ParseError as exc:
            messagebox.showerror("Export Validator", f"Invalid XML file: {exc}", parent=self)
            return False

    @staticmethod
    def _clean_tag(tag: str) -> str:
        if "}" in tag:
            return tag.split("}", 1)[1]
        return tag

    def _summarize_xml(self, xml_text: str) -> dict[str, int]:
        root = ET.fromstring(xml_text)
        counts: dict[str, int] = {}

        def walk(node: ET.Element, prefix: str) -> None:
            tag = self._clean_tag(node.tag)
            path = f"{prefix}/{tag}" if prefix else tag
            counts[path] = counts.get(path, 0) + 1
            for child in list(node):
                walk(child, path)

        walk(root, "")
        return counts

    def _inventory_xml_fields(self, xml_text: str) -> dict[str, dict[str, object]]:
        root = ET.fromstring(xml_text)
        inventory: dict[str, dict[str, object]] = {}

        def walk(node: ET.Element, prefix: str) -> None:
            tag = self._clean_tag(node.tag)
            path = f"{prefix}/{tag}" if prefix else tag
            entry = inventory.setdefault(path, {"count": 0, "attrs": set()})
            entry["count"] = int(entry["count"]) + 1
            attrs = entry["attrs"]
            if isinstance(attrs, set):
                for attr in node.attrib.keys():
                    attrs.add(attr)
            for child in list(node):
                walk(child, path)

        walk(root, "")
        return inventory

    def _build_sample_inventory_report(self, folder: Path, xml_files: list[Path]) -> str:
        lines = [
            "Export Validator Field Inventory",
            f"Folder: {folder}",
            f"Files scanned: {len(xml_files)}",
            "",
        ]
        for path in xml_files:
            lines.append(f"File: {path.name}")
            xml_text = self._read_file_text(str(path))
            if xml_text is None:
                lines.append("  ERROR: Could not read file.")
                lines.append("")
                continue
            try:
                root = ET.fromstring(xml_text)
            except ET.ParseError as exc:
                lines.append(f"  ERROR: Invalid XML ({exc})")
                lines.append("")
                continue
            lines.append(f"  Root: {self._clean_tag(root.tag)}")
            inventory = self._inventory_xml_fields(xml_text)
            for item_path in sorted(inventory.keys()):
                entry = inventory[item_path]
                count = entry.get("count", 0)
                attrs = entry.get("attrs", set())
                attrs_list = ", ".join(sorted(attrs)) if attrs else "None"
                lines.append(f"  - {item_path} (count {count}) attrs: {attrs_list}")
            lines.append("")
        lines.append(
            "Note: Use this inventory to define which fields should be validated. "
            "All other fields remain stored in the XML but will be ignored by validation."
        )
        return "\n".join(lines)

    def _build_validation_report(
        self,
        config: ExportValidatorConfig,
        candidate_filename: str,
        candidate_content: str,
    ) -> str:
        item_label = self._label_for_item(config.item_type)
        rule_name = self._rule_name_for_item_type(config.item_type)
        if not self._export_rules:
            self._load_export_rules()
        if not self._export_rules:
            return (
                "Core Export Validator Notes\n"
                "===========================\n"
                f"Export type: {item_label}\n"
                "Result: FAIL\n"
                f"Rules file not available: {self._rules_path}"
            )
        try:
            output = run_validation(
                export_types=self._export_rules,
                export_type=rule_name,
                baseline_content=config.xml_content,
                candidate_content=candidate_content,
                baseline_name=config.source_filename or "Stored configuration",
                candidate_name=candidate_filename,
                rules_name=str(self._rules_path),
                mode=self._validation_mode(),
            )
            return output.report_text
        except ExportValidationError as exc:
            return (
                "Core Export Validator Notes\n"
                "===========================\n"
                f"Export type: {item_label}\n"
                "Result: FAIL\n"
                f"Validation setup error: {exc}"
            )
        except ET.ParseError as exc:
            return (
                "Core Export Validator Notes\n"
                "===========================\n"
                f"Export type: {item_label}\n"
                "Result: FAIL\n"
                f"XML parse error: {exc}"
            )
        except Exception as exc:
            return (
                "Core Export Validator Notes\n"
                "===========================\n"
                f"Export type: {item_label}\n"
                "Result: FAIL\n"
                f"Unexpected error: {exc}"
            )

    def _build_data_quality_tests_report(
        self,
        config: ExportValidatorConfig,
        candidate_filename: str,
        candidate_xml: str,
    ) -> str:
        now = utils.format_datetime(datetime.now())
        config_updated = utils.format_datetime(config.stored_at)
        config_records, config_meta, fields = self._extract_data_quality_tests(config.xml_content)
        candidate_records, candidate_meta, _fields = self._extract_data_quality_tests(candidate_xml)
        config_map = {rec["tst_id"]: rec for rec in config_records if rec.get("tst_id")}
        candidate_map = {rec["tst_id"]: rec for rec in candidate_records if rec.get("tst_id")}

        missing_ids = sorted(set(config_map) - set(candidate_map))
        added_ids = sorted(set(candidate_map) - set(config_map))
        shared_ids = sorted(set(config_map) & set(candidate_map))

        lines = [
            "Export Validator Report",
            f"Generated: {now}",
            f"Item Type: {self._label_for_item(config.item_type)}",
            f"Config File: {config.source_filename or 'Stored XML'}",
            f"Config Updated: {config_updated}",
            f"Candidate File: {candidate_filename}",
            f"Config Records: {len(config_map)}",
            f"Candidate Records: {len(candidate_map)}",
            "",
        ]

        meta_diffs = self._diff_data_meta(config_meta, candidate_meta, fields.get("data_attrs", []))
        if meta_diffs:
            lines.append("Header differences:")
            lines.extend(f"  {diff}" for diff in meta_diffs)
            lines.append("")

        if missing_ids:
            lines.append("Missing tests (in config, not in candidate):")
            for tst_id in missing_ids[:50]:
                lines.append(f"  - {tst_id}")
            if len(missing_ids) > 50:
                lines.append(f"  ...and {len(missing_ids) - 50} more")
            lines.append("")

        if added_ids:
            lines.append("New tests (in candidate, not in config):")
            for tst_id in added_ids[:50]:
                lines.append(f"  + {tst_id}")
            if len(added_ids) > 50:
                lines.append(f"  ...and {len(added_ids) - 50} more")
            lines.append("")

        changes: list[str] = []
        for tst_id in shared_ids:
            config_rec = config_map[tst_id]
            candidate_rec = candidate_map[tst_id]
            diffs = self._diff_data_quality_test(config_rec, candidate_rec, fields.get("row_fields", []))
            if diffs:
                changes.append(f"{tst_id}")
                for diff_line in diffs:
                    changes.append(f"  {diff_line}")
                changes.append("")

        if not missing_ids and not added_ids and not changes:
            lines.append("No differences detected for Data Quality Tests.")
            return "\n".join(lines)

        if changes:
            lines.append("Field changes:")
            lines.extend(changes)
            if lines[-1] == "":
                lines.pop()

        lines.append("")
        lines.append(
            "Note: Only the specified fields are validated. All other XML fields are kept but ignored."
        )
        return "\n".join(lines)

    def _build_agreement_choices_report(
        self,
        config: ExportValidatorConfig,
        candidate_filename: str,
        candidate_xml: str,
    ) -> str:
        now = utils.format_datetime(datetime.now())
        config_updated = utils.format_datetime(config.stored_at)
        config_records, config_meta, fields = self._extract_agreement_choices(config.xml_content)
        candidate_records, candidate_meta, _fields = self._extract_agreement_choices(candidate_xml)
        config_map = {rec["agr_chc"]: rec for rec in config_records if rec.get("agr_chc")}
        candidate_map = {rec["agr_chc"]: rec for rec in candidate_records if rec.get("agr_chc")}

        missing_ids = sorted(set(config_map) - set(candidate_map))
        added_ids = sorted(set(candidate_map) - set(config_map))
        shared_ids = sorted(set(config_map) & set(candidate_map))

        lines = [
            "Export Validator Report",
            f"Generated: {now}",
            f"Item Type: {self._label_for_item(config.item_type)}",
            f"Config File: {config.source_filename or 'Stored XML'}",
            f"Config Updated: {config_updated}",
            f"Candidate File: {candidate_filename}",
            f"Config Records: {len(config_map)}",
            f"Candidate Records: {len(candidate_map)}",
            "",
        ]

        meta_diffs = self._diff_data_meta(config_meta, candidate_meta, fields.get("data_attrs", []))
        if meta_diffs:
            lines.append("Header differences:")
            lines.extend(f"  {diff}" for diff in meta_diffs)
            lines.append("")

        if missing_ids:
            lines.append("Missing agreement choices (in config, not in candidate):")
            for item_id in missing_ids[:50]:
                lines.append(f"  - {item_id}")
            if len(missing_ids) > 50:
                lines.append(f"  ...and {len(missing_ids) - 50} more")
            lines.append("")

        if added_ids:
            lines.append("New agreement choices (in candidate, not in config):")
            for item_id in added_ids[:50]:
                lines.append(f"  + {item_id}")
            if len(added_ids) > 50:
                lines.append(f"  ...and {len(added_ids) - 50} more")
            lines.append("")

        changes: list[str] = []
        for item_id in shared_ids:
            config_rec = config_map[item_id]
            candidate_rec = candidate_map[item_id]
            diffs = self._diff_agreement_choice(config_rec, candidate_rec, fields.get("row_fields", []))
            if diffs:
                changes.append(f"{item_id}")
                for diff_line in diffs:
                    changes.append(f"  {diff_line}")
                changes.append("")

        if not missing_ids and not added_ids and not changes:
            lines.append("No differences detected for Agreement Choices.")
            return "\n".join(lines)

        if changes:
            lines.append("Field changes:")
            lines.extend(changes)
            if lines[-1] == "":
                lines.pop()

        lines.append("")
        lines.append(
            "Note: Only the specified fields are validated. All other XML fields are kept but ignored."
        )
        return "\n".join(lines)

    def _extract_data_quality_tests(
        self, xml_text: str
    ) -> tuple[list[dict[str, object]], dict[str, str], dict[str, object]]:
        root = ET.fromstring(xml_text)
        spec = self._resolve_inventory_spec(
            "data_quality_tests",
            default_container="AutomatedTest",
            default_row_fields=[
                "TST_ID",
                "SUC_CON",
                "TST_DESC",
                "TST_ENBL",
                "CSV_OUT",
                "SQL_QRY",
            ],
            default_data_attrs=["EntityType", "UpdateDate", "UpdateUser"],
        )
        data_elem = self._resolve_data_element(root, spec["container"])
        meta = self._extract_data_meta(data_elem, spec["data_attrs"])
        records: list[dict[str, object]] = []

        for row in self._iter_row_images(data_elem, spec["container"]):
            child_map = self._child_text_map(row)
            record: dict[str, object] = {}
            for field in spec["row_fields"]:
                key = field.lower()
                record[key] = child_map.get(key, "")
            key_field = self._resolve_key_field(spec["row_fields"], ["TST_ID"])
            record["tst_id"] = str(record.get(key_field.lower(), "")).strip()
            records.append(record)
        return records, meta, spec

    def _extract_agreement_choices(
        self, xml_text: str
    ) -> tuple[list[dict[str, object]], dict[str, str], dict[str, object]]:
        root = ET.fromstring(xml_text)
        spec = self._resolve_inventory_spec(
            "agreement_choices",
            default_container="AgreementChoice",
            default_row_fields=[
                "AGR_CHC",
                "AGR_REN",
                "BIL_PRD",
                "CHC_DSC",
                "CHC_STS",
                "PRC_PRD",
                "SRV_PRD",
            ],
            default_data_attrs=["EntityType", "UpdateDate", "UpdateUser"],
        )
        data_elem = self._resolve_data_element(root, spec["container"])
        meta = self._extract_data_meta(data_elem, spec["data_attrs"])
        records: list[dict[str, object]] = []

        for row in self._iter_row_images(data_elem, spec["container"]):
            child_map = self._child_text_map(row)
            record: dict[str, object] = {}
            for field in spec["row_fields"]:
                key = field.lower()
                record[key] = child_map.get(key, "")
            key_field = self._resolve_key_field(spec["row_fields"], ["AGR_CHC"])
            record["agr_chc"] = str(record.get(key_field.lower(), "")).strip()
            records.append(record)
        return records, meta, spec

    @staticmethod
    def _attr_case_insensitive(element: ET.Element, name: str) -> Optional[str]:
        target = name.lower()
        for key, value in element.attrib.items():
            if key.lower() == target:
                return value
        return None

    def _resolve_data_element(self, root: ET.Element, entity_type: str) -> ET.Element:
        data_elements = [elem for elem in root.iter() if self._clean_tag(elem.tag).lower() == "data"]
        target_lower = entity_type.lower()
        for elem in data_elements:
            entity_value = self._attr_case_insensitive(elem, "EntityType")
            if (entity_value or "").lower() == target_lower:
                return elem
        if data_elements:
            return data_elements[0]
        return root

    def _extract_data_meta(self, data_elem: ET.Element, attrs: list[str]) -> dict[str, str]:
        requested = {attr.strip() for attr in attrs}
        entity_type = self._attr_case_insensitive(data_elem, "EntityType") or "" if "EntityType" in requested else ""
        update_date = self._attr_case_insensitive(data_elem, "UpdateDate") or "" if "UpdateDate" in requested else ""
        update_user = self._attr_case_insensitive(data_elem, "UpdateUser") or "" if "UpdateUser" in requested else ""
        return {
            "entity_type": str(entity_type).strip(),
            "update_date": str(update_date).strip(),
            "update_user": str(update_user).strip(),
        }

    def _iter_row_images(self, data_elem: ET.Element, container_tag: str) -> Iterable[ET.Element]:
        target = container_tag.lower()
        rows: list[ET.Element] = []
        for element in data_elem.iter():
            if self._clean_tag(element.tag).lower() != target:
                continue
            for child in list(element):
                if self._clean_tag(child.tag).lower() == "rowimage":
                    rows.append(child)
        if not rows:
            for child in data_elem.iter():
                if self._clean_tag(child.tag).lower() == "rowimage":
                    rows.append(child)
        return rows

    def _child_text_map(self, element: ET.Element) -> dict[str, str]:
        data: dict[str, str] = {}
        for child in list(element):
            key = self._clean_tag(child.tag).lower()
            if key == "sql_qry":
                data[key] = self._extract_sql_qry(child)
            else:
                data[key] = (child.text or "").strip()
        return data

    @staticmethod
    def _extract_sql_qry(element: ET.Element) -> str:
        if list(element):
            inner = "".join(ET.tostring(child, encoding="unicode") for child in list(element))
            return html.unescape(inner).strip()
        return html.unescape(element.text or "").strip()

    def _diff_data_quality_test(
        self, base: dict[str, object], cand: dict[str, object], fields: list[str]
    ) -> list[str]:
        diffs: list[str] = []

        def compare(label: str, left: object, right: object) -> None:
            if left == right:
                return
            diffs.append(f"{label}: {left} -> {right}")

        fields_lower = [field.lower() for field in fields]
        for field in fields:
            key = field.lower()
            left = base.get(key, "")
            right = cand.get(key, "")
            if key == "sql_qry":
                base_sql = str(left or "")
                cand_sql = str(right or "")
                if base_sql != cand_sql:
                    diffs.append("SQL_QRY:")
                    diffs.append(f"    - {base_sql.replace(chr(10), chr(10) + '      ')}")
                    diffs.append(f"    + {cand_sql.replace(chr(10), chr(10) + '      ')}")
                continue
            compare(field, self._canonical_text(left), self._canonical_text(right))

        return diffs

    @staticmethod
    def _canonical_text(value: object) -> str:
        return str(value or "")

    def _diff_agreement_choice(
        self, base: dict[str, object], cand: dict[str, object], fields: list[str]
    ) -> list[str]:
        diffs: list[str] = []

        def compare(label: str, left: object, right: object) -> None:
            if left == right:
                return
            diffs.append(f"{label}: {left} -> {right}")

        for field in fields:
            key = field.lower()
            compare(field, self._canonical_text(base.get(key)), self._canonical_text(cand.get(key)))

        return diffs

    def _diff_data_meta(
        self, base: dict[str, str], cand: dict[str, str], attrs: list[str]
    ) -> list[str]:
        diffs: list[str] = []
        if "EntityType" in attrs and self._canonical_text(base.get("entity_type")) != self._canonical_text(cand.get("entity_type")):
            diffs.append(
                f"EntityType: {self._canonical_text(base.get('entity_type'))} -> {self._canonical_text(cand.get('entity_type'))}"
            )
        if "UpdateDate" in attrs and self._canonical_text(base.get("update_date")) != self._canonical_text(cand.get("update_date")):
            diffs.append(
                f"UpdateDate: {self._canonical_text(base.get('update_date'))} -> {self._canonical_text(cand.get('update_date'))}"
            )
        if "UpdateUser" in attrs and self._canonical_text(base.get("update_user")) != self._canonical_text(cand.get("update_user")):
            diffs.append(
                f"UpdateUser: {self._canonical_text(base.get('update_user'))} -> {self._canonical_text(cand.get('update_user'))}"
            )
        return diffs

    def _resolve_key_field(self, fields: list[str], preferred: list[str]) -> str:
        for name in preferred:
            for field in fields:
                if field.upper() == name.upper():
                    return field
        return fields[0] if fields else preferred[0]

    def _resolve_inventory_spec(
        self,
        item_type: str,
        *,
        default_container: str,
        default_row_fields: list[str],
        default_data_attrs: list[str],
    ) -> dict[str, object]:
        self._load_inventory_specs()
        filename = ITEM_TYPE_SAMPLE_FILES.get(item_type, "")
        spec = self._inventory_specs.get(filename, {})
        container = spec.get("container") if spec else None
        row_fields = spec.get("row_fields") if spec else None
        data_attrs = spec.get("data_attrs") if spec else None

        resolved = {
            "container": container or default_container,
            "row_fields": row_fields if row_fields else default_row_fields,
            "data_attrs": data_attrs if data_attrs else default_data_attrs,
        }
        return resolved

    def _load_inventory_specs(self) -> None:
        if not self._inventory_path.exists():
            return
        mtime = self._inventory_path.stat().st_mtime
        if self._inventory_mtime and self._inventory_mtime == mtime:
            return
        self._inventory_mtime = mtime
        text = self._inventory_path.read_text(encoding="utf-8")
        current: Optional[str] = None
        specs: dict[str, dict[str, object]] = {}
        for line in text.splitlines():
            if line.startswith("## "):
                current = line[3:].strip()
                specs[current] = {"row_fields": [], "data_attrs": [], "container": None}
                continue
            stripped = line.strip()
            if not stripped.startswith("- `") or current is None:
                continue
            parts = stripped.split("`")
            if len(parts) < 2:
                continue
            path = parts[1]
            attrs: list[str] = []
            if "attrs:" in stripped:
                attrs_part = stripped.split("attrs:", 1)[1].strip()
                if attrs_part and attrs_part.lower() != "none":
                    attrs = [item.strip() for item in attrs_part.split(",") if item.strip()]
            if path == "Data":
                specs[current]["data_attrs"] = attrs
                continue
            segments = path.split("/")
            if len(segments) >= 4 and segments[0] == "Data" and segments[2] == "RowImage":
                container = segments[1]
                field = segments[3]
                if field:
                    specs[current]["row_fields"].append(field)
                    if not specs[current].get("container"):
                        specs[current]["container"] = container
        self._inventory_specs = specs


    def _label_for_item(self, item_type: str) -> str:
        for key, label in ITEM_TYPES:
            if key == item_type:
                return label
        return item_type

    def _set_report_text(self, text: str) -> None:
        self.report_text.configure(state="normal")
        self.report_text.delete("1.0", tk.END)
        self.report_text.insert("1.0", text)
        self.report_text.configure(state="disabled")

    # ------------------------------------------------------------------ Lock overlay
    def _show_lock_overlay(self) -> None:
        if not self._locked or self._lock_overlay is not None:
            return
        overlay = tk.Frame(self, bg="#111219")
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._lock_overlay = overlay
        card = ttk.Frame(overlay, padding=24)
        card.place(relx=0.5, rely=0.5, anchor="center")
        ttk.Label(
            card,
            text="Export Validator tab is under development.",
            style="SidebarHeading.TLabel",
            wraplength=420,
            justify="center",
        ).pack(anchor="center")
        ttk.Label(
            card,
            text=(
                "A validation tool where configurations may be loaded in for a production instance via "
                "uploading the instance's xml, these configurations may be updated at any time, validate "
                "exported configurations against the configurations stored in the database for a production "
                "instance."
            ),
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

    def apply_theme(self, theme: ThemePalette) -> None:
        self.theme = theme
        self._configure_styles()
        for widget in self.winfo_children():
            try:
                widget.configure(style="ExportValidator.Root.TFrame")
            except tk.TclError:
                pass

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.configure("ExportValidator.Root.TFrame", background=self.theme.surface_bg)
        style.configure("ExportValidator.Hero.TFrame", background=self.theme.card_alt_bg)
        style.configure("ExportValidator.Card.TFrame", background=self.theme.card_bg)
        style.configure(
            "ExportValidator.Title.TLabel",
            background=self.theme.card_alt_bg,
            foreground=self.theme.text_primary,
            font=("Segoe UI", 14, "bold"),
        )
        style.configure(
            "ExportValidator.Section.TLabel",
            background=self.theme.card_bg,
            foreground=self.theme.accent,
            font=("Segoe UI", 11, "bold"),
        )
        style.configure(
            "ExportValidator.BodyMuted.TLabel",
            background=self.theme.card_bg,
            foreground=self.theme.text_muted,
            font=("Segoe UI", 9),
        )
        style.configure(
            "ExportValidator.Card.TLabel",
            background=self.theme.card_bg,
            foreground=self.theme.text_primary,
            font=("Segoe UI", 10),
        )
        style.configure(
            "ExportValidator.Badge.TLabel",
            background=self.theme.surface_alt_bg,
            foreground=self.theme.text_secondary,
            padding=(12, 4),
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "ExportValidator.Treeview",
            background=self.theme.list_bg,
            fieldbackground=self.theme.list_bg,
            foreground=self.theme.text_primary,
            borderwidth=0,
            font=("Segoe UI", 10),
        )
        style.configure(
            "ExportValidator.Treeview.Heading",
            background=self.theme.list_alt_bg,
            foreground=self.theme.text_secondary,
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "ExportValidator.Treeview",
            background=[("selected", self.theme.list_selected_bg)],
            foreground=[("selected", self.theme.list_selected_fg)],
        )

    def is_locked(self) -> bool:
        return self._locked

    def focus_lock_entry(self) -> None:
        if self._pin_entry is not None:
            self._pin_entry.focus_set()

    def notify_locked(self) -> None:
        messagebox.showinfo("Export Validator", "Enter the PIN to unlock this tab.", parent=self)
        self.focus_lock_entry()
