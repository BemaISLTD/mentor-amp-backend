"""Expected-result ingestion and tolerance reconciliation integration tests."""

import json
from pathlib import Path

import pytest

from app.services import reconciliation_service, results_service
from app.services.common import unwrap_value

from .wp1_support import Env


@pytest.fixture()
def completed_env():
    env = Env()
    env.seed("RECON", horizon=12)
    submitted = env.submit("RECON", scenarios=["Base"])
    env.execute(submitted)
    run_id = submitted["by_scenario"]["Base"]
    assert env.run(run_id).status == "success"
    yield env, run_id
    env.close()


def _expected_rows(env: Env, run_id: str) -> list[dict]:
    with env.Session() as db:
        run = results_service.result_run(db, run_id)
        actual = results_service._accepted_rows(db, run)
    policy_id = sorted({row["policy_id"] for row in actual})[0]
    rows: dict[int, dict] = {}
    for row in actual:
        if row["policy_id"] != policy_id or row["variable_name"] not in {"expected_payment", "reserve"}:
            continue
        month = int(row["projection_month"])
        rows.setdefault(month, {"policy_id": policy_id, "projection_month": month})[
            row["variable_name"]
        ] = float(unwrap_value(row["value"]))
    return [rows[month] for month in sorted(rows)]


def _upload(env: Env, rows: list[dict], name: str = "SPIA exact reference") -> dict:
    ids = env.projects["RECON"]
    response = env.client.post(
        f"/v1/projects/{ids['project']}/expected-results",
        json={"name": name, "product_code": "spia_lite", "version_label": "v1", "rows": rows},
        headers=env.admin_headers(),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_recon_01_myga_reference_columns_normalize_to_engine_outputs():
    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "myga_reference_simplified_v1.json")
        .read_text(encoding="utf-8")
    )
    rows, columns = reconciliation_service.normalize_rows(fixture["anchor_case"]["rows"])
    assert len(rows) == 60
    assert rows[0]["projection_month"] == 1
    assert set(columns) - reconciliation_service.IDENTITY_COLUMNS == {
        "account_value_open",
        "interest_credited",
        "gross_withdrawal",
        "surrender_charge_on_withdrawal",
        "net_withdrawal_benefit",
        "account_value_close",
        "mgsv_close",
        "surrender_charge_rate",
        "surrender_benefit",
        "death_benefit",
        "maintenance_expense",
        "net_cash_flow",
    }


def test_recon_upload_exact_pass_aggregate_and_disposition(completed_env):
    env, run_id = completed_env
    rows = _expected_rows(env, run_id)
    expected = _upload(env, rows)
    assert expected["row_count"] == len(rows)
    assert len(expected["fingerprint"]) == 64

    policy_id = rows[0]["policy_id"]
    response = env.client.post(
        f"/v1/runs/{run_id}/reconcile",
        json={
            "expected_result_set_id": expected["id"], "policy_id": policy_id,
            "level": "both", "abs_tol": 0.0, "rel_tol": 0.0,
        },
        headers=env.admin_headers(),
    )
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["status"] == "PASS"
    assert report["summary"]["cells_failed"] == 0
    assert report["summary"]["max_abs_diff"] == 0.0
    assert report["summary"]["source_to_output_check"]["passed"] is True

    disposition = env.client.post(
        f"/v1/reconciliations/{report['id']}/disposition",
        json={"disposition_status": "approved", "disposition_notes": "Exact match."},
        headers=env.admin_headers(),
    )
    assert disposition.status_code == 200, disposition.text
    assert disposition.json()["disposition_status"] == "approved"
    assert disposition.json()["disposition_by"] == env.projects["RECON"]["admin"]

    upload_operation = env.client.get("/openapi.json").json()["paths"][
        "/v1/projects/{project_id}/expected-results"
    ]["post"]
    content = upload_operation["requestBody"]["content"]
    assert {"application/json", "multipart/form-data"}.issubset(content)


