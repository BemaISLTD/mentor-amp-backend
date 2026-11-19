"""Unit & security tests for M6-02 SparkExecutionBackend guards, compatibility, and planner rules."""

import os
import pytest

from app.config import settings
from app.core.execution import backends as exec_backends
from app.core.execution.backends import (
    BACKEND_SPARK,
    BackendCapability,
    BackendCapabilityRegistry,
    ExecutionPlanContext,
    ExecutionPlanner,
    ExecutionPlanningError,
    FALLBACK_ALLOW_CPU,
    FALLBACK_STRICT_FAIL,
    validate_execution_request,
)
from app.core.execution.spark_backend import (
    SparkExecutionBackend,
    SparkSessionStartupError,
    SparkWaveExecutionError,
    inspect_spark_model_compatibility,
    validate_spark_master,
)
from app.db.models.user import User
from app.services import run_execution_service, run_submission_service
from tests.integration.wp1_support import Env


@pytest.mark.parametrize("good_master", ["local", "local[*]", "local[1]", "local[2]", "local[8]", "local[16]"])
def test_validate_spark_master_accepts_default_allowlisted_local_masters(good_master):
    assert validate_spark_master(good_master) == good_master


@pytest.mark.parametrize(
    "bad_master",
    [
        "spark://evil.example:7077",
        "spark://169.254.169.254:7077",
        "local[17]",
        "local[999]",
        "local[0]",
        "mesos://master:5050",
        "yarn",
        "local[2]; rm -rf /",
        "local[2] && whoami",
        "k8s://https://10.0.0.1",
    ],
)
def test_validate_spark_master_rejects_untrusted_masters(bad_master):
    with pytest.raises(ExecutionPlanningError) as exc:
        validate_execution_request(
            BACKEND_SPARK,
            FALLBACK_ALLOW_CPU,
            {"spark_master": bad_master},
        )
    assert exc.value.code in {"SPARK_MASTER_FORBIDDEN", "INVALID_EXECUTION_OPTIONS"}
    assert exc.value.status_code == 422


def test_validate_spark_master_supports_explicit_server_cluster_allowlist(monkeypatch):
    monkeypatch.setattr(
        settings, "spark_allowed_masters", "local[1..8],spark://127.0.0.1:7077"
    )
    assert validate_spark_master("spark://127.0.0.1:7077") == "spark://127.0.0.1:7077"
    with pytest.raises(Exception):
        validate_spark_master("spark://10.9.9.9:7077")


def test_spark_quotas_enforce_partition_and_batch_bounds():
    with pytest.raises(ExecutionPlanningError) as exc1:
        validate_execution_request(
            BACKEND_SPARK,
            FALLBACK_ALLOW_CPU,
            {"spark_partitions": settings.spark_max_partitions + 1},
        )
    assert exc1.value.code == "INVALID_EXECUTION_OPTIONS"

    with pytest.raises(ExecutionPlanningError) as exc2:
        validate_execution_request(
            BACKEND_SPARK,
            FALLBACK_ALLOW_CPU,
            {"batch_size": settings.spark_max_batch_size + 1},
        )
    assert exc2.value.code == "INVALID_EXECUTION_OPTIONS"


def test_model_compatibility_inspects_real_formulas_not_only_product_code():
    ok, issues = inspect_spark_model_compatibility(
        formulas=[
            {
                "output_variable": "x",
                "function_ref": "dsl",
                "expression_text": "100 + 25",
                "dependencies": [],
            }
        ],
        variables=[{"name": "x", "source": {"type": "formula"}}],
    )
    assert ok is True
    assert issues == []

    bad_func_ok, bad_func_issues = inspect_spark_model_compatibility(
        formulas=[
            {
                "output_variable": "x",
                "function_ref": "unregistered_custom_external_call",
                "dependencies": [],
            }
        ]
    )
    assert bad_func_ok is False
    assert any("UNREGISTERED_FUNCTION_REF" in item for item in bad_func_issues)

    cycle_ok, cycle_issues = inspect_spark_model_compatibility(
        formulas=[
            {"output_variable": "a", "function_ref": "dsl", "expression_text": "b + 1", "dependencies": ["b"]},
            {"output_variable": "b", "function_ref": "dsl", "expression_text": "a + 1", "dependencies": ["a"]},
        ]
    )
    assert cycle_ok is False
    assert any("CIRCULAR_DEPENDENCY" in item for item in cycle_issues)

    cross_ok, cross_issues = inspect_spark_model_compatibility(
        formulas=[{"output_variable": "a", "function_ref": "dsl", "expression_text": "1", "dependencies": []}],
        variables=[{"name": "portfolio_cap", "source": {"type": "input", "scope": "cross_policy"}}],
    )
    assert cross_ok is False
    assert any("CROSS_POLICY_VARIABLE" in item for item in cross_issues)

    # Planner refuses Spark even under a valid product code when formula graph is incompatible.
    spark_ready_reg = BackendCapabilityRegistry(
        hardware={"python_version": "3.14.2", "cpu_count": 4, "pyspark_installed": True, "java_detected": True}
    )
    planner = ExecutionPlanner(spark_ready_reg)
    ctx = ExecutionPlanContext(
        product_code="MYGA_LITE",
        policy_count=100,
        horizon_months=120,
        formulas=[{"output_variable": "bad", "function_ref": "missing_fn", "dependencies": []}],
    )
    plan = planner.plan(validate_execution_request(BACKEND_SPARK, FALLBACK_STRICT_FAIL), ctx)
    assert plan.runnable is False
    assert plan.error_code == "MODEL_NOT_SPARK_COMPATIBLE"


