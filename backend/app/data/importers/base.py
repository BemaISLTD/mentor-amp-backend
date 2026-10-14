"""Base importer — format detection, parsing, validation, and storage."""

import os
import polars as pl
from pathlib import Path
from typing import Any, Callable

class ImportLimitError(ValueError):
    """The file is valid tabular data but exceeds the synchronous import budget."""


from app.data.importers.tsv_importer import parse_tsv  # noqa: E402
from app.data.importers.csv_importer import parse_csv  # noqa: E402
from app.data.importers.excel_importer import parse_excel  # noqa: E402


def detect_format(filename: str) -> str:
    """Detect an accepted tabular format from its extension."""
    ext = Path(filename).suffix.lower()
    if ext in (".tsv", ".tab"):
        return "tsv"
    elif ext == ".csv":
        return "csv"
    elif ext == ".xlsx":
        return "xlsx"
    elif ext == ".parquet":
        return "parquet"
    raise ValueError(f"Unsupported file format: {ext}. Supported: .tsv, .csv, .xlsx, .parquet")


def parse_file(
    file_path: str,
    *,
    max_rows: int | None = None,
    max_columns: int | None = None,
    max_xlsx_sheets: int | None = None,
) -> list[dict]:
    """Parse a file into a list of row dicts, auto-detecting format."""
    fmt = detect_format(file_path)
    if fmt == "csv":
        return parse_csv(file_path, max_rows=max_rows, max_columns=max_columns)
    if fmt == "tsv":
        return parse_tsv(file_path, max_rows=max_rows, max_columns=max_columns)
    if fmt == "xlsx":
        return parse_excel(
            file_path,
            max_rows=max_rows,
            max_columns=max_columns,
            max_sheets=max_xlsx_sheets,
        )
    frame = pl.scan_parquet(file_path)
    if max_rows is not None:
        frame = frame.limit(max_rows + 1)
    rows = frame.collect().to_dicts()
    if max_rows is not None and len(rows) > max_rows:
        raise ImportLimitError(f"Record count exceeds limit {max_rows}.")
    if max_columns is not None and rows and len(rows[0]) > max_columns:
        raise ImportLimitError(f"Column count exceeds limit {max_columns}.")
    return rows


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
