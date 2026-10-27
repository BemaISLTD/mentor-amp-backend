"""Formula Studio API coverage for validation, preview, persistence and graph analysis."""

import pytest

from app.db.models.model_variable import ModelVariableDefinition
from app.db.models.formula import FormulaRegistry
from app.db.models.modeling import ModelVersion
from app.db.models.audit_log import AuditLog
from app.db.models.variable import VariableRegistry

from .wp1_support import Env


@pytest.fixture()
def env():
    environment = Env()
    environment.seed("FSTUDIO")
    yield environment
    environment.close()


def _draft_version(env: Env) -> str:
    ids = env.projects["FSTUDIO"]
    response = env.client.post(
        f"/v1/models/{ids['model']}/versions",
        json={
            "parent_version_id": ids["version"],
            "version_label": "formula-studio-draft",
            "basis": "GAAP",
        },
        headers=env.admin_headers(),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_fstudio_01_validates_dependencies_and_unknown_variables(env):
    version_id = env.projects["FSTUDIO"]["version"]
    response = env.client.post(
        "/v1/formulas/validate-expression",
        json={
            "expression_text": "max(0.0, expected_payment - missing_adjustment)",
            "model_version_id": version_id,
            "output_variable": "adjusted_payment",
        },
        headers=env.admin_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["valid"] is False
    assert body["dependencies"] == ["expected_payment", "missing_adjustment"]
    assert body["unknown_variables"] == ["missing_adjustment"]
    assert body["functions_used"] == ["max"]

    unsafe = env.client.post(
        "/v1/formulas/validate-expression",
        json={"expression_text": "x.__class__"},
        headers=env.admin_headers(),
    )
    assert unsafe.status_code == 422
    assert unsafe.json()["error"]["code"] == "FORMULA_SYNTAX_ERROR"


def test_fstudio_02_previews_current_and_prior_values(env):
    response = env.client.post(
        "/v1/formulas/preview",
        json={
            "expression_text": "round(prior(balance, opening) + premium, 6)",
            "inputs": {"opening": 100.0, "premium": 25.1256784},
            "prior_inputs": {"balance": 200.0},
        },
        headers=env.admin_headers(),
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "valid": True,
        "result": 225.125678,
        "dependencies": ["opening", "premium"],
        "inputs_used": {"opening": 100.0, "premium": 25.1256784},
        "errors": [],
    }


def test_fstudio_03_04_06_create_edit_test_and_protect_formula(env):
    ids = env.projects["FSTUDIO"]
    draft_id = _draft_version(env)
    undefined = env.client.post(
        f"/v1/model-versions/{draft_id}/formulas",
        json={
            "name": "Undefined Output", "output_variable": "undefined_output",
            "function_ref": "dsl", "expression_text": "1",
        },
        headers=env.admin_headers(),
    )
    assert undefined.status_code == 422
    assert undefined.json()["error"]["code"] == "FORMULA_OUTPUT_NOT_DEFINED"
    with env.Session() as db:
        db.add(VariableRegistry(
            name="dsl_test_output", display_name="DSL Test Output", data_type="number",
            source_type="formula", source={"type": "formula"}, lookup_keys=[],
            required=True, product_applicability=["spia_lite"], basis_applicability=["GAAP"],
            version="v1", created_by=ids["admin"], updated_by=ids["admin"],
        ))
        db.flush()
        db.add(ModelVariableDefinition(
            model_version_id=draft_id, variable_name="dsl_test_output",
            display_name="DSL Test Output", kind="formula", data_type="number",
            required=True, source={"type": "formula"}, version="v1",
            created_by=ids["admin"], updated_by=ids["admin"],
        ))
        db.commit()

    invalid_owner = env.client.post(
        f"/v1/model-versions/{draft_id}/formulas",
        json={
            "name": "Invalid owner", "output_variable": "dsl_test_output",
            "function_ref": "dsl", "expression_text": "expected_payment + 1",
            "owner_user_id": "missing-user",
        },
        headers=env.admin_headers(),
    )
    assert invalid_owner.status_code == 422
    assert invalid_owner.json()["error"]["code"] == "FORMULA_OWNER_NOT_FOUND"

    created = env.client.post(
        f"/v1/model-versions/{draft_id}/formulas",
        json={
            "name": "DSL Test Formula",
            "output_variable": "dsl_test_output",
            "function_ref": "dsl",
            "expression_text": "expected_payment + 1",
            "dependencies": ["premium"],
            "explanation": "Formula Studio integration test",
            "unit": "USD",
            "test_cases": [
                {
                    "name": "adds one",
                    "inputs": {"expected_payment": 9.0},
                    "prior_inputs": {},
                    "expected_output": 10.0,
                    "abs_tol": 0.0,
                },
                {
                    "name": "intentional failure",
                    "inputs": {"expected_payment": 4.0},
                    "prior_inputs": {},
                    "expected_output": 99.0,
                    "abs_tol": 0.01,
                },
            ],
        },
        headers=env.admin_headers(),
    )
    assert created.status_code == 201, created.text
    formula = created.json()
    assert formula["dependencies"] == ["expected_payment"]
    assert formula["expression_text"] == "expected_payment + 1"
    assert formula["group_id"]
    assert len(formula["test_cases"]) == 2

    tests = env.client.post(
        f"/v1/formulas/{formula['id']}/run-tests", headers=env.admin_headers()
    )
    assert tests.status_code == 200, tests.text
    assert tests.json()["all_passed"] is False
    assert (tests.json()["passed"], tests.json()["failed"]) == (1, 1)
    assert tests.json()["results"][0]["abs_diff"] == 0.0

    edited = env.client.put(
        f"/v1/formulas/{formula['id']}",
        json={
            "expression_text": "expected_payment + premium",
            "dependencies": ["expected_payment"],
        },
        headers=env.admin_headers(),
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["dependencies"] == ["expected_payment", "premium"]

    invalid_update = env.client.put(
        f"/v1/formulas/{formula['id']}",
        json={"owner_user_id": "missing-user"},
        headers=env.admin_headers(),
    )
    assert invalid_update.status_code == 422
    assert invalid_update.json()["error"]["code"] == "FORMULA_OWNER_NOT_FOUND"

    with env.Session() as db:
        version = db.get(ModelVersion, draft_id)
        version.status = "approved"
        db.commit()

    protected = env.client.post(
        f"/v1/model-versions/{draft_id}/formulas",
        json={
            "name": "Forbidden Formula", "output_variable": "forbidden_output",
            "function_ref": "dsl", "expression_text": "1",
        },
        headers=env.admin_headers(),
    )
    assert protected.status_code == 409
    assert protected.json()["error"]["code"] == "MODEL_VERSION_IMMUTABLE"


def test_fstudio_05_07_compare_and_dependency_analysis(env):
    ids = env.projects["FSTUDIO"]
    draft_id = _draft_version(env)
    with env.Session() as db:
        first_formula = next(
            row for row in db.query(FormulaRegistry).filter_by(model_version_id=draft_id).all()
            if row.expression_text
        )
        formula_id = first_formula.id
        output = first_formula.output_variable

    edited = env.client.put(
        f"/v1/formulas/{formula_id}",
        json={"explanation": "Changed in Formula Studio"},
        headers=env.admin_headers(),
    )
    assert edited.status_code == 200, edited.text

    compared = env.client.get(
        f"/v1/model-versions/{ids['version']}/compare/{draft_id}",
        headers=env.admin_headers(),
    )
    assert compared.status_code == 200, compared.text
    modified = compared.json()["formula_changes"]["modified"]
    assert any(item["key"] == output and "explanation" in item["changes"] for item in modified)

    analysis = env.client.get(
        f"/v1/model-versions/{draft_id}/dependency-analysis",
        params={"variable": output},
        headers=env.admin_headers(),
    )
    assert analysis.status_code == 200, analysis.text
    assert output in analysis.json()["execution_order"]
    assert isinstance(analysis.json()["upstream_dependencies"], list)
    assert isinstance(analysis.json()["downstream_impacted"], list)


def test_fstudio_07_rollback_reactivates_prior_version_and_audits(env):
    ids = env.projects["FSTUDIO"]
    replacement_id = _draft_version(env)
    with env.Session() as db:
        prior = db.get(ModelVersion, ids["version"])
        replacement = db.get(ModelVersion, replacement_id)
        prior.status = "superseded"
        prior.is_current = False
        db.flush()
        replacement.status = "published"
        replacement.is_current = True
        db.commit()

    response = env.client.post(
        f"/v1/model-versions/{ids['version']}/rollback", headers=env.admin_headers()
    )
    assert response.status_code == 200, response.text
    assert response.json()["is_current"] is True
    assert response.json()["status"] == "published"
    with env.Session() as db:
        assert db.get(ModelVersion, replacement_id).is_current is False
        event = db.query(AuditLog).filter_by(
            entity_id=ids["version"], action="model_version.rollback_activated"
        ).one()
        assert event.context["replaced_current_version_ids"] == [replacement_id]


def test_fstudio_03_full_dsl_lifecycle_executes_and_traces(env):
    ids = env.projects["FSTUDIO"]
    draft_id = _draft_version(env)
    with env.Session() as db:
        formula = db.query(FormulaRegistry).filter_by(
            model_version_id=draft_id, output_variable="expected_payment"
        ).one()
        formula_id = formula.id

    edited = env.client.put(
        f"/v1/formulas/{formula_id}",
        json={
            "function_ref": "dsl",
            "expression_text": (
                "monthly_payment * survival_probability + prior(monthly_payment, 0) * 0"
            ),
            "explanation": "Controlled DSL version of the illustrative payment formula.",
        },
        headers=env.admin_headers(),
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["dependencies"] == ["monthly_payment", "survival_probability"]

    validated = env.client.post(
        f"/v1/model-versions/{draft_id}/validate", headers=env.admin_headers()
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["status"] == "validated"

    reviewer = env.user(
        "formula-lifecycle-reviewer@example.com",
        role="reviewer",
        memberships={"FSTUDIO": "viewer"},
    )
    approved = env.client.post(
        f"/v1/model-versions/{draft_id}/approve",
        json={"reason": "Independent Formula Studio review passed."},
        headers=reviewer["headers"],
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    published = env.client.post(
        f"/v1/model-versions/{draft_id}/publish", headers=env.admin_headers()
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"

    projection = env.client.post(
        f"/v1/projects/{ids['project']}/projection-sets",
        headers=env.admin_headers(),
        json={
            "name": "Formula Studio DSL execution",
            "model_version_id": draft_id,
            "inforce_file_ids": [ids["inforce"]],
            "assumption_table_ids": [ids["table"]],
            "factor_table_ids": [],
            "scenario_ids": [ids["scenarios"]["Base"]],
            "valuation_date": "2026-12-31",
            "horizon_months": 2,
            "time_step": "monthly",
            "output_variables": ["expected_payment"],
            "trace_scope": {"mode": "selected_policies", "policy_ids": ["SPIA-0001"]},
            "parameters": {},
        },
    )
    assert projection.status_code == 201, projection.text
    projection_id = projection.json()["id"]
    checked = env.client.post(
        f"/v1/projection-sets/{projection_id}/validate", headers=env.admin_headers()
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "validated"

    submitted = env.client.post(
        "/v1/run-sets",
        headers=env.admin_headers(),
        json={
            "project_id": ids["project"],
            "name": "Formula Studio governed DSL run",
            "projection_set_ids": [projection_id],
            "scenario_ids": [ids["scenarios"]["Base"]],
        },
    )
    assert submitted.status_code == 202, submitted.text
    run_id = submitted.json()["runs"][0]["id"]
    assert env.run(run_id).status == "success"

    trace = env.get(
        f"/runs/{run_id}/trace",
        policy_id="SPIA-0001", month=2, variable="expected_payment",
    )
    assert trace.status_code == 200, trace.text
    formula_trace = trace.json()["root"]["formula"]
    assert formula_trace["function_ref"] == "dsl"
    assert formula_trace["expression_text"] == (
        "monthly_payment * survival_probability + prior(monthly_payment, 0) * 0"
    )
    prior_child = next(
        child for child in trace.json()["root"]["children"]
        if child["variable"] == "monthly_payment" and child["month"] == 1
    )
    assert prior_child["kind"] == "input"

    dependents = env.get(
        f"/runs/{run_id}/trace/dependents",
        policy_id="SPIA-0001", month=1, variable="monthly_payment",
    )
    assert dependents.status_code == 200, dependents.text
    assert any(
        item["variable"] == "expected_payment" and item["month"] == 2
        for item in dependents.json()["dependents"]
    )
