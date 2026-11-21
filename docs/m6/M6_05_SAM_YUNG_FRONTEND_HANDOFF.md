# M6-05 Frontend Integration Handoff — Phase 6 Scale Execution & Full Governed Workflow (Sam Yung)

**From:** Samuel Ayinla (Backend / Technical Lead)  
**To:** Sam Yung (Frontend Lead — `MentorAmp-frontend`)  
**Backend Branch:** `feat/m6-scale-execution-integration`  
**Supersedes / Extends:** `SAM_YUNG_FRONTEND_HANDOFF_COMPLETE.md` (adds all Phase 6 M6-01..M6-04 execution-backend APIs and live E2E demonstration checklist; backend repo only — no changes made to `MentorAmp-frontend`)

---

## 1. Local Backend Startup & Test Data (Port `8001`)

From `backend/` (pure CPU `.venv` for fast UI dev, or `venv-spark` if testing live Spark runs):
```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```
Seed SPIA, 5-Year MYGA, FIA, and RILA projects (use disposable local DB only, `APP_ENV=local`):
```powershell
backend\.venv\Scripts\python.exe backend\scripts\seed_demo.py
backend\.venv\Scripts\python.exe backend\scripts\seed_indexed.py
```
Sample CSVs for live Data Manager uploads live under `samples/spia/` and `samples/myga/`.

---

## 2. Phase 6 Execution Capabilities & Backend Selection APIs

### A. Discover Available Backends & Hardware Status
- **`GET /v1/execution/capabilities`** (requires `projects:read`)
- **`GET /v1/system/status`** (also returns `engine.products`, `engine.internal_numerical_references`, `engine.available_execution_backends`, and `engine.execution_capabilities`)

Example `GET /v1/execution/capabilities` response on a CPU/Spark host without an NVIDIA GPU:
```json
{
  "default_backend": "cpu",
  "requested_modes": ["auto", "cpu", "gpu", "spark", "spark_gpu"],
  "fallback_policies": ["allow_cpu_fallback", "strict_fail"],
  "execution_options_schema": {
    "spark_partitions": {"type": "integer", "min": 1, "max": 1024},
    "spark_master": {"type": "string"},
    "gpu_device_id": {"type": "integer", "min": 0, "max": 15},
    "gpu_precision": {"type": "string", "enum": ["float64"]},
    "prefer_arrow": {"type": "boolean"},
    "batch_size": {"type": "integer", "min": 1, "max": 1000000}
  },
  "backends": {
    "cpu": {"name": "cpu", "available": true, "implemented": true, "environment_available": true, "accelerated": false, "supports_all_products": true},
    "spark": {"name": "spark", "available": true, "implemented": true, "environment_available": true, "accelerated": true, "supports_all_products": true},
    "gpu": {
      "name": "gpu",
      "available": false,
      "implemented": true,
      "environment_available": false,
      "accelerated": true,
      "supports_all_products": false,
      "supported_products": ["PROSPECTIVE_RESERVE_DISCOUNTING"],
      "unavailable_reason": "GPU_HARDWARE_UNAVAILABLE",
      "runtime_details": {
        "cuda_gpu_detected": false,
        "cuda_simulator_env": false,
        "simulator_never_counted_as_hardware": true
      }
    },
    "spark_gpu": {"name": "spark_gpu", "available": false, "implemented": false, "unavailable_reason": "GPU_HARDWARE_UNAVAILABLE"}
  }
}
```

### B. Product Catalog Rule (Important UI Distinction)
- `GET /v1/system/status -> engine.products` returns **only** the implemented Launch 1.0 actuarial products: `["SPIA", "MYGA", "FIA", "RILA"]`.
- `engine.internal_numerical_references` returns `["PROSPECTIVE_RESERVE_DISCOUNTING"]`. Do **not** list it as an 8th commercial insurance product in the Product Catalog.

### C. Unavailable GPU UI States (Required UX Rules)
1. Never show GPU as "Ready" or "Available" unless `backends.gpu.available === true`.
2. When `backends.gpu.available === false`, display a clear badge/tooltip explaining `unavailable_reason` (e.g., `GPU_HARDWARE_UNAVAILABLE`: *"No physical NVIDIA GPU detected on this server"*).
3. When a user selects `SPIA`, `MYGA`, `FIA`, or `RILA` and picks `execution_backend: "gpu"`:
   - With `fallback_policy: "allow_cpu_fallback"` -> Preflight shows a clear warning that execution will fall back to `cpu` (`fallback_reason: "GPU_HARDWARE_UNAVAILABLE"` or `"MODEL_NOT_GPU_SUPPORTED"`).
   - With `fallback_policy: "strict_fail"` -> Preflight returns `ok: false` and submit returns `409 Conflict` with structured `error.code`.

---

## 3. Preflight, Submission, Fallback & Run Monitoring Contracts

### A. Preflight & Submit Run Set
- **`POST /v1/run-sets/preflight`** (`runs:execute`)
- **`POST /v1/run-sets`** -> `202 Accepted` (`runs:execute`)

Request body (all 3 Phase 6 fields are optional; defaults remain `cpu` + `allow_cpu_fallback`):
```json
{
  "project_id": "<project_id>",
  "name": "Q4 Valuation Run",
  "projection_set_ids": ["<projection_set_id>"],
  "scenario_ids": ["<scenario_id>"],
  "execution_backend": "spark",
  "fallback_policy": "allow_cpu_fallback",
  "execution_options": {
    "spark_partitions": 4,
    "batch_size": 100
  }
}
```

