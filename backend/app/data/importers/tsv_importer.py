"""TSV (tab-separated values) file parser."""

import csv
import io


def parse_tsv(file_path: str) -> list[dict]:
    """Parse a TSV file into a list of row dicts. First row = headers."""
    with open(file_path, "r", encoding="utf-8-sig") as f:
        content = f.read()

    reader = csv.DictReader(io.StringIO(content), delimiter="\t")
    return [row for row in reader]
