from __future__ import annotations

from copy import copy
import csv
from dataclasses import dataclass, field
from datetime import datetime
from fnmatch import fnmatch
import os
from pathlib import Path
import posixpath
import re
import shutil
import tempfile
from typing import Callable, Iterable, Optional, Sequence
from xml.etree import ElementTree
from xml.sax.saxutils import escape as xml_escape
from zipfile import ZipFile

try:
    import pythoncom  # type: ignore
except ImportError:  # pragma: no cover - Windows runtime dependency
    pythoncom = None  # type: ignore

try:
    import win32com.client  # type: ignore
except ImportError:  # pragma: no cover - Windows runtime dependency
    win32com = None  # type: ignore

try:
    from openpyxl import load_workbook  # type: ignore
    from openpyxl.utils import column_index_from_string  # type: ignore
except ImportError:  # pragma: no cover - reported at runtime
    load_workbook = None
    column_index_from_string = None


class ProductionLogError(RuntimeError):
    """A user-facing production log import failure."""


@dataclass(slots=True)
class SheetImportRule:
    sheet_name: str
    data_start_row: int
    destination_columns: dict[str, str]
    source_columns: dict[str, str]
    route_values: list[str] = field(default_factory=list)

    def resolved_mappings(self) -> list[tuple[str, str]]:
        mappings: list[tuple[str, str]] = []
        for field_key, destination in self.destination_columns.items():
            source = self.source_columns.get(field_key, "").strip()
            destination = destination.strip().upper()
            if source and destination:
                mappings.append((source, destination))
        return mappings


@dataclass(slots=True)
class ImportResult:
    source_rows: int = 0
    rows_written: int = 0
    cells_written: int = 0
    rows_skipped: int = 0
    unrouted_rows: int = 0
    sheets_updated: set[str] = field(default_factory=set)
    issues: list["ImportIssue"] = field(default_factory=list)


@dataclass(slots=True)
class ImportIssue:
    reason: str
    sheet_name: Optional[str]
    source_row_number: int
    source_row: dict[str, str]
    target_row_number: Optional[int] = None
    target_values: dict[str, object] = field(default_factory=dict)
    target_row: list[object] = field(default_factory=list)


@dataclass(slots=True)
class OutlookAttachment:
    message_id: str
    attachment_name: str
    received_time: object
    content: bytes
    categories: list[str] = field(default_factory=list)


def read_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ProductionLogError(f"Unable to read CSV: {exc}") from exc
    return read_csv_bytes(raw)


def read_csv_bytes(raw: bytes) -> tuple[list[str], list[dict[str, str]]]:
    text: Optional[str] = None
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ProductionLogError("The CSV attachment uses an unsupported text encoding.")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    headers = [str(item or "").strip() for item in (reader.fieldnames or [])]
    if not headers or not any(headers):
        raise ProductionLogError("The CSV does not contain a header row.")
    normalized = [_normalize_header(item) for item in headers]
    if len(set(normalized)) != len(normalized):
        raise ProductionLogError("CSV headers must be unique (ignoring capitalization and spaces).")
    rows: list[dict[str, str]] = []
    for raw_row in reader:
        row = {
            header: "" if raw_row.get(original) is None else str(raw_row.get(original)).strip()
            for original, header in zip(reader.fieldnames or [], headers)
        }
        if any(value != "" for value in row.values()):
            rows.append(row)
    return headers, rows


