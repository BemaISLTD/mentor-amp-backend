# M6-05 Frontend Integration Handoff — Phase 6 Scale Execution & Full Governed Workflow (Sam Yung)

**From:** Samuel Ayinla (Backend / Technical Lead)  
**To:** Sam Yung (Frontend Lead — `MentorAmp-frontend`)  
**Backend Branch:** `feat/m6-scale-execution-integration`  
**Supersedes / Extends:** `SAM_YUNG_FRONTEND_HANDOFF_COMPLETE.md` (adds all Phase 6 M6-01..M6-04 execution-backend APIs and live E2E demonstration checklist; backend repo only — no changes made to `MentorAmp-frontend`)

---

## 1. Clean Local Backend Setup, Seeding & Startup (Port `8001`)

> **Important Working-Directory & Safety Rule:** All commands below run from the **`backend/`** directory against a **disposable local SQLite database** (`APP_ENV=local`). Never run seeders or password-setup snippets against any shared development, staging, or production database.

### Step 1 — Enter `backend/` and Configure a Disposable Local Environment

**Windows PowerShell** (starting from repository root):
```powershell
cd backend

$env:APP_ENV = "local"
$env:DEBUG = "true"
$env:AUTH_MODE = "jwt"
$env:DATABASE_URL = "sqlite:///./mentoramp_local_m605.db"
$env:ARTIFACT_STORAGE_PATH = ".\.local_m605_artifacts"
```

**macOS / Linux Bash** (starting from repository root):
```bash
cd backend

export APP_ENV="local"
export DEBUG="true"
export AUTH_MODE="jwt"
export DATABASE_URL="sqlite:///./mentoramp_local_m605.db"
export ARTIFACT_STORAGE_PATH="./.local_m605_artifacts"
```

*(If you have not created `backend/.venv` yet: `python -m venv .venv` and install `requirements.txt`. To demonstrate **real Spark** runs in Step 5, install `pyspark>=3.5` in a dedicated Spark venv and ensure Java 17 is on `JAVA_HOME`; see Step 5 below.)*

### Step 2 — Initialize the Database Schema

- **If using disposable local SQLite** (`DATABASE_URL=sqlite:///...` as in Step 1 above), initialize tables via SQLAlchemy ORM metadata (Alembic migrations include PostgreSQL-specific `ARRAY` DDL and target PostgreSQL):

```powershell
# Windows PowerShell (from backend/)
.\.venv\Scripts\python.exe -c "import app.db.models; from app.db.database import Base, engine; Base.metadata.create_all(bind=engine); print('SQLite schema initialized.')"

# macOS / Linux Bash (from backend/)
./.venv/bin/python -c "import app.db.models; from app.db.database import Base, engine; Base.metadata.create_all(bind=engine); print('SQLite schema initialized.')"
```

- **If using a disposable local PostgreSQL database** (`DATABASE_URL=postgresql://...`), run Alembic migrations instead:

```powershell
# Windows PowerShell (from backend/)
.\.venv\Scripts\alembic.exe upgrade head

# macOS / Linux Bash (from backend/)
./.venv/bin/alembic upgrade head
```

### Step 3 — Seed SPIA, 5-Year MYGA, FIA, and RILA Projects

From `backend/`:
```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe scripts\seed_demo.py
.\.venv\Scripts\python.exe scripts\seed_indexed.py

# macOS / Linux Bash
./.venv/bin/python scripts/seed_demo.py
./.venv/bin/python scripts/seed_indexed.py
```

### Step 4 — Set Local-Only Login Passwords (Disposable Local DB Only)

`scripts/seed_demo.py` generates random passwords for `demo-admin@mentoramp.local` (Admin/Author) and `demo-reviewer@mentoramp.local` (independent four-eyes Reviewer). From `backend/`, set temporary local passwords so you can sign in through `MentorAmp-frontend`:

**Windows PowerShell** (from `backend/`):
```powershell
$env:LOCAL_DEMO_ADMIN_PW = "<choose-local-admin-password>"
$env:LOCAL_DEMO_REVIEWER_PW = "<choose-local-reviewer-password>"
.\.venv\Scripts\python.exe -c "import os; from app.config import settings; from app.db.database import SessionLocal; from app.db.models.user import User; from app.core.security import hash_password; assert settings.app_env in {'local', 'test'}, f'Refusing reset in APP_ENV={settings.app_env}'; db = SessionLocal(); [setattr(u, 'password_hash', hash_password(pw)) for email, pw in (('demo-admin@mentoramp.local', os.environ['LOCAL_DEMO_ADMIN_PW']), ('demo-reviewer@mentoramp.local', os.environ['LOCAL_DEMO_REVIEWER_PW'])) if (u := db.query(User).filter(User.email == email).first())]; db.commit(); db.close(); print('Updated local passwords for demo-admin and demo-reviewer.')"
```

**macOS / Linux Bash** (from `backend/`):
```bash
export LOCAL_DEMO_ADMIN_PW="<choose-local-admin-password>"
export LOCAL_DEMO_REVIEWER_PW="<choose-local-reviewer-password>"
./.venv/bin/python -c "import os; from app.config import settings; from app.db.database import SessionLocal; from app.db.models.user import User; from app.core.security import hash_password; assert settings.app_env in {'local', 'test'}, f'Refusing reset in APP_ENV={settings.app_env}'; db = SessionLocal(); [setattr(u, 'password_hash', hash_password(pw)) for email, pw in (('demo-admin@mentoramp.local', os.environ['LOCAL_DEMO_ADMIN_PW']), ('demo-reviewer@mentoramp.local', os.environ['LOCAL_DEMO_REVIEWER_PW'])) if (u := db.query(User).filter(User.email == email).first())]; db.commit(); db.close(); print('Updated local passwords for demo-admin and demo-reviewer.')"
```

### Step 5 — Start the Backend API on Port `8001`

