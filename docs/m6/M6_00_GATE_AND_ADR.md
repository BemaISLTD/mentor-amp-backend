# Mentor AMP — Phase 6 M6-00 Gate Evidence, Execution Backend ADR, and Spark/CUDA Learning Guide

**Gate:** `M6-00` (Environment, Architecture & Baseline Verification Gate)  
**Branch:** `feat/m6-scale-execution-integration` (branched from `main` @ `efb9657b084dff980d65326c5c244bfd043bd695`)  
**Frontend Baseline:** `MentorAmp-frontend` @ `sf_frontend_patch` (`0d0a4e9`, clean, unmodified)  
**Parallel Isolation:** `NOAH_PHASE7_IDI_VA_PRT_LIFE_REPORTING_README.md` untouched and excluded from Phase 6 commits; no `M6-01`..`M6-06` code changes started.

---

## 1. Verified Host, Tooling & Dependency Compatibility Matrix

### 1.1 Development Host Specs (Directly Observed)
| Component | Observed Value | Operational Implication |
|---|---|---|
| CPU | Intel Core i5-6300U @ 2.40 GHz (`2` cores / `4` threads) | Local Spark limited safely to `local[2]` / `local[4]` workers |
| Memory / Disk | `15.41 GB` RAM, `~23.6 GB` free disk | Fits JDK 17 + isolated Spark/GPU venvs without bloating repo |
| OS / Containers | Windows 11 Pro (`10.0.22621`); WSL2 `Ubuntu-24.04` (`Python 3.12.3`) | Primary dev/test on Windows Python `3.14.2`; Linux runners in CI |
| Display / GPU | Intel HD Graphics 520 (no NVIDIA GPU / `nvidia-smi` absent) | Local GPU verification is **simulator-only**; hardware CUDA benchmarks require NVIDIA runner/cloud VM (`M6-03`) |
| Database / Docker | Local PostgreSQL 18 service running (`127.0.0.1:5432`); Docker CLI present (daemon stopped) | Disposable Postgres tests run via GitHub Actions service container unless dedicated `*_test` DB is configured locally |

### 1.2 Isolated Python 3.14 Environments (`pip check` = 0 broken dependencies in all three)
| Environment | Location | Key Versions | Purpose & Boundary |
|---|---|---|---|
| **CPU Reference** | `backend/.venv` | `Python 3.14.2`, `numpy==2.5.4`, `polars==1.42.0`, `dagster==1.13.28`, `fastapi==0.138.0`, `SQLAlchemy==2.0.51`, `alembic==1.18.4`, `psycopg2-binary==2.9.12` | **Unmodified** M1–M5 production/CPU baseline; passes with zero Java, Spark, or CUDA installed |
| **Spark Optional** | `%LOCALAPPDATA%\MentorAmp\venv-spark` + `backend/requirements-spark.txt` | `pyspark==4.1.1`, `py4j==0.10.9.9`, `pyarrow==26.0.1`, `pandas==3.1.1`, Microsoft OpenJDK `17.0.21.0.2` (`%LOCALAPPDATA%\MentorAmp\jdk-17`) | Verified genuine local Spark Classic multi-partition execution on Python `3.14.2` |
| **GPU Simulator Optional** | `%LOCALAPPDATA%\MentorAmp\venv-gpu-sim` + `backend/requirements-gpu-sim.txt` | `numba==0.68.1`, `numba-cuda==0.30.4`, `nvidia-cuda-runtime==13.5.37` (`cuda-bindings==13.5.0`, `llvmlite==0.50.0`, `numpy==2.5.5`) | Verified `@njit` CPU JIT and `NUMBA_ENABLE_CUDASIM=1` `@cuda.jit` kernel syntax/parity (**non-hardware simulator only**) |

