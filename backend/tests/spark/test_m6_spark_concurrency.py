"""Concurrent same-master Spark execution and isolated job-group cancellation tests (Section 7A hardening)."""

from __future__ import annotations

import math
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

pyspark = pytest.importorskip("pyspark")

from app.core.execution import spark_backend  # noqa: E402
from app.core.execution.backends import CpuReferenceBackend  # noqa: E402
from app.core.execution.spark_backend import (  # noqa: E402
    SparkCancelledError,
    SparkExecutionBackend,
    _resolve_java_home,
    cancel_spark_run,
)
from app.db.models.run import Run  # noqa: E402
from app.db.models.run_package import RunPackage  # noqa: E402
from app.services import run_execution_service  # noqa: E402
from tests.integration.wp1_support import Env  # noqa: E402

if _resolve_java_home() is None:
    pytest.skip("Java 17+ is required for real PySpark concurrency tests", allow_module_level=True)


def _load_sample_run_data():
    env = Env()
    try:
        env.seed("CONC", horizon=12)
        submitted = env.submit("CONC", scenarios=["Base"])
        run_id = submitted["runs"][0]["id"]
        with env.Session() as db:
            run = db.get(Run, run_id)
            pkg = db.query(RunPackage).filter(RunPackage.run_id == run_id).one()
            _configuration, functions, data = run_execution_service._load(db, run, pkg)
        return data, functions
    finally:
        env.close()


def test_concurrent_same_master_spark_runs_share_context_without_stopping_peer():
    data, functions = _load_sample_run_data()
    cpu_outcomes = list(CpuReferenceBackend().execute_policies(data, data.policies, functions))
    cpu_res0 = {p.policy_id: res.reserve_0 for p, res in cpu_outcomes}

    barrier = threading.Barrier(2, timeout=60)
    observed_active_counts: list[int] = []

    def _run_worker(run_id: str) -> dict[str, float | None]:
        backend = SparkExecutionBackend(
            execution_options={"spark_master": "local[2]", "spark_partitions": 2, "batch_size": 10}
        )
        outcomes: dict[str, float | None] = {}
        entered_barrier = False

        def _on_session(info: dict) -> None:
            nonlocal entered_barrier
            observed_active_counts.append(int(info.get("concurrent_session_runs", 0)))
            if not entered_barrier:
                entered_barrier = True
                barrier.wait()

        for wave in backend.execute_waves(
            data,
            data.policies,
            on_session_ready=_on_session,
            run_id=run_id,
            attempt_number=1,
            watch_poll_seconds=0.0,
        ):
            for pol, res in wave.outcomes:
                assert res.error is None
                outcomes[pol.policy_id] = res.reserve_0
        return outcomes

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_a = pool.submit(_run_worker, "conc-run-a")
        fut_b = pool.submit(_run_worker, "conc-run-b")
        out_a = fut_a.result(timeout=180)
        out_b = fut_b.result(timeout=180)

    assert set(out_a) == set(cpu_res0)
    assert set(out_b) == set(cpu_res0)
    for pid, expected_val in cpu_res0.items():
        assert math.isclose(out_a[pid], expected_val, abs_tol=1e-9)
        assert math.isclose(out_b[pid], expected_val, abs_tol=1e-9)
    assert max(observed_active_counts) >= 2
    assert spark_backend._SPARK_ACTIVE_RUNS == 0


def test_cancelling_one_concurrent_spark_run_leaves_peer_run_healthy():
    data, functions = _load_sample_run_data()
    cpu_outcomes = list(CpuReferenceBackend().execute_policies(data, data.policies, functions))
    cpu_res0 = {p.policy_id: res.reserve_0 for p, res in cpu_outcomes}

    both_ready = threading.Barrier(2, timeout=60)
    run_a_cancelled = threading.Event()

    def _run_a() -> None:
        backend = SparkExecutionBackend(
            execution_options={"spark_master": "local[2]", "spark_partitions": 2, "batch_size": 10}
        )

        def _hook(wave_idx: int, _items: list) -> None:
            if wave_idx == 0:
                both_ready.wait()
                assert cancel_spark_run("conc-cancel-a") is True
                run_a_cancelled.set()

        backend._policy_batch_hook = _hook
        list(
            backend.execute_waves(
                data,
                data.policies,
                run_id="conc-cancel-a",
                attempt_number=1,
                should_cancel=run_a_cancelled.is_set,
                watch_poll_seconds=0.0,
            )
        )

    def _run_b() -> dict[str, float | None]:
        backend = SparkExecutionBackend(
            execution_options={"spark_master": "local[2]", "spark_partitions": 2, "batch_size": 10}
        )

        def _hook(wave_idx: int, _items: list) -> None:
            if wave_idx == 0:
                both_ready.wait()
                run_a_cancelled.wait(timeout=30)

        backend._policy_batch_hook = _hook
        outcomes: dict[str, float | None] = {}
        for wave in backend.execute_waves(
            data,
            data.policies,
            run_id="conc-healthy-b",
            attempt_number=1,
            watch_poll_seconds=0.0,
        ):
            for pol, res in wave.outcomes:
                outcomes[pol.policy_id] = res.reserve_0
        return outcomes

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_a = pool.submit(_run_a)
        fut_b = pool.submit(_run_b)
        with pytest.raises(SparkCancelledError):
            fut_a.result(timeout=180)
        out_b = fut_b.result(timeout=180)

    assert len(out_b) == len(cpu_res0)
    for pid, expected_val in cpu_res0.items():
        assert math.isclose(out_b[pid], expected_val, abs_tol=1e-9)
    assert spark_backend._SPARK_ACTIVE_RUNS == 0
