"""M3 Reconciliation Integration Test Matrix (REC-01 to REC-14)."""

import pytest
from app.db.models.reconciliation import ExpectedResultSet, ReconciliationReport
from app.services import reconciliation_service, results_service
from app.services.common import unwrap_value
from .wp1_support import Env


@pytest.fixture()
def recon_env():
    env = Env()
    env.seed("REC_A", horizon=12)
    env.seed("REC_B", horizon=12)
    submitted_a = env.submit("REC_A", scenarios=["Base"])
    env.execute(submitted_a)
    run_a_id = submitted_a["by_scenario"]["Base"]
    assert env.run(run_a_id).status == "success"

    env.editor_a = env.user("editor-a@example.com", memberships={"REC_A": "editor"})
    env.editor_b = env.user("editor-b@example.com", memberships={"REC_B": "editor"})

    yield env, run_a_id
    env.close()


def _get_monthly_actuals(env: Env, run_id: str) -> tuple[str, list[dict]]:
    with env.Session() as db:
        run = results_service.result_run(db, run_id)
        actual = results_service._accepted_rows(db, run)
    policy_id = sorted({row["policy_id"] for row in actual})[0]
    return policy_id, actual


def test_rec_01_and_02_annual_sum_for_flows_and_last_for_reserves(recon_env):
    """REC-01: Monthly net_cash_flow / expected_payment aggregates to annual SUM.
    REC-02: Monthly account_value / reserve aggregates to annual LAST (end_of_period)."""
    env, run_id = recon_env
    policy_id, actual = _get_monthly_actuals(env, run_id)

    monthly_payments = [
        float(unwrap_value(row["value"]))
        for row in actual
        if row["policy_id"] == policy_id and row["variable_name"] == "expected_payment"
    ]
    monthly_reserves = [
        float(unwrap_value(row["value"]))
        for row in actual
        if row["policy_id"] == policy_id and row["variable_name"] == "reserve"
    ]
    assert len(monthly_payments) == 12
    assert len(monthly_reserves) >= 12

    expected_annual_payment_sum = sum(monthly_payments)
    expected_annual_reserve_last = monthly_reserves[-1]  # Month 12 end of period

    # Build aggregate-year expected result row
    expected_rows = [
        {
            "projection_year": 1,
            "expected_payment": expected_annual_payment_sum,
            "reserve": expected_annual_reserve_last,
        }
    ]
    ids = env.projects["REC_A"]
    res = env.client.post(
        f"/v1/projects/{ids['project']}/expected-results",
        json={
            "name": "Annual Aggregate Benchmark",
            "product_code": "SPIA",
            "version_label": "v1",
            "granularity": "aggregate_year",
            "rows": expected_rows,
        },
        headers=env.admin_headers(),
    )
    assert res.status_code == 201, res.text
    expected_set_id = res.json()["id"]

    rec_res = env.client.post(
        f"/v1/runs/{run_id}/reconcile",
        json={
            "expected_result_set_id": expected_set_id,
            "level": "aggregate_year",
            "policy_id": policy_id,
            "abs_tol": 0.001,
            "rel_tol": 0.0001,
            "variables": ["expected_payment", "reserve"],
        },
        headers=env.admin_headers(),
    )
    assert rec_res.status_code == 201, rec_res.text
    report = rec_res.json()
    assert report["status"] == "PASS"
    assert report["summary"]["cells_failed"] == 0


def test_rec_03_aggregate_year_fails_on_unsupported_aggregation(recon_env):
    """REC-03: Aggregate-year fails when variable aggregation metadata is missing or unsupported."""
    env, run_id = recon_env
    policy_id, _ = _get_monthly_actuals(env, run_id)
    ids = env.projects["REC_A"]

    # Upload expected set with a variable that has no aggregation semantic in SPIA
    res = env.client.post(
        f"/v1/projects/{ids['project']}/expected-results",
        json={
            "name": "Invalid Aggregation Benchmark",
            "product_code": "SPIA",
            "version_label": "v1",
            "granularity": "aggregate_year",
            "rows": [{"projection_year": 1, "non_aggregated_var": 500.0}],
        },
        headers=env.admin_headers(),
    )
    assert res.status_code == 201
    exp_id = res.json()["id"]

    rec_res = env.client.post(
        f"/v1/runs/{run_id}/reconcile",
        json={
            "expected_result_set_id": exp_id,
            "level": "aggregate_year",
            "policy_id": policy_id,
        },
        headers=env.admin_headers(),
    )
    assert rec_res.status_code == 422
    assert "UNSUPPORTED_AGGREGATION" in rec_res.text


