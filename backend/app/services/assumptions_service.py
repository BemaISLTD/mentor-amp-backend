"""Assumptions and actuarial factor table service.

Exposes governed factor tables, mortality projections, yield curves, and behavioral models.
Integrates Noah's central assumption table engine with FastAPI and database persistence.
Enforces fail-closed fixture verification (CONV-01), explicit synthetic data labeling (CONV-02),
project authorization boundaries (CONV-03), and zero-valued input preservation (CONV-04).
"""

from __future__ import annotations

import json
import math
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
    RegistryError,
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
_TABLE_METADATA: dict[str, dict[str, Any]] = {}
_CURVE_METADATA: dict[str, dict[str, Any]] = {}


def _get_default_lapse_schedule() -> IllustrativeLapseSchedule:
    return IllustrativeLapseSchedule(
        early_duration_rates=(0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08),
        expiration_shock_rate=0.18,
        post_shock_rate=0.10,
        ultimate_rate=0.06,
        grading_years=3,
    )


def initialize_assumptions_registry(
    fixtures_dir: Path | None = None,
    force_reload: bool = False,
) -> tuple[ActuarialTableRegistry, dict[str, YieldCurve]]:
    """Load and strictly verify all default governed tables and yield curves.

    Fail-closed invariant (CONV-01):
    Any missing, modified, malformed, or checksum-mismatched fixture raises RegistryError
    and PREVENTS partial or unverified registry publication.
    """
    global _REGISTRY, _CURVES, _TABLE_METADATA, _CURVE_METADATA
    if not force_reload and _REGISTRY is not None and _CURVES:
        return _REGISTRY, _CURVES

    target_dir = Path(fixtures_dir) if fixtures_dir is not None else FIXTURES_DIR
    if not target_dir.is_dir():
        raise RegistryError(f"Fixture directory does not exist: '{target_dir}'.")
    manifest_path = target_dir / "manifest.json"
    if not manifest_path.is_file():
        raise RegistryError(f"Fixture manifest missing at '{manifest_path}'.")

    # 1. Parse manifest strictly
    try:
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RegistryError(f"Malformed manifest.json at '{manifest_path}': {exc}") from exc

    manifest_classification = manifest_data.get("classification", "SYNTHETIC_NON_PRODUCTION")

    # 2. Strict SHA-256 verification (CONV-01: No silent exception bypass)
    # verify_fixture_manifest will raise RegistryError if any file checksum mismatches or is missing.
    verify_fixture_manifest(target_dir)

    # 3. Create isolated staging instances to guarantee zero partial publication on failure
    staging_registry = ActuarialTableRegistry()
    staging_curves: dict[str, YieldCurve] = {}
    staging_table_meta: dict[str, dict[str, Any]] = {}
    staging_curve_meta: dict[str, dict[str, Any]] = {}

    # 4. Synthetic Mortality Table
    mort_path = target_dir / "synthetic_mortality_sample.csv"
    if not mort_path.is_file():
        raise RegistryError(f"Required fixture '{mort_path.name}' is missing.")
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
            "source_reference": "MentorAmp illustrative development fixture (synthetic)",
            "effective_date": "2026-01-01",
            "created_by": "system-illustrative-seed",
            "approved_by": "demo-fixture-validator",
            "approved_at": "2026-01-02T00:00:00Z",
            "status": "approved",
        },
        interpolation=InterpolationMethod.STEP_FLOOR,
        boundary_policy=BoundaryPolicy.FAIL_CLOSED,
    )
    staging_registry.register(mort_table)
    staging_table_meta["synthetic-mortality-v1"] = {
        "is_synthetic": True,
        "is_production_approved": False,
        "classification": manifest_classification,
        "approval_authority": "SYNTHETIC_DEMO_NON_AUTHORITATIVE",
        "governance_notice": "Synthetic development fixture; not authorized for production valuation.",
        "scope": "global_reference",
        "project_id": None,
    }

    # 5. Synthetic Scale G2 Improvement Table
    g2_path = target_dir / "synthetic_scale_g2_sample.csv"
    if not g2_path.is_file():
        raise RegistryError(f"Required fixture '{g2_path.name}' is missing.")
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
            "source_reference": "MentorAmp illustrative development fixture (synthetic)",
            "effective_date": "2026-01-01",
            "created_by": "system-illustrative-seed",
            "approved_by": "demo-fixture-validator",
            "approved_at": "2026-01-02T00:00:00Z",
            "status": "approved",
        },
        interpolation=InterpolationMethod.STEP_FLOOR,
        boundary_policy=BoundaryPolicy.FAIL_CLOSED,
    )
    staging_registry.register(g2_table)
    staging_table_meta["synthetic-scale-g2-v1"] = {
        "is_synthetic": True,
        "is_production_approved": False,
        "classification": manifest_classification,
        "approval_authority": "SYNTHETIC_DEMO_NON_AUTHORITATIVE",
        "governance_notice": "Synthetic development fixture; not authorized for production valuation.",
        "scope": "global_reference",
        "project_id": None,
    }

    # 6. Synthetic MP Improvement Matrix
    mp_path = target_dir / "synthetic_mp_sample_matrix.csv"
    if not mp_path.is_file():
        raise RegistryError(f"Required fixture '{mp_path.name}' is missing.")
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
            "source_reference": "MentorAmp illustrative development fixture (synthetic)",
            "effective_date": "2026-01-01",
            "created_by": "system-illustrative-seed",
            "approved_by": "demo-fixture-validator",
            "approved_at": "2026-01-02T00:00:00Z",
            "status": "approved",
        },
        interpolation=InterpolationMethod.STEP_FLOOR,
        boundary_policy=BoundaryPolicy.FAIL_CLOSED,
    )
    staging_registry.register(mp_table)
    staging_table_meta["synthetic-mp-matrix-v1"] = {
        "is_synthetic": True,
        "is_production_approved": False,
        "classification": manifest_classification,
        "approval_authority": "SYNTHETIC_DEMO_NON_AUTHORITATIVE",
        "governance_notice": "Synthetic development fixture; not authorized for production valuation.",
        "scope": "global_reference",
        "project_id": None,
    }

    # 7. Standard Surrender Tiers Table
    surr_path = target_dir / "standard_surrender_tiers.csv"
    if not surr_path.is_file():
        raise RegistryError(f"Required fixture '{surr_path.name}' is missing.")
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
            "source_reference": "MentorAmp standard surrender tiers fixture (synthetic)",
            "effective_date": "2026-01-01",
            "created_by": "system-illustrative-seed",
            "approved_by": "demo-fixture-validator",
            "approved_at": "2026-01-02T00:00:00Z",
            "status": "approved",
        },
        interpolation=InterpolationMethod.STEP_FLOOR,
        boundary_policy=BoundaryPolicy.CLAMP_TO_BOUNDS,
    )
    staging_registry.register(surr_table)
    staging_table_meta["standard-surrender-tiers-v1"] = {
        "is_synthetic": True,
        "is_production_approved": False,
        "classification": manifest_classification,
        "approval_authority": "SYNTHETIC_DEMO_NON_AUTHORITATIVE",
        "governance_notice": "Synthetic development fixture; not authorized for production valuation.",
        "scope": "global_reference",
        "project_id": None,
    }

    # 8. Spot Yield Curve
    curve_path = target_dir / "illustrative_us_treasury_spot_curve.json"
    if not curve_path.is_file():
        raise RegistryError(f"Required fixture '{curve_path.name}' is missing.")
    spot_curve = load_yield_curve(curve_path)
    staging_curves[spot_curve.curve_id] = spot_curve
    staging_curves["illustrative-us-treasury-spot"] = spot_curve
    staging_curve_meta[spot_curve.curve_id] = {
        "is_synthetic": True,
        "is_production_approved": False,
        "classification": manifest_classification,
        "approval_authority": "SYNTHETIC_DEMO_NON_AUTHORITATIVE",
        "governance_notice": "Illustrative synthetic spot yield curve; not published US Treasury data.",
    }
    staging_curve_meta["illustrative-us-treasury-spot"] = staging_curve_meta[spot_curve.curve_id]

    # 9. Atomic publication: only update global active registry after 100% verification
    _REGISTRY = staging_registry
    _CURVES = staging_curves
    _TABLE_METADATA = staging_table_meta
    _CURVE_METADATA = staging_curve_meta

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
    meta = _TABLE_METADATA.get(gov.table_id, {
        "is_synthetic": False,
        "is_production_approved": gov.status == "approved" and gov.approved_by is not None,
        "classification": "GOVERNED_PRODUCTION" if gov.status == "approved" else "DRAFT",
        "approval_authority": gov.approved_by,
        "governance_notice": "Independently verified production assumption.",
        "scope": "global_reference",
        "project_id": None,
    })

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
        # Explicit synthetic / approval semantics (CONV-02)
        "is_synthetic": meta["is_synthetic"],
        "is_production_approved": meta["is_production_approved"],
        "classification": meta["classification"],
        "approval_authority": meta["approval_authority"],
        "governance_notice": meta["governance_notice"],
        "scope": meta["scope"],
        "project_id": meta["project_id"],
    }


