"""Acceptance evidence for the central actuarial assumption and factor table engine."""

from __future__ import annotations

import csv
import json
import math
import os
import platform
import time
import tracemalloc
from pathlib import Path

import numpy as np
import pytest

from app.core.assumptions import (
    ActuarialTable,
    ActuarialTableRegistry,
    BehaviorModelError,
    BoundaryPolicy,
    CurveMethodologyError,
    DimensionType,
    IllustrativeLapseSchedule,
    ImprovementProjectionMode,
    InterpolationMethod,
    MortalityMethodError,
    MortalityTable,
    RateCompounding,
    RegistryError,
    TableDimension,
    TableDimensionMissingError,
    TableError,
    TableGovernanceEnvelope,
    TableOutOfBoundsError,
    TableSchemaError,
    YieldCurve,
    YieldCurveType,
    annual_to_monthly_constant_force,
    annual_to_monthly_udd,
    baseline_lapse_rate,
    dynamic_lapse_multiplier,
    effective_lapse_rate,
    governed_table,
    load_csv_table,
    load_yield_curve,
    project_mp_improvement,
    project_scale_g2,
    verify_fixture_manifest,
)


FIXTURES = Path(__file__).parents[2] / "app" / "core" / "assumptions" / "fixtures"


def linux_hardware_value(path: str, key: str) -> str | None:
    source = Path(path)
    if not source.is_file():
        return None
    for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
        name, separator, value = line.partition(":")
        if separator and name.strip() == key:
            return value.strip()
    return None


def approved_table(
    dimensions,
    values,
    *,
    interpolation=InterpolationMethod.STEP_FLOOR,
    boundary=BoundaryPolicy.FAIL_CLOSED,
    table_id="test-table-v1",
):
    return governed_table(
        dimensions=dimensions,
        values=values,
        table_id=table_id,
        version_label="1.0.0",
        source_reference="Synthetic unit-test grid",
        effective_date="2026-01-01",
        created_by="modeler-a",
        approved_by="reviewer-b",
        approved_at="2026-01-02T00:00:00Z",
        status="approved",
        interpolation=interpolation,
        boundary_policy=boundary,
    )


def test_linear_interpolation_and_scalar_vector_tolerance_equivalence():
    dimensions = (
        TableDimension("tenor", DimensionType.CONTINUOUS, (1.0, 2.0)),
    )
    table = approved_table(dimensions, [0.03, 0.05], interpolation=InterpolationMethod.LINEAR)
    assert table.lookup_scalar(tenor=1.5) == pytest.approx(0.04)

    rng = np.random.default_rng(20261105)
    points = rng.uniform(1.0, 2.0, 1_000)
    vector = table.lookup_vector(tenor=points)
    scalar = np.asarray([table.lookup_scalar(tenor=float(point)) for point in points])
    np.testing.assert_allclose(vector, scalar, rtol=0.0, atol=1e-12)


def test_bilinear_interpolation_matches_affine_surface():
    dimensions = (
        TableDimension("age", DimensionType.CONTINUOUS, (40.0, 50.0)),
        TableDimension("duration", DimensionType.CONTINUOUS, (1.0, 5.0)),
    )
    # f(age, duration) = 2*age + 3*duration
    values = np.asarray([[83.0, 95.0], [103.0, 115.0]])
    table = approved_table(dimensions, values, interpolation=InterpolationMethod.BILINEAR)
    assert table.lookup_scalar(age=45.0, duration=3.0) == pytest.approx(99.0)


def test_multilinear_interpolation_across_three_axes():
    dimensions = (
        TableDimension("x", DimensionType.CONTINUOUS, (0.0, 1.0)),
        TableDimension("y", DimensionType.CONTINUOUS, (0.0, 1.0)),
        TableDimension("z", DimensionType.CONTINUOUS, (0.0, 1.0)),
    )
    values = np.empty((2, 2, 2))
    for x in range(2):
        for y in range(2):
            for z in range(2):
                values[x, y, z] = x + 2 * y + 4 * z
    table = approved_table(dimensions, values, interpolation=InterpolationMethod.MULTILINEAR)
    assert table.lookup_scalar(x=0.25, y=0.5, z=0.75) == pytest.approx(4.25)


