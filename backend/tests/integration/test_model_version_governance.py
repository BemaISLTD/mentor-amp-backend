"""Model version governance integration tests.

Comprehensive test coverage for senior-review governance requirements:
- G-01 to G-03: Publish requires independent approval (draft/validated refused, approved succeeds, supersedes previous).
- G-04: Approval refused if validation fingerprint does not match current model content (stale validation).
- G-05 to G-07: Approved and published versions cannot be rolled back to validated; immutability preserved.
- G-08 to G-17: Every material mutation invalidates previous validation (resets to draft, clears validation evidence).
- G-18 to G-27: Whole-model immutability (version fields, variables, formulas, published outputs locked on approved/published).
- G-31 to G-32: Segregation of duties (creator cannot self-approve; material modifier cannot approve).
- G-33 to G-34: Dedicated models:approve permission enforced on approve and reject (model_developer/read_only 403).
- G-35 to G-36: Rejection requires non-empty reason; sets rejected state, records provenance, clears validation.
- G-37: Cross-project scoping and isolation enforced on governance actions.
- G-38 to G-40: Deterministic canonical model fingerprinting.
"""

from datetime import date

import pytest

from app.core.formula_engine.registry import register
from app.core.model_fingerprint import (
    MODEL_FINGERPRINT_SCHEME,
    compute_model_definition_fingerprint,
)
from app.db.models.audit_log import AuditLog
from app.db.models.formula import FormulaRegistry
from app.db.models.model_variable import ModelVariableDefinition
from app.db.models.modeling import Model, ModelPublishedOutput, ModelVersion
from app.db.models.projection import ProjectionSet
from app.models.schemas import FormulaDefinition

from .wp1_support import Env


@pytest.fixture()
def env():
    environment = Env()
    environment.seed("GOV")
    yield environment
    environment.close()


# =============================================================================
# 1. PUBLISH REQUIRES INDEPENDENT APPROVAL (G-01, G-02, G-03)
# =============================================================================

def test_publish_requires_independent_approval(env):
    ids = env.projects["GOV"]
    model_id = ids["model"]
    v1_id = ids["version"]
    admin_headers = env.admin_headers()
    actuary = env.user("actuary_pub@example.com", role="actuary", memberships={"GOV": "editor"})

    # Create draft v2
    res = env.client.post(
        f"/v1/models/{model_id}/versions",
        json={"parent_version_id": v1_id, "version_label": "2026.pub_test", "basis": "GAAP"},
        headers=admin_headers,
    )
    assert res.status_code == 201
    v2_id = res.json()["id"]

    # G-01: Attempt publish while status is DRAFT -> 409 MODEL_VERSION_NOT_APPROVED
    pub_draft = env.client.post(f"/v1/model-versions/{v2_id}/publish", headers=admin_headers)
    assert pub_draft.status_code == 409
    assert pub_draft.json()["error"]["code"] == "MODEL_VERSION_NOT_APPROVED"

    # Validate v2
    val_res = env.client.post(f"/v1/model-versions/{v2_id}/validate", headers=admin_headers)
    assert val_res.status_code == 200
    assert val_res.json()["status"] == "validated"

    # G-02: Attempt publish while status is merely VALIDATED without approval -> 409 MODEL_VERSION_NOT_APPROVED
    pub_val = env.client.post(f"/v1/model-versions/{v2_id}/publish", headers=admin_headers)
    assert pub_val.status_code == 409
    assert pub_val.json()["error"]["code"] == "MODEL_VERSION_NOT_APPROVED"

    # Assert model version was NOT published
    with env.Session() as db:
        v2_db = db.get(ModelVersion, v2_id)
        assert v2_db.status == "validated"
        assert v2_db.is_current is False
        assert v2_db.published_at is None
        assert v2_db.published_by is None
        pub_audit = (
            db.query(AuditLog)
            .filter(AuditLog.entity_id == v2_id, AuditLog.action == "model_version.published")
            .first()
        )
        assert pub_audit is None

    # Approve v2 using independent actuary
    app_res = env.client.post(
        f"/v1/model-versions/{v2_id}/approve",
        headers=actuary["headers"],
        json={"reason": "Governance review passed for release."},
    )
    assert app_res.status_code == 200
    assert app_res.json()["status"] == "approved"
    assert app_res.json()["approved_by"] == actuary["id"]
    assert app_res.json()["published_by"] is None

    # G-03: Publish approved version -> succeeds
    pub_res = env.client.post(f"/v1/model-versions/{v2_id}/publish", headers=admin_headers)
    assert pub_res.status_code == 200
    pub_json = pub_res.json()
    assert pub_json["is_current"] is True
    assert pub_json["status"] == "approved"
    assert pub_json["published_by"] == ids["admin"]
    assert pub_json["published_at"] is not None

    # Verify previous version superseded and parent model active
    with env.Session() as db:
        old_v1 = db.get(ModelVersion, v1_id)
        assert old_v1.is_current is False
        assert old_v1.status == "superseded"

        new_v2 = db.get(ModelVersion, v2_id)
        assert new_v2.is_current is True

        model = db.get(Model, model_id)
        assert model.status == "active"


