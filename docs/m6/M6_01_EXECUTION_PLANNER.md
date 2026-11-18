# Mentor AMP — Phase 6 M6-01 Gate Evidence, Architecture Summary & Learning Walkthrough

**Gate:** `M6-01` (Unified Execution Planner)  
**Branch:** `feat/m6-scale-execution-integration`  
**Approved Baseline:** `main` @ `efb9657b084dff980d65326c5c244bfd043bd695` (`M6-00` @ `ee0120a`)  
**Implementation Commits:** `10a2778` (M6-01 planner, capability API, provenance, tests) -> `0cde662` (CI workflow fixes verified green on run `44366687735`)  
**Isolation:** `main`, `MentorAmp-frontend` (`sf_frontend_patch` @ `0d0a4e9`), and `NOAH_PHASE7_IDI_VA_PRT_LIFE_REPORTING_README.md` untouched.

---

## 1. Implemented Architecture & Files

| File | Responsibility Added in `M6-01` |
|---|---|
| `backend/app/core/execution/backends.py` | Defines `ExecutionBackend` protocol, `CpuReferenceBackend` (delegating without duplicating formulas), `ExecutionRequest` / `ExecutionPlanContext` / `ExecutionPlan`, `BackendCapabilityRegistry`, fail-closed `validate_execution_request(...)`, real runtime/hardware probe (`probe_hardware_and_runtimes`, never mistaking `NUMBA_ENABLE_CUDASIM=1` for GPU hardware), and `ExecutionPlanner`. |
| `backend/app/api/execution.py` | Extends `RunSetRequest` with optional `execution_backend`, `fallback_policy`, and `execution_options`; adds authenticated `GET /v1/execution/capabilities`. |
| `backend/app/api/system.py` | Preserves `"execution_backend": "cpu"` and adds `available_execution_backends` + `execution_capabilities` under `"engine"`. |
| `backend/app/services/run_submission_service.py` | Validates execution requests fail-closed (`422` on invalid backend/policy/options), evaluates planner in `preflight` & `submit_run_set` (`409` on strict rejection), freezes identical `RunPackage` inputs untouched, records intent in `RunSet.resolution["execution"]`, queued `RunEvent.data`, and `AuditLog` (`run_set.submitted`). |
| `backend/app/services/run_control_service.py` | Copies queued `execution_request` into retried `Run` queued events so retry lineage preserves backend intent alongside identical `RunPackage` fingerprints. |
| `backend/app/services/run_execution_service.py` | Resolves execution intent, runs worker-time `ExecutionPlanner` checks, emits `execution_planned` / `execution_fallback` events, fails cleanly on worker-time strict rejection, delegates policy loop to `ExecutionBackend` while preserving `run_execution_service.run_policy` test seam, and records rounded numeric metrics + `"execution"` provenance in `RunAttempt.metrics` & `Run.summary`. |
| `backend/app/services/run_manifest_service.py` | Records actual/planned/requested backend, fallback policy/reason, accelerated flag, and hardware context in `RunManifest.manifest["runtime"]["execution"]`. |
| `backend/app/services/run_query_service.py` | Exposes live/historical `execution` provenance and accurate `compute.backend` in `run_view(...)` and `list_attempts(...)`. |
| `backend/tests/unit/test_m6_execution_planner.py` | Unit tests covering `ExecutionBackend` protocol, unknown backend/policy fail-closed checks, invalid option validation, simulator non-GPU guard, fallback/strict rejection, and unsupported model GPU non-claiming. |
| `backend/tests/integration/test_m6_execution_planner_api.py` | End-to-end API & DB tests covering all 12 critical M6-01 requirements including fingerprint invariance, fallback & manifest persistence, legacy `RunPackage` readability, worker-time strict failure + retry lineage, and project/role authorization isolation. |

---

## 2. API Contract Extensions (100% Additive & Backward-Compatible)

1. **`GET /v1/execution/capabilities`** (requires authenticated `projects:read`):
   - Returns `default_backend` (`"cpu"`), `requested_modes`, `fallback_policies`, `execution_options_schema`, per-backend capability objects (`cpu`, `spark`, `gpu`, `spark_gpu`), and host hardware telemetry.
2. **`POST /v1/run-sets/preflight` & `POST /v1/run-sets`** (`RunSetRequest`):
   - Optional fields:
     - `execution_backend`: `"auto" | "cpu" | "spark" | "gpu" | "spark_gpu"` (defaults to `"cpu"` when omitted)
     - `fallback_policy`: `"allow_cpu_fallback" | "strict_fail"` (defaults to `"allow_cpu_fallback"`)
     - `execution_options`: optional dict validating `spark_partitions` (`1..1024`), `spark_master`, `gpu_device_id` (`0..15`), `gpu_precision` (`"float64"`), `prefer_arrow`, `batch_size` (`1..1_000_000`), and cross-backend option conflicts (`422 INVALID_EXECUTION_OPTIONS`).
   - Preflight adds `"execution"`, per-run `"execution_plan"`, and check `"execution_backend"`.
   - Submit returns `"execution"` on `run_set` and per-run objects; strict-rejected unavailable/unimplemented backends fail with structured HTTP `409` (`GPU_HARDWARE_UNAVAILABLE`, `SPARK_RUNTIME_UNAVAILABLE`, `MODEL_NOT_GPU_SUPPORTED`, `BACKEND_NOT_IMPLEMENTED`).
