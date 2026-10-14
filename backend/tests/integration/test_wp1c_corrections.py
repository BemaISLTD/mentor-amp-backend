"""WP1 correction pass — integration regressions for every correction item.

P0 #1 executable formula / build identity   P0 #2 terminal finalization   P0 #3 variable isolation
#4 package identity   #6 run-set resolution   #7 frozen scenario evidence   #8 override governance
#9 inforce fingerprint v2   #10 preflight deferred status
(#5 retention and #12 exact aggregation need PostgreSQL: tests/postgres.)
"""

import dataclasses
import json

import pytest
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.artifacts import read_attempt_artifact_rows
from app.core.execution import run_state
from app.core.execution.fingerprints import scenario_fingerprint
from app.core.formula_engine.formulas import FORMULA_REGISTRY
from app.db.models.inforce import InforceFile, InforceRecord
from app.db.models.model_variable import ModelVariableDefinition
from app.db.models.projection import ProjectionSet, RunEvent
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact, RunManifest
from app.db.models.run_package import RunAttempt, RunPackage
from app.db.models.scenario import ScenarioTable
from app.db.models.user import User
from app.products.spia_lite import config as spia
from app.services import projection_set_service, run_execution_service, run_finalization, run_package_verification
from app.services.common import ServiceError
from app.services.run_execution_service import claim_run
from app.services.run_submission_service import submit_run_set
from app.services.run_finalization import FinalizationError, TerminalOutcome

from .wp1_support import Env


@pytest.fixture()
def env():
    environment = Env()
    environment.seed("A")
    yield environment
    environment.close()


