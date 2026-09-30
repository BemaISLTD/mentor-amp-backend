"""Work Package 1 — project-scoped authorization.

H. A user with access to project A only cannot read any object of project B, even with the
   generic projects:read permission and B's exact IDs; lists never include B's objects.
I. A run submission (or Projection Set edit) cannot bring in another project's scenario or data.
"""

import pytest

from app.db.models.formula import FormulaRegistry
from app.db.models.run import Run
from app.db.models.scenario import ScenarioTable
from app.db.models.variable import VariableRegistry

from .wp1_support import Env


@pytest.fixture(scope="module")
def env():
    environment = Env()
    environment.seed("A")
    environment.seed("B")
    for key in ("A", "B"):
        environment.execute(environment.submit(key, ["Base", "Low Interest Rate"]))
    with environment.Session() as db:
        for key in ("A", "B"):
            ids = environment.projects[key]
            runs = db.query(Run).filter_by(project_id=ids["project"]).all()
            ids["runs"] = {run.name.split(" · ")[-1]: run.id for run in runs}
            ids["run_set"] = runs[0].run_set_id
            ids["formula"] = (
                db.query(FormulaRegistry).filter_by(model_version_id=ids["version"]).first().id
            )
    environment.viewer_a = environment.user("viewer-a@example.com", memberships={"A": "viewer"})
    environment.editor_a = environment.user("editor-a@example.com", memberships={"A": "editor"})
    yield environment
    environment.close()


def object_paths(ids: dict) -> list[str]:
    run = ids["runs"]["Base"]
    return [
        f"/projects/{ids['project']}",
        f"/projects/{ids['project']}/models",
        f"/projects/{ids['project']}/inputs",
        f"/projects/{ids['project']}/projection-sets",
        f"/projects/{ids['project']}/scenario-sets",
        f"/projects/{ids['project']}/run-sets",
        f"/projects/{ids['project']}/dashboard",
        f"/models/{ids['model']}",
        f"/model-versions/{ids['version']}/structure",
        f"/model-versions/{ids['version']}/formula-groups",
        f"/formulas/{ids['formula']}/detail",
        f"/formulas/{ids['formula']}",
        f"/projection-sets/{ids['projection_set']}",
        f"/scenarios/{ids['scenarios']['Base']}",
        f"/inputs/inforce/{ids['inforce']}",
        f"/inputs/inforce/{ids['inforce']}/records",
        f"/inputs/assumption-tables/{ids['table']}",
        f"/run-sets/{ids['run_set']}",
        f"/runs/{run}",
        f"/runs/{run}/summary",
        f"/runs/{run}/results",
        f"/runs/{run}/aggregates",
        f"/runs/{run}/export.csv",
        f"/runs/{run}/events",
        f"/runs/{run}/attempts",
        f"/runs/{run}/manifest",
        f"/runs/{run}/package",
        f"/runs/{run}/trace/policies",
        f"/runs/{run}/trace?policy_id=SPIA-0001&month=1&variable=expected_payment",
    ]


def test_member_of_a_can_read_every_object_of_a(env):
    for path in object_paths(env.projects["A"]):
        response = env.client.get(f"/v1{path}", headers=env.viewer_a["headers"])
        assert response.status_code == 200, (path, response.status_code, response.text)


def test_member_of_a_cannot_read_any_object_of_b(env):
    for path in object_paths(env.projects["B"]):
        response = env.client.get(f"/v1{path}", headers=env.viewer_a["headers"])
        # 404, not 403: another project's IDs are indistinguishable from IDs that do not exist.
        assert response.status_code == 404, (path, response.status_code, response.text)


def test_lists_only_contain_accessible_projects(env):
    headers = env.viewer_a["headers"]
    projects = env.client.get("/v1/projects/", headers=headers).json()
    assert [p["id"] for p in projects["projects"]] == [env.projects["A"]["project"]]
    runs = env.client.get("/v1/runs", headers=headers).json()
    assert {run["project_id"] for run in runs["runs"]} == {env.projects["A"]["project"]}
    assert env.client.get("/v1/runs", params={"project_id": env.projects["B"]["project"]},
                          headers=headers).status_code == 404
    formulas = env.client.get("/v1/formulas/", headers={**headers}).json()
    b_formula = env.projects["B"]["formula"]
    assert b_formula not in {formula["id"] for formula in formulas}


def test_cross_project_comparisons_are_refused(env):
    a_run, b_run = env.projects["A"]["runs"]["Base"], env.projects["B"]["runs"]["Low Interest Rate"]
    response = env.client.get("/v1/comparisons", params={"baseline_run_id": a_run, "current_run_id": b_run},
                              headers=env.viewer_a["headers"])
    assert response.status_code == 404
    scenarios = env.client.get("/v1/scenarios/compare", headers=env.viewer_a["headers"], params={
        "baseline_id": env.projects["A"]["scenarios"]["Base"],
        "compare_id": env.projects["B"]["scenarios"]["Base"],
    })
    assert scenarios.status_code == 404


