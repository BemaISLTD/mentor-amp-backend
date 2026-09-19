"""CSV (comma-separated values) file parser."""

import csv
import io


def parse_csv(file_path: str) -> list[dict]:
    """Parse a CSV file into a list of row dicts. Handles quoted fields."""
    with open(file_path, "r", encoding="utf-8-sig") as f:
        content = f.read()

    reader = csv.DictReader(io.StringIO(content))
    return [row for row in reader]
