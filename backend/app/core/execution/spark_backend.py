"""Real distributed PySpark execution backend and compatibility/security guards (Phase 6 M6-02).

Design invariants:
- Workers execute the exact shared pure engine (``run_policy`` + ``register_all_products``).
- Zero SQLAlchemy sessions, DB connections, or artifact stores are serialized to workers.
- ``spark_master`` and resource limits are enforced against server-controlled allowlists/quotas.
- Execution runs in bounded policy waves so driver memory stays bounded and cancellation,
  progress, heartbeats, trace capture, and m2-v1 Parquet flushing behave identically to CPU.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import perf_counter
from typing import Any

from app.config import settings
from app.core.dependency_engine.graph import topological_sort
from app.core.formula_engine.dsl import FormulaDslError, extract_dependencies
from app.core.formula_engine.formulas import FORMULA_REGISTRY
from app.core.projection_engine.engine import EngineError, PolicyResult
from app.core.projection_engine.run_data import PolicyRecord, RunData

BACKEND_ROOT = str(Path(__file__).resolve().parents[3])
WINDOWS_WORKER_BOOTSTRAP_DIR = str(Path(__file__).resolve().parent / "_spark_win_bootstrap")
_SHELL_UNSAFE = re.compile(r"[;&|`$\r\n\t\s<>]")
_LOCAL_BRACKET_RE = re.compile(r"^local\[(\*|\d+)\]$")
_LOCAL_RANGE_RULE_RE = re.compile(r"^local\[(\d+)\.\.(\d+)\]$")

_ACTIVE_SPARK_JOBS_LOCK = threading.Lock()
_ACTIVE_SPARK_JOBS: dict[str, tuple[Any, str]] = {}


def cancel_spark_run(run_id: str) -> bool:
    """Cancel an actively running Spark job group for ``run_id`` if registered."""
    with _ACTIVE_SPARK_JOBS_LOCK:
        entry = _ACTIVE_SPARK_JOBS.get(run_id)
    if entry is None:
        return False
    sc, job_group = entry
    try:
        sc.cancelJobGroup(job_group)
        return True
    except Exception:  # noqa: BLE001 - best-effort JVM cancellation on teardown
        return False


class SparkCancelledError(RuntimeError):
    """Raised when an active Spark wave/job group is cancelled."""


class SparkSecurityError(ValueError):
    """Raised when a requested spark_master or resource option violates server policy."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class SparkSessionStartupError(RuntimeError):
    """Raised if Spark JVM session creation fails BEFORE any policy calculation starts."""