def test_rec_04_cross_project_expected_result_rejected_with_404(recon_env):
    """REC-04: Expected result set from Project B used on Run A returns 404 Not Found (no cross-project leakage)."""
    env, run_a_id = recon_env
    ids_b = env.projects["REC_B"]

    # Upload expected set in Project B
    res_b = env.client.post(
        f"/v1/projects/{ids_b['project']}/expected-results",
        json={
            "name": "Project B Benchmark",
            "product_code": "SPIA",
            "version_label": "v1",
            "granularity": "policy_month",
            "rows": [{"policy_id": "P1", "projection_month": 1, "reserve": 100.0}],
        },
        headers=env.editor_b["headers"],
    )
    assert res_b.status_code == 201
    b_expected_id = res_b.json()["id"]

    # Try to reconcile Run A (Project A) with Project B expected set
    rec_res = env.client.post(
        f"/v1/runs/{run_a_id}/reconcile",
        json={"expected_result_set_id": b_expected_id, "level": "policy_month"},
        headers=env.editor_a["headers"],
    )
    assert rec_res.status_code == 404, rec_res.text


def test_rec_05_product_mismatch_rejected(recon_env):
    """REC-05: Product mismatch between expected set and run model rejected with 422 EXPECTED_RESULTS_PRODUCT_MISMATCH."""
    env, run_id = recon_env
    ids = env.projects["REC_A"]

    res = env.client.post(
        f"/v1/projects/{ids['project']}/expected-results",
        json={
            "name": "MYGA on SPIA",
            "product_code": "MYGA",  # Run model is SPIA
            "version_label": "v1",
            "granularity": "policy_month",
            "rows": [{"policy_id": "P1", "projection_month": 1, "reserve": 100.0}],
        },
        headers=env.admin_headers(),
    )
    assert res.status_code == 201
    exp_id = res.json()["id"]

    rec_res = env.client.post(
        f"/v1/runs/{run_id}/reconcile",
        json={"expected_result_set_id": exp_id, "level": "policy_month"},
        headers=env.admin_headers(),
    )
    assert rec_res.status_code == 422
    assert "EXPECTED_RESULTS_PRODUCT_MISMATCH" in rec_res.text


def test_rec_06_timing_convention_mismatch_rejected(recon_env):
    """REC-06: Timing convention mismatch rejected with 422 EXPECTED_RESULTS_TIMING_MISMATCH."""
    env, run_id = recon_env
    ids = env.projects["REC_A"]

    res = env.client.post(
        f"/v1/projects/{ids['project']}/expected-results",
        json={
            "name": "BOM on EOM Run",
            "product_code": "SPIA",
            "version_label": "v1",
            "timing_convention": "BOM",  # Run is EOM
            "granularity": "policy_month",
            "rows": [{"policy_id": "P1", "projection_month": 1, "reserve": 100.0}],
        },
        headers=env.admin_headers(),
    )
    assert res.status_code == 201
    exp_id = res.json()["id"]

    rec_res = env.client.post(
        f"/v1/runs/{run_id}/reconcile",
        json={"expected_result_set_id": exp_id, "level": "policy_month"},
        headers=env.admin_headers(),
    )
    assert rec_res.status_code == 422
    assert "EXPECTED_RESULTS_TIMING_MISMATCH" in rec_res.text


def test_rec_07_incompatible_granularity_rejected(recon_env):
    """REC-07: Incompatible granularity rejected with 422 EXPECTED_RESULTS_GRANULARITY_MISMATCH."""
    env, run_id = recon_env
    ids = env.projects["REC_A"]

    res = env.client.post(
        f"/v1/projects/{ids['project']}/expected-results",
        json={
            "name": "Yearly on Monthly",
            "product_code": "SPIA",
            "version_label": "v1",
            "granularity": "aggregate_year",
            "rows": [{"projection_year": 1, "reserve": 100.0}],
        },
        headers=env.admin_headers(),
    )
    assert res.status_code == 201
    exp_id = res.json()["id"]

    rec_res = env.client.post(
        f"/v1/runs/{run_id}/reconcile",
        json={"expected_result_set_id": exp_id, "level": "policy_month"},
        headers=env.admin_headers(),
    )
    assert rec_res.status_code == 422
    assert "EXPECTED_RESULTS_GRANULARITY_MISMATCH" in rec_res.text


def test_rec_08_duplicate_expected_result_keys_rejected(recon_env):
    """REC-08: Duplicate expected-result keys rejected during ingestion."""
    env, _ = recon_env
    ids = env.projects["REC_A"]

    # Duplicate policy_month key (Policy A / Month 1 twice)
    res = env.client.post(
        f"/v1/projects/{ids['project']}/expected-results",
        json={
            "name": "Duplicate Keys",
            "product_code": "SPIA",
            "version_label": "v1",
            "granularity": "policy_month",
            "rows": [
                {"policy_id": "POL_01", "projection_month": 1, "reserve": 100.0},
                {"policy_id": "POL_01", "projection_month": 1, "reserve": 200.0},
            ],
        },
        headers=env.admin_headers(),
    )
    assert res.status_code == 422
    assert "EXPECTED_RESULTS_DUPLICATE_KEY" in res.text


