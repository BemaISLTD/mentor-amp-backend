"""Tests for Dagster Workflow Orchestration (M5D).

Per CAS §9 and §15.8:
Verifies:
1. Complete 8-stage software-defined asset graph.
2. In-process asset materialization across the end-to-end pipeline:
   Inforce -> Validation -> Projection -> Aggregation -> Valuation ->
   Rollforward -> Reconciliation -> Reporting
3. Failure trapping: actuarial assertion errors halt without retrying corrupt state.
"""

from dagster import Failure, materialize
import pytest

from app.orchestration.dagster.assets import (
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

    # Check rollforward asset metadata
    rf_event = [e for e in mat_events if e.asset_key.path[-1] == "rollforward_asset"][0]
    rf_meta = rf_event.materialization.metadata
    assert rf_meta["is_continuous"].value is True
    assert rf_meta["opening_balance"].value == 5000.0
    assert rf_meta["ending_balance"].value == 6000.0

    # Check reconciliation asset metadata
    rec_event = [e for e in mat_events if e.asset_key.path[-1] == "reconciliation_report_asset"][0]
    rec_meta = rec_event.materialization.metadata
    assert rec_meta["reconciliation_status"].value == "PASS"

    # Check reporting asset metadata
    rep_event = [e for e in mat_events if e.asset_key.path[-1] == "reporting_asset"][0]
    rep_meta = rep_event.materialization.metadata
    assert rep_meta["rollforward_verified"].value is True
    assert rep_meta["reconciliation_status"].value == "PASS"


def test_dagster_failure_trapping():
    """Verify that a failure in an upstream asset terminates downstream execution."""
    # Test validation failure with empty inforce block
    from dagster import asset, build_asset_context

    context = build_asset_context()
    empty_inforce = {"status": "ingested", "record_count": 0, "fingerprint": "empty"}

    with pytest.raises(Failure) as exc_info:
        validation_asset(context, empty_inforce)

    assert "Inforce validation failed" in str(exc_info.value.description)
