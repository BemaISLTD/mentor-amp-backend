"""Governed table registry, fixture verification, and typed yield curves."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from .table_engine import (
    ActuarialTable,
    BoundaryPolicy,
    DimensionType,
    InterpolationMethod,
    TableDimension,
    TableSchemaError,
    governed_table,
)


class RegistryError(ValueError):
    """Invalid, unapproved, duplicated, or tampered registry object."""


class CurveMethodologyError(ValueError):
    """A yield curve is being used without a valid rate methodology."""


class YieldCurveType(str, Enum):
    SPOT_ZERO = "SPOT_ZERO"
    PAR = "PAR"


class RateCompounding(str, Enum):
    ANNUAL = "ANNUAL"
    CONTINUOUS = "CONTINUOUS"
    SIMPLE = "SIMPLE"


@dataclass(frozen=True)
class YieldCurve:
    """A curve whose par/spot semantics are explicit and non-interchangeable."""

    curve_id: str
    curve_type: YieldCurveType
    tenor_years: tuple[float, ...]
    rates: tuple[float, ...]
    source_reference: str
    effective_date: str
    compounding: RateCompounding = RateCompounding.ANNUAL
    methodology: str | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "curve_type", YieldCurveType(self.curve_type))
        except (TypeError, ValueError) as exc:
            raise CurveMethodologyError(
                f"Unsupported curve type '{self.curve_type}'."
            ) from exc
        try:
            object.__setattr__(self, "compounding", RateCompounding(self.compounding))
        except (TypeError, ValueError) as exc:
            raise CurveMethodologyError(
                f"Unsupported rate compounding '{self.compounding}'."
            ) from exc
        if not self.curve_id or not self.source_reference or not self.effective_date:
            raise CurveMethodologyError("Curve identity, source, and effective date are required.")
        if len(self.tenor_years) != len(self.rates) or not self.tenor_years:
            raise CurveMethodologyError("Curve tenors and rates must have equal non-zero length.")
        tenors = np.asarray(self.tenor_years, dtype=np.float64)
        rates = np.asarray(self.rates, dtype=np.float64)
        if np.any(~np.isfinite(tenors)) or np.any(tenors <= 0.0) or np.any(np.diff(tenors) <= 0.0):
            raise CurveMethodologyError("Curve tenors must be finite, positive, and increasing.")
        if np.any(~np.isfinite(rates)):
            raise CurveMethodologyError("Curve rates must be finite.")
        if self.compounding == RateCompounding.ANNUAL and np.any(rates <= -1.0):
            raise CurveMethodologyError("Annually compounded spot rates must exceed -100%.")

    def rate(self, tenor_years: float) -> float:
        tenor = float(tenor_years)
        if not math.isfinite(tenor):
            raise CurveMethodologyError("Requested tenor must be finite.")
        low, high = self.tenor_years[0], self.tenor_years[-1]
        if tenor < low or tenor > high:
            raise CurveMethodologyError(f"Requested tenor is outside [{low}, {high}].")
        return float(np.interp(tenor, self.tenor_years, self.rates))

    def discount_factor(self, tenor_years: float) -> float:
        """Derive a discount factor only from an explicitly identified spot curve."""

        if self.curve_type != YieldCurveType.SPOT_ZERO:
            raise CurveMethodologyError(
                "Par yields cannot produce discount factors directly. Apply an explicit, "
                "validated par-to-spot bootstrapping methodology first."
            )
        tenor = float(tenor_years)
        rate = self.rate(tenor)
        if self.compounding == RateCompounding.ANNUAL:
            return (1.0 + rate) ** (-tenor)
        if self.compounding == RateCompounding.CONTINUOUS:
            return math.exp(-rate * tenor)
        if self.compounding != RateCompounding.SIMPLE:  # Defensive against future enum additions.
            raise CurveMethodologyError(f"Unsupported rate compounding '{self.compounding}'.")
        denominator = 1.0 + rate * tenor
        if denominator <= 0.0:
            raise CurveMethodologyError("Simple-rate discount denominator must be positive.")
        return 1.0 / denominator

    def bootstrap_to_spot(
        self,
        converter: Callable[[np.ndarray, np.ndarray], Sequence[float]],
        *,
        methodology: str,
        validated: bool,
    ) -> "YieldCurve":
        """Convert a par curve only through a named, explicitly validated methodology."""

        if self.curve_type != YieldCurveType.PAR:
            raise CurveMethodologyError("Only a PAR curve requires par-to-spot bootstrapping.")
        if not validated or not methodology.strip():
            raise CurveMethodologyError(
                "Par-to-spot conversion requires a named methodology with validated=True."
            )
        converted = tuple(float(value) for value in converter(
            np.asarray(self.tenor_years, dtype=np.float64),
            np.asarray(self.rates, dtype=np.float64),
        ))
        if len(converted) != len(self.rates):
            raise CurveMethodologyError("Par-to-spot converter returned the wrong number of rates.")
        return YieldCurve(
            curve_id=f"{self.curve_id}-spot",
            curve_type=YieldCurveType.SPOT_ZERO,
            tenor_years=self.tenor_years,
            rates=converted,
            source_reference=self.source_reference,
            effective_date=self.effective_date,
            compounding=self.compounding,
            methodology=methodology,
        )


class ActuarialTableRegistry:
    """Thread-safe cache that exposes approved, fingerprint-valid tables only."""

    def __init__(self) -> None:
        self._tables: dict[str, ActuarialTable] = {}
        self._lock = threading.RLock()

    def register(self, table: ActuarialTable) -> None:
        if table.governance.status != "approved":
            raise RegistryError("Only independently approved tables may enter the runtime registry.")
        if not table.verify_fingerprint():
            raise RegistryError("Table fingerprint verification failed.")
        table_id = table.governance.table_id
        with self._lock:
            if table_id in self._tables:
                raise RegistryError(f"Table '{table_id}' is already registered and immutable.")
            self._tables[table_id] = table

    def get(self, table_id: str) -> ActuarialTable:
        with self._lock:
            try:
                table = self._tables[table_id]
            except KeyError as exc:
                raise KeyError(f"Actuarial table '{table_id}' is not registered.") from exc
        if not table.verify_fingerprint():
            raise RegistryError(f"Actuarial table '{table_id}' failed fingerprint verification.")
        return table

    def snapshot(self) -> Mapping[str, ActuarialTable]:
        with self._lock:
            return MappingProxyType(dict(self._tables))


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_coordinate(raw: str, dimension: TableDimension) -> Any:
    if dimension.dimension_type == DimensionType.CATEGORICAL:
        return raw
    try:
        numeric = float(raw)
    except ValueError as exc:
        raise TableSchemaError(
            f"Coordinate '{dimension.name}' contains non-numeric value '{raw}'."
        ) from exc
    if dimension.dimension_type == DimensionType.INTEGER:
        if not numeric.is_integer():
            raise TableSchemaError(
                f"Coordinate '{dimension.name}' contains fractional integer '{raw}'."
            )
        return int(numeric)
    return numeric


def load_csv_table(
    path: str | Path,
    *,
    dimensions: Sequence[TableDimension],
    value_column: str,
    governance: Mapping[str, Any],
    interpolation: InterpolationMethod = InterpolationMethod.STEP_FLOOR,
    boundary_policy: BoundaryPolicy = BoundaryPolicy.FAIL_CLOSED,
) -> ActuarialTable:
    """Load a complete CSV grid and construct its immutable governed table."""

    dimensions = tuple(dimensions)
    index_maps = [
        {value: index for index, value in enumerate(dimension.values)}
        for dimension in dimensions
    ]
    shape = tuple(len(dimension.values) for dimension in dimensions)
    values = np.full(shape, np.nan, dtype=np.float64)
    seen: set[tuple[int, ...]] = set()
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {dimension.name for dimension in dimensions} | {value_column}
        missing_columns = sorted(required - set(reader.fieldnames or []))
        if missing_columns:
            raise TableSchemaError(f"CSV is missing required columns: {missing_columns}.")
        for row_number, row in enumerate(reader, start=2):
            indices: list[int] = []
            for dimension, mapping in zip(dimensions, index_maps):
                coordinate = _parse_coordinate(row[dimension.name], dimension)
                if coordinate not in mapping:
                    raise TableSchemaError(
                        f"Row {row_number} coordinate {dimension.name}={coordinate!r} "
                        "is outside the declared grid."
                    )
                indices.append(mapping[coordinate])
            key = tuple(indices)
            if key in seen:
                raise TableSchemaError(f"Row {row_number} duplicates table coordinate {key}.")
            seen.add(key)
            try:
                value = float(row[value_column])
            except (TypeError, ValueError) as exc:
                raise TableSchemaError(
                    f"Row {row_number} value '{value_column}' is not numeric."
                ) from exc
            if not math.isfinite(value):
                raise TableSchemaError(f"Row {row_number} contains a non-finite table value.")
            values[key] = value
    if len(seen) != values.size or np.any(np.isnan(values)):
        raise TableSchemaError(
            f"CSV grid is incomplete: found {len(seen)} of {values.size} required coordinates."
        )
    return governed_table(
        dimensions=dimensions,
        values=values,
        interpolation=interpolation,
        boundary_policy=boundary_policy,
        **dict(governance),
    )


def load_yield_curve(path: str | Path) -> YieldCurve:
    """Load a JSON curve while preserving its declared par/spot semantics."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {
        "curve_type", "tenors_years", "rates", "source_reference", "effective_date",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise CurveMethodologyError(f"Curve fixture is missing required fields: {missing}.")
    curve_id = payload.get("curve_id") or Path(path).stem
    return YieldCurve(
        curve_id=curve_id,
        curve_type=YieldCurveType(payload["curve_type"]),
        tenor_years=tuple(float(value) for value in payload["tenors_years"]),
        rates=tuple(float(value) for value in payload["rates"]),
        source_reference=str(payload["source_reference"]),
        effective_date=str(payload["effective_date"]),
        compounding=RateCompounding(payload.get("compounding", "ANNUAL")),
        methodology=payload.get("methodology"),
    )


def verify_fixture_manifest(directory: str | Path) -> dict[str, str]:
    """Verify every fixture byte checksum declared in ``manifest.json``."""

    root = Path(directory)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    results: dict[str, str] = {}
    for record in manifest.get("fixtures", []):
        filename = record.get("filename")
        expected = record.get("sha256")
        if not filename or not isinstance(expected, str):
            raise RegistryError("Fixture manifest entries require filename and sha256.")
        actual = file_sha256(root / filename)
        if actual != expected:
            raise RegistryError(f"Fixture '{filename}' failed SHA-256 verification.")
        results[filename] = actual
    return results