class ProductionLogUpdater:
    def __init__(self, workbook_path: Path, routing_column: str, rules: Sequence[SheetImportRule]) -> None:
        self.workbook_path = workbook_path
        self.routing_column = routing_column.strip()
        self.rules = list(rules)

    def import_csv(self, csv_path: Path) -> ImportResult:
        headers, rows = read_csv_rows(csv_path)
        return self.import_rows(headers, rows)

    def import_bytes(self, raw: bytes) -> ImportResult:
        headers, rows = read_csv_bytes(raw)
        return self.import_rows(headers, rows)

    def import_rows(self, headers: Sequence[str], rows: Sequence[dict[str, str]]) -> ImportResult:
        if load_workbook is None or column_index_from_string is None:
            raise ProductionLogError("openpyxl is required to update production log workbooks.")
        self._validate_workbook()
        header_lookup = {_normalize_header(header): header for header in headers}
        routing_header = header_lookup.get(_normalize_header(self.routing_column))
        if not routing_header:
            raise ProductionLogError(f"Routing column '{self.routing_column}' was not found in the CSV.")
        active_rules = [rule for rule in self.rules if rule.resolved_mappings()]
        if not active_rules:
            raise ProductionLogError("No worksheet has both source and destination column mappings.")
        self._validate_route_values(active_rules)
        self._validate_rule_headers(active_rules, header_lookup)
        result = ImportResult(source_rows=len(rows))
        keep_vba = self.workbook_path.suffix.lower() == ".xlsm"
        try:
            workbook = load_workbook(self.workbook_path, keep_vba=keep_vba)
        except Exception as exc:
            raise ProductionLogError(f"Unable to open workbook: {exc}") from exc
        try:
            self._validate_sheets(workbook.sheetnames, active_rules)
            for source_row in rows:
                route = str(source_row.get(routing_header, "") or "").strip()
                rule = self._resolve_rule(route, active_rules)
                if rule is None:
                    result.unrouted_rows += 1
                    continue
                mappings = []
                for source_name, destination in rule.resolved_mappings():
                    actual_source = header_lookup[_normalize_header(source_name)]
                    value = source_row.get(actual_source, "")
                    if value != "":
                        mappings.append((destination, value))
                if not mappings:
                    result.rows_skipped += 1
                    continue
                sheet = workbook[rule.sheet_name]
                target_row = self._find_empty_row(sheet, rule.data_start_row, [item[0] for item in mappings])
                self._ensure_row_format(sheet, target_row, [item[0] for item in mappings], rule.data_start_row)
                for destination, value in mappings:
                    cell = sheet.cell(row=target_row, column=column_index_from_string(destination))
                    if not _is_empty(cell.value):
                        raise ProductionLogError(
                            f"Safety check failed: {rule.sheet_name}!{destination}{target_row} is not empty."
                        )
                    cell.value = _coerce_for_cell(value, cell.number_format)
                    result.cells_written += 1
                result.rows_written += 1
                result.sheets_updated.add(rule.sheet_name)
            if result.cells_written:
                self._atomic_save(workbook)
        finally:
            workbook.close()
        return result

    def _validate_workbook(self) -> None:
        if self.workbook_path.suffix.lower() not in {".xlsx", ".xlsm"}:
            raise ProductionLogError("Select an .xlsx or .xlsm production log workbook.")
        if not self.workbook_path.exists():
            raise ProductionLogError("The configured production log workbook was not found.")
        lock_path = self.workbook_path.parent / f"~${self.workbook_path.name}"
        if lock_path.exists():
            raise ProductionLogError("The workbook is currently open. Close it before importing.")

    @staticmethod
    def _validate_rule_headers(rules: Sequence[SheetImportRule], header_lookup: dict[str, str]) -> None:
        missing = sorted({
            source
            for rule in rules
            for source, _destination in rule.resolved_mappings()
            if _normalize_header(source) not in header_lookup
        })
        if missing:
            raise ProductionLogError("CSV columns not found: " + ", ".join(missing))

    @staticmethod
    def _validate_sheets(sheet_names: Sequence[str], rules: Sequence[SheetImportRule]) -> None:
        missing = sorted({rule.sheet_name for rule in rules if rule.sheet_name not in sheet_names})
        if missing:
            raise ProductionLogError("Workbook sheets not found: " + ", ".join(missing))

    @staticmethod
    def _validate_route_values(rules: Sequence[SheetImportRule]) -> None:
        owners: dict[str, str] = {}
        conflicts: list[str] = []
        for rule in rules:
            for route_value in rule.route_values or [rule.sheet_name]:
                normalized = _normalize_route(route_value)
                if not normalized:
                    continue
                owner = owners.get(normalized)
                if owner is not None and owner != rule.sheet_name:
                    conflicts.append(f"{route_value} ({owner} and {rule.sheet_name})")
                else:
                    owners[normalized] = rule.sheet_name
        if conflicts:
            raise ProductionLogError("Route values must identify one sheet only: " + ", ".join(conflicts))

    @staticmethod
    def _resolve_rule(route: str, rules: Sequence[SheetImportRule]) -> Optional[SheetImportRule]:
        normalized = _normalize_route(route)
        for rule in rules:
            candidates = rule.route_values or [rule.sheet_name]
            if normalized in {_normalize_route(item) for item in candidates}:
                return rule
        return None

    @staticmethod
    def _find_empty_row(sheet, start_row: int, destination_columns: Sequence[str]) -> int:
        row = max(1, int(start_row))
        while True:
            if all(_is_empty(sheet[f"{column}{row}"].value) for column in destination_columns):
                return row
            row += 1

    @staticmethod
    def _ensure_row_format(sheet, target_row: int, destination_columns: Sequence[str], start_row: int) -> None:
        if target_row <= start_row:
            return
        source_row = target_row - 1
        if sheet.row_dimensions[source_row].height is not None and sheet.row_dimensions[target_row].height is None:
            sheet.row_dimensions[target_row].height = sheet.row_dimensions[source_row].height
        for column in destination_columns:
            source = sheet[f"{column}{source_row}"]
            target = sheet[f"{column}{target_row}"]
            if target.style_id == 0 and source.style_id != 0:
                target._style = copy(source._style)
                if source.has_style:
                    target.font = copy(source.font)
                    target.fill = copy(source.fill)
                    target.border = copy(source.border)
                    target.alignment = copy(source.alignment)
                    target.number_format = source.number_format
                    target.protection = copy(source.protection)

    def _atomic_save(self, workbook) -> None:
        suffix = self.workbook_path.suffix
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.workbook_path.stem}-", suffix=suffix, dir=self.workbook_path.parent)
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            workbook.save(temp_path)
            os.replace(temp_path, self.workbook_path)
        except Exception as exc:
            raise ProductionLogError(f"The workbook could not be saved: {exc}") from exc
        finally:
            temp_path.unlink(missing_ok=True)


