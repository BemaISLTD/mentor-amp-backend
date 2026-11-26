"""Phase 7 product-aware upload, mapping, validation, version and approval flows."""

import csv
from pathlib import Path

import pytest

from .test_data_manager import data_manager, principal, run_flow  # noqa: F401


ROOT = Path(__file__).resolve().parents[3]


def _fixture(code: str) -> tuple[str, list[dict]]:
    path = ROOT / "samples" / code.lower() / f"synthetic_{code.lower()}_inforce.csv"
    text = path.read_text(encoding="utf-8")
    fields = next(csv.reader(text.splitlines()))
    return text, [{"source_column": field, "target_field": field} for field in fields]


@pytest.mark.parametrize("code", ("IDI", "VA", "PRT", "LIFE"))
def test_phase7_product_data_manager_flow_is_four_eyes_approved(data_manager, code):
    client, _db, project, current, creator, reviewer = data_manager
    source, mapping = _fixture(code)
    _session, validation, committed = run_flow(
        client,
        project.id,
        "liability_inforce",
        f"{code} Phase 7 inforce",
        source,
        mapping,
        {"valuation_date": "2026-12-31"},
    )
    assert validation["status"] == "passed"
    assert committed["status"] == "validated"

    self_approval = client.post(
        f"/v1/datasets/inforce/{committed['id']}/approve", json={"reason": "self review"}
    )
    assert self_approval.status_code == 409
    assert self_approval.json()["error"]["code"] == "SELF_APPROVAL_FORBIDDEN"

    current["user"] = principal(
        reviewer.id, "reviewer", ("imports:read", "imports:approve", "projects:read")
    )
    approved = client.post(
        f"/v1/datasets/inforce/{committed['id']}/approve",
        json={"reason": "Independent Phase 7 review complete"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_by"] == reviewer.id
    assert approved.json()["committed_by"] == creator.id


def test_life_v2_retains_and_compares_v1(data_manager):
    client, _db, project, current, _creator, reviewer = data_manager
    source, mapping = _fixture("LIFE")
    _session, _validation, first = run_flow(
        client, project.id, "liability_inforce", "LIFE readiness", source, mapping,
        {"valuation_date": "2026-12-31"},
    )
    current["user"] = principal(
        reviewer.id, "reviewer", ("imports:read", "imports:approve", "projects:read")
    )
    assert client.post(
        f"/v1/datasets/inforce/{first['id']}/approve", json={"reason": "v1 reviewed"}
    ).status_code == 200

    current["user"] = principal("import-creator")
    changed = source.replace(",20,1200,100,MONTHLY,", ",20,1260,105,MONTHLY,", 1)
    _session, _validation, second = run_flow(
        client, project.id, "liability_inforce", "LIFE readiness", changed, mapping,
        {"valuation_date": "2026-12-31"}, replaces=first["id"],
    )
    assert second["version_number"] == 2
    versions = client.get(f"/v1/datasets/inforce/{second['id']}/versions")
    assert versions.status_code == 200
    assert [row["version_number"] for row in versions.json()["versions"]] == [2, 1]
    comparison = client.get(
        f"/v1/datasets/inforce/{first['id']}/compare", params={"other_id": second["id"]}
    )
    assert comparison.status_code == 200, comparison.text
    assert comparison.json()["changed_count"] == 1


def test_life_invalid_frequency_and_term_are_blocked(data_manager):
    client, _db, project, _current, _creator, _reviewer = data_manager
    source, mapping = _fixture("LIFE")
    invalid = source.replace(
        ",20,1200,100,MONTHLY,", ",,1200,1200,MONTHLY,", 1
    )
    opened = client.post("/v1/import-sessions", json={
        "project_id": project.id,
        "category": "liability_inforce",
        "name": "Invalid LIFE readiness",
        "options": {"valuation_date": "2026-12-31"},
    })
    session_id = opened.json()["id"]
    assert client.post(
        f"/v1/import-sessions/{session_id}/files",
        files={"file": ("invalid-life.csv", invalid.encode(), "text/csv")},
    ).status_code == 200
    assert client.put(
        f"/v1/import-sessions/{session_id}/mapping", json={"fields": mapping}
    ).status_code == 200
    validation = client.post(f"/v1/import-sessions/{session_id}/validate")
    assert validation.status_code == 201
    assert validation.json()["error_count"] >= 2
    issues = client.get(f"/v1/validation-runs/{validation.json()['id']}/issues").json()["issues"]
    assert {item["code"] for item in issues} >= {
        "INVALID_LIFE_PREMIUM_FREQUENCY", "INVALID_LIFE_STATE"
    }
