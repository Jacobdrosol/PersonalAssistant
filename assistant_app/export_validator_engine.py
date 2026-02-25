from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree as ET


ValidationMode = Literal["strict", "compressed"]


class ExportValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ComparisonOptions:
    strip_whitespace: bool = True
    collapse_internal_whitespace: bool = False
    case_sensitive: bool = True


@dataclass(frozen=True)
class ExportRule:
    record_xpath: str
    key_fields: list[str]
    compare_fields: list[str]
    options: ComparisonOptions
    parent_xpath: str | None = None
    parent_key_fields: list[str] | None = None
    file_type: str = "xml"
    csv_has_header: bool = True
    csv_ignore_column_contains: list[str] | None = None


@dataclass(frozen=True)
class RecordData:
    index: int
    key: tuple[str, ...]
    key_display: str
    fields: dict[str, str]


@dataclass(frozen=True)
class FieldMismatch:
    key_display: str
    field_path: str
    baseline_value: str
    candidate_value: str


@dataclass(frozen=True)
class VariantDifference:
    key_display: str
    count: int
    field_values: dict[str, str]


@dataclass(frozen=True)
class CollectionResult:
    records: dict[tuple[str, ...], list[RecordData]]
    duplicates: dict[tuple[str, ...], list[RecordData]]
    total_records: int


@dataclass(frozen=True)
class ComparisonResult:
    baseline_total: int
    candidate_total: int
    matched_keys: int
    missing_in_candidate: list[RecordData]
    extra_in_candidate: list[RecordData]
    mismatches: list[FieldMismatch]
    baseline_only_variants: list[VariantDifference]
    candidate_only_variants: list[VariantDifference]
    baseline_duplicates: dict[tuple[str, ...], list[RecordData]]
    candidate_duplicates: dict[tuple[str, ...], list[RecordData]]

    @property
    def passed(self) -> bool:
        return not (
            self.missing_in_candidate
            or self.extra_in_candidate
            or self.mismatches
            or self.baseline_only_variants
            or self.candidate_only_variants
        )


@dataclass(frozen=True)
class CsvDataset:
    headers: list[str]
    rows: list[tuple[str, ...]]


@dataclass(frozen=True)
class CsvRowDifference:
    count: int
    values: tuple[str, ...]


@dataclass(frozen=True)
class CsvComparisonResult:
    baseline_headers: list[str]
    candidate_headers: list[str]
    baseline_total: int
    candidate_total: int
    missing_in_candidate: list[CsvRowDifference]
    extra_in_candidate: list[CsvRowDifference]
    baseline_duplicate_rows: int
    candidate_duplicate_rows: int

    @property
    def field_mismatches(self) -> int:
        return 0 if self.baseline_headers == self.candidate_headers else 1

    @property
    def passed(self) -> bool:
        return (
            self.field_mismatches == 0
            and not self.missing_in_candidate
            and not self.extra_in_candidate
        )


@dataclass(frozen=True)
class ValidationOutput:
    passed: bool
    report_text: str
    file_type: str