# =============================================================================
# 2. NO LIFECYCLE ROLLBACK FROM PROTECTED STATES (G-05, G-06, G-07)
# =============================================================================

def test_approved_and_published_cannot_rollback_to_validated(env):
    ids = env.projects["GOV"]
    model_id = ids["model"]
    v1_id = ids["version"]
    admin_headers = env.admin_headers()
    actuary = env.user("actuary_no_rollback@example.com", role="actuary", memberships={"GOV": "editor"})

    res = env.client.post(
        f"/v1/models/{model_id}/versions",
        json={"parent_version_id": v1_id, "version_label": "2026.rollback_test", "basis": "GAAP"},
        headers=admin_headers,
    )
    v2_id = res.json()["id"]

    env.client.post(f"/v1/model-versions/{v2_id}/validate", headers=admin_headers)
    env.client.post(f"/v1/model-versions/{v2_id}/approve", headers=actuary["headers"])

    # G-05: Calling validate on APPROVED version must be refused with MODEL_VERSION_IMMUTABLE
    val_on_approved = env.client.post(f"/v1/model-versions/{v2_id}/validate", headers=admin_headers)
    assert val_on_approved.status_code == 409
    assert val_on_approved.json()["error"]["code"] == "MODEL_VERSION_IMMUTABLE"

    with env.Session() as db:
        v2_db = db.get(ModelVersion, v2_id)
        assert v2_db.status == "approved"

    # Now publish v2
    env.client.post(f"/v1/model-versions/{v2_id}/publish", headers=admin_headers)

    # G-06: Calling validate on PUBLISHED/CURRENT version must be refused with MODEL_VERSION_IMMUTABLE
    val_on_current = env.client.post(f"/v1/model-versions/{v2_id}/validate", headers=admin_headers)
    assert val_on_current.status_code == 409
    assert val_on_current.json()["error"]["code"] == "MODEL_VERSION_IMMUTABLE"

    # G-07: Mutation routes must remain strictly blocked
    patch_res = env.client.patch(
        f"/v1/model-versions/{v2_id}",
        json={"notes": "malicious mutation"},
        headers=admin_headers,
    )
    assert patch_res.status_code == 409
    assert patch_res.json()["error"]["code"] == "MODEL_VERSION_IMMUTABLE"


# =============================================================================
# 3. CANONICAL DEFINITION FINGERPRINT & STALE VALIDATION (G-04)
# =============================================================================