def list_tables() -> list[dict[str, Any]]:
    registry = get_registry()
    snapshot = registry.snapshot()
    return [_serialize_table_summary(table) for table in snapshot.values()]


def list_project_tables(project_id: str) -> dict[str, Any]:
    """List factor tables in the context of an authorized project (CONV-03).

    Distinguishes project-owned assumptions from shared global reference data.
    """
    global_tables = list_tables()
    return {
        "project_id": project_id,
        "scope": "project",
        "project_tables": [],
        "global_reference_tables": global_tables,
        "notice": (
            f"Project '{project_id}' inherits global illustrative factor tables. "
            "No project-specific overrides are registered."
        ),
    }


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

    meta = _TABLE_METADATA.get(table_id, {})
    return {
        "table_id": table_id,
        "coordinates": coordinates,
        "value": float(value),
        "interpolation_used": lookup_table.interpolation.value,
        "boundary_policy_used": lookup_table.boundary_policy.value,
        "fingerprint_verified": table.verify_fingerprint(),
        "is_synthetic": meta.get("is_synthetic", False),
        "classification": meta.get("classification", "SYNTHETIC_NON_PRODUCTION"),
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
        "is_synthetic": True,
        "governance_notice": "Projection produced with illustrative development tables.",
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
        meta = _CURVE_METADATA.get(c.curve_id, {
            "is_synthetic": True,
            "is_production_approved": False,
            "classification": "SYNTHETIC_NON_PRODUCTION",
            "approval_authority": "SYNTHETIC_DEMO_NON_AUTHORITATIVE",
            "governance_notice": "Illustrative synthetic spot yield curve; not published US Treasury data.",
        })
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
            "is_synthetic": meta["is_synthetic"],
            "is_production_approved": meta["is_production_approved"],
            "classification": meta["classification"],
            "approval_authority": meta["approval_authority"],
            "governance_notice": meta["governance_notice"],
        })
    return results


