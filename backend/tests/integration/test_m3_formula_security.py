"""M3 Formula Studio Security Integration Test Matrix (FS-SEC-01 to FS-SEC-04 and Governance)."""

import pytest

from app.db.models.formula import FormulaRegistry
from app.db.models.modeling import ModelVersion
from .wp1_support import Env


@pytest.fixture()
def env():
    environment = Env()
    environment.seed("A")
    environment.seed("B")
    environment.editor_a = environment.user("editor-a@example.com", memberships={"A": "editor"})
    environment.editor_b = environment.user("editor-b@example.com", memberships={"B": "editor"})
    yield environment
    environment.close()


def test_fs_sec_01_cross_project_model_version_id_in_formula_creation_rejected_with_404(env):
    """FS-SEC-01: Project A user + Project B model_version_id -> POST /v1/formulas -> 404 Not Found."""
    version_b_id = env.projects["B"]["version"]
    response = env.client.post(
        "/v1/formulas/",
        json={
            "name": "Malicious Injected Formula",
            "output_variable": "injected_output",
            "function_ref": "dsl",
            "expression_text": "1.0",
            "model_version_id": version_b_id,
        },
        headers=env.editor_a["headers"],
    )
    assert response.status_code == 404, response.text
    assert "not found" in response.text.lower()


def test_fs_sec_02_non_member_cannot_create_formula_for_private_project(env):
    """FS-SEC-02: Non-member of Project B cannot create formula for Project B."""
    stranger = env.user("stranger@example.com", memberships={})
    version_b_id = env.projects["B"]["version"]
    response = env.client.post(
        "/v1/formulas/",
        json={
            "name": "Stranger Formula",
            "output_variable": "stranger_output",
            "function_ref": "dsl",
            "expression_text": "1.0",
            "model_version_id": version_b_id,
        },
        headers=stranger["headers"],
    )
    assert response.status_code == 404, response.text


def test_fs_sec_03_formula_edit_cannot_mutate_approved_model_version(env):
    """FS-SEC-03: Formula edit cannot mutate an approved model version."""
    version_a_id = env.projects["A"]["version"]
    with env.Session() as db:
        version = db.get(ModelVersion, version_a_id)
        version.status = "approved"
        formula = db.query(FormulaRegistry).filter_by(model_version_id=version_a_id).first()
        formula_id = formula.id
        db.commit()

    response = env.client.put(
        f"/v1/formulas/{formula_id}",
        json={"expression_text": "prior(balance) * 1.05"},
        headers=env.admin_headers(),
    )
    assert response.status_code in (409, 422), response.text
    assert "approved" in response.text.lower() or "immutable" in response.text.lower() or "mutable" in response.text.lower()


def test_fs_sec_04_malformed_ast_payload_cannot_execute_arbitrary_code(env):
    """FS-SEC-04: Malformed AST payload and arbitrary Python execution attempts are safely rejected."""
    bad_expressions = [
        "__import__('os').system('echo pwned')",
        "eval('1 + 1')",
        "x.__class__.__bases__[0]",
        "[x for x in (1, 2, 3)]",
        "lambda x: x + 1",
        "open('test.txt', 'w')",
    ]
    for expr in bad_expressions:
        response = env.client.post(
            "/v1/formulas/validate-expression",
            json={"expression_text": expr},
            headers=env.admin_headers(),
        )
        assert response.status_code == 422, f"Expected 422 for '{expr}', got {response.status_code}"
        assert "FORMULA_SYNTAX_ERROR" in response.text or "FORMULA_DSL_ERROR" in response.text


def test_fs_gov_editing_formula_updates_fingerprint_and_governance_mode(env):
    """Section 17 & 18 & 29: Formula edit updates model definition fingerprint; run package reflects governance mode."""
    from app.core.model_fingerprint import compute_model_definition_fingerprint
    from app.db.models.run_package import RunPackage
    from app.services import run_package_service

    version_a_id = env.projects["A"]["version"]
    with env.Session() as db:
        fp_before = compute_model_definition_fingerprint(db, version_a_id)
        formula = db.query(FormulaRegistry).filter_by(model_version_id=version_a_id).first()
        formula_id = formula.id

    # 1. Update formula explanation
    response = env.client.put(
        f"/v1/formulas/{formula_id}",
        json={"explanation": "Updated explanation for governance test"},
        headers=env.admin_headers(),
    )
    assert response.status_code == 200, response.text

    # 2. Assert fingerprint changed and status reset to draft
    with env.Session() as db:
        fp_after = compute_model_definition_fingerprint(db, version_a_id)
        version = db.get(ModelVersion, version_a_id)
        assert version.status == "draft", "Editing formula must reset model version to draft"
    assert fp_before != fp_after, "Editing formula explanation/metadata must change model definition fingerprint"

    # 3. Submitting while in draft is refused
    with pytest.raises(run_package_service.PackageInvalid) as exc_info:
        env.submit("A", ["Base"])
    assert "draft" in str(exc_info.value).lower()

    # 4. Revalidate model version -> now it can run in sandbox mode
    val_res = env.client.post(f"/v1/model-versions/{version_a_id}/validate", headers=env.admin_headers())
    assert val_res.status_code == 200

    submitted = env.submit("A", ["Base"])
    run_id = submitted["by_scenario"]["Base"]
    with env.Session() as db:
        package = db.query(RunPackage).filter_by(run_id=run_id).one()
        gov = package.package["governance"]
        conf = package.package["configuration"]["model_version"]
        assert gov["model_definition_fingerprint"] == fp_after
        assert conf["model_definition_fingerprint"] == fp_after
        assert gov["governance_mode"] == "sandbox"
        assert gov["is_governed"] is False


