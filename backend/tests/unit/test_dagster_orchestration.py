"""Tests for Dagster Workflow Orchestration (M5D).

Per CAS §9 and §15.8:
Verifies:
1. Complete 8-stage software-defined asset graph.
2. In-process asset materialization across the end-to-end pipeline:
   Inforce -> Validation -> Projection -> Aggregation -> Valuation ->
   Rollforward -> Reconciliation -> Reporting
3. Verification of real domain calculation outputs, Polars aggregation, and dynamic manifest SHA-256.
4. Deterministic failure trapping: actuarial assertion errors halt without retrying corrupt state (allow_retries=False).
5. Three-state reconciliation and lineage propagation.
"""

from decimal import Decimal
import hashlib
import json
from dagster import Failure, build_asset_context, materialize
import pytest

from app.orchestration.dagster.assets import (
    DEFAULT_RETRY_POLICY,
    InforceConfig,
    ProjectionConfig,
    ReconciliationConfig,
    ValuationConfig,
    aggregation_asset,
    inforce_data_asset,
    projection_run_asset,
    reconciliation_report_asset,
    reporting_asset,
    rollforward_asset,
    validation_asset,
    valuation_asset,
)
from app.orchestration.dagster.definitions import defs


def test_dagster_definitions_structure():
    """Verify that defs contains the expected 8 assets and pipeline job."""
    asset_keys = {key.path[-1] for key in defs.resolve_asset_graph().get_all_asset_keys()}
    expected_keys = {
        "inforce_data_asset",
        "validation_asset",
        "projection_run_asset",
        "aggregation_asset",
        "valuation_asset",
        "rollforward_asset",
        "reconciliation_report_asset",
        "reporting_asset",
    }
    assert expected_keys.issubset(asset_keys), f"Missing assets: {expected_keys - asset_keys}"

    job = defs.resolve_job_def("mentoramp_valuation_pipeline_job")
    assert job is not None
    assert job.name == "mentoramp_valuation_pipeline_job"


def test_dagster_end_to_end_pipeline_materialization():
    """Execute all 8 assets in dependency sequence via Dagster in-process materialization."""
    assets = [
        inforce_data_asset,
        validation_asset,
        projection_run_asset,
        aggregation_asset,
        valuation_asset,
        rollforward_asset,
        reconciliation_report_asset,
        reporting_asset,
    ]

    result = materialize(assets)
    assert result.success is True

    # Validate output events and metadata
    mat_events = result.get_asset_materialization_events()
    assert len(mat_events) == 8

    # 1. Inforce asset
    inforce_event = [e for e in mat_events if e.asset_key.path[-1] == "inforce_data_asset"][0]
    inforce_meta = inforce_event.materialization.metadata
    assert inforce_meta["record_count"].value == 3
    assert len(inforce_meta["fingerprint"].value) == 64
    assert inforce_meta["started_at"].value is not None
    assert inforce_meta["completed_at"].value is not None

    # 2. Validation asset
    val_event = [e for e in mat_events if e.asset_key.path[-1] == "validation_asset"][0]
    val_meta = val_event.materialization.metadata
    assert val_meta["validation_status"].value == "clean"
    assert val_meta["error_count"].value == 0
    assert val_meta["record_count"].value == 3

    # 3. Projection asset
    proj_event = [e for e in mat_events if e.asset_key.path[-1] == "projection_run_asset"][0]
    proj_meta = proj_event.materialization.metadata
    assert proj_meta["time_steps"].value == 12
    assert proj_meta["row_count"].value == 36  # 3 policies * 12 months
    assert len(proj_meta["artifact_fingerprint"].value) == 64

    # 4. Aggregation asset
    agg_event = [e for e in mat_events if e.asset_key.path[-1] == "aggregation_asset"][0]
    agg_meta = agg_event.materialization.metadata
    assert agg_meta["upstream_run_id"].value == proj_meta["run_id"].value
    assert agg_meta["total_cash_flow_sum"].value > 0.0
    assert agg_meta["ending_stock"].value > 0.0
    assert agg_meta["time_steps_aggregated"].value == 12

    # 5. Valuation asset
    valn_event = [e for e in mat_events if e.asset_key.path[-1] == "valuation_asset"][0]
    valn_meta = valn_event.materialization.metadata
    assert valn_meta["basis"].value == "STATUTORY"
    assert valn_meta["methodology"].value == "VM22"
    assert valn_meta["present_value_reserve"].value > 0.0

    # 6. Rollforward asset
    rf_event = [e for e in mat_events if e.asset_key.path[-1] == "rollforward_asset"][0]
    rf_meta = rf_event.materialization.metadata
    assert rf_meta["is_continuous"].value is True
    assert rf_meta["opening_balance"].value == 5000.0
    assert rf_meta["ending_balance"].value == 6000.0
    assert rf_meta["difference"].value == 0.0

    # 7. Reconciliation asset (defaults to NOT_VERIFIABLE when no external benchmark is provided)
    rec_event = [e for e in mat_events if e.asset_key.path[-1] == "reconciliation_report_asset"][0]
    rec_meta = rec_event.materialization.metadata
    assert rec_meta["reconciliation_status"].value == "NOT_VERIFIABLE"
    assert rec_meta["difference"].value == 0.0

    # 8. Reporting asset
    rep_event = [e for e in mat_events if e.asset_key.path[-1] == "reporting_asset"][0]
    rep_meta = rep_event.materialization.metadata
    assert rep_meta["rollforward_verified"].value is True
    assert rep_meta["reconciliation_status"].value == "NOT_VERIFIABLE"
    manifest_sha = rep_meta["manifest_sha256"].value
    assert len(manifest_sha) == 64