def get_curve_detail(curve_id: str) -> dict[str, Any]:
    c = _find_curve(curve_id)
    meta = _CURVE_METADATA.get(c.curve_id, {
        "is_synthetic": True,
        "is_production_approved": False,
        "classification": "SYNTHETIC_NON_PRODUCTION",
        "approval_authority": "SYNTHETIC_DEMO_NON_AUTHORITATIVE",
        "governance_notice": "Illustrative synthetic spot yield curve; not published US Treasury data.",
    })
    return {
        "curve_id": c.curve_id,
        "curve_type": c.curve_type.value,
        "compounding": c.compounding.value,
        "source_reference": c.source_reference,
        "effective_date": c.effective_date,
        "methodology": c.methodology,
        "is_synthetic": meta["is_synthetic"],
        "is_production_approved": meta["is_production_approved"],
        "classification": meta["classification"],
        "approval_authority": meta["approval_authority"],
        "governance_notice": meta["governance_notice"],
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

    meta = _CURVE_METADATA.get(curve.curve_id, {})
    return {
        "curve_id": curve_id,
        "tenor_years": tenor_years,
        "interpolated_rate": float(rate),
        "discount_factor": float(df),
        "curve_type": curve.curve_type.value,
        "compounding": curve.compounding.value,
        "is_synthetic": meta.get("is_synthetic", True),
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
    """Calculate baseline duration lapse rate.

    CONV-04 Invariant: Explicit None checks preserve zero-valued inputs (e.g. 0.0 shock).
    """
    if duration < 1:
        raise bad_request("Policy duration must be a positive integer >= 1.")

    if early_duration_rates is not None:
        if not early_duration_rates:
            raise bad_request("early_duration_rates cannot be empty when custom schedule is provided.")
        for idx, r in enumerate(early_duration_rates):
            if not math.isfinite(r) or r < 0.0 or r > 1.0:
                raise bad_request(f"early_duration_rates[{idx}] = {r} must be finite and in [0, 1].")

        # Explicit None checks to preserve 0.0 values! (CONV-04)
        exp_shock = expiration_shock_rate if expiration_shock_rate is not None else 0.15
        post_shock = post_shock_rate if post_shock_rate is not None else 0.08
        ult_rate = ultimate_rate if ultimate_rate is not None else 0.05

        for name, val in [
            ("expiration_shock_rate", exp_shock),
            ("post_shock_rate", post_shock),
            ("ultimate_rate", ult_rate),
        ]:
            if not math.isfinite(val) or val < 0.0 or val > 1.0:
                raise bad_request(f"{name} must be finite and in [0, 1], got {val}.")

        if grading_years < 1:
            raise bad_request("grading_years must be a positive integer >= 1.")

        try:
            schedule = IllustrativeLapseSchedule(
                early_duration_rates=tuple(early_duration_rates),
                expiration_shock_rate=exp_shock,
                post_shock_rate=post_shock,
                ultimate_rate=ult_rate,
                grading_years=grading_years,
            )
        except Exception as exc:
            raise bad_request(f"Invalid lapse schedule parameters: {exc}")
    else:
        # Reject unsupported combinations (e.g. shock overrides without early duration surrender periods)
        if any(v is not None for v in [expiration_shock_rate, post_shock_rate, ultimate_rate]):
            raise bad_request(
                "Custom shock, post-shock, or ultimate rates require early_duration_rates "
                "to define the surrender-charge period."
            )
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
        "is_synthetic": True,
        "governance_notice": "Illustrative behavioral lapse schedule for architecture validation only.",
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
    for name, val in [
        ("base_lapse", base_lapse),
        ("market_rate", market_rate),
        ("contract_rate", contract_rate),
        ("buffer", buffer),
        ("threshold", threshold),
        ("alpha", alpha),
        ("beta", beta),
        ("max_lapse_rate", max_lapse_rate),
    ]:
        if not math.isfinite(val):
            raise bad_request(f"Dynamic lapse parameter '{name}' must be finite, got {val}.")

    if not 0.0 <= base_lapse <= 1.0:
        raise bad_request(f"base_lapse must be in [0, 1], got {base_lapse}.")
    if buffer < 0.0:
        raise bad_request(f"buffer must be non-negative, got {buffer}.")
    if threshold <= 0.0:
        raise bad_request(f"threshold must be positive, got {threshold}.")
    if alpha < 0.0:
        raise bad_request(f"alpha must be non-negative, got {alpha}.")
    if beta <= 0.0:
        raise bad_request(f"beta must be positive, got {beta}.")
    if not 0.0 <= max_lapse_rate <= 1.0:
        raise bad_request(f"max_lapse_rate must be in [0, 1], got {max_lapse_rate}.")

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
        "is_synthetic": True,
        "governance_notice": "Illustrative behavioral lapse model for architecture validation only.",
    }