def test_rec_09_string_identifiers_preserved(recon_env):
    """REC-09: String identifiers like '000123' must preserve string identity and never be numeric-coerced."""
    env, _ = recon_env
    ids = env.projects["REC_A"]

    res = env.client.post(
        f"/v1/projects/{ids['project']}/expected-results",
        json={
            "name": "Leading Zero ID",
            "product_code": "SPIA",
            "version_label": "v1",
            "granularity": "policy_month",
            "rows": [
                {"policy_id": "000123", "scenario_id": "000456", "projection_month": 1, "reserve": 100.0},
            ],
        },
        headers=env.admin_headers(),
    )
    assert res.status_code == 201
    exp_id = res.json()["id"]

    with env.Session() as db:
        stored = db.get(ExpectedResultSet, exp_id)
        row = stored.rows_data[0]
        assert row["policy_id"] == "000123"
        assert isinstance(row["policy_id"], str)
        assert row["scenario_id"] == "000456"
        assert isinstance(row["scenario_id"], str)


def test_rec_10_missing_source_evidence_gives_not_verifiable(recon_env):
    """REC-10: Missing source evidence returns NOT_VERIFIABLE, not PASS."""
    from app.db.models.inforce import InforceRecord
    env, run_id = recon_env
    policy_id, actual = _get_monthly_actuals(env, run_id)
    ids = env.projects["REC_A"]

    # Simulate missing source evidence by removing inforce records
    with env.Session() as db:
        db.query(InforceRecord).filter(InforceRecord.file_id == ids["inforce"]).delete()
        db.commit()

    res = env.client.post(
        f"/v1/projects/{ids['project']}/expected-results",
        json={
            "name": "Source Check Test",
            "product_code": "SPIA",
            "version_label": "v1",
            "granularity": "policy_month",
            "rows": [{"policy_id": policy_id, "projection_month": 1, "expected_payment": 100.0}],
        },
        headers=env.admin_headers(),
    )
    assert res.status_code == 201
    exp_id = res.json()["id"]

    rec_res = env.client.post(
        f"/v1/runs/{run_id}/reconcile",
        json={
            "expected_result_set_id": exp_id,
            "level": "policy_month",
            "policy_id": policy_id,
        },
        headers=env.admin_headers(),
    )
    assert rec_res.status_code == 201
    report = rec_res.json()
    source_check = report["summary"]["source_to_output_check"]
    assert source_check["status"] == "NOT_VERIFIABLE"
    assert source_check["passed"] is False
    assert source_check["not_verifiable"] is True


def test_rec_13_and_14_reviewer_disposition_workflow_rules(recon_env):
    """REC-13: FAIL reconciliation cannot be approved without exception.
    REC-14: Approved with exception records history and requires a reason."""
    env, run_id = recon_env
    policy_id, _ = _get_monthly_actuals(env, run_id)
    ids = env.projects["REC_A"]

    # Create a failing reconciliation (expected != actual)
    res = env.client.post(
        f"/v1/projects/{ids['project']}/expected-results",
        json={
            "name": "Failing Reference",
            "product_code": "SPIA",
            "version_label": "v1",
            "granularity": "policy_month",
            "rows": [{"policy_id": policy_id, "projection_month": 1, "expected_payment": 999999.0}],
        },
        headers=env.admin_headers(),
    )
    exp_id = res.json()["id"]

    rec_res = env.client.post(
        f"/v1/runs/{run_id}/reconcile",
        json={"expected_result_set_id": exp_id, "level": "policy_month", "policy_id": policy_id},
        headers=env.admin_headers(),
    )
    assert rec_res.status_code == 201, rec_res.text
    report_id = rec_res.json()["id"]
    assert rec_res.json()["status"] == "FAIL"

    # REC-13: Plain 'approved' on FAIL must be rejected
    bad_approve = env.client.post(
        f"/v1/reconciliations/{report_id}/disposition",
        json={"disposition_status": "approved"},
        headers=env.admin_headers(),
    )
    assert bad_approve.status_code == 422
    assert "RECONCILIATION_CANNOT_APPROVE_FAILED" in bad_approve.text

    # REC-14: 'approved_with_exception' requires non-empty reason
    no_reason = env.client.post(
        f"/v1/reconciliations/{report_id}/disposition",
        json={"disposition_status": "approved_with_exception", "disposition_notes": ""},
        headers=env.admin_headers(),
    )
    assert no_reason.status_code == 422
    assert "RECONCILIATION_EXCEPTION_REASON_REQUIRED" in no_reason.text

    # Valid 'approved_with_exception'
    good_approve = env.client.post(
        f"/v1/reconciliations/{report_id}/disposition",
        json={
            "disposition_status": "approved_with_exception",
            "disposition_notes": "Known discrepancy due to rounding in external benchmark",
        },
        headers=env.admin_headers(),
    )
    assert good_approve.status_code == 200
    report_data = good_approve.json()
    assert report_data["disposition_status"] == "approved_with_exception"
    assert len(report_data["disposition_history"]) >= 1
    assert report_data["disposition_history"][-1]["to_status"] == "approved_with_exception"