### 1.3 Verified Python 3.14 Compatibility Findings (Observed During Setup)
1. **Spark 3.5 vs Spark 4.1+ on Python 3.14:** Spark 3.5 fails on Python 3.14; **PySpark Classic `4.1.1` + OpenJDK `17.0.21.0.2`** boots cleanly and completes partitioned `run_policy()` workloads.
2. **Arrow DataFrames vs Python RDD Shuffles on Windows Python 3.14:**
   - **Observed Failure:** Calling untyped `spark.createDataFrame(list_of_dicts)` or Python RDD `reduceByKey(...)` on Windows Python `3.14.2` crashes in `PythonRunner` with `C:\Python314\Lib\socket.py:743 OSError: [WinError 10038]` (`BufferedRWPair` socket finalization).
   - **Verified Fix & Architectural Rule:** Setting `spark.sql.execution.arrow.pyspark.enabled=true` and constructing DataFrames via columnar `pd.DataFrame` (or `mapInArrow` / JVM Catalyst `groupBy().agg()`) transfers batches via Apache Arrow directly into JVM Catalyst with **zero socket crash** and `0.0` numerical drift.
3. **Numba + `numba-cuda` on NumPy 2.5 / Non-GPU Hosts:**
   - `numba 0.63.x` is incompatible with `numpy 2.5.x`; `numba>=0.67.0` (`0.68.1`) + `numba-cuda==0.30.4` resolves cleanly against `numpy==2.5.5`.
   - `numba-cuda 0.30.4` eagerly calls `runtime.getLocalRuntimeVersion()` at import time in `numba_cuda/numba/cuda/debuginfo.py:46` even when `NUMBA_ENABLE_CUDASIM=1` is set. Installing the small `nvidia-cuda-runtime==13.5.37` wheel (`~2.7 MB`) supplies the runtime DLL so simulator mode imports and executes without hardware drivers.

---

## 2. Backend, Package, Orchestration & Frontend Contract Inventory

| Layer | File Path & Symbol | Current State | Phase 6 Planned Change (Deferred to `M6-01+`) |
|---|---|---|---|
| System Status API | `backend/app/api/system.py:44-49` | Hardcodes `"execution_backend": "cpu"` alongside `"engine_version": ENGINE_VERSION` | Report runtime capabilities (`available_backends`, Spark/GPU availability, default/active mode) without breaking existing keys |
| Run-Set Request API | `backend/app/api/execution.py:30-36`, `76-141` | `RunSetRequest(project_id, name, projection_set_ids, scenario_ids, notes)` posts to `/v1/run-sets/preflight` & `/v1/run-sets` | Add optional `execution_backend` (`auto`/`cpu`/`spark`/`gpu`/`spark_gpu`) + `fallback_policy`, defaulting safely when omitted |
| RunPackage Hashing | `backend/app/core/execution/run_package.py:16-50` | `mentoramp.run_package/v2` hashes the entire `configuration` dict into `package.fingerprint` | **Preserve canonical v2 math hashes** (see ADR-001 §3.1) |
| Package Builders | `backend/app/services/run_package_service.py:621, 1305` | Hardcodes `"execution_backend": "cpu"` in projection & valuation packages | Keep canonical configuration neutral or separate runtime execution intent cleanly |
| Manifest Provenance | `backend/app/services/run_manifest_service.py:48-152` | `mentoramp.run_manifest/v2` copies `execution_backend` & runtime build metadata | Record `requested_backend`, `planned_backend`, `actual_backend`, `fallback_reason`, Spark/GPU telemetry |
| DB Models | `backend/app/db/models/run.py`, `run_package.py:32-104` | `RunPackage` is write-once; `RunAttempt` holds `worker_id`, `executed_build`, `metrics`, `status`, `error_type` | Store attempt-level backend plan/actuals/telemetry cleanly on `RunAttempt.metrics` / `RunManifest` (avoid breaking write-once `RunPackage` triggers) |
| Execution Service | `backend/app/services/run_execution_service.py` | Loops `run_policy()` sequentially in-process -> `LocalParquetArtifactStore` (`m2-v1`) | Delegate execution step to `ExecutionPlanner` + backend adapters (`CPU`, `Spark`, `GPU`); keep DB claims & artifact commits on driver only |
| Dagster Assets | `backend/app/orchestration/dagster/assets.py` | `ProjectionConfig` invokes `execute_projection_simulation()` | Pass optional backend override cleanly through `ProjectionConfig` |
| Frontend UI | `MentorAmp-frontend/src/pages/run-set-workflow/RunSetWorkflow.tsx`, `ExecutionCenter.jsx`, `src/types/mentoramp-api.ts` | Submits `{ project_id, name, projection_set_ids, scenario_ids, notes }` with no backend selector | `M6-05` only (coordinate with Sam Yung): display backend selector, capability badges, and actual/fallback telemetry |

