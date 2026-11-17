# Mentor AMP — Complete Frontend Integration Handoff (Sam Yung)

**Target Frontend Repo:** `MentorAmp-frontend` (branch `sf_frontend_patch`, HEAD `0d0a4e9`)  
**Target Backend Repo:** `backend` (branch `feat/backend-convergence-apis-integration`, HEAD `29f1c91363be96f397c606f23b2f26486d4d8fcb`)  
**Status:** Backend convergence complete and verified; **do NOT merge `feat/backend-convergence-apis-integration` into `main` yet** (merge remains subject to final PR, PostgreSQL, and governance-document checks).

> [!IMPORTANT]
> This document supersedes the deferred/WIP sections of `SAM_M4_M5_FRONTEND_INTEGRATION_README.md`. It lists **every backend `/v1` API route not yet wired in `MentorAmp-frontend/src/`**, exact request/response schemas, TypeScript definitions to add, governance/security UI rules (CONV-01..05), and a **complete local seeding & testing playbook** so you can run against real data immediately.

---

## 1. Local Environment, Auth & Data Seeding Playbook

### Why Testing Was Blocked Without Data (and How to Fix It)

1. **Empty Database:** Starting the backend against a fresh database gives 0 projects, 0 models, 0 inforce files, and 0 runs.
2. **Demo User Passwords:** `backend/scripts/seed_demo.py` creates `demo-admin@mentoramp.local` and `demo-reviewer@mentoramp.local` with random `secrets.token_urlsafe(32)` passwords. If you run `seed_demo.py` *before* calling `POST /v1/auth/bootstrap`, `/v1/auth/bootstrap` returns `409 Conflict` ("Administrator bootstrap has already been completed.") and you won't know the password to sign in through `MentorAmp-frontend`'s login form!
3. **Port Alignment:** `MentorAmp-frontend/src/api/ApiProvider.tsx` defaults `VITE_API_BASE_URL` to **`http://localhost:8001/`**. Make sure you run uvicorn on port `8001` (or set `VITE_API_BASE_URL`).

### Step 1: Start the Backend on Port `8001`

From the `backend/` directory:

```powershell
# Activate venv
(Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned)
& ".\.venv\Scripts\Activate.ps1"

# Start FastAPI on port 8001 (matches ApiProvider.tsx default)
uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

CORS is already configured in `app/config.py` (`DEFAULT_CORS_ORIGINS`) for:
`http://localhost:5173`, `http://127.0.0.1:5173`, `http://localhost:5174`, `http://127.0.0.1:5174`, `http://localhost:5175`, `http://127.0.0.1:5175`, `http://localhost:5176`, `http://127.0.0.1:5176`, `http://localhost:3000`, `http://localhost:8001`.

### Step 2: Seed SPIA, 5-Year MYGA, FIA, and RILA Projects + Sample Data

From the repository root:

```powershell
# 1. Seed SPIA + 5-Year MYGA projects, models, mortality/factors, inforce, scenarios, expected results
backend\.venv\Scripts\python.exe backend\scripts\seed_demo.py

# 2. Seed isolated illustrative FIA & RILA blocks (30 policies, 72 months each)
backend\.venv\Scripts\python.exe backend\scripts\seed_indexed.py

# 3. Execute an end-to-end SPIA Run Set (creates completed runs, results, traces, manifests)
backend\.venv\Scripts\python.exe backend\scripts\demo_run.py --base-url http://localhost:8001/v1
```

### Step 3: Set Local-Only Passwords for Frontend Login (Disposable Local Test Environments Only)

> [!CAUTION]
> **Disposable Local Test Environments Only:** Never run demonstration seeders or password reset snippets against shared development, staging, or production/Neon databases. Ensure `APP_ENV=local` (or `test`) and `DATABASE_URL` points exclusively to your own disposable local database before running the command below.

Run this one-liner from `backend/` after seeding (supplying your own temporary local passwords via environment variables) so you can sign into the frontend as both **Admin** (author/executor) and **Reviewer** (independent four-eyes approver):

