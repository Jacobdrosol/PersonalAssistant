from __future__ import annotations

from datetime import datetime
import html
from pathlib import Path
from typing import Iterable, Optional
from xml.etree import ElementTree as ET

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from ... import utils
from ...database import Database
from ...models import ExportValidatorConfig, ExportValidatorInstance
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
    ("system_option_values", "System Option Values"),
    ("extended_distribution_staging_tables", "Extended Distribution Staging Tables"),
]

ITEM_TYPE_SAMPLE_FILES = {
    "agreement_choices": "AGREEMENT CHOICE.xml",
    "data_quality_tests": "DATA QUALITY TEST.xml",
}


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
        self.configs: dict[str, ExportValidatorConfig] = {}
        self.selected_item_type: Optional[str] = None
        self._inventory_path = Path("Sample_Exports_Field_Inventory.md")
        self._inventory_specs: dict[str, dict[str, object]] = {}
        self._inventory_mtime: Optional[float] = None

        self.instance_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Select or create an instance to begin.")

        self._configure_styles()
        self.configure(style="ExportValidator.Root.TFrame")
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
            text="Select an item type and load its configuration XML.",
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

        button_row = ttk.Frame(config_card, style="ExportValidator.Card.TFrame")
        button_row.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(button_row, text="Import Config...", command=self._import_config).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(button_row, text="Replace Config...", command=self._replace_config).pack(
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
        report_card.rowconfigure(1, weight=1)
        ttk.Label(report_card, text="Validation Report", style="ExportValidator.Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )

        self.report_text = tk.Text(
            report_card,
            wrap="word",
            height=24,
            width=72,
            background="#1c1d2b",
            foreground="#E8EAF6",
            insertbackground="#E8EAF6",
        )
        self.report_text.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        report_scroll = ttk.Scrollbar(report_card, orient=tk.VERTICAL, command=self.report_text.yview)
        report_scroll.grid(row=1, column=1, sticky="ns", pady=(8, 0))
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
        if self.current_instance_id is None:
            self.status_var.set("Select or create an instance to begin.")
            self._refresh_config_tree()
            return
        configs = self.db.get_export_validator_configs(self.current_instance_id)
        self.configs = {cfg.item_type: cfg for cfg in configs}
        self.status_var.set("Ready to import, replace, or validate.")
        self._refresh_config_tree()

    def _on_instance_selected(self, _event: object) -> None:
        name = self.instance_var.get()
        match = next((i for i in self.instances if i.name == name), None)
        if match:
            self.current_instance_id = match.id
        self._sync_instance_state()

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
            config = self.configs.get(key)
            if config:
                status = "Loaded"
                updated = utils.format_datetime(config.stored_at)
                filename = config.source_filename or ""
            else:
                status = "Not loaded"
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

    # ------------------------------------------------------------------ Actions
    def _import_config(self) -> None:
        self._load_config(replace=False)

    def _replace_config(self) -> None:
        self._load_config(replace=True)

    def _load_config(self, *, replace: bool) -> None:
        if self.current_instance_id is None:
            messagebox.showinfo("Export Validator", "Select an instance first.", parent=self)
            return
        item_type = self.selected_item_type
        if not item_type:
            messagebox.showinfo("Export Validator", "Select an item type first.", parent=self)
            return
        existing = self.configs.get(item_type)
        if existing and not replace:
            messagebox.showinfo(
                "Export Validator",
                "A configuration already exists for this item type. Use Replace to overwrite it.",
                parent=self,
            )
            return
        if replace and existing:
            confirm = messagebox.askyesno(
                "Replace Configuration",
                "Replace the current configuration file for this item type?",
                parent=self,
            )
            if not confirm:
                return
        path = filedialog.askopenfilename(
            parent=self,
            title="Select XML Configuration",
            filetypes=[("XML files", "*.xml"), ("All files", "*.*")],
        )
        if not path:
            return
        xml_text = self._read_xml_file(path)
        if xml_text is None:
            return
        if not self._parse_xml(xml_text):
            return
        try:
            self.db.upsert_export_validator_config(
                instance_id=self.current_instance_id,
                item_type=item_type,
                source_filename=Path(path).name,
                xml_content=xml_text,
            )
        except Exception as exc:
            messagebox.showerror("Export Validator", f"Could not save configuration: {exc}", parent=self)
            return
        self._sync_instance_state()
        messagebox.showinfo("Export Validator", "Configuration saved.", parent=self)

    def _validate_export(self) -> None:
        if self.current_instance_id is None:
            messagebox.showinfo("Export Validator", "Select an instance first.", parent=self)
            return
        item_type = self.selected_item_type
        if not item_type:
            messagebox.showinfo("Export Validator", "Select an item type first.", parent=self)
            return
        config = self.configs.get(item_type)
        if not config:
            messagebox.showinfo(
                "Export Validator",
                "Load a configuration for this item type before validating.",
                parent=self,
            )
            return
        path = filedialog.askopenfilename(
            parent=self,
            title="Select XML Export to Validate",
            filetypes=[("XML files", "*.xml"), ("All files", "*.*")],
        )
        if not path:
            return
        candidate_xml = self._read_xml_file(path)
        if candidate_xml is None:
            return
        if not self._parse_xml(candidate_xml):
            return
        report = self._build_validation_report(config, Path(path).name, candidate_xml)
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
    def _read_xml_file(self, path: str) -> Optional[str]:
        try:
            return Path(path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                return Path(path).read_text(encoding="utf-16")
            except Exception as exc:
                messagebox.showerror("Export Validator", f"Could not read XML file: {exc}", parent=self)
                return None
        except Exception as exc:
            messagebox.showerror("Export Validator", f"Could not read XML file: {exc}", parent=self)
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
            xml_text = self._read_xml_file(str(path))
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
        candidate_xml: str,
    ) -> str:
        if config.item_type == "data_quality_tests":
            return self._build_data_quality_tests_report(config, candidate_filename, candidate_xml)
        if config.item_type == "agreement_choices":
            return self._build_agreement_choices_report(config, candidate_filename, candidate_xml)
        now = utils.format_datetime(datetime.now())
        config_updated = utils.format_datetime(config.stored_at)
        lines = [
            "Export Validator Report",
            f"Generated: {now}",
            f"Item Type: {self._label_for_item(config.item_type)}",
            f"Config File: {config.source_filename or 'Stored XML'}",
            f"Config Updated: {config_updated}",
            f"Candidate File: {candidate_filename}",
            "",
        ]
        try:
            config_summary = self._summarize_xml(config.xml_content)
            candidate_summary = self._summarize_xml(candidate_xml)
        except ET.ParseError as exc:
            lines.append(f"Validation failed: {exc}")
            return "\n".join(lines)

        all_paths = sorted(set(config_summary) | set(candidate_summary))
        added = []
        removed = []
        changed = []
        for path in all_paths:
            base_count = config_summary.get(path)
            cand_count = candidate_summary.get(path)
            if base_count is None:
                added.append((path, cand_count or 0))
            elif cand_count is None:
                removed.append((path, base_count))
            elif base_count != cand_count:
                changed.append((path, base_count, cand_count))

        if not added and not removed and not changed:
            lines.append("No structural differences detected (tag path counts match).")
        else:
            lines.append("Structural differences detected:")
            if added:
                lines.append("")
                lines.append("Added paths:")
                for path, count in added[:25]:
                    lines.append(f"  + {path} (count {count})")
                if len(added) > 25:
                    lines.append(f"  ...and {len(added) - 25} more")
            if removed:
                lines.append("")
                lines.append("Removed paths:")
                for path, count in removed[:25]:
                    lines.append(f"  - {path} (count {count})")
                if len(removed) > 25:
                    lines.append(f"  ...and {len(removed) - 25} more")
            if changed:
                lines.append("")
                lines.append("Count changes:")
                for path, base_count, cand_count in changed[:25]:
                    lines.append(f"  * {path} ({base_count} -> {cand_count})")
                if len(changed) > 25:
                    lines.append(f"  ...and {len(changed) - 25} more")

        lines.append("")
        lines.append(
            "Note: This is a structural comparison. Field-level validation will be added once field mappings are set."
        )
        return "\n".join(lines)

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
