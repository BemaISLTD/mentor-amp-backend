# M6-03: Genuine CUDA GPU Execution Backend & Final Review Hardening

> **Branch:** `feat/m6-scale-execution-integration` (extends accepted M6-02 commit `25a9f3e18848fdca7a0f8bbf78b1506d4e98fa89` and provisional M6-03 commit `8515fc8881c8b0001fe0daa2c8e7837706235cc8`)  
> **Physical Hardware Gate:** **PHYSICAL GPU ACCEPTANCE = NOT RUN (OPEN)** — this local development host has `Intel(R) HD Graphics 520` (no NVIDIA driver / no `nvidia-smi`), and GitHub-hosted CI runs on CPU-only `ubuntu-latest` runners. Simulator runs (`NUMBA_ENABLE_CUDASIM=1`) verify kernel API, semantic trace parity, and negative/fallback behavior only and are **never** treated as physical NVIDIA GPU evidence.  
> **Provisional Tolerance Disclaimer:** Numerical parity comparisons use the M6 provisional engineering tolerance `abs_tol = 1e-9`. Formal CAS §17 numerical tolerance acceptance remains **OPEN** pending Product Owner confirmation.

---

## 1. GPU Kernel Architecture & Strict Calculation-Graph Equivalence

### Selected Workload & Kernel
- **Kernel:** `_prospective_reserve_kernel_impl` (`prospective_reserve_discounting_float64_v1`) in `backend/app/core/execution/gpu_backend.py`, compiled via `numba-cuda==0.30.4` (`@cuda.jit`, strict IEEE-754 `float64`).
- **Equations (shared verbatim with CPU `engine.py` and `illustrative_reserve.py`):**
  - `cash_flow_t = monthly_cash_flow * (1 + cash_flow_growth_rate)^((t - 1) / 12)`
  - `discount_factor_t = (1 + discount_rate_annual)^(-t / 12)`
  - `pv_cash_flow_t = cash_flow_t * discount_factor_t`
  - `reserve_H = 0.0`, `reserve_t = (reserve_{t+1} + cash_flow_{t+1}) * (1 + valuation_rate)^(-1/12)` (`illustrative_prospective_reserve`)

### Fail-Closed Compatibility Gate (`inspect_gpu_model_compatibility()`)
Any model variation not actually implemented by `_prospective_reserve_kernel_impl` fails closed (`MODEL_NOT_GPU_SUPPORTED`) at planning time (`ExecutionPlanner.plan()`) and again at worker startup (`GPUExecutionBackend.execute_batches()`):
1. **Product identity:** Must match `PROSPECTIVE_RESERVE_DISCOUNTING`.
2. **Exact formula set & dependencies (`REQUIRED_GPU_FORMULAS`):**
   - Exactly three formula outputs (`cash_flow`, `discount_factor`, `pv_cash_flow`): missing required formulas, duplicate outputs, or extra unsupported formula nodes are rejected.
   - Exact `function_ref` per output variable (`prospective_reserve:cash_flow`, `prospective_reserve:discount_factor`, `prospective_reserve:pv_cash_flow`).
   - Exact dependency set per formula (`cash_flow` -> `{monthly_cash_flow, cash_flow_growth_rate, projection_month}`, `discount_factor` -> `{discount_rate_annual, projection_month}`, `pv_cash_flow` -> `{cash_flow, discount_factor}`).
3. **Immutable code fingerprints:** Verifies registered `FORMULA_REGISTRY` functions and frozen `RunPackage` `implementation.implementation_fingerprint` values match `_approved_gpu_formula_fingerprints()`.
4. **Exact variable sources, defaults, and valuation wiring:**
   - `projection_month` (`context`, field `projection_month`), `monthly_cash_flow` (`input`, column `monthly_cash_flow`), `cash_flow_growth_rate` (`input`, column `cash_flow_growth_rate`, finite default `> -1.0`), `discount_rate_annual` (`manual`, finite rate `> -1.0`), `cash_flow`/`discount_factor`/`pv_cash_flow` (`formula`).
   - Exactly one valuation variable (`reserve`) with `method == "illustrative_prospective_reserve"`, `cash_flow == "cash_flow"`, `rate == "discount_rate_annual"`, `consistency_sum_of in (None, "pv_cash_flow")`.
   - Rejects any cross-policy variable (`portfolio` / `cross_policy`) and any extra `formula` / `valuation` / `prior_output` variable.
5. **Output variables & scenario overrides:**
   - `output_variables` must be a subset of `{"cash_flow", "discount_factor", "pv_cash_flow", "reserve"}`.
   - `scenario_overrides` may only target `"discount_rate_annual"` with supported operations (`set`, `add`, `subtract`, `multiply`, `percent_change`) and finite numeric values.

---

## 2. Product Status & Launch 1.0 Classification

Mentor AMP's authoritative Launch 1.0 actuarial product scope consists of **7 product families**: `SPIA`, `MYGA`, `FIA`, `RILA`, `IDI`, `VA`, `PRT` (4 currently implemented: `SPIA`, `MYGA`, `FIA`, `RILA`).

