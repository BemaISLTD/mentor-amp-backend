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

    actual_payment = [
        float(unwrap_value(row["value"]))
        for row in actual
        if row["policy_id"] == policy_id and int(row["projection_month"]) == 1 and row["variable_name"] == "expected_payment"
    ][0]

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
            "rows": [{"policy_id": policy_id, "projection_month": 1, "expected_payment": actual_payment}],
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
    # Item 1: Reconciliation must not return overall PASS when source_to_output_check.status == NOT_VERIFIABLE
    assert report["status"] == "NOT_VERIFIABLE"
    assert report["status"] != "PASS"

    # Disposition cannot approve a NOT_VERIFIABLE report without exception
    bad_approve = env.client.post(
        f"/v1/reconciliations/{report['id']}/disposition",
        json={"disposition_status": "approved"},
        headers=env.admin_headers(),
    )
    assert bad_approve.status_code == 422
    assert "RECONCILIATION_CANNOT_APPROVE_FAILED" in bad_approve.text


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


def test_rec_scenario_identity_binding_and_isolation(recon_env):
    """Item 2: Bind reconciliation to scenario identity.
    - Expected benchmark scenario identity matches/filters against run's frozen scenario.
    - Do not drop scenario_id from comparison identity.
    - Same policy/month under Base and Stress scenarios with different values cannot pass under wrong scenario.
    """
    env, run_base_id = recon_env
    ids = env.projects["REC_A"]

    # Submit and execute Low Interest Rate (Stress) scenario
    submitted_stress = env.submit("REC_A", scenarios=["Low Interest Rate"])
    env.execute(submitted_stress)
    run_stress_id = submitted_stress["by_scenario"]["Low Interest Rate"]
    assert env.run(run_stress_id).status == "success"

    policy_id, base_actuals = _get_monthly_actuals(env, run_base_id)
    _, stress_actuals = _get_monthly_actuals(env, run_stress_id)

    # Base vs Stress actuals for month 1 reserve/present_value
    base_val = [float(unwrap_value(r["value"])) for r in base_actuals if r["policy_id"] == policy_id and int(r["projection_month"]) == 1 and r["variable_name"] == "reserve"][0]
    stress_val = [float(unwrap_value(r["value"])) for r in stress_actuals if r["policy_id"] == policy_id and int(r["projection_month"]) == 1 and r["variable_name"] == "reserve"][0]
    assert base_val != stress_val, f"Base ({base_val}) and Stress ({stress_val}) values should differ"

    # 1. Expected results with explicit scenario_id = 'Base'
    res_base = env.client.post(
        f"/v1/projects/{ids['project']}/expected-results",
        json={
            "name": "Base Scenario Expected",
            "product_code": "SPIA",
            "granularity": "policy_month",
            "rows": [{"policy_id": policy_id, "scenario_id": "Base", "projection_month": 1, "reserve": base_val}],
        },
        headers=env.admin_headers(),
    )
    assert res_base.status_code == 201
    exp_base_id = res_base.json()["id"]

    # Reconcile Base run against Base expected set -> PASS
    rec_base = env.client.post(
        f"/v1/runs/{run_base_id}/reconcile",
        json={"expected_result_set_id": exp_base_id, "level": "policy_month", "policy_id": policy_id},
        headers=env.admin_headers(),
    )
    assert rec_base.status_code == 201
    assert rec_base.json()["status"] == "PASS"

    # Reconciling Stress run against Base expected set must be refused with 422 scenario mismatch
    rec_wrong = env.client.post(
        f"/v1/runs/{run_stress_id}/reconcile",
        json={"expected_result_set_id": exp_base_id, "level": "policy_month", "policy_id": policy_id},
        headers=env.admin_headers(),
    )
    assert rec_wrong.status_code == 422
    assert "EXPECTED_RESULTS_SCENARIO_MISMATCH" in rec_wrong.text or "scenario" in rec_wrong.text.lower()

    # 2. Multi-scenario expected set containing both Base and Stress benchmark rows
    res_multi = env.client.post(
        f"/v1/projects/{ids['project']}/expected-results",
        json={
            "name": "Multi Scenario Expected",
            "product_code": "SPIA",
            "granularity": "policy_month",
            "rows": [
                {"policy_id": policy_id, "scenario_id": "Base", "projection_month": 1, "reserve": base_val},
                {"policy_id": policy_id, "scenario_id": "Low Interest Rate", "projection_month": 1, "reserve": stress_val},
            ],
        },
        headers=env.admin_headers(),
    )
    assert res_multi.status_code == 201
    exp_multi_id = res_multi.json()["id"]

    # Base run against multi-scenario expected set matches Base row only and PASSES
    rec_multi_base = env.client.post(
        f"/v1/runs/{run_base_id}/reconcile",
        json={"expected_result_set_id": exp_multi_id, "level": "policy_month", "policy_id": policy_id},
        headers=env.admin_headers(),
    )
    assert rec_multi_base.status_code == 201
    assert rec_multi_base.json()["status"] == "PASS"

    # Stress run against multi-scenario expected set matches Stress row only and PASSES
    rec_multi_stress = env.client.post(
        f"/v1/runs/{run_stress_id}/reconcile",
        json={"expected_result_set_id": exp_multi_id, "level": "policy_month", "policy_id": policy_id},
        headers=env.admin_headers(),
    )
    assert rec_multi_stress.status_code == 201
    assert rec_multi_stress.json()["status"] == "PASS"

    # 3. Test intentionally different values under same scenario -> fails with scenario_id recorded in failure
    res_bad = env.client.post(
        f"/v1/projects/{ids['project']}/expected-results",
        json={
            "name": "Mismatch Value",
            "product_code": "SPIA",
            "granularity": "policy_month",
            "rows": [{"policy_id": policy_id, "scenario_id": "Base", "projection_month": 1, "reserve": base_val + 500.0}],
        },
        headers=env.admin_headers(),
    )
    exp_bad_id = res_bad.json()["id"]
    rec_bad = env.client.post(
        f"/v1/runs/{run_base_id}/reconcile",
        json={"expected_result_set_id": exp_bad_id, "level": "policy_month", "policy_id": policy_id},
        headers=env.admin_headers(),
    )
    assert rec_bad.status_code == 201
    bad_rep = rec_bad.json()
    assert bad_rep["status"] == "FAIL"
    assert len(bad_rep["failures"]) == 1
    assert "scenario_id" in bad_rep["failures"][0]