class DateMatchedProductionLogUpdater:
    """Date-routed updater that changes only target values and calculation flags.

    The workbook is never re-saved by Excel or openpyxl. That is deliberate:
    Office applications can normalize unrelated row heights, drawing metadata,
    and style records during a normal save. This writer copies every OOXML part
    byte-for-byte and surgically changes only the approved cell value elements
    plus workbook calculation flags that make Excel fully recalculate formulas
    the next time the workbook is opened.
    """

    def __init__(
        self,
        workbook_path: Path,
        *,
        routing_column: str,
        source_date_column: str,
        source_sort_column: str,
        target_date_column: str,
        rules: Sequence[SheetImportRule],
        backup_root: Optional[Path] = None,
    ) -> None:
        self.workbook_path = workbook_path
        self.routing_column = routing_column.strip()
        self.source_date_column = source_date_column.strip()
        self.source_sort_column = source_sort_column.strip() or self.source_date_column
        self.target_date_column = target_date_column.strip().upper() or "A"
        self.rules = list(rules)
        self.backup_root = backup_root

    def import_csv(self, csv_path: Path, *, apply: bool = True) -> ImportResult:
        headers, rows = read_csv_rows(csv_path)
        return self.import_rows(headers, rows, apply=apply)

    def import_bytes(self, raw: bytes, *, apply: bool = True) -> ImportResult:
        headers, rows = read_csv_bytes(raw)
        return self.import_rows(headers, rows, apply=apply)

    def import_rows(
        self,
        headers: Sequence[str],
        rows: Sequence[dict[str, str]],
        *,
        apply: bool = True,
    ) -> ImportResult:
        if load_workbook is None or column_index_from_string is None:
            raise ProductionLogError("openpyxl is required to inspect production log workbooks.")
        validator = ProductionLogUpdater(self.workbook_path, self.routing_column, self.rules)
        validator._validate_workbook()
        active_rules = [rule for rule in self.rules if rule.resolved_mappings()]
        if not active_rules:
            raise ProductionLogError("No worksheet has both source and destination mappings.")
        validator._validate_route_values(active_rules)
        header_lookup = {_normalize_header(header): header for header in headers}
        required = [self.routing_column, self.source_date_column, self.source_sort_column]
        required.extend(source for rule in active_rules for source, _destination in rule.resolved_mappings())
        missing = sorted({item for item in required if _normalize_header(item) not in header_lookup})
        if missing:
            raise ProductionLogError("CSV columns not found: " + ", ".join(missing))
        numbered_rows = [(index + 2, dict(row)) for index, row in enumerate(rows)]
        sort_header = header_lookup[_normalize_header(self.source_sort_column)]
        numbered_rows.sort(key=lambda item: (_parse_datetime(item[1].get(sort_header, "")) or datetime.max, item[0]))
        result = ImportResult(source_rows=len(rows))
        pending_changes: dict[str, dict[str, object]] = {}
        try:
            workbook = load_workbook(
                self.workbook_path,
                read_only=True,
                data_only=True,
                keep_links=True,
                keep_vba=self.workbook_path.suffix.lower() == ".xlsm",
            )
            validator._validate_sheets(workbook.sheetnames, active_rules)
            route_header = header_lookup[_normalize_header(self.routing_column)]
            date_header = header_lookup[_normalize_header(self.source_date_column)]
            for source_row_number, source_row in numbered_rows:
                route_value = str(source_row.get(route_header, "") or "").strip()
                rule = validator._resolve_rule(route_value, active_rules)
                if rule is None:
                    result.unrouted_rows += 1
                    self._add_issue(result, "No worksheet route matched the CSV value.", None, source_row_number, source_row)
                    continue
                run_datetime = _parse_datetime(source_row.get(date_header, ""))
                if run_datetime is None:
                    self._add_issue(
                        result,
                        f"The source date '{source_row.get(date_header, '')}' could not be parsed.",
                        rule.sheet_name,
                        source_row_number,
                        source_row,
                    )
                    continue
                mappings: list[tuple[str, str, object]] = []
                missing_values: list[str] = []
                for source_name, destination in rule.resolved_mappings():
                    actual_source = header_lookup[_normalize_header(source_name)]
                    raw_value = source_row.get(actual_source, "")
                    if _is_empty(raw_value):
                        missing_values.append(actual_source)
                    mappings.append((actual_source, destination, raw_value))
                if missing_values:
                    self._add_issue(
                        result,
                        "Required CSV values are blank: " + ", ".join(missing_values),
                        rule.sheet_name,
                        source_row_number,
                        source_row,
                    )
                    continue
                sheet = workbook[rule.sheet_name]
                target_row = self._find_date_row(sheet, run_datetime.date(), rule.data_start_row)
                if target_row is None:
                    self._add_issue(
                        result,
                        f"No row in column {self.target_date_column} matches {run_datetime.date().isoformat()}.",
                        rule.sheet_name,
                        source_row_number,
                        source_row,
                    )
                    continue
                target_values = {
                    destination: pending_changes.get(rule.sheet_name, {}).get(
                        f"{destination}{target_row}",
                        sheet[f"{destination}{target_row}"].value,
                    )
                    for _source_name, destination, _value in mappings
                }
                occupied = {column: value for column, value in target_values.items() if not _is_empty(value)}
                if occupied:
                    self._add_issue(
                        result,
                        "The matched workbook row is not empty in every destination cell.",
                        rule.sheet_name,
                        source_row_number,
                        source_row,
                        target_row,
                        target_values,
                        self._read_target_row(sheet, target_row),
                    )
                    continue
                if apply:
                    for _source_name, destination, raw_value in mappings:
                        cell = sheet[f"{destination}{target_row}"]
                        coordinate = f"{destination}{target_row}"
                        pending_changes.setdefault(rule.sheet_name, {})[coordinate] = _coerce_for_cell(
                            str(raw_value), str(getattr(cell, "number_format", "") or "")
                        )
                result.rows_written += 1
                result.cells_written += len(mappings)
                result.sheets_updated.add(rule.sheet_name)
            workbook.close()
            workbook = None
            if apply and pending_changes:
                _surgical_update_xlsx(self.workbook_path, pending_changes, backup_root=self.backup_root)
        except ProductionLogError:
            raise
        except Exception as exc:
            raise ProductionLogError(f"Date-matched workbook update failed: {exc}") from exc
        finally:
            if workbook is not None:
                workbook.close()
        return result

    def _find_date_row(self, sheet, target_date, data_start_row: int) -> Optional[int]:
        last_row = max(int(sheet.max_row), int(data_start_row))
        for row in range(max(1, int(data_start_row)), last_row + 1):
            value = sheet[f"{self.target_date_column}{row}"].value
            value_datetime = value if isinstance(value, datetime) else _parse_datetime(value)
            if value_datetime is not None and value_datetime.date() == target_date:
                return row
        return None

    @staticmethod
    def _read_target_row(sheet, row: int) -> list[object]:
        last_column = min(100, int(sheet.max_column))
        return [sheet.cell(row=row, column=column).value for column in range(1, last_column + 1)]

    @staticmethod
    def _add_issue(
        result: ImportResult,
        reason: str,
        sheet_name: Optional[str],
        source_row_number: int,
        source_row: dict[str, str],
        target_row_number: Optional[int] = None,
        target_values: Optional[dict[str, object]] = None,
        target_row: Optional[list[object]] = None,
    ) -> None:
        result.rows_skipped += 1
        result.issues.append(
            ImportIssue(
                reason=reason,
                sheet_name=sheet_name,
                source_row_number=source_row_number,
                source_row=dict(source_row),
                target_row_number=target_row_number,
                target_values=dict(target_values or {}),
                target_row=list(target_row or []),
            )
        )


