"""Immutable, vectorized multidimensional actuarial table lookups.

The engine is deliberately independent of persistence and product code.  A table is a
complete rectangular grid whose dimensions and values are frozen at construction time.
Categorical coordinates always require exact tokens; numerical dimensions support step
and multilinear interpolation with an explicit boundary policy.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Sequence

import numpy as np


class InterpolationMethod(str, Enum):
    STEP_FLOOR = "STEP_FLOOR"
    STEP_NEAREST = "STEP_NEAREST"
    LINEAR = "LINEAR"
    BILINEAR = "BILINEAR"
    MULTILINEAR = "MULTILINEAR"


class BoundaryPolicy(str, Enum):
    FAIL_CLOSED = "FAIL_CLOSED"
    CLAMP_TO_BOUNDS = "CLAMP_TO_BOUNDS"
    EXTRAPOLATE_FLAT = "EXTRAPOLATE_FLAT"


class DimensionType(str, Enum):
    CATEGORICAL = "CATEGORICAL"
    INTEGER = "INTEGER"
    CONTINUOUS = "CONTINUOUS"


class TableError(ValueError):
    """Base error for invalid table definitions and lookup requests."""


class TableDimensionMissingError(TableError):
    """A required query coordinate was not supplied."""


class TableOutOfBoundsError(TableError):
    """A numerical query coordinate is outside a fail-closed table grid."""


class TableSchemaError(TableError):
    """A table grid is incomplete, non-finite, duplicated, or otherwise invalid."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CANONICAL_CATEGORIES = {
    "sex": frozenset({"MALE", "FEMALE", "UNISEX", "M", "F", "U"}),
    "gender": frozenset({"MALE", "FEMALE", "UNISEX", "M", "F", "U"}),
    "smoker_status": frozenset({"SMOKER", "NON_SMOKER", "UNISMOKE"}),
    "underwriting_class": frozenset({
        "STANDARD", "PREFERRED", "PREFERRED_PLUS", "SUBSTANDARD",
    }),
}