**Option A — Standard CPU virtual environment** (fast UI dev; `GET /v1/execution/capabilities` will report `cpu: available`, `spark: unavailable` unless `pyspark` + Java 17 are installed, `gpu: unavailable` without NVIDIA hardware):
```powershell
# Windows PowerShell (from backend/)
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8001 --reload

# macOS / Linux Bash (from backend/)
./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

**Option B — Real Spark-enabled virtual environment** (required for live Spark runs in M6-05; ensure Java 17 is installed and `JAVA_HOME` is set, and point `<spark-venv>` at your venv with `requirements.txt` + `pyspark>=3.5`):
```powershell
# Windows PowerShell (from backend/)
$env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-17.0.18.8-hotspot"
$env:PATH = "$env:JAVA_HOME\bin;$env:PATH"
<spark-venv>\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8001
```

Sample CSVs for live Data Manager uploads live at `../samples/spia/` and `../samples/myga/` (relative to `backend/`, or `samples/spia/` and `samples/myga/` from repository root).

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

## 5. Required M6-05 Live Demonstration Checklist (No Mocked Responses)

All 15 items below must be demonstrated end-to-end from `MentorAmp-frontend` against the live backend (`feat/m6-scale-execution-integration`) — **no mocked frontend API responses**:

1. **Authentication & Project Access:** Sign in via `/v1/auth/token` as **Admin** (`demo-admin@mentoramp.local`) and verify project access; sign in separately as **Reviewer** (`demo-reviewer@mentoramp.local`) for four-eyes approval.
2. **Data Manager CSV Import:** Create an import session and upload a real sample CSV (`samples/spia/` or `samples/myga/`).
3. **Mapping & Validation:** Map CSV columns to canonical product fields, run `/validate`, and inspect validation status/issues.
4. **Commit & Four-Eyes Approval:** Commit the validated dataset as **Admin**, then approve it independently as **Reviewer** (`POST /v1/datasets/{kind}/{dataset_id}/approve`).
5. **Model & Projection Set Configuration:** Configure and validate a Projection Set binding the approved dataset, model version, scenarios, horizon, published outputs, and trace scope.
6. **Execution Capability Discovery:** Verify the UI queries `GET /v1/execution/capabilities` (and `GET /v1/system/status`) and renders real backend availability accurately (`cpu`, `spark`, `gpu`, `spark_gpu`) while keeping `PROSPECTIVE_RESERVE_DISCOUNTING` out of the commercial product catalog.
7. **Real CPU Backend Execution:** Submit a run explicitly targeting `execution_backend: "cpu"` and verify `actual_backend: "cpu"` on completion.
8. **Real Spark Backend Execution:** Against the Spark-enabled backend (Step 5 Option B), submit a run targeting `execution_backend: "spark"` and verify real Spark provenance (`actual_backend: "spark"`, `spark_master`, `partitions_used`, `waves_completed`, `worker_os_pids`).
9. **Unavailable GPU State & Fallback Warning:** Verify unavailable `gpu` renders cleanly with its backend `unavailable_reason` (`GPU_HARDWARE_UNAVAILABLE`), and submitting `gpu` + `allow_cpu_fallback` displays the preflight warning and records `requested_backend: "gpu"` -> `actual_backend: "cpu"` (`fallback_used: true`).
10. **Strict Rejection:** Submit `gpu` + `strict_fail` and verify preflight reports `ok: false` and submission fails closed (`409 Conflict`) with the structured backend error code surfaced cleanly in the UI.
11. **Run Submission, Status & Progress:** Observe `202 Accepted` submission, live run status transitions, policy `progress` (`done`/`total`), and `/events` log updates.
12. **Cancel & Retry Flows:** Demonstrate cancelling an active run (`POST /v1/runs/{id}/cancel` -> `cancelled`, `results_available: false`) and retrying (`POST /v1/runs/{id}/retry` -> clean child retry run preserving lineage without duplicate artifacts).
13. **Results & Immutable Run Manifest:** Inspect governed run outputs (`/summary`, `/results`, `/aggregates`) and immutable provenance (`/manifest` and `/package`).
14. **Policy Trace Tree:** Open the calculation trace view (`/trace/policies`, `/trace`, `/trace/dependents`) and inspect the dependency tree for a traced policy/month/variable.
15. **Reconciliation & Governed Export:** Run reconciliation against an expected result set (`/reconcile`, `/reconciliations`, reviewer `/disposition`), download `/export.csv` preserving governance headers (`X-MentorAmp-Run-Id`, `X-MentorAmp-Illustrative`, `X-MentorAmp-Complete`), and capture the reproducible evidence package below.

---

## 6. Joint M6-05 Evidence Package (For Independent Review)

To close M6-05 cleanly, capture and bundle:
- **Exact commit SHAs:** Frontend (`MentorAmp-frontend`) commit SHA + Backend (`feat/m6-scale-execution-integration`) commit SHA.
- **Live API captures:** Raw request/response evidence for capability discovery, preflight, strict GPU rejection, GPU->CPU fallback, CPU run, Spark run, cancel/retry, manifest, trace, reconciliation, and export headers.
- **UI evidence:** Annotated screenshots or screen recording showing each of the 15 checklist steps against live backend responses.
- **Execution provenance:** Confirmed CPU and Spark run manifests/attempts showing `requested_backend`, `planned_backend`, `actual_backend`, `fallback_used`, and Spark partition/worker metadata.
- **Open Phase 6 gates (kept explicitly open until verified):** Physical NVIDIA GPU hardware verification (`NOT_RUN`), formal CAS §17 tolerance/performance approval (`OPEN`), and production-scale multi-host Spark deployment (`NOT VERIFIED`).

Ping Samuel Ayinla immediately if you hit any backend CORS origin, payload shape, seed fixture, or Spark local environment question during live M6-05 integration.