@pytest.fixture()
def calculation_spy(monkeypatch):
    calls = {"count": 0}
    real = run_execution_service.run_policy

    def spy(*args, **kwargs):
        calls["count"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(run_execution_service, "run_policy", spy)
    return calls


def attempt_of(env: Env, run_id: str) -> RunAttempt:
    with env.Session() as db:
        attempt = db.query(RunAttempt).filter_by(run_id=run_id).one()
        db.expunge(attempt)
        return attempt


def assert_refused(env: Env, run_id: str, error_type: str, calls: dict) -> RunAttempt:
    run = env.run(run_id)
    assert run.status == "failed" and run.accepted_attempt_number is None
    assert calls["count"] == 0, "calculation must not begin"
    assert env.row_counts(run_id) == (0, 0)
    attempt = attempt_of(env, run_id)
    assert attempt.error_type == error_type
    response = env.get(f"/runs/{run_id}/aggregates")
    assert response.status_code == 409
    manifest = env.get(f"/runs/{run_id}/manifest").json()["final_manifest"]["manifest"]
    assert manifest["runtime"]["status"] == "failed"
    assert manifest["runtime"]["attempt"]["error_type"] == error_type
    return attempt


# =============================================================================
# P0 #1 — executable formula identity and build identity
# =============================================================================

def expected_payment_changed(monthly_payment, survival_probability):
    return monthly_payment * survival_probability * 1.01  # different actuarial code


def test_callable_changed_after_submission_is_refused(env, calculation_spy, monkeypatch):
    submitted = env.submit("A", ["Base"])
    ref = spia.function_ref("expected_payment")
    frozen = FORMULA_REGISTRY[ref]
    # Simulate a code change behind the same function_ref (the stale registration still carries
    # the OLD fingerprint: execution must recompute it from the callable, not trust the cache).
    monkeypatch.setitem(FORMULA_REGISTRY, ref, dataclasses.replace(frozen, func=expected_payment_changed))
    env.execute(submitted)
    attempt = assert_refused(env, submitted["by_scenario"]["Base"], "FORMULA_IMPLEMENTATION_MISMATCH", calculation_spy)
    with env.Session() as db:
        event = db.query(RunEvent).filter_by(run_id=submitted["by_scenario"]["Base"], step="failed").one()
    mismatch = event.data["mismatched"][0]
    assert mismatch["function_ref"] == ref
    assert mismatch["expected"]["implementation_fingerprint"] == frozen.implementation_fingerprint
    assert mismatch["actual"]["implementation_fingerprint"] != frozen.implementation_fingerprint
    assert attempt.cleanup_status == "not_needed"


def test_build_changed_after_submission_is_refused(env, calculation_spy, monkeypatch):
    submitted = env.submit("A", ["Base"])
    current = run_package_verification.current_build_identity()
    monkeypatch.setattr(run_package_verification, "current_build_identity",
                        lambda: {**current, "build_fingerprint": "0" * 64})
    env.execute(submitted)
    assert_refused(env, submitted["by_scenario"]["Base"], "BUILD_IDENTITY_MISMATCH", calculation_spy)


def test_package_records_executable_identity(env):
    run_id = env.submit("A", ["Base"])["by_scenario"]["Base"]
    configuration = env.get(f"/runs/{run_id}/package").json()["package"]["configuration"]
    for entry in configuration["formulas"]:
        registered = FORMULA_REGISTRY[entry["function_ref"]]
        assert entry["implementation"] == registered.identity()
    build = configuration["build"]
    assert len(build["build_fingerprint"]) == 64 and build["identity_source"] == "computed"
    assert "source_dirty" in build and "source_commit" in build


def test_submission_is_refused_without_an_immutable_build_identity(env, monkeypatch):
    from app.services import build_info

    monkeypatch.setattr(build_info.settings, "app_env", "production")
    monkeypatch.setattr(build_info.settings, "build_fingerprint", None)
    build_info.build_identity.cache_clear()
    try:
        with pytest.raises(ServiceError) as raised:
            env.submit("A", ["Base"])
    finally:
        build_info.build_identity.cache_clear()
    assert "BUILD_IDENTITY_UNAVAILABLE" in {p["code"] for p in raised.value.details["problems"]}


# =============================================================================
# P0 #2 — terminal finalization
# =============================================================================

@pytest.fixture()
def no_backoff(monkeypatch):
    monkeypatch.setattr(run_finalization, "RETRY_BACKOFF_SECONDS", 0)


def commit_failures(monkeypatch, *, commit_first: bool, times: int = 1) -> dict:
    """Make the terminal commit raise ``times`` times: before committing, or after (ack lost)."""
    real = run_finalization._commit
    state = {"calls": 0}

    def flaky(db):
        state["calls"] += 1
        if state["calls"] <= times:
            if commit_first:
                real(db)
            raise OperationalError("COMMIT", {}, Exception("server closed the connection unexpectedly"))
        real(db)

    monkeypatch.setattr(run_finalization, "_commit", flaky)
    return state


def evidence_counts(env: Env, run_id: str) -> tuple[int, int]:
    with env.Session() as db:
        manifests = db.query(func.count()).select_from(RunManifest).filter(RunManifest.run_id == run_id).scalar()
        attempts = db.query(func.count(RunAttempt.id)).filter(RunAttempt.run_id == run_id).scalar()
    return manifests, attempts


def test_terminal_commit_failing_before_commit_is_retried(env, monkeypatch, no_backoff):
    state = commit_failures(monkeypatch, commit_first=False)
    submitted = env.submit("A", ["Base"])
    env.execute(submitted)
    run_id = submitted["by_scenario"]["Base"]
    assert state["calls"] == 2  # failed once, finished on the fresh-session retry
    run = env.run(run_id)
    assert run.status == "success" and run.accepted_attempt_number == 1
    assert evidence_counts(env, run_id) == (1, 1)
    rows = env.get(f"/runs/{run_id}/results", variable="reserve", month=0).json()
    assert rows["total"] == 25  # canonical results visible exactly once
    with env.Session() as db:
        finalized = db.query(RunEvent).filter_by(run_id=run_id, step="finalized").one()
        assert finalized.data["recovered"] is True and finalized.data["state"] == "finalized"


def test_committed_terminal_state_with_lost_acknowledgement_is_recognised(env, monkeypatch, no_backoff):
    commit_failures(monkeypatch, commit_first=True)
    submitted = env.submit("A", ["Base"])
    env.execute(submitted)
    run_id = submitted["by_scenario"]["Base"]
    assert evidence_counts(env, run_id) == (1, 1)
    with env.Session() as db:
        manifest = db.get(RunManifest, run_id)
        run = db.get(Run, run_id)
        assert run.final_manifest_fingerprint == manifest.fingerprint
        finalized = db.query(RunEvent).filter_by(run_id=run_id, step="finalized").one()
        assert finalized.data["state"] == "already_finalized"  # recognised, nothing rewritten
        assert db.query(func.count(RunEvent.id)).filter_by(run_id=run_id, step="complete").scalar() == 1


def test_finalizing_twice_is_idempotent(env):
    submitted = env.submit("A", ["Base"])
    env.execute(submitted)
    run_id = submitted["by_scenario"]["Base"]
    with env.Session() as db:
        attempt = db.query(RunAttempt).filter_by(run_id=run_id).one()
        outcome = TerminalOutcome.from_dict(attempt.intended_outcome)
        before = db.get(RunManifest, run_id).fingerprint
        events_before = db.query(func.count(RunEvent.id)).filter_by(run_id=run_id).scalar()
    with env.Session() as db:
        result = run_finalization.finalize_attempt(db, outcome)
    assert result.state == "already_finalized"
    with env.Session() as db:
        assert db.get(RunManifest, run_id).fingerprint == before
        assert db.query(func.count(RunEvent.id)).filter_by(run_id=run_id).scalar() == events_before
        assert run_finalization.recover_run_finalization(db, run_id).state == "already_finalized"
    assert evidence_counts(env, run_id) == (1, 1)


def test_worker_that_died_after_recording_its_outcome_is_recovered(env, monkeypatch, no_backoff):
    commit_failures(monkeypatch, commit_first=False, times=99)  # the database never comes back
    submitted = env.submit("A", ["Base"])
    env.execute(submitted)
    run_id = submitted["by_scenario"]["Base"]
    run = env.run(run_id)
    assert run.status == "running" and run.accepted_attempt_number is None
    assert env.row_counts(run_id)[0] > 0  # committed batches exist...
    assert env.get(f"/runs/{run_id}/aggregates").status_code == 409  # ...but are not results
    assert attempt_of(env, run_id).intended_outcome["status"] == "success"

    monkeypatch.undo()  # the database is back; a reaper calls recovery
    with env.Session() as db:
        result = run_finalization.recover_run_finalization(db, run_id)
    assert result.state == "finalized" and result.recovered is True
    assert env.run(run_id).status == "success"
    assert env.get(f"/runs/{run_id}/aggregates").status_code == 200
    assert evidence_counts(env, run_id) == (1, 1)


def test_attempt_without_a_recorded_outcome_is_left_for_the_reaper(env):
    run_id = env.submit("A", ["Base"])["by_scenario"]["Base"]
    with env.Session() as db:
        assert claim_run(db, run_id, "worker-that-dies") is not None
        assert run_finalization.recover_run_finalization(db, run_id).state == "calculation_incomplete"
    assert env.run(run_id).status == "running"


def test_inconsistent_terminal_state_withholds_results(env):
    submitted = env.submit("A", ["Base"])
    env.execute(submitted)
    run_id = submitted["by_scenario"]["Base"]
    with env.Session() as db:
        # An impossible combination: the run says success, its accepted attempt says running.
        db.execute(update(RunAttempt.__table__).where(RunAttempt.__table__.c.run_id == run_id)
                   .values(status="running"))
        db.commit()
    response = env.get(f"/runs/{run_id}/aggregates")
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "FINALIZATION_INCONSISTENT"
    assert env.get(f"/runs/{run_id}").json()["results_available"] is False
    with env.Session() as db, pytest.raises(FinalizationError) as raised:
        run_finalization.recover_run_finalization(db, run_id)
    assert raised.value.code == "FINALIZATION_INCONSISTENT"


# =============================================================================
# P0 #3 — model-version-scoped variables
# =============================================================================

@pytest.fixture()
def two_projects():
    environment = Env()
    environment.seed("A")
    environment.seed("B")
    yield environment
    environment.close()


def definition(env: Env, key: str, name: str) -> ModelVariableDefinition:
    with env.Session() as db:
        row = db.query(ModelVariableDefinition).filter_by(
            model_version_id=env.projects[key]["version"], variable_name=name).one()
        db.expunge(row)
        return row


def frozen_rate(env: Env, run_id: str) -> float:
    variables = env.get(f"/runs/{run_id}/package").json()["package"]["configuration"]["variables"]
    return next(v for v in variables if v["name"] == "discount_rate_annual")["source"]["value"]


def test_same_variable_name_resolves_independently_per_model_version(two_projects):
    env = two_projects
    patched = env.client.patch(
        f"/v1/model-versions/{env.projects['B']['version']}/variables/discount_rate_annual",
        headers=env.admin_headers(), json={"source": {"type": "manual", "value": 0.06}, "default_value": 0.06},
    )
    assert patched.status_code == 200 and patched.json()["version"] == "v2"
    a, b = env.submit("A", ["Base"]), env.submit("B", ["Base"])
    env.execute(a)
    env.execute(b)
    a_run, b_run = a["by_scenario"]["Base"], b["by_scenario"]["Base"]
    assert frozen_rate(env, a_run) == 0.045 and frozen_rate(env, b_run) == 0.06
    assert env.summary(b_run)["headline"]["value"] < env.summary(a_run)["headline"]["value"]
    assert definition(env, "A", "discount_rate_annual").version == "v1"  # A untouched


def test_a_project_editor_cannot_change_another_projects_variables(two_projects):
    env = two_projects
    editor = env.user("editor-a@example.com", memberships={"A": "editor"})
    viewer = env.user("viewer-a@example.com", memberships={"A": "viewer"})
    body = {"source": {"type": "manual", "value": 0.2}}
    b_url = f"/v1/model-versions/{env.projects['B']['version']}/variables/discount_rate_annual"
    a_url = f"/v1/model-versions/{env.projects['A']['version']}/variables/discount_rate_annual"
    assert env.client.patch(b_url, json=body, headers=editor["headers"]).status_code == 404
    assert env.client.get(f"/v1/model-versions/{env.projects['B']['version']}/variables",
                          headers=editor["headers"]).status_code == 404
    assert env.client.patch(a_url, json=body, headers=viewer["headers"]).status_code == 403
    assert env.client.patch(a_url, json=body, headers=editor["headers"]).status_code == 200
    # The global semantic catalog is administrator-only.
    assert env.client.put("/v1/variables/discount_rate_annual", json={"required": False},
                          headers=editor["headers"]).status_code == 403
    assert definition(env, "B", "discount_rate_annual").source["value"] == 0.045


def test_changing_project_a_cannot_affect_project_bs_future_runs(two_projects):
    env = two_projects
    with env.Session() as db:
        row = db.query(ModelVariableDefinition).filter_by(
            model_version_id=env.projects["A"]["version"], variable_name="discount_rate_annual").one()
        row.source = {"type": "manual", "value": 0.2}
        db.commit()
    b_run = env.submit("B", ["Base"])["by_scenario"]["Base"]
    assert frozen_rate(env, b_run) == 0.045


def test_a_model_version_cannot_define_one_variable_twice(env):
    existing = definition(env, "A", "discount_rate_annual")
    with env.Session() as db:
        db.add(ModelVariableDefinition(model_version_id=existing.model_version_id, variable_name="discount_rate_annual",
                                       kind="manual", data_type="number", source={"type": "manual", "value": 1}))
        with pytest.raises(IntegrityError):
            db.commit()


def test_a_variable_edit_via_the_api_after_submission_does_not_change_the_run(env):
    submitted = env.submit("A", ["Base"])
    patched = env.client.patch(
        f"/v1/model-versions/{env.projects['A']['version']}/variables/discount_rate_annual",
        headers=env.admin_headers(), json={"source": {"type": "manual", "value": 0.10}},
    )
    assert patched.status_code == 200
    env.execute(submitted)
    run_id = submitted["by_scenario"]["Base"]
    assert frozen_rate(env, run_id) == 0.045
    assert env.summary(run_id)["status"] == "success"


def test_a_variable_the_model_does_not_define_blocks_validation(env):
    with env.Session() as db:
        db.query(ModelVariableDefinition).filter_by(
            model_version_id=env.projects["A"]["version"], variable_name="monthly_payment").delete()
        db.commit()
        result = projection_set_service.validate(db, env.projects["A"]["projection_set"])
    check = next(c for c in result["validation"]["checks"] if c["code"] == "model_version_selected")
    assert result["status"] == "draft" and "monthly_payment" in check["message"]


# =============================================================================
# #4 package identity and #7 frozen scenario evidence
# =============================================================================

def tamper_identity(env: Env, run_id: str, **changes) -> None:
    with env.Session() as db:
        package = db.query(RunPackage).filter_by(run_id=run_id).one()
        document = dict(package.package)
        document["identity"] = {**document["identity"], **changes}
        db.execute(update(RunPackage.__table__).where(RunPackage.__table__.c.id == package.id).values(package=document))
        db.commit()


@pytest.mark.parametrize("field", ["package_id", "frozen_for_run_id", "project_id", "run_set_id"])
def test_relabelled_package_identity_is_refused(env, calculation_spy, field):
    submitted = env.submit("A", ["Base"])
    run_id = submitted["by_scenario"]["Base"]
    tamper_identity(env, run_id, **{field: "someone-else"})
    env.execute(submitted)
    assert_refused(env, run_id, "PACKAGE_IDENTITY_MISMATCH", calculation_spy)


def test_run_pointing_at_another_packages_fingerprint_is_refused(env, calculation_spy):
    submitted = env.submit("A", ["Base", "Low Interest Rate"])
    base, low = submitted["by_scenario"]["Base"], submitted["by_scenario"]["Low Interest Rate"]
    with env.Session() as db:
        other = db.get(Run, low).run_package_fingerprint
        db.execute(update(Run.__table__).where(Run.__table__.c.id == base).values(run_package_fingerprint=other))
        db.commit()
    env.execute(submitted)
    assert_refused(env, base, "PACKAGE_IDENTITY_MISMATCH", {"count": 0})


def test_mutated_run_scenario_index_is_refused_and_evidence_uses_the_frozen_scenario(env, calculation_spy):
    submitted = env.submit("A", ["Base"])
    run_id = submitted["by_scenario"]["Base"]
    with env.Session() as db:
        db.execute(update(Run.__table__).where(Run.__table__.c.id == run_id)
                   .values(scenario_id=env.projects["A"]["scenarios"]["Low Interest Rate"]))
        db.commit()
    env.execute(submitted)
    assert_refused(env, run_id, "RUN_INDEX_MISMATCH", calculation_spy)


def test_result_and_trace_rows_carry_the_frozen_scenario_id(env):
    submitted = env.submit("A", ["Low Interest Rate"])
    env.execute(submitted)
    run_id = submitted["by_scenario"]["Low Interest Rate"]
    frozen = env.get(f"/runs/{run_id}/package").json()["package"]["configuration"]["scenario"]["id"]
    with env.Session() as db:
        labels = {
            row["scenario_id"] for row in read_attempt_artifact_rows(db, run_id, "outputs", 1)
        }
    assert labels == {frozen}


# =============================================================================
# #6 run-set resolution
# =============================================================================

def run_set(env: Env, submitted: dict) -> dict:
    return env.get(f"/run-sets/{submitted['run_set']['id']}").json()


def test_run_set_records_inherited_scenarios(env):
    ids = env.projects["A"]
    submitted = env.submit("A")  # no request-level scenarios: inherit the Projection Set's
    detail = run_set(env, submitted)
    assert detail["scenario_ids"] == [ids["scenarios"]["Base"], ids["scenarios"]["Low Interest Rate"]]
    resolved = detail["resolution"]["projection_sets"][0]
    assert resolved["scenario_source"] == "projection_set" and len(resolved["runs"]) == 2
    assert detail["resolution"]["requested_scenario_ids"] == []


def test_run_set_records_request_level_scenarios(env):
    ids = env.projects["A"]
    detail = run_set(env, env.submit("A", ["Low Interest Rate"]))
    assert detail["scenario_ids"] == [ids["scenarios"]["Low Interest Rate"]]
    assert detail["resolution"]["projection_sets"][0]["scenario_source"] == "request"
    assert detail["resolution"]["requested_scenario_ids"] == [ids["scenarios"]["Low Interest Rate"]]


def test_run_set_records_each_projection_sets_own_scenarios(env):
    ids = env.projects["A"]
    with env.Session() as db:
        admin = db.get(User, ids["admin"])
        copy = projection_set_service.duplicate(db, ids["projection_set"], "v2", admin)
        db.get(ProjectionSet, copy["id"]).scenario_ids = [ids["scenarios"]["Low Interest Rate"]]
        db.commit()
        assert projection_set_service.validate(db, copy["id"])["status"] == "validated"
        result = submit_run_set(db, {"project_id": ids["project"], "name": "two sets",
                                     "projection_set_ids": [ids["projection_set"], copy["id"]]}, admin)
    detail = env.get(f"/run-sets/{result['run_set']['id']}").json()
    assert detail["run_count"] == 3
    assert detail["scenario_ids"] == [ids["scenarios"]["Base"], ids["scenarios"]["Low Interest Rate"]]
    mapping = {entry["projection_set_id"]: entry for entry in detail["resolution"]["projection_sets"]}
    assert mapping[ids["projection_set"]]["scenario_ids"] == [ids["scenarios"]["Base"], ids["scenarios"]["Low Interest Rate"]]
    assert mapping[copy["id"]]["scenario_ids"] == [ids["scenarios"]["Low Interest Rate"]]
    assert len(mapping[copy["id"]]["runs"]) == 1


# =============================================================================
# #8 scenario override governance (end to end) and #9 inforce fingerprint v2
# =============================================================================

def add_scenario(env: Env, name: str, overrides: list) -> str:
    with env.Session() as db:
        base = db.get(ScenarioTable, env.projects["A"]["scenarios"]["Base"])
        scenario = ScenarioTable(set_id=base.set_id, scenario_name=name, overrides=overrides, status="validated",
                                 fingerprint=scenario_fingerprint(overrides), scenario_type="deterministic")
        db.add(scenario)
        db.commit()
        return scenario.id


def test_a_scenario_cannot_override_a_variable_the_model_protects(env):
    scenario_id = add_scenario(env, "Payment shock", [
        {"target_variable": "monthly_payment", "operation": "multiply", "value": 2.0},
    ])
    response = env.client.post("/v1/run-sets", headers=env.admin_headers(), json={
        "project_id": env.projects["A"]["project"], "projection_set_ids": [env.projects["A"]["projection_set"]],
        "scenario_ids": [scenario_id],
    })
    assert response.status_code == 422
    assert {p["code"] for p in response.json()["error"]["details"]["problems"]} == {"SCENARIO_TARGET_NOT_OVERRIDABLE"}


def test_overlapping_scenario_overrides_cannot_run(env):
    scenario_id = add_scenario(env, "Stacked", [
        {"target_variable": "discount_rate_annual", "operation": "set", "value": 0.03, "applies_from_period": 0},
        {"target_variable": "discount_rate_annual", "operation": "add", "value": 0.01,
         "applies_from_period": 6, "applies_to_period": 12},
    ])
    response = env.client.post("/v1/run-sets", headers=env.admin_headers(), json={
        "project_id": env.projects["A"]["project"], "projection_set_ids": [env.projects["A"]["projection_set"]],
        "scenario_ids": [scenario_id],
    })
    assert response.status_code == 422
    assert "SCENARIO_OVERRIDE_OVERLAP" in {p["code"] for p in response.json()["error"]["details"]["problems"]}


def test_policy_id_changed_after_submission_is_detected(env, calculation_spy):
    submitted = env.submit("A", ["Base"])
    with env.Session() as db:
        record = db.query(InforceRecord).filter_by(policy_id="SPIA-0007").one()
        record.policy_id = "SPIA-0099"  # data untouched: only the identity changes
        db.commit()
    env.execute(submitted)
    assert_refused(env, submitted["by_scenario"]["Base"], "DATASET_FINGERPRINT_MISMATCH", calculation_spy)


def test_duplicate_policy_ids_are_refused_at_execution(env, calculation_spy):
    submitted = env.submit("A", ["Base"])
    with env.Session() as db:
        original = db.query(InforceRecord).filter_by(policy_id="SPIA-0003").one()
        db.add(InforceRecord(file_id=original.file_id, policy_id="SPIA-0003", data=dict(original.data)))
        db.commit()
    env.execute(submitted)
    assert_refused(env, submitted["by_scenario"]["Base"], "DATASET_INVALID", calculation_spy)


def test_an_inforce_file_with_an_old_fingerprint_scheme_cannot_be_submitted(env):
    with env.Session() as db:
        db.get(InforceFile, env.projects["A"]["inforce"]).fingerprint_scheme = "inforce-v1"
        db.commit()
    with pytest.raises(ServiceError) as raised:
        env.submit("A", ["Base"])
    assert "INPUT_FINGERPRINT_OUTDATED" in {p["code"] for p in raised.value.details["problems"]}


# =============================================================================
# #10 preflight
# =============================================================================

def test_preflight_does_not_report_an_unbuilt_capability_as_passing(env):
    response = env.client.post("/v1/run-sets/preflight", headers=env.admin_headers(), json={
        "project_id": env.projects["A"]["project"], "projection_set_ids": [env.projects["A"]["projection_set"]],
    })
    body = response.json()
    reports = next(check for check in body["checks"] if check["code"] == "reports_attached")
    assert reports["status"] == "deferred" and "not available" in reports["message"]
    assert body["ok"] is True  # deferred is neither a pass nor a failure
    assert all(check["status"] == "pass" for check in body["checks"] if check["code"] != "reports_attached")


def test_terminal_statuses_other_than_success_expose_no_results(env):
    submitted = env.submit("A", ["Base", "Low Interest Rate"])
    base, low = submitted["by_scenario"]["Base"], submitted["by_scenario"]["Low Interest Rate"]
    with env.Session() as db:
        run_state.transition_run(db.get(Run, low), run_state.CANCELLED)
        db.commit()
        assert claim_run(db, base, "worker") is not None
    for run_id in (base, low):
        assert env.get(f"/runs/{run_id}/results").status_code == 409
        assert env.get(f"/runs/{run_id}/trace/policies").status_code == 409


def test_legacy_runs_without_a_package_remain_readable(env):
    """Runs submitted before Work Package 1 have no package, only a migrated legacy manifest."""
    import hashlib
    import uuid
    from datetime import date

    from sqlalchemy import insert

    run_id = str(uuid.uuid4())
    manifest = {"schema_version": "m1", "output_variables": ["expected_payment", "reserve"],
                "variables": [{"name": "discount_rate_annual", "kind": "manual", "version": "v1"}]}
    fingerprint = hashlib.sha256(run_id.encode()).hexdigest()
    with env.Session() as db:
        db.add(Run(id=run_id, project_id=env.projects["A"]["project"], name="legacy", status="success",
                   model_version_id=env.projects["A"]["version"], horizon_months=2,
                   valuation_date=date(2026, 12, 31), accepted_attempt_number=1, attempt_count=1,
                   final_manifest_fingerprint=fingerprint))
        db.flush()
        db.add(RunAttempt(run_id=run_id, attempt_number=1, status="success", worker_id="legacy-m1"))
        db.add(RunManifest(run_id=run_id, fingerprint=fingerprint, manifest=manifest,
                           schema_version="mentoramp.m1_manifest/legacy", attempt_number=1))
        rows = [
            {"run_id": run_id, "attempt_number": 1, "policy_id": "P1", "scenario_id": "s",
             "projection_month": month, "variable_name": "expected_payment",
             "value_json": json.dumps({"value": 100.0}), "product": "spia_lite"}
            for month in (1, 2)
        ]
        stored = env.artifact_store.write_rows(run_id, "outputs", rows)
        db.add(RunArtifact(
            run_id=run_id, attempt_number=1, artifact_type="outputs", storage_uri=stored.uri,
            storage_backend="local", file_format="parquet", schema_version="m1-v1",
            row_count=stored.row_count, checksum_sha256=stored.checksum_sha256,
            partition={"attempt_number": 1},
        ))
        db.commit()
    response = env.get(f"/runs/{run_id}/aggregates", variables="expected_payment")
    assert response.status_code == 200, response.text
    assert response.json()["rows"][1]["values"]["expected_payment"] == 200.0
    assert env.get(f"/runs/{run_id}").json()["legacy_run"] is True