class SparkWaveExecutionError(RuntimeError):
    """Raised if a Spark partition/wave fails AFTER execution begins (never falls back silently)."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class SparkWorkerTemplate:
    """Immutable pure-Python template broadcast once per Spark execution (no DB/secrets)."""

    variables: dict[str, Any]
    formulas: list[Any]
    tables: dict[str, Any]
    scenario: Any
    valuation_date: Any
    horizon_months: int
    output_variables: list[str]
    traced_policy_ids: frozenset[str]
    parameters: dict[str, Any]
    decrement_methodology: Any


@dataclass(frozen=True)
class SerializedPolicyOutcome:
    """Serializable per-policy outcome emitted from Spark partition workers."""

    policy_index: int
    policy_id: str
    months_computed: int
    outputs: list[tuple[int, str, float]]
    trace_rows: list[dict[str, Any]]
    sums: dict[str, float]
    reserve_0: float | None
    warnings: list[str]
    error_dict: dict[str, Any] | None
    partition_id: int
    worker_os_pid: int
    worker_hostname: str
    task_attempt_id: int
    calc_seconds: float

    def to_policy_result(self) -> PolicyResult:
        err = None
        if self.error_dict is not None:
            err = EngineError(
                error_type=str(self.error_dict.get("error_type") or "CALCULATION_ERROR"),
                message=str(self.error_dict.get("message") or "Policy calculation failed."),
                variable=self.error_dict.get("variable"),
                month=self.error_dict.get("month"),
            )
        return PolicyResult(
            policy_id=self.policy_id,
            outputs=self.outputs,
            trace_rows=self.trace_rows,
            sums=self.sums,
            reserve_0=self.reserve_0,
            warnings=self.warnings,
            error=err,
            months_computed=self.months_computed,
        )


@dataclass
class SparkExecutionWave:
    """One deterministic bounded wave of sorted policy results + Spark telemetry."""

    wave_index: int
    outcomes: list[tuple[PolicyRecord, PolicyResult]]
    partition_count: int
    partitions_seen: list[int]
    worker_os_pids: list[int]
    worker_hostnames: list[str]
    broadcast_seconds: float
    partition_exec_seconds: float
    collect_seconds: float
    shuffle_agg_seconds: float
    shuffle_row_count: int
    executor_infos: list[dict[str, Any]] = field(default_factory=list)


def validate_spark_master(
    raw_master: str,
    *,
    allowed_rules_csv: str | None = None,
) -> str:
    """Validate ``spark_master`` strictly against server allowlists and character safety."""
    if not isinstance(raw_master, str) or not raw_master.strip():
        raise SparkSecurityError(
            "INVALID_EXECUTION_OPTIONS",
            "Execution option 'spark_master' must be a non-empty string.",
            {"spark_master": raw_master},
        )
    master = raw_master.strip()
    if _SHELL_UNSAFE.search(master):
        raise SparkSecurityError(
            "SPARK_MASTER_FORBIDDEN",
            f"Spark master '{master}' contains forbidden characters.",
            {"spark_master": master},
        )

    rules_text = (
        settings.spark_allowed_masters if allowed_rules_csv is None else allowed_rules_csv
    )
    rules = [item.strip() for item in rules_text.split(",") if item.strip()]

    for rule in rules:
        if master == rule:
            return master
        range_match = _LOCAL_RANGE_RULE_RE.match(rule)
        local_match = _LOCAL_BRACKET_RE.match(master)
        if range_match and local_match and local_match.group(1) != "*":
            lo, hi = int(range_match.group(1)), int(range_match.group(2))
            requested_threads = int(local_match.group(1))
            if lo <= requested_threads <= hi:
                return master
        if rule.endswith("*") and not rule.startswith("local["):
            prefix = rule[:-1]
            if master.startswith(prefix):
                return master

    raise SparkSecurityError(
        "SPARK_MASTER_FORBIDDEN",
        f"Spark master '{master}' is not permitted by server allowlist ({rules}).",
        {"spark_master": master, "allowed_masters": rules},
    )


def inspect_spark_model_compatibility(
    formulas: Sequence[Any] | None = None,
    variables: Sequence[Any] | Mapping[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    """Verify policy-level parallelism from actual model formula dependencies and callables."""
    issues: list[str] = []
    if formulas is None:
        return True, []
    formula_list = list(formulas)
    if not formula_list:
        return False, ["NO_FORMULAS"]

    # Inspect variable scope metadata for cross-policy dependencies if variable specs exist.
    var_items: Iterable[Any]
    if isinstance(variables, Mapping):
        var_items = variables.values()
    elif variables is not None:
        var_items = variables
    else:
        var_items = ()
    for var in var_items:
        source = var.get("source") if isinstance(var, Mapping) else getattr(var, "source", None)
        if isinstance(source, Mapping) and source.get("scope") in {"portfolio", "cross_policy"}:
            name = var.get("name") if isinstance(var, Mapping) else getattr(var, "name", "unknown")
            issues.append(f"CROSS_POLICY_VARIABLE:{name}")

    # Inspect every formula's callable/DSL integrity and DAG acyclicity.
    nodes: list[Any] = []
    seen_outputs: set[str] = set()
    for item in formula_list:
        if isinstance(item, Mapping):
            out_var = str(item.get("output_variable") or "")
            func_ref = str(item.get("function_ref") or "")
            expr = item.get("expression_text")
            deps = tuple(item.get("dependencies") or ())
        else:
            out_var = str(getattr(item, "output_variable", "") or "")
            func_ref = str(getattr(item, "function_ref", "") or "")
            expr = getattr(item, "expression_text", None)
            deps = tuple(getattr(item, "dependencies", ()) or ())

        if out_var in seen_outputs:
            issues.append(f"DUPLICATE_OUTPUT_VARIABLE:{out_var}")
        seen_outputs.add(out_var)

        if func_ref == "dsl" or func_ref.startswith("dsl:"):
            try:
                extract_dependencies(expr or "")
            except FormulaDslError as err:
                issues.append(f"INVALID_DSL_FORMULA:{out_var}:{err.message}")
        elif func_ref not in FORMULA_REGISTRY:
            issues.append(f"UNREGISTERED_FUNCTION_REF:{func_ref}")

        @dataclass
        class _Node:
            output_variable: str
            dependencies: tuple[str, ...]

        nodes.append(_Node(output_variable=out_var, dependencies=deps))

    try:
        topological_sort(nodes)
    except ValueError as exc:
        issues.append(f"CIRCULAR_DEPENDENCY:{exc}")

    return len(issues) == 0, issues


def _resolve_java_home() -> str | None:
    if os.environ.get("JAVA_HOME") and os.path.isdir(os.environ["JAVA_HOME"]):
        return os.environ["JAVA_HOME"]
    java_exe = shutil.which("java")
    if java_exe:
        return str(Path(java_exe).resolve().parents[1])
    local_jdk = Path(os.environ.get("LOCALAPPDATA", "")) / "MentorAmp" / "jdk-17"
    if (local_jdk / "bin" / "java.exe").exists():
        return str(local_jdk)
    return None


def _run_partition_worker(
    partition_index: int,
    iterator: Iterable[tuple[int, PolicyRecord]],
    template_or_broadcast: Any,
    backend_root: str,
) -> Iterable[dict[str, Any]]:
    """Pure Spark worker task executing Mentor AMP ``run_policy()`` per policy in a partition."""
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)

    from pyspark import TaskContext  # type: ignore[import-not-found]

    from app.core.formula_engine.formulas import FORMULA_FUNCTIONS
    from app.core.projection_engine.engine import run_policy
    from app.products.registry import register_all_products

    register_all_products()
    tmpl: SparkWorkerTemplate = (
        template_or_broadcast
        if isinstance(template_or_broadcast, SparkWorkerTemplate)
        else template_or_broadcast.value
    )

    worker_data = RunData(
        variables=tmpl.variables,
        formulas=tmpl.formulas,
        tables=tmpl.tables,
        policies=[],
        scenario=tmpl.scenario,
        valuation_date=tmpl.valuation_date,
        horizon_months=tmpl.horizon_months,
        output_variables=tmpl.output_variables,
        traced_policy_ids=tmpl.traced_policy_ids,
        parameters=tmpl.parameters,
        decrement_methodology=tmpl.decrement_methodology,
    )

    ctx = TaskContext.get()
    ctx_pid = int(ctx.partitionId()) if ctx is not None else int(partition_index)
    task_attempt_id = int(ctx.taskAttemptId()) if ctx is not None else 0
    worker_pid = int(os.getpid())
    worker_host = socket.gethostname()

    for policy_index, policy in iterator:
        t0 = perf_counter()
        result = run_policy(worker_data, policy, FORMULA_FUNCTIONS)
        calc_s = perf_counter() - t0
        yield {
            "policy_index": policy_index,
            "policy_id": policy.policy_id,
            "months_computed": result.months_computed,
            "outputs": list(result.outputs),
            "trace_rows": list(result.trace_rows),
            "sums": dict(result.sums),
            "reserve_0": result.reserve_0,
            "warnings": list(result.warnings),
            "error_dict": result.error.as_dict() if result.error is not None else None,
            "partition_id": ctx_pid,
            "worker_os_pid": worker_pid,
            "worker_hostname": worker_host,
            "task_attempt_id": task_attempt_id,
            "calc_seconds": calc_s,
        }


def _executor_snapshots(sc: Any) -> list[dict[str, Any]]:
    """Return executor telemetry distinguishing the Spark driver from worker executors."""
    snapshots: list[dict[str, Any]] = []
    try:
        summaries = sc._jsc.sc().statusStore().executorList(True)
        count = int(summaries.size())
        for idx in range(count):
            summary = summaries.apply(idx)
            executor_id = str(summary.id())
            host_port = str(summary.hostPort())
            host, _, port_str = host_port.partition(":")
            try:
                port = int(port_str) if port_str else 0
            except ValueError:
                port = 0
            snapshots.append({
                "executor_id": executor_id,
                "is_driver": executor_id == "driver",
                "host": host,
                "port": port,
                "host_port": host_port,
                "is_active": bool(summary.isActive()),
                "total_cores": int(summary.totalCores()),
                "max_tasks": int(summary.maxTasks()),
                "completed_tasks": int(summary.completedTasks()),
                "total_tasks": int(summary.totalTasks()),
                "max_memory": int(summary.maxMemory()),
            })
        if snapshots:
            return snapshots
    except Exception:  # noqa: BLE001 - fall back to statusTracker below
        pass

    try:
        infos = list(sc._jsc.sc().statusTracker().getExecutorInfos())
        for info in infos:
            snapshots.append({
                "executor_id": None,
                "is_driver": None,
                "host": str(info.host()),
                "port": int(info.port()),
                "cache_size": int(info.cacheSize()),
                "num_running_tasks": int(info.numRunningTasks()),
            })
        return snapshots
    except Exception:  # noqa: BLE001 - optional JVM telemetry
        return []


class SparkExecutionBackend:
    """Real distributed PySpark Classic backend executing ``run_policy()`` across partitions."""

    name = "spark"
    accelerated = True

    def __init__(
        self,
        *,
        execution_options: Mapping[str, Any] | None = None,
        policy_batch_hook: Callable[..., Any] | None = None,
    ):
        self.execution_options = dict(execution_options or {})
        # Optional test seam cleanly supporting fault/cancellation simulation without changing prod code.
        self._policy_batch_hook = policy_batch_hook

    def capability(self):
        from app.core.execution.backends import BackendCapability, POLICY_PARALLEL_PRODUCTS

        java_home = _resolve_java_home()
        import importlib.util

        pyspark_installed = importlib.util.find_spec("pyspark") is not None
        pyarrow_installed = importlib.util.find_spec("pyarrow") is not None
        env_ok = bool(settings.spark_enabled and pyspark_installed and java_home is not None)
        return BackendCapability(
            name="spark",
            description="Distributed Apache Spark Classic executor (shared run_policy engine)",
            implemented=True,
            environment_available=env_ok,
            accelerated=True,
            implementation_stage="M6-02",
            supports_all_products=False,
            supported_products=tuple(sorted(POLICY_PARALLEL_PRODUCTS)),
            unavailable_reason=None if env_ok else "SPARK_RUNTIME_UNAVAILABLE",
            runtime_details={
                "spark_enabled": settings.spark_enabled,
                "pyspark_installed": pyspark_installed,
                "java_detected": java_home is not None,
                "pyarrow_installed": pyarrow_installed,
                "default_master": settings.spark_default_master,
                "max_partitions": settings.spark_max_partitions,
            },
        )

    def execute_policies(
        self,
        data: RunData,
        policies: Iterable[PolicyRecord],
        functions: Mapping[str, Callable[..., Any]],
    ) -> Iterable[tuple[PolicyRecord, PolicyResult]]:
        """Execute a full policy iterable via bounded Spark waves (never one Spark job per policy)."""
        del functions
        policy_list = list(policies)
        for wave in self.execute_waves(data, policy_list):
            yield from wave.outcomes

    def execute_waves(
        self,
        data: RunData,
        policies: Sequence[PolicyRecord],
        *,
        on_session_ready: Callable[[dict[str, Any]], None] | None = None,
        run_id: str | None = None,
        attempt_number: int | None = None,
        should_cancel: Callable[[], bool] | None = None,
        watch_poll_seconds: float = 1.0,
    ) -> Iterable[SparkExecutionWave]:
        """Yield bounded Spark waves in deterministic policy order.

        Raises ``SparkSessionStartupError`` before calculating if Spark JVM initialization fails,
        ``SparkCancelledError`` if cancelled before or during execution, or
        ``SparkWaveExecutionError`` if a running partition/wave crashes or times out.
        """
        raw_master = self.execution_options.get("spark_master") or settings.spark_default_master
        master = validate_spark_master(str(raw_master))
        requested_partitions = int(
            self.execution_options.get("spark_partitions") or settings.spark_default_partitions
        )
        if requested_partitions < 1 or requested_partitions > settings.spark_max_partitions:
            raise SparkSecurityError(
                "INVALID_EXECUTION_OPTIONS",
                f"spark_partitions must be between 1 and {settings.spark_max_partitions}.",
                {"spark_partitions": requested_partitions},
            )
        batch_size = int(self.execution_options.get("batch_size") or settings.spark_default_batch_size)
        if batch_size < 1 or batch_size > settings.spark_max_batch_size:
            raise SparkSecurityError(
                "INVALID_EXECUTION_OPTIONS",
                f"batch_size must be between 1 and {settings.spark_max_batch_size}.",
                {"batch_size": batch_size},
            )
        prefer_arrow = bool(self.execution_options.get("prefer_arrow", True))

        java_home = _resolve_java_home()
        if java_home:
            os.environ.setdefault("JAVA_HOME", java_home)
            java_bin = str(Path(java_home) / "bin")
            if java_bin not in os.environ.get("PATH", ""):
                os.environ["PATH"] = f"{java_bin}{os.pathsep}{os.environ.get('PATH', '')}"

        os.environ["PYSPARK_PYTHON"] = sys.executable
        os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

        t_boot_start = perf_counter()
        job_group = (
            f"mentoramp-run-{run_id}-attempt-{attempt_number or 1}"
            if run_id
            else f"mentoramp-adhoc-{uuid.uuid4().hex[:12]}"
        )
        try:
            import pyspark  # type: ignore[import-not-found]
            from pyspark import SparkContext  # type: ignore[import-not-found]
            from pyspark.sql import SparkSession  # type: ignore[import-not-found]
            from pyspark.sql import functions as F  # type: ignore[import-not-found]

            os.environ.setdefault("SPARK_HOME", str(Path(pyspark.__file__).parent))
            os.environ.setdefault("SPARK_SCALA_VERSION", "2.13")

            # Stop any active session bound to a different master (e.g. local[2] -> spark://...)
            existing_session = getattr(SparkSession, "_instantiatedSession", None)
            existing_ctx = getattr(SparkContext, "_active_spark_context", None)
            if existing_session is not None:
                try:
                    existing_master = str(existing_session.sparkContext.master)
                except Exception:  # noqa: BLE001
                    existing_master = ""
                if existing_master != master:
                    try:
                        existing_session.stop()
                    except Exception:  # noqa: BLE001
                        pass
            elif existing_ctx is not None:
                try:
                    existing_master = str(existing_ctx.master)
                except Exception:  # noqa: BLE001
                    existing_master = ""
                if existing_master != master:
                    try:
                        existing_ctx.stop()
                    except Exception:  # noqa: BLE001
                        pass

            worker_pythonpath = BACKEND_ROOT
            if sys.platform == "win32":
                worker_pythonpath = f"{WINDOWS_WORKER_BOOTSTRAP_DIR}{os.pathsep}{BACKEND_ROOT}"

            builder = (
                SparkSession.builder.master(master)
                .appName("mentoramp-m6-projection")
                .config("spark.ui.enabled", "false")
                .config("spark.driver.memory", settings.spark_driver_memory)
                .config("spark.executor.memory", settings.spark_executor_memory)
                .config("spark.network.timeout", f"{settings.spark_network_timeout_seconds}s")
                .config("spark.python.authenticate.socketTimeout", "60s")
                .config("spark.executorEnv.PYTHONPATH", worker_pythonpath)
                .config("spark.pyspark.python", sys.executable)
                .config("spark.pyspark.driver.python", sys.executable)
                .config("spark.sql.execution.arrow.pyspark.enabled", "true" if prefer_arrow else "false")
                .config("spark.python.worker.reuse", "true")
                .config("spark.python.worker.faulthandler.enabled", "true")
            )
            if master.startswith("local"):
                builder = (
                    builder.config("spark.driver.host", "127.0.0.1")
                    .config("spark.driver.bindAddress", "127.0.0.1")
                )
            else:
                builder = (
                    builder.config("spark.driver.host", "127.0.0.1")
                    .config("spark.driver.bindAddress", "127.0.0.1")
                    .config("spark.executor.cores", "1")
                )

            spark = builder.getOrCreate()
            sc = spark.sparkContext
            if str(sc.master) != master:
                raise RuntimeError(
                    f"SparkSession master mismatch: expected '{master}', got '{sc.master}'."
                )

            # In Standalone/cluster mode, give worker JVMs a moment to register executors.
            if not master.startswith("local"):
                for _ in range(20):
                    snaps = _executor_snapshots(sc)
                    worker_snaps = [s for s in snaps if s.get("is_driver") is False]
                    if len(worker_snaps) >= 2:
                        break
                    time.sleep(0.25)

            sc.setJobGroup(job_group, f"MentorAMP run {run_id or 'adhoc'}", interruptOnCancel=True)
            if run_id:
                with _ACTIVE_SPARK_JOBS_LOCK:
                    _ACTIVE_SPARK_JOBS[run_id] = (sc, job_group)

            all_executors = _executor_snapshots(sc)
            worker_executors = [s for s in all_executors if s.get("is_driver") is False]
            session_info = {
                "spark_version": str(spark.version),
                "java_version": str(sc._jvm.System.getProperty("java.version")),
                "python_version": platform.python_version(),
                "spark_master": str(sc.master),
                "application_id": str(sc.applicationId),
                "job_group": job_group,
                "default_parallelism": int(sc.defaultParallelism),
                "spark_session_boot_seconds": round(perf_counter() - t_boot_start, 4),
                "executor_snapshots": all_executors,
                "worker_executor_infos": worker_executors,
                "worker_executor_ids": [str(s.get("executor_id")) for s in worker_executors],
            }
        except Exception as exc:  # noqa: BLE001 - session init boundary
            raise SparkSessionStartupError(f"Unable to initialize SparkSession on '{master}': {exc}") from exc

        broadcast_template = None
        try:
            if on_session_ready is not None:
                on_session_ready(session_info)

            # Strip any cached compiled DSL expressions so broadcast payload is purely immutable data.
            clean_formulas = [replace(f, compiled_expression=None) for f in data.formulas]
            t_bcast_start = perf_counter()
            template = SparkWorkerTemplate(
                variables=data.variables,
                formulas=clean_formulas,
                tables=data.tables,
                scenario=data.scenario,
                valuation_date=data.valuation_date,
                horizon_months=data.horizon_months,
                output_variables=list(data.output_variables),
                traced_policy_ids=frozenset(data.traced_policy_ids),
                parameters=dict(data.parameters),
                decrement_methodology=data.decrement_methodology,
            )
            # Apache Spark 4.1.1 PythonBroadcast.readObject moves a temp file on Windows while
            # its FileOutputStream is still open; use direct closure transport on win32 clusters,
            # while keeping sc.broadcast() on all local modes and POSIX distributed clusters.
            use_python_broadcast = not (sys.platform == "win32" and not master.startswith("local"))
            if use_python_broadcast:
                broadcast_template = sc.broadcast(template)
                worker_template_arg = broadcast_template
            else:
                broadcast_template = None
                worker_template_arg = template
            broadcast_seconds = perf_counter() - t_bcast_start

            total = len(policies)
            for wave_idx, start in enumerate(range(0, total, batch_size)):
                if should_cancel is not None and should_cancel():
                    sc.cancelJobGroup(job_group)
                    raise SparkCancelledError(
                        f"Run cancelled before Spark wave {wave_idx} (job_group={job_group})."
                    )

                end = min(start + batch_size, total)
                indexed_wave = [(idx, policies[idx]) for idx in range(start, end)]
                num_slices = max(1, min(requested_partitions, len(indexed_wave)))

                if self._policy_batch_hook is not None:
                    self._policy_batch_hook(wave_idx, indexed_wave)

                if should_cancel is not None and should_cancel():
                    sc.cancelJobGroup(job_group)
                    raise SparkCancelledError(
                        f"Run cancelled during Spark wave {wave_idx} setup (job_group={job_group})."
                    )

                t_wave_start = perf_counter()
                stop_watch = threading.Event()
                watcher_thread: threading.Thread | None = None
                if should_cancel is not None and watch_poll_seconds > 0:
                    def _watch_cancel() -> None:
                        while not stop_watch.wait(watch_poll_seconds):
                            try:
                                if should_cancel():
                                    sc.cancelJobGroup(job_group)
                                    return
                            except Exception:  # noqa: BLE001
                                return

                    watcher_thread = threading.Thread(
                        target=_watch_cancel,
                        name=f"spark-cancel-{job_group}",
                        daemon=True,
                    )
                    watcher_thread.start()

                try:
                    backend_root = BACKEND_ROOT
                    tmpl_ref = worker_template_arg
                    rdd = sc.parallelize(indexed_wave, numSlices=num_slices)
                    raw_dicts: list[dict[str, Any]] = rdd.mapPartitionsWithIndex(
                        lambda p_idx, it, _t=tmpl_ref, _root=backend_root: _run_partition_worker(
                            p_idx, it, _t, _root
                        )
                    ).collect()
                    raw_outcomes = [SerializedPolicyOutcome(**row) for row in raw_dicts]
                except Exception as exc:
                    exc_text = str(exc).lower()
                    if (
                        "cancelled" in exc_text
                        or "canceljobgroup" in exc_text
                        or (should_cancel is not None and should_cancel())
                    ):
                        raise SparkCancelledError(
                            f"Spark wave {wave_idx} cancelled (job_group={job_group}): {exc}"
                        ) from exc
                    raise SparkWaveExecutionError(
                        "SPARK_EXECUTION_ERROR",
                        f"Spark wave {wave_idx} (policies {start}..{end - 1}) failed: {exc}",
                        {"wave_index": wave_idx, "policy_start": start, "policy_end": end - 1},
                    ) from exc
                finally:
                    stop_watch.set()
                    if watcher_thread is not None:
                        watcher_thread.join(timeout=1.0)
                partition_plus_collect_s = perf_counter() - t_wave_start

                raw_outcomes.sort(key=lambda item: item.policy_index)
                outcomes = [(policies[item.policy_index], item.to_policy_result()) for item in raw_outcomes]

                # Compact Arrow/Catalyst summary aggregation once per run (zero raw-row collect).
                t_agg_start = perf_counter()
                shuffle_rows = 0
                if prefer_arrow and raw_outcomes and wave_idx == 0:
                    try:
                        import pandas as pd  # type: ignore[import-not-found]

                        compact_df = pd.DataFrame([
                            {
                                "partition_id": item.partition_id,
                                "policy_id": item.policy_id,
                                "reserve_0": float(item.reserve_0 or 0.0),
                            }
                            for item in raw_outcomes
                        ])
                        agg_df = spark.createDataFrame(compact_df)
                        shuffle_rows = len(
                            agg_df.groupBy("partition_id").agg(F.sum("reserve_0")).collect()
                        )
                    except Exception:  # noqa: BLE001 - summary verification is optional observability
                        shuffle_rows = 0
                shuffle_agg_seconds = perf_counter() - t_agg_start

                partitions_seen = sorted({item.partition_id for item in raw_outcomes})
                worker_pids = sorted({item.worker_os_pid for item in raw_outcomes})
                worker_hosts = sorted({item.worker_hostname for item in raw_outcomes})
                max_task_calc = max((item.calc_seconds for item in raw_outcomes), default=0.0)

                yield SparkExecutionWave(
                    wave_index=wave_idx,
                    outcomes=outcomes,
                    partition_count=num_slices,
                    partitions_seen=partitions_seen,
                    worker_os_pids=worker_pids,
                    worker_hostnames=worker_hosts,
                    broadcast_seconds=round(broadcast_seconds if wave_idx == 0 else 0.0, 4),
                    partition_exec_seconds=round(max_task_calc, 4),
                    collect_seconds=round(partition_plus_collect_s, 4),
                    shuffle_agg_seconds=round(shuffle_agg_seconds, 4),
                    shuffle_row_count=shuffle_rows,
                    executor_infos=_executor_snapshots(sc),
                )
        finally:
            if run_id:
                with _ACTIVE_SPARK_JOBS_LOCK:
                    current = _ACTIVE_SPARK_JOBS.get(run_id)
                    if current is not None and current[1] == job_group:
                        _ACTIVE_SPARK_JOBS.pop(run_id, None)
            try:
                sc.clearJobGroup()
            except Exception:  # noqa: BLE001
                pass
            if broadcast_template is not None:
                try:
                    broadcast_template.unpersist(blocking=False)
                except Exception:  # noqa: BLE001
                    pass
            try:
                spark.stop()
            except Exception:  # noqa: BLE001
                pass