```powershell
$env:LOCAL_DEMO_ADMIN_PW = "<choose-local-admin-password>"
$env:LOCAL_DEMO_REVIEWER_PW = "<choose-local-reviewer-password>"
backend\.venv\Scripts\python.exe -c "
import os
from app.config import settings
from app.db.database import SessionLocal
from app.db.models.user import User
from app.core.security import hash_password
assert settings.app_env in {'local', 'test'}, f'Refusing reset in APP_ENV={settings.app_env}'
pw_map = {
    'demo-admin@mentoramp.local': os.environ['LOCAL_DEMO_ADMIN_PW'],
    'demo-reviewer@mentoramp.local': os.environ['LOCAL_DEMO_REVIEWER_PW'],
}
with SessionLocal() as db:
    for email, pw in pw_map.items():
        u = db.query(User).filter(User.email == email).first()
        if u:
            u.password_hash = hash_password(pw)
            print('Updated local test password for:', email)
    db.commit()
"
```

Now sign into `MentorAmp-frontend` at `/v1/auth/token` (against your local server only) with:
- **Admin:** `demo-admin@mentoramp.local` and your `LOCAL_DEMO_ADMIN_PW`
- **Reviewer (for four-eyes approvals):** `demo-reviewer@mentoramp.local` and your `LOCAL_DEMO_REVIEWER_PW`

### Step 4: Sample CSV Files for Testing Data Manager / Imports

Use the sample CSV files already in the repo:
- **SPIA:** `samples/spia/synthetic_spia_inforce.csv`, `samples/spia/synthetic_mortality_gompertz.csv`
- **MYGA:** `samples/myga/synthetic_myga_inforce.csv`, `samples/myga/synthetic_myga_inforce_10001.csv`, `samples/myga/myga_surrender_charge.csv`, `samples/myga/myga_sc_age_cap.csv`, `samples/myga/expected_reference_myga_10001.csv`

---

## 2. Unwired Backend APIs — Complete Reference

Every route below is live on `/v1` in `backend/app/main.py` and **not yet called** in `MentorAmp-frontend/src/`.

### A. Central Assumption & Factor Table Engine (12 Endpoints)

**Permissions:** `registries:read` for all global `/v1/assumptions/*` routes; `projects:read` (project-scoped membership enforced) for `GET /v1/projects/{project_id}/assumptions/tables`.

> [!WARNING]
> **Mandatory CONV-02 Governance UI Rules:**
> - Always inspect `is_synthetic`, `is_production_approved`, `production_catalog_eligible`, and `production_governed_authorized`.
> - When `is_synthetic === true` or `is_production_approved === false`, display a clear badge/banner (e.g., `"Synthetic / Illustrative — Not Production Approved"`) using `governance_notice` and `classification` (`"SYNTHETIC_NON_PRODUCTION"`).
> - **Never** allow synthetic tables (`is_synthetic: true`) to appear in a "Production Approved" filter or represent `status: "approved"` as Actuarial Product Owner sign-off (`fixture_registration_status` is fixture registration only).

#### 1. `GET /v1/assumptions/tables`
Lists all registered factor/assumption tables in the Assumption Engine registry.
- **Response `200`:**
```json
{
  "tables": [
    {
      "table_id": "string",
      "version_label": "string",
      "status": "string",
      "fixture_registration_status": "string",
      "source_reference": "string",
      "effective_date": "string",
      "created_by": "string",
      "approved_by": "string | null",
      "approved_at": "string | null",
      "fingerprint_sha256": "string",
      "interpolation": "STEP_FLOOR | STEP_NEAREST | LINEAR | BILINEAR | MULTILINEAR",
      "boundary_policy": "FAIL_CLOSED | CLAMP_TO_BOUNDS | EXTRAPOLATE_FLAT",
      "dimensions": [
        {
          "name": "string",
          "dimension_type": "NUMERIC | CATEGORICAL",
          "values": ["number | string"],
          "count": 0,
          "min": "number | null",
          "max": "number | null"
        }
      ],
      "shape": [0],
      "total_cells": 0,
      "is_synthetic": true,
      "is_production_approved": false,
      "production_catalog_eligible": false,
      "production_governed_authorized": false,
      "classification": "SYNTHETIC_NON_PRODUCTION | GOVERNED_PRODUCTION | DRAFT",
      "approval_authority": "string | null",
      "governance_notice": "string",
      "scope": "global_reference",
      "project_id": "string | null"
    }
  ]
}
```

#### 2. `GET /v1/projects/{project_id}/assumptions/tables`
Project-scoped assumption tables view (enforces project membership / CONV-03 isolation; returns `403` if user lacks project access, `404` if project does not exist).
- **Response `200`:**
```json
{
  "project_id": "string",
  "scope": "project",
  "project_tables": [],
  "global_reference_tables": [ /* same table summary objects as above */ ],
  "notice": "Project '...' inherits global illustrative factor tables. No project-specific overrides are registered."
}
```