def test_viewers_cannot_change_or_run_but_editors_can(env):
    ps = env.projects["A"]["projection_set"]
    viewer, editor = env.viewer_a["headers"], env.editor_a["headers"]
    assert env.client.patch(f"/v1/projection-sets/{ps}", json={"description": "x"},
                            headers=viewer).status_code == 403
    submit = {"project_id": env.projects["A"]["project"], "projection_set_ids": [ps]}
    assert env.client.post("/v1/run-sets/preflight", json=submit, headers=viewer).status_code == 403
    preflight = env.client.post("/v1/run-sets/preflight", json=submit, headers=editor)
    assert preflight.status_code == 200 and preflight.json()["ok"] is True
    # An editor of A is still a stranger to B.
    submit_b = {"project_id": env.projects["B"]["project"], "projection_set_ids": [env.projects["B"]["projection_set"]]}
    assert env.client.post("/v1/run-sets", json=submit_b, headers=editor).status_code == 404


def test_uploads_are_project_scoped_and_preview_cannot_read_server_files(env):
    response = env.client.post(
        "/v1/imports/inforce", params={"project_id": env.projects["B"]["project"]},
        files={"file": ("x.csv", b"policy_id\nP1\n", "text/csv")}, headers=env.editor_a["headers"],
    )
    assert response.status_code == 404
    outside = env.client.get("/v1/imports/preview", params={"file_path": __file__}, headers=env.editor_a["headers"])
    assert outside.status_code == 404


# =============================================================================
# I: cross-project scenario and data injection
# =============================================================================

def test_scenario_of_another_project_cannot_be_injected_into_a_run(env):
    a, b = env.projects["A"], env.projects["B"]
    with env.Session() as db:
        runs_before = db.query(Run).count()
    for headers in (env.editor_a["headers"], env.admin_headers()):  # even an administrator
        response = env.client.post("/v1/run-sets", headers=headers, json={
            "project_id": a["project"], "projection_set_ids": [a["projection_set"]],
            "scenario_ids": [b["scenarios"]["Low Interest Rate"]],
        })
        assert response.status_code == 422, response.text
        problems = {p["code"] for p in response.json()["error"]["details"]["problems"]}
        assert problems == {"SCENARIO_NOT_IN_PROJECT"}
    with env.Session() as db:
        assert db.query(Run).count() == runs_before  # nothing was created


def test_request_level_scenarios_are_fully_validated(env):
    a = env.projects["A"]
    with env.Session() as db:
        base = db.get(ScenarioTable, a["scenarios"]["Base"])
        bad = ScenarioTable(
            set_id=base.set_id, scenario_name="Unvalidated", overrides=[
                {"target_variable": "reserve", "operation": "explode", "value": "high"},
            ], status="draft", scenario_type="deterministic",
        )
        db.add(bad)
        db.commit()
        bad_id = bad.id
    response = env.client.post("/v1/run-sets", headers=env.admin_headers(), json={
        "project_id": a["project"], "projection_set_ids": [a["projection_set"]], "scenario_ids": [bad_id],
    })
    assert response.status_code == 422
    problems = {p["code"] for p in response.json()["error"]["details"]["problems"]}
    assert {"SCENARIO_NOT_RUNNABLE", "SCENARIO_NOT_FINGERPRINTED", "SCENARIO_TARGET_CALCULATED",
            "SCENARIO_OPERATION_INVALID", "SCENARIO_VALUE_INVALID"} <= problems


def test_scenario_overrides_must_target_variables_of_the_model(env):
    a = env.projects["A"]
    with env.Session() as db:
        db.add(VariableRegistry(name="unrelated_rate", data_type="number", source_type="manual",
                                source={"type": "manual", "value": 0.1}, lookup_keys=[],
                                product_applicability=[], basis_applicability=[]))
        base = db.get(ScenarioTable, a["scenarios"]["Base"])
        overrides = [{"target_variable": "unrelated_rate", "operation": "set", "value": 0.2}]
        from app.core.execution.fingerprints import scenario_fingerprint
        other = ScenarioTable(set_id=base.set_id, scenario_name="Unrelated", overrides=overrides,
                              status="validated", fingerprint=scenario_fingerprint(overrides),
                              scenario_type="deterministic")
        db.add(other)
        db.commit()
        other_id = other.id
    response = env.client.post("/v1/run-sets", headers=env.admin_headers(), json={
        "project_id": a["project"], "projection_set_ids": [a["projection_set"]], "scenario_ids": [other_id],
    })
    assert response.status_code == 422
    assert {p["code"] for p in response.json()["error"]["details"]["problems"]} == {"SCENARIO_TARGET_UNKNOWN"}


def test_projection_sets_cannot_reference_another_projects_objects(env):
    a, b = env.projects["A"], env.projects["B"]
    headers = env.editor_a["headers"]
    created = env.client.post(f"/v1/projects/{a['project']}/projection-sets", headers=headers, json={
        "name": "Cross-project attempt", "model_version_id": a["version"],
        "inforce_file_ids": [b["inforce"]], "assumption_table_ids": [a["table"]],
        "scenario_ids": [a["scenarios"]["Base"]], "valuation_date": "2026-12-31", "horizon_months": 12,
    })
    assert created.status_code == 422 and created.json()["error"]["code"] == "CROSS_PROJECT_REFERENCE"
    attach = env.client.post(f"/v1/projection-sets/{a['projection_set']}/scenarios", headers=headers,
                             json={"scenario_id": b["scenarios"]["Base"]})
    assert attach.status_code == 422
    patched = env.client.patch(f"/v1/projection-sets/{a['projection_set']}", headers=headers,
                               json={"assumption_table_ids": [b["table"]]})
    assert patched.status_code == 422