---

## 3. Architecture Decision Record (ADR-001): Phase 6 Execution Architecture

### Decision 1 — Calculation-Neutral `RunPackage` Fingerprints vs Runtime Backend Intent
- **Context:** `backend/app/core/execution/run_package.py` hashes `configuration` into `RunPackage.fingerprint`. If changing `execution_backend` mutated `configuration` inside `RunPackage`, submitting the exact same actuarial model, data, assumptions, and scenario on `cpu` vs `spark` would yield two different `RunPackage` fingerprints and break deterministic parity comparisons.
- **Decision:** Keep the frozen actuarial `RunPackage` (`mentoramp.run_package/v2`) **calculation-neutral** (identical canonical `package.fingerprint` for the same actuarial inputs regardless of whether `cpu`, `spark`, or `gpu` executes it). Persist **execution intent and actual execution provenance** (`requested_backend`, `planned_backend`, `actual_backend`, `planner_reason`, `fallback_reason`, partition/device metrics) on `RunSet`/`RunAttempt.metrics` and `RunManifest.runtime`.
- **Consequence:** Zero regression to existing CPU `RunPackage` v2 tests or legacy immutable DB rows; makes CPU-vs-Spark-vs-GPU bit/tolerance parity audits trivial because both executions reference the exact same input package hash.

### Decision 2 — Additive Backend Selector & Explicit Safe Fallback Policy
- **Decision:** Introduce `ExecutionBackendMode = Literal["auto", "cpu", "spark", "gpu", "spark_gpu"]` and `FallbackPolicy = Literal["allow_cpu_fallback", "strict_fail"]`:
  - `auto`: Routes small workloads to pure `cpu` (below `M6_MIN_POLICIES_FOR_SPARK` threshold observed in §5), large policy/scenario workloads to `spark`, and dense numeric batches to `gpu`/`spark_gpu` when available.
  - Explicit `spark` / `gpu`: Attempts requested backend; if unavailable or degraded:
    - With `allow_cpu_fallback` (default for interactive resilience): safely executes on `cpu`, marks `fallback_used = true` + `fallback_reason`, emits run event, and surfaces badge in API/UI.
    - With `strict_fail` (CI / benchmark / SLA enforcement): fails cleanly with structured `EngineError` rather than silently succeeding on CPU.

### Decision 3 — PySpark Worker Purity & Driver-Only Persistence
- **Decision:** PySpark workers remain 100% pure (no SQLAlchemy sessions, no PostgreSQL connections, no non-idempotent DB mutations inside executors). Workers initialize `register_all_products()`, receive broadcast `RunData` template tables once via `sc.broadcast(...)`, process partitions via `mapPartitionsWithIndex` / Arrow batches, and return deterministic outputs/traces (or write staged partition Parquet) to the driver for governed `LocalParquetArtifactStore` + `RunManifest` finalization.

### Decision 4 — First Proposed GPU Kernel Workload & Toolchain
- **Selected Kernel Workload:**
  1. **Batched Prospective Reserve & Discounting Scan** matching `backend/app/core/valuation/illustrative_reserve.py` (`reserve_t = (reserve_{t+1} + cash_flow_{t+1}) * (1 + i)^(-1/12)`) and `backend/app/core/valuation/primitives.py` (`curve_to_discount_factors`, `discount_cash_flows`, `present_value`) reshaped as dense contiguous `float64` arrays of shape `(N_policies * N_scenarios, H_months)`.
  2. **RILA / FIA Multi-Scenario Path Credit Kernel:** Vectorized/SIMT capped/floored/buffer index crediting across stochastic or stressed economic scenario grids `(N_policies, N_scenarios, H_months)`.