def _surgical_update_xlsx(
    workbook_path: Path,
    changes: dict[str, dict[str, object]],
    *,
    backup_root: Optional[Path] = None,
) -> Path:
    """Atomically update selected values and request full calculation on open."""
    backup_path = _create_workbook_backup(workbook_path, backup_root=backup_root)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{workbook_path.stem}-surgical-",
        suffix=workbook_path.suffix,
        dir=workbook_path.parent,
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with ZipFile(workbook_path, "r") as source:
            sheet_parts = _worksheet_part_names(source)
            missing_sheets = sorted(set(changes) - set(sheet_parts))
            if missing_sheets:
                raise ProductionLogError("Workbook sheets not found: " + ", ".join(missing_sheets))
            changed_parts = {sheet_parts[sheet_name] for sheet_name in changes}
            expected_parts: dict[str, bytes] = {}
            for sheet_name, cell_changes in changes.items():
                part_name = sheet_parts[sheet_name]
                expected_parts[part_name] = _patch_worksheet_values(source.read(part_name), cell_changes)
            workbook_part = "xl/workbook.xml"
            expected_parts[workbook_part] = _patch_workbook_calculation(source.read(workbook_part))
            changed_parts.add(workbook_part)
            with ZipFile(temp_path, "w", allowZip64=True) as destination:
                destination.comment = source.comment
                for info in source.infolist():
                    data = expected_parts.get(info.filename, source.read(info.filename))
                    destination.writestr(info, data)
        _verify_surgical_archive(workbook_path, temp_path, expected_parts, changed_parts)
        os.replace(temp_path, workbook_path)
        return backup_path
    except Exception:
        # The original path has not been replaced unless every verification passed.
        # Keep the backup as a recoverable audit artifact either way.
        raise
    finally:
        temp_path.unlink(missing_ok=True)


