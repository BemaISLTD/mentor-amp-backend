"""Integration tests for M6-01 Unified Execution Planner API & Governance."""

import pytest

from app.core.execution import backends as exec_backends
from app.core.execution.backends import (
    BACKEND_CPU,
    BACKEND_GPU,
    BACKEND_SPARK,
    BACKEND_SPARK_GPU,
    BackendCapability,
    BackendCapabilityRegistry,
    FALLBACK_STRICT_FAIL,
)
from app.db.models.audit_log import AuditLog
from app.db.models.projection import RunSet
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact, RunManifest
from app.db.models.run_package import RunPackage
from app.db.models.user import User
from app.services import run_execution_service, run_submission_service

from .wp1_support import Env


@pytest.fixture()
def env():
    environment = Env()
    environment.seed("A", horizon=12)
    environment.seed("B", horizon=12)
    yield environment
    environment.close()


def _base_payload(env: Env, project_key: str = "A", **extra) -> dict:
    ids = env.projects[project_key]
    return {
        "project_id": ids["project"],
        "name": "M6-01 Planner Run",
        "projection_set_ids": [ids["projection_set"]],
        "scenario_ids": [ids["scenarios"]["Base"]],
        **extra,
    }


def test_capability_discovery_and_system_status_contract(env: Env):
    unauth = env.client.get("/v1/execution/capabilities")
    assert unauth.status_code == 401

    resp = env.get("/execution/capabilities")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["default_backend"] == "cpu"
    assert set(body["backends"]) == {"cpu", "spark", "gpu", "spark_gpu"}
    assert body["backends"]["cpu"]["available"] is True
    assert body["backends"]["cpu"]["accelerated"] is False
    assert body["backends"]["gpu"]["available"] is False
    assert body["hardware"]["cuda_gpu_detected"] is False

    status = env.get("/system/status")
    assert status.status_code == 200, status.text
    engine = status.json()["engine"]
    assert engine["execution_backend"] == "cpu"
    assert "cpu" in engine["available_execution_backends"]


def test_cpu_default_behavior_and_fingerprint_invariance_across_backends(env: Env):
    default_resp = env.client.post("/v1/run-sets", headers=env.admin_headers(), json=_base_payload(env))
    assert default_resp.status_code == 202, default_resp.text
    default_run = default_resp.json()["runs"][0]

    auto_resp = env.client.post(
        "/v1/run-sets",
        headers=env.admin_headers(),
        json=_base_payload(env, execution_backend="auto"),
    )
    assert auto_resp.status_code == 202, auto_resp.text
    auto_run = auto_resp.json()["runs"][0]

    gpu_fb_resp = env.client.post(
        "/v1/run-sets",
        headers=env.admin_headers(),
        json=_base_payload(env, execution_backend="gpu", fallback_policy="allow_cpu_fallback"),
    )
    assert gpu_fb_resp.status_code == 202, gpu_fb_resp.text
    gpu_fb_run = gpu_fb_resp.json()["runs"][0]

    # Critical Test #8: Backend choice MUST NOT mutate RunPackage mathematical input fingerprint.
    assert default_run["run_package_fingerprint"] == auto_run["run_package_fingerprint"]
    assert default_run["run_package_fingerprint"] == gpu_fb_run["run_package_fingerprint"]

    # Critical Test #1: Default CPU behavior & Parquet bytes identical.
    assert env.summary(default_run["id"])["status"] == "success"
    assert env.summary(auto_run["id"])["status"] == "success"
    assert env.summary(gpu_fb_run["id"])["status"] == "success"

    def parquet_blobs(run_id: str):
        with env.Session() as db:
            return sorted(
                (a.artifact_type, a.row_count, env.artifact_store.read_bytes(a.storage_uri))
                for a in db.query(RunArtifact).filter_by(run_id=run_id, attempt_number=1).all()
            )

    assert parquet_blobs(default_run["id"]) == parquet_blobs(auto_run["id"])
    assert parquet_blobs(default_run["id"]) == parquet_blobs(gpu_fb_run["id"])

    # Critical Test #9: Provenance persisted on Run view, Attempt, Manifest, Events, RunSet, Audit.
    view = env.get(f"/runs/{gpu_fb_run['id']}").json()
    assert view["compute"]["backend"] == "cpu"
    assert view["execution"]["requested_backend"] == "gpu"
    assert view["execution"]["planned_backend"] == "cpu"
    assert view["execution"]["actual_backend"] == "cpu"
    assert view["execution"]["fallback_used"] is True
    assert view["execution"]["fallback_reason"] == "GPU_HARDWARE_UNAVAILABLE"
    assert view["execution"]["accelerated"] is False

    attempts = env.get(f"/runs/{gpu_fb_run['id']}/attempts").json()["attempts"]
    assert len(attempts) == 1
    assert attempts[0]["execution"]["actual_backend"] == "cpu"
    assert attempts[0]["execution"]["fallback_used"] is True

    manifest = env.get(f"/runs/{gpu_fb_run['id']}/manifest").json()["final_manifest"]["manifest"]
    assert manifest["runtime"]["execution"]["requested_backend"] == "gpu"
    assert manifest["runtime"]["execution"]["actual_backend"] == "cpu"
    assert manifest["runtime"]["execution"]["fallback_used"] is True
    assert manifest["runtime"]["execution"]["fallback_reason"] == "GPU_HARDWARE_UNAVAILABLE"

    events = env.get(f"/runs/{gpu_fb_run['id']}/events").json()["events"]
    assert any(ev["step"] == "execution_fallback" and ev["level"] == "warning" for ev in events)

    with env.Session() as db:
        rs = db.get(RunSet, gpu_fb_resp.json()["run_set"]["id"])
        assert rs.resolution["execution"]["execution_backend"] == "gpu"
        assert db.query(AuditLog).filter_by(action="run_set.submitted", entity_id=rs.id).count() == 1


