"""Run cancel and retry behavior over frozen packages and Parquet evidence."""

import pytest

from app.core.execution import run_state
from app.db.models.projection import RunSet
from app.db.models.run import Run
from app.db.models.run_package import RunAttempt, RunPackage
from app.db.models.user import User
from app.services import run_control_service, run_execution_service, run_package_verification
from app.services.run_execution_service import claim_run

from .wp1_support import Env


@pytest.fixture()
def env():
    environment = Env()
    environment.seed("A")
    yield environment
    environment.close()


def post(env: Env, path: str, headers: dict | None = None):
    return env.client.post(f"/v1{path}", headers=headers or env.admin_headers())


def test_pending_run_is_cancelled_immediately_and_never_claimed(env):
    submitted = env.submit("A", ["Base"])
    run_id = submitted["by_scenario"]["Base"]
    response = post(env, f"/runs/{run_id}/cancel")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["can_cancel"] is False and body["can_retry"] is True
    assert body["cancel_requested_at"] is not None

    with env.Session() as db:
        run = db.get(Run, run_id)
        assert run.accepted_attempt_number is None
        assert claim_run(db, run_id, "late-worker") is None
        assert db.get(RunSet, run.run_set_id).status == "cancelled"
    manifest = env.get(f"/runs/{run_id}/manifest").json()["final_manifest"]["manifest"]
    assert manifest["runtime"]["status"] == "cancelled"
    assert manifest["runtime"]["attempt"] is None
    results = env.get(f"/runs/{run_id}/results")
    assert results.status_code == 409
    assert results.json()["error"]["code"] == "RESULTS_NOT_AVAILABLE"
    assert post(env, f"/runs/{run_id}/cancel").status_code == 409


def test_running_cancel_records_request_without_racing_terminal_state(env):
    run_id = env.submit("A", ["Base"])["by_scenario"]["Base"]
    with env.Session() as db:
        assert claim_run(db, run_id, "worker-a") is not None
    response = post(env, f"/runs/{run_id}/cancel")
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "running"
    assert response.json()["cancel_requested_at"] is not None
    events = env.get(f"/runs/{run_id}/events").json()["events"]
    assert events[-1]["step"] == "cancel_requested"


def test_worker_stops_between_policies_and_keeps_partial_evidence_noncanonical(env, monkeypatch):
    submitted = env.submit("A", ["Base"])
    run_id = submitted["by_scenario"]["Base"]
    real = run_execution_service.run_policy
    calls = {"count": 0}

    def request_cancel(data, policy, functions):
        calls["count"] += 1
        if calls["count"] == 6:
            with env.Session() as control_db:
                admin = control_db.get(User, env.projects["A"]["admin"])
                run_control_service.cancel_run(control_db, run_id, admin)
        return real(data, policy, functions)

    monkeypatch.setattr(run_execution_service, "run_policy", request_cancel)
    env.execute(submitted)

    run = env.run(run_id)
    assert run.status == "cancelled"
    assert run.progress_done == 6
    assert run.accepted_attempt_number is None
    with env.Session() as db:
        attempt = db.query(RunAttempt).filter_by(run_id=run_id).one()
        assert attempt.status == "cancelled"
        assert attempt.cleanup_status == "retained_noncanonical"
    assert env.row_counts(run_id)[0] > 0
    manifest = env.get(f"/runs/{run_id}/manifest").json()["final_manifest"]["manifest"]
    assert manifest["runtime"]["status"] == "cancelled"
    assert manifest["runtime"]["attempt"]["status"] == "cancelled"
    summary = env.get(f"/runs/{run_id}/summary")
    assert summary.status_code == 200
    assert summary.json()["results_available"] is False and summary.json()["headline"] is None
    assert env.get(f"/runs/{run_id}/results").status_code == 409


def test_failed_run_retry_reuses_frozen_configuration_and_succeeds(env, monkeypatch):
    submitted = env.submit("A", ["Base"])
    source_id = submitted["by_scenario"]["Base"]
    real = run_execution_service.run_policy
    monkeypatch.setattr(
        run_execution_service, "run_policy",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("forced failure")),
    )
    env.execute(submitted)
    monkeypatch.setattr(run_execution_service, "run_policy", real)
    source = env.run(source_id)
    assert source.status == "failed"

    response = post(env, f"/runs/{source_id}/retry")
    assert response.status_code == 202, response.text
    retry_id = response.json()["id"]
    retried = env.run(retry_id)
    assert retried.status == "success"
    assert retried.retried_from_run_id == source_id
    assert retried.run_set_id == source.run_set_id
    assert retried.run_package_fingerprint == source.run_package_fingerprint

    with env.Session() as db:
        source_package = db.query(RunPackage).filter_by(run_id=source_id).one()
        retry_package = db.query(RunPackage).filter_by(run_id=retry_id).one()
        assert retry_package.id != source_package.id
        assert retry_package.package["configuration"] == source_package.package["configuration"]
        assert retry_package.package["identity"]["frozen_for_run_id"] == retry_id
        assert retry_package.fingerprint == source_package.fingerprint
        assert db.get(RunSet, source.run_set_id).status == "partial_success"
    assert env.summary(retry_id)["headline"]["value"] == pytest.approx(893043.9157255866)


def test_retry_refuses_success_and_changed_build(env, monkeypatch):
    successful = env.submit("A", ["Base"])
    env.execute(successful)
    success_id = successful["by_scenario"]["Base"]
    response = post(env, f"/runs/{success_id}/retry")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RUN_NOT_RETRYABLE"

    cancelled_id = env.submit("A", ["Base"])["by_scenario"]["Base"]
    assert post(env, f"/runs/{cancelled_id}/cancel").status_code == 202
    monkeypatch.setattr(
        run_package_verification,
        "current_build_identity",
        lambda: {"engine_version": "changed", "build_fingerprint": "0" * 64},
    )
    mismatch = post(env, f"/runs/{cancelled_id}/retry")
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "BUILD_IDENTITY_MISMATCH"


def test_project_viewer_cannot_cancel_or_retry(env):
    run_id = env.submit("A", ["Base"])["by_scenario"]["Base"]
    viewer = env.user("run-viewer@example.com", role="actuary", memberships={"A": "viewer"})
    assert post(env, f"/runs/{run_id}/cancel", viewer["headers"]).status_code == 403
    assert env.run(run_id).status == run_state.PENDING
    assert post(env, f"/runs/{run_id}/cancel").status_code == 202
    assert post(env, f"/runs/{run_id}/retry", viewer["headers"]).status_code == 403

    env.seed("B")
    other_run = env.submit("B", ["Base"])["by_scenario"]["Base"]
    editor = env.user("project-a-editor@example.com", memberships={"A": "editor"})
    assert post(env, f"/runs/{other_run}/cancel", editor["headers"]).status_code == 404
    assert post(env, f"/runs/{other_run}/retry", editor["headers"]).status_code == 404


def test_mixed_success_and_cancelled_run_set_is_partial_success(env):
    submitted = env.submit("A", ["Base", "Low Interest Rate"])
    assert post(env, f"/runs/{submitted['by_scenario']['Base']}/cancel").status_code == 202
    env.execute(submitted)
    with env.Session() as db:
        assert db.get(RunSet, submitted["run_set"]["id"]).status == "partial_success"