- **Toolchain:** `numba-cuda` (`@cuda.jit` explicit thread-per-policy/path kernels in `float64`) verified locally under `NUMBA_ENABLE_CUDASIM=1` and benchmarked against CPU NumPy/`numba @njit` on real NVIDIA CUDA hardware in `M6-03`. Keep `float64` default to prevent single-precision `float32` compounding drift in 40-year annuity projections.

### Decision 5 — Representative Spark Benchmark Workload Proposal (`M6-02` / `M6-03`)
- Synthesize deterministic policy portfolios scaled cleanly from real `MYGA_LITE` (`480` months) and `SPIA_REFERENCE` (`240` months) definitions at **`N = 100`, `1,000`, `10,000`, and `25,000` policies** across `2–10` scenarios (`24,000` to `12,000,000+` policy-months).
- Capture per-scale stage metrics: JVM boot vs warm session, broadcast size, partition count (`2, 4, 8`), serialization/Arrow transfer time, executor task time, driver Parquet commit time, peak RSS, and max absolute diff vs CPU.

---

## 4. Governance & Open Sign-Off Gates (`CAS §17` & `ACT-DEC-*`)

The following remain **OPEN / GATED** at the end of `M6-00` and are NOT claimed signed-off:
1. **`CAS §17` Performance Benchmark & Numerical Parity Tolerances:** Proposed engineering tolerances (`max_abs_diff <= 1e-9` for identical `float64` deterministic arithmetic, `<= 1e-6` or actuarial owner threshold where parallel FMA ordering differs) require Product Owner / Actuarial confirmation before `M6-03` acceptance closure (distinct from M5 `ACT-DEC-06`).
2. **Signed Actuarial Product Oracles (`ACT-M4-DEC-01..15`):** Confirmed in `tests/golden_policy/test_fia_golden.py:30` and `test_rila_golden.py:39` that owner-approved FIA/RILA golden benchmarks remain skipped/pending sign-off; Phase 6 parity compares Spark/GPU against the frozen CPU reference engine plus already-validated SPIA reference and MYGA engineered baselines.
3. **NVIDIA Hardware Execution:** No NVIDIA GPU exists on this Windows laptop; `@cuda.jit` under `NUMBA_ENABLE_CUDASIM=1` validates API parity only and is NOT CUDA timing/hardware evidence.
4. **Hosted GitHub Actions CI:** `.github/workflows/ci.yml` has been created and YAML-validated locally, and will only be claimed green after executing on GitHub runners on push/PR.

---

## 5. Observed M6-00 Gate Verification Evidence

### 5.1 Pure-CPU Regression & Alembic Check (`backend/.venv`, Zero Java/Spark/CUDA)
| Check Command | Outcome | Notes |
|---|---|---|
| `pytest backend/tests/unit/test_wp1_alembic_history.py -q` | **3 passed** (`3.46s`) | Canonical Alembic single head verified (`c9f4e2d8a615`) |
| `pytest backend/tests/unit backend/tests/golden_policy -q` | **524 passed, 2 skipped** (`229.99s`) | Both skips are explicit pending `ACT-M4-DEC-01..15` (`test_owner_approved_fia_golden`, `test_owner_approved_rila_golden`) |
| `pytest tests/integration/test_api_contract.py test_m1_end_to_end.py test_wp1_reproducibility.py test_wp1_run_safety.py test_run_control.py -q` | **61 passed** (`229.85s`) | Zero failures across API contract, run control, safety & reproducibility |
| `pytest test_wp1_alembic_history.py golden_policy test_spia_reference_workflow.py test_m4_indexed_workflow.py -q` | **17 passed, 3 skipped** (`121.46s`) | 3 skips are opt-in `MENTORAMP_TEST_POSTGRES_URL` disposable DB tests |

