"""Rebuild the SPIA reference valuation from uploaded files only."""

import pytest

from app.db.models.projection import ProjectionSet
from app.services import data_manager_service

from .wp1_support import BACKEND, Env


@pytest.fixture()
def env():
    environment = Env()
    environment.seed("A", horizon=600)
    environment.monkeypatch.setattr(
        data_manager_service, "get_artifact_store", lambda: environment.artifact_store,
    )
    yield environment
    environment.close()


def import_dataset(
    env: Env,
    reviewer_headers: dict,
    *,
    category: str,
    name: str,
    filename: str,
    content: bytes,
    fields: list[dict],
    options: dict | None = None,
) -> dict:
    headers = env.admin_headers()
    project_id = env.projects["A"]["project"]
    opened = env.client.post("/v1/import-sessions", headers=headers, json={
        "project_id": project_id, "category": category, "name": name,
        "options": options or {},
    })
    assert opened.status_code == 201, opened.text
    session_id = opened.json()["id"]
    uploaded = env.client.post(
        f"/v1/import-sessions/{session_id}/files", headers=headers,
        files={"file": (filename, content)},
    )
    assert uploaded.status_code == 200, uploaded.text
    mapped = env.client.put(
        f"/v1/import-sessions/{session_id}/mapping", headers=headers, json={"fields": fields},
    )
    assert mapped.status_code == 200, mapped.text
    validated = env.client.post(f"/v1/import-sessions/{session_id}/validate", headers=headers)
    assert validated.status_code == 201, validated.text
    assert validated.json()["error_count"] == 0, validated.text
    committed = env.client.post(f"/v1/import-sessions/{session_id}/commit", headers=headers)
    assert committed.status_code == 201, committed.text
    dataset = committed.json()
    approved = env.client.post(
        f"/v1/datasets/{dataset['kind']}/{dataset['id']}/approve",
        headers=reviewer_headers, json={"reason": "Independent file-rebuild review"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    return approved.json()


def test_uploaded_files_reproduce_the_spia_golden_reserves(env):
    ids = env.projects["A"]
    reviewer = env.user(
        "file-reviewer@example.com", role="reviewer", memberships={"A": "viewer"},
    )
    seeded_run_set = env.submit("A", ["Base", "Low Interest Rate"])
    env.execute(seeded_run_set)
    seeded_base = env.summary(seeded_run_set["by_scenario"]["Base"])["headline"]["value"]
    seeded_low = env.summary(
        seeded_run_set["by_scenario"]["Low Interest Rate"]
    )["headline"]["value"]
    inforce_path = BACKEND.parent / "samples" / "spia" / "synthetic_spia_inforce.csv"
    mortality_path = BACKEND.parent / "samples" / "spia" / "synthetic_mortality_gompertz.csv"
    inforce_fields = [{"source_column": field, "target_field": field} for field in (
        "policy_id", "product_type", "issue_date", "issue_age", "gender", "premium",
        "monthly_payment",
    )]
    table_fields = [{"source_column": field, "target_field": field}
                    for field in ("age", "gender", "qx")]
    scenario_fields = [{"source_column": field, "target_field": field} for field in (
        "scenario_name", "target_variable", "operation", "value",
        "applies_from_period", "applies_to_period",
    )]
    inforce = import_dataset(
        env, reviewer["headers"], category="liability_inforce", name="SPIA file rebuild",
        filename=inforce_path.name, content=inforce_path.read_bytes(), fields=inforce_fields,
        options={"valuation_date": "2026-12-31", "expected_record_count": 25},
    )
    mortality = import_dataset(
        env, reviewer["headers"], category="assumption_table",
        name="SYNTH_MORT_GOMPERTZ_2026", filename=mortality_path.name,
        content=mortality_path.read_bytes(), fields=table_fields,
        options={
            "table_type": "mortality", "lookup_keys": ["age", "gender"],
            "value_column": "qx",
        },
    )
    scenario_csv = (
        "scenario_name,target_variable,operation,value,applies_from_period,applies_to_period\n"
        "Low Interest Rate (file),discount_rate_annual,set,0.03,0,1200\n"
    ).encode()
    scenario = import_dataset(
        env, reviewer["headers"], category="scenario", name="Low Interest Rate (file)",
        filename="low_interest_rate.csv", content=scenario_csv, fields=scenario_fields,
        options={"model_version_id": ids["version"]},
    )

    with env.Session() as db:
        source = db.get(ProjectionSet, ids["projection_set"])
        parameters = dict(source.parameters or {})
        output_variables = list(source.output_variables or [])
    created = env.client.post(
        f"/v1/projects/{ids['project']}/projection-sets", headers=env.admin_headers(), json={
            "name": "SPIA uploaded-file valuation",
            "model_version_id": ids["version"],
            "inforce_file_ids": [inforce["id"]],
            "assumption_table_ids": [mortality["id"]],
            "factor_table_ids": [],
            "scenario_ids": [ids["scenarios"]["Base"], scenario["id"]],
            "valuation_date": "2026-12-31",
            "horizon_months": 600,
            "time_step": "monthly",
            "output_variables": output_variables,
            "trace_scope": {"mode": "selected_policies", "policy_ids": ["SPIA-0001"]},
            "parameters": parameters,
        },
    )
    assert created.status_code == 201, created.text
    projection_set_id = created.json()["id"]
    validation = env.client.post(
        f"/v1/projection-sets/{projection_set_id}/validate", headers=env.admin_headers(),
    )
    assert validation.status_code == 200, validation.text
    assert validation.json()["status"] == "validated", validation.text

    submitted = env.client.post("/v1/run-sets", headers=env.admin_headers(), json={
        "project_id": ids["project"], "name": "Uploaded-file golden run",
        "projection_set_ids": [projection_set_id],
        "scenario_ids": [ids["scenarios"]["Base"], scenario["id"]],
    })
    assert submitted.status_code == 202, submitted.text
    runs = {item["scenario"]["name"]: item["id"] for item in submitted.json()["runs"]}
    base = env.summary(runs["Base"])
    low = env.summary(runs["Low Interest Rate (file)"])
    base_rows = env.get(
        f"/runs/{runs['Base']}/aggregates", variables="reserve", month_from=0, month_to=0,
    ).json()["rows"]
    low_rows = env.get(
        f"/runs/{runs['Low Interest Rate (file)']}/aggregates",
        variables="reserve", month_from=0, month_to=0,
    ).json()["rows"]
    # The file-only rebuild must be bit-identical to the correction-pass seed path in the same
    # runtime. The published decimal renderings can differ by a few ULPs across libm versions.
    assert base_rows[0]["values"]["reserve"] == seeded_base
    assert low_rows[0]["values"]["reserve"] == seeded_low
    assert seeded_base == pytest.approx(4889731.244841259, rel=0, abs=5e-9)
    assert seeded_low == pytest.approx(5541437.408305269, rel=0, abs=5e-9)
    assert base["headline"]["value"] == base_rows[0]["values"]["reserve"]
    assert low["headline"]["value"] == low_rows[0]["values"]["reserve"]

    package = env.get(f"/runs/{runs['Base']}/package").json()["package"]["configuration"]
    assert [item["id"] for item in package["datasets"]["inforce"]] == [inforce["id"]]
    assert [item["id"] for item in package["datasets"]["assumption_tables"]] == [mortality["id"]]
