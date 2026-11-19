# M6-03: Genuine CUDA GPU Execution Backend & Section 7 Spark Operational Hardening

> **Branch:** `feat/m6-scale-execution-integration` (extends accepted M6-02 commit `25a9f3e18848fdca7a0f8bbf78b1506d4e98fa89`)  
> **Hardware Status:** **PHYSICAL NVIDIA GPU HARDWARE VERIFICATION: NOT RUN** (local host has `Intel(R) HD Graphics 520` and no `nvidia-smi`; GitHub-hosted CI runs on CPU-only `ubuntu-latest` runners). All GPU kernel logic and API/provenance contracts below are verified via unit tests and explicit non-hardware `NUMBA_ENABLE_CUDASIM=1` execution in `venv-gpu-sim` and CI.  
> **Provisional Tolerance Disclaimer:** Numerical parity comparisons use the M6 provisional engineering tolerance `abs_tol = 1e-9`. Formal CAS §17 numerical tolerance acceptance remains open pending Product Owner confirmation.

---

## 1. Architecture Decision & Selected GPU Workload

### Workload Selection
- **Product:** `PROSPECTIVE_RESERVE_DISCOUNTING` (`backend/app/products/prospective_reserve_discounting/`)
- **Equations (shared verbatim with CPU reference `engine.py` and `illustrative_reserve.py`):**
  - `cash_flow_t = monthly_cash_flow * (1 + cash_flow_growth_rate)^((t - 1) / 12)`
  - `discount_factor_t = (1 + discount_rate_annual)^(-t / 12)`
  - `pv_cash_flow_t = cash_flow_t * discount_factor_t`
  - `reserve_H = 0.0`, `reserve_t = (reserve_{t+1} + cash_flow_{t+1}) * (1 + valuation_rate)^(-1/12)` (`illustrative_prospective_reserve`)
- **Why this workload:** Purely dense IEEE-754 `float64` per-policy computation with deterministic memory stride, zero cross-policy coupling, and zero lookup-table branching.
- **No silent approximations or model misrouting:** `inspect_gpu_model_compatibility()` verifies both product code (`PROSPECTIVE_RESERVE_DISCOUNTING`) and every formula/variable/valuation definition. Other products (`SPIA_LITE`, `MYGA_LITE`, `FIA_LITE`, `RILA_LITE`) or unsupported formula/valuation refs never silently claim GPU execution (`MODEL_NOT_GPU_SUPPORTED`).

### CUDA Stack
- **Compiler & Runtime:** `numba==0.68.1`, `numba-cuda==0.30.4`, `nvidia-cuda-runtime==13.5.37` (`@cuda.jit`, strict `float64`).
- **Kernel:** `_prospective_reserve_kernel_impl` compiled via `_compile_or_get_cuda_kernel()` in `backend/app/core/execution/gpu_backend.py` (`prospective_reserve_discounting_float64_v1`).
- **Hardware vs Simulator Boundary:**
  - `probe_cuda_hardware()` in `gpu_backend.py` ALWAYS sets `cuda_gpu_detected = False` whenever `NUMBA_ENABLE_CUDASIM=1` is present or no CUDA device is accessible.
  - Production `GPUExecutionBackend(allow_simulator=False)` refuses simulator execution (`GPU_HARDWARE_UNAVAILABLE`).
  - Only explicit isolated dev/CI simulator tests pass `allow_simulator=True`, and record `hardware_verified = False`, `execution_mode = "cuda_simulator"`, and `simulator_non_hardware = True` in `execution_provenance["gpu"]`.

---

## 2. Backend Capability, Compatibility, and Lifecycle Integration