def test_recon_01_multipart_csv_upload_normalizes_aliases(completed_env):
    env, _ = completed_env
    project_id = env.projects["RECON"]["project"]
    uploaded = env.client.post(
        f"/v1/projects/{project_id}/expected-results",
        data={
            "name": "CSV reference",
            "product_code": "spia_lite",
            "version_label": "csv-v1",
            "timing_convention": "BOM",
        },
        files={
            "file": (
                "reference.csv",
                b"policy_id,month,av_eom\nSPIA-0001,1,123.45\n",
                "text/csv",
            )
        },
        headers=env.admin_headers(),
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["source_reference"] == "reference.csv"
    assert body["columns_detected"] == ["account_value_close", "policy_id", "projection_month", "projection_year"]

    detail = env.client.get(
        f"/v1/expected-results/{body['id']}", headers=env.admin_headers()
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["rows"] == [{
        "account_value_close": 123.45,
        "policy_id": "SPIA-0001",
        "projection_month": 1,
        "projection_year": 1,
    }]


def test_recon_perturbed_value_returns_cell_failure(completed_env):
    env, run_id = completed_env
    rows = _expected_rows(env, run_id)
    changed_row = next(row for row in rows if "expected_payment" in row)
    changed_row["expected_payment"] += 1.0
    expected = _upload(env, rows, "Perturbed reference")
    response = env.client.post(
        f"/v1/runs/{run_id}/reconcile",
        json={
            "expected_result_set_id": expected["id"], "policy_id": rows[0]["policy_id"],
            "level": "policy_month", "abs_tol": 0.01, "rel_tol": 0.0,
        },
        headers=env.admin_headers(),
    )
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["status"] == "FAIL"
    failure = next(item for item in report["failures"] if item["variable"] == "expected_payment")
    assert failure["policy_id"] == rows[0]["policy_id"]
    assert failure["projection_month"] == changed_row["projection_month"]
    assert failure["abs_diff"] == pytest.approx(1.0)


def test_recon_rejects_malformed_rows_and_empty_comparison(completed_env):
    env, run_id = completed_env
    project_id = env.projects["RECON"]["project"]
    malformed = env.client.post(
        f"/v1/projects/{project_id}/expected-results",
        json={"name": "Malformed", "product_code": "spia_lite", "rows": {"month": 1}},
        headers=env.admin_headers(),
    )
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "EXPECTED_RESULTS_ROWS"

    invalid_cases = [
        (
            {"name": "Missing keys", "product_code": "spia_lite", "rows": [{"reserve": 1.0}]},
            "EXPECTED_RESULTS_KEYS",
        ),
        (
            {
                "name": "Invalid period", "product_code": "spia_lite",
                "rows": [{"policy_id": "P1", "projection_month": -1, "reserve": 1.0}],
            },
            "EXPECTED_RESULTS_PERIOD",
        ),
        (
            {
                "name": "Fractional period", "product_code": "spia_lite",
                "rows": [{"policy_id": "P1", "projection_month": 1.5, "reserve": 1.0}],
            },
            "EXPECTED_RESULTS_PERIOD",
        ),
        (
            {
                "name": "Non-finite value", "product_code": "spia_lite",
                "rows": [{"policy_id": "P1", "projection_month": 1, "reserve": "NaN"}],
            },
            "EXPECTED_RESULTS_NONFINITE",
        ),
        (
            {
                "name": "No values", "product_code": "spia_lite",
                "rows": [{"policy_id": "P1", "projection_month": 1}],
            },
            "EXPECTED_RESULTS_VALUES",
        ),
        (
            {
                "name": "Alias collision", "product_code": "spia_lite",
                "rows": [{"policy_id": "P1", "month": 1, "projection_month": 1, "reserve": 1.0}],
            },
            "EXPECTED_RESULTS_DUPLICATE_COLUMN",
        ),
    ]
    for payload, error_code in invalid_cases:
        rejected = env.client.post(
            f"/v1/projects/{project_id}/expected-results",
            json=payload,
            headers=env.admin_headers(),
        )
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["error"]["code"] == error_code

    expected = _upload(env, _expected_rows(env, run_id), "No matching variable")
    empty = env.client.post(
        f"/v1/runs/{run_id}/reconcile",
        json={
            "expected_result_set_id": expected["id"],
            "variables": ["does_not_exist"],
        },
        headers=env.admin_headers(),
    )
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "NO_COMPARABLE_EXPECTED_RESULTS"


def test_recon_disposition_requires_reviewer_permission(completed_env):
    env, run_id = completed_env
    rows = _expected_rows(env, run_id)
    expected = _upload(env, rows, "Disposition permissions")
    report = env.client.post(
        f"/v1/runs/{run_id}/reconcile",
        json={"expected_result_set_id": expected["id"], "policy_id": rows[0]["policy_id"]},
        headers=env.admin_headers(),
    ).json()
    reader = env.user(
        "reconciliation-reader@example.com", role="read_only", memberships={"RECON": "viewer"}
    )
    denied = env.client.post(
        f"/v1/reconciliations/{report['id']}/disposition",
        json={"disposition_status": "approved"},
        headers=reader["headers"],
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "RECONCILIATION_REVIEW_FORBIDDEN"
