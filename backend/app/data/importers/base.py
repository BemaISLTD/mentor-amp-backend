"""Base importer — format detection, parsing, validation, and storage."""

import os
from pathlib import Path
from typing import Any, Callable

from app.data.importers.tsv_importer import parse_tsv
from app.data.importers.csv_importer import parse_csv
from app.data.importers.excel_importer import parse_excel


def detect_format(filename: str) -> str:
    """Detect file format from extension. Returns 'tsv', 'csv', or 'xlsx'."""
    ext = Path(filename).suffix.lower()
    if ext in (".tsv", ".tab"):
        return "tsv"
    elif ext == ".csv":
        return "csv"
    elif ext in (".xlsx", ".xls"):
        return "xlsx"
    raise ValueError(f"Unsupported file format: {ext}. Supported: .tsv, .csv, .xlsx")


def parse_file(file_path: str) -> list[dict]:
    """Parse a file into a list of row dicts, auto-detecting format."""
    fmt = detect_format(file_path)
    parsers: dict[str, Callable[[str], list[dict]]] = {
        "tsv": parse_tsv,
        "csv": parse_csv,
        "xlsx": parse_excel,
    }
    return parsers[fmt](file_path)


def preview_file(file_path: str, max_rows: int = 20) -> dict[str, Any]:
    """Return columns and sample rows for preview, without storing anything."""
    rows = parse_file(file_path)
    columns = list(rows[0].keys()) if rows else []
    sample = rows[:max_rows]
    return {
        "columns": columns,
        "row_count": len(rows),
        "sample_rows": sample,
    }


def import_file(
    file_path: str,
    db_session: Any,
    validate_func: Callable[[list[dict]], list[dict]],
    store_func: Callable[[Any, list[dict]], int],
) -> dict[str, Any]:
    """Full import pipeline: parse -> validate -> store.

    Returns a dict with:
        - row_count: total rows parsed
        - stored_count: rows actually stored
        - columns: list of column names
        - errors: list of validation errors
    """
    rows = parse_file(file_path)
    columns = list(rows[0].keys()) if rows else []

    errors = validate_func(rows) if validate_func else []

    # Separate hard errors (block import) from warnings (store anyway)
    hard_errors = [e for e in errors if e.get("severity", "error") != "warning"]

    stored_count = 0
    if not hard_errors:
        stored_count = store_func(db_session, rows)

    return {
        "row_count": len(rows),
        "stored_count": stored_count,
        "columns": columns,
        "errors": errors,
    }