### Key Files Updated / Created
- `backend/app/config.py`: GPU settings (`gpu_enabled`, `gpu_default_precision="float64"`, `gpu_min_compute_capability_major=5`, `gpu_min_compute_capability_minor=0`, `gpu_memory_utilization_limit=0.80`, `gpu_max_batch_bytes=512*1024*1024`, `gpu_threads_per_block=128`) and Section 7 Spark settings (`spark_driver_host`, `spark_driver_bind_address`, `spark_worker_python`, `spark_ship_app_zip`, `spark_max_wave_result_rows=250_000`, `spark_driver_max_result_size="1g"`).
- `backend/app/products/prospective_reserve_discounting/` (`__init__.py`, `config.py`) + `backend/app/products/registry.py`: Shared formula/inforce/valuation definitions registered cleanly alongside `SPIA`, `MYGA`, `FIA`, and `RILA`.
- `backend/app/core/execution/gpu_backend.py`:
  - `probe_cuda_hardware()` (device names, compute capability, free/total VRAM, driver/runtime versions)
  - `inspect_gpu_model_compatibility()` (strict product, function ref, cross-policy, and valuation method validation)
  - `GPUExecutionBackend.execute_batches()` / `execute_policies()`:
    - Pre-execution checks: `GPU_DISABLED`, `UNSUPPORTED_GPU_PRECISION`, `GPU_HARDWARE_UNAVAILABLE`, `GPU_DEVICE_INVALID`, `GPU_COMPUTE_CAPABILITY_UNSUPPORTED`, `MODEL_NOT_GPU_SUPPORTED`, `GPU_INSUFFICIENT_MEMORY`
    - Bounded per-batch VRAM allocation capped by `min(gpu_max_batch_bytes, free_vram * gpu_memory_utilization_limit)`
    - Deterministic `finally` device array release
    - Per-policy input error isolation (missing cash flow, non-finite inputs, `<= -100%` growth rate) matching CPU `EngineError` semantics and producing `partial_success` cleanly
    - Full `m2-v1` Parquet output rows and trace rows (including leaf input/context/manual resolution traces and valuation traces)
- `backend/app/core/execution/backends.py`: `BACKEND_GPU` marked `implemented=True` (`M6-03`), hardware discovery delegated to `probe_cuda_hardware()`, `get_backend()` constructs `GPUExecutionBackend`, `ExecutionPlanner.plan()` runs `inspect_gpu_model_compatibility()`.
- `backend/app/services/run_execution_service.py`:
  - Shared `_make_cancel_checker()` used by Spark and GPU batches.
  - Pre-Batch-0 `GPUSessionStartupError` honors `allow_cpu_fallback` (logs `execution_fallback` warning event and updates provenance `actual_backend="cpu"`, `fallback_used=True`) or fails closed under `strict_fail`.
  - Mid-execution `GPUKernelExecutionError` **NEVER** silently falls back to CPU (`execution_error_code` recorded on attempt and run fails cleanly).
  - Cancellation between batches flushes retained partial artifacts (`results_available=False`, `status="cancelled"`), and `/runs/{id}/retry` runs cleanly without duplicate outputs.

---

## 3. Section 7: Phase 6 Spark Operational Hardening

Implemented in `backend/app/core/execution/spark_backend.py` and `backend/app/config.py`:

1. **7A — Concurrent Spark Run Safety:**
   - Guarded `SparkSession` acquisition and teardown with `_SPARK_SESSION_LOCK`, `_SPARK_ACTIVE_RUNS`, and `_SPARK_ACTIVE_MASTER`.
   - Simultaneous runs targeting the same master share the singleton JVM `SparkContext` without calling `spark.stop()` out from under an active peer (`spark.stop()` runs only when `_SPARK_ACTIVE_RUNS` decrements to `0`).
   - Re-binds thread-local `sc.setJobGroup(job_group, ..., interruptOnCancel=True)` per wave so cancelling one run via `cancel_spark_run(run_id)` isolates cancellation to its own job group without disrupting healthy peer runs.
   - Fails fast with `SPARK_MASTER_CONTENTION` (`SparkSessionStartupError`) if a run requests a different Spark master while another master is actively occupied (`_SPARK_ACTIVE_RUNS > 0`).
2. **7B — Remote Multi-Machine Readiness (Config & Code Shipping):**
   - `_is_loopback_master()` + `_resolve_spark_runtime_config()` remove hardcoded `127.0.0.1` `spark.driver.host` / `spark.driver.bindAddress` assumptions for remote cluster masters, honor `settings.spark_worker_python`, and ship `backend/app/*.py` once per `applicationId` via `_build_app_zip()` + `sc.addPyFile(...)` when `spark_ship_app_zip=True` or using a non-loopback cluster master.
   - *Honesty boundary:* Multi-host physical cluster execution across separate network machines remains **NOT VERIFIED** locally (single-host standalone/local verification only).