#### 3. `GET /v1/assumptions/tables/{table_id}`
Returns full metadata + `sample_preview` (up to 50 coordinate/value pairs) for a single table.
- **Response `200`:** Same fields as table summary + `"sample_preview": [{ "coordinates": { ... }, "value": 0.0 }]`.
- **Errors:** `404` if `table_id` not found.

#### 4. `POST /v1/assumptions/tables/{table_id}/lookup`
Performs multi-dimensional lookup with optional interpolation and boundary policy overrides.
- **Request:**
```json
{
  "coordinates": { "attained_age": 65, "sex": "M" },
  "interpolation": "LINEAR",
  "boundary_policy": "CLAMP_TO_BOUNDS"
}
```
  - `interpolation` (optional): `"STEP_FLOOR" | "STEP_NEAREST" | "LINEAR" | "BILINEAR" | "MULTILINEAR"`
  - `boundary_policy` (optional): `"FAIL_CLOSED" | "CLAMP_TO_BOUNDS" | "EXTRAPOLATE_FLAT"`
- **Response `200`:**
```json
{
  "table_id": "string",
  "coordinates": { "attained_age": 65, "sex": "M" },
  "value": 0.01234,
  "interpolation_used": "LINEAR",
  "boundary_policy_used": "CLAMP_TO_BOUNDS",
  "fingerprint_verified": true,
  "is_synthetic": true,
  "classification": "SYNTHETIC_NON_PRODUCTION"
}
```
- **Errors:** `400` on out-of-bounds (`FAIL_CLOSED`) or invalid coordinates; `404` if table not found.

#### 5. `GET /v1/assumptions/mortality/tables`
Lists all tables with `attained_age` and `sex` dimensions.
- **Response `200`:** `{ "mortality_tables": [ /* TableSummary[] */ ] }`

#### 6. `POST /v1/assumptions/mortality/project`
Projects a mortality rate `qx` using Scale G2 or MP improvement matrix.
- **Request:**
```json
{
  "table_id": "string",
  "attained_age": 65,
  "sex": "M",
  "base_year": 2025,
  "projection_year": 2035,
  "improvement_table_id": "string | null",
  "base_qx": 0.01,
  "mode": "FIXED_ATTAINED_AGE",
  "terminal_age": 120
}
```
  - `attained_age`: int `0..150`; `base_year`, `projection_year`: int `1900..2200`; `base_qx` (optional): `0.0..1.0`; `mode`: `"FIXED_ATTAINED_AGE" | "GENERATIONAL"`; `terminal_age` (optional): `1..150`.
- **Response `200`:**
```json
{
  "base_qx": 0.01,
  "projected_qx": 0.0091,
  "attained_age": 65,
  "sex": "M",
  "base_year": 2025,
  "projection_year": 2035,
  "mode": "FIXED_ATTAINED_AGE",
  "method": "MP_MATRIX | SCALE_G2 | UNADJUSTED_BASE",
  "terminal_age": 120,
  "is_synthetic": true,
  "governance_notice": "Projection produced with illustrative development tables."
}
```

#### 7. `POST /v1/assumptions/mortality/monthly-decrement`
Converts an annual `qx` into a monthly decrement rate.
- **Request:**
```json
{
  "annual_qx": 0.012,
  "method": "constant_force",
  "month_index": 1
}
```
  - `method`: `"constant_force"` or `"udd"`; `month_index`: int `1..12` (used for UDD).
- **Response `200`:**
```json
{
  "annual_qx": 0.012,
  "method": "constant_force",
  "month_index": 1,
  "monthly_decrement": 0.0010055
}
```

#### 8. `GET /v1/assumptions/curves`
Lists available yield/discount curves.
- **Response `200`:**
```json
{
  "curves": [
    {
      "curve_id": "string",
      "curve_type": "SPOT_ZERO | ...",
      "compounding": "string",
      "source_reference": "string",
      "effective_date": "string",
      "min_tenor": 0.25,
      "max_tenor": 30.0,
      "point_count": 12,
      "tenors": [0.25, 0.5, 1.0],
      "rates": [0.04, 0.042, 0.045],
      "is_synthetic": true,
      "is_production_approved": false,
      "classification": "SYNTHETIC_NON_PRODUCTION",
      "approval_authority": "SYNTHETIC_DEMO_NON_AUTHORITATIVE",
      "governance_notice": "Illustrative synthetic spot yield curve; not published US Treasury data."
    }
  ]
}
```