@dataclass(frozen=True)
class TableDimension:
    """One ordered table axis.

    Categorical values retain their declared order and must be queried exactly. Integer
    and continuous grids must be strictly increasing and finite.
    """

    name: str
    dimension_type: DimensionType
    values: tuple[Any, ...]

    def __post_init__(self) -> None:
        if isinstance(self.values, (str, bytes)):
            raise TableSchemaError(
                f"Dimension '{self.name}' values must be a sequence, not text."
            )
        try:
            frozen_values = tuple(self.values)
        except TypeError as exc:
            raise TableSchemaError(
                f"Dimension '{self.name}' values must be an iterable sequence."
            ) from exc
        object.__setattr__(self, "values", frozen_values)
        try:
            object.__setattr__(self, "dimension_type", DimensionType(self.dimension_type))
        except (TypeError, ValueError) as exc:
            raise TableSchemaError(
                f"Unsupported dimension type '{self.dimension_type}'."
            ) from exc
        if not self.name or not self.name.strip():
            raise TableSchemaError("Dimension names must be non-empty.")
        if not self.values:
            raise TableSchemaError(f"Dimension '{self.name}' has no grid values.")
        if len(set(self.values)) != len(self.values):
            raise TableSchemaError(f"Dimension '{self.name}' contains duplicate grid values.")
        if self.dimension_type == DimensionType.CATEGORICAL:
            if any(not isinstance(value, str) or not value for value in self.values):
                raise TableSchemaError(
                    f"Categorical dimension '{self.name}' requires non-empty string tokens."
                )
            allowed = _CANONICAL_CATEGORIES.get(self.name.strip().lower())
            invalid = sorted(set(self.values) - allowed) if allowed is not None else []
            if invalid:
                raise TableSchemaError(
                    f"Categorical dimension '{self.name}' contains noncanonical token(s): "
                    f"{invalid}. Allowed tokens: {sorted(allowed)}."
                )
            return
        try:
            numeric = np.asarray(self.values, dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise TableSchemaError(f"Dimension '{self.name}' must be numeric.") from exc
        if not np.all(np.isfinite(numeric)):
            raise TableSchemaError(f"Dimension '{self.name}' contains a non-finite value.")
        if np.any(np.diff(numeric) <= 0.0):
            raise TableSchemaError(f"Dimension '{self.name}' must be strictly increasing.")
        if self.dimension_type == DimensionType.INTEGER and np.any(numeric != np.floor(numeric)):
            raise TableSchemaError(f"Integer dimension '{self.name}' contains a fractional value.")


@dataclass(frozen=True)
class TableGovernanceEnvelope:
    """Immutable provenance and four-eyes approval evidence for one table version."""

    table_id: str
    version_label: str
    source_reference: str
    effective_date: str
    status: str
    created_by: str
    approved_by: str | None
    approved_at: str | None
    fingerprint_sha256: str

    def __post_init__(self) -> None:
        required = {
            "table_id": self.table_id,
            "version_label": self.version_label,
            "source_reference": self.source_reference,
            "effective_date": self.effective_date,
            "created_by": self.created_by,
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if missing:
            raise TableSchemaError(f"Governance fields may not be blank: {missing}.")
        if self.status not in {"draft", "validated", "approved"}:
            raise TableSchemaError(f"Unsupported table governance status '{self.status}'.")
        if self.approved_by is not None and self.approved_by == self.created_by:
            raise TableSchemaError("Four-eyes governance forbids self-approval.")
        if self.status == "approved" and (not self.approved_by or not self.approved_at):
            raise TableSchemaError("Approved tables require an independent approver and timestamp.")
        try:
            date.fromisoformat(self.effective_date)
        except (TypeError, ValueError) as exc:
            raise TableSchemaError("effective_date must be an ISO-8601 calendar date.") from exc
        if self.approved_at is not None:
            try:
                approved = datetime.fromisoformat(self.approved_at.replace("Z", "+00:00"))
            except (AttributeError, TypeError, ValueError) as exc:
                raise TableSchemaError("approved_at must be an ISO-8601 timestamp.") from exc
            if approved.tzinfo is None or approved.utcoffset() is None:
                raise TableSchemaError("approved_at must include an explicit UTC offset.")
        if not _SHA256.fullmatch(self.fingerprint_sha256):
            raise TableSchemaError("fingerprint_sha256 must be a lowercase SHA-256 hex digest.")


class ActuarialTable:
    """A complete immutable grid with scalar and vector lookup entry points."""

    __slots__ = (
        "_dimensions", "_values", "_governance", "_interpolation",
        "_boundary_policy", "_sealed",
    )

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("ActuarialTable instances are immutable.")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        *,
        dimensions: Sequence[TableDimension],
        values: Any,
        governance: TableGovernanceEnvelope,
        interpolation: InterpolationMethod = InterpolationMethod.STEP_FLOOR,
        boundary_policy: BoundaryPolicy = BoundaryPolicy.FAIL_CLOSED,
    ) -> None:
        self._dimensions = tuple(dimensions)
        if not self._dimensions:
            raise TableSchemaError("An actuarial table requires at least one dimension.")
        names = [dimension.name for dimension in self._dimensions]
        if len(set(names)) != len(names):
            raise TableSchemaError("Dimension names must be unique.")
        array = np.asarray(values, dtype=np.float64)
        expected_shape = tuple(len(dimension.values) for dimension in self._dimensions)
        if array.shape != expected_shape:
            raise TableSchemaError(
                f"Table values shape {array.shape} does not match grid shape {expected_shape}."
            )
        if not np.all(np.isfinite(array)):
            raise TableSchemaError("Table values may not contain NaN or infinity.")

        interpolation = InterpolationMethod(interpolation)
        boundary_policy = BoundaryPolicy(boundary_policy)
        numeric_count = sum(
            dimension.dimension_type != DimensionType.CATEGORICAL
            for dimension in self._dimensions
        )
        required_count = {
            InterpolationMethod.LINEAR: 1,
            InterpolationMethod.BILINEAR: 2,
        }.get(interpolation)
        if required_count is not None and numeric_count != required_count:
            raise TableSchemaError(
                f"{interpolation.value} requires exactly {required_count} numerical dimension(s)."
            )
        if interpolation == InterpolationMethod.MULTILINEAR and numeric_count < 1:
            raise TableSchemaError("MULTILINEAR requires at least one numerical dimension.")

        actual_fingerprint = self.compute_fingerprint(self._dimensions, array)
        if governance.fingerprint_sha256 != actual_fingerprint:
            raise TableSchemaError(
                "Governance fingerprint does not match the table coordinates and values."
            )
        # Back the array with immutable bytes.  Callers cannot re-enable WRITEABLE on this view.
        immutable = np.frombuffer(array.astype(np.float64, copy=False).tobytes(), dtype=np.float64)
        self._values = immutable.reshape(expected_shape)
        self._governance = governance
        self._interpolation = interpolation
        self._boundary_policy = boundary_policy
        self._sealed = True

    @staticmethod
    def compute_fingerprint(dimensions: Sequence[TableDimension], values: Any) -> str:
        array = np.asarray(values, dtype=np.float64)
        if not np.all(np.isfinite(array)):
            raise TableSchemaError("Table values may not contain NaN or infinity.")
        payload = {
            "dimensions": [
                {
                    "name": dimension.name,
                    "type": dimension.dimension_type.value,
                    "values": list(dimension.values),
                }
                for dimension in dimensions
            ],
            "values": array.tolist(),
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def dimensions(self) -> tuple[TableDimension, ...]:
        return self._dimensions

    @property
    def values(self) -> np.ndarray:
        return self._values

    @property
    def governance(self) -> TableGovernanceEnvelope:
        return self._governance

    @property
    def interpolation(self) -> InterpolationMethod:
        return self._interpolation

    @property
    def boundary_policy(self) -> BoundaryPolicy:
        return self._boundary_policy

    def verify_fingerprint(self) -> bool:
        return self.compute_fingerprint(self._dimensions, self._values) == (
            self._governance.fingerprint_sha256
        )

    def dimension(self, name: str) -> TableDimension:
        for dimension in self._dimensions:
            if dimension.name == name:
                return dimension
        raise KeyError(name)

    def lookup_scalar(self, **coordinates: Any) -> float:
        """Evaluate one point using the same numerical path as vector lookup."""

        nonscalar = sorted(
            name for name, value in coordinates.items() if np.asarray(value).ndim != 0
        )
        if nonscalar:
            raise TableError(
                f"lookup_scalar requires scalar coordinate values; received arrays for {nonscalar}."
            )
        vector = self.lookup_vector(
            **{name: np.asarray([value]) for name, value in coordinates.items()}
        )
        return float(vector.reshape(-1)[0])

    def lookup_vector(self, **coordinate_arrays: np.ndarray) -> np.ndarray:
        """Evaluate broadcast-compatible coordinate arrays without policy-level Python loops."""

        expected = {dimension.name for dimension in self._dimensions}
        supplied = set(coordinate_arrays)
        missing = sorted(expected - supplied)
        if missing:
            raise TableDimensionMissingError(f"Missing table coordinate(s): {missing}.")
        extra = sorted(supplied - expected)
        if extra:
            raise TableError(f"Unknown table coordinate(s): {extra}.")

        try:
            broadcast = np.broadcast_arrays(
                *(np.asarray(coordinate_arrays[d.name]) for d in self._dimensions)
            )
        except ValueError as exc:
            raise TableError("Coordinate arrays are not broadcast-compatible.") from exc
        output_shape = broadcast[0].shape
        flat_coordinates = [array.reshape(-1) for array in broadcast]

        lower_indices: list[np.ndarray] = []
        upper_indices: list[np.ndarray] = []
        weights: list[np.ndarray] = []
        numeric_positions: list[int] = []

        for position, (dimension, raw) in enumerate(zip(self._dimensions, flat_coordinates)):
            if dimension.dimension_type == DimensionType.CATEGORICAL:
                categories = np.asarray(dimension.values, dtype=object)
                matches = raw.astype(object)[:, None] == categories[None, :]
                valid = np.any(matches, axis=1)
                if not np.all(valid):
                    invalid = sorted({str(value) for value in raw[~valid]})
                    raise ValueError(
                        f"Invalid categorical value(s) for '{dimension.name}': {invalid}."
                    )
                indices = np.argmax(matches, axis=1)
                lower_indices.append(indices)
                upper_indices.append(indices)
                weights.append(np.zeros(indices.shape, dtype=np.float64))
                continue

            try:
                query = raw.astype(np.float64)
            except (TypeError, ValueError) as exc:
                raise TableError(f"Coordinate '{dimension.name}' must be numeric.") from exc
            if not np.all(np.isfinite(query)):
                raise TableError(f"Coordinate '{dimension.name}' contains NaN or infinity.")
            grid = np.asarray(dimension.values, dtype=np.float64)
            outside = (query < grid[0]) | (query > grid[-1])
            if np.any(outside) and self._boundary_policy == BoundaryPolicy.FAIL_CLOSED:
                raise TableOutOfBoundsError(
                    f"Coordinate '{dimension.name}' is outside [{grid[0]}, {grid[-1]}]."
                )
            query = np.clip(query, grid[0], grid[-1])
            numeric_positions.append(position)

            insertion = np.searchsorted(grid, query, side="right")
            lower = np.clip(insertion - 1, 0, len(grid) - 1)
            upper = np.clip(insertion, 0, len(grid) - 1)
            if self._interpolation == InterpolationMethod.STEP_FLOOR:
                upper = lower
                weight = np.zeros(query.shape, dtype=np.float64)
            elif self._interpolation == InterpolationMethod.STEP_NEAREST:
                low_distance = np.abs(query - grid[lower])
                high_distance = np.abs(grid[upper] - query)
                nearest = np.where(high_distance < low_distance, upper, lower)
                lower = nearest
                upper = nearest
                weight = np.zeros(query.shape, dtype=np.float64)
            else:
                span = grid[upper] - grid[lower]
                weight = np.divide(
                    query - grid[lower], span,
                    out=np.zeros(query.shape, dtype=np.float64), where=span != 0.0,
                )
            lower_indices.append(lower)
            upper_indices.append(upper)
            weights.append(weight)

        if self._interpolation in {
            InterpolationMethod.STEP_FLOOR, InterpolationMethod.STEP_NEAREST,
        }:
            return np.asarray(self._values[tuple(lower_indices)]).reshape(output_shape)

        result = np.zeros(flat_coordinates[0].shape, dtype=np.float64)
        for corner in itertools.product((0, 1), repeat=len(numeric_positions)):
            indices = list(lower_indices)
            contribution = np.ones(result.shape, dtype=np.float64)
            for bit, position in zip(corner, numeric_positions):
                if bit:
                    indices[position] = upper_indices[position]
                    contribution *= weights[position]
                else:
                    contribution *= 1.0 - weights[position]
            result += self._values[tuple(indices)] * contribution
        return result.reshape(output_shape)


def governed_table(
    *,
    dimensions: Sequence[TableDimension],
    values: Any,
    table_id: str,
    version_label: str,
    source_reference: str,
    effective_date: str,
    created_by: str,
    approved_by: str | None = None,
    approved_at: str | None = None,
    status: str = "validated",
    interpolation: InterpolationMethod = InterpolationMethod.STEP_FLOOR,
    boundary_policy: BoundaryPolicy = BoundaryPolicy.FAIL_CLOSED,
) -> ActuarialTable:
    """Construct a table and its matching governance fingerprint atomically."""

    fingerprint = ActuarialTable.compute_fingerprint(dimensions, values)
    envelope = TableGovernanceEnvelope(
        table_id=table_id,
        version_label=version_label,
        source_reference=source_reference,
        effective_date=effective_date,
        status=status,
        created_by=created_by,
        approved_by=approved_by,
        approved_at=approved_at,
        fingerprint_sha256=fingerprint,
    )
    return ActuarialTable(
        dimensions=dimensions,
        values=values,
        governance=envelope,
        interpolation=interpolation,
        boundary_policy=boundary_policy,
    )