3. **7C — Driver-Memory Wave Scalability:**
   - `_compute_wave_row_budget()` estimates output + trace rows per policy (`horizon_months * (len(output_variables) + (15 if traced else 0))`), caps `effective_batch_size` within `settings.spark_max_wave_result_rows`, sets `spark.driver.maxResultSize = settings.spark_driver_max_result_size`, and records wave row-budget telemetry in `execution_provenance["spark"]`.

---

## 4. Verification & Benchmark Evidence

### Test Suites Passed Locally
| Environment | Command | Observed Result | Notes |
|---|---|---|---|
| `backend/.venv` (Pure CPU) | `python -m pytest -q tests/unit tests/golden_policy tests/integration` | **799 passed, 4 skipped** | Includes M1–M5 CPU/golden tests, M6 planner/API tests, `test_m6_gpu_backend.py`, and updated `register_all_products()` assertions |
| `venv-spark` (Spark 4.1.1 + Java 17) | `python -m pytest -q tests/unit/test_m6_spark_backend.py tests/unit/test_m6_gpu_backend.py tests/integration/test_m6_execution_planner_api.py tests/spark` | **43 passed, 0 failed** | Includes `tests/spark/test_m6_spark_concurrency.py` verifying simultaneous 2-thread shared-context Spark execution (`_SPARK_ACTIVE_RUNS >= 2`) and isolated cancellation |
| `venv-gpu-sim` (`NUMBA_ENABLE_CUDASIM=1`) | `NUMBA_ENABLE_CUDASIM=1 python -m pytest -q tests/unit/test_m6_gpu_backend.py tests/unit/test_m6_execution_planner.py tests/integration/test_m6_execution_planner_api.py tests/gpu` | **36 passed, 0 failed** | Simulator non-hardware parity (`abs_tol = 1e-9`), 1m & 480m horizons, trace parity (`772 == 772`), reconciliation `PASS`, error isolation (`partial_success`), cancellation & retry without duplicates |

### CUDA Simulator Benchmark Check (`NUMBA_ENABLE_CUDASIM=1`, **Non-Hardware**)
Ran `NUMBA_ENABLE_CUDASIM=1 python scripts/benchmark_m6_03_gpu.py --allow-simulator`:
- `mode`: `"cuda_simulator_non_hardware"`, `hardware_verified`: `false`, `cuda_gpu_detected`: `false`
- `25 policies x 120 months` (`3,000` policy-months): `cpu_seconds = 0.2734`, `max_reserve_0_abs_diff = 0.0`, `max_consistency_rel_err = 3.47e-15`, `peak_estimated_device_bytes = 97,000`
- `100 policies x 360 months` (`36,000` policy-months): `cpu_seconds = 3.7231`, `max_reserve_0_abs_diff = 0.0`, `max_consistency_rel_err = 8.92e-15`, `peak_estimated_device_bytes = 1,156,000`
- *(Simulator timing reflects CPU Python thread emulation only and MUST NOT be interpreted as physical GPU speedup.)*

### Running Physical NVIDIA CUDA Hardware Acceptance (On Authorized GPU Host)
On a Linux/Windows host with an NVIDIA GPU (compute capability >= 5.0), NVIDIA driver, and CUDA Toolkit (`numba-cuda` installed without `NUMBA_ENABLE_CUDASIM`):
```bash
cd backend
unset NUMBA_ENABLE_CUDASIM
python -m pytest -q tests/unit/test_m6_gpu_backend.py tests/gpu
python scripts/benchmark_m6_03_gpu.py
```
Verify in the output that `cuda_gpu_detected` is `true`, `hardware_verified` is `true`, `device_name` matches the physical NVIDIA GPU, and both PTX kernel timings and H2D/D2H memory transfer metrics are recorded cleanly.
