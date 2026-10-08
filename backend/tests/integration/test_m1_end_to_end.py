"""Milestone 1 end-to-end: seed → Projection Set → Run Set → results → trace → comparison.

Runs the real FastAPI app in demo mode (AUTH_MODE=disabled) against an in-memory SQLite
database, so it proves the whole API contract without a database server. The same code runs on
PostgreSQL/Neon. Contract: docs/build/02_BACKEND_FRONTEND_CONTRACT.md.
"""

import importlib.util
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401 - register every table
from app.config import settings
from app.db.database import Base, get_db
from app.main import app
from app.services import run_execution_service

BACKEND = Path(__file__).resolve().parents[2]
HORIZON = 600


def _load_seed_module():
    spec = importlib.util.spec_from_file_location("seed_demo", BACKEND / "scripts" / "seed_demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def env():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(run_execution_service, "SessionLocal", TestSession)
    seed = _load_seed_module()
    monkeypatch.setattr(seed, "SessionLocal", TestSession)
    app.dependency_overrides[get_db] = override_get_db
    seed.main()
    with TestClient(app) as client:
        yield {"client": client, "session": TestSession, "seed": seed}
    app.dependency_overrides.clear()
    monkeypatch.undo()
    engine.dispose()


@pytest.fixture(autouse=True)
def demo_mode(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "disabled")


def get(client, path, **params):
    response = client.get(f"/v1{path}", params=params)
    assert response.status_code == 200, (path, response.status_code, response.text)
    return response.json()


@pytest.fixture(scope="module")
def ids(env):
    client = env["client"]
    previous_auth_mode = settings.auth_mode
    previous_app_env = settings.app_env
    settings.auth_mode = "disabled"
    settings.app_env = "test"
    try:
        project_response = client.get("/v1/projects/")
        assert project_response.status_code == 200, project_response.text
        project = next(p for p in project_response.json()["projects"]
                       if p["name"].startswith("MentorAmp Demo"))
        projection_set = client.get(f"/v1/projects/{project['id']}/projection-sets").json()["projection_sets"][0]
        submitted = client.post("/v1/run-sets", json={
            "project_id": project["id"], "name": "Test Run Set",
            "projection_set_ids": [projection_set["id"]],
        })
        assert submitted.status_code == 202, submitted.text
        body = submitted.json()
        runs = {run["scenario"]["name"]: run["id"] for run in body["runs"]}
        return {
            "project": project["id"], "projection_set": projection_set["id"],
            "run_set": body["run_set"]["id"], "base": runs["Base"], "low": runs["Low Interest Rate"],
            "submitted": body,
        }
    finally:
        settings.auth_mode = previous_auth_mode
        settings.app_env = previous_app_env


def test_demo_mode_needs_no_token(env):
    me = get(env["client"], "/auth/me")
    assert me["email"] == settings.demo_user_email and me["roles"] == ["admin"]
    status = get(env["client"], "/system/status")
    assert status["auth_mode"] == "disabled"
    assert status["database"]["status"] == "ok"
    assert status["engine"]["registered_functions"] >= 5


def test_jwt_mode_still_requires_a_token(env, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "jwt")
    response = env["client"].get("/v1/system/status")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_seed_is_idempotent_and_projection_set_is_validated(env, ids):
    env["seed"].main()
    projection_sets = get(env["client"], f"/projects/{ids['project']}/projection-sets")
    assert projection_sets["total"] == 1
    ps = projection_sets["projection_sets"][0]
    assert ps["status"] == "validated"
    assert all(check["status"] == "pass" for check in ps["validation"]["checks"])
    assert ps["counts"] == {"input_count": 2, "formula_count": 5, "scenario_count": 2, "policy_count": 25}
    assert ps["illustrative"] is True


def test_models_structure_and_formulas(env, ids):
    client = env["client"]
    models = get(client, f"/projects/{ids['project']}/models")
    assert models["total"] == 1 and models["counts"] == {"all": 1, "owned": 1, "shared": 0}
    model = models["models"][0]
    assert model["product_code"] == "SPIA" and model["current_version"]["illustrative"] is True
    version_id = model["current_version"]["id"]

    detail = get(client, f"/models/{model['id']}")
    assert len(detail["versions"]) == 1 and detail["access"][0]["role"] == "owner"

    structure = get(client, f"/model-versions/{version_id}/structure")
    assert structure["validation"]["status"] == "validated"
    assert structure["facts"]["formula_count"] == 5
    assert structure["facts"]["input_variable_count"] == 7
    assert structure["facts"]["published_output_count"] == 4
    assert [row["level"] for row in structure["hierarchy"]][:3] == ["model", "product", "basis"]

    outputs = get(client, f"/model-versions/{version_id}/published-outputs")
    aggregation = {o["variable_name"]: o["aggregation"] for o in outputs["outputs"]}
    assert aggregation["reserve"] == "end_of_period" and aggregation["expected_payment"] == "sum"

    groups = get(client, f"/model-versions/{version_id}/formula-groups")["groups"]
    formulas = groups[0]["formulas"]
    assert [f["output_variable"] for f in formulas][-1] == "pv_expected_payment"
    pv = next(f for f in formulas if f["output_variable"] == "pv_expected_payment")
    formula = get(client, f"/formulas/{pv['id']}/detail")
    assert formula["expression_text"] == "pv_expected_payment = expected_payment × discount_factor"
    assert {d["variable"] for d in formula["formula_dependencies"]} == {"expected_payment", "discount_factor"}
    q = next(f for f in formulas if f["output_variable"] == "q_monthly")
    q_detail = get(client, f"/formulas/{q['id']}/detail")
    assert q_detail["inputs"][0]["variable"] == "mortality_rate_annual"
    assert "SYNTH_MORT_GOMPERTZ_2026" in q_detail["inputs"][0]["source_summary"]


def test_inputs_mappings_and_scenarios(env, ids):
    client = env["client"]
    inputs = get(client, f"/projects/{ids['project']}/inputs")
    assert inputs["summary"]["total"] == 2 and inputs["summary"]["mapping_count"] == 3
    inforce = next(i for i in inputs["inputs"] if i["category"] == "liability_inforce")
    table = next(i for i in inputs["inputs"] if i["category"] == "assumption_table")
    assert inforce["record_count"] == 25 and table["record_count"] == 222

    detail = get(client, f"/inputs/inforce/{inforce['id']}")
    assert detail["mapping"] == {"required_count": 3, "mapped_count": 3, "status": "validated"}
    assert detail["validation"]["status"] == "validated"
    records = get(client, f"/inputs/inforce/{inforce['id']}/records", limit=10)
    assert records["total"] == 25 and len(records["records"]) == 10

    mortality = get(client, f"/inputs/assumption-tables/{table['id']}", limit=5)
    assert mortality["lookup_keys"] == ["age", "gender"] and mortality["value_column"] == "qx"
    assert get(client, f"/projects/{ids['project']}/validation-issues")["total"] == 0

    sets = get(client, f"/projects/{ids['project']}/scenario-sets")["scenario_sets"]
    scenarios = {s["name"]: s for s in sets[0]["scenarios"]}
    low = get(client, f"/scenarios/{scenarios['Low Interest Rate']['id']}")
    assert low["overrides"][0]["target_variable"] == "discount_rate_annual"
    assert low["overrides"][0]["value"] == pytest.approx(0.03)


def test_run_set_completes_with_expected_numbers(env, ids):
    client = env["client"]
    run_set = get(client, f"/run-sets/{ids['run_set']}")
    assert run_set["status"] == "success"
    assert {run["status"] for run in run_set["runs"]} == {"success"}
    assert run_set["scenario_names"] == ["Base", "Low Interest Rate"]

    run = get(client, f"/runs/{ids['base']}")
    assert run["progress"] == {"total": 25, "done": 25, "percent": 100.0, "unit": "policies", "eta_seconds": None}
    assert run["illustrative"] is True and run["manifest_fingerprint"]
    steps = get(client, f"/runs/{ids['base']}/steps")["steps"]
    assert [step["step_key"] for step in steps] == ["verify_inputs", "calculate", "finalize"]
    assert [step["status"] for step in steps] == ["success", "success", "success"]
    assert steps[1]["progress_done"] == steps[1]["progress_total"] == 25

    summary = get(client, f"/runs/{ids['base']}/summary")
    assert summary["status"] == "success" and summary["failed_policy_count"] == 0
    assert summary["output_row_count"] == 25 * 4 * HORIZON + 25
    assert summary["warning_count"] == 0
    headline = summary["headline"]["value"]
    assert headline == pytest.approx(summary["totals"]["pv_expected_payment"]["value"], rel=1e-9)
    assert summary["trace"]["policy_ids"] == ["SPIA-0001", "SPIA-0002", "SPIA-0003"]

    low = get(client, f"/runs/{ids['low']}/summary")
    assert low["headline"]["value"] > headline  # lower discount rate -> higher reserve
    assert low["totals"]["expected_payment"]["value"] == pytest.approx(
        summary["totals"]["expected_payment"]["value"], rel=1e-12
    )  # discount rate does not change undiscounted payments


def test_aggregates_export_and_results(env, ids):
    client = env["client"]
    agg = get(client, f"/runs/{ids['base']}/aggregates", variables="reserve,expected_payment")
    rows = agg["rows"]
    assert rows[0]["period_label"] == "2026 (valuation)" and rows[0]["values"]["expected_payment"] is None
    assert rows[1]["period_label"] == "2027" and rows[1]["period_end_date"] == "2027-12-31"
    assert len(rows) == HORIZON // 12 + 1
    summary = get(client, f"/runs/{ids['base']}/summary")
    assert rows[0]["values"]["reserve"] == pytest.approx(summary["headline"]["value"], rel=1e-9)
    total_payments = math.fsum(r["values"]["expected_payment"] for r in rows[1:])
    assert total_payments == pytest.approx(summary["totals"]["expected_payment"]["value"], rel=1e-9)
    assert rows[1]["change_pct"]["reserve"] < 0  # reserve runs off

    monthly = get(client, f"/runs/{ids['base']}/aggregates", group_by="projection_month", variables="reserve")
    assert len(monthly["rows"]) == HORIZON + 1

    export = client.get(f"/v1/runs/{ids['base']}/export.csv")
    assert export.status_code == 200 and export.headers["content-type"].startswith("text/csv")
    assert export.headers["x-mentoramp-illustrative"] == "true"
    assert export.text.splitlines()[0] == "period,period_label,period_end_date,reserve,expected_payment"

    results = get(client, f"/runs/{ids['base']}/results", variable="reserve", month=12)
    assert results["total"] == 25 and results["rows"][0]["projection_year"] == 1


def test_browser_can_read_the_export_completeness_header(env, ids):
    origin = settings.cors_origin_list[0]
    export = env["client"].get(f"/v1/runs/{ids['base']}/export.csv", headers={"Origin": origin})
    assert export.status_code == 200 and export.headers["x-mentoramp-complete"] == "true"
    exposed = {name.strip().lower() for name in export.headers["access-control-expose-headers"].split(",")}
    assert "x-mentoramp-complete" in exposed


def test_variable_catalog_lists_context_variables(env):
    kinds = {variable["name"]: variable["kind"] for variable in get(env["client"], "/variables/")}
    assert kinds["projection_month"] == "context" and kinds["attained_age"] == "context"


def test_trace_follows_the_formula_to_the_table_row(env, ids):
    client = env["client"]
    policies = get(client, f"/runs/{ids['base']}/trace/policies")
    assert policies["policy_ids"] == ["SPIA-0001", "SPIA-0002", "SPIA-0003"]
    trace = get(client, f"/runs/{ids['base']}/trace", policy_id="SPIA-0001", month=12,
                variable="pv_expected_payment", depth=5)
    root = trace["root"]
    assert root["kind"] == "formula"
    assert [c["variable"] for c in root["children"]] == ["discount_factor", "expected_payment"]
    expected_payment = next(c for c in root["children"] if c["variable"] == "expected_payment")
    discount = next(c for c in root["children"] if c["variable"] == "discount_factor")
    assert root["value"] == pytest.approx(expected_payment["value"] * discount["value"], rel=1e-12)

    survival = next(c for c in expected_payment["children"] if c["variable"] == "survival_probability")
    q = next(c for c in survival["children"] if c["variable"] == "q_monthly")
    mortality = q["children"][0]
    assert mortality["kind"] == "assumption"
    assert mortality["source"]["lookup_keys"] == {"age": 72, "gender": "M"}
    assert mortality["source"]["table"] == "SYNTH_MORT_GOMPERTZ_2026"
    assert mortality["value"] == pytest.approx(0.029631)
    prev = next(c for c in survival["children"] if c["variable"] == "survival_prev")
    assert prev["kind"] == "prior_output" and prev["source"]["of_month"] == 11

    low_trace = get(client, f"/runs/{ids['low']}/trace", policy_id="SPIA-0001", month=12,
                    variable="discount_factor", depth=2)
    rate = next(c for c in low_trace["root"]["children"] if c["variable"] == "discount_rate_annual")
    assert rate["value"] == pytest.approx(0.03)
    assert rate["scenario_override"]["operation"] == "set"
    assert rate["scenario_override"]["base_value"] == pytest.approx(0.045)

    reserve = get(client, f"/runs/{ids['base']}/trace", policy_id="SPIA-0001", month=0, variable="reserve")
    assert reserve["root"]["kind"] == "valuation"

    forward = get(client, f"/runs/{ids['base']}/trace/dependents", policy_id="SPIA-0001", month=12,
                  variable="survival_probability")
    assert {d["variable"] for d in forward["dependents"]} == {"expected_payment", "survival_prev"}

    missing = client.get(f"/v1/runs/{ids['base']}/trace",
                         params={"policy_id": "SPIA-0010", "month": 12, "variable": "reserve"})
    assert missing.status_code == 404
    assert missing.json()["error"]["details"]["reason"] == "TRACE_NOT_CAPTURED"


def test_comparison_names_the_changed_input(env, ids):
    comparison = get(env["client"], "/comparisons", baseline_run_id=ids["base"], current_run_id=ids["low"])
    assert comparison["same_projection_set"] is True
    assert comparison["changed_inputs"] == [{
        "variable": "discount_rate_annual", "baseline": 0.045, "current": 0.03,
        "source": "scenario override", "unit": "rate",
    }]
    reserve = next(t for t in comparison["totals"] if t["variable"] == "reserve")
    assert reserve["difference"] > 0 and reserve["label"] == "Reserve at valuation"
    assert comparison["attribution"]["available"] is True
    assert comparison["attribution"]["drivers"][0]["amount"] == pytest.approx(reserve["difference"])


def test_manifest_events_dashboard_and_run_lists(env, ids):
    client = env["client"]
    base_manifest = get(client, f"/runs/{ids['base']}/manifest")
    low_manifest = get(client, f"/runs/{ids['low']}/manifest")
    final = base_manifest["final_manifest"]["manifest"]
    assert final["runtime"]["status"] == "success"
    # The package fingerprint is the configuration identity: the scenario differs, so it differs.
    assert base_manifest["run_package"]["fingerprint"] != low_manifest["run_package"]["fingerprint"]
    assert base_manifest["final_manifest"]["fingerprint"] != base_manifest["run_package"]["fingerprint"]
    assert final["configuration"]["illustrative"] is True
    assert {d["name"] for d in final["configuration"]["datasets"]["inforce"]} == {"synthetic_spia_inforce.csv"}

    events = get(client, f"/runs/{ids['base']}/events")["events"]
    # "complete" is written with the terminal outcome; "finalized" is an informational entry after it.
    assert events[0]["step"] == "queued" and [e["step"] for e in events[-2:]] == ["complete", "finalized"]
    later = get(client, f"/runs/{ids['base']}/events", after_id=events[-3]["id"])
    assert [e["step"] for e in later["events"]] == ["complete", "finalized"]

    run_sets = get(client, f"/projects/{ids['project']}/run-sets")
    assert run_sets["stats"]["ready_projection_set_count"] == 1
    assert run_sets["run_sets"][0]["runs_by_status"] == {"success": 2}
    runs = get(client, "/runs", project_id=ids["project"], status="success")
    assert runs["total"] == 2

    dashboard = get(client, f"/projects/{ids['project']}/dashboard")
    assert dashboard["models"]["total"] == 1 and dashboard["runs"]["total"] == 2
    assert dashboard["headline"]["metric"] == "reserve"
    assert dashboard["latest_comparison"]["baseline_label"] == "Base"
    assert dashboard["latest_comparison"]["current_label"] == "Low Interest Rate"


def test_projection_set_edit_duplicate_and_submission_rules(env, ids):
    client = env["client"]
    duplicate = client.post(f"/v1/projection-sets/{ids['projection_set']}/duplicate", json={})
    assert duplicate.status_code == 201, duplicate.text
    copy = duplicate.json()
    assert copy["version_label"] == "v2" and copy["status"] == "draft"

    blocked = client.post("/v1/run-sets", json={
        "project_id": ids["project"], "name": "Should fail", "projection_set_ids": [copy["id"]],
    })
    assert blocked.status_code == 409
    assert blocked.json()["error"]["details"]["reason"] == "PROJECTION_SET_NOT_VALIDATED"

    patched = client.patch(f"/v1/projection-sets/{copy['id']}", json={"trace_scope": {"mode": "none"}})
    assert patched.status_code == 200 and patched.json()["status"] == "draft"
    validated = client.post(f"/v1/projection-sets/{copy['id']}/validate").json()
    assert validated["status"] == "needs_review"  # no trace scope is a warning, not a failure
    preflight = client.post("/v1/run-sets/preflight", json={
        "project_id": ids["project"], "projection_set_ids": [copy["id"]],
    }).json()
    assert preflight["ok"] is True and len(preflight["planned_runs"]) == 2

    bad = client.patch(f"/v1/projection-sets/{copy['id']}", json={"output_variables": ["not_a_variable"]})
    assert bad.status_code == 200
    invalid = client.post(f"/v1/projection-sets/{copy['id']}/validate").json()
    assert invalid["status"] == "draft" and invalid["validation"]["status"] == "invalid"

    unknown = client.post(f"/v1/projects/{ids['project']}/projection-sets", json={"name": ""})
    assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "VALIDATION_ERROR"