def test_unknown_backend_and_invalid_options_fail_closed(env: Env):
    for bad_payload in (
        _base_payload(env, execution_backend="quantum"),
        _base_payload(env, fallback_policy="pretend_ok"),
        _base_payload(env, execution_backend="cpu", execution_options={"spark_partitions": 4}),
        _base_payload(env, execution_backend="spark", execution_options={"spark_partitions": 0}),
        _base_payload(env, execution_backend="gpu", execution_options={"unknown_knob": True}),
    ):
        pre = env.client.post("/v1/run-sets/preflight", headers=env.admin_headers(), json=bad_payload)
        assert pre.status_code == 422, pre.text
        sub = env.client.post("/v1/run-sets", headers=env.admin_headers(), json=bad_payload)
        assert sub.status_code == 422, sub.text


def test_strict_unavailable_gpu_and_spark_rejected_at_preflight_and_submit(env: Env):
    for backend, expected_code in (("gpu", "GPU_HARDWARE_UNAVAILABLE"),):
        payload = _base_payload(env, execution_backend=backend, fallback_policy="strict_fail")
        pre = env.client.post("/v1/run-sets/preflight", headers=env.admin_headers(), json=payload)
        assert pre.status_code == 200, pre.text
        assert pre.json()["ok"] is False
        assert any(p["code"] == expected_code for p in pre.json()["problems"])

        sub = env.client.post("/v1/run-sets", headers=env.admin_headers(), json=payload)
        assert sub.status_code == 409, sub.text
        assert sub.json()["error"]["code"] == expected_code

    spark_strict = _base_payload(env, execution_backend="spark", fallback_policy="strict_fail")
    sub_spark = env.client.post("/v1/run-sets", headers=env.admin_headers(), json=spark_strict)
    assert sub_spark.status_code == 409
    assert sub_spark.json()["error"]["code"] in {"SPARK_RUNTIME_UNAVAILABLE", "BACKEND_NOT_IMPLEMENTED"}


def test_legacy_run_packages_remain_readable_and_reproducible(env: Env):
    # Pre-M6 submission without execution metadata in RunEvent or RunSet resolution.
    submitted = env.submit("A", scenarios=["Base"])
    run_id = submitted["runs"][0]["id"]
    with env.Session() as db:
        rs = db.get(RunSet, submitted["run_set"]["id"])
        res = dict(rs.resolution or {})
        res.pop("execution", None)
        rs.resolution = res
        db.commit()

    # Strip execution metadata from RunEvent via direct API view expectations: execute cleanly.
    env.execute(submitted)
    view = env.get(f"/runs/{run_id}").json()
    assert view["status"] == "success"
    assert view["compute"]["backend"] == "cpu"
    assert view["execution"]["actual_backend"] == "cpu"
    assert env.get(f"/runs/{run_id}/package").status_code == 200
    assert env.get(f"/runs/{run_id}/manifest").status_code == 200