### 5.2 Frozen CPU Reference Baseline (`backend/scripts/capture_m6_00_cpu_baseline.py`)
- **Workload:** `MYGA_LITE` (`25` policies × `480` months), `Base` + `Rates Up 150bp` + deterministic replay (`43.445s` execution+replay; `44.672s` total)
- **Base Scenario Run (`4f1583dd-0081-4191-8e2c-652cc8e3287f`):**
  - `RunPackage` (`mentoramp.run_package/v2`): `983963880ebaa6e519df085973fe9ba43dc4a5f96fdefa57a7a3b0e8ab5fcb97`
  - `RunManifest` (`mentoramp.run_manifest/v2`): `d4a58a48c74cc4a3999bde02837741002c2bf5af30fc9124686742077298cc45`
  - Headline Reserve: `4163674.9018098796` (abs diff vs golden oracle: `4.66e-10`)
  - Undiscounted `liability_outgo`: `5298179.05844914` (abs diff: `0.0`)
  - Artifacts (`m2-v1` Parquet): outputs (`63,371` rows, SHA-256 `5d068bc41ba01dd6003b10dd8dd1234a8f402e587f856d545441701d3da11e25`) + outputs (`80,654` rows, SHA-256 `971ef41b0c4e22cb3f55777ccf4e24bc3e9602fc86ab6f5b8f40f0d41f125ae5`) + traces (`41,761` rows, SHA-256 `0d63bc3964cf717d73e01987496607a716ecdcf81a89dafba51ac880f6985913`)
- **Rates Up 150bp Scenario Run (`c8ed64a5-9209-42f6-8cc6-c17ba260e351`):**
  - `RunPackage`: `d80cef928d0b132cead4b89f63c568bd797d58441c084904e0b7dde1a70c3f75`
  - `RunManifest`: `dbc94fd780166d01baf29796a54eb692ada7d68974766e13991e87588d1c94f8`
  - Headline Reserve: `4201201.546339422` (abs diff: `0.0`); `liability_outgo`: `5358149.664428925` (abs diff: `9.31e-10`)
- **Trace Check (`MYGA-0001`, month 30, `surrender_benefit`):** `51979.599258014285` (abs diff: `0.0`), Deterministic Parquet byte-for-byte replay: **PASS**.

### 5.3 Live Local PySpark Classic Partition Smoke (`backend/scripts/smoke_spark_m6_00.py`)
- **Runtime:** `Python 3.14.2`, `PySpark 4.1.1`, `Spark 4.1.1`, `OpenJDK 17.0.21.0.2`, `master=local[2]`, Driver OS PID `4736`
- **Workload:** `16` lifelib-fixture-anchored `SPIA_REFERENCE` policies × `24` months (`backend/tests/fixtures/spia_reference_lifelib_v0_17_2.json`)
- **CPU baseline:** `0.5621s`
- **Spark JVM session boot:** `19.368s`
- **2 Partitions (`mapPartitionsWithIndex`):** `8.005s`, Worker OS PIDs `[16532, 18952]`, partition IDs `[0, 1]`, `max_abs_diff_vs_cpu = 0.0`
- **4 Partitions (`mapPartitionsWithIndex`):** `13.018s`, Worker OS PIDs `[15416, 17964, 22092, 22504]`, partition IDs `[0, 1, 2, 3]`, `max_abs_diff_vs_cpu = 0.0`
- **Arrow Catalyst DataFrame Shuffle (`df.groupBy("partition_id").agg(...)`):** 4 groups × 4 policies each, `total_reserve_diff_vs_cpu = 0.0` (**PASS**)

### 5.4 Isolated GPU Simulator & Numba CPU JIT Sanity Check (`venv-gpu-sim`, Non-Hardware)
- Batched prospective reserve `(8, 60)` kernel matching `app/core/valuation/illustrative_reserve.prospective_reserve`:
  - `numba @njit` (CPU): `max_abs_diff = 0.0`
  - `numba-cuda @cuda.jit` under `NUMBA_ENABLE_CUDASIM=1`: `max_abs_diff = 0.0` (**simulator-only / non-hardware**)

### 5.5 Initial CI Workflow (`.github/workflows/ci.yml`)
- Three isolated jobs: `cpu-and-migrations` (Python 3.14, pure CPU dependencies, Alembic canonical head + unit/golden/integration), `postgres-disposable` (`postgres:18-alpine` service container with `MENTORAMP_TEST_POSTGRES_URL` -> `mentoramp_ci_test`), and `spark-local-smoke` (Temurin Java 17 + `requirements-spark.txt` + `smoke_spark_m6_00.py`).
- Pinned action major tags, `permissions: contents: read`, non-production dummy secrets only. Validated locally with `yaml.safe_load`.

