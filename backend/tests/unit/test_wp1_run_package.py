"""Work Package 1 — run package fingerprint semantics (pure, no database)."""

import copy

import pytest

from app.core.execution import run_package
from app.core.execution.fingerprints import fingerprint, fingerprint_sequence

CONFIGURATION = {
    "project": {"id": "p1"},
    "projection_set": {"id": "ps1", "name": "SPIA", "version_label": "v1"},
    "model": {"id": "m1", "name": "SPIA model", "product_code": "SPIA"},
    "model_version": {"id": "mv1", "version_label": "v0.1", "basis": "Illustrative", "methodology": None,
                      "methodology_version": None, "block_name": None, "profile_name": None,
                      "illustrative": True},
    "valuation_date": "2026-12-31", "horizon_months": 24, "time_step": "monthly", "execution_backend": "cpu",
    "parameters": {},
    "formulas": [], "variables": [],
    "datasets": {"inforce": [{"id": "f1", "name": "x.csv", "version_label": None, "record_count": 2,
                              "fingerprint": "aa"}],
                 "assumption_tables": [], "factor_tables": []},
    "table_bindings": {},
    "scenario": {"id": "s1", "name": "Base", "fingerprint": "bb", "overrides": []},
    "outputs": {"output_variables": ["reserve"], "trace_scope": {"mode": "none", "policy_ids": []},
                "max_traced_policies": 25, "published": {}},
    "build": {"app_version": "0.1.0", "engine_version": "m1-cpu-1", "code_version": "development",
              "source_commit": None},
}


def sealed(configuration=None, **identity):
    return run_package.seal(
        {"package_id": identity.get("package_id", "pkg-1"), "frozen_for_run_id": identity.get("run", "r1"),
         "created_at": identity.get("created_at", "2026-09-30T00:00:00Z")},
        {"projection_set": {"status": identity.get("status", "validated")}},
        copy.deepcopy(configuration or CONFIGURATION),
    )


def test_same_configuration_gives_the_same_fingerprint_whatever_the_identity_or_governance():
    first = sealed()
    second = sealed(package_id="pkg-2", run="r2", created_at="2027-01-01T00:00:00Z", status="approved")
    assert first["fingerprint"] == second["fingerprint"] == fingerprint(CONFIGURATION)


@pytest.mark.parametrize("path, value", [
    (("parameters",), {"lapse_shock": 1.1}),
    (("horizon_months",), 36),
    (("scenario", "overrides"), [{"target_variable": "discount_rate_annual", "operation": "set", "value": 0.03}]),
    (("datasets", "inforce", 0, "fingerprint"), "changed"),
    (("outputs", "output_variables"), ["reserve", "expected_payment"]),
    (("build", "code_version"), "release-2"),
    (("model_version", "id"), "mv2"),
])
def test_any_material_configuration_change_changes_the_fingerprint(path, value):
    changed = copy.deepcopy(CONFIGURATION)
    target = changed
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert sealed(changed)["fingerprint"] != sealed()["fingerprint"]


def test_verify_accepts_an_intact_package_and_refuses_an_altered_one():
    package = sealed()
    assert run_package.verify(package, package["fingerprint"]) == package["configuration"]
    altered = copy.deepcopy(package)
    altered["configuration"]["horizon_months"] = 6
    with pytest.raises(run_package.RunPackageError) as raised:
        run_package.verify(altered, package["fingerprint"])
    assert raised.value.code == "PACKAGE_TAMPERED"
    with pytest.raises(run_package.RunPackageError):
        run_package.verify(package, "a-different-expected-fingerprint")
    unsupported = {**package, "schema_version": "mentoramp.run_package/v0"}
    with pytest.raises(run_package.RunPackageError) as raised:
        run_package.verify(unsupported, package["fingerprint"])
    assert raised.value.code == "PACKAGE_SCHEMA_UNSUPPORTED"


def test_streamed_dataset_fingerprint_equals_the_whole_list_fingerprint():
    rows = [{"policy_id": f"P{i}", "value": i / 7, "text": "é"} for i in range(50)]
    assert fingerprint_sequence(iter(rows)) == fingerprint(rows)
    assert fingerprint_sequence([]) == fingerprint([])


def test_trace_scope_is_capped_by_the_frozen_limit():
    policies = [f"P{i}" for i in range(10)]
    assert run_package.traced_policy_ids({"mode": "all"}, policies, 3) == frozenset(policies[:3])
    assert run_package.traced_policy_ids({"mode": "selected_policies", "policy_ids": ["P9", "X"]},
                                         policies, 25) == frozenset({"P9"})
    assert run_package.traced_policy_ids({"mode": "none"}, policies, 25) == frozenset()