def test_fs_gov_governed_run_fingerprint_requirements(env):
    """Item 3: Tightened governed RunPackage fingerprint requirements.
    - missing approved fingerprint -> governed run refused
    - stale approved fingerprint -> governed run refused
    - missing/stale validation fingerprint -> governed run refused
    - all fingerprints equal executable model -> governed run accepted
    - sandbox behavior remains available where intentionally allowed
    """
    from app.core.model_fingerprint import compute_model_definition_fingerprint
    from app.db.models.modeling import ModelVersion
    from app.db.models.projection import ProjectionSet
    from app.db.models.run_package import RunPackage
    from app.services import run_package_service

    version_id = env.projects["A"]["version"]
    proj_set_id = env.projects["A"]["projection_set"]

    # Ensure model is validated first
    val_res = env.client.post(f"/v1/model-versions/{version_id}/validate", headers=env.admin_headers())
    assert val_res.status_code == 200

    with env.Session() as db:
        actual_fp = compute_model_definition_fingerprint(db, version_id)
        version = db.get(ModelVersion, version_id)
        pset = db.get(ProjectionSet, proj_set_id)
        pset.parameters = {"governance_mode": "governed"}
        db.commit()

    # 1. Missing approved fingerprint -> refused
    with env.Session() as db:
        version = db.get(ModelVersion, version_id)
        version.status = "approved"
        version.validation_fingerprint = actual_fp
        version.approved_fingerprint = None
        db.commit()

    with pytest.raises(run_package_service.PackageInvalid) as exc_info:
        env.submit("A", ["Base"])
    assert "GOVERNED_RUN_APPROVED_FINGERPRINT_MISSING" in str(exc_info.value) or "approved_fingerprint" in str(exc_info.value)

    # 2. Stale approved fingerprint -> refused
    with env.Session() as db:
        version = db.get(ModelVersion, version_id)
        version.status = "approved"
        version.validation_fingerprint = actual_fp
        version.approved_fingerprint = "0" * 64  # Stale
        db.commit()

    with pytest.raises(run_package_service.PackageInvalid) as exc_info:
        env.submit("A", ["Base"])
    assert "MODEL_APPROVAL_STALE" in str(exc_info.value) or "approval" in str(exc_info.value).lower()

    # 3. Missing validation fingerprint -> refused
    with env.Session() as db:
        version = db.get(ModelVersion, version_id)
        version.status = "approved"
        version.validation_fingerprint = None
        version.approved_fingerprint = actual_fp
        db.commit()

    with pytest.raises(run_package_service.PackageInvalid) as exc_info:
        env.submit("A", ["Base"])
    assert "GOVERNED_RUN_VALIDATION_FINGERPRINT_MISSING" in str(exc_info.value) or "validation_fingerprint" in str(exc_info.value)

    # Stale validation fingerprint -> refused
    with env.Session() as db:
        version = db.get(ModelVersion, version_id)
        version.status = "approved"
        version.validation_fingerprint = "1" * 64  # Stale
        version.approved_fingerprint = actual_fp
        db.commit()

    with pytest.raises(run_package_service.PackageInvalid) as exc_info:
        env.submit("A", ["Base"])
    assert "MODEL_VALIDATION_STALE" in str(exc_info.value) or "validation" in str(exc_info.value).lower()

    # 4. All fingerprints matching executable model -> governed run accepted
    with env.Session() as db:
        version = db.get(ModelVersion, version_id)
        version.status = "approved"
        version.validation_fingerprint = actual_fp
        version.approved_fingerprint = actual_fp
        db.commit()

    submitted = env.submit("A", ["Base"])
    run_id = submitted["by_scenario"]["Base"]
    with env.Session() as db:
        package = db.query(RunPackage).filter_by(run_id=run_id).one()
        gov = package.package["governance"]
        assert gov["governance_mode"] == "governed"
        assert gov["is_governed"] is True
        assert gov["model_definition_fingerprint"] == actual_fp
        assert gov["validation_fingerprint"] == actual_fp
        assert gov["approved_fingerprint"] == actual_fp

    # 5. Sandbox behavior remains available where intentionally allowed
    with env.Session() as db:
        # Corrupt the approval fingerprint, but request sandbox mode
        version = db.get(ModelVersion, version_id)
        version.approved_fingerprint = "0" * 64
        pset = db.get(ProjectionSet, proj_set_id)
        pset.parameters = {"governance_mode": "sandbox"}
        db.commit()

    sandbox_submitted = env.submit("A", ["Base"])
    s_run_id = sandbox_submitted["by_scenario"]["Base"]
    with env.Session() as db:
        s_pkg = db.query(RunPackage).filter_by(run_id=s_run_id).one()
        s_gov = s_pkg.package["governance"]
        assert s_gov["governance_mode"] == "sandbox"
        assert s_gov["is_governed"] is False

