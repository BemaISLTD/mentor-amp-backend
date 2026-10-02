"""Work Package 1 — execution safety.

E. Atomic claim: two workers can never execute the same run.
F. Partial failure: rows written before a failure never become visible results.
G. Result access: only success runs have results; partial_success needs an explicit opt-in;
   pending, running, failed and cancelled runs have none.
"""

import pytest
from sqlalchemy import func, insert
from sqlalchemy.exc import IntegrityError

from app.core.execution import run_state
from app.core.projection_engine.engine import EngineError, PolicyResult
from app.db.models.run import Run
from app.db.models.run_output import RunOutput
from app.db.models.run_package import RunAttempt
from app.services import run_execution_service
from app.services.run_execution_service import claim_run

from .wp1_support import Env


@pytest.fixture()
def env():
    environment = Env()
    environment.seed("A")
    yield environment
    environment.close()


RESULT_ENDPOINTS = (
    "/runs/{id}/results",
    "/runs/{id}/aggregates",
    "/runs/{id}/export.csv",
    "/runs/{id}/trace/policies",
)


def assert_no_results(env: Env, run_id: str, code: str = "RESULTS_NOT_AVAILABLE") -> None:
    for path in RESULT_ENDPOINTS:
        response = env.get(path.format(id=run_id))
        assert response.status_code == 409, (path, response.text)
        assert response.json()["error"]["code"] == code
    trace = env.get(f"/runs/{run_id}/trace", policy_id="SPIA-0001", month=1, variable="expected_payment")
    assert trace.status_code == 409


# =============================================================================
# E: atomic claim
# =============================================================================

def test_only_one_of_two_competing_workers_claims_a_run(env):
    run_id = env.submit("A", ["Base"])["by_scenario"]["Base"]
    worker_a, worker_b = env.Session(), env.Session()
    try:
        # Both workers have read the run and seen it pending (the race the old code lost).
        assert worker_a.get(Run, run_id).status == "pending"
        assert worker_b.get(Run, run_id).status == "pending"
        first = claim_run(worker_a, run_id, "worker-a")
        second = claim_run(worker_b, run_id, "worker-b")
        assert first is not None and first.attempt_number == 1 and first.worker_id == "worker-a"
        assert second is None
    finally:
        worker_a.close()
        worker_b.close()
    with env.Session() as db:
        run = db.get(Run, run_id)
        assert run.status == "running" and run.attempt_count == 1
        assert db.query(func.count(RunAttempt.id)).filter_by(run_id=run_id).scalar() == 1
        # A run that is already running, or finished, cannot be claimed again either.
        assert claim_run(db, run_id, "worker-c") is None


def test_a_finished_run_is_never_executed_again(env):
    submitted = env.submit("A", ["Base"])
    env.execute(submitted)
    run_id = submitted["by_scenario"]["Base"]
    with env.Session() as db:
        assert run_execution_service.execute_run(db, run_id, "late-worker") is None
        assert db.query(func.count(RunAttempt.id)).filter_by(run_id=run_id).scalar() == 1


