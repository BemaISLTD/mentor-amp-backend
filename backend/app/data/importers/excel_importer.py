"""Excel (.xlsx) file parser using openpyxl."""

from openpyxl import load_workbook


def parse_excel(file_path: str) -> list[dict]:
    """Parse the first sheet of an Excel file. First row = headers."""
    wb = load_workbook(file_path, read_only=True, data_only=True)
    ws = wb.active

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