#### 9. `GET /v1/assumptions/curves/{curve_id}`
Returns full curve metadata, methodology, and points with precomputed discount factors (for `SPOT_ZERO`).
- **Response `200`:**
```json
{
  "curve_id": "string",
  "curve_type": "SPOT_ZERO",
  "compounding": "string",
  "source_reference": "string",
  "effective_date": "string",
  "methodology": "string",
  "is_synthetic": true,
  "is_production_approved": false,
  "classification": "SYNTHETIC_NON_PRODUCTION",
  "approval_authority": "SYNTHETIC_DEMO_NON_AUTHORITATIVE",
  "governance_notice": "string",
  "points": [
    { "tenor_years": 1.0, "rate": 0.045, "discount_factor": 0.9569 }
  ]
}
```

#### 10. `POST /v1/assumptions/curves/{curve_id}/discount-factor`
- **Request:** `{ "tenor_years": 5.5 }` (`tenor_years >= 0`)
- **Response `200`:**
```json
{
  "curve_id": "string",
  "tenor_years": 5.5,
  "interpolated_rate": 0.046,
  "discount_factor": 0.7812,
  "curve_type": "SPOT_ZERO",
  "compounding": "string",
  "is_synthetic": true
}
```

#### 11. `POST /v1/assumptions/behavior/baseline-lapse`
- **Request:**
```json
{
  "duration": 5,
  "early_duration_rates": [0.02, 0.02, 0.03, 0.03, 0.04],
  "expiration_shock_rate": 0.15,
  "post_shock_rate": 0.08,
  "ultimate_rate": 0.05,
  "grading_years": 3
}
```
  - **Rule (CONV-04):** `0.0` is valid for shock/post-shock/ultimate rates. However, if any of `expiration_shock_rate`, `post_shock_rate`, or `ultimate_rate` is provided, `early_duration_rates` (non-empty) **must** also be provided (otherwise `400`). Omit all override fields to evaluate the default illustrative schedule.
- **Response `200`:**
```json
{
  "duration": 5,
  "baseline_lapse_rate": 0.04,
  "surrender_period_years": 5,
  "shock_duration": 6,
  "is_synthetic": true,
  "governance_notice": "Illustrative behavioral lapse schedule for architecture validation only."
}
```

#### 12. `POST /v1/assumptions/behavior/evaluate`
- **Request:**
```json
{
  "base_lapse": 0.05,
  "market_rate": 0.06,
  "contract_rate": 0.03,
  "buffer": 0.01,
  "threshold": 0.02,
  "alpha": 2.0,
  "beta": 1.0,
  "max_lapse_rate": 0.50
}
```
- **Response `200`:**
```json
{
  "base_lapse": 0.05,
  "market_rate": 0.06,
  "contract_rate": 0.03,
  "spread": 0.02,
  "dynamic_multiplier": 3.0,
  "effective_lapse_rate": 0.15,
  "max_lapse_rate": 0.50,
  "is_synthetic": true,
  "governance_notice": "Illustrative behavioral lapse model for architecture validation only."
}
```

---

### B. Valuation Detail & Rollforward Summary (2 Endpoints)

**Permission:** `projects:read` (project-scoped via `run_id`).

#### 1. `GET /v1/runs/{run_id}/valuation-detail`
Returns valuation-specific details when present, or a graceful projection fallback otherwise.
- **Response `200` (Valuation run):**
```json
{
  "run_id": "string",
  "project_id": "string",
  "status": "string",
  "is_valuation_run": true,
  "has_valuation_detail": true,
  "methodology": "string | null",
  "basis": "string | null",
  "valuation_date": "string | null",
  "discount_curve_fingerprint": "string | null",
  "configuration": {},
  "summary_results": {},
  "created_at": "string",
  "completed_at": "string | null"
}
```
- **Response `200` (Projection run without subordinate valuation record):**
```json
{
  "run_id": "string",
  "project_id": "string",
  "status": "string",
  "is_valuation_run": false,
  "has_valuation_detail": false,
  "methodology": "string | null",
  "basis": "string | null",
  "valuation_date": "string | null",
  "headline": {},
  "totals": {},
  "notice": "This execution is a projection run without dedicated subordinate valuation records."
}
```

#### 2. `GET /v1/runs/{run_id}/rollforward`
Returns the rollforward attribution schedule **plus independent arithmetic continuity verification (CONV-05)**.
> [!CAUTION]
> **CONV-05 UI Rule:** Never trust `stored_continuity_verified` alone. Display `recomputed_continuity_verified` as the authoritative continuity status, and if `is_tampered_or_forged === true` (or any cohort has `is_tampered === true`), display a high-severity integrity alert!