3. **`GET /v1/runs/{run_id}`, `/v1/runs/{run_id}/attempts`, `/v1/runs/{run_id}/manifest`**:
   - Preserves all legacy keys while adding `"execution"` provenance (`requested_backend`, `planned_backend`, `actual_backend`, `fallback_policy`, `fallback_used`, `fallback_reason`, `accelerated`, `hardware`) without altering any `RunPackage` hash.

---

## 3. Verification & Hosted CI Evidence

### 3.1 Local Post-Final-Edit Suite (`backend/.venv`, Python 3.14.2 Windows)
- Targeted planner & WP1 authorization (`test_m6_execution_planner.py`, `test_m6_execution_planner_api.py`, `test_wp1_authorization.py`): **`39 passed`** (`19.52s`).
- Targeted WP1 monkeypatch & run control checks (`task-4540`): **`104 passed`** (`92.22s`).
- Full CPU unit + golden_policy + integration suite (`task-4549`): **`773 passed, 4 skipped`** (`426.41s`).
  - The 4 skips are pre-existing explicit skips: `2` FIA/RILA golden oracle tests pending `ACT-M4-DEC-01..15`, and `2` PostgreSQL opt-in tests covered by the disposable PostgreSQL job below.

### 3.2 Live GitHub Actions CI Verification & Defect Resolution
- **Run `44365315975` (initial push `10a2778`):** `PySpark Local Partition Smoke` passed (`✓`), while two genuine workflow defects were exposed:
  1. Workflow-level `JWT_SECRET_KEY` masked unset-secret checking in `tests/unit/test_wp1_config_safety.py::test_jwt_mode_production_needs_explicit_cors_and_a_real_secret`.
  2. Unpinned `sqlalchemy 2.1.x` switched `postgresql://` driver lookup to `psycopg` v3 instead of `psycopg2` inside `tests/postgres/test_m5_postgres_governance.py`.
- **Run `44366250337` (`b674ae4`):** Removed `JWT_SECRET_KEY` AND accidentally omitted `DEBUG=true`, causing `Settings()` import failure at collection time (`pytest` exit code 4).
- **Run `44366687735` (`0cde662`) — ALL 3 JOBS PASSED (`ubuntu-latest`, Python 3.14):**
  - `CPU Reference Tests & Alembic Single-Head`: **`773 passed, 4 skipped`** (`167.26s`, total job `3m37s`).
  - `Disposable PostgreSQL Governance & Migrations` (`postgres:18-alpine`): **`25 passed`** (`36.80s`, total job `1m30s`).
  - `PySpark Local Partition Smoke (Python 3.14 + Java 17)`: **`parity_status = "PASS"`**, `max_abs_diff_vs_cpu = 0.0` across 2 & 4 partitions (`3.703s` JVM boot, total job `1m8s`).

---

## 4. Remaining Limitations & Open Gates (`M6-02+` Scope)
- **Distributed Spark (`spark`) Execution (`M6-02`)** and **Physical CUDA Kernels (`gpu` / `spark_gpu`) (`M6-03`)** are intentionally marked `implemented=False` in M6-01; no run claims Spark or GPU acceleration until those milestones land.
- **No NVIDIA GPU on Local Laptop:** Hardware GPU testing waits for a CUDA runner/machine in `M6-03`.
- **`CAS §17` Benchmark/Parity Tolerances & `ACT-M4-DEC-01..15`:** Still **OPEN** pending Product Owner / Actuarial confirmation.

---

## 5. Plain-English Learning Walkthrough (M6-01)

- **What `ExecutionBackend` does:** Think of `ExecutionBackend` as a **standard wall plug** in Mentor AMP. The platform plugs a run into it, passes the loaded policies and formulas, and gets back the exact same output structure. Right now in M6-01 only the **CPU plug** (`CpuReferenceBackend`) is live — later, Spark (`M6-02`) and GPU (`M6-03`) will plug into that exact same socket without rewriting business rules.
- **How `ExecutionPlanner` chooses a method:** Before running anything, the planner acts like an **air-traffic controller**. It checks three real facts: *(1)* did the user ask for a valid backend (`auto`, `cpu`, `spark`, `gpu`, `spark_gpu`) and valid options?, *(2)* is that engine actually built and present on this machine (it checks real CUDA hardware and refuses to treat a simulator as a GPU)?, and *(3)* does that engine actually support this specific actuarial product?
- **What Fallback means:** Suppose you ask for `"gpu"` on a laptop that has no NVIDIA GPU:
  - Under **`allow_cpu_fallback`** (default), the planner says: *"GPU isn't available here, so I will run it safely on CPU instead — and I will record a warning saying `fallback_used=True` and `fallback_reason=GPU_HARDWARE_UNAVAILABLE` so nobody thinks a GPU ran it."*
  - Under **`strict_fail`**, the planner says: *"You demanded real GPU execution with no fallback, and no GPU is ready — I am rejecting/failing this run cleanly instead of pretending."*
- **Why mathematical fingerprints stay backend-neutral:** Your `RunPackage` fingerprint (`run_package_fingerprint`) is the mathematical barcode of *what* you are calculating (policies, mortality tables, formulas, scenario). Whether you calculate `2 + 2` on a pocket calculator (CPU) or a supercomputer (Spark/GPU), **the question didn't change**, so the `RunPackage` fingerprint stays identical!
- **How `RunManifest` proves what actually ran:** While `RunPackage` locks the *inputs*, the final `RunManifest` (and `RunAttempt`) locks the **audit receipt** after the run finishes: recording `requested_backend`, `planned_backend`, `actual_backend`, `fallback_used`, `fallback_reason`, `accelerated`, and exact hardware facts — so an auditor always knows both *what was asked for* and *what actually computed the numbers*.
