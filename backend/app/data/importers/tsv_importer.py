"""TSV (tab-separated values) file parser."""

import csv
from app.data.importers.base import ImportLimitError


def parse_tsv(
    file_path: str,
    *,
    max_rows: int | None = None,
    max_columns: int | None = None,
) -> list[dict]:
    """Parse a TSV file into a list of row dicts. First row = headers."""
    with open(file_path, "r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, delimiter="\t")
        if max_columns is not None and len(reader.fieldnames or []) > max_columns:
            raise ImportLimitError(f"Column count exceeds limit {max_columns}.")
        rows = []
        for row in reader:
            rows.append(row)
            if max_rows is not None and len(rows) > max_rows:
                raise ImportLimitError(f"Record count exceeds limit {max_rows}.")
        return rows
