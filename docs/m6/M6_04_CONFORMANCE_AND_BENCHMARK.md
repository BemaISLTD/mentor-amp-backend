# M6-04: Numerical Parity, Performance Benchmarking & Recovery Conformance

> **Branch:** `feat/m6-scale-execution-integration`  
> **Reviewed M6-04 Commit:** `ce1b8610d8102ef15fba32a7c206b0bb7a4bcb6b` (extending M6-03 commit `95caefe989caa7a52cfc12fd524a37fe6a87fff9`)  
> **Acceptance Record:**
> - **M6-04 execution conformance:** **ACCEPTED FOR PROGRESSION** (to M6-05)
> - **Physical GPU hardware acceptance:** **OPEN / NOT RUN** (awaiting Product Owner Eko approval for physical NVIDIA GPU infrastructure/budget; simulator results below are **Non-Hardware** only)
> - **Formal CAS §17 performance and numerical tolerance approval:** **OPEN** (provisional engineering tolerance `abs_tol = 1e-9` / `rel_tol = 1e-9` only; independent actuarial golden approvals remain separate from cross-backend parity)
> - **M6-05 frontend integration:** **OPEN**
> - **Production-scale remote Spark deployment:** **NOT VERIFIED**

---

## 1. Execution Conformance Matrix (Identical Frozen `RunPackage` Inputs)

Verified in `backend/tests/spark/test_m6_04_conformance_and_recovery.py`, `backend/tests/spark/test_m6_spark_execution.py`, `backend/tests/spark/test_m6_spark_concurrency.py`, and `backend/tests/gpu/test_m6_gpu_execution.py`:

| Product / Reference Model | Backends Compared | Frozen `RunPackage` Fingerprint | Policy / Scenario / Month / Variable Parity | Semantic Trace & Trace-Tree Parity | Reconciliation | Deterministic Replay | Evidence Classification |
|---|---|---|---|---|---|---|---|
| `SPIA` (`SPIA_LITE`, `SPIA_REFERENCE`) | CPU vs PySpark (`local[1]`, `local[2]`, `local[4]`, local standalone 2-worker) | Exact SHA-256 match | `max_abs_diff = 0.0`, `max_rel_diff = 0.0` (`currency`, `probability`) | Exact key-set + `formula_id`/`source_type`/`input_values`/`output_value` + `/trace` tree (`depth=6`) | `PASS` (`1e-9`) | Exact replay across `p=1`, `p=2`, `p=4` | Real CPU + Real Spark |
| `MYGA` (`MYGA_LITE`) | CPU vs PySpark (`local[2]`, `local[4]`, bounded waves `batch_size=7`) | Exact SHA-256 match | Matches CPU and golden values (`abs_tol = 1e-6` golden, `1e-9` parity) | Exact trace parity (`surrender_benefit`, month 30, `depth=8`) | Golden + parity PASS | Exact Parquet byte replay across `p=2` & `p=4` | Real CPU + Real Spark |
| `FIA` (`FIA_LITE`) | CPU vs PySpark (`local[2]`, `p=2`) | Exact SHA-256 match | Full row-for-row `max_abs_diff <= 1e-9` across all outputs | Exact semantic trace parity across all trace rows | `PASS` (`1e-9`) | Deterministic `m2-v1` Parquet outputs | Real CPU + Real Spark |
| `RILA` (`RILA_LITE`) | CPU vs PySpark (`local[2]`, `p=2`) | Exact SHA-256 match | Full row-for-row `max_abs_diff <= 1e-9` across all outputs | Exact semantic trace parity across all trace rows | `PASS` (`1e-9`) | Deterministic `m2-v1` Parquet outputs | Real CPU + Real Spark |
| `PROSPECTIVE_RESERVE_DISCOUNTING` (Internal GPU Ref) | CPU vs CUDA Simulator (`NUMBA_ENABLE_CUDASIM=1`) | Exact SHA-256 match | `144,100` points, `max_output_abs_diff = 0.0` (`1e-9` tol) | All 772 trace rows (incl. `scenario_override`) + `/trace` tree (`depth=6`) | `PASS` (`1e-9`) | Bitwise repeat determinism | **Simulator Only (Non-Hardware)** |