def test_attempt_numbers_are_unique_per_run_in_the_database(env):
    run_id = env.submit("A", ["Base"])["by_scenario"]["Base"]
    with env.Session() as db:
        assert claim_run(db, run_id, "worker-a") is not None
        db.add(RunAttempt(run_id=run_id, attempt_number=1, status="running", worker_id="rogue"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_illegal_status_transitions_are_refused():
    run = type("RunLike", (), {"status": "success"})()
    with pytest.raises(run_state.IllegalTransition):
        run_state.transition_run(run, "running")
    for current, new in (("failed", "success"), ("cancelled", "running"), ("pending", "success")):
        with pytest.raises(run_state.IllegalTransition):
            run_state.check_run_transition(current, new)
    run_state.check_run_transition("pending", "running")
    run_state.check_run_transition("pending", "cancelled")
    run_state.check_run_transition("running", "partial_success")


def test_the_database_refuses_unknown_run_statuses(env):
    run_id = env.submit("A", ["Base"])["by_scenario"]["Base"]
    with env.Session() as db:
        db.get(Run, run_id).status = "completed"
        with pytest.raises(IntegrityError):
            db.commit()


# =============================================================================
# F: partial failure
# =============================================================================

def fail_on_call(monkeypatch, number: int) -> None:
    real = run_execution_service.run_policy
    calls = {"count": 0}

    def exploding(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == number:
            raise RuntimeError("simulated worker failure")
        return real(*args, **kwargs)

    monkeypatch.setattr(run_execution_service, "run_policy", exploding)


def test_failure_after_committed_batches_leaves_no_visible_results(env, monkeypatch):
    submitted = env.submit("A", ["Base"])
    run_id = submitted["by_scenario"]["Base"]
    # Batches are committed every 5 policies: policies 1-10 are already in the database
    # when policy 12 fails.
    fail_on_call(monkeypatch, 12)
    env.execute(submitted)

    run = env.run(run_id)
    assert run.status == "failed" and run.accepted_attempt_number is None
    with env.Session() as db:
        attempt = db.query(RunAttempt).filter_by(run_id=run_id).one()
        assert attempt.status == "failed" and attempt.error_type == "EXECUTION_ERROR"
        assert attempt.cleanup_status == "deleted"
    assert env.row_counts(run_id) == (0, 0)  # the committed batches were removed
    assert_no_results(env, run_id)
    view = env.get(f"/runs/{run_id}").json()
    assert view["results_available"] is False and view["status"] == "failed"
    manifest = env.get(f"/runs/{run_id}/manifest").json()["final_manifest"]["manifest"]
    assert manifest["runtime"]["status"] == "failed"
    assert "simulated worker failure" in manifest["runtime"]["errors"][0]


def test_rows_of_a_failed_attempt_stay_invisible_even_if_cleanup_fails(env, monkeypatch):
    submitted = env.submit("A", ["Base"])
    run_id = submitted["by_scenario"]["Base"]
    fail_on_call(monkeypatch, 12)

    def cleanup_that_cannot_reach_the_database(db, run_id, attempt_number):
        return "failed"

    monkeypatch.setattr(run_execution_service, "_delete_attempt_rows", cleanup_that_cannot_reach_the_database)
    env.execute(submitted)

    outputs, traces = env.row_counts(run_id)
    assert outputs > 0 and traces > 0  # the orphaned rows really are still there...
    assert env.run(run_id).accepted_attempt_number is None
    assert_no_results(env, run_id)  # ...but no API returns them
    with env.Session() as db:
        assert db.query(RunAttempt).filter_by(run_id=run_id).one().cleanup_status == "failed"


def test_rows_not_belonging_to_the_accepted_attempt_are_never_read(env):
    submitted = env.submit("A", ["Base"])
    env.execute(submitted)
    run_id = submitted["by_scenario"]["Base"]
    before = env.get(f"/runs/{run_id}/aggregates", variables="reserve").json()["rows"][0]["values"]["reserve"]
    with env.Session() as db:
        # A stray row from some other (non-accepted) attempt.
        db.execute(insert(RunOutput.__table__), [{
            "run_id": run_id, "attempt_number": 99, "policy_id": "SPIA-0001", "scenario_id": "x",
            "projection_month": 0, "variable_name": "reserve", "value": {"value": 1e12},
        }])
        db.commit()
    after = env.get(f"/runs/{run_id}/aggregates", variables="reserve").json()["rows"][0]["values"]["reserve"]
    assert after == before


# =============================================================================
# G: result access by status
# =============================================================================

def test_pending_and_cancelled_runs_have_no_results(env):
    submitted = env.submit("A", ["Base", "Low Interest Rate"])
    base, low = submitted["by_scenario"]["Base"], submitted["by_scenario"]["Low Interest Rate"]
    assert_no_results(env, base)  # pending
    with env.Session() as db:
        run_state.transition_run(db.get(Run, low), run_state.CANCELLED)
        db.commit()
    assert_no_results(env, low)
    comparison = env.get("/comparisons", baseline_run_id=base, current_run_id=low)
    assert comparison.status_code == 409


def test_running_runs_have_no_results(env):
    run_id = env.submit("A", ["Base"])["by_scenario"]["Base"]
    with env.Session() as db:
        assert claim_run(db, run_id, "worker-a") is not None
    assert env.run(run_id).status == "running"
    assert_no_results(env, run_id)


def test_success_runs_have_complete_results(env):
    submitted = env.submit("A", ["Base", "Low Interest Rate"])
    env.execute(submitted)
    base, low = submitted["by_scenario"]["Base"], submitted["by_scenario"]["Low Interest Rate"]
    aggregates = env.get(f"/runs/{base}/aggregates")
    assert aggregates.status_code == 200 and aggregates.json()["complete"] is True
    assert aggregates.json()["attempt_number"] == 1
    export = env.get(f"/runs/{base}/export.csv")
    assert export.status_code == 200 and export.headers["x-mentoramp-complete"] == "true"
    comparison = env.get("/comparisons", baseline_run_id=base, current_run_id=low)
    assert comparison.status_code == 200 and comparison.json()["complete"] is True


def test_partial_success_results_require_an_explicit_opt_in(env, monkeypatch):
    real = run_execution_service.run_policy

    def one_policy_fails(data, policy, functions):
        if policy.policy_id == "SPIA-0005":
            result = PolicyResult(policy_id=policy.policy_id)
            result.error = EngineError("lookup_failed", "simulated policy-level failure", "x", 3)
            return result
        return real(data, policy, functions)

    monkeypatch.setattr(run_execution_service, "run_policy", one_policy_fails)
    submitted = env.submit("A", ["Base"])
    env.execute(submitted)
    run_id = submitted["by_scenario"]["Base"]
    run = env.run(run_id)
    assert run.status == "partial_success" and run.accepted_attempt_number == 1

    assert_no_results(env, run_id, code="RESULTS_INCOMPLETE")
    opted_in = env.get(f"/runs/{run_id}/aggregates", include_partial="true")
    assert opted_in.status_code == 200 and opted_in.json()["complete"] is False
    export = env.get(f"/runs/{run_id}/export.csv", include_partial="true")
    assert export.status_code == 200 and export.headers["x-mentoramp-complete"] == "false"
    assert "INCOMPLETE" in export.headers["content-disposition"]
    summary = env.summary(run_id)
    assert summary["complete"] is False and summary["failed_policy_count"] == 1

    dashboard = env.get(f"/projects/{env.projects['A']['project']}/dashboard").json()
    assert dashboard["headline"] is None  # the dashboard headline only uses complete runs
