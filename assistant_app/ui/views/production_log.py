from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
import threading
from typing import Optional

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from ...database import Database
from ...models import ProductionLogAutomation, ProductionLogClient, ProductionLogSheetConfig
from ...production_log_automation import (
    ProductionLogAutomationRunner,
    latest_scheduled_occurrence,
    normalize_scheduled_time,
)
from ...production_log_engine import (
    DateMatchedProductionLogUpdater,
    ImportResult,
    ProductionLogError,
    ProductionLogUpdater,
    SheetImportRule,
    read_csv_rows,
)
from ...theme import ThemePalette

try:  # Optional dependency for reading Excel files.
    from openpyxl import load_workbook  # type: ignore
    from openpyxl.utils import get_column_letter  # type: ignore
except Exception:  # pragma: no cover - handled at runtime
    load_workbook = None
    get_column_letter = None


FIELD_LABELS = {
    "run_date": "Run Date",
    "dry_file_name": "Dry - File Name",
    "live_file_name": "Live - File Name",
    "dry_dist_id": "Dry - Dist ID",
    "live_dist_id": "Live - Dist ID",
    "dry_run_counts": "Dry Run Counts",
    "live_run_counts": "Live Run Counts",
    "system_a_count_hyphen": "System-A Count",
    "system_a_pkg_code_hyphen": "System-A PKG Code",
    "lettershop_recd": "Lettershop REC'd",
    "comments": "Comments",
    "mass_cancel": "Mass Cancel",
    "coreqc_email": "CoreQC-email",
    "check_sl": "Check SL",
    "send_approval_engage": "Send Approval to Engage",
    "dry_report_name": "Dry - Report Name",
    "live_report_name": "Live - Report Name",
    "dry_vs_live_run_counts": "Dry Run Counts vs Live Run Counts",
    "dist_file_name": "Dist File Name",
    "dist_run_id": "Dist Run ID",
    "cpn330": "CPN330",
    "csv_counts": "CSV Counts",
    "smc_initialization": "SMC Initialization",
    "email_mktg_srv": "Email Mktg Srv",
    "system_a_counts": "System A counts",
    "system_a_count_plain": "System A Count",
    "system_a_pkg_code_plain": "System A PKG Code",
    "send_approval_email": "Send Approval Email",
    "job_ran_successfully": "Job Ran Successfully",
    "counts": "Counts",
    "errors": "Errors",
    "in_archive": "In Archive",
    "dist_id": "Dist ID",
    "dist_file": "Dist File",
    "records_exported": "Records Exported",
    "output_recd": "Output REC'd",
    "in_cdsfiles": "In CDSFiles",
    "error_comments": "Error Comments",
    "select_set": "Select Set",
    "jobstream": "Jobstream",
}

SETUP_GUIDE_FILENAME = "production-log-setup-guide.md"

SHEET_TEMPLATES: list[tuple[str, str, list[str]]] = [
    (
        "custom_generic",
        "Custom / Generic",
        [],
    ),
    (
        "single_bills_mailed",
        "Single Bills - Mailed",
        [
            "dry_file_name",
            "live_file_name",
            "dry_dist_id",
            "live_dist_id",
            "dry_run_counts",
            "live_run_counts",
            "run_date",
            "system_a_count_hyphen",
            "system_a_pkg_code_hyphen",
            "lettershop_recd",
            "comments",
            "mass_cancel",
            "select_set",
            "jobstream",
        ],
    ),
    (
        "single_bills_emailed",
        "Single Bills - Emailed",
        [
            "dry_file_name",
            "live_file_name",
            "dry_dist_id",
            "live_dist_id",
            "dry_run_counts",
            "live_run_counts",
            "run_date",
            "coreqc_email",
            "check_sl",
            "send_approval_engage",
            "comments",
            "mass_cancel",
            "select_set",
            "jobstream",
        ],
    ),
    (
        "gift_bills_mailed",
        "Gift Bills - Mailed",
        [
            "dry_file_name",
            "live_file_name",
            "dry_dist_id",
            "live_dist_id",
            "dry_run_counts",
            "live_run_counts",
            "run_date",
            "system_a_count_hyphen",
            "system_a_pkg_code_hyphen",
            "lettershop_recd",
            "comments",
            "mass_cancel",
            "select_set",
            "jobstream",
        ],
    ),
    (
        "gift_bills_emailed",
        "Gift Bills - Emailed",
        [
            "dry_file_name",
            "live_file_name",
            "dry_dist_id",
            "live_dist_id",
            "dry_run_counts",
            "live_run_counts",
            "run_date",
            "coreqc_email",
            "check_sl",
            "send_approval_engage",
            "comments",
            "mass_cancel",
            "select_set",
            "jobstream",
        ],
    ),
    (
        "single_renewals_mailed",
        "Single Renewals - Mailed",
        [
            "dry_file_name",
            "live_file_name",
            "dry_dist_id",
            "live_dist_id",
            "dry_run_counts",
            "live_run_counts",
            "run_date",
            "system_a_count_hyphen",
            "system_a_pkg_code_hyphen",
            "lettershop_recd",
            "comments",
            "select_set",
            "jobstream",
        ],
    ),
    (
        "single_renewals_emailed",
        "Single Renewals - Emailed",
        [
            "dry_file_name",
            "live_file_name",
            "dry_dist_id",
            "live_dist_id",
            "dry_run_counts",
            "live_run_counts",
            "run_date",
            "coreqc_email",
            "check_sl",
            "send_approval_engage",
            "comments",
            "select_set",
            "jobstream",
        ],
    ),
    (
        "single_unren_renewals_mailed",
        "Single UnRen Renewals - Mailed",
        [
            "dry_file_name",
            "live_file_name",
            "dry_dist_id",
            "live_dist_id",
            "dry_run_counts",
            "live_run_counts",
            "run_date",
            "system_a_count_hyphen",
            "system_a_pkg_code_hyphen",
            "lettershop_recd",
            "comments",
            "select_set",
            "jobstream",
        ],
    ),
    (
        "single_unren_renewals_emailed",
        "Single UnRen Renewals - Emailed",
        [
            "dry_file_name",
            "live_file_name",
            "dry_dist_id",
            "live_dist_id",
            "dry_run_counts",
            "live_run_counts",
            "run_date",
            "coreqc_email",
            "check_sl",
            "send_approval_engage",
            "comments",
            "select_set",
            "jobstream",
        ],
    ),
    (
        "auto_renewal_links_mailed",
        "Auto Renewal Links - Mailed",
        [
            "dry_file_name",
            "live_file_name",
            "dry_dist_id",
            "live_dist_id",
            "dry_run_counts",
            "live_run_counts",
            "run_date",
            "system_a_count_hyphen",
            "system_a_pkg_code_hyphen",
            "lettershop_recd",
            "comments",
            "select_set",
            "jobstream",
        ],
    ),
    (
        "auto_renewal_links_emailed",
        "Auto Renewal Links - Emailed",
        [
            "dry_file_name",
            "live_file_name",
            "dry_dist_id",
            "live_dist_id",
            "dry_run_counts",
            "live_run_counts",
            "run_date",
            "coreqc_email",
            "check_sl",
            "send_approval_engage",
            "comments",
            "select_set",
            "jobstream",
        ],
    ),
    (
        "auto_renewal_apply",
        "Auto Renewal Apply",
        [
            "dry_run_counts",
            "live_run_counts",
            "dry_dist_id",
            "live_dist_id",
            "dry_report_name",
            "live_report_name",
            "comments",
            "dry_vs_live_run_counts",
            "select_set",
            "jobstream",
        ],
    ),
    (
        "gift_renewal_mailed",
        "Gift Renewal - Mailed",
        [
            "dry_file_name",
            "live_file_name",
            "dry_dist_id",
            "live_dist_id",
            "dry_run_counts",
            "live_run_counts",
            "run_date",
            "system_a_count_hyphen",
            "system_a_pkg_code_hyphen",
            "lettershop_recd",
            "comments",
            "select_set",
            "jobstream",
        ],
    ),
    (
        "gift_renewal_emailed",
        "Gift Renewal - Emailed",
        [
            "dry_file_name",
            "live_file_name",
            "dry_dist_id",
            "live_dist_id",
            "dry_run_counts",
            "live_run_counts",
            "run_date",
            "coreqc_email",
            "check_sl",
            "send_approval_engage",
            "comments",
            "select_set",
            "jobstream",
        ],
    ),
    (
        "gift_unren_renewal_mailed",
        "Gift UnRen Renewal - Mailed",
        [
            "dry_file_name",
            "live_file_name",
            "dry_dist_id",
            "live_dist_id",
            "dry_run_counts",
            "live_run_counts",
            "run_date",
            "system_a_count_hyphen",
            "system_a_pkg_code_hyphen",
            "lettershop_recd",
            "comments",
            "select_set",
            "jobstream",
        ],
    ),
    (
        "gift_unren_renewal_emailed",
        "Gift UnRen Renewal - Emailed",
        [
            "dry_file_name",
            "live_file_name",
            "dry_dist_id",
            "live_dist_id",
            "dry_run_counts",
            "live_run_counts",
            "run_date",
            "coreqc_email",
            "check_sl",
            "send_approval_engage",
            "comments",
            "select_set",
            "jobstream",
        ],
    ),
    (
        "ancillary_acks",
        "Ancillary Acks",
        [
            "dist_file_name",
            "dist_run_id",
            "run_date",
            "cpn330",
            "csv_counts",
            "smc_initialization",
            "email_mktg_srv",
            "system_a_counts",
            "lettershop_recd",
            "comments",
            "select_set",
            "jobstream",
        ],
    ),
    (
        "cold_donor_mailed",
        "Cold Donor - Mailed",
        [
            "dist_file_name",
            "dist_run_id",
            "run_date",
            "system_a_count_plain",
            "system_a_pkg_code_plain",
            "lettershop_recd",
            "comments",
        ],
    ),
    (
        "cold_donor_emailed",
        "Cold Donor - Emailed",
        [
            "dist_file_name",
            "dist_run_id",
            "run_date",
            "coreqc_email",
            "check_sl",
            "send_approval_email",
            "comments",
            "select_set",
            "jobstream",
        ],
    ),
    (
        "recurring_donations",
        "Recurring Donations",
        [
            "run_date",
            "job_ran_successfully",
            "counts",
            "errors",
            "in_archive",
            "comments",
            "select_set",
            "jobstream",
        ],
    ),
    (
        "premium_ship",
        "Premium Ship",
        [
            "run_date",
            "in_archive",
            "dist_id",
            "job_ran_successfully",
            "counts",
            "errors",
            "comments",
            "select_set",
            "jobstream",
        ],
    ),
    (
        "single_copy_ship",
        "Single Copy Ship",
        [
            "run_date",
            "in_archive",
            "dist_id",
            "job_ran_successfully",
            "counts",
            "errors",
            "comments",
            "select_set",
            "jobstream",
        ],
    ),
    (
        "cycle_end",
        "Cycle End",
        [
            "run_date",
            "dist_file",
            "dist_run_id",
            "records_exported",
            "output_recd",
            "system_a_count_plain",
            "system_a_pkg_code_plain",
            "comments",
            "select_set",
            "jobstream",
        ],
    ),
    (
        "eod",
        "EOD",
        [
            "run_date",
            "job_ran_successfully",
            "errors",
            "in_archive",
            "in_cdsfiles",
            "error_comments",
            "comments",
            "select_set",
            "jobstream",
        ],
    ),
]