def load_export_types_from_file(rules_path: Path) -> dict[str, Any]:
    if not rules_path.exists():
        raise ExportValidationError(f"Rules file not found: {rules_path}")
    try:
        payload = json.loads(rules_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ExportValidationError(f"Rules file is invalid JSON: {exc}") from exc
    export_types = payload.get("export_types")
    if not isinstance(export_types, dict):
        raise ExportValidationError("Rules JSON must contain an 'export_types' object.")
    return export_types


def load_rule(export_types: dict[str, Any], export_type: str) -> ExportRule:
    if export_type not in export_types:
        available = ", ".join(sorted(export_types.keys()))
        raise ExportValidationError(
            f"Export type '{export_type}' not found in rules. Available types: {available}"
        )
    payload = export_types[export_type]
    if not isinstance(payload, dict):
        raise ExportValidationError(f"Rule for '{export_type}' must be an object.")

    file_type = str(payload.get("file_type", "xml")).strip().lower()
    if file_type not in {"xml", "csv"}:
        raise ExportValidationError(f"Rule '{export_type}' file_type must be 'xml' or 'csv'.")

    record_xpath = str(payload.get("record_xpath", "")).strip()
    key_fields = _read_string_list(payload.get("key_fields"), "key_fields", export_type)
    compare_fields = _read_string_list(payload.get("compare_fields"), "compare_fields", export_type)
    parent_xpath_raw = str(payload.get("parent_xpath", "")).strip()
    parent_xpath = parent_xpath_raw or None
    parent_key_fields = _read_string_list(
        payload.get("parent_key_fields"), "parent_key_fields", export_type
    )
    csv_ignore_column_contains = _read_string_list(
        payload.get("csv_ignore_column_contains"), "csv_ignore_column_contains", export_type
    )

    if file_type == "xml":
        if not record_xpath:
            raise ExportValidationError(
                f"Rule '{export_type}' is missing record_xpath. Update export_rules.json."
            )
        if not key_fields and not parent_key_fields:
            raise ExportValidationError(
                f"Rule '{export_type}' has no key_fields configured."
            )
        if not compare_fields:
            raise ExportValidationError(
                f"Rule '{export_type}' has no compare_fields configured."
            )
        if parent_xpath and not parent_key_fields:
            raise ExportValidationError(
                f"Rule '{export_type}' uses parent_xpath but has no parent_key_fields configured."
            )

    options_payload = payload.get("options", {})
    if not isinstance(options_payload, dict):
        raise ExportValidationError(f"Rule '{export_type}' options must be an object.")
    options = ComparisonOptions(
        strip_whitespace=bool(options_payload.get("strip_whitespace", True)),
        collapse_internal_whitespace=bool(options_payload.get("collapse_internal_whitespace", False)),
        case_sensitive=bool(options_payload.get("case_sensitive", True)),
    )

    return ExportRule(
        record_xpath=record_xpath,
        key_fields=key_fields,
        compare_fields=compare_fields,
        options=options,
        parent_xpath=parent_xpath,
        parent_key_fields=parent_key_fields or None,
        file_type=file_type,
        csv_has_header=bool(payload.get("csv_has_header", True)),
        csv_ignore_column_contains=csv_ignore_column_contains or None,
    )


def get_file_type(export_types: dict[str, Any], export_type: str) -> str:
    payload = export_types.get(export_type)
    if not isinstance(payload, dict):
        return "xml"
    file_type = str(payload.get("file_type", "xml")).strip().lower()
    return "csv" if file_type == "csv" else "xml"


def run_validation(
    *,
    export_types: dict[str, Any],
    export_type: str,
    baseline_content: str,
    candidate_content: str,
    baseline_name: str,
    candidate_name: str,
    rules_name: str,
    mode: ValidationMode = "strict",
) -> ValidationOutput:
    normalized_mode: ValidationMode = "compressed" if mode == "compressed" else "strict"
    rule = load_rule(export_types, export_type)

    if rule.file_type == "csv":
        baseline = read_csv_dataset_from_text(
            baseline_content, options=rule.options, has_header=rule.csv_has_header
        )
        candidate = read_csv_dataset_from_text(
            candidate_content, options=rule.options, has_header=rule.csv_has_header
        )
        baseline = filter_csv_dataset_columns(baseline, rule.csv_ignore_column_contains)
        candidate = filter_csv_dataset_columns(candidate, rule.csv_ignore_column_contains)
        result = compare_csv_datasets(baseline, candidate, mode=normalized_mode)
        report_text = build_csv_report(
            result=result,
            export_type=export_type,
            baseline_name=baseline_name,
            candidate_name=candidate_name,
            rules_name=rules_name,
            mode=normalized_mode,
        )
        return ValidationOutput(passed=result.passed, report_text=report_text, file_type="csv")

    baseline = collect_records_from_xml_text(baseline_content, rule)
    candidate = collect_records_from_xml_text(candidate_content, rule)
    result = compare_collections(baseline, candidate, rule.compare_fields, mode=normalized_mode)
    report_text = build_xml_report(
        result=result,
        export_type=export_type,
        baseline_name=baseline_name,
        candidate_name=candidate_name,
        rules_name=rules_name,
        mode=normalized_mode,
    )
    return ValidationOutput(passed=result.passed, report_text=report_text, file_type="xml")


def _read_string_list(value: Any, field_name: str, export_type: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ExportValidationError(f"Rule '{export_type}' {field_name} must be a list.")
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ExportValidationError(
                f"Rule '{export_type}' {field_name} must only contain strings."
            )
        stripped = item.strip()
        if stripped:
            cleaned.append(stripped)
    return cleaned


def strip_namespaces(root: ET.Element) -> None:
    for element in root.iter():
        if "}" in element.tag:
            element.tag = element.tag.split("}", 1)[1]
        cleaned_attributes: dict[str, str] = {}
        for key, value in element.attrib.items():
            if "}" in key:
                key = key.split("}", 1)[1]
            cleaned_attributes[key] = value
        element.attrib.clear()
        element.attrib.update(cleaned_attributes)


def normalize_value(value: str, options: ComparisonOptions) -> str:
    normalized = value
    if options.strip_whitespace:
        normalized = normalized.strip()
    if options.collapse_internal_whitespace:
        normalized = re.sub(r"\s+", " ", normalized)
    if not options.case_sensitive:
        normalized = normalized.casefold()
    return normalized


def flatten_text(element: ET.Element) -> str:
    return "".join(element.itertext())


def extract_values(record: ET.Element, path: str) -> list[str]:
    if path in (".", "./"):
        return [flatten_text(record)]
    if path == "text()":
        return [record.text or ""]
    if path.startswith("@"):
        return [record.attrib.get(path[1:], "")]
    if "/@" in path:
        element_path, attribute = path.rsplit("/@", 1)
        nodes = [record] if element_path in ("", ".") else record.findall(element_path)
        if not nodes:
            return [""]
        values = [node.attrib.get(attribute, "") for node in nodes]
        return values or [""]
    nodes = record.findall(path)
    if not nodes:
        maybe_single = record.find(path)
        if maybe_single is not None:
            nodes = [maybe_single]
    if not nodes:
        return [""]
    return [flatten_text(node) for node in nodes]


def extract_field(record: ET.Element, path: str, options: ComparisonOptions) -> str:
    values = extract_values(record, path)
    normalized = [normalize_value(value, options) for value in values]
    if len(normalized) == 1:
        return normalized[0]
    return " || ".join(normalized)


def display_key(values: tuple[str, ...]) -> str:
    return " | ".join(value if value else "<empty>" for value in values)


def collect_records_from_xml_text(xml_text: str, rule: ExportRule) -> CollectionResult:
    root = ET.fromstring(xml_text)
    strip_namespaces(root)
    grouped: dict[tuple[str, ...], list[RecordData]] = defaultdict(list)
    parent_key_counts: dict[tuple[str, ...], int] = defaultdict(int)
    index = 0

    if rule.parent_xpath:
        parent_nodes = [root] if rule.parent_xpath in (".", "./") else root.findall(rule.parent_xpath)
        parent_key_fields = rule.parent_key_fields or []
        for parent_node in parent_nodes:
            key_values = tuple(
                extract_field(parent_node, path, rule.options) for path in parent_key_fields
            )
            parent_key_counts[key_values] += 1
            child_nodes = [parent_node] if rule.record_xpath in (".", "./") else parent_node.findall(rule.record_xpath)
            for child_node in child_nodes:
                index += 1
                grouped[key_values].append(
                    RecordData(
                        index=index,
                        key=key_values,
                        key_display=display_key(key_values),
                        fields={path: extract_field(child_node, path, rule.options) for path in rule.compare_fields},
                    )
                )
        duplicates = {key: grouped[key] for key, count in parent_key_counts.items() if count > 1}
    else:
        nodes = [root] if rule.record_xpath in (".", "./") else root.findall(rule.record_xpath)
        for node in nodes:
            index += 1
            key_values = tuple(extract_field(node, path, rule.options) for path in rule.key_fields)
            grouped[key_values].append(
                RecordData(
                    index=index,
                    key=key_values,
                    key_display=display_key(key_values),
                    fields={path: extract_field(node, path, rule.options) for path in rule.compare_fields},
                )
            )
        duplicates = {key: records for key, records in grouped.items() if len(records) > 1}
    return CollectionResult(records=dict(grouped), duplicates=duplicates, total_records=index)


def sort_keys(keys: set[tuple[str, ...]]) -> list[tuple[str, ...]]:
    return sorted(keys, key=lambda key: " | ".join(key))


def compare_collections(
    baseline: CollectionResult,
    candidate: CollectionResult,
    compare_fields: list[str],
    *,
    mode: ValidationMode,
) -> ComparisonResult:
    baseline_keys = set(baseline.records.keys())
    candidate_keys = set(candidate.records.keys())

    missing_keys = sort_keys(baseline_keys - candidate_keys) if mode == "strict" else []
    extra_keys = sort_keys(candidate_keys - baseline_keys)
    shared_keys = sort_keys(baseline_keys & candidate_keys)

    missing_in_candidate = [record for key in missing_keys for record in baseline.records[key]]
    extra_in_candidate = [record for key in extra_keys for record in candidate.records[key]]

    mismatches: list[FieldMismatch] = []
    baseline_only_variants: list[VariantDifference] = []
    candidate_only_variants: list[VariantDifference] = []

    for key in shared_keys:
        baseline_records = baseline.records[key]
        candidate_records = candidate.records[key]
        if len(baseline_records) == 1 and len(candidate_records) == 1:
            left = baseline_records[0]
            right = candidate_records[0]
            for field_path in compare_fields:
                left_value = left.fields[field_path]
                right_value = right.fields[field_path]
                if left_value != right_value:
                    mismatches.append(
                        FieldMismatch(
                            key_display=left.key_display,
                            field_path=field_path,
                            baseline_value=left_value,
                            candidate_value=right_value,
                        )
                    )
            continue

        def signature(record: RecordData) -> tuple[str, ...]:
            return tuple(record.fields[field] for field in compare_fields)

        baseline_counter = Counter(signature(record) for record in baseline_records)
        candidate_counter = Counter(signature(record) for record in candidate_records)

        if mode == "strict":
            for sig, count in (baseline_counter - candidate_counter).items():
                baseline_only_variants.append(
                    VariantDifference(
                        key_display=display_key(key),
                        count=count,
                        field_values={field: sig[i] for i, field in enumerate(compare_fields)},
                    )
                )
        for sig, count in (candidate_counter - baseline_counter).items():
            candidate_only_variants.append(
                VariantDifference(
                    key_display=display_key(key),
                    count=count,
                    field_values={field: sig[i] for i, field in enumerate(compare_fields)},
                )
            )

    return ComparisonResult(
        baseline_total=baseline.total_records,
        candidate_total=candidate.total_records,
        matched_keys=len(shared_keys),
        missing_in_candidate=missing_in_candidate,
        extra_in_candidate=extra_in_candidate,
        mismatches=mismatches,
        baseline_only_variants=baseline_only_variants,
        candidate_only_variants=candidate_only_variants,
        baseline_duplicates=baseline.duplicates,
        candidate_duplicates=candidate.duplicates,
    )


def read_csv_dataset_from_text(csv_text: str, options: ComparisonOptions, has_header: bool) -> CsvDataset:
    reader = csv.reader(io.StringIO(csv_text))
    raw_rows = [list(row) for row in reader]
    if has_header and raw_rows:
        raw_headers = raw_rows[0]
        raw_data_rows = raw_rows[1:]
    else:
        raw_headers = []
        raw_data_rows = raw_rows

    width = max([len(raw_headers)] + [len(row) for row in raw_data_rows], default=0)
    headers = [normalize_value(header, options) for header in raw_headers] if raw_headers else []
    while len(headers) < width:
        headers.append(f"Column{len(headers) + 1}")

    normalized_rows: list[tuple[str, ...]] = []
    for row in raw_data_rows:
        padded = row + [""] * (width - len(row))
        normalized_rows.append(tuple(normalize_value(value, options) for value in padded[:width]))
    return CsvDataset(headers=headers, rows=normalized_rows)


def filter_csv_dataset_columns(dataset: CsvDataset, ignore_column_contains: list[str] | None) -> CsvDataset:
    if not ignore_column_contains:
        return dataset
    tokens = [token.casefold() for token in ignore_column_contains if token]
    if not tokens:
        return dataset
    keep_indexes: list[int] = []
    keep_headers: list[str] = []
    for index, header in enumerate(dataset.headers):
        header_folded = header.casefold()
        if any(token in header_folded for token in tokens):
            continue
        keep_indexes.append(index)
        keep_headers.append(header)
    filtered_rows = [tuple(row[index] for index in keep_indexes) for row in dataset.rows]
    return CsvDataset(headers=keep_headers, rows=filtered_rows)


def compare_csv_datasets(
    baseline: CsvDataset, candidate: CsvDataset, *, mode: ValidationMode
) -> CsvComparisonResult:
    baseline_counter = Counter(baseline.rows)
    candidate_counter = Counter(candidate.rows)
    baseline_only = baseline_counter - candidate_counter if mode == "strict" else Counter()
    candidate_only = candidate_counter - baseline_counter
    baseline_only_rows = [CsvRowDifference(count=count, values=values) for values, count in baseline_only.items()]
    candidate_only_rows = [CsvRowDifference(count=count, values=values) for values, count in candidate_only.items()]
    baseline_only_rows.sort(key=lambda diff: " | ".join(diff.values))
    candidate_only_rows.sort(key=lambda diff: " | ".join(diff.values))
    return CsvComparisonResult(
        baseline_headers=baseline.headers,
        candidate_headers=candidate.headers,
        baseline_total=len(baseline.rows),
        candidate_total=len(candidate.rows),
        missing_in_candidate=baseline_only_rows,
        extra_in_candidate=candidate_only_rows,
        baseline_duplicate_rows=sum(1 for count in baseline_counter.values() if count > 1),
        candidate_duplicate_rows=sum(1 for count in candidate_counter.values() if count > 1),
    )


def _format_value(value: str) -> str:
    compact = value.replace("\r\n", "\n").replace("\r", "\n")
    compact = compact.replace("\n", "\\n")
    return compact if compact else "<empty>"


def _format_csv_row(headers: list[str], values: tuple[str, ...]) -> str:
    if not values:
        return "<empty row>"
    if not headers:
        return " | ".join(f"Column{index + 1}={_format_value(value)}" for index, value in enumerate(values))
    return " | ".join(f"{headers[index]}={_format_value(value)}" for index, value in enumerate(values))


def _mode_label(mode: ValidationMode) -> str:
    return "Mass (1:1)" if mode == "strict" else "Compressed (candidate subset)"


def build_xml_report(
    *,
    result: ComparisonResult,
    export_type: str,
    baseline_name: str,
    candidate_name: str,
    rules_name: str,
    mode: ValidationMode,
) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = [
        "Core Export Validator Notes",
        "===========================",
        f"Generated: {timestamp}",
        f"Export type: {export_type}",
        f"Validation mode: {_mode_label(mode)}",
        f"Baseline file: {baseline_name}",
        f"Candidate file: {candidate_name}",
        f"Rules file: {rules_name}",
        "",
        "Summary",
        "-------",
        f"Result: {'PASS' if result.passed else 'FAIL'}",
        f"Baseline record count: {result.baseline_total}",
        f"Candidate record count: {result.candidate_total}",
        f"Matched keys: {result.matched_keys}",
        f"Missing in candidate: {len(result.missing_in_candidate)}",
        f"Extra in candidate: {len(result.extra_in_candidate)}",
        f"Field mismatches: {len(result.mismatches)}",
        f"Baseline-only variants (same key): {sum(diff.count for diff in result.baseline_only_variants)}",
        f"Candidate-only variants (same key): {sum(diff.count for diff in result.candidate_only_variants)}",
        f"Duplicate keys in baseline: {len(result.baseline_duplicates)}",
        f"Duplicate keys in candidate: {len(result.candidate_duplicates)}",
        "",
    ]
    if mode == "compressed":
        lines.append("Note: Compressed mode ignores baseline-only records/variants.")
        lines.append("")

    if result.missing_in_candidate:
        lines.extend(["Missing Records in Candidate", "----------------------------"])
        summary = Counter(record.key_display for record in result.missing_in_candidate)
        for key_display in sorted(summary.keys()):
            lines.append(
                f"Key '{key_display}' is in baseline file, but not candidate file (count: {summary[key_display]})."
            )
        lines.append("")

    if result.extra_in_candidate:
        lines.extend(["Extra Records in Candidate", "--------------------------"])
        summary = Counter(record.key_display for record in result.extra_in_candidate)
        for key_display in sorted(summary.keys()):
            lines.append(
                f"Key '{key_display}' is in candidate file, but not baseline file (count: {summary[key_display]})."
            )
        lines.append("")

    if result.mismatches:
        lines.extend(["Field Mismatches", "----------------"])
        for mismatch in result.mismatches:
            lines.append(f"Key: {mismatch.key_display}")
            lines.append(f"Field: {mismatch.field_path}")
            lines.append(f"Baseline: {_format_value(mismatch.baseline_value)}")
            lines.append(f"Candidate: {_format_value(mismatch.candidate_value)}")
            lines.append("")

    if result.baseline_only_variants:
        lines.extend(["Baseline-Only Record Variants (Same Key)", "----------------------------------------"])
        for diff in result.baseline_only_variants:
            lines.append(f"Key: {diff.key_display}")
            lines.append(f"Occurrences not found in candidate: {diff.count}")
            for field_name, field_value in diff.field_values.items():
                lines.append(f"{field_name}: {_format_value(field_value)}")
            lines.append("")

    if result.candidate_only_variants:
        lines.extend(["Candidate-Only Record Variants (Same Key)", "-----------------------------------------"])
        for diff in result.candidate_only_variants:
            lines.append(f"Key: {diff.key_display}")
            lines.append(f"Occurrences not found in baseline: {diff.count}")
            for field_name, field_value in diff.field_values.items():
                lines.append(f"{field_name}: {_format_value(field_value)}")
            lines.append("")

    if result.passed:
        lines.extend(["No differences found.", ""])
    return "\n".join(lines)


def build_csv_report(
    *,
    result: CsvComparisonResult,
    export_type: str,
    baseline_name: str,
    candidate_name: str,
    rules_name: str,
    mode: ValidationMode,
) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    missing_total = sum(diff.count for diff in result.missing_in_candidate)
    extra_total = sum(diff.count for diff in result.extra_in_candidate)
    lines: list[str] = [
        "Core Export Validator Notes",
        "===========================",
        f"Generated: {timestamp}",
        f"Export type: {export_type}",
        f"Validation mode: {_mode_label(mode)}",
        f"Baseline file: {baseline_name}",
        f"Candidate file: {candidate_name}",
        f"Rules file: {rules_name}",
        "",
        "Summary",
        "-------",
        f"Result: {'PASS' if result.passed else 'FAIL'}",
        f"Baseline record count: {result.baseline_total}",
        f"Candidate record count: {result.candidate_total}",
        f"Matched rows: {result.baseline_total - missing_total}",
        f"Missing in candidate: {missing_total}",
        f"Extra in candidate: {extra_total}",
        f"Field mismatches: {result.field_mismatches}",
        "Baseline-only variants (same key): 0",
        "Candidate-only variants (same key): 0",
        f"Duplicate keys in baseline: {result.baseline_duplicate_rows}",
        f"Duplicate keys in candidate: {result.candidate_duplicate_rows}",
        "",
    ]
    if mode == "compressed":
        lines.append("Note: Compressed mode ignores baseline-only rows.")
        lines.append("")

    if result.baseline_headers != result.candidate_headers:
        lines.extend(
            [
                "Field Mismatches",
                "----------------",
                "Key: CSV Header Row",
                f"Baseline: {_format_csv_row([], tuple(result.baseline_headers))}",
                f"Candidate: {_format_csv_row([], tuple(result.candidate_headers))}",
                "",
            ]
        )

    if result.missing_in_candidate:
        lines.extend(["Missing Records in Candidate", "----------------------------"])
        for diff in result.missing_in_candidate:
            lines.append(f"Count: {diff.count}")
            lines.append(f"Row: {_format_csv_row(result.baseline_headers, diff.values)}")
            lines.append("")

    if result.extra_in_candidate:
        lines.extend(["Extra Records in Candidate", "--------------------------"])
        for diff in result.extra_in_candidate:
            lines.append(f"Count: {diff.count}")
            lines.append(f"Row: {_format_csv_row(result.candidate_headers, diff.values)}")
            lines.append("")

    if result.passed:
        lines.extend(["No differences found.", ""])
    return "\n".join(lines)