def test_worker_time_strict_failure_and_retry_lineage(env: Env, monkeypatch):
    # Submit while GPU is transiently mocked as available+implemented for SPIA_LITE? No, use mocked
    # worker unavailability between submission and execution to test attempt failure + retry lineage.
    ready_caps = {
        BACKEND_CPU: BackendCapability(
            name=BACKEND_CPU, description="CPU", implemented=True, environment_available=True,
            accelerated=False, implementation_stage="M1-M5", supports_all_products=True,
        ),
        BACKEND_SPARK: BackendCapability(
            name=BACKEND_SPARK, description="Spark", implemented=True, environment_available=True,
            accelerated=True, implementation_stage="M6-02", supports_all_products=True,
        ),
        BACKEND_GPU: BackendCapability(
            name=BACKEND_GPU, description="GPU", implemented=False, environment_available=False,
            accelerated=True, implementation_stage="M6-03",
        ),
        BACKEND_SPARK_GPU: BackendCapability(
            name=BACKEND_SPARK_GPU, description="Spark+GPU", implemented=False, environment_available=False,
            accelerated=True, implementation_stage="M6-03",
        ),
    }
    ready_reg = BackendCapabilityRegistry(hardware={"cpu_count": 4}, capabilities=ready_caps)
    monkeypatch.setattr(exec_backends, "current_capability_registry", lambda: ready_reg)
    monkeypatch.setattr(run_submission_service, "current_capability_registry", lambda: ready_reg)

    with env.Session() as db:
        admin = db.get(User, env.projects["A"]["admin"])
        submitted = run_submission_service.submit_run_set(
            db,
            _base_payload(env, execution_backend="spark", fallback_policy=FALLBACK_STRICT_FAIL),
            admin,
        )
    run_id = submitted["runs"][0]["id"]

    # Now worker executes using real M6-01 registry where Spark is NOT yet implemented -> fails cleanly!
    real_reg = BackendCapabilityRegistry()
    monkeypatch.setattr(run_execution_service, "current_capability_registry", lambda: real_reg)
    status_out = None
    with env.Session() as db:
        status_out = run_execution_service.execute_run(db, run_id, worker_id="worker-1")
    assert status_out == "failed"

    failed_view = env.get(f"/runs/{run_id}").json()
    assert failed_view["status"] == "failed"
    assert failed_view["attempt_count"] == 1
    assert failed_view["accepted_attempt_number"] is None
    assert failed_view["execution"]["requested_backend"] == "spark"

    failed_manifest = env.get(f"/runs/{run_id}/manifest").json()["final_manifest"]["manifest"]
    assert failed_manifest["runtime"]["status"] == "failed"
    assert failed_manifest["runtime"]["execution"]["requested_backend"] == "spark"

    # Retry preserves identical RunPackage fingerprint & queued execution_request, creates new lineage.
    retry_resp = env.client.post(f"/v1/runs/{run_id}/retry", headers=env.admin_headers())
    assert retry_resp.status_code == 202, retry_resp.text
    retried = retry_resp.json()
    assert retried["retried_from_run_id"] == run_id
    assert retried["run_package_fingerprint"] == failed_view["run_package_fingerprint"]
    assert env.get(f"/runs/{retried['id']}/manifest").json()["final_manifest"] is not None


def test_authorization_and_project_isolation_preserved(env: Env):
    viewer_a = env.user("viewer-m6@example.com", role="reviewer", memberships={"A": "viewer"})
    outsider_b = env.user("outsider-m6@example.com", role="actuary", memberships={"B": "owner"})

    # Viewer lacks runs:execute permission (403); outsider gets 404 anti-enumeration on Project A.
    for actor, expected_status in ((viewer_a, 403), (outsider_b, 404)):
        pre = env.client.post(
            "/v1/run-sets/preflight",
            headers=actor["headers"],
            json=_base_payload(env, "A", execution_backend="cpu"),
        )
        assert pre.status_code == expected_status
        sub = env.client.post(
            "/v1/run-sets",
            headers=actor["headers"],
            json=_base_payload(env, "A", execution_backend="gpu", fallback_policy="allow_cpu_fallback"),
        )
        assert sub.status_code == expected_status

    # Outsider cannot read project A run attempts/manifest or capability endpoints without auth.
    admin_sub = env.client.post("/v1/run-sets", headers=env.admin_headers(), json=_base_payload(env, "A"))
    run_id = admin_sub.json()["runs"][0]["id"]
    assert env.get(f"/runs/{run_id}/attempts", headers=outsider_b["headers"]).status_code == 404
    assert env.get(f"/runs/{run_id}/manifest", headers=outsider_b["headers"]).status_code == 404
    assert env.client.post(f"/v1/runs/{run_id}/retry", headers=viewer_a["headers"]).status_code == 403
