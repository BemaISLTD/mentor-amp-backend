"""Model version governance integration tests.

Verifies:
1. Model version validation lifecycle (valid formulas/outputs pass, incomplete fail).
2. Segregation of duties (4-eyes principle): creators cannot approve their own versions (SELF_APPROVAL_FORBIDDEN).
3. RBAC on approvals: model developers cannot approve, only users with imports:approve / reviewer / actuary / admin.
4. Model version rejection flow with reason and audit log.
5. Immutability: approved/published versions reject updates to version fields and variable definitions (MODEL_VERSION_IMMUTABLE).
6. Archival safety: blocking deletion of active/current versions, approved versions, and versions in use by projection sets.
7. Publication: activating version as current and marking parent model active.
"""

from datetime import date

import pytest

from app.db.models.audit_log import AuditLog
from app.db.models.modeling import Model, ModelVersion
from app.db.models.projection import ProjectionSet

from .wp1_support import Env


@pytest.fixture()
def env():
    environment = Env()
    environment.seed("GOV")
    yield environment
    environment.close()


def test_model_version_validation_flow(env):
    ids = env.projects["GOV"]
    model_id = ids["model"]
    v1_id = ids["version"]

    # 1. Clone a new version from seeded v1 (has complete SPIA formulas and published outputs)
    res = env.client.post(
        f"/v1/models/{model_id}/versions",
        json={"parent_version_id": v1_id, "version_label": "2026.2", "basis": "GAAP"},
        headers=env.admin_headers(),
    )
    assert res.status_code == 201, res.text
    v2_id = res.json()["id"]
    assert res.json()["status"] == "draft"

    # Validate v2: should succeed and transition status to validated
    val_res = env.client.post(
        f"/v1/model-versions/{v2_id}/validate",
        headers=env.admin_headers(),
    )
    assert val_res.status_code == 200, val_res.text
    val_json = val_res.json()
    assert val_json["status"] == "validated"
    assert val_json["validation"]["status"] == "validated"

    # Verify audit log recorded for validation
    with env.Session() as db:
        v2 = db.get(ModelVersion, v2_id)
        assert v2.status == "validated"
        audit = (
            db.query(AuditLog)
            .filter(AuditLog.entity_id == v2_id, AuditLog.action == "model_version.validated")
            .first()
        )
        assert audit is not None

    # 2. Create an empty draft version without parent (missing formulas and outputs)
    bare_res = env.client.post(
        f"/v1/models/{model_id}/versions",
        json={"version_label": "2026.bare", "basis": "GAAP"},
        headers=env.admin_headers(),
    )
    assert bare_res.status_code == 201, bare_res.text
    bare_id = bare_res.json()["id"]

    bare_val = env.client.post(
        f"/v1/model-versions/{bare_id}/validate",
        headers=env.admin_headers(),
    )
    assert bare_val.status_code == 200
    # Status remains draft because validation checks failed
    assert bare_val.json()["status"] == "draft"
    assert bare_val.json()["validation"]["status"] == "invalid"