---

## 6. Learning Walkthrough: Spark & CUDA Concepts Grounded in Your Telemetry

When you read the JSON output from `smoke_spark_m6_00.py` above, you just watched every core distributed-computing concept happen in real life:

1. **Driver (`driver_os_pid = 4736`):**
   - The Python process running `smoke_spark_m6_00.py` + the Spark JVM `SparkContext` coordinator. It plans the DAG, broadcasts shared lookup tables, schedules tasks, and collects final results (and in Mentor AMP M6-02+, ONLY the driver writes to PostgreSQL and `LocalParquetArtifactStore`).
2. **Executor:**
   - The JVM worker container running tasks. In `local[2]`, the driver process hosts a local executor with 2 execution slots. Because PySpark runs user Python code (`run_policy`), the JVM executor spawns **Python worker processes** (`worker_os_pids = [15416, 17964, 22092, 22504]`) over local IPC pipes/sockets.
   - *Key Mentor AMP insight:* Because worker Python processes start fresh and do NOT inherit your driver's FastAPI startup memory, each partition inside `mapPartitionsWithIndex` **must call `register_all_products()`**, exactly as `backend/app/products/registry.py` documents!
3. **Partition (`partition_ids_seen = [0, 1, 2, 3]`):**
   - A horizontal slice of policies (`16` policies split into `4` partitions of `4` policies each). One partition is the fundamental unit of parallel scheduling.
4. **Task (`[Stage 1: (3 + 1) / 4]`):**
   - One unit of work executing your function on **one partition** on **one executor slot**. With `master="local[2]"` and `4` partitions, Spark ran 2 tasks concurrently, then the next 2 (`0+2 -> 1+2 -> 2+2 -> 3+1`).
5. **Broadcast (`sc.broadcast(template)`):**
   - Instead of serializing and resending mortality tables, formula graphs, and scenario metadata 16 times (once per policy) or 4 times (once per task), `sc.broadcast(template)` sends read-only shared `RunData` tables **once per executor** and reuses `broadcast_template.value` across all tasks on that executor.
6. **Serialization (`Py4J` / `pickle` / `Apache Arrow`):**
   - Moving data across the Python <-> JVM boundary. In Stage 0/1, policy inputs and outputs crossed via Python worker sockets. In Stage 2 (`spark.createDataFrame(pd.DataFrame(p4_rows))`), columnar **Apache Arrow** transferred memory cleanly into the JVM — avoiding Windows Python 3.14 `socket.py` worker serialization bugs completely!
7. **Shuffle (`df.groupBy("partition_id").agg(...)`):**
   - Redistributing rows across partitions by key (required by `groupBy`, `join`, `repartition`). Shuffles write intermediate data to disk and cross the network in clusters — so rule #1 of Spark performance is **run policy projections embarrassingly parallel (`mapPartitions`) with zero shuffle, and shuffle/reduce only compact per-policy summaries or monthly totals**.
8. **Local Mode (`local[2]`) vs Cluster Mode:**
   - **Local mode:** Everything runs in one machine (`local[k]` gives `k` threads). Notice boot took **`19.37s`** and 4 partitions took **`13.02s`** vs **`0.56s`** on CPU! That proves empirically why Spark **slows down small runs (< ~1,000–5,000 policy-simulations)** due to JVM + Python worker IPC overhead.
   - **Cluster mode (`spark://...`, YARN, Kubernetes):** The driver coordinates multiple remote executor JVMs across many machines — only then does horizontal scaling beat the fixed `~20s` startup overhead on large portfolios (`10,000+` policies or multi-scenario runs).
9. **CUDA Kernel (`@cuda.jit` & `cuda.grid(1)` preview for `M6-03`):**
   - While Spark scales *horizontally across machines* (1 partition per CPU core), **CUDA** scales *massively parallel inside one GPU* by launching thousands of lightweight threads simultaneously (`p = cuda.grid(1)` assigns thread `p` to policy/scenario `p`, running the backward reserve loop independently in registers/L1 cache on contiguous `float64` NumPy/device arrays rather than Python dicts and loops).
