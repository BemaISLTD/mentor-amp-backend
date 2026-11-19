# Mentor AMP — M6-02: Real Distributed PySpark Execution

## Architecture & Engine Reuse

`SparkExecutionBackend` (`backend/app/core/execution/spark_backend.py`) implements the `ExecutionBackend` protocol using the exact shared calculation engine (`run_policy()` + `register_all_products()` + `FORMULA_FUNCTIONS`), with **zero actuarial formulas rewritten in Spark SQL** and full preservation of the backend-neutral `RunPackage` fingerprint.

- **Driver vs. Workers**:
  - The FastAPI/execution driver validates the frozen `RunPackage` and `RunExecutionPlanner` plan, strips cached compiled DSL callables into an immutable `SparkWorkerTemplate`, broadcasts the template once per execution (`sc.broadcast(template)` on local modes and POSIX clusters; direct immutable closure transport on `win32` Standalone clusters to bypass upstream Apache Spark 4.1.1 `PythonBroadcast.readObject` `MoveFileEx` open-handle locks on Windows), and dispatches deterministic indexed waves (`RDD.mapPartitionsWithIndex`).
  - Zero SQLAlchemy sessions, database connections, secrets, or artifact-store handles are serialized to workers.
  - Each Python worker registers product formulas (`register_all_products()`), computes each policy with `run_policy(worker_data, policy, FORMULA_FUNCTIONS)`, and emits plain serializable outcomes (`SerializedPolicyOutcome`).
- **Bounded Wave Execution & Governance**:
  - `run_execution_service.py` consumes `SparkExecutionBackend.execute_waves(...)` in bounded policy batches (`batch_size`, default `250`, max `5000`), updating `Run.progress_done`, steps, heartbeats, and writing governed `m2-v1` Parquet output/trace artifacts (`RunArtifact`) + `RunManifest` between waves without unbounded full-portfolio driver materialization.
  - Optional `allow_cpu_fallback` applies **strictly** to pre-calculation `SparkSessionStartupError` before Wave 0 starts; mid-run failures raise `SparkWaveExecutionError` and fail cleanly without silent CPU fallback.
- **Security Allowlists & Compatibility Inspection**:
  - `validate_spark_master()` enforces an explicit server allowlist (`settings.spark_allowed_masters`, default `local,local[*],local[1..16]`) and rejects shell-unsafe characters (`SPARK_MASTER_FORBIDDEN`).
  - `inspect_spark_model_compatibility()` verifies formula DAG acyclicity (`topological_sort`), DSL expression validity, `FORMULA_REGISTRY` availability, and rejects cross-policy/portfolio-scoped variables.

## M6-02 Review Corrections Implemented

1. **M6-02-01 (Canonical Per-Policy Error Isolation & Partial Success)**:
   - Preserved all Data Manager canonical validation rules (verified explicit rejection of incomplete SPIA CSVs cleanly with `status == "invalid"`, `error_count >= 1`, and blocked `/commit`).
   - Verified runtime isolation using a schema-valid canonical SPIA Reference CSV containing one runtime-failing policy (`BAD-FORM-1` triggering `ValueError("unsupported benefit_form")`): both CPU and Spark return `partial_success`, `error_count == 1`, `accepted_attempt_number == 1`, withhold default `/results` (`409 RESULTS_INCOMPLETE`) and block incomplete reconciliation until complete, and match outputs, traces, and artifact row counts on all surviving policies (`GOOD-1`, `GOOD-2`).
2. **M6-02-02 (Active Cancellation, Spark Job Groups & Retry Determinism)**:
   - Assigned each Spark run attempt a unique job group (`mentoramp-run-{run_id}-attempt-{attempt_number}`) with `interruptOnCancel=True` and wired `run_control_service.cancel_run()` -> `cancel_spark_run()` -> `sc.cancelJobGroup(...)`, alongside fresh DB `cancel_requested_at` checks between waves (`SparkCancelledError`).
   - Verified mid-run cancellation halts before Wave 1/Wave 2 run, retains Wave 0 partial artifacts noncanonically (`results_available=False`), and verified worker-crash failure + API `/retry` completes with zero duplicate output rows (`1225` unique rows across 3 bounded wave part files).
3. **M6-02-03 (Local Standalone Multi-Worker Execution & Driver/Executor Separation)**:
   - Replaced stale active `SparkContext`/`SparkSession` instances on master change, configured `SPARK_HOME`, `SPARK_SCALA_VERSION=2.13`, and `spark_executor_memory="512m"`.
   - Added a strictly scoped `win32`-only worker bootstrap (`backend/app/core/execution/_spark_win_bootstrap/sitecustomize.py`) flushing CPython 3.14 `BufferedRWPair` on Spark protocol stream sentinels (`-1`, `-2`, `-4`), leaving POSIX/CI paths standard.
   - Separated driver telemetry from registered non-driver worker executors (`worker_executor_infos`, `worker_executor_ids`, `executor_infos`) in `Run` provenance.
   - Verified on a **local standalone multi-worker cluster** (one Master JVM + two separate Worker JVMs on `127.0.0.1`) with 4 partitions, 2 non-driver application executors, >=2 distinct worker Python OS PIDs, and full CPU parity across outputs, traces, totals, and reserve.

## Parity Verification & Empirical Benchmarks

- **Provisional Engineering Tolerance**: All M6-02 automated parity checks use `abs_tol = 1e-9` (actual observed diffs in `benchmark_m6_02_spark_parity.py` were `0.0`). **CAS §17 formal numerical-tolerance governance and actuarial golden-test approvals remain OPEN pending Product Owner confirmation.**
- **Empirical CPU vs. Spark Benchmark (`backend/scripts/benchmark_m6_02_spark_parity.py`, 4-core Windows-11, `venv-spark` Python 3.14.2)**:
  - `16` policies x `240` months (`3,840` policy-months): CPU `2.172s` vs. `Spark local[2]` `36.003s` (includes `18.37s` cold JVM/session boot, `max_abs_diff = 0.0`) and `Spark local[4]` `12.603s` (`max_abs_diff = 0.0`).
  - `100` policies x `240` months (`24,000` policy-months): CPU `17.049s` vs. `Spark local[2]` `14.765s` (`1.155x`, `max_abs_diff = 0.0`) and `Spark local[4]` `16.211s` (`1.052x`, `max_abs_diff = 0.0`).
  - `300` policies x `240` months (`72,000` policy-months, `2` waves): CPU `34.759s` vs. `Spark local[2]` `38.423s` (`0.905x`, `max_abs_diff = 0.0`) and `Spark local[4]` `38.835s` (`0.895x`, `partition_exec_seconds = 0.6896s`, `max_abs_diff = 0.0`).
  - For small portfolios, JVM startup, template broadcast, and Python worker IPC/pickling dominate compute time (`spark_min_policy_months = 50_000` remains a conservative provisional default).

## Out of Scope / Not Started

- M6-03 GPU / CUDA prospective-reserve kernel execution is **not** started here.
- Multi-host cloud cluster deployment (EMR/Databricks/Kubernetes) is outside M6-02 local/CI verification scope.