def _create_workbook_backup(workbook_path: Path, *, backup_root: Optional[Path] = None) -> Path:
    if backup_root is None:
        appdata = Path(os.getenv("APPDATA") or (Path.home() / "AppData/Roaming"))
        backup_root = appdata / "PersonalAssistant" / "production_log_backups"
    backup_dir = backup_root / workbook_path.stem
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = backup_dir / f"{workbook_path.stem}_{stamp}{workbook_path.suffix}"
    shutil.copy2(workbook_path, backup_path)
    return backup_path


def _worksheet_part_names(archive: ZipFile) -> dict[str, str]:
    main_namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    relation_namespace = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package_namespace = "http://schemas.openxmlformats.org/package/2006/relationships"
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        item.attrib["Id"]: item.attrib["Target"]
        for item in relationships.findall(f"{{{package_namespace}}}Relationship")
    }
    result: dict[str, str] = {}
    sheets = workbook.find(f"{{{main_namespace}}}sheets")
    if sheets is None:
        return result
    for sheet in sheets:
        relation_id = sheet.attrib.get(f"{{{relation_namespace}}}id", "")
        target = targets.get(relation_id, "")
        if not target:
            continue
        if target.startswith("/"):
            part_name = target.lstrip("/")
        else:
            part_name = posixpath.normpath(posixpath.join("xl", target))
        result[str(sheet.attrib.get("name", ""))] = part_name
    return result