def test_step_floor_and_step_nearest_are_distinct():
    dimension = (TableDimension("duration", DimensionType.INTEGER, (1, 2, 3)),)
    floor = approved_table(dimension, [0.10, 0.08, 0.06])
    nearest = approved_table(
        dimension,
        [0.10, 0.08, 0.06],
        interpolation=InterpolationMethod.STEP_NEAREST,
        table_id="nearest-v1",
    )
    assert floor.lookup_scalar(duration=1.75) == pytest.approx(0.10)
    assert nearest.lookup_scalar(duration=1.75) == pytest.approx(0.08)


def test_boundary_policies_fail_closed_and_clamp():
    dimension = (TableDimension("age", DimensionType.INTEGER, (40, 50)),)
    fail = approved_table(dimension, [0.01, 0.02])
    with pytest.raises(TableOutOfBoundsError):
        fail.lookup_scalar(age=39)
    clamp = approved_table(
        dimension,
        [0.01, 0.02],
        boundary=BoundaryPolicy.CLAMP_TO_BOUNDS,
        table_id="clamp-v1",
    )
    assert clamp.lookup_scalar(age=39) == pytest.approx(0.01)
    assert clamp.lookup_scalar(age=55) == pytest.approx(0.02)


def test_missing_and_invalid_categorical_coordinates_fail_closed():
    dimensions = (
        TableDimension("age", DimensionType.INTEGER, (40, 41)),
        TableDimension("sex", DimensionType.CATEGORICAL, ("M", "F")),
    )
    table = approved_table(dimensions, [[0.01, 0.008], [0.011, 0.009]])
    with pytest.raises(TableDimensionMissingError):
        table.lookup_scalar(age=40)
    with pytest.raises(ValueError, match="Invalid categorical"):
        table.lookup_scalar(age=40, sex="UNKNOWN")
    with pytest.raises(ValueError, match="Invalid categorical"):
        table.lookup_scalar(age=40, sex="m")


def test_semantic_categories_are_canonical_and_dimension_type_strings_are_normalized():
    dimension = TableDimension("sex", "CATEGORICAL", ("M", "F"))
    assert dimension.dimension_type is DimensionType.CATEGORICAL
    with pytest.raises(TableSchemaError, match="noncanonical"):
        TableDimension("sex", DimensionType.CATEGORICAL, ("X",))
    with pytest.raises(TableSchemaError, match="noncanonical"):
        TableDimension("smoker_status", DimensionType.CATEGORICAL, ("UNKNOWN",))


def test_scalar_lookup_rejects_array_coordinates_instead_of_discarding_values():
    table = approved_table(
        (TableDimension("duration", DimensionType.INTEGER, (1, 2)),),
        [0.02, 0.03],
    )
    with pytest.raises(TableError, match="requires scalar"):
        table.lookup_scalar(duration=np.asarray([1, 2]))


def test_table_schema_rejects_incomplete_shapes_nonfinite_values_and_bad_method_dimensions():
    age = (TableDimension("age", DimensionType.INTEGER, (40, 41)),)
    fingerprint = ActuarialTable.compute_fingerprint(age, [0.01, 0.02])
    envelope = TableGovernanceEnvelope(
        table_id="bad-v1",
        version_label="1",
        source_reference="synthetic",
        effective_date="2026-01-01",
        status="approved",
        created_by="a",
        approved_by="b",
        approved_at="2026-01-02T00:00:00Z",
        fingerprint_sha256=fingerprint,
    )
    with pytest.raises(TableSchemaError, match="shape"):
        ActuarialTable(dimensions=age, values=[0.01], governance=envelope)
    with pytest.raises(TableSchemaError, match="NaN"):
        approved_table(age, [0.01, np.nan], table_id="nan-v1")
    with pytest.raises(TableSchemaError, match="exactly 2"):
        approved_table(age, [0.01, 0.02], interpolation=InterpolationMethod.BILINEAR)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("effective_date", "2026-13-40", "effective_date"),
        ("approved_at", "not-a-timestamp", "approved_at"),
        ("approved_at", "2026-01-02T00:00:00", "explicit UTC offset"),
    ],
)
def test_governance_dates_require_valid_iso_values_and_approved_timestamp_timezone(
    field, value, message
):
    kwargs = {
        "table_id": "bad-date-v1",
        "version_label": "1",
        "source_reference": "synthetic",
        "effective_date": "2026-01-01",
        "status": "approved",
        "created_by": "a",
        "approved_by": "b",
        "approved_at": "2026-01-02T00:00:00Z",
        "fingerprint_sha256": "0" * 64,
    }
    kwargs[field] = value
    with pytest.raises(TableSchemaError, match=message):
        TableGovernanceEnvelope(**kwargs)