### B. Requested vs Actual Execution Provenance & Status
- **`GET /v1/runs/{run_id}`** -> Check `status`, `progress` (`done`/`total`), `results_available`, `accepted_attempt_number`, and `execution`:
  - `execution.requested_backend` (what user asked for: `cpu` / `spark` / `gpu` / `auto`)
  - `execution.planned_backend` (what planner chose)
  - `execution.actual_backend` (what actually computed the results)
  - `execution.fallback_used` (`true`/`false`), `execution.fallback_reason`, `execution.fallback_message`
  - `execution.spark` (when Spark ran: `spark_master`, `partitions_used`, `waves_completed`, `worker_os_pids`, `effective_batch_size`)
  - `execution.gpu` (when GPU ran: `kernel_name`, `hardware_verified`, `device_name`, `h2d_transfer_seconds`, `kernel_execution_seconds`, `d2h_transfer_seconds`)
- **`GET /v1/runs/{run_id}/events`** -> Live execution log including `execution_planned`, `execution_fallback`, policy progress, errors, and finalization.
- **`GET /v1/runs/{run_id}/attempts`** -> Attempt history and metrics.
- **`POST /v1/runs/{run_id}/cancel`** -> `202 Accepted` (stops active CPU/Spark/GPU run between waves/batches; sets `results_available: false`).
- **`POST /v1/runs/{run_id}/retry`** -> `202 Accepted` (creates a clean child retry run preserving lineage and preventing duplicate artifacts).

---

## 4. End-to-End Governed Workflow Endpoints (For Live Demo)

| Step | Endpoint(s) | Purpose |
|---|---|---|
| **1. Import** | `POST /v1/import-sessions`, `POST /v1/import-sessions/{id}/files`, `GET /v1/import-sessions/{id}/preview` | Upload raw CSV (inforce, assumption, factor, scenario) |
| **2. Map** | `PUT /v1/import-sessions/{id}/mapping`, `GET/POST /v1/mapping-profiles` | Map CSV columns to canonical product fields |
| **3. Validate & Commit** | `POST /v1/import-sessions/{id}/validate`, `GET /v1/validation-runs/{id}/issues`, `POST /v1/import-sessions/{id}/commit` | Run schema/business checks and commit immutable dataset |
| **4. Four-Eyes Approve** | `POST /v1/datasets/{kind}/{dataset_id}/approve` (as Reviewer user) | Independent reviewer approval (`inforce` / `assumption` / `factor` / `scenario`) |
| **5. Configure** | `POST /v1/projects/{project_id}/projection-sets`, `POST /v1/projection-sets/{id}/validate` | Bind model version, datasets, scenarios, horizon, outputs, trace scope |
| **6. Run** | `GET /v1/execution/capabilities`, `POST /v1/run-sets/preflight`, `POST /v1/run-sets`, `GET /v1/runs/{id}` | Select CPU/Spark/GPU + fallback policy, monitor progress, cancel/retry |
| **7. Results & Manifest** | `GET /v1/runs/{id}/summary`, `GET /v1/runs/{id}/results`, `GET /v1/runs/{id}/aggregates`, `GET /v1/runs/{id}/manifest`, `GET /v1/runs/{id}/package` | Inspect governed outputs (note: `partial_success` returns `409 RESULTS_INCOMPLETE` unless `?include_partial=true`) |
| **8. Trace** | `GET /v1/runs/{id}/trace/policies`, `GET /v1/runs/{id}/trace?policy_id=...&month=...&variable=...&depth=6`, `GET /v1/runs/{id}/trace/dependents` | Inspect full calculation dependency tree |
| **9. Reconcile** | `POST /v1/projects/{project_id}/expected-results`, `POST /v1/runs/{id}/reconcile`, `GET /v1/runs/{id}/reconciliations`, `POST /v1/reconciliations/{id}/disposition` | Compare run outputs against expected benchmark set & record reviewer disposition |
| **10. Export** | `GET /v1/runs/{id}/export.csv?group_by=projection_year` | Download CSV with governance headers (`X-MentorAmp-Run-Id`, `X-MentorAmp-Illustrative`, `X-MentorAmp-Complete`) |

---

## 5. Requested Live Demo Checklist for Sam Yung (M6-05 Acceptance)

Please demonstrate the following real flow end-to-end against the live backend (no mocked frontend data):
1. Sign in as **Admin**, open Data Manager, **import → map → validate → commit** a sample SPIA or MYGA CSV, then sign in as **Reviewer** to **approve** it.
2. Configure and validate a **Projection Set** using the approved dataset.
3. Open Run Submission, verify `GET /v1/execution/capabilities` renders `cpu`, `spark`, and unavailable `gpu` state cleanly; demonstrate **strict rejection** (`gpu` + `strict_fail`) AND **CPU fallback** (`gpu` + `allow_cpu_fallback` showing `requested_backend: "gpu"` -> `actual_backend: "cpu"`).
4. Submit a real **CPU** or **Spark** run, watch live progress/events, and show **cancel + retry** (or retry on a failed run).
5. Open **Results**, **Run Manifest**, **Trace Tree** (`/trace`), run **Reconciliation** against expected results, and download **Export CSV** (`/export.csv`).

Ping Samuel Ayinla if you need any backend CORS origin, seed fixture, or sample CSV adjusted during live integration!