def test_rec_expected_results_governance_workflow(recon_env):
    """Item 4: Expected-result governance workflow (approve & supersede with four-eyes enforcement)."""
    env, run_id = recon_env
    ids = env.projects["REC_A"]
    policy_id, _ = _get_monthly_actuals(env, run_id)

    # 1. Create expected result set by editor_a
    res = env.client.post(
        f"/v1/projects/{ids['project']}/expected-results",
        json={
            "name": "Governance Workflow Benchmark",
            "product_code": "SPIA",
            "granularity": "policy_month",
            "rows": [{"policy_id": policy_id, "projection_month": 1, "expected_payment": 100.0}],
        },
        headers=env.editor_a["headers"],
    )
    assert res.status_code == 201
    exp_id = res.json()["id"]
    assert res.json()["status"] == "validated"

    # 2. Four-eyes enforcement: Creator (editor_a) cannot self-approve
    self_approve = env.client.post(
        f"/v1/expected-results/{exp_id}/approve",
        headers=env.editor_a["headers"],
    )
    assert self_approve.status_code == 403
    assert "EXPECTED_RESULTS_SELF_APPROVAL_FORBIDDEN" in self_approve.text

    # 3. Independent reviewer (admin) approves
    approve_res = env.client.post(
        f"/v1/expected-results/{exp_id}/approve",
        headers=env.admin_headers(),
    )
    assert approve_res.status_code == 200
    app_data = approve_res.json()
    assert app_data["status"] == "approved"
    assert app_data["approved_by"] == ids["admin"]
    assert app_data["approved_at"] is not None

    # Cannot approve already approved set
    dup_approve = env.client.post(
        f"/v1/expected-results/{exp_id}/approve",
        headers=env.admin_headers(),
    )
    assert dup_approve.status_code == 409
    assert "EXPECTED_RESULTS_ALREADY_APPROVED" in dup_approve.text

    # 4. Create successor expected result set (v2)
    res_v2 = env.client.post(
        f"/v1/projects/{ids['project']}/expected-results",
        json={
            "name": "Governance Workflow Benchmark v2",
            "product_code": "SPIA",
            "version_label": "v2",
            "granularity": "policy_month",
            "rows": [{"policy_id": policy_id, "projection_month": 1, "expected_payment": 101.0}],
        },
        headers=env.editor_a["headers"],
    )
    assert res_v2.status_code == 201
    v2_id = res_v2.json()["id"]

    # 5. Cannot supersede self
    self_sup = env.client.post(
        f"/v1/expected-results/{exp_id}/supersede",
        json={"superseded_by_id": exp_id},
        headers=env.admin_headers(),
    )
    assert self_sup.status_code == 422
    assert "EXPECTED_RESULTS_SELF_SUPERSEDE" in self_sup.text

    # 6. Valid supersede
    sup_res = env.client.post(
        f"/v1/expected-results/{exp_id}/supersede",
        json={"superseded_by_id": v2_id},
        headers=env.admin_headers(),
    )
    assert sup_res.status_code == 200
    sup_data = sup_res.json()
    assert sup_data["status"] == "superseded"
    assert sup_data["superseded_by_id"] == v2_id

    # 7. Superseded set cannot be approved
    sup_app = env.client.post(
        f"/v1/expected-results/{exp_id}/approve",
        headers=env.admin_headers(),
    )
    assert sup_app.status_code == 409
    assert "EXPECTED_RESULTS_ALREADY_SUPERSEDED" in sup_app.text


def test_rec_audit_snapshots_resolved_vars_and_timing(recon_env):
    """Item 5: Fix reconciliation audit snapshots (resolved variables, run timing, expected timing preserved)."""
    env, run_id = recon_env
    ids = env.projects["REC_A"]
    policy_id, _ = _get_monthly_actuals(env, run_id)

    res = env.client.post(
        f"/v1/projects/{ids['project']}/expected-results",
        json={
            "name": "Snapshot Audit Benchmark",
            "product_code": "SPIA",
            "timing_convention": "EOM",
            "granularity": "policy_month",
            "rows": [
                {"policy_id": policy_id, "projection_month": 1, "expected_payment": 100.0, "reserve": 1200.0},
            ],
        },
        headers=env.admin_headers(),
    )
    exp_id = res.json()["id"]

    # Reconcile with variables filter omitted
    rec_res = env.client.post(
        f"/v1/runs/{run_id}/reconcile",
        json={"expected_result_set_id": exp_id, "level": "policy_month", "policy_id": policy_id},
        headers=env.admin_headers(),
    )
    assert rec_res.status_code == 201
    rep = rec_res.json()

    # Must persist actual resolved variables compared, NOT []
    assert len(rep["variables_requested"]) > 0
    assert set(rep["variables_requested"]) == {"expected_payment", "reserve"}

    # Must preserve timing conventions
    assert rep["summary"]["expected_timing_convention"] == "EOM"
    assert rep["timing_convention_snapshot"] in ("EOM", None)