- **Response `200`:**
```json
{
  "run_id": "string",
  "project_id": "string",
  "count": 0,
  "stored_continuity_verified": true,
  "recomputed_continuity_verified": true,
  "is_tampered_or_forged": false,
  "max_continuity_difference": 0.0,
  "tolerance": 0.01,
  "cohort_evaluations": {
    "COHORT_KEY": {
      "cohort_id": "string",
      "opening_balance": 1000.0,
      "sum_movements": 50.0,
      "expected_closing_balance": 1050.0,
      "actual_closing_balance": 1050.0,
      "arithmetic_difference": 0.0,
      "tolerance": 0.01,
      "recomputed_continuity_verified": true,
      "stored_continuity_flag": true,
      "is_tampered": false
    }
  },
  "items": [
    {
      "id": "string",
      "run_id": "string",
      "project_id": "string",
      "cohort_id": "string",
      "methodology": "string | null",
      "basis": "string | null",
      "currency": "string",
      "component_name": "string",
      "component_order": 1,
      "is_movement": false,
      "opening_balance": 1000.0,
      "movement_amount": 0.0,
      "closing_balance": 1000.0,
      "stored_continuity_flag": true,
      "continuity_difference": 0.0,
      "trace_identifier": "string | null",
      "created_at": "string"
    }
  ]
}
```

---

### C. Promoted M5 Governance, Rollforward, Derived Datasets, Reports & Run Steps (15 Endpoints)

Previously under `/internal/experimental`, these are now promoted to `/v1` in `backend/app/main.py`.
- **Read permission:** `projects:read` (or `runs:read` for `/runs/{run_id}/steps`).
- **Write permission:** `registries:write` (with active-project check; published items are immutable — `409 Conflict` on `PATCH`, use `POST .../versions` instead).

| Entity | Method & Path | Request Body / Notes | Response Key / Shape |
| :--- | :--- | :--- | :--- |
| **Rollforward Templates** | `GET /v1/projects/{project_id}/rollforward-templates` | — | `{ "templates": RollforwardTemplate[] }` |
| | `POST /v1/projects/{project_id}/rollforward-templates` | `{ name, description?, model_version_id, configuration?, steps?: [{ step_key, name, step_type, sequence?, configuration? }] }` (`201`) | `RollforwardTemplate` (includes `steps`) |
| | `GET /v1/rollforward-templates/{template_id}` | — | `RollforwardTemplate` (includes `steps`) |
| | `PATCH /v1/rollforward-templates/{template_id}` | `{ name?, description?, configuration?, steps? }` (`409` if published) | `RollforwardTemplate` |
| | `POST /v1/rollforward-templates/{template_id}/versions` | Empty body (`201`) | New draft `RollforwardTemplate` (`version_number + 1`) |
| | `POST /v1/rollforward-templates/{template_id}/publish` | Empty body | Published `RollforwardTemplate` |
| | `DELETE /v1/rollforward-templates/{template_id}` | Soft-archives template (`204 No Content`) | `204` |
| **Rollforward Jobs** | `GET /v1/projects/{project_id}/rollforward-jobs` | — | `{ "jobs": RollforwardJob[] }` |
| | `POST /v1/projects/{project_id}/rollforward-jobs` | `{ template_id, name, from_date: "YYYY-MM-DD", to_date: "YYYY-MM-DD", parameters? }` (`201`) | `RollforwardJob` |
| | `GET /v1/rollforward-jobs/{job_id}` | — | `RollforwardJob` |
| | `PATCH /v1/rollforward-jobs/{job_id}` | `{ status?, run_set_id? }` | `RollforwardJob` |
| **Reports** | `GET /v1/projects/{project_id}/reports` | — | `{ "reports": Report[] }` |
| | `POST /v1/projects/{project_id}/reports` | `{ name, report_type, description?, definition? }` (`201`) | `Report` |
| | `GET /v1/reports/{report_id}` | — | `Report` |
| | `PATCH /v1/reports/{report_id}` | `{ name?, report_type?, description?, definition?, artifact_uri?, artifact_fingerprint? }` | `Report` |
| | `POST /v1/reports/{report_id}/versions` | Empty body (`201`) | New draft `Report` |
| | `POST /v1/reports/{report_id}/publish` | Empty body | Published `Report` |
| | `DELETE /v1/reports/{report_id}` | Soft-archives report (`204 No Content`) | `204` |
| **Derived Datasets** | `GET /v1/projects/{project_id}/derived-datasets` | — | `{ "derived_datasets": DerivedDataset[] }` |
| | `POST /v1/projects/{project_id}/derived-datasets` | `{ name, dataset_type, description?, source_run_ids?, schema?, storage_uri?, storage_backend?, file_format?, row_count?, fingerprint? }` (`201`) | `DerivedDataset` |
| | `GET /v1/derived-datasets/{dataset_id}` | — | `DerivedDataset` |
| | `PATCH /v1/derived-datasets/{dataset_id}` | Partial update of create fields | `DerivedDataset` |
| | `POST /v1/derived-datasets/{dataset_id}/versions` | Empty body (`201`) | New draft `DerivedDataset` |
| | `POST /v1/derived-datasets/{dataset_id}/publish` | Empty body | Published `DerivedDataset` |
| | `DELETE /v1/derived-datasets/{dataset_id}` | Soft-archives dataset (`204 No Content`) | `204` |
| **Run Steps** | `GET /v1/runs/{run_id}/steps` | — | `{ "steps": RunStep[] }` |