def _patch_worksheet_values(raw_xml: bytes, changes: dict[str, object]) -> bytes:
    try:
        text = raw_xml.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProductionLogError("A worksheet uses an unsupported XML encoding.") from exc
    for coordinate, value in changes.items():
        escaped_coordinate = re.escape(coordinate.upper())
        pattern = re.compile(
            rf'(<c\b[^>]*\br="{escaped_coordinate}"[^>]*?)(?:\s*/>|>.*?</c>)',
            flags=re.DOTALL,
        )
        matches = list(pattern.finditer(text))
        if len(matches) != 1:
            raise ProductionLogError(
                f"Safety check failed: expected one existing formatted cell node for {coordinate}, found {len(matches)}."
            )
        start_tag = matches[0].group(1)
        if isinstance(value, bool):
            start_tag = re.sub(r'\s+t="[^"]*"', "", start_tag)
            replacement = f'{start_tag} t="b"><v>{1 if value else 0}</v></c>'
        elif isinstance(value, (int, float)):
            if re.search(r'\s+t="(?!n")[^"]*"', start_tag):
                start_tag = re.sub(r'\s+t="[^"]*"', ' t="n"', start_tag)
            replacement = f"{start_tag}><v>{value}</v></c>"
        else:
            start_tag = re.sub(r'\s+t="[^"]*"', "", start_tag)
            replacement = f'{start_tag} t="inlineStr"><is><t>{xml_escape(str(value))}</t></is></c>'
        text = text[: matches[0].start()] + replacement + text[matches[0].end() :]
    return text.encode("utf-8")


def _patch_workbook_calculation(raw_xml: bytes) -> bytes:
    """Request an automatic full calculation without resaving the workbook.

    Formula cells store both their formula and a cached result. Surgical value
    updates intentionally leave formula cells untouched, so Excel must be told
    that their cached results may be stale. Updating calcPr is formatting-neutral
    and leaves worksheet XML, styles, drawings, and formulas unchanged.
    """
    try:
        text = raw_xml.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProductionLogError("The workbook uses an unsupported XML encoding.") from exc
    pattern = re.compile(r"<calcPr\b[^>]*/?>")
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ProductionLogError(
            f"Safety check failed: expected one workbook calculation property node, found {len(matches)}."
        )
    tag = matches[0].group(0)
    for name, value in (
        ("calcMode", "auto"),
        ("fullCalcOnLoad", "1"),
        ("forceFullCalc", "1"),
    ):
        attribute = re.compile(rf"(\s+{re.escape(name)}\s*=\s*)(['\"])(.*?)\2")
        if attribute.search(tag):
            tag = attribute.sub(lambda match: f"{match.group(1)}{match.group(2)}{value}{match.group(2)}", tag, count=1)
        else:
            insertion = tag.rfind("/>")
            if insertion < 0:
                insertion = tag.rfind(">")
            tag = tag[:insertion] + f' {name}="{value}"' + tag[insertion:]
    return (text[: matches[0].start()] + tag + text[matches[0].end() :]).encode("utf-8")


