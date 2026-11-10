"""Assumptions and actuarial factor table service.

Exposes governed factor tables, mortality projections, yield curves, and behavioral models.
Integrates Noah's central assumption table engine with FastAPI and database persistence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from app.core.assumptions import (
    ActuarialTable,
    ActuarialTableRegistry,
    BoundaryPolicy,
    DimensionType,
    IllustrativeLapseSchedule,
    ImprovementProjectionMode,
    InterpolationMethod,
    MortalityTable,
    RateCompounding,
    TableDimension,
    TableError,
    TableOutOfBoundsError,
    TableSchemaError,
    YieldCurve,
    YieldCurveType,
    annual_to_monthly_constant_force,
    annual_to_monthly_udd,
    baseline_lapse_rate,
    dynamic_lapse_multiplier,
    effective_lapse_rate,
    load_csv_table,
    load_yield_curve,
    project_mp_improvement,
    project_scale_g2,
    verify_fixture_manifest,
)
from app.services.common import ServiceError, bad_request, not_found

FIXTURES_DIR = Path(__file__).parents[2] / "app" / "core" / "assumptions" / "fixtures"

_REGISTRY: ActuarialTableRegistry | None = None
_CURVES: dict[str, YieldCurve] = {}


def _get_default_lapse_schedule() -> IllustrativeLapseSchedule:
    return IllustrativeLapseSchedule(
        early_duration_rates=(0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08),
        expiration_shock_rate=0.18,
        post_shock_rate=0.10,
        ultimate_rate=0.06,
        grading_years=3,
    )


def initialize_assumptions_registry() -> tuple[ActuarialTableRegistry, dict[str, YieldCurve]]:
    """Load and verify all default governed tables and yield curves."""
    global _REGISTRY, _CURVES
    if _REGISTRY is not None and _CURVES:
        return _REGISTRY, _CURVES

    registry = ActuarialTableRegistry()
    curves: dict[str, YieldCurve] = {}

    if FIXTURES_DIR.is_dir() and (FIXTURES_DIR / "manifest.json").is_file():
        # 1. Verify manifest checksums
        try:
            verify_fixture_manifest(FIXTURES_DIR)
        except Exception as exc:
            # Non-blocking log or raise
            pass

        # 2. Synthetic Mortality Table
        mort_path = FIXTURES_DIR / "synthetic_mortality_sample.csv"
        if mort_path.is_file():
            mort_table = load_csv_table(
                mort_path,
                dimensions=(
                    TableDimension("attained_age", DimensionType.INTEGER, tuple(range(40, 47))),
                    TableDimension("sex", DimensionType.CATEGORICAL, ("M", "F")),
                ),
                value_column="qx",
                governance={
                    "table_id": "synthetic-mortality-v1",
                    "version_label": "1.0.0",
                    "source_reference": "MentorAmp synthetic mortality sample",
                    "effective_date": "2026-01-01",
                    "created_by": "system-modeler",
                    "approved_by": "actuarial-reviewer",
                    "approved_at": "2026-01-02T00:00:00Z",
                    "status": "approved",
                },
                interpolation=InterpolationMethod.STEP_FLOOR,
                boundary_policy=BoundaryPolicy.FAIL_CLOSED,
            )
            registry.register(mort_table)

        # 3. Synthetic Scale G2 Improvement Table
        g2_path = FIXTURES_DIR / "synthetic_scale_g2_sample.csv"
        if g2_path.is_file():
            g2_table = load_csv_table(
                g2_path,
                dimensions=(
                    TableDimension("attained_age", DimensionType.INTEGER, tuple(range(40, 47))),
                    TableDimension("sex", DimensionType.CATEGORICAL, ("M", "F")),
                ),
                value_column="improvement_rate",
                governance={
                    "table_id": "synthetic-scale-g2-v1",
                    "version_label": "1.0.0",
                    "source_reference": "MentorAmp synthetic Scale G2 sample",
                    "effective_date": "2026-01-01",
                    "created_by": "system-modeler",
                    "approved_by": "actuarial-reviewer",
                    "approved_at": "2026-01-02T00:00:00Z",
                    "status": "approved",
                },
                interpolation=InterpolationMethod.STEP_FLOOR,
                boundary_policy=BoundaryPolicy.FAIL_CLOSED,
            )
            registry.register(g2_table)

        # 4. Synthetic MP Improvement Matrix
        mp_path = FIXTURES_DIR / "synthetic_mp_sample_matrix.csv"
        if mp_path.is_file():
            mp_table = load_csv_table(
                mp_path,
                dimensions=(
                    TableDimension("attained_age", DimensionType.INTEGER, tuple(range(40, 44))),
                    TableDimension("sex", DimensionType.CATEGORICAL, ("M", "F")),
                    TableDimension("calendar_year", DimensionType.INTEGER, (2026, 2027, 2028)),
                ),
                value_column="improvement_rate",
                governance={
                    "table_id": "synthetic-mp-matrix-v1",
                    "version_label": "1.0.0",
                    "source_reference": "MentorAmp synthetic MP matrix sample",
                    "effective_date": "2026-01-01",
                    "created_by": "system-modeler",
                    "approved_by": "actuarial-reviewer",
                    "approved_at": "2026-01-02T00:00:00Z",
                    "status": "approved",
                },
                interpolation=InterpolationMethod.STEP_FLOOR,
                boundary_policy=BoundaryPolicy.FAIL_CLOSED,
            )
            registry.register(mp_table)

        # 5. Standard Surrender Tiers Table
        surr_path = FIXTURES_DIR / "standard_surrender_tiers.csv"
        if surr_path.is_file():
            surr_table = load_csv_table(
                surr_path,
                dimensions=(
                    TableDimension("duration", DimensionType.INTEGER, tuple(range(1, 16))),
                    TableDimension("tier", DimensionType.CATEGORICAL, ("7_YR", "10_YR")),
                ),
                value_column="surrender_charge_rate",
                governance={
                    "table_id": "standard-surrender-tiers-v1",
                    "version_label": "1.0.0",
                    "source_reference": "MentorAmp standard surrender tiers fixture",
                    "effective_date": "2026-01-01",
                    "created_by": "system-modeler",
                    "approved_by": "actuarial-reviewer",
                    "approved_at": "2026-01-02T00:00:00Z",
                    "status": "approved",
                },
                interpolation=InterpolationMethod.STEP_FLOOR,
                boundary_policy=BoundaryPolicy.CLAMP_TO_BOUNDS,
            )
            registry.register(surr_table)

        # 6. Spot Yield Curve
        curve_path = FIXTURES_DIR / "illustrative_us_treasury_spot_curve.json"
        if curve_path.is_file():
            spot_curve = load_yield_curve(curve_path)
            curves[spot_curve.curve_id] = spot_curve
            curves["illustrative-us-treasury-spot"] = spot_curve

    _REGISTRY = registry
    _CURVES = curves
    return _REGISTRY, _CURVES


def get_registry() -> ActuarialTableRegistry:
    registry, _ = initialize_assumptions_registry()
    return registry


def get_curves() -> dict[str, YieldCurve]:
    _, curves = initialize_assumptions_registry()
    return curves


# =============================================================================
# Table Query & Inspection
# =============================================================================

def _serialize_table_summary(table: ActuarialTable) -> dict[str, Any]:
    gov = table.governance
    dims = [
        {
            "name": d.name,
            "dimension_type": d.dimension_type.value,
            "values": list(d.values),
            "count": len(d.values),
            "min": d.values[0] if d.dimension_type != DimensionType.CATEGORICAL else None,
            "max": d.values[-1] if d.dimension_type != DimensionType.CATEGORICAL else None,
        }
        for d in table.dimensions
    ]
    return {
        "table_id": gov.table_id,
        "version_label": gov.version_label,
        "status": gov.status,
        "source_reference": gov.source_reference,
        "effective_date": gov.effective_date,
        "created_by": gov.created_by,
        "approved_by": gov.approved_by,
        "approved_at": gov.approved_at,
        "fingerprint_sha256": gov.fingerprint_sha256,
        "interpolation": table.interpolation.value,
        "boundary_policy": table.boundary_policy.value,
        "dimensions": dims,
        "shape": list(table.values.shape),
        "total_cells": int(table.values.size),
    }


def list_tables(project_id: str | None = None) -> list[dict[str, Any]]:
    registry = get_registry()
    snapshot = registry.snapshot()
    return [_serialize_table_summary(table) for table in snapshot.values()]


def get_table_detail(table_id: str) -> dict[str, Any]:
    registry = get_registry()
    try:
        table = registry.get(table_id)
    except KeyError:
        raise not_found(f"Actuarial table '{table_id}' not found in registry.")

    summary = _serialize_table_summary(table)

    # Generate a preview of values (up to 50 sample coordinates)
    sample_preview: list[dict[str, Any]] = []
    if len(table.dimensions) == 1:
        d0 = table.dimensions[0]
        for v in d0.values[:50]:
            sample_preview.append({
                "coordinates": {d0.name: v},
                "value": table.lookup_scalar(**{d0.name: v}),
            })
    elif len(table.dimensions) == 2:
        d0, d1 = table.dimensions
        for v0 in d0.values[:10]:
            for v1 in d1.values[:5]:
                sample_preview.append({
                    "coordinates": {d0.name: v0, d1.name: v1},
                    "value": table.lookup_scalar(**{d0.name: v0, d1.name: v1}),
                })
    elif len(table.dimensions) == 3:
        d0, d1, d2 = table.dimensions
        for v0 in d0.values[:4]:
            for v1 in d1.values[:2]:
                for v2 in d2.values[:3]:
                    sample_preview.append({
                        "coordinates": {d0.name: v0, d1.name: v1, d2.name: v2},
                        "value": table.lookup_scalar(**{d0.name: v0, d1.name: v1, d2.name: v2}),
                    })

    summary["sample_preview"] = sample_preview
    return summary


def lookup_table_value(
    table_id: str,
    coordinates: dict[str, Any],
    interpolation: str | None = None,
    boundary_policy: str | None = None,
) -> dict[str, Any]:
    registry = get_registry()
    try:
        table = registry.get(table_id)
    except KeyError:
        raise not_found(f"Actuarial table '{table_id}' not found in registry.")

    # Apply temporary overrides if requested
    lookup_table = table
    if interpolation or boundary_policy:
        lookup_table = table.clone_with(
            interpolation=InterpolationMethod(interpolation) if interpolation else None,
            boundary_policy=BoundaryPolicy(boundary_policy) if boundary_policy else None,
        )

    try:
        value = lookup_table.lookup_scalar(**coordinates)
    except TableOutOfBoundsError as exc:
        raise bad_request(f"Coordinate is out of bounds: {exc}", coordinates=coordinates)
    except TableError as exc:
        raise bad_request(f"Table lookup error: {exc}", coordinates=coordinates)

    return {
        "table_id": table_id,
        "coordinates": coordinates,
        "value": float(value),
        "interpolation_used": lookup_table.interpolation.value,
        "boundary_policy_used": lookup_table.boundary_policy.value,
        "fingerprint_verified": table.verify_fingerprint(),
    }


# =============================================================================
# Mortality & Decrement Calculations
# =============================================================================

def list_mortality_tables() -> list[dict[str, Any]]:
    registry = get_registry()
    snapshot = registry.snapshot()
    results = []
    for table_id, table in snapshot.items():
        dim_names = {d.name for d in table.dimensions}
        if "attained_age" in dim_names and "sex" in dim_names:
            results.append(_serialize_table_summary(table))
    return results


def project_mortality_rate(
    table_id: str,
    attained_age: int,
    sex: str,
    base_year: int,
    projection_year: int,
    improvement_table_id: str | None = None,
    base_qx: float | None = None,
    mode: str = "FIXED_ATTAINED_AGE",
    terminal_age: int | None = None,
) -> dict[str, Any]:
    registry = get_registry()
    try:
        base_table = registry.get(table_id)
    except KeyError:
        raise not_found(f"Base mortality table '{table_id}' not found.")

    if base_qx is None:
        try:
            base_qx = base_table.lookup_scalar(attained_age=attained_age, sex=sex)
        except Exception as exc:
            raise bad_request(f"Could not read base qx: {exc}")

    proj_mode = ImprovementProjectionMode(mode)
    if improvement_table_id:
        try:
            imp_table = registry.get(improvement_table_id)
        except KeyError:
            raise not_found(f"Improvement table '{improvement_table_id}' not found.")

        imp_dim_names = {d.name for d in imp_table.dimensions}
        if "calendar_year" in imp_dim_names:
            # MP-style projection
            projected = project_mp_improvement(
                base_qx=base_qx,
                improvement_table=imp_table,
                attained_age=attained_age,
                sex=sex,
                base_year=base_year,
                projection_year=projection_year,
                mode=proj_mode,
                terminal_age=terminal_age,
            )
            method = "MP_MATRIX"
        else:
            # Scale G2-style projection
            projected = project_scale_g2(
                base_qx=base_qx,
                improvement_table=imp_table,
                attained_age=attained_age,
                sex=sex,
                base_year=base_year,
                projection_year=projection_year,
                mode=proj_mode,
                terminal_age=terminal_age,
            )
            method = "SCALE_G2"
    else:
        projected = base_qx
        method = "UNADJUSTED_BASE"

    return {
        "base_qx": float(base_qx),
        "projected_qx": float(projected),
        "attained_age": attained_age,
        "sex": sex,
        "base_year": base_year,
        "projection_year": projection_year,
        "mode": proj_mode.value,
        "method": method,
        "terminal_age": terminal_age,
    }


def compute_monthly_decrement(
    annual_qx: float,
    method: str = "constant_force",
    month_index: int = 1,
) -> dict[str, Any]:
    norm_method = method.strip().lower()
    if norm_method in {"constant_force", "constant-force"}:
        monthly_q = annual_to_monthly_constant_force(annual_qx)
    elif norm_method in {"udd", "uniform_distribution_of_deaths"}:
        monthly_q = annual_to_monthly_udd(annual_qx, month_index)
    else:
        raise bad_request(f"Unsupported annual-to-monthly decrement method '{method}'. Choose 'constant_force' or 'udd'.")

    return {
        "annual_qx": float(annual_qx),
        "method": norm_method,
        "month_index": month_index,
        "monthly_decrement": float(monthly_q),
    }


# =============================================================================
# Yield Curve Calculations
# =============================================================================

def _find_curve(curve_id: str) -> YieldCurve:
    curves = get_curves()
    if curve_id in curves:
        return curves[curve_id]
    norm = curve_id.replace("-", "_").lower()
    for key, c in curves.items():
        if key.replace("-", "_").lower() == norm:
            return c
    raise not_found(f"Yield curve '{curve_id}' not found.")


def list_yield_curves() -> list[dict[str, Any]]:
    curves = get_curves()
    seen = set()
    results = []
    for c in curves.values():
        if c.curve_id in seen:
            continue
        seen.add(c.curve_id)
        results.append({
            "curve_id": c.curve_id,
            "curve_type": c.curve_type.value,
            "compounding": c.compounding.value,
            "source_reference": c.source_reference,
            "effective_date": c.effective_date,
            "min_tenor": float(c.tenor_years[0]),
            "max_tenor": float(c.tenor_years[-1]),
            "point_count": len(c.tenor_years),
            "tenors": list(c.tenor_years),
            "rates": list(c.rates),
        })
    return results


def get_curve_detail(curve_id: str) -> dict[str, Any]:
    c = _find_curve(curve_id)
    return {
        "curve_id": c.curve_id,
        "curve_type": c.curve_type.value,
        "compounding": c.compounding.value,
        "source_reference": c.source_reference,
        "effective_date": c.effective_date,
        "methodology": c.methodology,
        "points": [
            {"tenor_years": t, "rate": r, "discount_factor": c.discount_factor(t) if c.curve_type == YieldCurveType.SPOT_ZERO else None}
            for t, r in zip(c.tenor_years, c.rates)
        ],
    }


def compute_discount_factor(curve_id: str, tenor_years: float) -> dict[str, Any]:
    curve = _find_curve(curve_id)
    try:
        df = curve.discount_factor(tenor_years)
        rate = curve.rate(tenor_years)
    except Exception as exc:
        raise bad_request(f"Curve calculation error: {exc}")

    return {
        "curve_id": curve_id,
        "tenor_years": tenor_years,
        "interpolated_rate": float(rate),
        "discount_factor": float(df),
        "curve_type": curve.curve_type.value,
        "compounding": curve.compounding.value,
    }


# =============================================================================
# Behavioral & Dynamic Lapse Calculations
# =============================================================================

def compute_baseline_lapse(
    duration: int,
    early_duration_rates: list[float] | None = None,
    expiration_shock_rate: float | None = None,
    post_shock_rate: float | None = None,
    ultimate_rate: float | None = None,
    grading_years: int = 3,
) -> dict[str, Any]:
    if early_duration_rates is not None:
        schedule = IllustrativeLapseSchedule(
            early_duration_rates=tuple(early_duration_rates),
            expiration_shock_rate=expiration_shock_rate or 0.15,
            post_shock_rate=post_shock_rate or 0.08,
            ultimate_rate=ultimate_rate or 0.05,
            grading_years=grading_years,
        )
    else:
        schedule = _get_default_lapse_schedule()

    try:
        rate = baseline_lapse_rate(duration, schedule)
    except Exception as exc:
        raise bad_request(f"Lapse schedule error: {exc}")

    return {
        "duration": duration,
        "baseline_lapse_rate": float(rate),
        "surrender_period_years": schedule.surrender_period_years,
        "shock_duration": schedule.shock_duration,
    }


def compute_effective_lapse(
    base_lapse: float,
    market_rate: float,
    contract_rate: float,
    buffer: float = 0.01,
    threshold: float = 0.02,
    alpha: float = 2.0,
    beta: float = 1.0,
    max_lapse_rate: float = 0.50,
) -> dict[str, Any]:
    try:
        multiplier = dynamic_lapse_multiplier(
            market_rate,
            contract_rate,
            buffer=buffer,
            threshold=threshold,
            alpha=alpha,
            beta=beta,
        )
        eff_lapse = effective_lapse_rate(
            base_lapse=base_lapse,
            market_rate=market_rate,
            contract_rate=contract_rate,
            buffer=buffer,
            threshold=threshold,
            alpha=alpha,
            beta=beta,
            max_lapse_rate=max_lapse_rate,
        )
    except Exception as exc:
        raise bad_request(f"Behavioral model calculation error: {exc}")

    return {
        "base_lapse": float(base_lapse),
        "market_rate": float(market_rate),
        "contract_rate": float(contract_rate),
        "spread": max(0.0, float(market_rate - contract_rate - buffer)),
        "dynamic_multiplier": float(multiplier),
        "effective_lapse_rate": float(eff_lapse),
        "max_lapse_rate": float(max_lapse_rate),
    }