---

### D. Run Control, Model Version Lifecycle & Dataset Approval Actions

These endpoints complete the interactive lifecycle buttons in **Runs**, **Models**, and **Data Manager**:

1. **Run Cancel / Retry** (`runs:execute`):
   - `POST /v1/runs/{run_id}/cancel` (`202 Accepted`) — cancels a queued/running run.
   - `POST /v1/runs/{run_id}/retry` (`202 Accepted`) — creates & queues a retry attempt in background tasks; returns the new/updated run payload.
2. **Model Version Lifecycle** (`registries:write` for `validate`; `models:approve` for `approve`, `reject`, `rollback` — **four-eyes rule: approver cannot be the creator/submitter**):
   - `POST /v1/model-versions/{model_version_id}/validate`
   - `POST /v1/model-versions/{model_version_id}/approve` — optional body `{ "reason": "string" }`
   - `POST /v1/model-versions/{model_version_id}/reject` — optional body `{ "reason": "string" }`
   - `POST /v1/model-versions/{model_version_id}/rollback`
3. **Model Version Variables & Published Outputs** (`registries:write`):
   - `PATCH /v1/model-versions/{model_version_id}/variables/{variable_name}`
   - `DELETE /v1/model-versions/{model_version_id}/variables/{variable_name}` (`204`)
   - `PATCH /v1/model-versions/{model_version_id}/published-outputs/{variable_name}` — `{ display_name?, unit?, dimension?, aggregation?, description?, sort_order? }`
   - `DELETE /v1/model-versions/{model_version_id}/published-outputs/{variable_name}` (`204`)
4. **Dataset Four-Eyes Approval / Rejection** (`imports:approve`):
   - `POST /v1/datasets/{kind}/{dataset_id}/approve` (`kind` in `"inforce" | "assumption" | "factor" | "scenario"`) — **required** body `{ "reason": "string (min 1 char)" }`
   - `POST /v1/datasets/{kind}/{dataset_id}/reject` — **required** body `{ "reason": "string (min 1 char)" }`

---

### E. Remaining Unwired Endpoints (Projects, Inputs, Data Manager Logs, Assets, Global Variables, Dashboard)

- **Project Archive:** `POST /v1/projects/{project_id}/archive` (`projects:write`) — body `{ "reason": "string" }`, returns `ProjectResponse`.
- **Input Table Records (paginated):**
  - `GET /v1/inputs/assumptions/{table_id}/records?limit=100&offset=0&search=...`
  - `GET /v1/inputs/factors/{table_id}/records?limit=100&offset=0&search=...`
- **Import Session Log:** `GET /v1/import-sessions/{session_id}/log` → `{ "events": [...] }`.
- **Asset Positions:** `GET /v1/asset-positions/`, `POST /v1/asset-positions/`, `GET /v1/asset-positions/{position_id}`, `PATCH /v1/asset-positions/{position_id}`, `DELETE /v1/asset-positions/{position_id}`.
- **Global Semantic Variable Catalog** (admin-only writes): `GET /v1/variables/`, `POST /v1/variables/`, `GET /v1/variables/{name}`, `PUT /v1/variables/{name}`, `DELETE /v1/variables/{name}`.
- **Dashboard Stats:** `GET /v1/dashboard/stats?project_id=...` (note: `/v1/projects/{project_id}/dashboard` is already wired).
- **Legacy One-Shot Imports** (prefer `/v1/import-sessions` in Data Manager): `POST /v1/imports/assumptions`, `POST /v1/imports/factors`, `POST /v1/imports/inforce`, `POST /v1/imports/scenarios`, `GET /v1/imports/inforce/{file_id}/records`, `GET /v1/imports/preview`.