def test_annual_to_monthly_constant_force_is_mathematically_exact():
    qx = 0.12
    expected = 1.0 - (1.0 - qx) ** (1.0 / 12.0)
    assert annual_to_monthly_constant_force(qx) == pytest.approx(expected, abs=1e-15)
    assert annual_to_monthly_constant_force(0.0) == 0.0
    assert annual_to_monthly_constant_force(1.0) == 1.0


def test_udd_is_month_varying_and_compounds_to_annual_survival():
    qx = 0.12
    rates = np.asarray([annual_to_monthly_udd(qx, month) for month in range(1, 13)])
    assert np.all(np.diff(rates) > 0.0)
    assert math.prod(1.0 - rate for rate in rates) == pytest.approx(1.0 - qx, abs=1e-15)
    with pytest.raises(MortalityMethodError):
        annual_to_monthly_udd(qx, 0)
    with pytest.raises(MortalityMethodError, match="integer"):
        annual_to_monthly_udd(qx, 1.5)


def test_configurable_terminal_age_is_validated_against_selected_table():
    dimensions = (
        TableDimension("attained_age", DimensionType.INTEGER, (44, 45, 46)),
        TableDimension("sex", DimensionType.CATEGORICAL, ("M", "F")),
    )
    table = approved_table(dimensions, [[0.02, 0.015], [0.03, 0.022], [0.04, 0.03]])
    mortality = MortalityTable(table, terminal_age=46)
    assert mortality.qx(45, "M") == pytest.approx(0.03)
    assert mortality.qx(46, "M") == 1.0
    assert mortality.px(47, "F") == 0.0
    with pytest.raises(MortalityMethodError, match="not present"):
        MortalityTable(table, terminal_age=120)
    with pytest.raises(MortalityMethodError, match="integer"):
        MortalityTable(table, terminal_age=46.0)
    with pytest.raises(MortalityMethodError, match="integer"):
        mortality.qx(45.5, "M")


def test_scale_g2_fixed_age_and_generational_projection_are_distinct():
    dimensions = (
        TableDimension("attained_age", DimensionType.INTEGER, (40, 41, 42)),
        TableDimension("sex", DimensionType.CATEGORICAL, ("M",)),
    )
    table = approved_table(dimensions, [[0.01], [0.02], [0.03]])
    fixed = project_scale_g2(
        0.10, table, attained_age=40, sex="M", base_year=2025, projection_year=2027,
        mode=ImprovementProjectionMode.FIXED_ATTAINED_AGE,
    )
    generation = project_scale_g2(
        0.10, table, attained_age=40, sex="M", base_year=2025, projection_year=2027,
        mode=ImprovementProjectionMode.GENERATIONAL,
    )
    assert fixed == pytest.approx(0.10 * 0.99 * 0.99)
    assert generation == pytest.approx(0.10 * 0.98 * 0.97)


@pytest.mark.parametrize(
    "coordinates",
    [
        {"attained_age": 40.5, "base_year": 2025, "projection_year": 2026},
        {"attained_age": 40, "base_year": 2025.0, "projection_year": 2026},
        {"attained_age": 40, "base_year": 2025, "projection_year": 2026.0},
        {
            "attained_age": 40,
            "base_year": 2025,
            "projection_year": 2026,
            "terminal_age": 42.0,
        },
    ],
)
def test_mortality_projection_rejects_fractional_or_float_integer_coordinates(coordinates):
    table = approved_table(
        (
            TableDimension("attained_age", DimensionType.INTEGER, (40, 41, 42)),
            TableDimension("sex", DimensionType.CATEGORICAL, ("M",)),
        ),
        [[0.01], [0.02], [0.03]],
    )
    with pytest.raises(MortalityMethodError, match="integer"):
        project_scale_g2(0.10, table, sex="M", **coordinates)