---

## 2. Numerical Tolerance Governance (Output-Type Classification)

| Output Type | Observed CPU vs Spark Diff (`1,333,300` benchmark points) | Provisional Engineering Tolerance | Formal CAS §17 Owner Status |
|---|---|---|---|
| **Currency** (`annuity_payment`, `pv_liability_cash_flow`, `reserve`, `account_value_close`, etc.) | `1,066,900 / 1,066,900` exact matches (`max_abs_diff = 0.0`, `max_rel_diff = 0.0`) | `abs_tol = 1e-9`, `rel_tol = 1e-9` | **OPEN** (pending Eko sign-off) |
| **Probability** (`survival_probability`, mortality/lapse probs) | `266,400 / 266,400` exact matches (`max_abs_diff = 0.0`, `max_rel_diff = 0.0`) | `abs_tol = 1e-9`, `rel_tol = 1e-9` | **OPEN** (pending Eko sign-off) |
| **Rates & Factors** (`discount_factor`, `credited_rate`, `surrender_charge_rate`) | Verified exact (`0.0`) in conformance suites | `abs_tol = 1e-9`, `rel_tol = 1e-9` | **OPEN** (pending Eko sign-off) |

*Governance separation:* Cross-backend numerical parity proves Spark/GPU matches the Mentor AMP CPU engine. Independent actuarial accuracy remains governed separately by golden-policy validation and actuarial model approval.

---

## 3. Raw Empirical CPU vs PySpark Benchmark Results

Measured on Windows 11, Python `3.14.2`, `4` logical CPUs, Apache Spark `4.1.1` + Java `17`, running `backend/scripts/benchmark_m6_04_conformance.py` (`SPIA_REFERENCE`, 5 output variables: `survival_probability`, `payment_factor_life`, `annuity_payment`, `pv_liability_cash_flow`, `reserve`). Raw JSON saved in `.m4-review/m6_04_benchmark_results.json`.

> **Benchmark Provenance:** `.m4-review/m6_04_benchmark_results.json` records `source_commit = 95caefe989caa7a52cfc12fd524a37fe6a87fff9` because the script executed on the local `feat/m6-scale-execution-integration` working tree (HEAD `95caefe989caa7a52cfc12fd524a37fe6a87fff9` plus the uncommitted M6-04 changes: `benchmark_m6_04_conformance.py`, the 64-bit Windows `_peak_rss_mb()` handle fix in `benchmark_m6_02_spark_parity.py`, the `spark.python.authenticate.socketTimeout = 120s` setting in `spark_backend.py`, and the M6-04 conformance test suite) immediately prior to committing that exact working tree verbatim as `ce1b8610d8102ef15fba32a7c206b0bb7a4bcb6b`. No calculation-path code changed between the benchmark run and commit `ce1b8610d8102ef15fba32a7c206b0bb7a4bcb6b`; the raw JSON `source_commit` value is preserved unaltered for provenance accuracy.
>
> **Measurement Scope:** `benchmark_m6_04_conformance.py` measures the **actuarial calculation and `m2-v1` Parquet serialization paths only** (CPU runner vs PySpark backend). It does **not** measure the full UI, HTTP API, relational database persistence, trace query, reconciliation, or CSV export lifecycle.