---

## 3. TypeScript Types to Add to `MentorAmp-frontend/src/types/mentoramp-api.ts`

```ts
// --- Central Assumption & Factor Table Engine ---

export type TableDimension = {
  name: string
  dimension_type: 'NUMERIC' | 'CATEGORICAL' | string
  values: (number | string)[]
  count: number
  min: number | string | null
  max: number | string | null
}

export type AssumptionClassification =
  | 'SYNTHETIC_NON_PRODUCTION'
  | 'GOVERNED_PRODUCTION'
  | 'DRAFT'

export type AssumptionTableSummary = {
  table_id: string
  version_label: string
  status: string
  fixture_registration_status: string
  source_reference: string
  effective_date: string
  created_by: string
  approved_by: string | null
  approved_at: string | null
  fingerprint_sha256: string
  interpolation: string
  boundary_policy: string
  dimensions: TableDimension[]
  shape: number[]
  total_cells: number
  is_synthetic: boolean
  is_production_approved: boolean
  production_catalog_eligible: boolean
  production_governed_authorized: boolean
  classification: AssumptionClassification | string
  approval_authority: string | null
  governance_notice: string
  scope: string
  project_id: string | null
}

export type AssumptionTableDetail = AssumptionTableSummary & {
  sample_preview: { coordinates: Record<string, number | string>; value: number }[]
}

export type ProjectAssumptionTablesResponse = {
  project_id: string
  scope: 'project'
  project_tables: AssumptionTableSummary[]
  global_reference_tables: AssumptionTableSummary[]
  notice: string
}

export type TableLookupRequest = {
  coordinates: Record<string, number | string>
  interpolation?: 'STEP_FLOOR' | 'STEP_NEAREST' | 'LINEAR' | 'BILINEAR' | 'MULTILINEAR'
  boundary_policy?: 'FAIL_CLOSED' | 'CLAMP_TO_BOUNDS' | 'EXTRAPOLATE_FLAT'
}

export type TableLookupResponse = {
  table_id: string
  coordinates: Record<string, number | string>
  value: number
  interpolation_used: string
  boundary_policy_used: string
  fingerprint_verified: boolean
  is_synthetic: boolean
  classification: string
}

export type MortalityProjectionRequest = {
  table_id: string
  attained_age: number
  sex: string
  base_year: number
  projection_year: number
  improvement_table_id?: string | null
  base_qx?: number | null
  mode?: 'FIXED_ATTAINED_AGE' | 'GENERATIONAL'
  terminal_age?: number | null
}

export type MortalityProjectionResponse = {
  base_qx: number
  projected_qx: number
  attained_age: number
  sex: string
  base_year: number
  projection_year: number
  mode: string
  method: 'MP_MATRIX' | 'SCALE_G2' | 'UNADJUSTED_BASE' | string
  terminal_age: number | null
  is_synthetic: boolean
  governance_notice: string
}

export type YieldCurveSummary = {
  curve_id: string
  curve_type: string
  compounding: string
  source_reference: string
  effective_date: string
  min_tenor: number
  max_tenor: number
  point_count: number
  tenors: number[]
  rates: number[]
  is_synthetic: boolean
  is_production_approved: boolean
  classification: string
  approval_authority: string | null
  governance_notice: string
}

export type YieldCurveDetail = Omit<YieldCurveSummary, 'min_tenor' | 'max_tenor' | 'point_count' | 'tenors' | 'rates'> & {
  methodology: string
  points: { tenor_years: number; rate: number; discount_factor: number | null }[]
}

// --- Valuation Detail & Rollforward (CONV-05) ---

export type ValuationDetailResponse = {
  run_id: string
  project_id: string
  status: string
  is_valuation_run: boolean
  has_valuation_detail: boolean
  methodology: string | null
  basis: string | null
  valuation_date: string | null
  discount_curve_fingerprint?: string | null
  configuration?: Record<string, unknown>
  summary_results?: Record<string, unknown>
  headline?: Record<string, unknown>
  totals?: Record<string, unknown>
  notice?: string
  created_at?: string
  completed_at?: string | null
}

export type RollforwardCohortEvaluation = {
  cohort_id: string
  opening_balance: number
  sum_movements: number
  expected_closing_balance: number
  actual_closing_balance: number
  arithmetic_difference: number
  tolerance: number
  recomputed_continuity_verified: boolean
  stored_continuity_flag: boolean
  is_tampered: boolean
}

export type RollforwardSummaryItem = {
  id: string
  run_id: string
  project_id: string
  cohort_id: string
  methodology: string | null
  basis: string | null
  currency: string
  component_name: string
  component_order: number
  is_movement: boolean
  opening_balance: number
  movement_amount: number
  closing_balance: number
  stored_continuity_flag: boolean
  continuity_difference: number
  trace_identifier: string | null
  created_at: string
}

export type RollforwardSummaryResponse = {
  run_id: string
  project_id: string
  count: number
  stored_continuity_verified: boolean | null
  recomputed_continuity_verified: boolean | null
  is_tampered_or_forged: boolean
  max_continuity_difference: number
  tolerance: number
  cohort_evaluations: Record<string, RollforwardCohortEvaluation>
  items: RollforwardSummaryItem[]
}
```