TEMPLATE_LABELS = {key: label for key, label, _fields in SHEET_TEMPLATES}
TEMPLATE_FIELDS = {key: fields for key, _label, fields in SHEET_TEMPLATES}
TEMPLATE_KEY_BY_LABEL = {label: key for key, label, _fields in SHEET_TEMPLATES}
TEMPLATE_PLACEHOLDER = "Select sheet type..."
CUSTOM_TEMPLATE_KEY = "custom_generic"


class SheetMappingManager(tk.Toplevel):
    """Manage routing and arbitrary CSV-to-workbook mappings by worksheet."""

    def __init__(self, owner: "ProductionLogView") -> None:
        super().__init__(owner)
        self.owner = owner
        self.db = owner.db
        self.client_id = owner.current_client_id
        self.title("Sheet Routing & Column Mappings")
        self.geometry("1180x760")
        self.minsize(960, 620)
        self.transient(owner.winfo_toplevel())
        self.configure(background=owner.theme.window_bg)
        self.sheet_name_var = tk.StringVar(value="")
        self.header_row_var = tk.StringVar(value="5")
        self.data_start_row_var = tk.StringVar(value="6")
        self.routes_var = tk.StringVar(value="")
        self.mapping_rows: list[dict[str, object]] = []
        self.destination_choices: list[str] = []
        self.configs: dict[str, ProductionLogSheetConfig] = {}
        self._build()
        self._reload_configs()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=14, style="ProdLog.Root.TFrame")
        outer.pack(fill=tk.BOTH, expand=True)
        ttk.Label(outer, text="Sheet Routing & Column Mappings", style="ProdLog.Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text=(
                "Select a worksheet, assign the CSV route values that send rows to it, and add every "
                "CSV-header → workbook-column mapping required by that sheet. Each worksheet is independent."
            ),
            style="ProdLog.BodyMuted.TLabel",
            wraplength=1080,
            justify="left",
        ).pack(anchor="w", pady=(4, 10))

        split = ttk.Panedwindow(outer, orient=tk.HORIZONTAL)
        split.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(split, padding=(0, 0, 10, 0), style="ProdLog.Root.TFrame")
        right = ttk.Frame(split, padding=(10, 0, 0, 0), style="ProdLog.Root.TFrame")
        split.add(left, weight=1)
        split.add(right, weight=2)

        ttk.Label(left, text="Workbook sheets", style="ProdLog.Section.TLabel").pack(anchor="w")
        self.sheet_tree = ttk.Treeview(
            left,
            columns=("status", "routes", "mappings", "rows"),
            show="tree headings",
            height=24,
            style="ProdLog.Treeview",
        )
        self.sheet_tree.heading("#0", text="Sheet")
        self.sheet_tree.heading("status", text="Status")
        self.sheet_tree.heading("routes", text="Routes")
        self.sheet_tree.heading("mappings", text="Maps")
        self.sheet_tree.heading("rows", text="Rows")
        self.sheet_tree.column("#0", width=210, stretch=True)
        self.sheet_tree.column("status", width=85, anchor="center")
        self.sheet_tree.column("routes", width=55, anchor="center")
        self.sheet_tree.column("mappings", width=55, anchor="center")
        self.sheet_tree.column("rows", width=70, anchor="center")
        self.sheet_tree.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self.sheet_tree.bind("<<TreeviewSelect>>", self._on_sheet_selected)

        editor = ttk.Frame(right, style="ProdLog.Card.TFrame", padding=12)
        editor.pack(fill=tk.BOTH, expand=True)
        ttk.Label(editor, textvariable=self.sheet_name_var, style="ProdLog.Section.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w"
        )
        ttk.Label(editor, text="Header row", style="ProdLog.Card.TLabel").grid(
            row=1, column=0, sticky="w", pady=(8, 2)
        )
        ttk.Entry(editor, textvariable=self.header_row_var, width=7).grid(row=1, column=1, sticky="w", pady=(8, 2))
        ttk.Label(editor, text="Data starts row", style="ProdLog.Card.TLabel").grid(
            row=1, column=2, sticky="w", padx=(18, 6), pady=(8, 2)
        )
        ttk.Entry(editor, textvariable=self.data_start_row_var, width=7).grid(row=1, column=3, sticky="w", pady=(8, 2))

        ttk.Label(editor, text="CSV route values", style="ProdLog.Card.TLabel").grid(
            row=2, column=0, sticky="nw", pady=(8, 2)
        )
        ttk.Entry(editor, textvariable=self.routes_var).grid(
            row=2, column=1, columnspan=3, sticky="ew", pady=(8, 2)
        )
        ttk.Label(
            editor,
            text="Separate multiple values with commas or semicolons. Blank uses the worksheet name.",
            style="ProdLog.BodyMuted.TLabel",
        ).grid(row=3, column=1, columnspan=3, sticky="w")

        header = ttk.Frame(editor, style="ProdLog.Card.TFrame")
        header.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(12, 4))
        header.columnconfigure(0, weight=1)
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="CSV header", style="ProdLog.Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Workbook destination column", style="ProdLog.Card.TLabel").grid(
            row=0, column=1, sticky="w", padx=(10, 0)
        )

        mapping_holder = ttk.Frame(editor, style="ProdLog.Card.TFrame")
        mapping_holder.grid(row=5, column=0, columnspan=4, sticky="nsew")
        mapping_holder.rowconfigure(0, weight=1)
        mapping_holder.columnconfigure(0, weight=1)
        self.mapping_canvas = tk.Canvas(
            mapping_holder,
            highlightthickness=0,
            height=360,
            bg=self.owner.theme.card_bg,
        )
        mapping_scroll = ttk.Scrollbar(mapping_holder, orient=tk.VERTICAL, command=self.mapping_canvas.yview)
        self.mapping_canvas.configure(yscrollcommand=mapping_scroll.set)
        self.mapping_canvas.grid(row=0, column=0, sticky="nsew")
        mapping_scroll.grid(row=0, column=1, sticky="ns")
        self.mapping_frame = ttk.Frame(self.mapping_canvas, style="ProdLog.Card.TFrame")
        self.mapping_window = self.mapping_canvas.create_window((0, 0), window=self.mapping_frame, anchor="nw")
        self.mapping_frame.bind(
            "<Configure>",
            lambda _event: self.mapping_canvas.configure(scrollregion=self.mapping_canvas.bbox("all")),
        )
        self.mapping_canvas.bind(
            "<Configure>",
            lambda event: self.mapping_canvas.itemconfigure(self.mapping_window, width=event.width),
        )

        actions = ttk.Frame(editor, style="ProdLog.Card.TFrame")
        actions.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="Add Mapping Row", command=self._add_mapping_row).pack(side=tk.LEFT)
        ttk.Button(actions, text="Load Sample CSV...", command=self._load_sample_csv).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(actions, text="Save Selected Sheet", command=self._save_selected_sheet).pack(side=tk.RIGHT)
        editor.columnconfigure(1, weight=1)
        editor.columnconfigure(3, weight=1)
        editor.rowconfigure(5, weight=1)

    def _sheet_names(self) -> list[str]:
        values = [str(item) for item in self.owner.sheet_combo.cget("values")]
        return values

    def _reload_configs(self, select_sheet: str = "") -> None:
        if self.client_id is None:
            return
        self.configs = {
            item.sheet_name: item for item in self.db.get_production_log_sheet_configs(self.client_id)
        }
        self.sheet_tree.delete(*self.sheet_tree.get_children())
        for sheet_name in self._sheet_names():
            config = self.configs.get(sheet_name)
            mapping_count = 0
            route_count = 0
            rows = "—"
            status = "Not set"
            if config is not None:
                mapping_count = sum(
                    1
                    for key, destination in config.column_mappings.items()
                    if destination and config.source_mappings.get(key)
                )
                route_count = len(config.route_values) or 1
                rows = f"{config.header_row}/{config.data_start_row}"
                status = "Ready" if mapping_count else "Incomplete"
            self.sheet_tree.insert(
                "",
                tk.END,
                iid=sheet_name,
                text=sheet_name,
                values=(status, route_count, mapping_count, rows),
            )
        target = select_sheet or self.owner.sheet_var.get().strip()
        if target and self.sheet_tree.exists(target):
            self.sheet_tree.selection_set(target)
            self.sheet_tree.see(target)
            self._load_sheet(target)
        elif self.sheet_tree.get_children():
            first = str(self.sheet_tree.get_children()[0])
            self.sheet_tree.selection_set(first)
            self._load_sheet(first)

    def _on_sheet_selected(self, _event: object | None = None) -> None:
        selected = self.sheet_tree.selection()
        if selected:
            self._load_sheet(str(selected[0]))

    def _load_sheet(self, sheet_name: str) -> None:
        self.sheet_name_var.set(sheet_name)
        config = self.configs.get(sheet_name)
        self.header_row_var.set(str(config.header_row if config else 5))
        self.data_start_row_var.set(str(config.data_start_row if config else 6))
        self.routes_var.set(", ".join(config.route_values) if config else "")
        self._load_destination_choices(sheet_name)
        self._clear_mapping_rows()
        if config is not None:
            for key, destination in config.column_mappings.items():
                source = config.source_mappings.get(key, "").strip()
                if source and destination:
                    self._add_mapping_row(source, destination)
        if not self.mapping_rows:
            self._add_mapping_row()

    def _load_destination_choices(self, sheet_name: str) -> None:
        self.destination_choices = []
        path = Path(self.owner.workbook_var.get().strip())
        if load_workbook is None or not path.exists():
            return
        try:
            header_row = max(1, int(self.header_row_var.get()))
            workbook = load_workbook(path, read_only=True, data_only=True)
            try:
                if sheet_name not in workbook.sheetnames:
                    return
                sheet = workbook[sheet_name]
                values = next(
                    sheet.iter_rows(min_row=header_row, max_row=header_row, values_only=True),
                    (),
                )
                max_column = max(int(sheet.max_column), len(values), 1)
                for index in range(1, max_column + 1):
                    letter = get_column_letter(index) if get_column_letter else str(index)
                    header = str(values[index - 1]).strip() if index <= len(values) and values[index - 1] else ""
                    self.destination_choices.append(f"{letter} - {header}" if header else letter)
            finally:
                workbook.close()
        except Exception:
            self.destination_choices = []

    def _clear_mapping_rows(self) -> None:
        for child in self.mapping_frame.winfo_children():
            child.destroy()
        self.mapping_rows = []

    def _add_mapping_row(self, source: str = "", destination: str = "") -> None:
        row_index = len(self.mapping_rows)
        source_var = tk.StringVar(value=source)
        destination_var = tk.StringVar(value=self._display_destination(destination))
        row = ttk.Frame(self.mapping_frame, style="ProdLog.Card.TFrame")
        row.grid(row=row_index, column=0, sticky="ew", pady=2)
        row.columnconfigure(0, weight=1)
        row.columnconfigure(1, weight=1)
        source_combo = ttk.Combobox(
            row,
            textvariable=source_var,
            state="normal",
            values=self.owner._csv_headers,
        )
        source_combo.grid(row=0, column=0, sticky="ew")
        destination_combo = ttk.Combobox(
            row,
            textvariable=destination_var,
            state="normal",
            values=self.destination_choices,
        )
        destination_combo.grid(row=0, column=1, sticky="ew", padx=(10, 6))
        entry: dict[str, object] = {
            "frame": row,
            "source": source_var,
            "destination": destination_var,
        }
        ttk.Button(row, text="Remove", command=lambda item=entry: self._remove_mapping_row(item)).grid(
            row=0, column=2
        )
        self.mapping_rows.append(entry)

    def _remove_mapping_row(self, entry: dict[str, object]) -> None:
        frame = entry["frame"]
        if isinstance(frame, ttk.Frame):
            frame.destroy()
        if entry in self.mapping_rows:
            self.mapping_rows.remove(entry)
        for index, item in enumerate(self.mapping_rows):
            item_frame = item["frame"]
            if isinstance(item_frame, ttk.Frame):
                item_frame.grid_configure(row=index)
        if not self.mapping_rows:
            self._add_mapping_row()

    def _display_destination(self, column: str) -> str:
        target = str(column or "").strip().upper()
        for display in self.destination_choices:
            if display.split(" - ", 1)[0].upper() == target:
                return display
        return target

    @staticmethod
    def _destination_column(value: str) -> str:
        return str(value or "").split(" - ", 1)[0].strip().upper()

    def _load_sample_csv(self) -> None:
        self.owner._choose_sample_csv()
        headers = tuple(self.owner._csv_headers)
        for item in self.mapping_rows:
            frame = item["frame"]
            if not isinstance(frame, ttk.Frame):
                continue
            for child in frame.winfo_children():
                if isinstance(child, ttk.Combobox) and str(child.cget("textvariable")) == str(item["source"]):
                    child.configure(values=headers)

    def _parse_rows(self) -> tuple[int, int]:
        try:
            header_row = int(self.header_row_var.get())
            data_start_row = int(self.data_start_row_var.get())
        except ValueError as exc:
            raise ValueError("Header row and data-start row must be whole numbers.") from exc
        if header_row < 1 or data_start_row <= header_row:
            raise ValueError("Data-start row must be after a positive header row.")
        return header_row, data_start_row

    def _save_selected_sheet(self) -> None:
        if self.client_id is None:
            return
        sheet_name = self.sheet_name_var.get().strip()
        if not sheet_name:
            messagebox.showinfo("Sheet Mapping", "Select a worksheet first.", parent=self)
            return
        try:
            header_row, data_start_row = self._parse_rows()
        except ValueError as exc:
            messagebox.showerror("Sheet Mapping", str(exc), parent=self)
            return
        pairs: list[tuple[str, str]] = []
        for item in self.mapping_rows:
            source_var = item["source"]
            destination_var = item["destination"]
            source = source_var.get().strip() if isinstance(source_var, tk.StringVar) else ""
            destination = (
                self._destination_column(destination_var.get())
                if isinstance(destination_var, tk.StringVar)
                else ""
            )
            if not source and not destination:
                continue
            if not source or not destination:
                messagebox.showerror(
                    "Sheet Mapping",
                    "Every mapping row must contain both a CSV header and a destination column.",
                    parent=self,
                )
                return
            pairs.append((source, destination))
        if not pairs:
            messagebox.showerror("Sheet Mapping", "Add at least one complete mapping row.", parent=self)
            return
        destinations = [destination for _source, destination in pairs]
        duplicate_destinations = sorted({item for item in destinations if destinations.count(item) > 1})
        if duplicate_destinations:
            messagebox.showerror(
                "Sheet Mapping",
                "A destination column can be mapped only once: " + ", ".join(duplicate_destinations),
                parent=self,
            )
            return
        route_values = [
            item.strip() for item in re.split(r"[,;\n]+", self.routes_var.get()) if item.strip()
        ]
        if self._route_conflicts(sheet_name, route_values):
            return
        column_mappings = {f"mapping_{index:03d}": destination for index, (_source, destination) in enumerate(pairs, 1)}
        source_mappings = {f"mapping_{index:03d}": source for index, (source, _destination) in enumerate(pairs, 1)}
        self.db.upsert_production_log_sheet_config(
            client_id=self.client_id,
            sheet_name=sheet_name,
            template_key=CUSTOM_TEMPLATE_KEY,
            header_row=header_row,
            data_start_row=data_start_row,
            column_mappings=column_mappings,
            source_mappings=source_mappings,
            route_values=route_values,
        )
        self.owner.sheet_configs = {
            item.sheet_name: item for item in self.db.get_production_log_sheet_configs(self.client_id)
        }
        if self.owner.sheet_var.get().strip() == sheet_name:
            self.owner._on_sheet_selected()
        self._reload_configs(select_sheet=sheet_name)
        messagebox.showinfo("Sheet Mapping", f"Saved routing and mappings for '{sheet_name}'.", parent=self)

    def _route_conflicts(self, sheet_name: str, route_values: list[str]) -> bool:
        candidates = route_values or [sheet_name]
        normalized = {re.sub(r"[^a-z0-9]+", "", item.casefold()) for item in candidates}
        for other_name, config in self.configs.items():
            if other_name == sheet_name:
                continue
            other_values = config.route_values or [other_name]
            for value in other_values:
                if re.sub(r"[^a-z0-9]+", "", value.casefold()) in normalized:
                    messagebox.showerror(
                        "Sheet Mapping",
                        f"Route value '{value}' is already assigned to worksheet '{other_name}'.",
                        parent=self,
                    )
                    return True
        return False