def test_no_silent_cpu_fallback_after_spark_execution_begins(monkeypatch):
    """Pre-wave startup error may fallback if allow_cpu_fallback, mid-wave error NEVER falls back."""
    env = Env()
    try:
        ids = env.seed("A", horizon=6)
        spark_ready = BackendCapabilityRegistry(
            hardware={"python_version": "3.14.2", "cpu_count": 4, "pyspark_installed": True, "java_detected": True}
        )
        monkeypatch.setattr(exec_backends, "current_capability_registry", lambda: spark_ready)
        monkeypatch.setattr(run_submission_service, "current_capability_registry", lambda: spark_ready)
        monkeypatch.setattr(run_execution_service, "current_capability_registry", lambda: spark_ready)

        class CrashInWaveSparkBackend(SparkExecutionBackend):
            def execute_waves(self, data, policies, *, on_session_ready=None, **_kwargs):
                raise SparkWaveExecutionError("SPARK_EXECUTION_ERROR", "Simulated worker executor death mid-wave")

        monkeypatch.setattr(
            spark_ready,
            "get_backend",
            lambda name, **kwargs: (
                CrashInWaveSparkBackend(execution_options=kwargs.get("execution_options"))
                if name == BACKEND_SPARK
                else BackendCapabilityRegistry.get_backend(spark_ready, name, **kwargs)
            ),
        )

        with env.Session() as db:
            admin = db.get(User, ids["admin"])
            submitted = run_submission_service.submit_run_set(
                db,
                {
                    "project_id": ids["project"],
                    "name": "Spark crash non-fallback check",
                    "projection_set_ids": [ids["projection_set"]],
                    "scenario_ids": [ids["scenarios"]["Base"]],
                    "execution_backend": BACKEND_SPARK,
                    "fallback_policy": FALLBACK_ALLOW_CPU,
                },
                admin,
            )
            run_id = submitted["runs"][0]["id"]
            status = run_execution_service.execute_run(db, run_id, worker_id="worker-crash-test")
            assert status == "failed"

        view = env.get(f"/runs/{run_id}").json()
        assert view["status"] == "failed"
        assert view["accepted_attempt_number"] is None
        assert view["execution"]["actual_backend"] == BACKEND_SPARK
        assert view["execution"]["fallback_used"] is False
        assert view["results_available"] is False

        # Contrast with pre-wave SparkSessionStartupError under allow_cpu_fallback -> explicit CPU fallback.
        class StartupFailSparkBackend(SparkExecutionBackend):
            def execute_waves(self, data, policies, *, on_session_ready=None, **_kwargs):
                raise SparkSessionStartupError("JVM port busy before calculation started")

        monkeypatch.setattr(
            spark_ready,
            "get_backend",
            lambda name, **kwargs: (
                StartupFailSparkBackend(execution_options=kwargs.get("execution_options"))
                if name == BACKEND_SPARK
                else BackendCapabilityRegistry.get_backend(spark_ready, name, **kwargs)
            ),
        )
        with env.Session() as db:
            admin = db.get(User, ids["admin"])
            submitted2 = run_submission_service.submit_run_set(
                db,
                {
                    "project_id": ids["project"],
                    "name": "Spark pre-wave fallback check",
                    "projection_set_ids": [ids["projection_set"]],
                    "scenario_ids": [ids["scenarios"]["Base"]],
                    "execution_backend": BACKEND_SPARK,
                    "fallback_policy": FALLBACK_ALLOW_CPU,
                },
                admin,
            )
            run2_id = submitted2["runs"][0]["id"]
            status2 = run_execution_service.execute_run(db, run2_id, worker_id="worker-fb-test")
            assert status2 == "success"

        view2 = env.get(f"/runs/{run2_id}").json()
        assert view2["execution"]["actual_backend"] == "cpu"
        assert view2["execution"]["fallback_used"] is True
        assert view2["execution"]["fallback_reason"] == "SPARK_SESSION_INIT_FAILED"
    finally:
        env.close()