def test_mp_matrix_fixed_age_and_generational_projection_use_calendar_year():
    dimensions = (
        TableDimension("attained_age", DimensionType.INTEGER, (40, 41, 42)),
        TableDimension("sex", DimensionType.CATEGORICAL, ("M",)),
        TableDimension("calendar_year", DimensionType.INTEGER, (2026, 2027)),
    )
    values = np.asarray([
        [[0.010, 0.011]],
        [[0.020, 0.021]],
        [[0.030, 0.031]],
    ])
    table = approved_table(dimensions, values)
    fixed = project_mp_improvement(
        0.10, table, attained_age=40, sex="M", base_year=2025, projection_year=2027,
    )
    generation = project_mp_improvement(
        0.10, table, attained_age=40, sex="M", base_year=2025, projection_year=2027,
        mode=ImprovementProjectionMode.GENERATIONAL,
    )
    assert fixed == pytest.approx(0.10 * (1.0 - 0.010) * (1.0 - 0.011))
    assert generation == pytest.approx(0.10 * (1.0 - 0.020) * (1.0 - 0.031))


def test_generational_terminal_age_safeguard_overrides_improvement():
    dimensions = (
        TableDimension("attained_age", DimensionType.INTEGER, (40, 41, 42)),
        TableDimension("sex", DimensionType.CATEGORICAL, ("M",)),
    )
    table = approved_table(dimensions, [[0.01], [0.01], [0.01]])
    assert project_scale_g2(
        0.10,
        table,
        attained_age=40,
        sex="M",
        base_year=2025,
        projection_year=2027,
        mode=ImprovementProjectionMode.GENERATIONAL,
        terminal_age=42,
    ) == 1.0


def test_illustrative_lapse_schedule_has_exact_expiration_shock_and_grading():
    schedule = IllustrativeLapseSchedule(
        early_duration_rates=(0.02,) * 7,
        expiration_shock_rate=0.20,
        post_shock_rate=0.08,
        ultimate_rate=0.06,
        grading_years=2,
    )
    values = baseline_lapse_rate(np.asarray([1, 7, 8, 9, 10, 15]), schedule)
    np.testing.assert_allclose(values, [0.02, 0.02, 0.20, 0.07, 0.06, 0.06])


def test_lapse_schedule_rejects_fractional_grading_period():
    with pytest.raises(BehaviorModelError, match="positive integer"):
        IllustrativeLapseSchedule(
            early_duration_rates=(0.02,),
            expiration_shock_rate=0.20,
            post_shock_rate=0.08,
            ultimate_rate=0.06,
            grading_years=1.5,
        )


def test_dynamic_lapse_multiplier_buffer_ceiling_and_vectorization():
    markets = np.asarray([0.04, 0.06, 0.10])
    multiplier = dynamic_lapse_multiplier(
        markets, 0.04, buffer=0.005, threshold=0.01, alpha=1.5, beta=1.2
    )
    assert multiplier[0] == 1.0
    effective = effective_lapse_rate(
        0.05,
        markets,
        0.04,
        buffer=0.005,
        threshold=0.01,
        alpha=1.5,
        beta=1.2,
        max_lapse_rate=0.50,
    )
    assert effective[-1] <= 0.50
    with pytest.raises(BehaviorModelError):
        dynamic_lapse_multiplier(0.05, 0.04, buffer=0.0, threshold=0.0, alpha=1.0, beta=1.0)


def test_table_array_is_immutable_and_self_approval_is_rejected():
    dimension = (TableDimension("duration", DimensionType.INTEGER, (1, 2)),)
    table = approved_table(dimension, [0.02, 0.03])
    with pytest.raises(ValueError):
        table.values[0] = 0.99
    with pytest.raises(ValueError):
        table.values.setflags(write=True)
    with pytest.raises(AttributeError, match="immutable"):
        table._governance = table.governance
    with pytest.raises(TableSchemaError, match="self-approval"):
        TableGovernanceEnvelope(
            table_id="self-approved",
            version_label="1",
            source_reference="synthetic",
            effective_date="2026-01-01",
            status="approved",
            created_by="same-user",
            approved_by="same-user",
            approved_at="2026-01-02T00:00:00Z",
            fingerprint_sha256="0" * 64,
        )


def test_table_tampering_is_rejected_by_governance_fingerprint():
    dimension = (TableDimension("duration", DimensionType.INTEGER, (1, 2)),)
    original = approved_table(dimension, [0.02, 0.03])
    with pytest.raises(TableSchemaError, match="fingerprint"):
        ActuarialTable(
            dimensions=dimension,
            values=[0.02, 0.031],
            governance=original.governance,
        )