def test_dagster_failure_trapping_empty_inforce():
    """Verify that an empty inforce block fails immediately with allow_retries=False."""
    context = build_asset_context()
    empty_inforce = {"status": "ingested", "record_count": 0, "fingerprint": "empty", "policies": []}

    with pytest.raises(Failure) as exc_info:
        validation_asset(context, empty_inforce)

    assert "Inforce validation failed: Block contains 0 records" in str(exc_info.value.description)
    assert exc_info.value.allow_retries is False


def test_dagster_failure_trapping_invalid_policy_attributes():
    """Verify that corrupt policy attributes fail validation with allow_retries=False."""
    context = build_asset_context()
    corrupt_inforce = {
        "status": "ingested",
        "fingerprint": "test_corrupt_fp",
        "policies": [
            {"policy_id": "P1", "issue_age": -5, "premium": 1000.0, "account_value": 1000.0},
        ],
    }

    with pytest.raises(Failure) as exc_info:
        validation_asset(context, corrupt_inforce)

    assert "Invalid issue_age" in str(exc_info.value.description)
    assert exc_info.value.allow_retries is False


def test_dagster_reconciliation_discrepancy_and_tolerance():
    """Verify three-state reconciliation reporting on benchmark comparison."""
    context = build_asset_context()
    val_out = {
        "upstream_run_id": "run-test-rec",
        "present_value_reserve": 125000.00,
        "basis": "STATUTORY",
        "methodology": "VM22",
    }
    rf_out = {"is_continuous": True}

    # Case 1: Match within tolerance -> PASS
    cfg_pass = ReconciliationConfig(expected_benchmark=125000.005, tolerance=0.01)
    pass_res = reconciliation_report_asset(context, cfg_pass, val_out, rf_out)
    assert pass_res.value["status"] == "PASS"

    # Case 2: Discrepancy beyond tolerance -> FAIL
    cfg_fail = ReconciliationConfig(expected_benchmark=130000.00, tolerance=0.01)
    fail_res = reconciliation_report_asset(context, cfg_fail, val_out, rf_out)
    assert fail_res.value["status"] == "FAIL"
    assert fail_res.value["difference"] == 5000.00

    # Case 3: Missing benchmark -> NOT_VERIFIABLE (No fake identity pass)
    cfg_unverifiable = ReconciliationConfig(expected_benchmark=None)
    unverifiable_res = reconciliation_report_asset(context, cfg_unverifiable, val_out, rf_out)
    assert unverifiable_res.value["status"] == "NOT_VERIFIABLE"
    assert unverifiable_res.value["expected_benchmark"] is None