def test_model_version_segregation_of_duties_and_approval(env):
    ids = env.projects["GOV"]
    model_id = ids["model"]
    v1_id = ids["version"]

    dev = env.user("dev@example.com", role="model_developer", memberships={"GOV": "editor"})
    actuary = env.user("actuary@example.com", role="actuary", memberships={"GOV": "editor"})
    dev2 = env.user("dev2@example.com", role="model_developer", memberships={"GOV": "editor"})

    # Developer creates a cloned draft version
    created = env.client.post(
        f"/v1/models/{model_id}/versions",
        json={"parent_version_id": v1_id, "version_label": "2026.dev", "basis": "GAAP"},
        headers=dev["headers"],
    )
    assert created.status_code == 201
    v_id = created.json()["id"]

    # Validate the version first
    val = env.client.post(f"/v1/model-versions/{v_id}/validate", headers=dev["headers"])
    assert val.status_code == 200
    assert val.json()["status"] == "validated"

    # 1. Model developer cannot approve (403 Forbidden)
    dev_app = env.client.post(f"/v1/model-versions/{v_id}/approve", headers=dev["headers"])
    assert dev_app.status_code == 403

    # 2. Peer model developer without reviewer/actuary role cannot approve (403 Forbidden)
    peer_app = env.client.post(f"/v1/model-versions/{v_id}/approve", headers=dev2["headers"])
    assert peer_app.status_code == 403

    # 3. Actuary approves successfully
    app_res = env.client.post(
        f"/v1/model-versions/{v_id}/approve",
        headers=actuary["headers"],
        json={"reason": "Actuarial peer review passed."},
    )
    assert app_res.status_code == 200, app_res.text
    app_json = app_res.json()
    assert app_json["status"] == "approved"
    assert app_json["published_by"] == actuary["id"]
    assert app_json["published_at"] is not None

    # Verify audit log
    with env.Session() as db:
        audit = (
            db.query(AuditLog)
            .filter(AuditLog.entity_id == v_id, AuditLog.action == "model_version.approved")
            .first()
        )
        assert audit is not None
        assert audit.context.get("reason") == "Actuarial peer review passed."

    # 4. Self-approval forbidden: Actuary creates another version and attempts self-approval
    act_created = env.client.post(
        f"/v1/models/{model_id}/versions",
        json={"parent_version_id": v1_id, "version_label": "2026.act", "basis": "GAAP"},
        headers=actuary["headers"],
    )
    assert act_created.status_code == 201
    act_v_id = act_created.json()["id"]

    env.client.post(f"/v1/model-versions/{act_v_id}/validate", headers=actuary["headers"])

    self_app = env.client.post(f"/v1/model-versions/{act_v_id}/approve", headers=actuary["headers"])
    assert self_app.status_code == 409
    assert self_app.json()["error"]["code"] == "SELF_APPROVAL_FORBIDDEN"


def test_model_version_rejection_flow(env):
    ids = env.projects["GOV"]
    model_id = ids["model"]
    v1_id = ids["version"]

    dev = env.user("dev3@example.com", role="model_developer", memberships={"GOV": "editor"})
    reviewer = env.user("reviewer@example.com", role="reviewer", memberships={"GOV": "viewer"})

    created = env.client.post(
        f"/v1/models/{model_id}/versions",
        json={"parent_version_id": v1_id, "version_label": "2026.rej", "basis": "GAAP"},
        headers=dev["headers"],
    )
    v_id = created.json()["id"]

    # Validate
    env.client.post(f"/v1/model-versions/{v_id}/validate", headers=dev["headers"])

    # Reviewer rejects with reason
    rej_res = env.client.post(
        f"/v1/model-versions/{v_id}/reject",
        headers=reviewer["headers"],
        json={"reason": "Incorrect mortality tables selected."},
    )
    assert rej_res.status_code == 200, rej_res.text
    assert rej_res.json()["status"] == "rejected"

    # Cannot approve a rejected version
    app_res = env.client.post(f"/v1/model-versions/{v_id}/approve", headers=reviewer["headers"])
    assert app_res.status_code == 409
    assert app_res.json()["error"]["code"] == "MODEL_VERSION_NOT_VALIDATED"


def test_model_version_immutability(env):
    ids = env.projects["GOV"]
    model_id = ids["model"]
    v1_id = ids["version"]

    actuary = env.user("actuary2@example.com", role="actuary", memberships={"GOV": "editor"})
    dev = env.user("dev4@example.com", role="model_developer", memberships={"GOV": "editor"})

    created = env.client.post(
        f"/v1/models/{model_id}/versions",
        json={"parent_version_id": v1_id, "version_label": "2026.locked", "basis": "GAAP"},
        headers=dev["headers"],
    )
    v_id = created.json()["id"]

    # Draft versions can be edited and variables updated
    draft_patch = env.client.patch(
        f"/v1/model-versions/{v_id}",
        headers=dev["headers"],
        json={"notes": "draft notes"},
    )
    assert draft_patch.status_code == 200

    # Get a variable name
    vars_res = env.client.get(f"/v1/model-versions/{v_id}/variables", headers=dev["headers"])
    assert vars_res.status_code == 200
    var_name = vars_res.json()["variables"][0]["variable_name"]

    draft_var_patch = env.client.patch(
        f"/v1/model-versions/{v_id}/variables/{var_name}",
        headers=dev["headers"],
        json={"display_name": "Updated in draft"},
    )
    assert draft_var_patch.status_code == 200

    # Now validate and approve
    env.client.post(f"/v1/model-versions/{v_id}/validate", headers=dev["headers"])
    app_res = env.client.post(f"/v1/model-versions/{v_id}/approve", headers=actuary["headers"])
    assert app_res.status_code == 200

    # After approval, edits are strictly blocked (409 MODEL_VERSION_IMMUTABLE)
    locked_patch = env.client.patch(
        f"/v1/model-versions/{v_id}",
        headers=dev["headers"],
        json={"notes": "illicit edit on approved version"},
    )
    assert locked_patch.status_code == 409
    assert locked_patch.json()["error"]["code"] == "MODEL_VERSION_IMMUTABLE"

    locked_var_patch = env.client.patch(
        f"/v1/model-versions/{v_id}/variables/{var_name}",
        headers=dev["headers"],
        json={"display_name": "Ill-advised variable edit"},
    )
    assert locked_var_patch.status_code == 409
    assert locked_var_patch.json()["error"]["code"] == "MODEL_VERSION_IMMUTABLE"