def test_registry_accepts_only_approved_unique_verified_tables():
    dimension = (TableDimension("duration", DimensionType.INTEGER, (1, 2)),)
    approved = approved_table(dimension, [0.02, 0.03])
    draft = governed_table(
        dimensions=dimension,
        values=[0.02, 0.03],
        table_id="draft-v1",
        version_label="1",
        source_reference="synthetic",
        effective_date="2026-01-01",
        created_by="modeler-a",
        status="validated",
    )
    registry = ActuarialTableRegistry()
    registry.register(approved)
    assert registry.get(approved.governance.table_id) is approved
    with pytest.raises(RegistryError, match="already registered"):
        registry.register(approved)
    with pytest.raises(RegistryError, match="approved"):
        registry.register(draft)
    with pytest.raises(TypeError):
        registry.snapshot()["new"] = approved


def test_par_yields_cannot_be_used_as_spot_rates_without_validated_methodology():
    par = YieldCurve(
        curve_id="synthetic-par",
        curve_type=YieldCurveType.PAR,
        tenor_years=(1.0, 2.0),
        rates=(0.04, 0.045),
        source_reference="Synthetic par-yield fixture",
        effective_date="2026-01-01",
    )
    with pytest.raises(CurveMethodologyError, match="Par yields"):
        par.discount_factor(1.0)
    with pytest.raises(CurveMethodologyError, match="validated"):
        par.bootstrap_to_spot(lambda tenors, rates: rates, methodology="", validated=False)
    spot = par.bootstrap_to_spot(
        lambda tenors, rates: np.asarray([0.039, 0.043]),
        methodology="Unit-test bootstrap with independently verified cash-flow equations",
        validated=True,
    )
    assert spot.curve_type == YieldCurveType.SPOT_ZERO
    assert spot.discount_factor(2.0) == pytest.approx((1.0 + 0.043) ** -2.0)


def test_spot_curve_interpolation_and_compounding_are_explicit():
    spot = YieldCurve(
        curve_id="synthetic-spot",
        curve_type=YieldCurveType.SPOT_ZERO,
        tenor_years=(1.0, 2.0),
        rates=(0.03, 0.05),
        source_reference="Synthetic spot fixture",
        effective_date="2026-01-01",
        compounding=RateCompounding.CONTINUOUS,
    )
    assert spot.rate(1.5) == pytest.approx(0.04)
    assert spot.discount_factor(1.5) == pytest.approx(math.exp(-0.04 * 1.5))


def test_yield_curve_rejects_unknown_semantics_and_normalizes_valid_enum_strings():
    common = {
        "curve_id": "synthetic-spot",
        "tenor_years": (1.0, 2.0),
        "rates": (0.03, 0.05),
        "source_reference": "Synthetic spot fixture",
        "effective_date": "2026-01-01",
    }
    normalized = YieldCurve(
        **common,
        curve_type="SPOT_ZERO",
        compounding="SIMPLE",
    )
    assert normalized.curve_type is YieldCurveType.SPOT_ZERO
    assert normalized.compounding is RateCompounding.SIMPLE
    assert normalized.discount_factor(1.0) == pytest.approx(1.0 / 1.03)
    with pytest.raises(CurveMethodologyError, match="curve type"):
        YieldCurve(**common, curve_type="UNKNOWN")
    with pytest.raises(CurveMethodologyError, match="compounding"):
        YieldCurve(**common, curve_type="SPOT_ZERO", compounding="UNKNOWN")


def test_fixture_manifest_checksums_and_synthetic_classification():
    verified = verify_fixture_manifest(FIXTURES)
    assert len(verified) == 5
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["classification"] == "SYNTHETIC_NON_PRODUCTION"
    curve = json.loads(
        (FIXTURES / "illustrative_us_treasury_spot_curve.json").read_text(encoding="utf-8")
    )
    assert curve["classification"] == "SYNTHETIC_NON_PRODUCTION"
    assert "not published US Treasury data" in curve["source_reference"]
    typed_curve = load_yield_curve(FIXTURES / "illustrative_us_treasury_spot_curve.json")
    assert typed_curve.curve_type == YieldCurveType.SPOT_ZERO
    assert typed_curve.discount_factor(5.0) == pytest.approx((1.0 + 0.0390) ** -5.0)