| Workload | Policy-Months | Spark Master / Partitions | Worker PIDs | CPU (s) | Spark E2E (s) | Boot (s) | Broadcast (s) | Partition Exec (s) | Collect (s) | Shuffle (s) | CPU Thru (pm/s) | Spark Thru (pm/s) | Speed Ratio (CPU/Spark) | Output Rows | Parquet Size (bytes) | Peak Driver RSS (MB) | Parity (`max_abs_diff`) |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `small_25p_24m_2sc_cold` | 1,200 | `local[2]` / 2 | 4 | 1.3545 | 44.1119 | 26.8908 | 0.6733 | 0.0876 | 12.5530 | 2.6951 | 885.9 | 27.2 | 0.031x | 6,050 | 50,068 | 183.09 | `0.0` (6,050/6,050 exact) |
| `small_25p_24m_2sc_warm` | 1,200 | `local[2]` / 2 | 4 | 0.9775 | 16.0184 | 0.4629 | 0.0852 | 0.0565 | 13.4741 | 0.1388 | 1,227.7 | 74.9 | 0.061x | 6,050 | 50,068 | 185.79 | `0.0` (6,050/6,050 exact) |
| `medium_100p_120m_2sc_p2` | 24,000 | `local[2]` / 2 | 8 | 27.6704 | 47.2301 | 0.4198 | 0.0798 | 1.2142 | 44.7488 | 0.0988 | 867.4 | 508.2 | 0.586x | 120,200 | 612,620 | 261.82 | `0.0` (120,200/120,200 exact) |
| `med_long_200p_240m_1sc_p4` | 48,000 | `local[4]` / 4 | 8 | 48.6596 | 54.4181 | 0.2182 | 0.0437 | 1.3162 | 53.2864 | 0.0420 | 986.4 | 882.1 | 0.894x | 240,200 | 1,127,365 | 485.81 | `0.0` (240,200/240,200 exact) |
| `large_400p_240m_1sc_p2` | 96,000 | `local[2]` / 2 | 8 | 101.2451 | 83.4673 | 0.1883 | 0.0400 | 2.2600 | 82.2083 | 0.0496 | 948.2 | 1,150.2 | **1.213x** | 480,400 | 2,253,744 | 739.78 | `0.0` (480,400/480,400 exact) |
| `large_400p_240m_1sc_p4` | 96,000 | `local[4]` / 4 | 16 | 65.9872 | 64.4643 | 0.1680 | 0.0291 | 1.6650 | 63.2330 | 0.0403 | 1,454.8 | 1,489.2 | **1.024x** | 480,400 | 2,253,744 | 763.91 | `0.0` (480,400/480,400 exact) |

### Honest Empirical Interpretation (No Invented Speedups)
- **Cold startup dominates tiny runs:** Booting the Spark JVM cold took `26.89s` (plus Python worker spawn & `collect()` overhead `12.55s`), making Spark `~32x` slower than CPU (`1.35s`) on `1,200` policy-months. Warm session reuse cut boot time from `26.89s` down to `0.46s`, but Python worker serialization + `collect()` (`13.47s`) still dominated small portfolios.
- **Provisional, hardware-specific `auto` threshold (`50,000` policy-months):** On this single 4-logical-CPU Windows laptop, Spark stayed slower than CPU at `24,000` (`0.586x`) and `48,000` (`0.894x`) policy-months and was slightly faster at `96,000` policy-months (`1.213x` on `local[2]`, `1.024x` on `local[4]`). The planner's current `spark_min_policy_months = 50,000` setting is retained as a **provisional, hardware-specific heuristic** to avoid routing small local runs into Spark JVM overhead — it is **not** an approved production crossover threshold, and these small single-laptop benchmarks do **not** promise or establish production Spark speedup.
- **Over-partitioning on a small host:** Going from `local[2]` (8 worker PIDs) to `local[4]` (16 worker PIDs across 4 waves) on a 4-logical-CPU laptop increased driver RSS (`739.8 MB -> 763.9 MB`) and CPU/OS process contention without linear speedup.
- **Scope & governance boundary:** These numbers reflect isolated calculation + Parquet write paths on one local developer workstation only. Production performance thresholds, cluster sizing, and full UI/API/database/export lifecycle latency remain **OPEN** pending remote cluster verification and Product Owner (Eko) sign-off.

---

## 4. Failure & Recovery Conformance Evidence

Verified in `backend/tests/spark/test_m6_04_conformance_and_recovery.py`, `backend/tests/spark/test_m6_spark_execution.py`, and `backend/tests/spark/test_m6_spark_concurrency.py`:

| Scenario | Verified Behavior | Status |
|---|---|---|
| **Failed Spark partition / executor interruption mid-wave** | Wave 1 crash after Wave 0 flush retains partial noncanonical Parquet (`attempt_number=1`), sets `status="failed"`, `accepted_attempt_number=None`, `results_available=False`, blocks `/results` with `409`, and **never** falls back silently to CPU even when `allow_cpu_fallback` is set | **PASS** |
| **Partial Parquet writes & duplicate artifact prevention** | Retrying via `POST /v1/runs/{id}/retry` creates a clean child run (`202 Accepted`) whose outputs have zero duplicate `(policy_id, scenario_id, projection_month, variable_name)` keys and intact `m2-v1` schema | **PASS** |
| **Driver interruption & claim idempotency** | Open `running` attempt claimed by a crashed driver cannot be double-claimed by another worker (`execute_run` returns `None`) | **PASS** |
| **Run cancellation (single & concurrent)** | Mid-wave cancellation transitions cleanly to `cancelled` (`results_available=False`); cancelling one concurrent Spark run isolates its job group and leaves a peer run finishing `success` | **PASS** |
| **Corrupted Parquet artifact** | Tampering with stored `.parquet` bytes causes `read_attempt_artifact_rows` and `GET /v1/runs/{id}/results` to fail closed loudly (`ARTIFACT_CHECKSUM_MISMATCH` / `409`) | **PASS** |
| **Unsupported backend & resource limits** | Unknown backend (`quantum_tpu`), `spark_partitions > max`, `batch_size > max`, forbidden `spark_master`, and unavailable/incompatible GPU (`strict_fail`) fail closed (`422` / `409`) | **PASS** |
| **Pre-Wave-0 startup/quota failure** | `SparkSessionStartupError` before Wave 0 cleanly falls back to CPU under `allow_cpu_fallback` (logging `execution_fallback`) and fails closed under `strict_fail` | **PASS** |
| **Project authorization & isolation** | Cross-project user receives `403`/`404` on submit, get run, results, manifest, trace, cancel, and retry | **PASS** |

---

## 5. Regression & Hosted CI Verification

| Suite / Environment | Result |
|---|---|
| `backend/.venv` (Pure CPU + Alembic + Golden + Integration) | **801 passed, 4 skipped** |
| `venv-spark` (Spark unit, GPU unit, planner API, Spark E2E, concurrency, M6-04 conformance & recovery) | **49 passed, 0 failed** |
| `venv-gpu-sim` (`NUMBA_ENABLE_CUDASIM=1`, non-hardware GPU unit + planner + semantic trace parity) | **38 passed, 0 failed** |
| Hosted GitHub Actions CI (`Mentor AMP Backend CI`, 4 jobs incl. Disposable PostgreSQL & Alembic single-head) | **PASS** on `ce1b8610d8102ef15fba32a7c206b0bb7a4bcb6b` (run `44896433352`) |

---

## 6. Outstanding Gates & Owner Decisions (Phase 6 Remains OPEN)

| Gate / Decision | Status | Owner / Next Step |
|---|---|---|
| **M6-04 CPU/Spark Engineering Conformance** | **ACCEPTED FOR PROGRESSION** | Progressing to M6-05 frontend-to-backend integration |
| **M6-03 Physical NVIDIA GPU Verification** | **OPEN / NOT RUN** | Pending Eko approval for authorized NVIDIA GPU VM / runner (`benchmark_m6_03_gpu.py --require-physical-gpu`) |
| **CAS §17 Formal Performance & Numerical Tolerance Approval** | **OPEN** | Pending Eko Product Owner sign-off (provisional `1e-9` engineering tolerance currently in use; independent actuarial golden approvals remain separate from cross-backend parity) |
| **M6-05 Live Frontend-to-Backend E2E Integration** | **OPEN** | Active next priority with Sam Yung (`docs/m6/M6_05_SAM_YUNG_FRONTEND_HANDOFF.md`) |
| **Production-Scale Remote Spark Deployment** | **NOT VERIFIED** | Single-process local concurrency and local 2-worker standalone verified; multi-host cloud cluster remains unverified |