def _verify_surgical_archive(
    original_path: Path,
    candidate_path: Path,
    expected_parts: dict[str, bytes],
    changed_parts: set[str],
) -> None:
    with ZipFile(original_path, "r") as original, ZipFile(candidate_path, "r") as candidate:
        original_names = original.namelist()
        candidate_names = candidate.namelist()
        if original_names != candidate_names:
            raise ProductionLogError("Workbook integrity check failed: archive structure changed.")
        for name in original_names:
            candidate_data = candidate.read(name)
            if name in changed_parts:
                if candidate_data != expected_parts[name]:
                    raise ProductionLogError(f"Workbook integrity check failed for {name}.")
            elif candidate_data != original.read(name):
                raise ProductionLogError(
                    f"Workbook integrity check failed: unrelated workbook part changed ({name})."
                )


class OutlookCsvSource:
    """Reads matching CSV attachments without changing Outlook mailbox state."""

    def fetch_pending(
        self,
        *,
        folder_path: str,
        subject_contains: str = "",
        subject_exact: bool = False,
        sender_contains: str = "",
        body_contains: str = "",
        attachment_pattern: str = "*.csv",
        required_category: str = "",
        already_processed: Optional[Callable[[str, str], bool]] = None,
        received_since: Optional[datetime] = None,
        received_until: Optional[datetime] = None,
        limit: int = 30,
    ) -> list[OutlookAttachment]:
        if win32com is None:
            raise ProductionLogError("pywin32 is required to read Outlook attachments.")
        if not folder_path.strip():
            raise ProductionLogError("Configure an Outlook folder path first.")
        initialized = False
        if pythoncom is not None:
            pythoncom.CoInitialize()
            initialized = True
        try:
            app = win32com.client.Dispatch("Outlook.Application")
            namespace = app.GetNamespace("MAPI")
            folder = self._resolve_folder(namespace, folder_path)
            if folder is None:
                raise ProductionLogError(f"Outlook folder not found: {folder_path}")
            items = folder.Items
            items.Sort("[ReceivedTime]", True)
            matches: list[OutlookAttachment] = []
            with tempfile.TemporaryDirectory(prefix="production-log-email-") as temp_dir:
                for item in items:
                    if getattr(item, "Class", None) != 43:
                        continue
                    subject = str(getattr(item, "Subject", "") or "")
                    sender = self._sender_address(item)
                    body = str(getattr(item, "Body", "") or "")
                    categories = self._parse_categories(str(getattr(item, "Categories", "") or ""))
                    received_time = getattr(item, "ReceivedTime", None)
                    comparable_received = _naive_datetime(received_time)
                    if received_since and comparable_received and comparable_received < _naive_datetime(received_since):
                        break
                    if received_until and comparable_received and comparable_received > _naive_datetime(received_until):
                        continue
                    if subject_contains:
                        if subject_exact and subject.casefold().strip() != subject_contains.casefold().strip():
                            continue
                        if not subject_exact and subject_contains.casefold() not in subject.casefold():
                            continue
                    if sender_contains and sender_contains.casefold() not in sender.casefold():
                        continue
                    if body_contains and body_contains.casefold() not in body.casefold():
                        continue
                    if required_category and required_category.casefold() not in {
                        item.casefold() for item in categories
                    }:
                        continue
                    message_id = str(getattr(item, "EntryID", "") or "")
                    if not message_id:
                        continue
                    attachments = getattr(item, "Attachments", None)
                    if attachments is None:
                        continue
                    for index in range(1, int(attachments.Count) + 1):
                        attachment = attachments.Item(index)
                        name = str(getattr(attachment, "FileName", "") or "")
                        if not fnmatch(name.casefold(), (attachment_pattern or "*.csv").casefold()):
                            continue
                        if already_processed and already_processed(message_id, name):
                            continue
                        destination = Path(temp_dir) / f"{len(matches):03d}-{Path(name).name}"
                        attachment.SaveAsFile(str(destination))
                        matches.append(
                            OutlookAttachment(
                                message_id=message_id,
                                attachment_name=name,
                                received_time=received_time,
                                content=destination.read_bytes(),
                                categories=categories,
                            )
                        )
            matches.reverse()
            # Outlook is scanned newest-first so date cutoffs can stop early, but
            # production updates must always consume the oldest pending mail first.
            return matches[:limit]
        except ProductionLogError:
            raise
        except Exception as exc:
            raise ProductionLogError(f"Unable to read Outlook: {exc}") from exc
        finally:
            if initialized:
                pythoncom.CoUninitialize()

    @staticmethod
    def _resolve_folder(namespace, folder_path: str):
        parts = [part for part in folder_path.replace("\\", "/").split("/") if part]
        if not parts:
            return None
        try:
            folder = namespace.Folders.Item(parts[0])
            for part in parts[1:]:
                folder = folder.Folders.Item(part)
            return folder
        except Exception:
            return None

    def mark_message_updated(
        self,
        message_id: str,
        *,
        remove_category: str,
        add_category: str,
    ) -> None:
        if win32com is None:
            raise ProductionLogError("pywin32 is required to update Outlook categories.")
        initialized = False
        if pythoncom is not None:
            pythoncom.CoInitialize()
            initialized = True
        try:
            app = win32com.client.Dispatch("Outlook.Application")
            namespace = app.GetNamespace("MAPI")
            item = namespace.GetItemFromID(message_id)
            categories = self._parse_categories(str(getattr(item, "Categories", "") or ""))
            categories = [value for value in categories if value.casefold() != remove_category.casefold()]
            if add_category and add_category.casefold() not in {value.casefold() for value in categories}:
                categories.append(add_category)
            item.Categories = ", ".join(categories)
            item.Save()
        except Exception as exc:
            raise ProductionLogError(f"Workbook updated, but Outlook categories could not be changed: {exc}") from exc
        finally:
            if initialized:
                pythoncom.CoUninitialize()

    @staticmethod
    def _parse_categories(value: str) -> list[str]:
        return [item.strip() for item in re.split(r"[,;]", value or "") if item.strip()]

    @staticmethod
    def _sender_address(item) -> str:
        address = str(getattr(item, "SenderEmailAddress", "") or "")
        try:
            sender = getattr(item, "Sender", None)
            exchange_user = sender.GetExchangeUser() if sender is not None else None
            primary = str(getattr(exchange_user, "PrimarySmtpAddress", "") or "")
            if primary:
                return primary
        except Exception:
            pass
        return address