- **`PROSPECTIVE_RESERVE_DISCOUNTING` is NOT an eighth Launch 1.0 actuarial product.**
- It is explicitly classified as an **illustrative internal GPU numerical reference** (`MODEL_CLASSIFICATION = "internal_gpu_numerical_reference"`, `IS_LAUNCH_PRODUCT = False`) in `backend/app/products/prospective_reserve_discounting/config.py`.
- `register_all_products()` in `backend/app/products/registry.py` keeps `REGISTERED_PRODUCTS == ["SPIA", "MYGA", "FIA", "RILA"]` and exposes `PROSPECTIVE_RESERVE_DISCOUNTING` separately under `REGISTERED_INTERNAL_REFERENCES` (and `GET /v1/system/status -> engine.internal_numerical_references`).

### Backend Support Matrix by Model / Product
| Model / Product | Classification | CPU Reference | PySpark Distributed | CUDA GPU Kernel |
|---|---|---|---|---|
| `SPIA` (`SPIA_LITE`, `SPIA_REFERENCE`) | Launch 1.0 Product | Supported | Supported | Not supported (`MODEL_NOT_GPU_SUPPORTED`) |
| `MYGA` (`MYGA_LITE`) | Launch 1.0 Product | Supported | Supported | Not supported (`MODEL_NOT_GPU_SUPPORTED`) |
| `FIA` (`FIA_LITE`) | Launch 1.0 Product | Supported | Supported | Not supported (`MODEL_NOT_GPU_SUPPORTED`) |
| `RILA` (`RILA_LITE`) | Launch 1.0 Product | Supported | Supported | Not supported (`MODEL_NOT_GPU_SUPPORTED`) |
| `IDI`, `VA`, `PRT` | Launch 1.0 (Phase 7) | Planned | Planned | Not supported |
| `PROSPECTIVE_RESERVE_DISCOUNTING` | Internal GPU Numerical Reference | Supported | Policy-parallel compatible | **Supported (`prospective_reserve_discounting_float64_v1`)** |

### Reusability for Existing Actuarial Products
The backward discounting loop (`reserve_t = (reserve_{t+1} + cash_flow_{t+1}) * (1 + r)^(-1/12)`) is mathematically identical to `app/core/valuation/illustrative_reserve.py` used by `SPIA`, `MYGA`, `FIA`, and `RILA`. In a future milestone, this kernel can be reused as an optional GPU valuation post-stage on already-projected cash-flow matrices without rewriting contract mechanics—provided profiling shows the valuation step is large enough to amortize H2D/D2H PCIe transfer overhead.

---

## 3. Semantic Trace Parity & Timing Telemetry

### Semantic Trace Verification (`tests/gpu/test_m6_gpu_execution.py`)
Beyond matching output values (`abs_tol = 1e-9`) and trace row counts (`772 == 772`), GPU E2E tests compare every trace row semantically against CPU across both Base (`4.5%`) and Stress (`-1.0%` scenario override) runs:
- Exact `(policy_id, projection_month, variable_name)` key set parity.
- Exact `formula_id`, `source_type`, `source_table`, `lookup_keys_json`, and `error_message` parity.
- Recursive semantic parity of `input_values_json` (including `scenario_override` metadata `{scenario, operation, value, base_value}` on scenario-adjusted `discount_rate_annual` rows) and `output_value_json`.
- Node-by-node recursive parity of `GET /v1/runs/{id}/trace` trees (`depth=6`) for `reserve` (months 0 and 1) and `pv_cash_flow` (months 1 and 24).

### Non-Double-Counted CUDA Timing & Mandatory Physical-GPU Acceptance Gate
In `backend/app/core/execution/gpu_backend.py`:
- On physical CUDA hardware (`hardware_verified == True`), kernel execution time (`kernel_execution_seconds`) is measured using **hardware CUDA events** (`cuda.event(timing=True)` + `cuda.event_elapsed_time`), cleanly separated from CPU dispatch (`kernel_launch_seconds`), host sync wait (`kernel_sync_wait_seconds`), `h2d_transfer_seconds`, `d2h_transfer_seconds`, and outer `end_to_end_batch_seconds` without double-counting synchronization. `vram_free_after_bytes` is recorded after device buffer cleanup.
- On simulator (`hardware_verified == False`), `timing_source` is explicitly `"simulator_wall_clock"` with `kernel_launch_seconds = 0.0` and `kernel_sync_wait_seconds = 0.0`.

In `backend/scripts/benchmark_m6_03_gpu.py`:
- `--require-physical-gpu`: **Fails closed with exit code `2` and `acceptance_status = "FAIL"`** whenever no physical NVIDIA GPU is detected or `NUMBA_ENABLE_CUDASIM=1` / `--allow-simulator` is set. Cannot return `PASS` from a skip.
- Optional dev mode (no flags, no GPU): exits `0` with `acceptance_status = "NOT_RUN"`, `physical_gpu_acceptance = "NOT_RUN"`.
- Simulator parity check (`NUMBA_ENABLE_CUDASIM=1 ... --allow-simulator`): measures cold vs warm invocations, compares every policy/month/output point against CPU, and reports `acceptance_status = "SIMULATOR_ONLY_NOT_HARDWARE_ACCEPTED"`, `physical_gpu_acceptance = "NOT_RUN"`.