class ProductionLogView(ttk.Frame):
    _HEADER_ROW = 5
    _DATA_START_ROW = 6
    _PREVIEW_ROWS = 10

    def __init__(self, master: tk.Misc, db: Database, theme: ThemePalette) -> None:
        super().__init__(master, padding=(16, 16))
        self.db = db
        self.theme = theme
        # Feature visibility is controlled by the Settings tab. Once enabled,
        # Production Log is immediately available and has no secondary PIN gate.
        self._locked = False
        self._accent_strip: Optional[tk.Frame] = None

        self.clients: list[ProductionLogClient] = []
        self.current_client_id: Optional[int] = None
        self.sheet_configs: dict[str, ProductionLogSheetConfig] = {}
        self._column_choice_map: dict[str, str] = {}
        self._last_column_choices: list[str] = []
        self._active_template_key: Optional[str] = None
        self._active_field_keys: list[str] = []
        self._sample_csv_path: Optional[Path] = None
        self._csv_headers: list[str] = []
        self._import_running = False
        self.automations: list[ProductionLogAutomation] = []
        self.current_automation_id: Optional[int] = None

        self.client_var = tk.StringVar(value="")
        self.workbook_var = tk.StringVar(value="")
        self.sheet_var = tk.StringVar(value="")
        self.template_var = tk.StringVar(value=TEMPLATE_PLACEHOLDER)
        self.status_var = tk.StringVar(value="Read-only preview mode.")
        self.email_folder_var = tk.StringVar(value="")
        self.email_subject_var = tk.StringVar(value="")
        self.email_subject_exact_var = tk.BooleanVar(value=False)
        self.email_sender_var = tk.StringVar(value="")
        self.email_body_var = tk.StringVar(value="")
        self.attachment_pattern_var = tk.StringVar(value="*.csv")
        self.required_category_var = tk.StringVar(value="")
        self.completed_category_var = tk.StringVar(value="")
        self.routing_column_var = tk.StringVar(value="")
        self.update_mode_var = tk.StringVar(value="append_empty")
        self.source_sort_column_var = tk.StringVar(value="")
        self.source_date_column_var = tk.StringVar(value="")
        self.target_date_column_var = tk.StringVar(value="A")
        self.route_values_var = tk.StringVar(value="")
        self.header_row_var = tk.StringVar(value=str(self._HEADER_ROW))
        self.data_start_row_var = tk.StringVar(value=str(self._DATA_START_ROW))
        self.preview_caption_var = tk.StringVar(value="")
        self.sample_csv_var = tk.StringVar(value="No sample CSV selected.")
        self.import_status_var = tk.StringVar(value="Configure an email source or load a sample CSV.")
        self.automation_var = tk.StringVar(value="")
        self.automation_name_var = tk.StringVar(value="")
        self.outlook_source_var = tk.StringVar(value="auto")
        self.scheduled_time_var = tk.StringVar(value="08:00")
        self.lookback_days_var = tk.StringVar(value="1")
        self.retry_minutes_var = tk.StringVar(value="15")
        self.catch_up_var = tk.BooleanVar(value=True)
        self.paused_var = tk.BooleanVar(value=False)
        self.weekday_vars = [tk.BooleanVar(value=True) for _ in range(7)]

        self._field_vars: dict[str, tk.StringVar] = {
            key: tk.StringVar(value="") for key in FIELD_LABELS
        }
        self._mapping_inputs: dict[str, ttk.Combobox] = {}
        self._source_mapping_inputs: dict[str, ttk.Combobox] = {}
        self._source_field_vars: dict[str, tk.StringVar] = {
            key: tk.StringVar(value="") for key in FIELD_LABELS
        }
        self._mapping_field_frame: Optional[ttk.Frame] = None

        self._configure_styles()
        self.configure(style="ProdLog.Root.TFrame")
        self._build_ui()
        self._load_clients()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        hero = ttk.Frame(self, style="ProdLog.Hero.TFrame", padding=(16, 12))
        hero.pack(fill=tk.X)
        accent = tk.Frame(hero, width=4, bg=self.theme.accent)
        accent.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))
        self._accent_strip = accent
        hero_text = ttk.Frame(hero, style="ProdLog.Hero.TFrame")
        hero_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Label(hero_text, text="Production Log", style="ProdLog.Title.TLabel").pack(anchor="w")
        ttk.Label(
            hero_text,
            text=(
                "Route CSV rows from Outlook into configured worksheets without overwriting existing cells. "
                "Formatting is retained and successful updates are saved automatically."
            ),
            style="ProdLog.Body.TLabel",
            wraplength=860,
            justify="left",
        ).pack(anchor="w", pady=(6, 0))
        ttk.Label(hero, textvariable=self.status_var, style="ProdLog.Badge.TLabel").pack(
            side=tk.RIGHT, padx=(12, 0)
        )
        ttk.Button(hero, text="Setup Guide", command=self._open_setup_guide).pack(side=tk.RIGHT)

        body = ttk.Frame(self, style="ProdLog.Root.TFrame")
        body.pack(fill=tk.BOTH, expand=True, pady=(14, 0))

        paned = ttk.Panedwindow(body, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(paned, style="ProdLog.Root.TFrame")
        left.columnconfigure(0, weight=1)

        right = ttk.Frame(paned, style="ProdLog.Root.TFrame")
        right.columnconfigure(0, weight=1)

        paned.add(left, weight=1)
        paned.add(right, weight=2)

        client_card = ttk.Frame(left, style="ProdLog.Card.TFrame", padding=(16, 14))
        client_card.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(client_card, text="Client Setup", style="ProdLog.Section.TLabel").pack(anchor="w")

        client_row = ttk.Frame(client_card, style="ProdLog.Card.TFrame")
        client_row.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(client_row, text="Client", style="ProdLog.Card.TLabel").pack(side=tk.LEFT)
        self.client_combo = ttk.Combobox(client_row, textvariable=self.client_var, state="readonly", width=28)
        self.client_combo.pack(side=tk.LEFT, padx=(8, 6))
        self.client_combo.bind("<<ComboboxSelected>>", self._on_client_selected)
        ttk.Button(client_row, text="New...", command=self._new_client).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(client_row, text="Rename...", command=self._rename_client).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(client_row, text="Delete", command=self._delete_client).pack(side=tk.LEFT)

        workbook_row = ttk.Frame(client_card, style="ProdLog.Card.TFrame")
        workbook_row.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(workbook_row, text="Workbook", style="ProdLog.Card.TLabel").pack(side=tk.LEFT)
        self.workbook_entry = ttk.Entry(
            workbook_row,
            textvariable=self.workbook_var,
            state="readonly",
            width=54,
        )
        self.workbook_entry.pack(side=tk.LEFT, padx=(8, 6), fill=tk.X, expand=True)
        ttk.Button(workbook_row, text="Select Workbook...", command=self._choose_workbook).pack(side=tk.LEFT)

        sheet_row = ttk.Frame(client_card, style="ProdLog.Card.TFrame")
        sheet_row.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(sheet_row, text="Sheet", style="ProdLog.Card.TLabel").pack(side=tk.LEFT)
        self.sheet_combo = ttk.Combobox(sheet_row, textvariable=self.sheet_var, state="readonly", width=24)
        self.sheet_combo.pack(side=tk.LEFT, padx=(8, 6))
        self.sheet_combo.bind("<<ComboboxSelected>>", self._on_sheet_selected)
        ttk.Button(sheet_row, text="Refresh Sheets", command=self._load_workbook_sheets).pack(side=tk.LEFT)

        mapping_card = ttk.Frame(left, style="ProdLog.Card.TFrame", padding=(16, 14))
        mapping_card.pack(fill=tk.BOTH, expand=True)
        mapping_card.columnconfigure(1, weight=1)
        mapping_card.columnconfigure(2, weight=1)
        ttk.Label(
            mapping_card,
            text="Column Mapping (configured separately for each worksheet)",
            style="ProdLog.Section.TLabel",
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(
            mapping_card,
            text="For each field, select its CSV source header and spreadsheet destination column.",
            style="ProdLog.BodyMuted.TLabel",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 8))

        layout_row = ttk.Frame(mapping_card, style="ProdLog.Card.TFrame")
        layout_row.grid(row=2, column=0, columnspan=3, sticky="ew", pady=2)
        ttk.Label(layout_row, text="Header row", style="ProdLog.Card.TLabel").pack(side=tk.LEFT)
        ttk.Entry(layout_row, textvariable=self.header_row_var, width=5).pack(side=tk.LEFT, padx=(6, 12))
        ttk.Label(layout_row, text="Data starts row", style="ProdLog.Card.TLabel").pack(side=tk.LEFT)
        ttk.Entry(layout_row, textvariable=self.data_start_row_var, width=5).pack(side=tk.LEFT, padx=(6, 12))
        ttk.Button(layout_row, text="Reload Columns", command=self._reload_selected_sheet_layout).pack(side=tk.LEFT)

        ttk.Label(mapping_card, text="Sheet Type", style="ProdLog.Card.TLabel").grid(
            row=3, column=0, sticky="w", pady=2, padx=(0, 8)
        )
        self.template_combo = ttk.Combobox(
            mapping_card,
            textvariable=self.template_var,
            state="readonly",
            width=28,
        )
        self.template_combo["values"] = [TEMPLATE_PLACEHOLDER] + [
            label for _key, label, _fields in SHEET_TEMPLATES
        ]
        self.template_combo.grid(row=3, column=1, sticky="w", pady=2)
        self.template_combo.bind("<<ComboboxSelected>>", self._on_template_selected)

        self._mapping_field_frame = ttk.Frame(mapping_card, style="ProdLog.Card.TFrame")
        self._mapping_field_frame.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
        self._mapping_field_frame.columnconfigure(1, weight=1)
        self._mapping_field_frame.columnconfigure(2, weight=1)
        self._render_mapping_fields()

        ttk.Label(mapping_card, text="CSV route values", style="ProdLog.Card.TLabel").grid(
            row=5, column=0, sticky="w", pady=(8, 2), padx=(0, 8)
        )
        ttk.Entry(mapping_card, textvariable=self.route_values_var).grid(
            row=5, column=1, columnspan=2, sticky="ew", pady=(8, 2)
        )
        ttk.Label(
            mapping_card,
            text="Comma-separated values from the automation's routing CSV column; blank defaults to this sheet name.",
            style="ProdLog.BodyMuted.TLabel",
            wraplength=540,
            justify="left",
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(0, 2))

        button_row = ttk.Frame(mapping_card, style="ProdLog.Card.TFrame")
        button_row.grid(row=7, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(button_row, text="Clear Mapping", command=self._clear_mapping).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(button_row, text="Save Mapping", command=self._save_mapping).pack(side=tk.LEFT)

        right_pane = ttk.Panedwindow(right, orient=tk.VERTICAL)
        right_pane.pack(fill=tk.BOTH, expand=True)

        preview_card = ttk.Frame(right_pane, style="ProdLog.Card.TFrame", padding=(16, 14))
        preview_card.columnconfigure(0, weight=1)
        preview_card.rowconfigure(1, weight=1)
        ttk.Label(preview_card, text="Preview (read-only)", style="ProdLog.Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.preview_tree = ttk.Treeview(preview_card, show="headings", height=12, style="ProdLog.Treeview")
        self.preview_tree.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        scroll = ttk.Scrollbar(preview_card, orient=tk.VERTICAL, command=self.preview_tree.yview)
        scroll.grid(row=1, column=1, sticky="ns", pady=(8, 0))
        scroll_x = ttk.Scrollbar(preview_card, orient=tk.HORIZONTAL, command=self.preview_tree.xview)
        scroll_x.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.preview_tree.configure(yscrollcommand=scroll.set, xscrollcommand=scroll_x.set)
        ttk.Label(
            preview_card,
            textvariable=self.preview_caption_var,
            style="ProdLog.BodyMuted.TLabel",
        ).grid(row=3, column=0, sticky="w", pady=(8, 0))

        automation_card = ttk.Frame(right_pane, style="ProdLog.Card.TFrame", padding=(16, 14))
        automation_card.columnconfigure(1, weight=1)
        automation_card.columnconfigure(3, weight=1)
        ttk.Label(automation_card, text="Scheduled Automations", style="ProdLog.Section.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w"
        )
        ttk.Label(automation_card, text="Automation", style="ProdLog.Card.TLabel").grid(
            row=1, column=0, sticky="w", pady=(8, 2), padx=(0, 8)
        )
        self.automation_combo = ttk.Combobox(
            automation_card, textvariable=self.automation_var, state="readonly", width=28
        )
        self.automation_combo.grid(row=1, column=1, sticky="ew", pady=(8, 2))
        self.automation_combo.bind("<<ComboboxSelected>>", self._on_automation_selected)
        automation_actions = ttk.Frame(automation_card, style="ProdLog.Card.TFrame")
        automation_actions.grid(row=1, column=2, columnspan=2, sticky="w", padx=(8, 0), pady=(8, 2))
        ttk.Button(automation_actions, text="New...", command=self._new_automation).pack(side=tk.LEFT)
        ttk.Button(automation_actions, text="Delete", command=self._delete_automation).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Label(automation_card, text="Name", style="ProdLog.Card.TLabel").grid(
            row=2, column=0, sticky="w", pady=2, padx=(0, 8)
        )
        ttk.Entry(automation_card, textvariable=self.automation_name_var).grid(row=2, column=1, sticky="ew", pady=2)
        ttk.Checkbutton(automation_card, text="Paused", variable=self.paused_var).grid(
            row=2, column=2, sticky="w", padx=(12, 0), pady=2
        )
        ttk.Checkbutton(automation_card, text="Catch up after missed time", variable=self.catch_up_var).grid(
            row=2, column=3, sticky="w", pady=2
        )
        ttk.Label(automation_card, text="Outlook folder", style="ProdLog.Card.TLabel").grid(
            row=3, column=0, sticky="w", pady=2, padx=(0, 8)
        )
        ttk.Entry(automation_card, textvariable=self.email_folder_var).grid(row=3, column=1, sticky="ew", pady=2)
        ttk.Label(automation_card, text="Outlook access", style="ProdLog.Card.TLabel").grid(
            row=3, column=2, sticky="w", padx=(12, 8), pady=2
        )
        ttk.Combobox(
            automation_card,
            textvariable=self.outlook_source_var,
            state="readonly",
            values=("classic_outlook", "new_outlook", "auto"),
            width=24,
        ).grid(row=3, column=3, sticky="ew", pady=2)
        ttk.Label(automation_card, text="Email subject", style="ProdLog.Card.TLabel").grid(
            row=4, column=0, sticky="w", pady=2, padx=(0, 8)
        )
        ttk.Entry(automation_card, textvariable=self.email_subject_var).grid(row=4, column=1, sticky="ew", pady=2)
        subject_options = ttk.Frame(automation_card, style="ProdLog.Card.TFrame")
        subject_options.grid(row=4, column=2, sticky="w", padx=(12, 8), pady=2)
        ttk.Checkbutton(subject_options, text="Exact subject", variable=self.email_subject_exact_var).pack(side=tk.LEFT)
        ttk.Label(subject_options, text="Sender", style="ProdLog.Card.TLabel").pack(side=tk.LEFT, padx=(12, 0))
        ttk.Entry(automation_card, textvariable=self.email_sender_var, width=24).grid(row=4, column=3, sticky="ew", pady=2)
        ttk.Label(automation_card, text="Body contains", style="ProdLog.Card.TLabel").grid(
            row=5, column=0, sticky="w", pady=2, padx=(0, 8)
        )
        ttk.Entry(automation_card, textvariable=self.email_body_var).grid(row=5, column=1, sticky="ew", pady=2)
        ttk.Label(automation_card, text="Required category", style="ProdLog.Card.TLabel").grid(
            row=5, column=2, sticky="w", pady=2, padx=(12, 8)
        )
        ttk.Entry(automation_card, textvariable=self.required_category_var, width=24).grid(
            row=5, column=3, sticky="ew", pady=2
        )
        ttk.Label(automation_card, text="Attachment", style="ProdLog.Card.TLabel").grid(
            row=6, column=0, sticky="w", pady=2, padx=(0, 8)
        )
        ttk.Entry(automation_card, textvariable=self.attachment_pattern_var).grid(row=6, column=1, sticky="ew", pady=2)
        ttk.Label(automation_card, text="Completed category", style="ProdLog.Card.TLabel").grid(
            row=6, column=2, sticky="w", pady=2, padx=(12, 8)
        )
        ttk.Entry(automation_card, textvariable=self.completed_category_var, width=24).grid(
            row=6, column=3, sticky="ew", pady=2
        )
        ttk.Label(automation_card, text="Routing CSV column", style="ProdLog.Card.TLabel").grid(
            row=7, column=0, sticky="w", pady=2, padx=(0, 8)
        )
        self.routing_column_combo = ttk.Combobox(
            automation_card, textvariable=self.routing_column_var, state="normal"
        )
        self.routing_column_combo.grid(row=7, column=1, sticky="ew", pady=2)
        ttk.Label(automation_card, text="Update mode", style="ProdLog.Card.TLabel").grid(
            row=7, column=2, sticky="w", pady=2, padx=(12, 8)
        )
        ttk.Combobox(
            automation_card,
            textvariable=self.update_mode_var,
            state="readonly",
            values=("append_empty", "match_date"),
            width=24,
        ).grid(row=7, column=3, sticky="ew", pady=2)
        date_options = ttk.Frame(automation_card, style="ProdLog.Card.TFrame")
        date_options.grid(row=8, column=0, columnspan=4, sticky="ew", pady=2)
        ttk.Label(date_options, text="Sort CSV by", style="ProdLog.Card.TLabel").pack(side=tk.LEFT)
        ttk.Entry(date_options, textvariable=self.source_sort_column_var, width=22).pack(side=tk.LEFT, padx=(6, 12))
        ttk.Label(date_options, text="CSV date", style="ProdLog.Card.TLabel").pack(side=tk.LEFT)
        ttk.Entry(date_options, textvariable=self.source_date_column_var, width=22).pack(side=tk.LEFT, padx=(6, 12))
        ttk.Label(date_options, text="Workbook date column", style="ProdLog.Card.TLabel").pack(side=tk.LEFT)
        ttk.Entry(date_options, textvariable=self.target_date_column_var, width=5).pack(side=tk.LEFT, padx=(6, 0))
        schedule_frame = ttk.Frame(automation_card, style="ProdLog.Card.TFrame")
        schedule_frame.grid(row=9, column=0, columnspan=4, sticky="ew", pady=(5, 2))
        ttk.Label(schedule_frame, text="Time", style="ProdLog.Card.TLabel").pack(side=tk.LEFT)
        ttk.Entry(schedule_frame, textvariable=self.scheduled_time_var, width=7).pack(side=tk.LEFT, padx=(6, 14))
        ttk.Label(schedule_frame, text="Prior days to include", style="ProdLog.Card.TLabel").pack(side=tk.LEFT)
        ttk.Entry(schedule_frame, textvariable=self.lookback_days_var, width=5).pack(side=tk.LEFT, padx=(6, 14))
        ttk.Label(schedule_frame, text="Retry every (minutes)", style="ProdLog.Card.TLabel").pack(side=tk.LEFT)
        ttk.Entry(schedule_frame, textvariable=self.retry_minutes_var, width=5).pack(side=tk.LEFT, padx=(6, 0))
        days_frame = ttk.Frame(automation_card, style="ProdLog.Card.TFrame")
        days_frame.grid(row=10, column=0, columnspan=4, sticky="w", pady=2)
        ttk.Label(days_frame, text="Run on", style="ProdLog.Card.TLabel").pack(side=tk.LEFT, padx=(0, 8))
        for index, label in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
            ttk.Checkbutton(days_frame, text=label, variable=self.weekday_vars[index]).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Label(automation_card, textvariable=self.sample_csv_var, style="ProdLog.BodyMuted.TLabel").grid(
            row=11, column=0, columnspan=4, sticky="w", pady=(5, 2)
        )
        actions = ttk.Frame(automation_card, style="ProdLog.Card.TFrame")
        actions.grid(row=12, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        ttk.Button(
            actions,
            text="Configure Sheet Mappings...",
            command=self._open_sheet_mapping_manager,
        ).pack(side=tk.LEFT)
        ttk.Button(actions, text="Load Sample CSV...", command=self._choose_sample_csv).pack(side=tk.LEFT)
        ttk.Button(actions, text="Save Automation", command=self._save_automation).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(actions, text="Import Sample Now", command=self._import_sample).pack(side=tk.RIGHT)
        ttk.Button(actions, text="Run Automation Now", command=self._run_automation_now).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Label(automation_card, textvariable=self.import_status_var, style="ProdLog.BodyMuted.TLabel").grid(
            row=13, column=0, columnspan=4, sticky="w", pady=(8, 0)
        )
        right_pane.add(preview_card, weight=1)
        right_pane.add(automation_card, weight=1)

    def _open_setup_guide(self) -> None:
        candidates = []
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            candidates.append(Path(bundle_root) / "docs" / SETUP_GUIDE_FILENAME)
        candidates.append(Path(__file__).resolve().parents[3] / "docs" / SETUP_GUIDE_FILENAME)
        guide_path = next((path for path in candidates if path.exists()), None)
        if guide_path is None:
            messagebox.showerror(
                "Production Log Setup Guide",
                "The Production Log setup guide is missing from this installation.",
                parent=self,
            )
            return
        try:
            os.startfile(str(guide_path))  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror(
                "Production Log Setup Guide",
                f"Unable to open the setup guide:\n{exc}",
                parent=self,
            )

    def _open_sheet_mapping_manager(self) -> None:
        if self.current_client_id is None:
            messagebox.showinfo("Sheet Mappings", "Create or select a client first.", parent=self)
            return
        if not self.workbook_var.get().strip():
            messagebox.showinfo("Sheet Mappings", "Select the client's workbook first.", parent=self)
            return
        manager = SheetMappingManager(self)
        manager.lift()
        manager.focus_force()

    # ------------------------------------------------------------------ Client management
    def _load_clients(self) -> None:
        self.clients = self.db.get_production_log_clients()
        names = [client.name for client in self.clients]
        self.client_combo["values"] = names
        if self.current_client_id and any(c.id == self.current_client_id for c in self.clients):
            current = next(c for c in self.clients if c.id == self.current_client_id)
            self.client_combo.set(current.name)
        elif names:
            self.client_combo.current(0)
            self.current_client_id = self.clients[0].id
        else:
            self.client_combo.set("")
            self.current_client_id = None
        self._sync_client_state()

    def _sync_client_state(self) -> None:
        if self.current_client_id is None:
            self.workbook_var.set("")
            self.sheet_combo["values"] = []
            self.sheet_var.set("")
            self.sheet_configs = {}
            self._load_automations()
            self._update_mapping_inputs([])
            self._clear_preview()
            self.status_var.set("Select a client to begin.")
            return
        current = next((c for c in self.clients if c.id == self.current_client_id), None)
        if current:
            self.workbook_var.set(current.workbook_path or "")
        self._load_automations()
        self.sheet_configs = {
            cfg.sheet_name: cfg
            for cfg in self.db.get_production_log_sheet_configs(self.current_client_id)
        }
        self._refresh_status()
        self._load_workbook_sheets()

    def _on_client_selected(self, _event: object) -> None:
        name = self.client_var.get()
        match = next((c for c in self.clients if c.name == name), None)
        if match:
            self.current_client_id = match.id
        self._sync_client_state()

    def _new_client(self) -> None:
        name = simpledialog.askstring("New Client", "Client name:", parent=self)
        if not name:
            return
        try:
            new_id = self.db.create_production_log_client(name)
        except ValueError as exc:
            messagebox.showerror("Client", str(exc), parent=self)
            return
        self.current_client_id = new_id
        self._load_clients()

    def _rename_client(self) -> None:
        if self.current_client_id is None:
            return
        current = next((c for c in self.clients if c.id == self.current_client_id), None)
        if current is None:
            return
        name = simpledialog.askstring("Rename Client", "New name:", initialvalue=current.name, parent=self)
        if not name:
            return
        try:
            self.db.update_production_log_client(self.current_client_id, name)
        except ValueError as exc:
            messagebox.showerror("Client", str(exc), parent=self)
            return
        self._load_clients()

    def _delete_client(self) -> None:
        if self.current_client_id is None:
            return
        current = next((c for c in self.clients if c.id == self.current_client_id), None)
        if current is None:
            return
        confirm = messagebox.askyesno(
            "Delete Client",
            f"Delete client '{current.name}' and its mappings? This cannot be undone.",
            parent=self,
        )
        if not confirm:
            return
        self.db.delete_production_log_client(self.current_client_id)
        self.current_client_id = None
        self._load_clients()

    # ------------------------------------------------------------------ Workbook handling
    def _choose_workbook(self) -> None:
        if self.current_client_id is None:
            messagebox.showinfo("Workbook", "Create or select a client first.", parent=self)
            return
        path = filedialog.askopenfilename(
            parent=self,
            title="Select Production Log Workbook",
            filetypes=[("Excel files", "*.xlsx *.xlsm"), ("All files", "*.*")],
        )
        if not path:
            return
        self.db.update_production_log_client_workbook(self.current_client_id, path)
        self._load_clients()

    def _refresh_status(self) -> None:
        path_text = (self.workbook_var.get() or "").strip()
        if not path_text:
            self.status_var.set("Read-only preview mode.")
            return
        path = Path(path_text)
        if not path.exists():
            self.status_var.set("Workbook not found.")
            return
        lock_path = path.parent / f"~${path.name}"
        if lock_path.exists():
            self.status_var.set("In use by another user. Updates paused.")
        else:
            self.status_var.set("Workbook available.")

    def _load_workbook_sheets(self) -> None:
        self._refresh_status()
        path_text = (self.workbook_var.get() or "").strip()
        if not path_text or not Path(path_text).exists():
            self.sheet_combo["values"] = []
            self.sheet_var.set("")
            self._update_mapping_inputs([])
            self._clear_preview()
            return
        if load_workbook is None:
            self.status_var.set("Install openpyxl to load workbook sheets.")
            return
        try:
            workbook = load_workbook(path_text, read_only=True, data_only=True)
        except Exception as exc:
            self.status_var.set(f"Unable to read workbook: {exc}")
            return
        try:
            sheets = list(workbook.sheetnames)
        finally:
            workbook.close()
        self.sheet_combo["values"] = sheets
        current = self.sheet_var.get()
        if current not in sheets:
            if sheets:
                self.sheet_var.set(sheets[0])
            else:
                self.sheet_var.set("")
        self._on_sheet_selected()

    def _on_sheet_selected(self, _event: object | None = None) -> None:
        sheet_name = self.sheet_var.get().strip()
        if not sheet_name:
            self._update_mapping_inputs([])
            self._clear_preview()
            return
        self._load_sheet_row_settings(sheet_name)
        self._load_column_choices(sheet_name)
        self._load_sheet_mapping(sheet_name)
        self._load_sheet_preview(sheet_name)

    def _load_sheet_row_settings(self, sheet_name: str) -> None:
        config = self.sheet_configs.get(sheet_name)
        if config is None and self.current_client_id is not None:
            config = self.db.get_production_log_sheet_config(self.current_client_id, sheet_name)
        self.header_row_var.set(str(config.header_row if config else self._HEADER_ROW))
        self.data_start_row_var.set(str(config.data_start_row if config else self._DATA_START_ROW))

    def _sheet_row_numbers(self) -> tuple[int, int]:
        try:
            header_row = int(self.header_row_var.get())
            data_start_row = int(self.data_start_row_var.get())
        except ValueError as exc:
            raise ValueError("Header row and data-start row must be whole numbers.") from exc
        if header_row < 1:
            raise ValueError("Header row must be at least 1.")
        if data_start_row <= header_row:
            raise ValueError("Data-start row must be after the header row.")
        return header_row, data_start_row

    def _reload_selected_sheet_layout(self) -> None:
        sheet_name = self.sheet_var.get().strip()
        if not sheet_name:
            messagebox.showinfo("Column Mapping", "Select a worksheet first.", parent=self)
            return
        try:
            self._sheet_row_numbers()
        except ValueError as exc:
            messagebox.showerror("Column Mapping", str(exc), parent=self)
            return
        self._load_column_choices(sheet_name)
        if self._active_template_key == CUSTOM_TEMPLATE_KEY:
            self._set_active_template(CUSTOM_TEMPLATE_KEY)
        self._load_sheet_preview(sheet_name)

    def _load_column_choices(self, sheet_name: str) -> None:
        path_text = (self.workbook_var.get() or "").strip()
        self._column_choice_map = {}
        self._last_column_choices = []
        if not path_text or load_workbook is None:
            self._update_mapping_inputs([])
            return
        try:
            workbook = load_workbook(path_text, read_only=True, data_only=True)
        except Exception:
            self._update_mapping_inputs([])
            return
        try:
            if sheet_name not in workbook.sheetnames:
                self._update_mapping_inputs([])
                return
            sheet = workbook[sheet_name]
            try:
                header_row, data_start_row = self._sheet_row_numbers()
            except ValueError:
                header_row, data_start_row = self._HEADER_ROW, self._DATA_START_ROW
            header_rows = list(
                sheet.iter_rows(
                    min_row=header_row,
                    max_row=header_row,
                    values_only=True,
                )
            )
            header_values = list(header_rows[0]) if header_rows else []
            preview_rows = list(
                sheet.iter_rows(
                    min_row=data_start_row,
                    max_row=data_start_row + self._PREVIEW_ROWS - 1,
                    values_only=True,
                )
            )
            max_col = 0
            for idx, header in enumerate(header_values, start=1):
                if header not in (None, ""):
                    max_col = max(max_col, idx)
            for row in preview_rows:
                for idx, cell in enumerate(row, start=1):
                    if cell not in (None, ""):
                        max_col = max(max_col, idx)
            if max_col == 0:
                max_col = max(len(header_values), 1)
            choices = []
            for col_idx in range(1, max_col + 1):
                letter = get_column_letter(col_idx) if get_column_letter else str(col_idx)
                header_text = ""
                if col_idx - 1 < len(header_values):
                    header_text = str(header_values[col_idx - 1]).strip() if header_values[col_idx - 1] is not None else ""
                display = f"{letter} - {header_text}" if header_text else letter
                self._column_choice_map[display] = letter
                choices.append(display)
        finally:
            workbook.close()
        self._last_column_choices = choices
        self._update_mapping_inputs(choices)

    def _update_mapping_inputs(self, choices: list[str]) -> None:
        values = [""] + choices if choices else [""]
        for combo in self._mapping_inputs.values():
            combo.configure(values=values)
        source_values = [""] + self._csv_headers if self._csv_headers else [""]
        for combo in self._source_mapping_inputs.values():
            combo.configure(values=source_values)

    def _render_mapping_fields(self) -> None:
        if self._mapping_field_frame is None:
            return
        for child in self._mapping_field_frame.winfo_children():
            child.destroy()
        self._mapping_inputs = {}
        self._source_mapping_inputs = {}
        if not self._active_field_keys:
            ttk.Label(
                self._mapping_field_frame,
                text="Select a sheet type to see the available fields.",
                style="ProdLog.BodyMuted.TLabel",
            ).grid(row=0, column=0, sticky="w")
            return
        ttk.Label(self._mapping_field_frame, text="Field", style="ProdLog.BodyMuted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        ttk.Label(self._mapping_field_frame, text="CSV header", style="ProdLog.BodyMuted.TLabel").grid(
            row=0, column=1, sticky="w", padx=(0, 8)
        )
        ttk.Label(self._mapping_field_frame, text="Spreadsheet column", style="ProdLog.BodyMuted.TLabel").grid(
            row=0, column=2, sticky="w"
        )
        for row_idx, key in enumerate(self._active_field_keys, start=1):
            label = FIELD_LABELS.get(key, key)
            ttk.Label(self._mapping_field_frame, text=label, style="ProdLog.Card.TLabel").grid(
                row=row_idx, column=0, sticky="w", pady=2, padx=(0, 8)
            )
            combo = ttk.Combobox(
                self._mapping_field_frame,
                textvariable=self._source_field_vars[key],
                state="normal",
                width=26,
            )
            combo.grid(row=row_idx, column=1, sticky="w", pady=2)
            self._source_mapping_inputs[key] = combo
            destination_combo = ttk.Combobox(
                self._mapping_field_frame,
                textvariable=self._field_vars[key],
                state="readonly",
                width=26,
            )
            destination_combo.grid(row=row_idx, column=2, sticky="w", pady=2)
            self._mapping_inputs[key] = destination_combo
        self._update_mapping_inputs(self._last_column_choices)

    def _set_active_template(self, template_key: Optional[str]) -> None:
        self._active_template_key = template_key
        if template_key == CUSTOM_TEMPLATE_KEY:
            fields = []
            for display in self._last_column_choices:
                letter = self._column_choice_map.get(display, display).strip().upper()
                key = f"custom_{letter}"
                fields.append(key)
                FIELD_LABELS[key] = display
                if key not in self._field_vars:
                    self._field_vars[key] = tk.StringVar(value=self._display_for_column(letter))
                if key not in self._source_field_vars:
                    self._source_field_vars[key] = tk.StringVar(value="")
                self._field_vars[key].set(self._display_for_column(letter))
        else:
            fields = list(TEMPLATE_FIELDS.get(template_key or "", []))
        priority = ["run_date", "select_set", "jobstream"]
        ordered = [field for field in priority if field in fields]
        ordered.extend([field for field in fields if field not in ordered])
        self._active_field_keys = ordered
        self._render_mapping_fields()

    def _select_template(self, template_key: Optional[str]) -> None:
        if template_key and template_key in TEMPLATE_LABELS:
            self.template_var.set(TEMPLATE_LABELS[template_key])
        else:
            self.template_var.set(TEMPLATE_PLACEHOLDER)
        self._set_active_template(template_key)

    def _on_template_selected(self, _event: object | None = None) -> None:
        label = self.template_var.get().strip()
        template_key = TEMPLATE_KEY_BY_LABEL.get(label)
        self._set_active_template(template_key)

    def _load_sheet_mapping(self, sheet_name: str) -> None:
        for var in self._field_vars.values():
            var.set("")
        for var in self._source_field_vars.values():
            var.set("")
        self.route_values_var.set("")
        self._select_template(None)
        if self.current_client_id is None:
            return
        config = self.sheet_configs.get(sheet_name)
        if config is None:
            config = self.db.get_production_log_sheet_config(self.current_client_id, sheet_name)
        if config is None:
            return
        self.header_row_var.set(str(config.header_row))
        self.data_start_row_var.set(str(config.data_start_row))
        self._select_template(config.template_key)
        for key, column in config.column_mappings.items():
            display = self._display_for_column(column)
            if key in self._field_vars:
                self._field_vars[key].set(display)
        for key, source_column in config.source_mappings.items():
            if key in self._source_field_vars:
                self._source_field_vars[key].set(source_column)
        self.route_values_var.set(", ".join(config.route_values))

    def _display_for_column(self, column: str) -> str:
        column = (column or "").strip().upper()
        for display, letter in self._column_choice_map.items():
            if letter.upper() == column:
                return display
        return column

    def _clear_mapping(self) -> None:
        for var in self._field_vars.values():
            var.set("")
        for var in self._source_field_vars.values():
            var.set("")

    def _save_mapping(self) -> None:
        if self.current_client_id is None:
            messagebox.showinfo("Mapping", "Select a client first.", parent=self)
            return
        sheet_name = self.sheet_var.get().strip()
        if not sheet_name:
            messagebox.showinfo("Mapping", "Select a sheet first.", parent=self)
            return
        if not self._active_template_key:
            messagebox.showinfo("Mapping", "Select a sheet type first.", parent=self)
            return
        try:
            header_row, data_start_row = self._sheet_row_numbers()
        except ValueError as exc:
            messagebox.showerror("Mapping", str(exc), parent=self)
            return
        mapping: dict[str, str] = {}
        source_mapping: dict[str, str] = {}
        duplicates: dict[str, list[str]] = {}
        used: dict[str, str] = {}
        for key in self._active_field_keys:
            var = self._field_vars[key]
            raw = var.get().strip()
            if not raw:
                continue
            column = self._column_choice_map.get(raw, raw).strip().upper()
            if column in used:
                duplicates.setdefault(column, [used[column]]).append(FIELD_LABELS.get(key, key))
            else:
                used[column] = FIELD_LABELS.get(key, key)
            mapping[key] = column
            source = self._source_field_vars[key].get().strip()
            if source:
                source_mapping[key] = source
        if duplicates:
            details = []
            for column, fields in duplicates.items():
                fields_list = ", ".join(fields)
                details.append(f"{column}: {fields_list}")
            messagebox.showerror(
                "Mapping",
                "Each column can map to only one field.\n\nConflicts:\n" + "\n".join(details),
                parent=self,
            )
            return
        self.db.upsert_production_log_sheet_config(
            client_id=self.current_client_id,
            sheet_name=sheet_name,
            template_key=self._active_template_key,
            header_row=header_row,
            data_start_row=data_start_row,
            column_mappings=mapping,
            source_mappings=source_mapping,
            route_values=self._parse_route_values(),
        )
        self.sheet_configs[sheet_name] = self.db.get_production_log_sheet_config(
            self.current_client_id, sheet_name
        ) or self.sheet_configs.get(sheet_name)
        messagebox.showinfo("Mapping", "Column mapping saved.", parent=self)

    # ------------------------------------------------------------------ CSV and Outlook import
    def _load_automations(self) -> None:
        if self.current_client_id is None:
            self.automations = []
        else:
            self.automations = self.db.get_production_log_automations(self.current_client_id)
        self.automation_combo.configure(values=[item.name for item in self.automations])
        selected = next(
            (item for item in self.automations if item.id == self.current_automation_id),
            None,
        )
        if selected is None and self.automations:
            selected = self.automations[0]
        self.current_automation_id = selected.id if selected else None
        self.automation_var.set(selected.name if selected else "")
        self._load_automation_form(selected)

    def _load_automation_form(self, automation: Optional[ProductionLogAutomation]) -> None:
        self.automation_name_var.set(automation.name if automation else "")
        self.outlook_source_var.set(automation.outlook_source if automation else "auto")
        self.email_folder_var.set(automation.email_folder if automation else "")
        self.email_subject_var.set(automation.email_subject_contains if automation else "")
        self.email_subject_exact_var.set(automation.email_subject_exact if automation else False)
        self.email_sender_var.set(automation.email_sender_contains if automation else "")
        self.email_body_var.set(automation.email_body_contains if automation else "")
        self.required_category_var.set(automation.required_category if automation else "")
        self.completed_category_var.set(automation.completed_category if automation else "")
        self.attachment_pattern_var.set(automation.attachment_pattern if automation else "*.csv")
        self.routing_column_var.set(automation.routing_column if automation else "")
        self.update_mode_var.set(automation.update_mode if automation else "append_empty")
        self.source_sort_column_var.set(automation.source_sort_column if automation else "")
        self.source_date_column_var.set(automation.source_date_column if automation else "")
        self.target_date_column_var.set(automation.target_date_column if automation else "A")
        self.scheduled_time_var.set(automation.scheduled_time if automation else "08:00")
        self.lookback_days_var.set(str(automation.lookback_days if automation else 1))
        self.retry_minutes_var.set(str(automation.retry_minutes if automation else 15))
        self.catch_up_var.set(automation.catch_up if automation else True)
        self.paused_var.set(automation.paused if automation else False)
        selected_days = set(automation.weekdays if automation else range(7))
        for index, variable in enumerate(self.weekday_vars):
            variable.set(index in selected_days)
        if automation is None:
            self.import_status_var.set("Create an automation profile to configure scheduled imports.")
            return
        runs = self.db.get_production_log_automation_runs(automation.id, limit=1)
        if runs:
            latest = runs[0]
            self.import_status_var.set(
                f"Last run: {latest.started_at:%Y-%m-%d %H:%M} — {latest.status}: {latest.message}"
            )
        else:
            self.import_status_var.set("This automation has not run yet.")

    def _on_automation_selected(self, _event: object | None = None) -> None:
        selected = next((item for item in self.automations if item.name == self.automation_var.get()), None)
        self.current_automation_id = selected.id if selected else None
        self._load_automation_form(selected)

    def _new_automation(self) -> None:
        if self.current_client_id is None:
            messagebox.showinfo("Automation", "Select a client first.", parent=self)
            return
        name = simpledialog.askstring("New Automation", "Automation name:", parent=self)
        if not name:
            return
        try:
            self.current_automation_id = self.db.create_production_log_automation(self.current_client_id, name)
        except ValueError as exc:
            messagebox.showerror("Automation", str(exc), parent=self)
            return
        self._load_automations()

    def _delete_automation(self) -> None:
        if self.current_automation_id is None:
            return
        name = self.automation_name_var.get().strip() or "this automation"
        if not messagebox.askyesno(
            "Delete Automation",
            f"Delete '{name}' and its run history?",
            parent=self,
        ):
            return
        self.db.delete_production_log_automation(self.current_automation_id)
        self.current_automation_id = None
        self._load_automations()

    def _choose_sample_csv(self) -> None:
        path_text = filedialog.askopenfilename(
            parent=self,
            title="Select a Sample CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path_text:
            return
        path = Path(path_text)
        try:
            headers, rows = read_csv_rows(path)
        except ProductionLogError as exc:
            messagebox.showerror("Sample CSV", str(exc), parent=self)
            return
        self._sample_csv_path = path
        self._csv_headers = headers
        self.sample_csv_var.set(f"Sample: {path.name} ({len(rows)} data rows)")
        self.routing_column_combo.configure(values=headers)
        if self.routing_column_var.get().strip() not in headers and headers:
            self.routing_column_var.set(headers[0])
        self._update_mapping_inputs(self._last_column_choices)
        self.import_status_var.set("Sample CSV loaded. Map its headers, then save each worksheet mapping.")

    def _save_automation(self) -> None:
        if self.current_automation_id is None:
            messagebox.showinfo("Automation", "Create or select an automation first.", parent=self)
            return
        routing_column = self.routing_column_var.get().strip()
        if not routing_column:
            messagebox.showerror("Automation", "Enter the routing CSV column.", parent=self)
            return
        if not self.email_folder_var.get().strip():
            messagebox.showerror("Automation", "Enter the Outlook folder path.", parent=self)
            return
        try:
            scheduled_time = normalize_scheduled_time(self.scheduled_time_var.get())
            lookback_days = int(self.lookback_days_var.get())
            retry_minutes = int(self.retry_minutes_var.get())
            if lookback_days < 0:
                raise ValueError("Prior days cannot be negative.")
            if retry_minutes < 1:
                raise ValueError("Retry minutes must be at least 1.")
            weekdays = [index for index, variable in enumerate(self.weekday_vars) if variable.get()]
            self.db.update_production_log_automation(
                self.current_automation_id,
                name=self.automation_name_var.get(),
                outlook_source=self.outlook_source_var.get(),
                email_folder=self.email_folder_var.get(),
                email_subject_contains=self.email_subject_var.get(),
                email_subject_exact=self.email_subject_exact_var.get(),
                email_sender_contains=self.email_sender_var.get(),
                email_body_contains=self.email_body_var.get(),
                required_category=self.required_category_var.get(),
                completed_category=self.completed_category_var.get(),
                attachment_pattern=self.attachment_pattern_var.get(),
                routing_column=routing_column,
                scheduled_time=scheduled_time,
                weekdays=weekdays,
                lookback_days=lookback_days,
                catch_up=self.catch_up_var.get(),
                retry_minutes=retry_minutes,
                paused=self.paused_var.get(),
                update_mode=self.update_mode_var.get(),
                source_sort_column=self.source_sort_column_var.get(),
                source_date_column=self.source_date_column_var.get(),
                target_date_column=self.target_date_column_var.get(),
            )
        except ValueError as exc:
            messagebox.showerror("Automation", str(exc), parent=self)
            return
        selected_id = self.current_automation_id
        self._load_automations()
        self.current_automation_id = selected_id
        messagebox.showinfo("Automation", "Automation saved.", parent=self)

    def _parse_route_values(self) -> list[str]:
        return [item.strip() for item in re.split(r"[,;\n]+", self.route_values_var.get()) if item.strip()]

    def _build_import_rules(self) -> list[SheetImportRule]:
        if self.current_client_id is None:
            return []
        configs = self.db.get_production_log_sheet_configs(self.current_client_id)
        return [
            SheetImportRule(
                sheet_name=config.sheet_name,
                data_start_row=config.data_start_row,
                destination_columns=config.column_mappings,
                source_columns=config.source_mappings,
                route_values=config.route_values,
            )
            for config in configs
        ]

    def _make_updater(self) -> ProductionLogUpdater | DateMatchedProductionLogUpdater:
        workbook_path = Path(self.workbook_var.get().strip())
        routing_column = self.routing_column_var.get().strip()
        if not routing_column:
            raise ProductionLogError("Configure the routing CSV column first.")
        if self.update_mode_var.get() == "match_date":
            source_date = self.source_date_column_var.get().strip()
            if not source_date:
                raise ProductionLogError("Configure the CSV date column first.")
            return DateMatchedProductionLogUpdater(
                workbook_path,
                routing_column=routing_column,
                source_date_column=source_date,
                source_sort_column=self.source_sort_column_var.get(),
                target_date_column=self.target_date_column_var.get(),
                rules=self._build_import_rules(),
            )
        return ProductionLogUpdater(workbook_path, routing_column, self._build_import_rules())

    def _import_sample(self) -> None:
        if self._sample_csv_path is None:
            messagebox.showinfo("Import Sample", "Load a sample CSV first.", parent=self)
            return
        sample_path = self._sample_csv_path
        try:
            updater = self._make_updater()
        except ProductionLogError as exc:
            messagebox.showerror("Import Sample", str(exc), parent=self)
            return

        def work() -> ImportResult:
            return updater.import_csv(sample_path)

        self._run_import_task(work, "Importing sample CSV...", show_dialog=True)

    def _run_automation_now(self) -> None:
        if self.current_automation_id is None:
            messagebox.showinfo("Automation", "Create or select an automation first.", parent=self)
            return
        automation = self.db.get_production_log_automation(self.current_automation_id)
        if automation is None:
            messagebox.showerror("Automation", "The selected automation no longer exists.", parent=self)
            return
        now = datetime.now()
        scheduled_for = latest_scheduled_occurrence(automation, now) or now
        self._run_automation_profiles(
            [(automation, scheduled_for, "manual")],
            show_dialog=True,
        )

    def _run_automation_profiles(
        self,
        jobs: list[tuple[ProductionLogAutomation, datetime, str]],
        *,
        show_dialog: bool,
    ) -> None:
        if self._import_running:
            if show_dialog:
                messagebox.showinfo("Automation", "Another import is already running.", parent=self)
            return
        self._import_running = True
        names = ", ".join(item[0].name for item in jobs)
        self.import_status_var.set(f"Running: {names}...")

        def runner() -> None:
            service = ProductionLogAutomationRunner(self.db)
            messages: list[str] = []
            failures: list[str] = []
            for automation, scheduled_for, trigger_type in jobs:
                try:
                    result = service.run(
                        automation,
                        scheduled_for=scheduled_for,
                        trigger_type=trigger_type,
                    )
                except Exception as exc:
                    failures.append(f"{automation.name}: {exc}")
                else:
                    messages.append(f"{automation.name}: {result.message}")
            self.after(
                0,
                lambda: self._finish_automation_profiles(messages, failures, show_dialog),
            )

        threading.Thread(target=runner, name="production-log-scheduler", daemon=True).start()

    def _finish_automation_profiles(
        self,
        messages: list[str],
        failures: list[str],
        show_dialog: bool,
    ) -> None:
        self._import_running = False
        summary_parts = messages + [f"FAILED — {item}" for item in failures]
        summary = " | ".join(summary_parts) or "No automation ran."
        self.import_status_var.set(summary)
        if self.sheet_var.get().strip():
            self._load_sheet_preview(self.sheet_var.get().strip())
        if self.current_automation_id is not None:
            current = self.db.get_production_log_automation(self.current_automation_id)
            if current is not None and not show_dialog:
                self._load_automation_form(current)
        if show_dialog:
            if failures:
                messagebox.showerror("Production Log Automation", summary, parent=self)
            else:
                messagebox.showinfo("Production Log Automation", summary, parent=self)

    def _run_import_task(
        self,
        work,
        running_message: str,
        *,
        show_dialog: bool,
    ) -> None:
        if self._import_running:
            if show_dialog:
                messagebox.showinfo("Production Log", "An import is already running.", parent=self)
            return
        self._import_running = True
        self.import_status_var.set(running_message)

        def runner() -> None:
            try:
                result = work()
            except Exception as exc:
                self.after(0, lambda error=exc: self._finish_import_error(error, show_dialog))
            else:
                self.after(0, lambda value=result: self._finish_import_success(value, show_dialog))

        threading.Thread(target=runner, name="production-log-import", daemon=True).start()

    def _finish_import_success(self, result: ImportResult, show_dialog: bool) -> None:
        self._import_running = False
        sheets = ", ".join(sorted(result.sheets_updated)) or "none"
        report_path = self._write_manual_issue_report(result)
        summary = (
            f"Wrote {result.rows_written} rows / {result.cells_written} cells; "
            f"skipped {result.rows_skipped}; unrouted {result.unrouted_rows}; sheets: {sheets}."
        )
        if report_path is not None:
            summary += f" Exception report: {report_path}"
        if result.source_rows == 0:
            summary = "No new matching CSV attachments were found."
        self.import_status_var.set(summary)
        self._refresh_status()
        if self.sheet_var.get().strip():
            self._load_sheet_preview(self.sheet_var.get().strip())
        if show_dialog:
            messagebox.showinfo("Production Log Import", summary, parent=self)

    def _write_manual_issue_report(self, result: ImportResult) -> Optional[Path]:
        if not result.issues:
            return None
        report_dir = self.db.path.parent / "production_log_reports" / "manual"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"{datetime.now():%Y%m%d_%H%M%S_%f}_exceptions.json"
        payload = {
            "created_at": datetime.now().isoformat(),
            "source_file": str(self._sample_csv_path or ""),
            "workbook": self.workbook_var.get().strip(),
            "source_rows": result.source_rows,
            "rows_written": result.rows_written,
            "cells_written": result.cells_written,
            "rows_skipped": result.rows_skipped,
            "issues": [
                {
                    "reason": issue.reason,
                    "sheet_name": issue.sheet_name,
                    "source_row_number": issue.source_row_number,
                    "source_row": issue.source_row,
                    "target_row_number": issue.target_row_number,
                    "target_values": issue.target_values,
                    "target_row": issue.target_row,
                }
                for issue in result.issues
            ],
        }
        report_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return report_path

    def _finish_import_error(self, error: Exception, show_dialog: bool) -> None:
        self._import_running = False
        message = str(error) or error.__class__.__name__
        self.import_status_var.set(f"Import failed: {message}")
        if show_dialog:
            messagebox.showerror("Production Log Import", message, parent=self)

    def refresh_automation_status(self) -> None:
        self._load_automations()
        if self.sheet_var.get().strip():
            self._load_sheet_preview(self.sheet_var.get().strip())

    # ------------------------------------------------------------------ Preview
    def _clear_preview(self) -> None:
        self.preview_tree.delete(*self.preview_tree.get_children())
        self.preview_tree["columns"] = []
        self.preview_caption_var.set("")

    def _load_sheet_preview(self, sheet_name: str) -> None:
        self._clear_preview()
        path_text = (self.workbook_var.get() or "").strip()
        if not path_text or load_workbook is None:
            return
        try:
            workbook = load_workbook(path_text, read_only=True, data_only=True)
        except Exception:
            return
        try:
            if sheet_name not in workbook.sheetnames:
                return
            sheet = workbook[sheet_name]
            try:
                header_row, data_start_row = self._sheet_row_numbers()
            except ValueError:
                header_row, data_start_row = self._HEADER_ROW, self._DATA_START_ROW
            self.preview_caption_var.set(
                f"Showing {self._PREVIEW_ROWS} rows starting at row {data_start_row}; headers from row {header_row}."
            )
            header_rows = list(
                sheet.iter_rows(
                    min_row=header_row,
                    max_row=header_row,
                    values_only=True,
                )
            )
            headers = list(header_rows[0]) if header_rows else []
            data_rows = list(
                sheet.iter_rows(
                    min_row=data_start_row,
                    max_row=data_start_row + self._PREVIEW_ROWS - 1,
                    values_only=True,
                )
            )
            max_col = 0
            for idx, header in enumerate(headers, start=1):
                if header not in (None, ""):
                    max_col = max(max_col, idx)
            for row in data_rows:
                for idx, cell in enumerate(row, start=1):
                    if cell not in (None, ""):
                        max_col = max(max_col, idx)
            if max_col == 0:
                max_col = max(len(headers), 1)
            columns = []
            for col_idx in range(1, max_col + 1):
                letter = get_column_letter(col_idx) if get_column_letter else str(col_idx)
                columns.append(letter)
            self.preview_tree["columns"] = columns
            for col_idx, letter in enumerate(columns, start=1):
                header_text = ""
                if col_idx - 1 < len(headers):
                    header_val = headers[col_idx - 1]
                    header_text = str(header_val).strip() if header_val is not None else ""
                heading = f"{letter} - {header_text}" if header_text else letter
                self.preview_tree.heading(letter, text=heading)
                self.preview_tree.column(letter, width=140, anchor="w", stretch=False)
            for row in data_rows:
                values = []
                for col_idx in range(1, max_col + 1):
                    value = row[col_idx - 1] if col_idx - 1 < len(row) else ""
                    values.append("" if value is None else str(value))
                self.preview_tree.insert("", tk.END, values=values)
        finally:
            workbook.close()

    def apply_theme(self, theme: ThemePalette) -> None:
        self.theme = theme
        self._configure_styles()
        if hasattr(self, "_accent_strip") and self._accent_strip is not None:
            self._accent_strip.configure(bg=self.theme.accent)
        for widget in self.winfo_children():
            try:
                widget.configure(style="ProdLog.Root.TFrame")
            except tk.TclError:
                pass

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.configure("ProdLog.Root.TFrame", background=self.theme.surface_bg)
        style.configure("ProdLog.Hero.TFrame", background=self.theme.card_alt_bg)
        style.configure("ProdLog.Card.TFrame", background=self.theme.card_bg)
        style.configure(
            "ProdLog.Title.TLabel",
            background=self.theme.card_alt_bg,
            foreground=self.theme.text_primary,
            font=("Segoe UI", 14, "bold"),
        )
        style.configure(
            "ProdLog.Section.TLabel",
            background=self.theme.card_bg,
            foreground=self.theme.accent,
            font=("Segoe UI", 11, "bold"),
        )
        style.configure(
            "ProdLog.Body.TLabel",
            background=self.theme.card_alt_bg,
            foreground=self.theme.text_secondary,
            font=("Segoe UI", 10),
        )
        style.configure(
            "ProdLog.BodyMuted.TLabel",
            background=self.theme.card_bg,
            foreground=self.theme.text_muted,
            font=("Segoe UI", 9),
        )
        style.configure(
            "ProdLog.Card.TLabel",
            background=self.theme.card_bg,
            foreground=self.theme.text_primary,
            font=("Segoe UI", 10),
        )
        style.configure(
            "ProdLog.Badge.TLabel",
            background=self.theme.surface_alt_bg,
            foreground=self.theme.text_secondary,
            padding=(12, 4),
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "ProdLog.Treeview",
            background=self.theme.list_bg,
            fieldbackground=self.theme.list_bg,
            foreground=self.theme.text_primary,
            borderwidth=0,
            font=("Segoe UI", 10),
        )
        style.configure(
            "ProdLog.Treeview.Heading",
            background=self.theme.list_alt_bg,
            foreground=self.theme.text_secondary,
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "ProdLog.Treeview",
            background=[("selected", self.theme.list_selected_bg)],
            foreground=[("selected", self.theme.list_selected_fg)],
        )

    def is_locked(self) -> bool:
        return False

    def focus_lock_entry(self) -> None:
        return None

    def notify_locked(self) -> None:
        return None