def _normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _normalize_route(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _is_empty(value: object) -> bool:
    return value is None or (isinstance(value, str) and value == "")


def _coerce_for_cell(value: str, number_format: str) -> object:
    text = str(value)
    if "@" in (number_format or ""):
        return text
    stripped = text.strip()
    if re.fullmatch(r"-?(?:0|[1-9]\d*)", stripped):
        if len(stripped.lstrip("-")) == 1 or not stripped.lstrip("-").startswith("0"):
            try:
                return int(stripped)
            except ValueError:
                pass
    if re.fullmatch(r"-?(?:0|[1-9]\d*)\.\d+", stripped):
        try:
            return float(stripped)
        except ValueError:
            pass
    return text


def _coerce_for_excel(value: object, number_format: str) -> object:
    text = str(value or "")
    if "@" in (number_format or ""):
        return text
    stripped = text.strip()
    if re.fullmatch(r"-?(?:0|[1-9]\d*)", stripped):
        if len(stripped.lstrip("-")) == 1 or not stripped.lstrip("-").startswith("0"):
            try:
                return int(stripped)
            except ValueError:
                pass
    if re.fullmatch(r"-?(?:0|[1-9]\d*)\.\d+", stripped):
        try:
            return float(stripped)
        except ValueError:
            pass
    return text


def _parse_datetime(value: object) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"\s+", " ", text)
    for pattern in (
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _naive_datetime(value: object) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value