def test_stale_validation_fingerprint_refuses_approval(env):
    ids = env.projects["GOV"]
    model_id = ids["model"]
    v1_id = ids["version"]
    admin_headers = env.admin_headers()
    actuary = env.user("actuary_stale@example.com", role="actuary", memberships={"GOV": "editor"})

    res = env.client.post(
        f"/v1/models/{model_id}/versions",
        json={"parent_version_id": v1_id, "version_label": "2026.fingerprint_test", "basis": "GAAP"},
        headers=admin_headers,
    )
    v2_id = res.json()["id"]

    # Validate version: captures validation_fingerprint
    val_res = env.client.post(f"/v1/model-versions/{v2_id}/validate", headers=admin_headers)
    assert val_res.status_code == 200
    v_fp = val_res.json()["validation"]["fingerprint"]
    assert v_fp is not None
    assert v_fp.startswith("mentoramp.model_definition/v1:")

    # Directly simulate a bypass or content tampering that changed the model without going through service invalidation
    with env.Session() as db:
        v2_db = db.get(ModelVersion, v2_id)
        # Change configuration
        v2_db.configuration = {"injected_param": 999}
        db.commit()

    # G-04: Approval must verify current fingerprint == validation fingerprint. Mismatch -> 409 MODEL_VALIDATION_STALE
    app_res = env.client.post(f"/v1/model-versions/{v2_id}/approve", headers=actuary["headers"])
    assert app_res.status_code == 409
    assert app_res.json()["error"]["code"] == "MODEL_VALIDATION_STALE"

    # Re-validating restores matching fingerprint and allows approval
    val_again = env.client.post(f"/v1/model-versions/{v2_id}/validate", headers=admin_headers)
    assert val_again.status_code == 200
    assert val_again.json()["validation"]["fingerprint"] != v_fp

    app_ok = env.client.post(f"/v1/model-versions/{v2_id}/approve", headers=actuary["headers"])
    assert app_ok.status_code == 200
    assert app_ok.json()["status"] == "approved"


# =============================================================================
# 4. MATERIAL MUTATIONS INVALIDATE VALIDATION (G-08 to G-17)
# =============================================================================

def test_material_mutations_invalidate_validation(env):
    ids = env.projects["GOV"]
    model_id = ids["model"]
    v1_id = ids["version"]
    admin_headers = env.admin_headers()

    def _setup_validated_version(label: str) -> str:
        res = env.client.post(
            f"/v1/models/{model_id}/versions",
            json={"parent_version_id": v1_id, "version_label": label, "basis": "GAAP"},
            headers=admin_headers,
        )
        vid = res.json()["id"]
        v = env.client.post(f"/v1/model-versions/{vid}/validate", headers=admin_headers)
        assert v.status_code == 200
        assert v.json()["status"] == "validated"
        return vid

    def _assert_invalidated(vid: str):
        with env.Session() as db:
            row = db.get(ModelVersion, vid)
            assert row.status == "draft", f"Expected status 'draft', got '{row.status}'"
            assert row.validation_fingerprint is None
            assert row.validated_at is None
            assert row.validated_by is None
            assert row.last_material_modified_by is not None
            assert row.last_material_modified_at is not None

    # G-08: ModelVersion fields update invalidates validation
    v_id = _setup_validated_version("2026.inv_mv")
    env.client.patch(f"/v1/model-versions/{v_id}", json={"basis": "STAT"}, headers=admin_headers)
    _assert_invalidated(v_id)

    # G-09: Variable definition update invalidates validation
    v_id = _setup_validated_version("2026.inv_var_upd")
    var_list = env.client.get(f"/v1/model-versions/{v_id}/variables", headers=admin_headers).json()["variables"]
    var_name = var_list[0]["variable_name"]
    res_upd = env.client.patch(
        f"/v1/model-versions/{v_id}/variables/{var_name}",
        json={"display_name": "Adjusted Interest Rate"},
        headers=admin_headers,
    )
    assert res_upd.status_code == 200
    _assert_invalidated(v_id)

    # G-10: Variable definition deletion invalidates validation
    v_id = _setup_validated_version("2026.inv_var_del")
    var_list = env.client.get(f"/v1/model-versions/{v_id}/variables", headers=admin_headers).json()["variables"]
    var_name = var_list[0]["variable_name"]
    del_var = env.client.delete(
        f"/v1/model-versions/{v_id}/variables/{var_name}",
        headers=admin_headers,
    )
    assert del_var.status_code == 204
    _assert_invalidated(v_id)

    # G-11: Formula creation invalidates validation
    v_id = _setup_validated_version("2026.inv_f_create")
    new_formula = FormulaDefinition(
        id=f"test_formula_{v_id[:8]}",
        name="Test Formula",
        output_variable="test_output_var",
        function_ref="app.formulas.spia:dummy",
        model_version_id=v_id,
        category="test",
        dependencies=[],
    )
    with env.Session() as db:
        register(db, new_formula, ids["admin"])
    _assert_invalidated(v_id)

    # G-12: Formula update invalidates validation
    v_id = _setup_validated_version("2026.inv_f_upd")
    with env.Session() as db:
        f_row = db.query(FormulaRegistry).filter(FormulaRegistry.model_version_id == v_id).first()
        f_id = f_row.id
    env.client.put(f"/v1/formulas/{f_id}", json={"description": "Updated formula"}, headers=admin_headers)
    _assert_invalidated(v_id)

    # G-13: Formula dependencies update invalidates validation
    v_id = _setup_validated_version("2026.inv_f_deps")
    with env.Session() as db:
        f_row = db.query(FormulaRegistry).filter(FormulaRegistry.model_version_id == v_id).first()
        f_id = f_row.id
    env.client.put(f"/v1/formulas/{f_id}", json={"dependencies": ["new_dep"]}, headers=admin_headers)
    _assert_invalidated(v_id)

    # G-14: Formula deletion invalidates validation
    v_id = _setup_validated_version("2026.inv_f_del")
    with env.Session() as db:
        f_row = db.query(FormulaRegistry).filter(FormulaRegistry.model_version_id == v_id).first()
        f_id = f_row.id
    del_f = env.client.delete(f"/v1/formulas/{f_id}", headers=admin_headers)
    assert del_f.status_code == 204
    _assert_invalidated(v_id)

    # G-15: Published output creation invalidates validation
    v_id = _setup_validated_version("2026.inv_out_create")
    po_res = env.client.post(
        f"/v1/model-versions/{v_id}/published-outputs",
        json={"variable_name": "extra_output", "display_name": "Extra Output"},
        headers=admin_headers,
    )
    assert po_res.status_code == 201
    _assert_invalidated(v_id)

    # G-16: Published output update invalidates validation
    v_id = _setup_validated_version("2026.inv_out_upd")
    with env.Session() as db:
        po_row = db.query(ModelPublishedOutput).filter(ModelPublishedOutput.model_version_id == v_id).first()
        po_var = po_row.variable_name
    po_patch = env.client.patch(
        f"/v1/model-versions/{v_id}/published-outputs/{po_var}",
        json={"display_name": "Modified Output Name"},
        headers=admin_headers,
    )
    assert po_patch.status_code == 200
    _assert_invalidated(v_id)

    # G-17: Published output deletion invalidates validation
    v_id = _setup_validated_version("2026.inv_out_del")
    with env.Session() as db:
        po_row = db.query(ModelPublishedOutput).filter(ModelPublishedOutput.model_version_id == v_id).first()
        po_var = po_row.variable_name
    po_del = env.client.delete(
        f"/v1/model-versions/{v_id}/published-outputs/{po_var}",
        headers=admin_headers,
    )
    assert po_del.status_code == 204
    _assert_invalidated(v_id)