---

## 4. Spark Operational Hardening & Documented Limitations (Section 7)

Retained in `backend/app/core/execution/spark_backend.py` and `backend/app/config.py`:
- **Single-process concurrent Spark run safety:** `_SPARK_SESSION_LOCK`, `_SPARK_ACTIVE_RUNS`, `_SPARK_ACTIVE_MASTER`, per-wave `sc.setJobGroup(...)` isolation, and `SPARK_MASTER_CONTENTION` rejection for conflicting simultaneous masters.
- **Remote configuration groundwork:** configurable `spark_driver_host`, `spark_driver_bind_address`, `spark_worker_python`, and `sc.addPyFile` zip packaging (`_build_app_zip()`).
- **Driver-memory row-budget safeguards:** `_compute_wave_row_budget()` (`spark_max_wave_result_rows = 250_000`) and `spark.driver.maxResultSize = "1g"`.

**Explicitly Documented Unverified Operational Limitations (OPEN for remaining Phase 6 acceptance):**
- Multi-host remote Spark cluster deployment across separate network machines: **NOT VERIFIED**.
- Multi-process distributed admission / scheduling across separate worker OS processes: **NOT VERIFIED**.
- Production-scale driver/executor memory behavior at enterprise portfolio sizes: **NOT VERIFIED**.

---

## 5. Verification Evidence & Gate Status

### Local Verification Results
| Environment | Suite / Command | Observed Result |
|---|---|---|
| `backend/.venv` (Pure CPU) | `python -m pytest -q tests/unit tests/golden_policy tests/integration` | **801 passed, 4 skipped** |
| `venv-spark` (Spark 4.1.1 + Java 17) | `python -m pytest -q tests/unit/test_m6_spark_backend.py tests/unit/test_m6_gpu_backend.py tests/integration/test_m6_execution_planner_api.py tests/spark` | **45 passed, 0 failed** |
| `venv-gpu-sim` (`NUMBA_ENABLE_CUDASIM=1`) | `NUMBA_ENABLE_CUDASIM=1 python -m pytest -q tests/unit/test_m6_gpu_backend.py tests/unit/test_m6_execution_planner.py tests/integration/test_m6_execution_planner_api.py tests/gpu` | **38 passed, 0 failed** |
| `venv-gpu-sim` (Physical GPU Gate Check) | `python scripts/benchmark_m6_03_gpu.py --require-physical-gpu` | **Exit code `2` (`acceptance_status = FAIL`, `PHYSICAL_GPU_UNAVAILABLE`)** |
| `venv-gpu-sim` (Simulator Parity Benchmark) | `NUMBA_ENABLE_CUDASIM=1 python scripts/benchmark_m6_03_gpu.py --allow-simulator` | `25x120`: `12,025` points, `max_output_abs_diff = 0.0`; `100x360`: `144,100` points, `max_output_abs_diff = 0.0`; `physical_gpu_acceptance = NOT_RUN` |

### Running Physical NVIDIA CUDA Hardware Acceptance (On Authorized GPU Host)
```bash
cd backend
unset NUMBA_ENABLE_CUDASIM
python -m pytest -q tests/unit/test_m6_gpu_backend.py tests/gpu
python scripts/benchmark_m6_03_gpu.py --require-physical-gpu
```

### Gate Status Summary
| Gate | Status | Notes |
|---|---|---|
| GPU Model Compatibility Equivalence Gate | **CLOSED** | Exact formula set, dependencies, code fingerprints, variable sources, valuation refs, outputs, and scenario overrides verified + 11 negative tests |
| Launch 1.0 7-Product Catalog Integrity | **CLOSED** | `PROSPECTIVE_RESERVE_DISCOUNTING` separated into `internal_numerical_references`; `products` remains `["SPIA", "MYGA", "FIA", "RILA"]` |
| Semantic Trace & Trace-Tree Parity (Simulator) | **CLOSED** | All 772 trace rows (incl. `scenario_override` provenance) and recursive `/trace` trees verified against CPU |
| Non-Double-Counted CUDA Timing & `--require-physical-gpu` | **CLOSED** | CUDA hardware events implemented; `--require-physical-gpu` exits `2` (`FAIL`) without physical GPU or under simulator |
| Physical NVIDIA GPU Hardware Execution & Benchmarks | **OPEN (NOT RUN)** | Requires authorized physical NVIDIA GPU VM or GPU runner |
| CAS §17 Formal Numerical Tolerance Sign-Off | **OPEN** | Provisional `1e-9` tolerance used pending Product Owner confirmation |
| Multi-Host / Multi-Process / Production-Scale Spark Ops | **OPEN** | Single-process local concurrency verified; remote/multi-process/prod-scale remain open |
