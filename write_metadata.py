from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, List, Any, Tuple


def _normalize_cell(value: Any) -> str:
    """
    Convert arbitrary Python values into a CSV-friendly string:
    - None -> ""
    - lists/tuples -> "; "-joined strings (if all elements are scalar), else JSON
    - dicts -> JSON
    - everything else -> str(value)
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        if all(isinstance(x, (str, int, float, bool, type(None))) for x in value):
            return "; ".join("" if x is None else str(x) for x in value)
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _read_existing_csv(input_csv: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    """
    Read an existing CSV (if present). Returns (rows, headers).
    If the file does not exist or is empty, returns ([], []).
    """
    rows: List[Dict[str, str]] = []
    headers: List[str] = []
    if input_csv.exists() and input_csv.stat().st_size > 0:
        with input_csv.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            rows.extend(reader)
    return rows, headers


def _compute_headers(
    existing_headers: List[str],
    new_records: List[Dict[str, Any]],
    add_missing_columns: bool,
) -> List[str]:
    """
    Merge existing headers with keys from new_records.
    Preserves existing order; appends new columns in first-seen order.
    """
    headers = list(existing_headers)
    if add_missing_columns:
        seen = set(headers)
        for rec in new_records:
            for k in rec.keys():
                if k not in seen:
                    headers.append(k)
                    seen.add(k)
    return headers


def append_records_to_csv(
    input_csv_path: str | Path,
    output_csv_path: str | Path,
    records: List[Dict[str, Any]],
    *,
    add_missing_columns: bool = True,
    keep_existing_rows: bool = True,
) -> None:
    """
    Append a list of augmented metadata records to a CSV by matching keys to column headers.

    Parameters
    ----------
    input_csv_path : path-like
        Existing CSV to read headers (and optionally existing rows) from. If it
        doesn't exist, headers will be inferred from the provided records.
    output_csv_path : path-like
        Destination CSV path to write the combined result.
    records : list of dict
        Augmented metadata dicts to write as new rows.
    add_missing_columns : bool
        If True (default), any keys present in `records` but missing from the CSV
        headers will be appended as new columns.
    keep_existing_rows : bool
        If True (default), existing rows from the input CSV are copied to the output.
        If False, only the new records are written.
    """
    input_csv = Path(input_csv_path)
    output_csv = Path(output_csv_path)

    # Load any existing data
    existing_rows, existing_headers = _read_existing_csv(input_csv)

    # Determine headers
    headers = _compute_headers(existing_headers, records, add_missing_columns)

    # If no existing headers and not adding new columns, infer from records anyway
    if not headers:
        seen = set()
        headers = []
        for rec in records:
            for k in rec.keys():
                if k not in seen:
                    headers.append(k)
                    seen.add(k)

    # Build rows for new records
    new_rows: List[Dict[str, str]] = []
    for rec in records:
        row: Dict[str, str] = {h: "" for h in headers}
        for k, v in rec.items():
            if k not in headers:
                if add_missing_columns:
                    headers.append(k)
                    row[k] = _normalize_cell(v)
                else:
                    continue
            else:
                row[k] = _normalize_cell(v)
        new_rows.append(row)

    # Ensure all rows have all headers
    if add_missing_columns:
        def pad(row: Dict[str, str]) -> Dict[str, str]:
            for h in headers:
                row.setdefault(h, "")
            return {h: row.get(h, "") for h in headers}

        existing_rows = [pad(dict(r)) for r in existing_rows]
        new_rows = [pad(r) for r in new_rows]

    # Combine
    final_rows = (existing_rows if keep_existing_rows else []) + new_rows

    # Write output
    with output_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(final_rows)

'''aug1 = {"title": "Sunset", "begin": "2021:10:12 18:03:11", "number": 3.217}
aug2 = {"title": "Library Lawn", "n_odd": "Nashville, TN"}

records = [aug1, aug2]

append_records_to_csv(
    input_csv_path="template.csv",
    output_csv_path="manifest_out.csv",
    records=records,
    add_missing_columns=True,
    keep_existing_rows=True,
)'''