def test_model_version_archival_safety(env):
    ids = env.projects["GOV"]
    model_id = ids["model"]
    v1_id = ids["version"]

    admin_headers = env.admin_headers()

    # 1. Cannot archive current version (v1 is current from seed)
    del_curr = env.client.delete(f"/v1/model-versions/{v1_id}", headers=admin_headers)
    assert del_curr.status_code == 409
    assert del_curr.json()["error"]["code"] == "CANNOT_ARCHIVE_CURRENT_VERSION"

    # 2. Create and approve a non-current version
    created = env.client.post(
        f"/v1/models/{model_id}/versions",
        json={"parent_version_id": v1_id, "version_label": "2026.app", "basis": "GAAP"},
        headers=admin_headers,
    )
    v2_id = created.json()["id"]
    env.client.post(f"/v1/model-versions/{v2_id}/validate", headers=admin_headers)

    # Approve with another actuary
    actuary = env.user("actuary3@example.com", role="actuary", memberships={"GOV": "editor"})
    env.client.post(f"/v1/model-versions/{v2_id}/approve", headers=actuary["headers"])

    # Cannot archive approved version
    del_app = env.client.delete(f"/v1/model-versions/{v2_id}", headers=admin_headers)
    assert del_app.status_code == 409
    assert del_app.json()["error"]["code"] == "CANNOT_ARCHIVE_APPROVED_VERSION"

    # 3. Create a draft version and associate it with a projection set
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

    # Attempt to archive draft that is in use
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


def test_model_version_publish_flow(env):
    ids = env.projects["GOV"]
    model_id = ids["model"]
    v1_id = ids["version"]

    admin_headers = env.admin_headers()
    actuary = env.user("actuary4@example.com", role="actuary", memberships={"GOV": "editor"})

    created = env.client.post(
        f"/v1/models/{model_id}/versions",
        json={"parent_version_id": v1_id, "version_label": "2026.pub", "basis": "GAAP"},
        headers=admin_headers,
    )
    v2_id = created.json()["id"]

    # 1. Publishing draft version fails (409 MODEL_VERSION_NOT_READY)
    pub_draft = env.client.post(f"/v1/model-versions/{v2_id}/publish", headers=admin_headers)
    assert pub_draft.status_code == 409
    assert pub_draft.json()["error"]["code"] == "MODEL_VERSION_NOT_READY"

    # 2. Validate and approve
    env.client.post(f"/v1/model-versions/{v2_id}/validate", headers=admin_headers)
    env.client.post(f"/v1/model-versions/{v2_id}/approve", headers=actuary["headers"])

    # 3. Publish approved version
    pub_res = env.client.post(f"/v1/model-versions/{v2_id}/publish", headers=admin_headers)
    assert pub_res.status_code == 200, pub_res.text
    pub_json = pub_res.json()
    assert pub_json["is_current"] is True
    assert pub_json["status"] == "approved"
    assert pub_json["published_by"] == ids["admin"]

    # Check that previous current version is no longer current and model is active
    with env.Session() as db:
        old_v1 = db.get(ModelVersion, v1_id)
        assert old_v1.is_current is False

        new_v2 = db.get(ModelVersion, v2_id)
        assert new_v2.is_current is True

        model = db.get(Model, model_id)
        assert model.status == "active"