def test_synthetic_fixture_equations_are_reproducible():
    with (FIXTURES / "synthetic_mortality_sample.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        mortality = list(csv.DictReader(handle))
    for sex, starting in (("M", 0.00120), ("F", 0.00090)):
        rows = [row for row in mortality if row["sex"] == sex]
        expected = starting
        for row in rows[:-1]:
            assert float(row["qx"]) == pytest.approx(expected, abs=5e-12)
            expected = round(expected * 1.10, 5)
        assert int(rows[-1]["attained_age"]) == 46
        assert float(rows[-1]["qx"]) == 1.0

    with (FIXTURES / "synthetic_scale_g2_sample.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        for row in csv.DictReader(handle):
            age = int(row["attained_age"])
            expected = 0.0120 - 0.0002 * (age - 40) + (0.0010 if row["sex"] == "F" else 0.0)
            assert float(row["improvement_rate"]) == pytest.approx(expected, abs=1e-12)

    with (FIXTURES / "synthetic_mp_sample_matrix.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        for row in csv.DictReader(handle):
            age = int(row["attained_age"])
            year = int(row["calendar_year"])
            expected = (
                0.0120 - 0.0002 * (age - 40) - 0.0001 * (year - 2026)
                + (0.0010 if row["sex"] == "F" else 0.0)
            )
            assert float(row["improvement_rate"]) == pytest.approx(expected, abs=1e-12)

    with (FIXTURES / "standard_surrender_tiers.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        for row in csv.DictReader(handle):
            term = int(row["tier"].split("_")[0])
            duration = int(row["duration"])
            expected = max(0.0, (term - duration + 1) * 0.01)
            assert float(row["surrender_charge_rate"]) == pytest.approx(expected, abs=1e-12)


def test_csv_loader_rejects_incomplete_grid_and_loads_complete_fixture(tmp_path):
    dimensions = (
        TableDimension("attained_age", DimensionType.INTEGER, tuple(range(40, 47))),
        TableDimension("sex", DimensionType.CATEGORICAL, ("M", "F")),
    )
    governance = {
        "table_id": "synthetic-mortality-v1",
        "version_label": "1.0.0",
        "source_reference": "MentorAmp synthetic mortality sample",
        "effective_date": "2026-01-01",
        "created_by": "modeler-a",
        "approved_by": "reviewer-b",
        "approved_at": "2026-01-02T00:00:00Z",
        "status": "approved",
    }
    table = load_csv_table(
        FIXTURES / "synthetic_mortality_sample.csv",
        dimensions=dimensions,
        value_column="qx",
        governance=governance,
    )
    assert table.lookup_scalar(attained_age=42, sex="F") == pytest.approx(0.00109)

    incomplete = tmp_path / "incomplete.csv"
    incomplete.write_text("attained_age,sex,qx\n40,M,0.01\n", encoding="utf-8")
    with pytest.raises(TableSchemaError, match="incomplete"):
        load_csv_table(
            incomplete,
            dimensions=dimensions,
            value_column="qx",
            governance={**governance, "table_id": "incomplete-v1"},
        )


def test_vectorized_100000_lookup_benchmark_records_factual_evidence():
    dimensions = (
        TableDimension("age", DimensionType.CONTINUOUS, (20.0, 60.0, 100.0)),
        TableDimension("duration", DimensionType.CONTINUOUS, (1.0, 10.0, 20.0)),
    )
    values = np.asarray([
        [0.001, 0.002, 0.003],
        [0.010, 0.020, 0.030],
        [0.100, 0.200, 0.300],
    ])
    table = approved_table(dimensions, values, interpolation=InterpolationMethod.BILINEAR)
    rng = np.random.default_rng(42)
    ages = rng.uniform(20.0, 100.0, 100_000)
    durations = rng.uniform(1.0, 20.0, 100_000)
    tracemalloc.start()
    started = time.perf_counter()
    result = table.lookup_vector(age=ages, duration=durations)
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    evidence = {
        "points": result.size,
        "elapsed_seconds": elapsed,
        "peak_tracemalloc_bytes": peak,
        "os": platform.platform(),
        "cpu": (
            platform.processor()
            or linux_hardware_value("/proc/cpuinfo", "model name")
            or "not reported"
        ),
        "logical_cpus": os.cpu_count(),
        "memory_total": (
            linux_hardware_value("/proc/meminfo", "MemTotal") or "not reported"
        ),
    }
    print(json.dumps(evidence, sort_keys=True))
    assert result.shape == (100_000,)
    assert np.all(np.isfinite(result))
    assert elapsed > 0.0