# =============================================================================
# 5. WHOLE-MODEL IMMUTABILITY (G-18 to G-27)
# =============================================================================

def test_whole_model_immutability_on_approved_and_published(env):
    ids = env.projects["GOV"]
    model_id = ids["model"]
    v1_id = ids["version"]
    admin_headers = env.admin_headers()
    actuary = env.user("actuary_imm@example.com", role="actuary", memberships={"GOV": "editor"})

    res = env.client.post(
        f"/v1/models/{model_id}/versions",
        json={"parent_version_id": v1_id, "version_label": "2026.immutable", "basis": "GAAP"},
        headers=admin_headers,
    )
    v2_id = res.json()["id"]

    # Validate and approve
    env.client.post(f"/v1/model-versions/{v2_id}/validate", headers=admin_headers)
    env.client.post(f"/v1/model-versions/{v2_id}/approve", headers=actuary["headers"])

    # Locate child entity keys
    with env.Session() as db:
        f_id = db.query(FormulaRegistry.id).filter(FormulaRegistry.model_version_id == v2_id).first()[0]
        po_var = db.query(ModelPublishedOutput.variable_name).filter(ModelPublishedOutput.model_version_id == v2_id).first()[0]
        var_name = db.query(ModelVariableDefinition.variable_name).filter(ModelVariableDefinition.model_version_id == v2_id).first()[0]

    # G-18: ModelVersion PATCH refused on approved version
    res = env.client.patch(f"/v1/model-versions/{v2_id}", json={"notes": "edit"}, headers=admin_headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "MODEL_VERSION_IMMUTABLE"

    # G-20: Variable PATCH refused
    res = env.client.patch(f"/v1/model-versions/{v2_id}/variables/{var_name}", json={"display_name": "x"}, headers=admin_headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "MODEL_VERSION_IMMUTABLE"

    # G-21: Variable DELETE refused
    res = env.client.delete(f"/v1/model-versions/{v2_id}/variables/{var_name}", headers=admin_headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "MODEL_VERSION_IMMUTABLE"

    # G-22: Formula POST refused
    new_formula = FormulaDefinition(
        id=f"f_imm_{v2_id[:6]}",
        name="Imm Formula",
        output_variable="imm_out",
        function_ref="ref",
        model_version_id=v2_id,
        category="imm",
        dependencies=[],
    )
    with env.Session() as db:
        with pytest.raises(Exception) as exc_info:
            register(db, new_formula, ids["admin"])
        assert "MODEL_VERSION_IMMUTABLE" in str(exc_info.value) or "immutable" in str(exc_info.value).lower()

    # G-23: Formula PUT refused
    res = env.client.put(f"/v1/formulas/{f_id}", json={"description": "new"}, headers=admin_headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "MODEL_VERSION_IMMUTABLE"

    # G-24: Formula DELETE refused
    res = env.client.delete(f"/v1/formulas/{f_id}", headers=admin_headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "MODEL_VERSION_IMMUTABLE"

    # G-25: Published output POST refused
    res = env.client.post(
        f"/v1/model-versions/{v2_id}/published-outputs",
        json={"variable_name": "imm_out_new", "display_name": "Imm"},
        headers=admin_headers,
    )
    assert res.status_code == 409 and res.json()["error"]["code"] == "MODEL_VERSION_IMMUTABLE"

    # G-26: Published output PATCH refused
    res = env.client.patch(
        f"/v1/model-versions/{v2_id}/published-outputs/{po_var}",
        json={"display_name": "Imm Patched"},
        headers=admin_headers,
    )
    assert res.status_code == 409 and res.json()["error"]["code"] == "MODEL_VERSION_IMMUTABLE"

    # G-27: Published output DELETE refused
    res = env.client.delete(f"/v1/model-versions/{v2_id}/published-outputs/{po_var}", headers=admin_headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "MODEL_VERSION_IMMUTABLE"

    # Now publish and verify G-19: ModelVersion PATCH refused on published version
    pub_res = env.client.post(f"/v1/model-versions/{v2_id}/publish", headers=admin_headers)
    assert pub_res.status_code == 200

    res_pub = env.client.patch(f"/v1/model-versions/{v2_id}", json={"notes": "edit on published"}, headers=admin_headers)
    assert res_pub.status_code == 409 and res_pub.json()["error"]["code"] == "MODEL_VERSION_IMMUTABLE"


# =============================================================================
# 6. SEGREGATION OF DUTIES & MATERIAL MODIFIER (G-31, G-32)
# =============================================================================

def test_segregation_of_duties_material_modifier(env):
    ids = env.projects["GOV"]
    model_id = ids["model"]
    v1_id = ids["version"]

    creator = env.user("creator@example.com", role="actuary", memberships={"GOV": "editor"})
    modifier = env.user("modifier@example.com", role="actuary", memberships={"GOV": "editor"})
    reviewer = env.user("reviewer_independent@example.com", role="reviewer", memberships={"GOV": "viewer"})

    # Creator creates draft version
    res = env.client.post(
        f"/v1/models/{model_id}/versions",
        json={"parent_version_id": v1_id, "version_label": "2026.sod", "basis": "GAAP"},
        headers=creator["headers"],
    )
    v_id = res.json()["id"]

    # Creator validates
    env.client.post(f"/v1/model-versions/{v_id}/validate", headers=creator["headers"])

    # G-31: Creator attempts self-approval -> 409 SELF_APPROVAL_FORBIDDEN
    self_app = env.client.post(f"/v1/model-versions/{v_id}/approve", headers=creator["headers"])
    assert self_app.status_code == 409
    assert self_app.json()["error"]["code"] == "SELF_APPROVAL_FORBIDDEN"

    # Now Modifier makes a material change (edits a variable definition)
    var_list = env.client.get(f"/v1/model-versions/{v_id}/variables", headers=creator["headers"]).json()["variables"]
    var_name = var_list[0]["variable_name"]
    mod_res = env.client.patch(
        f"/v1/model-versions/{v_id}/variables/{var_name}",
        json={"display_name": "Modified By Modifier"},
        headers=modifier["headers"],
    )
    assert mod_res.status_code == 200

    # Modifier validates the new content
    val_mod = env.client.post(f"/v1/model-versions/{v_id}/validate", headers=modifier["headers"])
    assert val_mod.status_code == 200

    # G-32: Modifier attempts approval -> 409 SELF_APPROVAL_FORBIDDEN (since modifier made the last material edit)
    mod_app = env.client.post(f"/v1/model-versions/{v_id}/approve", headers=modifier["headers"])
    assert mod_app.status_code == 409
    assert mod_app.json()["error"]["code"] == "SELF_APPROVAL_FORBIDDEN"

    # Independent third party (Reviewer) approves -> 200 OK
    rev_app = env.client.post(
        f"/v1/model-versions/{v_id}/approve",
        json={"reason": "Independent actuarial sign-off."},
        headers=reviewer["headers"],
    )
    assert rev_app.status_code == 200
    assert rev_app.json()["status"] == "approved"
    assert rev_app.json()["approved_by"] == reviewer["id"]


# =============================================================================
# 7. DEDICATED models:approve PERMISSION (G-33, G-34)
# =============================================================================

def test_dedicated_models_approve_permission(env):
    ids = env.projects["GOV"]
    model_id = ids["model"]
    v1_id = ids["version"]

    dev = env.user("dev_no_perm@example.com", role="model_developer", memberships={"GOV": "editor"})
    ro = env.user("ro_no_perm@example.com", role="read_only", memberships={"GOV": "viewer"})
    actuary = env.user("act_with_perm@example.com", role="actuary", memberships={"GOV": "editor"})
    admin_headers = env.admin_headers()

    res = env.client.post(
        f"/v1/models/{model_id}/versions",
        json={"parent_version_id": v1_id, "version_label": "2026.rbac", "basis": "GAAP"},
        headers=admin_headers,
    )
    v_id = res.json()["id"]
    env.client.post(f"/v1/model-versions/{v_id}/validate", headers=admin_headers)

    # G-33: Model developer without models:approve cannot approve (403)
    dev_app = env.client.post(f"/v1/model-versions/{v_id}/approve", headers=dev["headers"])
    assert dev_app.status_code == 403

    # Read-only user cannot approve (403)
    ro_app = env.client.post(f"/v1/model-versions/{v_id}/approve", headers=ro["headers"])
    assert ro_app.status_code == 403

    # G-34: Model developer without models:approve cannot reject (403)
    dev_rej = env.client.post(
        f"/v1/model-versions/{v_id}/reject",
        json={"reason": "rejection reason"},
        headers=dev["headers"],
    )
    assert dev_rej.status_code == 403

    # Actuary with models:approve can reject
    act_rej = env.client.post(
        f"/v1/model-versions/{v_id}/reject",
        json={"reason": "Legitimate actuarial rejection reason."},
        headers=actuary["headers"],
    )
    assert act_rej.status_code == 200
    assert act_rej.json()["status"] == "rejected"


# =============================================================================
# 8. MODEL VERSION REJECTION REQUIREMENTS (G-35, G-36)
# =============================================================================

def test_rejection_requires_valid_reason_and_records_provenance(env):
    ids = env.projects["GOV"]
    model_id = ids["model"]
    v1_id = ids["version"]
    admin_headers = env.admin_headers()
    actuary = env.user("actuary_rej@example.com", role="actuary", memberships={"GOV": "editor"})

    res = env.client.post(
        f"/v1/models/{model_id}/versions",
        json={"parent_version_id": v1_id, "version_label": "2026.rej_test", "basis": "GAAP"},
        headers=admin_headers,
    )
    v_id = res.json()["id"]
    env.client.post(f"/v1/model-versions/{v_id}/validate", headers=admin_headers)

    # G-35: Empty or whitespace reason is refused (422 MODEL_REJECTION_REASON_REQUIRED)
    for blank_reason in [None, "", "   ", " \t\n "]:
        rej_res = env.client.post(
            f"/v1/model-versions/{v_id}/reject",
            json={"reason": blank_reason} if blank_reason is not None else {},
            headers=actuary["headers"],
        )
        assert rej_res.status_code == 422
        assert rej_res.json()["error"]["code"] == "MODEL_REJECTION_REASON_REQUIRED"

    # G-36: Valid reason succeeds and records rejection evidence
    valid_reason = "Assumption tables do not match 2026 basis guidelines."
    rej_ok = env.client.post(
        f"/v1/model-versions/{v_id}/reject",
        json={"reason": valid_reason},
        headers=actuary["headers"],
    )
    assert rej_ok.status_code == 200
    rej_json = rej_ok.json()
    assert rej_json["status"] == "rejected"
    assert rej_json["rejection_reason"] == valid_reason
    assert rej_json["rejected_by"] == actuary["id"]
    assert rej_json["rejected_at"] is not None
    assert rej_json["validation_fingerprint"] is None

    # Cannot approve rejected version
    app_rej = env.client.post(f"/v1/model-versions/{v_id}/approve", headers=actuary["headers"])
    assert app_rej.status_code == 409
    assert app_rej.json()["error"]["code"] == "MODEL_VERSION_NOT_VALIDATED"


# =============================================================================
# 9. PROJECT SECURITY & SCOPING (G-37)
# =============================================================================

def test_project_security_and_scoping(env):
    ids = env.projects["GOV"]
    v1_id = ids["version"]

    # Seed a second, distinct project "OTHER"
    other_ids = env.seed("OTHER")
    other_user = env.user("outsider@example.com", role="actuary", memberships={"OTHER": "editor"})

    # outsider has no membership in GOV
    # Attempting to read/modify/validate/approve/reject/publish GOV version from outsider
    for path, method in [
        (f"/v1/model-versions/{v1_id}/structure", "get"),
        (f"/v1/model-versions/{v1_id}/validate", "post"),
        (f"/v1/model-versions/{v1_id}/approve", "post"),
        (f"/v1/model-versions/{v1_id}/reject", "post"),
        (f"/v1/model-versions/{v1_id}/publish", "post"),
    ]:
        client_fn = getattr(env.client, method)
        res = client_fn(path, headers=other_user["headers"])
        assert res.status_code in (403, 404), f"Path {path} should be forbidden/not found, got {res.status_code}"


# =============================================================================
# 10. CANONICAL FINGERPRINT DETERMINISM (G-38, G-39, G-40)
# =============================================================================

def test_canonical_fingerprint_determinism():
    base_calc = {
        "version_number": 2,
        "block_name": "SPIA_Main",
        "profile_name": "Standard",
        "basis": "GAAP",
        "methodology": "GrossPremium",
        "illustrative": False,
        "configuration": {"risk_discount_rate": 0.05, "precision": 6},
    }
    formulas_1 = [
        {"id": "f1", "name": "F1", "output_variable": "var_a", "function_ref": "ref_a", "dependencies": ["x", "y"], "version": "v1"},
        {"id": "f2", "name": "F2", "output_variable": "var_b", "function_ref": "ref_b", "dependencies": ["var_a"], "version": "v1"},
    ]
    formulas_2_reordered = [formulas_1[1], formulas_1[0]]

    variables_1 = [
        {"variable_name": "var_a", "data_type": "number", "unit": "currency", "required": True, "default_value": 0.0},
        {"variable_name": "var_b", "data_type": "number", "unit": "ratio", "required": False, "default_value": 1.0},
    ]
    variables_2_reordered = [variables_1[1], variables_1[0]]

    outputs_1 = [
        {"variable_name": "var_b", "display_name": "B", "unit": "ratio", "aggregation": "end_of_period", "dimension": None},
    ]

    fp_base = compute_model_definition_fingerprint(base_calc, formulas_1, variables_1, outputs_1)
    assert fp_base.startswith(f"{MODEL_FINGERPRINT_SCHEME}:")

    # G-39: Re-ordering formulas or variables yields the exact same fingerprint
    fp_reordered = compute_model_definition_fingerprint(base_calc, formulas_2_reordered, variables_2_reordered, outputs_1)
    assert fp_base == fp_reordered

    # G-38: Non-calculation metadata (not in calculation_fields) is ignored
    calc_with_ignored_metadata = dict(base_calc)
    calc_with_ignored_metadata["notes"] = "Random reviewer note"
    calc_with_ignored_metadata["change_summary"] = "v2 draft"
    fp_ignored = compute_model_definition_fingerprint(calc_with_ignored_metadata, formulas_1, variables_1, outputs_1)
    assert fp_base == fp_ignored

    # G-40: Changing formula expression / dependencies changes fingerprint
    formulas_modified = [
        {"id": "f1", "name": "F1", "output_variable": "var_a", "function_ref": "ref_a_modified", "dependencies": ["x", "y"], "version": "v1"},
        formulas_1[1],
    ]
    fp_formula_mod = compute_model_definition_fingerprint(base_calc, formulas_modified, variables_1, outputs_1)
    assert fp_base != fp_formula_mod

    # Changing variable unit changes fingerprint
    variables_mod = [
        {"variable_name": "var_a", "data_type": "number", "unit": "count", "required": True, "default_value": 0.0},
        variables_1[1],
    ]
    fp_var_mod = compute_model_definition_fingerprint(base_calc, formulas_1, variables_mod, outputs_1)
    assert fp_base != fp_var_mod

    # Changing configuration changes fingerprint
    calc_mod_config = dict(base_calc, configuration={"risk_discount_rate": 0.06, "precision": 6})
    fp_config_mod = compute_model_definition_fingerprint(calc_mod_config, formulas_1, variables_1, outputs_1)
    assert fp_base != fp_config_mod


# =============================================================================
# 11. ARCHIVAL SAFETY
# =============================================================================

def test_model_version_archival_safety(env):
    ids = env.projects["GOV"]
    model_id = ids["model"]
    v1_id = ids["version"]
    admin_headers = env.admin_headers()

    # 1. Cannot archive current version
    del_curr = env.client.delete(f"/v1/model-versions/{v1_id}", headers=admin_headers)
    assert del_curr.status_code == 409
    assert del_curr.json()["error"]["code"] == "CANNOT_ARCHIVE_CURRENT_VERSION"

    # 2. Create and approve a non-current version
    created = env.client.post(
        f"/v1/models/{model_id}/versions",
        json={"parent_version_id": v1_id, "version_label": "2026.app_arch", "basis": "GAAP"},
        headers=admin_headers,
    )
    v2_id = created.json()["id"]
    env.client.post(f"/v1/model-versions/{v2_id}/validate", headers=admin_headers)

    actuary = env.user("actuary_arch@example.com", role="actuary", memberships={"GOV": "editor"})
    env.client.post(f"/v1/model-versions/{v2_id}/approve", headers=actuary["headers"])

    # Cannot archive approved version
    del_app = env.client.delete(f"/v1/model-versions/{v2_id}", headers=admin_headers)
    assert del_app.status_code == 409
    assert del_app.json()["error"]["code"] == "CANNOT_ARCHIVE_APPROVED_VERSION"

    # 3. Create a draft version and associate with a projection set
    draft_res = env.client.post(
        f"/v1/models/{model_id}/versions",
        json={"version_label": "2026.draft_in_use", "basis": "GAAP"},
        headers=admin_headers,
    )
    v_draft_id = draft_res.json()["id"]

    with env.Session() as db:
        ps = ProjectionSet(
            project_id=ids["project"],
            name="PS using draft",
            model_version_id=v_draft_id,
            status="draft",
            valuation_date=date(2026, 1, 1),
            horizon_months=12,
            time_step="monthly",
            created_by=ids["admin"],
            updated_by=ids["admin"],
        )
        db.add(ps)
        db.commit()

    # Attempt to archive draft in use
    del_in_use = env.client.delete(f"/v1/model-versions/{v_draft_id}", headers=admin_headers)
    assert del_in_use.status_code == 409
    assert del_in_use.json()["error"]["code"] == "MODEL_VERSION_IN_USE"

    # 4. Archiving an unused draft succeeds
    free_draft = env.client.post(
        f"/v1/models/{model_id}/versions",
        json={"version_label": "2026.free_draft", "basis": "GAAP"},
        headers=admin_headers,
    )
    free_id = free_draft.json()["id"]
    del_free = env.client.delete(f"/v1/model-versions/{free_id}", headers=admin_headers)
    assert del_free.status_code == 204
