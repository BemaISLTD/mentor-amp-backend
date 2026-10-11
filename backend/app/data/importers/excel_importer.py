"""Excel (.xlsx) file parser using openpyxl."""

from app.data.importers.base import ImportLimitError
from openpyxl import load_workbook


def parse_excel(
    file_path: str,
    *,
    max_rows: int | None = None,
    max_columns: int | None = None,
    max_sheets: int | None = None,
) -> list[dict]:
    """Parse the first sheet of an Excel file. First row = headers."""
    wb = load_workbook(file_path, read_only=True, data_only=True)
    if max_sheets is not None and len(wb.sheetnames) > max_sheets:
        wb.close()
        raise ImportLimitError(f"Workbook sheet count exceeds limit {max_sheets}.")
    ws = wb.active
    if max_columns is not None and ws.max_column > max_columns:
        wb.close()
        raise ImportLimitError(f"Column count exceeds limit {max_columns}.")
    if max_rows is not None and ws.max_row - 1 > max_rows:
        wb.close()
        raise ImportLimitError(f"Record count exceeds limit {max_rows}.")

    rows_iter = ws.iter_rows(values_only=True)
    headers = [str(h) if h is not None else f"col_{i}" for i, h in enumerate(next(rows_iter, []))]

    if not headers:
        wb.close()
        return []

    result = []
    for row in rows_iter:
        if all(v is None for v in row):
            continue  # skip completely empty rows
        result.append({headers[i]: row[i] if i < len(row) else None for i in range(len(headers))})

    wb.close()
    return result
