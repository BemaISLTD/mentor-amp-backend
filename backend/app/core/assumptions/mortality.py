"""Mortality decrements, terminal-age controls, and improvement projections.

This module intentionally separates annual-to-monthly decrement methodology from
mortality-improvement methodology.  It also distinguishes a fixed-attained-age period
projection from a generational projection in which attained age advances with calendar
time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from numbers import Integral

from .table_engine import ActuarialTable, DimensionType, TableDimension, TableSchemaError


class MortalityMethodError(ValueError):
    """Invalid mortality rate, method parameter, or projection coordinate."""


class ImprovementProjectionMode(str, Enum):
    FIXED_ATTAINED_AGE = "FIXED_ATTAINED_AGE"
    GENERATIONAL = "GENERATIONAL"


def _required_dimension(
    table: ActuarialTable,
    name: str,
    dimension_type: DimensionType,
) -> TableDimension:
    try:
        dimension = table.dimension(name)
    except KeyError as exc:
        raise TableSchemaError(f"Mortality table lacks {name!r} dimension.") from exc
    if dimension.dimension_type != dimension_type:
        raise TableSchemaError(
            f"Mortality table dimension '{name}' must be {dimension_type.value}."
        )
    return dimension


def _validate_categorical_token(table: ActuarialTable, name: str, value: str) -> None:
    dimension = _required_dimension(table, name, DimensionType.CATEGORICAL)
    if value not in dimension.values:
        raise ValueError(f"Invalid categorical value for '{name}': {value!r}.")


def _validate_projection_contract(
    table: ActuarialTable,
    *,
    sex: str,
    terminal_age: int | None,
) -> None:
    age_dimension = _required_dimension(table, "attained_age", DimensionType.INTEGER)
    _validate_categorical_token(table, "sex", sex)
    if terminal_age is not None and terminal_age not in age_dimension.values:
        raise MortalityMethodError(
            f"Terminal age {terminal_age} is not present in the selected table grid "
            f"[{age_dimension.values[0]}, {age_dimension.values[-1]}]."
        )


def _strict_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise MortalityMethodError(f"{label} must be an integer.")
    return int(value)


def _validate_probability(qx: float) -> float:
    value = float(qx)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise MortalityMethodError("Annual mortality qx must be finite and in [0, 1].")
    return value


def annual_to_monthly_constant_force(qx: float) -> float:
    """Convert annual ``qx`` using a constant force of mortality.

    With ``p_x = 1 - q_x`` and ``mu_x = -log(p_x)``, monthly survival is
    ``exp(-mu_x / 12) = p_x ** (1/12)``.  The resulting monthly decrement is
    constant across the twelve months.
    """

    annual = _validate_probability(qx)
    if annual == 1.0:
        return 1.0
    return -math.expm1(math.log1p(-annual) / 12.0)


def annual_to_monthly_udd(qx: float, month_index: int) -> float:
    """Convert annual ``qx`` under Uniform Distribution of Deaths (UDD).

    ``month_index`` is one-based in ``1..12``.  Conditional on survival to the
    start of month ``k``, the decrement is ``(qx/12) / (1 - (k-1)qx/12)``.
    Unlike constant force, this conditional rate increases through the year.
    """

    annual = _validate_probability(qx)
    month = _strict_integer(month_index, "month_index")
    if not 1 <= month <= 12:
        raise MortalityMethodError("month_index must be an integer in 1..12.")
    denominator = 1.0 - ((month - 1) / 12.0) * annual
    return (annual / 12.0) / denominator


@dataclass(frozen=True)
class MortalityTable:
    """A governed mortality table plus its configurable terminal age.

    The terminal age must be represented by the selected table's integer age grid.
    This prevents a caller from choosing an omega beyond the table's verified scope.
    At and above omega, ``qx`` is forced to one without extrapolation.
    """

    table: ActuarialTable
    terminal_age: int
    age_dimension: str = "attained_age"
    sex_dimension: str = "sex"

    def __post_init__(self) -> None:
        terminal_age = _strict_integer(self.terminal_age, "terminal_age")
        object.__setattr__(self, "terminal_age", terminal_age)
        try:
            age = self.table.dimension(self.age_dimension)
        except KeyError as exc:
            raise TableSchemaError(
                f"Mortality table lacks age dimension '{self.age_dimension}'."
            ) from exc
        if age.dimension_type != DimensionType.INTEGER:
            raise TableSchemaError("Mortality table age dimension must be INTEGER.")
        if self.terminal_age not in age.values:
            raise MortalityMethodError(
                f"Terminal age {self.terminal_age} is not present in the selected table grid "
                f"[{age.values[0]}, {age.values[-1]}]."
            )
        try:
            sex = self.table.dimension(self.sex_dimension)
        except KeyError as exc:
            raise TableSchemaError(
                f"Mortality table lacks sex dimension '{self.sex_dimension}'."
            ) from exc
        if sex.dimension_type != DimensionType.CATEGORICAL:
            raise TableSchemaError("Mortality table sex dimension must be CATEGORICAL.")

    def qx(self, attained_age: int, sex: str, **additional_coordinates: object) -> float:
        attained_age = _strict_integer(attained_age, "attained_age")
        _validate_categorical_token(self.table, self.sex_dimension, sex)
        if attained_age >= self.terminal_age:
            return 1.0
        value = self.table.lookup_scalar(
            **{
                self.age_dimension: attained_age,
                self.sex_dimension: sex,
                **additional_coordinates,
            }
        )
        return _validate_probability(value)

    def px(self, attained_age: int, sex: str, **additional_coordinates: object) -> float:
        return 1.0 - self.qx(attained_age, sex, **additional_coordinates)


def _validate_projection_years(base_year: int, projection_year: int) -> tuple[int, int]:
    base_year = _strict_integer(base_year, "base_year")
    projection_year = _strict_integer(projection_year, "projection_year")
    if projection_year < base_year:
        raise MortalityMethodError("projection_year may not precede base_year.")
    return base_year, projection_year


def _projection_age(
    attained_age: int,
    base_year: int,
    calendar_year: int,
    mode: ImprovementProjectionMode,
) -> int:
    if mode == ImprovementProjectionMode.FIXED_ATTAINED_AGE:
        return attained_age
    # A life aged x at the base-year valuation is aged x+1 during the next
    # calendar-year improvement interval, x+2 during the following interval, etc.
    return attained_age + calendar_year - base_year


def _apply_improvement(qx: float, rate: float) -> float:
    if not math.isfinite(rate) or not -1.0 < rate < 1.0:
        raise MortalityMethodError("Mortality improvement rates must be finite and in (-1, 1).")
    return min(1.0, max(0.0, qx * (1.0 - rate)))


def project_scale_g2(
    base_qx: float,
    improvement_table: ActuarialTable,
    *,
    attained_age: int,
    sex: str,
    base_year: int,
    projection_year: int,
    mode: ImprovementProjectionMode = ImprovementProjectionMode.FIXED_ATTAINED_AGE,
    terminal_age: int | None = None,
) -> float:
    """Apply a 1D age/sex Scale-G2-style improvement vector.

    Fixed-attained-age mode uses the same age vector element in every calendar
    interval. Generational mode advances attained age with each interval.
    """

    qx = _validate_probability(base_qx)
    attained_age = _strict_integer(attained_age, "attained_age")
    if terminal_age is not None:
        terminal_age = _strict_integer(terminal_age, "terminal_age")
    base, target = _validate_projection_years(base_year, projection_year)
    mode = ImprovementProjectionMode(mode)
    _validate_projection_contract(
        improvement_table,
        sex=sex,
        terminal_age=terminal_age,
    )
    if terminal_age is not None and attained_age >= terminal_age:
        return 1.0
    for year in range(base + 1, target + 1):
        age = _projection_age(attained_age, base, year, mode)
        if terminal_age is not None and age >= terminal_age:
            return 1.0
        rate = improvement_table.lookup_scalar(attained_age=age, sex=sex)
        qx = _apply_improvement(qx, rate)
    return qx


def project_mp_improvement(
    base_qx: float,
    improvement_table: ActuarialTable,
    *,
    attained_age: int,
    sex: str,
    base_year: int,
    projection_year: int,
    mode: ImprovementProjectionMode = ImprovementProjectionMode.FIXED_ATTAINED_AGE,
    terminal_age: int | None = None,
) -> float:
    """Apply an age/sex/calendar-year MP-style improvement matrix product."""

    qx = _validate_probability(base_qx)
    attained_age = _strict_integer(attained_age, "attained_age")
    if terminal_age is not None:
        terminal_age = _strict_integer(terminal_age, "terminal_age")
    base, target = _validate_projection_years(base_year, projection_year)
    mode = ImprovementProjectionMode(mode)
    _validate_projection_contract(
        improvement_table,
        sex=sex,
        terminal_age=terminal_age,
    )
    if terminal_age is not None and attained_age >= terminal_age:
        return 1.0
    for year in range(base + 1, target + 1):
        age = _projection_age(attained_age, base, year, mode)
        if terminal_age is not None and age >= terminal_age:
            return 1.0
        rate = improvement_table.lookup_scalar(
            attained_age=age, sex=sex, calendar_year=year
        )
        qx = _apply_improvement(qx, rate)
    return qx
