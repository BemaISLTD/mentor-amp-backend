"""Product-aware Data Manager validation for MYGA inforce files."""

import pytest

from .test_data_manager import data_manager  # noqa: F401 - shared isolated API fixture


MYGA_FIELDS = (
    "policy_id", "product_type", "issue_date", "issue_age", "gender", "premium",
    "guarantee_period_years", "declared_rate", "gmir", "gmsv_rate",
    "mva_ref_yield_at_issue", "account_value", "mgsv_value", "free_wd_base",
)


def _validate(client, project_id: str, csv: str, fields: tuple[str, ...] = MYGA_FIELDS):
    opened = client.post("/v1/import-sessions", json={
        "project_id": project_id,
        "category": "liability_inforce",
        "name": "MYGA product schema",
        "options": {"valuation_date": "2026-12-31"},
    })
    assert opened.status_code == 201, opened.text
    session_id = opened.json()["id"]
    uploaded = client.post(
        f"/v1/import-sessions/{session_id}/files",
        files={"file": ("myga.csv", csv.encode(), "text/csv")},
    )
    assert uploaded.status_code == 200, uploaded.text
    mapped = client.put(
        f"/v1/import-sessions/{session_id}/mapping",
        json={"fields": [
            {"source_column": field, "target_field": field} for field in fields
        ]},
    )
    assert mapped.status_code == 200, mapped.text
    response = client.post(f"/v1/import-sessions/{session_id}/validate")
    assert response.status_code == 201, response.text
    validation = response.json()
    issues = client.get(f"/v1/validation-runs/{validation['id']}/issues")
    assert issues.status_code == 200, issues.text
    validation["issues"] = issues.json()["issues"]
    return session_id, validation


def test_myga_schema_accepts_account_value_fields_without_spia_payment(data_manager):
    client, _db, project, _current, _creator, _reviewer = data_manager
    csv = (
        ",".join(MYGA_FIELDS) + "\n"
        "MYGA-001,MYGA,2026-01-01,60,M,100000,5,0.0445,0.0025,0.028,"
        "0.05,100000,87500,10000\n"
    )

    session_id, validation = _validate(client, project.id, csv)

    assert validation["error_count"] == 0, validation
    committed = client.post(f"/v1/import-sessions/{session_id}/commit")
    assert committed.status_code == 201, committed.text
    assert committed.json()["row_count"] == 1


def test_myga_schema_reports_missing_myga_field_not_monthly_payment(data_manager):
    client, _db, project, _current, _creator, _reviewer = data_manager
    fields = tuple(field for field in MYGA_FIELDS if field != "declared_rate")
    values = {
        "policy_id": "MYGA-002", "product_type": "MYGA", "issue_date": "2026-01-01",
        "issue_age": "60", "gender": "F", "premium": "100000",
        "guarantee_period_years": "5", "gmir": "0.0025", "gmsv_rate": "0.028",
        "mva_ref_yield_at_issue": "0.05", "account_value": "100000",
        "mgsv_value": "87500", "free_wd_base": "10000",
    }
    csv = ",".join(fields) + "\n" + ",".join(values[field] for field in fields) + "\n"

    _session_id, validation = _validate(client, project.id, csv, fields)

    assert validation["error_count"] > 0
    errors = validation["issues"]
    assert any(item["code"] == "REQUIRED_FIELD_NOT_MAPPED"
               and item["column_name"] == "declared_rate" for item in errors)
    assert not any(item["column_name"] == "monthly_payment" for item in errors)


def test_unknown_inforce_product_is_rejected_explicitly(data_manager):
    client, _db, project, _current, _creator, _reviewer = data_manager
    common = ("policy_id", "product_type", "issue_date", "issue_age", "gender", "premium")
    csv = ",".join(common) + "\nUNKNOWN-1,UNKNOWN,2026-01-01,45,M,1000\n"

    _session_id, validation = _validate(client, project.id, csv, common)

    assert any(item["code"] == "INVALID_PRODUCT_TYPE" for item in validation["issues"])


@pytest.mark.parametrize(("field", "value", "code"), [
    ("gmsv_rate", "0.001", "GMSV_RATE_BELOW_MINIMUM"),
    ("guarantee_period_years", "0", "INVALID_GUARANTEE_PERIOD"),
    ("guarantee_period_years", "5.5", "TYPE_MISMATCH"),
    ("account_value", "-100", "NEGATIVE_AMOUNT"),
    ("account_value", "nan", "VALUE_NOT_FINITE"),
])
def test_myga_rejects_invalid_contract_values(data_manager, field, value, code):
    client, _db, project, _current, _creator, _reviewer = data_manager
    item = dict(zip(MYGA_FIELDS, (
        "MYGA-INVALID", "MYGA", "2026-01-01", "60", "M", "100000", "5",
        "0.0445", "0.0025", "0.028", "0.05", "100000", "87500", "100000",
    )))
    item[field] = value
    csv = ",".join(MYGA_FIELDS) + "\n" + ",".join(item[name] for name in MYGA_FIELDS) + "\n"

    _session_id, validation = _validate(client, project.id, csv)

    assert validation["error_count"] > 0
    assert any(issue["code"] == code and issue["severity"] == "error"
               for issue in validation["issues"])
