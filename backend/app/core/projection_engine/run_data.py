"""In-memory inputs for one projection run.

Everything the engine needs is loaded ONCE into these plain objects (by
``app.services.run_loader``). The engine itself never touches the database, files or network,
so it can be tested without a database and later executed on other backends (CAS §9, MP §22).
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

_NUMERIC = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def normalize_key(value: Any) -> Any:
    """Normalise a lookup-key value so table rows and resolved values compare exactly.

    Whole numbers (including numeric text such as "72" or "72.0") become ``int``; other numbers
    stay ``float``; other text is trimmed and upper-cased. ``None`` stays ``None``.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        number = float(value)
        return int(number) if number.is_integer() else number
    text = str(value).strip()
    if _NUMERIC.match(text):
        number = float(text)
        return int(number) if number.is_integer() else number
    return text.upper()


@dataclass(frozen=True)
class VariableSpec:
    name: str
    kind: str
    data_type: str = "number"
    unit: str | None = None
    source: dict = field(default_factory=dict)
    default_value: Any = None
    required: bool = True
    display_name: str | None = None
    version: str = "v1"

    @property
    def source_type(self) -> str:
        return str(self.source.get("type") or self.kind)


@dataclass(frozen=True)
class FormulaSpec:
    id: str
    name: str
    output_variable: str
    function_ref: str
    dependencies: tuple[str, ...]
    expression_text: str | None = None
    version: str = "v1"
    illustrative: bool = False


@dataclass
class TableSpec:
    """A lookup table (assumption or factor) with an exact-match index."""

    id: str
    name: str
    key_columns: tuple[str, ...]
    value_column: str
    rows: list[dict]
    fingerprint: str | None = None
    kind: str = "assumption"
    index: dict[tuple, Any] = field(default_factory=dict)

    def build_index(self) -> list[tuple]:
        """Build the exact-match index; return any duplicate keys found."""
        self.index = {}
        duplicates: list[tuple] = []
        for row in self.rows:
            key = tuple(normalize_key(row.get(column)) for column in self.key_columns)
            if key in self.index:
                duplicates.append(key)
            self.index[key] = row.get(self.value_column)
        return duplicates


@dataclass(frozen=True)
class PolicyRecord:
    policy_id: str
    data: dict
    dataset_id: str | None = None
    dataset_name: str | None = None


@dataclass(frozen=True)
class ScenarioSpec:
    id: str | None
    name: str
    overrides: tuple[dict, ...] = ()


@dataclass
class RunData:
    variables: dict[str, VariableSpec]
    formulas: list[FormulaSpec]  # in execution (dependency) order
    tables: dict[str, TableSpec]
    policies: list[PolicyRecord]
    scenario: ScenarioSpec
    valuation_date: date
    horizon_months: int
    output_variables: list[str]
    traced_policy_ids: frozenset[str] = frozenset()

    def valuation_variables(self) -> list[VariableSpec]:
        return [spec for spec in self.variables.values() if spec.source_type == "valuation"]