---

## 4. Screen-by-Screen Integration Checklist (`MentorAmp-frontend/src/App.tsx`)

1. **Inputs / Assumption Explorer:**
   - Wire `GET /v1/projects/{project_id}/assumptions/tables`, `GET /v1/assumptions/tables/{table_id}`, and `POST /v1/assumptions/tables/{table_id}/lookup`.
   - Add Mortality Projection & Yield Curve calculators using `/v1/assumptions/mortality/*`, `/v1/assumptions/curves*`, and `/v1/assumptions/behavior/*`.
   - Enforce CONV-02 synthetic/non-production badges on all synthetic tables and curves.
   - Wire `GET /v1/inputs/assumptions/{table_id}/records` and `GET /v1/inputs/factors/{table_id}/records` for paginated row browsing.
2. **Results & Investigate:**
   - Add a **Valuation Detail** panel calling `GET /v1/runs/{run_id}/valuation-detail` (handle both `is_valuation_run: true` and `false`).
   - Add a **Rollforward Attribution** panel calling `GET /v1/runs/{run_id}/rollforward` with per-cohort continuity status and CONV-05 `is_tampered_or_forged` alert banner.
   - Optionally display execution steps via `GET /v1/runs/{run_id}/steps`.
3. **Runs:**
   - Wire **Cancel** (`POST /v1/runs/{run_id}/cancel`) for active runs and **Retry** (`POST /v1/runs/{run_id}/retry`) for failed/cancelled runs.
4. **Models / Formula Studio:**
   - Wire `validate`, `approve`, `reject`, and `rollback` buttons on model versions (respecting `models:approve` permission & four-eyes rule).
   - Wire variable & published-output `PATCH` / `DELETE` actions.
5. **Data Manager:**
   - Wire `POST /v1/datasets/{kind}/{dataset_id}/approve` and `/reject` with a required `reason` modal (`imports:approve`).
   - Wire `GET /v1/import-sessions/{session_id}/log`.
6. **Reports & Rollforward Governance (M5):**
   - Wire `/v1/projects/{project_id}/rollforward-templates`, `/rollforward-jobs`, `/derived-datasets`, and `/reports` (CRUD + `/versions` + `/publish` + `DELETE` archive).
7. **Admin / Settings & Projects:**
   - Wire `POST /v1/projects/{project_id}/archive`, `/v1/asset-positions/*`, and `/v1/variables/*`.

---

## 5. End-to-End Smoke Verification Checklist

1. Start backend on port `8001`, run `seed_demo.py`, `seed_indexed.py`, and `demo_run.py --base-url http://localhost:8001/v1`.
2. Reset demo passwords using the Python snippet in Section 1 and sign in as `demo-admin@mentoramp.local`.
3. Confirm `GET /v1/projects/` shows 4 projects (SPIA, 5-Year MYGA, FIA, RILA).
4. Confirm `GET /v1/projects/{project_id}/assumptions/tables` returns global illustrative tables with `is_synthetic: true` and `is_production_approved: false`.
5. Confirm `GET /v1/runs/{run_id}/rollforward` and `GET /v1/runs/{run_id}/valuation-detail` return `200 OK` on a completed SPIA run.
6. Sign in as `demo-reviewer@mentoramp.local` in a second browser/incognito session to test four-eyes `approve` / `reject` flows on datasets and model versions.
