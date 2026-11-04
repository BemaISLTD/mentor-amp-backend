# MentorAmp — Comprehensive Frontend Integration & Implementation Specification (M3 + M4 FIA/RILA + M5 Valuation & Governance)

| Field | Value |
|---|---|
| **Document ID** | `SAM-FE-M3-M4-M5-SPEC` |
| **Version** | `3.0` (Covers M1–M3 Production + M4 FIA/RILA Indexed Verticals + M5 Valuation Foundation, Rollforwards, Derived Datasets & Reports) |
| **Date** | November 2026 |
| **Target Audience** | **Sam Yung** (Lead Frontend Engineer — `MentorAmp-frontend`, React 18 / TypeScript / Vite) |
| **Author** | Samuel |
| **Backend Reference Branches** | `main` (M1–M3) • `origin/feat/m4-fia-rila-foundation` (`999147b`) • `feat/m5-valuation-frameworks-dagster` (`130cebf`) |
| **API Prefix & Docs** | `/v1` • Swagger UI: `http://localhost:8000/docs` • ReDoc: `http://localhost:8000/redoc` |
| **Controlling References** | `CAS v3.2` • `Master Build Plan` • `W0R` • `ITD` • `NOAH_M4_FIA_RILA_IMPLEMENTATION_README.md` • `Samuel M5 Controlling Specification` |

---

## 0. Coordination Notice for Sam (Read First)

> **IMPORTANT — Backend Review & Parallel Frontend Execution Notice**
>
> 1. **You can build and wire 100% of the frontend screens immediately using this specification.** All REST endpoint paths, HTTP methods, RBAC permissions, request/response envelopes, variable names, product codes (`SPIA`, `MYGA`, `FIA`, `RILA`), and governance state machines documented below are locked.
> 2. **Noah's M4 Branch (`origin/feat/m4-fia-rila-foundation`) Is Undergoing Final Review:** While I am finishing the review of Noah's M4 backend PR, a small backend correction pass (`M4-01` through `M4-05`) will be pushed soon to tighten a few server-side validations:
>    - **What will NOT change:** No endpoint URLs, request payloads, inforce column names, factor table keys, or 25 published `FIA`/`RILA` output variable names will change.
>    - **What the upcoming backend patch will tighten (and how you should already code the UI):**
>      - **`M4-01` (`system/status` dynamic product discovery):** `/v1/system/status` will dynamically return `["SPIA", "MYGA", "FIA", "RILA"]` from the registry. Always read `engine.products` dynamically with a fallback to `["SPIA", "MYGA", "FIA", "RILA"]`.
>      - **`M4-02` (Inforce file-upload validation):** The Data Manager `/v1/import-sessions/{id}/validate` endpoint will enforce the same cross-column indexed segment conservation and strategy-family rules (`ptp`/`spread` for `FIA`; `buffer`/`floor` for `RILA`) during file upload that the engine already enforces at run preflight (`INVALID_INDEXED_STATE`). Build your Data Manager validation table and client-side hints for these rules right now.
>      - **`M4-03` (Segment columns in `GET /v1/runs/{run_id}/results`):** `GET /v1/runs/{run_id}/results` will pass through `segment_id`, `strategy_type`, and `index_code` on each `ResultRowResponse` item (matching `GET /v1/runs/{run_id}/trace` and `GET /v1/runs/{run_id}/export.csv`, which already include them). Type those 3 fields as optional (`string | null`) on `ResultRowResponse` now so your Segment Explorer table lights up seamlessly.
>      - **`M4-04` (Scenario `path_id` override in RunSet execution):** When a RunSet executes `Base`, `Bull`, `Bear`, and `Stress` scenarios, `path_id` (`0`, `1`, `2`, `3`) will override per-scenario. Build your Scenario Manager override editor to support `target_variable: "path_id"`, `operation: "set"`, `value: 0 | 1 | 2 | 3`.
> 3. **If any minor contract adjustment arises after the backend patch, I will send a short delta note — do not wait to start implementation.**

---

## 1. Executive Summary & Core Architectural Invariants

The MentorAmp backend now spans **five architectural tiers**:
1. **M1–M2 Foundation & MYGA Vertical:** Projects, RBAC (5 roles, 10 permissions), Audit Logs, Products, Asset Positions, Data Manager (4-step import wizard), Modeling Registry, Scenario Manager, Projection Sets, Run Sets, Canonical Runs, Parquet Artifact Store, and Trace Engine (`SPIA` + `MYGA`).
2. **M3 Formula Studio, Expected Results & 3-State Reconciliation:** Live DSL expression validator, sample row evaluator, unit test runner, immutable `RunPackage` manifests, Expected Result Benchmarks (`draft` -> `approved` -> `superseded`), 3-state Actuarial Reconciliation (`PASS`, `FAIL`, `NOT_VERIFIABLE`), and Four-Eyes Segregation of Duties (`SELF_APPROVAL_FORBIDDEN`).
3. **M4 Indexed Annuity Verticals (`FIA` & `RILA`):**
   - Multi-segment account mechanics (`fixed_av`, `segment_1`, `segment_2`) with **conservation invariant** (`fixed_av + segment_1_initial_value + segment_2_initial_value == account_value`).
   - **FIA** (`ptp` point-to-point with cap & participation, `spread` with net return floor at `0%` — principal protected).
   - **RILA** (`buffer` absorbs initial loss up to `downside_rate`; `floor` caps policyholder loss at `-downside_rate`).
   - **25 published output variables** per product (11 portfolio-level + 14 segment-level across `segment_1` and `segment_2`) with segment attribution dimensions (`segment_id`, `strategy_type`, `index_code`).
   - **Representative RILA Block:** 30 policies (`RILA-001`..`RILA-030`), 4 equity paths (`Base=0`, `Bull=1`, `Bear=2`, `Stress=3`), 72-month projection (`6` years).
4. **M5 Valuation Foundation, Rollforwards, Derived Datasets & Reports:**
   - **Launch Valuation Bases (`SUPPORTED_LAUNCH_BASES`):** `STATUTORY`, `GAAP`, `FAIR_VALUE`.
   - **Deferred Bases (`KNOWN_FUTURE_BASES`):** `TAX`, `ECONOMIC` (rejected at submission with `TAXONOMY_BASIS_DEFERRED` — UI must show these disabled with a *"Deferred to Post-Launch"* badge).
   - **Trusted Provenance Valuation Runs:** Valuation runs derive lineage strictly from persisted PostgreSQL `source_run_id` (and `source_artifact_id`) or `derived_dataset_id`. Untrusted caller-supplied `base_package_document` is forbidden (`UNTRUSTED_PROVENANCE_FORBIDDEN`).
   - **M5E Regulatory Reference Verticals (`VM21`, `VM22`, `LDTI`, `DAC`, `MRB`, `ASC815`):** Strictly gated behind actuarial sign-off decisions (`ACT-DEC-01` through `ACT-DEC-09`). Generic present-value foundation runs use `methodology: "GENERIC_PV_FOUNDATION"` and must **never** be labeled in the UI as compliant `VM-22` or `VM-21` reserves.
   - **Governed Registries:** Rollforward Templates (`/v1/projects/{id}/rollforward-templates`), Rollforward Jobs (`/v1/projects/{id}/rollforward-jobs`), Derived Datasets (`/v1/projects/{id}/derived-datasets`), and Reports (`/v1/projects/{id}/reports`), plus granular Run Steps (`/v1/runs/{id}/steps`).

### 1.1 Mandatory Governance & Actuarial Framing Rules in the UI

| Rule | UI Requirement |
|---|---|
| **Rule 1 — No Fake Regulatory Reserve Claims** | Never label a `GENERIC_PV_FOUNDATION`, `SPIA`, `MYGA`, `FIA`, or `RILA` run as a *"VM-22 Reserve"*, *"VM-21 CTE70 Reserve"*, *"US GAAP LDTI Liability"*, or *"ASC 815 Fair Value"*. Display: **"Deterministic Policy Projection & Valuation Foundation"**. |
| **Rule 2 — Pending Actuarial Decision Badges (`ACT-M4-DEC-01..15` & `ACT-DEC-01..09`)** | When viewing `FIA` or `RILA` models/runs, render an informational banner/drawer showing that the indexed mechanics operate under **Engineering Baseline Defaults** pending formal sign-off of `ACT-M4-DEC-01` through `ACT-M4-DEC-15` (and `ACT-DEC-01`..`09` for M5E regulatory verticals). |
| **Rule 3 — Four-Eyes Segregation of Duties (`SELF_APPROVAL_FORBIDDEN`)** | Compare `currentUser.id` (from `GET /v1/auth/me`) against `created_by` / `submitted_by` on Model Versions, Datasets, Expected Result Benchmarks, and Reconciliation Dispositions. If `currentUser.id === creatorId`, **disable** the Approve / Publish / Override button and show tooltip: *"Four-eyes governance: You cannot approve or disposition your own submission."* |
| **Rule 4 — 3-State Reconciliation Badges (`PASS`, `FAIL`, `NOT_VERIFIABLE`)** | Never treat `NOT_VERIFIABLE` as `PASS` or as a crash. Render distinct semantic badges: `PASS` (Green), `FAIL` (Red), `NOT_VERIFIABLE` (Amber/Neutral — e.g., when no approved benchmark matches or a required metric is missing). |
| **Rule 5 — Sandbox vs. Governed Runs** | Governed runs (`is_sandbox: false`) require **published** model versions and **approved** datasets/projection sets. Sandbox runs (`is_sandbox: true`) allow `draft`/`validated`/`approved` model versions for rapid iteration and must display a prominent **`SANDBOX / NON-GOVERNED`** watermark badge. |

---

## 2. Screen-by-Screen UI Implementation Guide (All 8 Screens)

```
MentorAmp Shell
├── Topbar: Project Selector | Active Product Filter (SPIA / MYGA / FIA / RILA) | Engine Status Pill | User / Role Pill
├── 1. Dashboard (/dashboard)
├── 2. Model Library & Formula Studio (/models, /models/:id/versions/:vid/studio)
├── 3. Data Manager & Input Library (/data-manager, /inputs)
├── 4. Scenarios & Factor Tables (/scenarios)
├── 5. Projection Sets (/projection-sets)
├── 6. Execution Center & Pipeline Lineage (/runs, /run-sets/:id, /runs/:id)
├── 7. Results, Segment Explorer, Trace, Reconciliation & Rollforwards (/analysis/:runId, /rollforwards, /derived-datasets, /reports)
└── 8. Governance, Audit & Admin Settings (/settings)
```

### Screen 1: App Shell, Topbar & Dashboard (`/dashboard`)
- **System Status Boot Check (`GET /v1/system/status`):**
  - Call on app initialization.
  - Populate supported product selector from `response.engine.products` (`["SPIA", "MYGA", "FIA", "RILA"]`).
  - Show engine badge: `Engine: ${response.engine.engine_version} (${response.engine.execution_backend.toUpperCase()})` and DB latency pill (`${response.database.latency_ms} ms`).
- **Project Summary (`GET /v1/projects/{project_id}/dashboard`):**
  - Render KPI cards from `counts`: `models`, `published_models`, `inforce_files`, `assumption_tables`, `factor_tables`, `scenarios`, `projection_sets`, `run_sets`, `runs`.
  - Render Run Status Breakdown bar (`runs_by_status`: `queued`, `validating`, `running`, `aggregating`, `succeeded`, `failed`, `cancelled`).
  - Render `recent_runs` table with clickable links to `/runs/{id}` and `latest_succeeded_run` quick-jump card.

### Screen 2: Model Library, Version Lifecycle & Formula Studio (`/models`)
- **Product Type Filter & Creation:**
  - Support creating and filtering models across all 4 product types: `SPIA`, `MYGA`, `FIA`, `RILA` (`GET /v1/models?project_id=...`, `POST /v1/models`).
  - Also support Product Catalog & Mappings (`GET/POST /v1/products/`, `GET/POST/DELETE /v1/products/{product_id}/mappings`).
- **Model Version Lifecycle Stepper (`GET /v1/model-versions/{version_id}`):**
  - State progression bar: `draft` -> `validated` -> `approved` -> `published` (plus `rejected`, `archived`).
  - Action buttons wired to:
    - `POST /v1/model-versions/{version_id}/validate`
    - `POST /v1/model-versions/{version_id}/approve` (disable if `currentUser.id === version.created_by`)
    - `POST /v1/model-versions/{version_id}/reject` (modal requiring `{ "reason": "..." }`)
    - `POST /v1/model-versions/{version_id}/publish` (disable if `currentUser.id === version.created_by`)
    - `POST /v1/model-versions/{version_id}/rollback` (`{ "reason": "..." }`)
    - `POST /v1/model-versions/{version_id}/archive`
  - **Version Comparison & Dependency DAG:**
    - Diff modal calling `GET /v1/model-versions/{version_id}/compare/{other_version_id}` (displays `added_formulas`, `removed_formulas`, `modified_formulas`, `added_variables`, `removed_variables`).
    - DAG graph calling `GET /v1/model-versions/{version_id}/dependency-analysis` (renders `execution_order`, `edges`, `cycles`, `unresolved_references`).
- **Formula Studio (`/models/:id/versions/:vid/studio`):**
  - **Left Pane:** Formula Groups (`GET/POST /v1/model-versions/{version_id}/formula-groups`) & Variable Catalog (`GET/POST /v1/model-versions/{version_id}/variables`, `GET /v1/variables`).
    - For `FIA` and `RILA`, group variables visually into **Portfolio Variables (11)** and **Indexed Segment Variables (14 across `segment_1` & `segment_2`)**.
  - **Center Pane (DSL Expression Editor):**
    - Debounced (300ms) live validation calling `POST /v1/formulas/validate-expression` with `{ expression, available_variables }`.
    - Display inline syntax errors, referenced variables pill list, and whitelist function autocomplete (`min`, `max`, `abs`, `round`, `floor`, `ceil`, `exp`, `log`, `sqrt`, `pow`, `where`, `clip`, `coalesce`, `if_else`).
  - **Bottom Pane (Interactive Preview & Unit Test Runner):**
    - **Preview Tab:** Editable key-value inputs calling `POST /v1/formulas/preview` -> displays `result` or `error`.
    - **Test Cases Tab:** Editable test cases (`name`, `inputs`, `expected`, `tolerance`) calling `POST /v1/formulas/{formula_id}/run-tests` -> shows pass/fail badge per case (`passed`, `failed`, `results[]`).
  - **Right Pane (Lineage Inspector):**
    - Calls `GET /v1/formulas/{formula_id}/dependencies` -> shows `upstream_variables`, `downstream_formulas`, and `execution_wave`.

### Screen 3: Data Manager & Input Library (`/data-manager`, `/inputs`)
- **4-Step Governed Import Wizard:**
  1. **Step 1 — Create Session & Upload File:**
     - `POST /v1/import-sessions` with `dataset_type` (`"inforce" | "assumption" | "factor" | "scenario"`), `product_type` (`"SPIA" | "MYGA" | "FIA" | "RILA"`), `table_type` (for factors: e.g. `"MYGA_SURRENDER"`, `"FIA_INDEX_PATHS"`, `"FIA_SURRENDER"`, `"FIA_OPTION_BUDGET"`, `"RILA_INDEX_PATHS"`, `"RILA_SURRENDER"`, `"RILA_OPTION_BUDGET"`).
     - `POST /v1/import-sessions/{session_id}/files` (`multipart/form-data`).
  2. **Step 2 — Preview & Schema Mapping:**
     - `GET /v1/import-sessions/{session_id}/preview` -> shows detected columns and first 20 rows.
     - `PUT /v1/import-sessions/{session_id}/mapping` -> map source columns to target schema columns (see Section 3 for exact `SPIA`, `MYGA`, `FIA`, `RILA` required and optional columns).
     - Provide a **"Download CSV Template"** helper button in the UI for each product (`SPIA`, `MYGA`, `FIA`, `RILA`) and factor table type so actuaries can download pre-formatted headers.
  3. **Step 3 — Validation & Issue Inspector:**
     - `POST /v1/import-sessions/{session_id}/validate`.
     - If `error_count > 0`, fetch `GET /v1/validation-runs/{validation_id}/issues` and `GET /v1/validation-runs/{validation_id}/rejected-records` to display row-by-row errors (`row_number`, `column_name`, `code`, `message`).
     - Highlight `FIA`/`RILA` conservation errors (`fixed_av + segment_1_initial_value + segment_2_initial_value != account_value`) and strategy-family mismatches (`FIA` only allows `ptp`/`spread`; `RILA` only allows `buffer`/`floor`).
  4. **Step 4 — Commit & Dataset Four-Eyes Approval:**
     - `POST /v1/import-sessions/{session_id}/commit` -> creates the persisted dataset (`inforce_file_id`, `assumption_table_id`, or `factor_table_id`).
     - **Dataset Governance Drawer:** Call `POST /v1/datasets/{kind}/{dataset_id}/approve` or `/reject` (`kind` in `inforce`, `assumption`, `factor`, `scenario`), view version history (`GET /v1/datasets/{kind}/{dataset_id}/versions`), and diff two dataset versions (`GET /v1/datasets/{kind}/{dataset_id}/compare?other_id=...`).
- **Input Library Record Browser (`/inputs`):**
  - Browse all project inputs via `GET /v1/projects/{project_id}/inputs`.
  - Paginated, filterable, sortable policy record grid via `GET /v1/imports/inforce/{file_id}/records?page=1&page_size=50&filter_column=product_type&filter_operator=eq&filter_value=RILA&sort_by=policy_id&sort_order=asc`.
  - Factor & Assumption record grids via `GET /v1/inputs/factors/{table_id}/records` and `GET /v1/inputs/assumptions/{table_id}/records`.

### Screen 4: Scenarios & Factor Tables (`/scenarios`)
- **Scenario List & Builder (`GET/POST /v1/projects/{project_id}/scenarios`):**
  - Support creating scenarios with `overrides[]` (`target_variable`, `operation: "set" | "add" | "multiply"`, `value`, `start_step`, `end_step`).
  - **M4 Indexed Scenario Presets (`FIA` & `RILA`):**
    - Add a **"Generate 4 Standard Equity Path Scenarios (Base / Bull / Bear / Stress)"** quick-action button that creates the 4 canonical scenarios for `FIA`/`RILA`:
      - `Base`: `{ target_variable: "path_id", operation: "set", value: 0 }`
      - `Bull`: `{ target_variable: "path_id", operation: "set", value: 1 }`
      - `Bear`: `{ target_variable: "path_id", operation: "set", value: 2 }`
      - `Stress`: `{ target_variable: "path_id", operation: "set", value: 3 }`
  - Scenario lifecycle actions: `POST /v1/scenarios/{scenario_id}/validate`, `POST /v1/scenarios/{scenario_id}/duplicate`, and four-eyes approval via `POST /v1/datasets/scenario/{scenario_id}/approve`.

### Screen 5: Projection Sets Library (`/projection-sets`)
- **Projection Set Composer (`POST /v1/projects/{project_id}/projection-sets`):**
  - Binds together:
    - `model_version_id`
    - `inforce_file_id`
    - `assumption_table_ids: string[]`
    - `factor_table_ids: string[]` (for `FIA`/`RILA`, remind user to attach all 3 required tables: `{code}_INDEX_PATHS`, `{code}_SURRENDER`, `{code}_OPTION_BUDGET`)
    - `scenario_ids: string[]`
    - `horizon_months` (e.g. `60` for `MYGA`, `12` or `72` for `FIA`/`RILA`)
    - `step_size: "monthly" | "annual"`
  - Validate readiness via `POST /v1/projection-sets/{id}/validate` (checks that all linked datasets exist and belong to the project).

### Screen 6: Execution Center, Run Sets, Valuation Submission & Pipeline Lineage (`/runs`)
- **Preflight Gate (`POST /v1/run-sets/preflight`):**
  - Always invoke `/v1/run-sets/preflight` before enabling the **"Launch Run Set"** button, or display its returned `checks[]` (`passed`, `code`, `message`) in a preflight checklist modal.
  - Toggle for **Governed Run (`is_sandbox: false`)** vs. **Sandbox Run (`is_sandbox: true`)**.
- **Run Set & Valuation Run Submission (`POST /v1/run-sets` -> `202 Accepted`):**
  - Standard projection mode: submits `projection_set_id`, `scenario_ids`, `execution_mode`, `is_sandbox`.
  - **M5 Valuation Foundation Mode (Optional Panel on Run Submission):**
    - Basis selector limited to `SUPPORTED_LAUNCH_BASES`: `STATUTORY`, `GAAP`, `FAIR_VALUE` (show `TAX` and `ECONOMIC` disabled as `KNOWN_FUTURE_BASES`).
    - Methodology selector: `GENERIC_PV_FOUNDATION` (show `VM21`, `VM22`, `LDTI`, `DAC`, `MRB`, `ASC815` locked behind `ACT-DEC-01..09` badge).
    - Trusted Provenance Input Selector: pick either an existing completed **Source Projection Run (`source_run_id`)** or a published **Derived Dataset (`derived_dataset_id`)**. **Never** send `base_package_document` from the client.
- **Live Run Polling & Step Lineage (`/runs/:runId`):**
  - Poll `GET /v1/runs/{run_id}` and `GET /v1/runs/{run_id}/steps` every 2 seconds while `status` is in `["queued", "validating", "running", "aggregating"]`.
  - Render the **Step Progress Timeline** from `GET /v1/runs/{run_id}/steps` (`step_key`, `step_name`, `status`, `attempt_number`, `started_at`, `completed_at`, `details`).
  - Render **Run Package & Reproducibility Manifest** drawer (`GET /v1/runs/{run_id}/package` and `GET /v1/runs/{run_id}/manifest`) showing `package_fingerprint`, `build_fingerprint`, `source_commit`, `engine_version`, frozen `inputs`, `formulas`, and `artifacts` (with SHA-256 checksums).
  - Support `POST /v1/runs/{run_id}/cancel` and `POST /v1/runs/{run_id}/retry`.

### Screen 7: Results Explorer, Multi-Segment Inspector, Trace, Reconciliation & Rollforwards (`/analysis/:runId`)
This screen should have **6 tabs**:
1. **Tab 1 — Summary & Portfolio Charts:**
   - Calls `GET /v1/runs/{run_id}/summary` and `GET /v1/runs/{run_id}/aggregates`.
   - Plot time-series curves by `step` (`1..horizon_months`):
     - For `SPIA`: `benefit_payment`, `survival_prob`, `pv_benefits`.
     - For `MYGA`: `account_value_open`, `interest_credited`, `surrender_charge`, `cash_surrender_value`, `account_value_close`.
     - For `FIA` / `RILA`: `total_account_value_close`, `fixed_av_close`, `segment_1_interim_val`, `segment_2_interim_val`, `cash_surrender_value`, `death_benefit`, `interest_credited`, `option_budget`.
   - **Multi-Scenario Comparison Fan Chart:** When viewing a multi-scenario Run Set (`Base`, `Bull`, `Bear`, `Stress`), call `GET /v1/comparisons?baseline_run_id={baseId}&candidate_run_id={otherId}&variable_name=account_value_close` (or overlay the 4 runs in the Run Set) to show the equity path ordering (`Bull > Base > Bear` and `Base != Stress`).
2. **Tab 2 — Multi-Segment Indexed Inspector (New for `FIA` & `RILA`):**
   - Calls `GET /v1/runs/{run_id}/results?policy_id={pid}&page=1&page_size=200`.
   - Filterable by `policy_id`, `variable_name`, `step`, and segment (`segment_1`, `segment_2`, or Portfolio).
   - Displays segment dimension badges on each row: `segment_id` (`segment_1` / `segment_2`), `strategy_type` (`ptp` / `spread` / `buffer` / `floor`), and `index_code` (`SP500` / `RUSSELL2000`).
   - Highlights maturity/rollover months where `segment_1_matured_close === 1.0` or `segment_2_matured_close === 1.0` and shows `segment_1_credited_rate` and `segment_1_sweep`.
   - Includes **"Export Full CSV"** button linked to `GET /v1/runs/{run_id}/export.csv`.
3. **Tab 3 — Cell-by-Cell Actuarial Trace Tree:**
   - Policy picker populated from `GET /v1/runs/{run_id}/trace/policies`.
   - Drill-down trace call: `GET /v1/runs/{run_id}/trace?policy_id={pid}&step={step}&variable_name={var}`.
   - Displays recursive `formula_expression`, `input_values`, `upstream_nodes`, `computed_value`, and (for `FIA`/`RILA` segment variables) `segment_id`, `strategy_type`, and `index_code`.
   - Reverse impact analysis via `GET /v1/runs/{run_id}/trace/dependents?policy_id={pid}&step={step}&variable_name={var}`.
4. **Tab 4 — Expected Results & 3-State Reconciliation:**
   - Manage project benchmarks (`GET/POST /v1/projects/{project_id}/expected-results`, `POST /v1/expected-results/{id}/approve`, `POST /v1/expected-results/{id}/supersede`).
   - Run reconciliation (`POST /v1/runs/{run_id}/reconcile` with optional `{ expected_result_id }`).
   - View reconciliation reports (`GET /v1/runs/{run_id}/reconciliations`, `GET /v1/reconciliations/{id}`):
     - Overall status badge: `PASS` | `FAIL` | `NOT_VERIFIABLE`
     - Summary counters: `total_checks`, `passed_checks`, `failed_checks`, `not_verifiable_checks`, `max_abs_error`
     - Metric breakdown table (`metric_name`, `policy_id`, `step`, `expected_value`, `actual_value`, `abs_diff`, `rel_diff`, `tolerance_abs`, `tolerance_rel`, `status`, `reason`).
   - **Reviewer Disposition Modal (`POST /v1/reconciliations/{reconciliation_id}/disposition`):**
     - Allows a second qualified user (`reviewer` or `actuary` with `models:approve`, where `user.id !== reconciliation.created_by`) to record `action: "accept_exception" | "reject"`, `rationale`, and optional `signoff_reference`.
5. **Tab 5 — M5 Rollforward Templates & Rollforward Jobs (`/rollforwards`):**
   - Create and publish Rollforward Templates (`GET/POST /v1/projects/{project_id}/rollforward-templates`, `POST /v1/rollforward-templates/{id}/versions`, `POST /v1/rollforward-templates/{id}/publish`).
   - Execute and inspect Rollforward Jobs (`GET/POST /v1/projects/{project_id}/rollforward-jobs`, `GET /v1/rollforward-jobs/{job_id}`) showing `opening_balance`, movement components (`interest_credited`, `benefits_paid`, `surrenders`, `fees`, etc.), `closing_balance`, `expected_closing`, `unexplained_movement`, and `validation_status`.
6. **Tab 6 — M5 Derived Datasets & Governed Reports (`/derived-datasets`, `/reports`):**
   - Browse/manage Derived Datasets (`GET/POST /v1/projects/{project_id}/derived-datasets`, `/versions`, `/publish`) and Governed Reports (`GET/POST /v1/projects/{project_id}/reports`, `/versions`, `/publish`).
   - Manage Asset Positions (`GET/POST /v1/asset-positions/`, `PATCH/DELETE /v1/asset-positions/{id}`).

### Screen 8: Governance, Audit Log & Admin Settings (`/settings`)
- User management (`GET/POST /v1/users/`), Project Membership (`GET/POST /v1/projects/{project_id}/members`), Project Archive (`POST /v1/projects/{project_id}/archive`), and immutable Audit Log viewer (`GET /v1/audit-logs/?entity_type=&entity_id=&actor_user_id=&limit=100&offset=0`).

---

## 3. Complete Product Data Dictionaries (`SPIA`, `MYGA`, `FIA`, `RILA`)

### 3.1 Product Overview Matrix

| Product Code | Product Name | Engine Module | Primary Mechanics | Horizon Typical | Required Factor Tables |
|---|---|---|---|---|---|
| `SPIA` | Single Premium Immediate Annuity | `spia_model.py` | Life-contingent monthly annuity payout, mortality decrement (`qx`), survival probability (`px`), discounted PV of benefits | `120`–`360` mo | Mortality / Discount (via assumptions) |
| `MYGA` | Multi-Year Guarantee Annuity | `myga_model.py` | Compound fixed interest crediting (`(1 + credited_rate)^(1/12) - 1`), surrender charge schedule, MVA, dynamic lapse, cash surrender value floor | `60` mo (`12` mo anchor) | `MYGA_SURRENDER`, `MYGA_LAPSE_BASE` |
| `FIA` | Fixed Indexed Annuity | `fia_model.py` + `indexed_model.py` | Fixed sleeve (`fixed_av`) + 2 indexed segments (`segment_1`, `segment_2`) with **Point-to-Point (`ptp`)** or **Spread (`spread`)** crediting; **0% downside floor** (principal protected) | `12`–`72` mo | `FIA_INDEX_PATHS`, `FIA_SURRENDER`, `FIA_OPTION_BUDGET` |
| `RILA` | Registered Index-Linked Annuity | `rila_model.py` + `indexed_model.py` | Fixed sleeve (`fixed_av`) + 2 indexed segments (`segment_1`, `segment_2`) with **Buffer (`buffer`)** or **Floor (`floor`)** downside risk sharing; upside cap & participation | `12`–`72` mo (`72` mo rep block) | `RILA_INDEX_PATHS`, `RILA_SURRENDER`, `RILA_OPTION_BUDGET` |

---

### 3.2 Inforce CSV / Dataset Schemas by Product

#### A. `SPIA` Inforce Columns
| Column | Type | Required | Description / Validation |
|---|---|---|---|
| `policy_id` | `string` | Yes | Unique policy identifier (e.g. `"SPIA-001"`) |
| `product_type` | `string` | Yes | Must be `"SPIA"` |
| `issue_date` | `string` (`YYYY-MM-DD`) | Yes | Contract issue date |
| `issue_age` | `integer` | Yes | `0 <= issue_age <= 120` |
| `gender` | `string` | Yes | `"M"` or `"F"` |
| `premium` | `number` | Yes | `>= 0` |
| `annual_payout` | `number` | Yes | `> 0` (monthly benefit = `annual_payout / 12`) |
| `payout_freq` | `integer` | No | Default `12` |

#### B. `MYGA` Inforce Columns
| Column | Type | Required | Description / Validation |
|---|---|---|---|
| `policy_id` | `string` | Yes | Unique policy identifier (e.g. `"MYGA-001"`) |
| `product_type` | `string` | Yes | Must be `"MYGA"` |
| `issue_date` | `string` (`YYYY-MM-DD`) | Yes | Contract issue date |
| `issue_age` | `integer` | Yes | `0 <= issue_age <= 120` |
| `gender` | `string` | Yes | `"M"` or `"F"` |
| `premium` | `number` | Yes | `>= 0` |
| `account_value` | `number` | Yes | Initial account value `> 0` (alias `av` accepted) |
| `credited_rate` | `number` | Yes | Annual guaranteed credited rate (e.g. `0.045` for 4.5%) |
| `guarantee_period_years` | `integer` | Yes | Guarantee term in years (e.g. `5`) |
| `mva_factor` | `number` | No | Default `1.0` |
| `minimum_guaranteed_surrender_value` | `number` | No | Default `0.0` |

#### C. `FIA` & `RILA` Inforce Columns (`backend/app/services/inforce_registry.py` & `indexed_model.py`)

> **Conservation Invariant (Enforced in Engine & Data Manager):**
> For every row in an `FIA` or `RILA` inforce file:
> $$\texttt{fixed\_av} + \texttt{segment\_1\_initial\_value} + \texttt{segment\_2\_initial\_value} = \texttt{account\_value}$$
> (within `1e-6` tolerance; otherwise fails with error code `INVALID_INDEXED_STATE`).

| Column | Type | Required | Allowed Values / Rules (`FIA` vs `RILA`) |
|---|---|---|---|
| `policy_id` | `string` | Yes | e.g. `"FIA-001"`, `"RILA-001"`..`"RILA-030"` |
| `product_type` | `string` | Yes | `"FIA"` or `"RILA"` |
| `issue_date` | `string` (`YYYY-MM-DD`) | Yes | Contract issue date |
| `issue_age` | `integer` | Yes | `0 <= issue_age <= 120` |
| `gender` | `string` | Yes | `"M"` or `"F"` |
| `premium` | `number` | Yes | Initial single premium `>= 0` |
| `account_value` | `number` | Yes | Total opening account value `> 0` (alias `av` accepted) |
| `fixed_av` | `number` | Yes | Opening fixed account balance `>= 0` |
| `segment_1_base` | `number` | Yes | Segment 1 benefit/calculation base `>= 0` |
| `segment_1_initial_value` | `number` | Yes | Segment 1 opening value `>= 0` |
| `segment_1_strategy` | `string` | Yes | **`FIA`:** `"ptp"` or `"spread"` ONLY<br>**`RILA`:** `"buffer"` or `"floor"` ONLY |
| `segment_1_index_code` | `string` | Yes | `"SP500"` or `"RUSSELL2000"` |
| `segment_1_term_months` | `integer` | Yes | Positive integer (`12`, `36`, etc.) |
| `segment_1_cap` | `number` | Yes | Upside cap rate `>= 0` (e.g. `0.08` for 8%, `0.15` for 15%) |
| `segment_1_downside_rate` | `number` | Yes | `0.0 <= rate <= 1.0` (Buffer/Floor rate for `RILA`, e.g. `0.10` for 10%; `0.0` for `FIA`) |
| `segment_1_start_index` | `number` | Yes | Starting index level `> 0` (typically `100.0`) |
| `segment_2_base` | `number` | Yes | Segment 2 calculation base (`0.0` if inactive) |
| `segment_2_initial_value` | `number` | Yes | Segment 2 opening value (`0.0` if inactive) |
| `segment_2_strategy` | `string` | Yes | **`FIA`:** `"ptp"` or `"spread"`<br>**`RILA`:** `"buffer"` or `"floor"` |
| `segment_2_index_code` | `string` | Yes | `"SP500"` or `"RUSSELL2000"` |
| `segment_2_term_months` | `integer` | Yes | Positive integer (`12`, `36`, etc.) |
| `segment_2_cap` | `number` | Yes | Upside cap rate `>= 0` |
| `segment_2_downside_rate` | `number` | Yes | `0.0 <= rate <= 1.0` |
| `segment_2_start_index` | `number` | Yes | Starting index level `> 0` (`100.0`) |
| `fixed_rate` | `number` | No | Default `0.0`. Annual fixed sleeve rate (`(1 + fixed_rate)^(1/12) - 1` monthly) |
| `gross_withdrawal` | `number` | No | Default `0.0`. Monthly withdrawal amount (`>= 0`) |
| `fee` | `number` | No | Default `0.0`. Monthly contract fee (`>= 0`) |
| `mva_amount` | `number` | No | Default `0.0`. Market value adjustment amount |
| `minimum_guarantee` | `number` | No | Default `0.0`. Minimum guaranteed contract value |
| `path_id` | `integer` | No | Default `0`. Equity path selector (`0`=Base, `1`=Bull, `2`=Bear, `3`=Stress) |
| `withdrawal_mode` | `string` | No | Default `"pro_rata"`. Allowed: `"pro_rata"` or `"dollar_for_dollar"` |
| `interim_method` | `string` | No | Default `"prorated_intrinsic"`. Allowed: `"prorated_intrinsic"` or `"external"` |
| `rollover_rule` | `string` | No | Default `"auto_renew"`. Allowed: `"auto_renew"` or `"sweep_to_fixed"` |
| `death_benefit_rule` | `string` | No | Default `"account_value"`. Allowed: `"account_value"` or `"greater_of_av_and_guarantee"` |
| `segment_1_elapsed` | `integer` | No | Default `0`. Elapsed months in current term (`0 <= elapsed < term_months`) |
| `segment_1_participation_rate` | `number` | No | Default `1.0`. Upside participation rate `>= 0` |
| `segment_1_spread` | `number` | No | Default `0.0`. Index spread `>= 0` (used when strategy is `"spread"`) |
| `segment_1_external_interim` | `number \| null` | No | Default `null`. Required when `interim_method === "external"` |
| `segment_2_elapsed` | `integer` | No | Default `0` |
| `segment_2_participation_rate` | `number` | No | Default `1.0` |
| `segment_2_spread` | `number` | No | Default `0.0` |
| `segment_2_external_interim` | `number \| null` | No | Default `null` |

---

### 3.3 Required Factor Table Schemas (`MYGA`, `FIA`, `RILA`)

When uploading Factor Tables in Data Manager (`dataset_type: "factor"`), use these exact `table_type` and column structures:

| `table_type` | Product | Key Columns (`key_columns`) | Value Column (`value_columns`) | Example Row |
|---|---|---|---|---|
| `MYGA_SURRENDER` | `MYGA` | `["policy_year"]` | `["charge_rate"]` | `{ "policy_year": 1, "charge_rate": 0.08 }` |
| `MYGA_LAPSE_BASE` | `MYGA` | `["policy_year"]` | `["base_lapse_rate"]` | `{ "policy_year": 1, "base_lapse_rate": 0.03 }` |
| `FIA_INDEX_PATHS` | `FIA` | `["path_id", "month", "index_code"]` | `["level"]` | `{ "path_id": 0, "month": 1, "index_code": "SP500", "level": 100.3274 }` |
| `FIA_SURRENDER` | `FIA` | `["policy_year"]` | `["rate"]` | `{ "policy_year": 1, "rate": 0.07 }` |
| `FIA_OPTION_BUDGET` | `FIA` | `["policy_year"]` | `["budget"]` | `{ "policy_year": 1, "budget": 0.025 }` |
| `RILA_INDEX_PATHS` | `RILA` | `["path_id", "month", "index_code"]` | `["level"]` | `{ "path_id": 1, "month": 12, "index_code": "RUSSELL2000", "level": 114.0 }` |
| `RILA_SURRENDER` | `RILA` | `["policy_year"]` | `["rate"]` | `{ "policy_year": 1, "rate": 0.07 }` |
| `RILA_OPTION_BUDGET` | `RILA` | `["policy_year"]` | `["budget"]` | `{ "policy_year": 1, "budget": 0.032 }` |

---

### 3.4 All 25 Published Output Variables for `FIA` & `RILA`

Both `FIA` and `RILA` publish the exact same 25 output variables (`11` portfolio-level + `14` segment-level). Segment-level rows carry `segment_id` (`"segment_1"` or `"segment_2"`), `strategy_type`, and `index_code`.

#### Portfolio-Level Output Variables (11)
| Variable Name | Unit | Aggregation Rule | Description |
|---|---|---|---|
| `account_value_open` | `USD` | `last` | Total policy opening account value at start of monthly step |
| `account_value_close` | `USD` | `last` | Total policy closing account value (`fixed_av_close + segment_1_interim_val + segment_2_interim_val` after maturity/rollover) |
| `total_account_value_close` | `USD` | `last` | Explicit total closing account value across fixed + active segments |
| `fixed_av_close` | `USD` | `last` | Closing balance of the fixed-rate account sleeve |
| `cash_surrender_value` | `USD` | `last` | `max(total_account_value_close * (1 - surrender_rate) + mva_amount, minimum_guarantee)` |
| `death_benefit` | `USD` | `last` | Contract death benefit (`account_value` or `max(account_value, minimum_guarantee)`) |
| `gross_withdrawal` | `USD` | `sum` | Gross withdrawal deducted in step |
| `fee` | `USD` | `sum` | Contract fee deducted in step |
| `interest_credited` | `USD` | `sum` | Total interest + index return credited across fixed & segments in step |
| `net_cash_flow` | `USD` | `sum` | `-(gross_withdrawal + fee)` |
| `option_budget` | `rate` | `last` | Annualized option budget rate from `{code}_OPTION_BUDGET` factor table |

#### Segment-Level Output Variables (14 — 7 for `segment_1`, 7 for `segment_2`)
| Variable Name (`slot` in `segment_1`, `segment_2`) | Unit | Aggregation Rule | `segment_id` Dimension | Description |
|---|---|---|---|---|
| `{slot}_base_close` | `USD` | `last` | `"segment_1"` / `"segment_2"` | Closing segment calculation base after withdrawals, fees, and maturity rollover |
| `{slot}_interim_val` | `USD` | `last` | `"segment_1"` / `"segment_2"` | Interim or maturity-settled market value of the segment |
| `{slot}_elapsed_close` | `month` | `last` | `"segment_1"` / `"segment_2"` | Elapsed months in current segment term (resets to `0` at maturity rollover) |
| `{slot}_start_close` | `index` | `last` | `"segment_1"` / `"segment_2"` | Segment starting index level (resets to current `index_level` on `"auto_renew"`) |
| `{slot}_matured_close` | `indicator` | `last` | `"segment_1"` / `"segment_2"` | `1.0` on the step the segment reaches `term_months`, else `0.0` |
| `{slot}_sweep` | `USD` | `last` | `"segment_1"` / `"segment_2"` | Amount swept from segment into `fixed_av` at maturity when `rollover_rule == "sweep_to_fixed"` |
| `{slot}_credited_rate` | `rate` | `last` | `"segment_1"` / `"segment_2"` | Credited index rate applied across the segment term |

---

### 3.5 Canonical Engineering Anchors & Pending Owner Decisions

#### Engineering Verification Anchors (Use in UI Demo Presets & Smoke Tests)
1. **`SPIA` Anchor (`SPIA-001`):**
   - `issue_age: 65`, `annual_payout: $12,000` (`$1,000/mo`), `qx_annual: 0.012`, `discount_rate_annual: 0.04`.
   - Month 1: `benefit_payment = $1,000.00`, `qx_monthly = 0.0010055385`, `survival_prob = 0.9989944615`, `pv_benefits = $995.736446`.
2. **`MYGA` Anchor (`MYGA-001`):**
   - `account_value: $100,000`, `credited_rate: 0.045` (4.5%), `Year 1 surrender charge: 0.08` (8%).
   - Month 12: `account_value_close = $104,500.00`, `cash_surrender_value = $96,140.00`.
3. **`FIA` & `RILA` Month-12 Split Anchor (`FIA-001` & `RILA-001`):**
   - `account_value: $50,000` split into `fixed_av: $25,000` (`fixed_rate: 0.03`) + `segment_1_initial_value: $25,000` (`term_months: 12`, Base path `+4%` return, `cap: 0.08`).
   - Month 12: Fixed sleeve grows to `$25,750.00` (`+$750.00`), Segment 1 matures at `$26,000.00` (`+$1,000.00`), **`account_value_close = $51,750.00`**, **`interest_credited (cumulative) = $1,750.00`**.
4. **RILA 30-Policy Representative Block (`RILA-001`..`RILA-030`):**
   - 30 policies across `SP500` & `RUSSELL2000`, `buffer` (`10%`/`15%`) & `floor` (`10%`), `12`-month & `36`-month terms, `pro_rata` & `dollar_for_dollar` withdrawals, `auto_renew` & `sweep_to_fixed` rollovers.
   - Executed across 4 scenarios (`Base=0`, `Bull=1`, `Bear=2`, `Stress=3`) for `72` months (`6` years).
   - Strict scenario ordering at Month 72: **`Bull > Base > Bear`** and **`Base != Stress`**.

#### Pending Actuarial Decisions (`ACT-M4-DEC-01`..`15` & `ACT-DEC-01`..`09`)
Render a collapsible **"Actuarial Governance & Pending Decisions"** info badge on `FIA`/`RILA` and Valuation screens listing:
- **M4 Indexed Mechanics (`ACT-M4-DEC-01` through `ACT-M4-DEC-15`):**
  - `ACT-M4-DEC-01..03`: Crediting strategies (`ptp`, `spread`, `buffer`, `floor`), index return formula (`(I_t / I_0) - 1`), and multi-year point-to-point horizon (`12`/`36` months).
  - `ACT-M4-DEC-04..06`: Interim segment valuation (`prorated_intrinsic` vs `external`), segment maturity rollover (`auto_renew` vs `sweep_to_fixed`), and withdrawal reduction (`pro_rata` vs `dollar_for_dollar`).
  - `ACT-M4-DEC-07..15`: Fee allocation, CSV/MVA treatment, death benefit rule, option budget lookup, and representative block calibration.
- **M5E Regulatory Valuation Verticals (`ACT-DEC-01` through `ACT-DEC-09` — GATED):**
  - `VM21`, `VM22`, `LDTI`, `DAC`, `MRB`, and `ASC815` reference calculators remain explicitly gated until formal actuarial sign-off of `ACT-DEC-01`..`09`.

---

## 4. Complete Backend API Reference (All `/v1` Endpoints)

### 4.1 System, Auth, Users, Projects & Audit Logs

#### `GET /v1/system/status`
- **Auth:** None (Public health & capability discovery)
- **Response `200 OK`:**
```json
{
  "status": "ok",
  "app": "MentorAmp Actuarial Modeling Platform",
  "version": "0.1.0",
  "app_env": "development",
  "code_version": "0.1.0",
  "auth_mode": "jwt",
  "database": {
    "status": "ok",
    "latency_ms": 1.42
  },
  "engine": {
    "registered_functions": ["abs", "ceil", "clip", "coalesce", "exp", "floor", "if_else", "log", "max", "min", "pow", "round", "sqrt", "where"],
    "products": ["SPIA", "MYGA", "FIA", "RILA"],
    "execution_backend": "cpu",
    "engine_version": "m3-cpu-dsl-1"
  },
  "time": "2026-11-04T08:00:00Z"
}
```

#### Authentication & Users (`/v1/auth`, `/v1/users`)
| Method & Path | Permission | Request Body / Params | Response |
|---|---|---|---|
| `POST /v1/auth/bootstrap` | Public ( only when 0 users exist) | `{ "email": "admin@mentoramp.com", "full_name": "Admin", "password": "..." }` | `201` `UserResponse` |
| `POST /v1/auth/token` | Public | `application/x-www-form-urlencoded`: `username` (email), `password` | `200` `{ "access_token": "eyJ...", "token_type": "bearer" }` |
| `GET /v1/auth/me` | Authenticated | — | `200` `CurrentUserResponse` (`id`, `email`, `full_name`, `role`, `is_active`, `permissions: string[]`) |
| `GET /v1/users/` | `projects:write` | — | `200` `UserResponse[]` |
| `POST /v1/users/` | `projects:write` | `{ "email": "...", "full_name": "...", "password": "...", "role": "actuary" }` | `201` `UserResponse` |

**Built-in Roles & Permissions Matrix:**
- `admin`: All 10 permissions (`projects:read`, `projects:write`, `registries:read`, `registries:write`, `imports:read`, `imports:write`, `imports:approve`, `models:approve`, `runs:read`, `runs:execute`)
- `actuary`: `projects:read`, `registries:read`, `registries:write`, `imports:read`, `imports:write`, `imports:approve`, `models:approve`, `runs:read`, `runs:execute`
- `model_developer`: `projects:read`, `registries:read`, `registries:write`, `imports:read`, `imports:write`, `runs:read`, `runs:execute`
- `reviewer`: `projects:read`, `registries:read`, `imports:read`, `imports:approve`, `models:approve`, `runs:read`
- `read_only`: `projects:read`, `registries:read`, `imports:read`, `runs:read`

#### Projects & Audit Logs (`/v1/projects`, `/v1/audit-logs`)
| Method & Path | Permission | Request Body / Query Params | Response |
|---|---|---|---|
| `GET /v1/projects/` | `projects:read` | `?include_archived=false` | `200` `ProjectResponse[]` |
| `POST /v1/projects/` | `projects:write` | `{ "name": "Q4 FIA/RILA Valuation", "description": "..." }` | `201` `ProjectResponse` |
| `GET /v1/projects/{project_id}` | `projects:read` | — | `200` `ProjectResponse` |
| `PATCH /v1/projects/{project_id}` | `projects:write` | `{ "name"?: "...", "description"?: "..." }` | `200` `ProjectResponse` |
| `POST /v1/projects/{project_id}/archive` | `projects:write` | `{ "reason": "Year-end close complete" }` | `200` `ProjectResponse` (`status: "archived"`) |
| `GET /v1/projects/{project_id}/members` | `projects:read` | — | `200` `ProjectMemberResponse[]` (`id`, `project_id`, `user_id`, `role`, `created_at`) |
| `POST /v1/projects/{project_id}/members` | `projects:write` | `{ "user_id": "...", "role": "owner" \| "editor" \| "viewer" }` | `201` `ProjectMemberResponse` |
| `GET /v1/audit-logs/` | `projects:read` | `?entity_type=&entity_id=&actor_user_id=&limit=100&offset=0` | `200` `AuditLogResponse[]` (`id`, `actor_user_id`, `action`, `entity_type`, `entity_id`, `metadata_json`, `created_at`) |

---

### 4.2 Products, Product Mappings & Asset Positions (`/v1/products`, `/v1/asset-positions`)

| Method & Path | Permission | Request Body / Query Params | Response |
|---|---|---|---|
| `GET /v1/products/` | `registries:read` | `?project_id=&product_type=&status=&limit=100&offset=0` | `200` `ProductResponse[]` |
| `POST /v1/products/` | `registries:write` | `{ "project_id": "...", "product_code": "RILA-BUF10", "product_name": "RILA 10% Buffer", "product_type": "RILA", "description": "...", "metadata_json": {} }` | `201` `ProductResponse` |
| `GET /v1/products/{product_id}` | `registries:read` | — | `200` `ProductResponse` |
| `PATCH /v1/products/{product_id}` | `registries:write` | `{ "product_name"?: "...", "description"?: "...", "status"?: "active" \| "archived", "metadata_json"?: {} }` | `200` `ProductResponse` |
| `DELETE /v1/products/{product_id}` | `registries:write` | — | `204 No Content` |
| `GET /v1/products/{product_id}/mappings` | `registries:read` | — | `200` `ProductMappingResponse[]` |
| `POST /v1/products/{product_id}/mappings` | `registries:write` | `{ "source_plan_code": "PLN-RILA-01", "mapping_rules": {} }` | `201` `ProductMappingResponse` |
| `DELETE /v1/products/{product_id}/mappings/{mapping_id}` | `registries:write` | — | `204 No Content` |
| `GET /v1/asset-positions/` | `registries:read` | `?project_id=&product_id=&as_of_date=&asset_class=&limit=100&offset=0` | `200` `AssetPositionResponse[]` |
| `POST /v1/asset-positions/` | `registries:write` | `{ "project_id": "...", "product_id"?: "...", "asset_id": "CUSIP-001", "asset_class": "corporate_bond", "as_of_date": "2026-09-30", "book_value": 1000000, "market_value": 985000, "par_value": 1000000, "coupon_rate": 0.052, "maturity_date": "2031-09-30", "credit_rating": "A", "currency": "USD", "metadata_json": {} }` | `201` `AssetPositionResponse` |
| `GET /v1/asset-positions/{position_id}` | `registries:read` | — | `200` `AssetPositionResponse` |
| `PATCH /v1/asset-positions/{position_id}` | `registries:write` | `{ "book_value"?: ..., "market_value"?: ..., "credit_rating"?: ..., "metadata_json"?: {} }` | `200` `AssetPositionResponse` |
| `DELETE /v1/asset-positions/{position_id}` | `registries:write` | — | `204 No Content` |

---

### 4.3 Modeling Registry, Versions, Formula Studio & Variables (`/v1/models`, `/v1/model-versions`, `/v1/formulas`, `/v1/variables`)

#### Models & Model Versions
| Method & Path | Permission | Request Body / Query Params | Response |
|---|---|---|---|
| `GET /v1/projects/{project_id}/models` | `registries:read` | `?product_type=&status=` | `200` `ModelListItemResponse[]` |
| `GET /v1/models` | `registries:read` | `?project_id=&product_type=&status=` | `200` `ModelListItemResponse[]` |
| `POST /v1/models` | `registries:write` | `{ "project_id": "...", "name": "RILA 6Y Indexed Model", "product_type": "RILA", "description": "..." }` | `201` `ModelDetailResponse` (auto-seeds canonical product variables & formulas!) |
| `GET /v1/models/{model_id}` | `registries:read` | — | `200` `ModelDetailResponse` |
| `PATCH /v1/models/{model_id}` | `registries:write` | `{ "name"?: "...", "description"?: "...", "product_type"?: "..." }` | `200` `ModelDetailResponse` |
| `POST /v1/models/{model_id}/clone` | `registries:write` | `{ "new_name": "RILA Model Copy" }` | `201` `ModelDetailResponse` |
| `GET /v1/models/{model_id}/versions` | `registries:read` | — | `200` `ModelVersionResponse[]` |
| `POST /v1/models/{model_id}/versions` | `registries:write` | `{ "version_label": "v2.0", "changelog": "Updated buffer cap", "clone_from_version_id"?: "..." }` | `201` `ModelVersionResponse` |
| `GET /v1/model-versions/{version_id}` | `registries:read` | — | `200` `ModelVersionResponse` |
| `PATCH /v1/model-versions/{version_id}` | `registries:write` | `{ "version_label"?: "...", "changelog"?: "..." }` | `200` `ModelVersionResponse` |
| `POST /v1/model-versions/{version_id}/validate` | `registries:write` | — | `200` `ModelValidationResponse` (`is_valid`, `status`, `errors[]`, `warnings[]`, `execution_order[]`) |
| `POST /v1/model-versions/{version_id}/approve` | `models:approve` | — (Four-eyes enforced: `actor != created_by`) | `200` `ModelVersionResponse` (`status: "approved"`) |
| `POST /v1/model-versions/{version_id}/reject` | `models:approve` | `{ "reason": "Missing surrender floor check" }` | `200` `ModelVersionResponse` (`status: "rejected"`) |
| `POST /v1/model-versions/{version_id}/publish` | `models:approve` | — (Four-eyes enforced: `actor != created_by`) | `200` `ModelVersionResponse` (`status: "published"`) |
| `POST /v1/model-versions/{version_id}/rollback` | `models:approve` | `{ "reason": "Reverting to prior quarter model" }` | `200` `ModelVersionResponse` |
| `POST /v1/model-versions/{version_id}/archive` | `registries:write` | — | `200` `ModelVersionResponse` (`status: "archived"`) |
| `GET /v1/model-versions/{version_id}/dependency-analysis` | `registries:read` | — | `200` `{ "model_version_id": "...", "is_acyclic": true, "execution_order": [...], "edges": [{"from": "...", "to": "..."}], "cycles": [], "unresolved_references": [] }` |
| `GET /v1/model-versions/{version_id}/compare/{other_version_id}` | `registries:read` | — | `200` `{ "base_version_id": "...", "target_version_id": "...", "added_formulas": [...], "removed_formulas": [...], "modified_formulas": [...], "added_variables": [...], "removed_variables": [...] }` |
| `GET /v1/model-versions/{version_id}/structure` | `registries:read` | — | `200` `ModelStructureResponse` (groups, variables, formulas, published_outputs) |
| `GET /v1/model-versions/{version_id}/published-outputs` | `registries:read` | — | `200` `PublishedOutputResponse[]` |
| `PUT /v1/model-versions/{version_id}/published-outputs` | `registries:write` | `{ "outputs": [{ "variable_name": "account_value_close", "display_name": "Closing AV", "unit": "USD", "aggregation_rule": "last" }] }` | `200` `PublishedOutputResponse[]` |

#### Formula Groups, Formulas & Formula Studio
| Method & Path | Permission | Request Body | Response |
|---|---|---|---|
| `GET /v1/model-versions/{version_id}/formula-groups` | `registries:read` | — | `200` `FormulaGroupResponse[]` |
| `POST /v1/model-versions/{version_id}/formula-groups` | `registries:write` | `{ "name": "Segment Valuation", "execution_phase": "projection", "sort_order": 20, "description": "..." }` | `201` `FormulaGroupResponse` |
| `PATCH /v1/formula-groups/{group_id}` | `registries:write` | `{ "name"?: "...", "execution_phase"?: "...", "sort_order"?: 20 }` | `200` `FormulaGroupResponse` |
| `DELETE /v1/formula-groups/{group_id}` | `registries:write` | — | `204 No Content` |
| `POST /v1/formula-groups/{group_id}/formulas` | `registries:write` | `{ "target_variable": "net_return", "expression": "max(index_ret - spread, 0.0)", "description": "...", "sort_order": 1, "test_cases": [{ "name": "Positive net", "inputs": {"index_ret": 0.06, "spread": 0.02}, "expected": 0.04, "tolerance": 1e-6 }] }` | `201` `FormulaResponse` |
| `GET /v1/formulas/{formula_id}` | `registries:read` | — | `200` `FormulaResponse` |
| `GET /v1/formulas/{formula_id}/detail` | `registries:read` | — | `200` `FormulaDetailResponse` (includes `test_cases`, `referenced_variables`, `validation_status`) |
| `PATCH /v1/formulas/{formula_id}` | `registries:write` | `{ "target_variable"?: "...", "expression"?: "...", "description"?: "...", "sort_order"?: 1, "test_cases"?: [...] }` | `200` `FormulaResponse` |
| `DELETE /v1/formulas/{formula_id}` | `registries:write` | — | `204 No Content` |
| `POST /v1/formulas/validate-expression` | `registries:read` | `{ "expression": "min(max(ret, -0.10), cap)", "available_variables": ["ret", "cap"] }` | `200` `{ "is_valid": true, "referenced_variables": ["cap", "ret"], "errors": [], "warnings": [] }` |
| `POST /v1/formulas/preview` | `registries:read` | `{ "expression": "min(max(ret, -0.10), cap)", "inputs": { "ret": 0.14, "cap": 0.12 } }` | `200` `{ "expression": "...", "inputs": {...}, "result": 0.12, "error": null }` |
| `POST /v1/formulas/{formula_id}/run-tests` | `registries:read` | `{ "test_cases"?: [...] }` (optional override; uses saved `test_cases` if omitted) | `200` `{ "formula_id": "...", "total": 2, "passed": 2, "failed": 0, "results": [{ "name": "...", "passed": true, "expected": 0.12, "actual": 0.12, "abs_diff": 0.0, "error": null }] }` |
| `GET /v1/formulas/{formula_id}/dependencies` | `registries:read` | — | `200` `{ "formula_id": "...", "target_variable": "...", "upstream_variables": [...], "downstream_formulas": [...], "execution_wave": 2 }` |

#### Variables Catalog
| Method & Path | Permission | Request Body / Params | Response |
|---|---|---|---|
| `GET /v1/variables` | `registries:read` | `?project_id=&model_version_id=&variable_type=&category=` | `200` `VariableResponse[]` |
| `GET /v1/model-versions/{version_id}/variables` | `registries:read` | — | `200` `VariableResponse[]` |
| `POST /v1/model-versions/{version_id}/variables` | `registries:write` | `{ "name": "segment_1_interim_val", "display_name": "Segment 1 Interim Value", "data_type": "float", "variable_type": "intermediate", "category": "indexed_segment", "unit": "USD", "default_value": "0.0", "is_published_output": true, "aggregation_rule": "last", "description": "..." }` | `201` `VariableResponse` |
| `PATCH /v1/variables/{variable_id}` | `registries:write` | Partial `VariableCreateRequest` | `200` `VariableResponse` |
| `DELETE /v1/variables/{variable_id}` | `registries:write` | — | `204 No Content` |

---

### 4.4 Data Manager, Import Sessions & Input Library (`/v1/import-sessions`, `/v1/datasets`, `/v1/inputs`, `/v1/imports`)

#### Import Sessions & Validation
| Method & Path | Permission | Request Body / Params | Response |
|---|---|---|---|
| `POST /v1/import-sessions` | `imports:write` | `{ "project_id": "...", "name": "RILA 30-Policy Block", "dataset_type": "inforce" \| "assumption" \| "factor" \| "scenario", "product_type": "RILA", "table_type"?: "RILA_INDEX_PATHS" }` | `201` `ImportSessionResponse` |
| `GET /v1/projects/{project_id}/import-sessions` | `imports:read` | `?dataset_type=&status=` | `200` `ImportSessionResponse[]` |
| `GET /v1/import-sessions/{session_id}` | `imports:read` | — | `200` `ImportSessionResponse` |
| `POST /v1/import-sessions/{session_id}/files` | `imports:write` | `multipart/form-data`: `file` (CSV/JSON/Parquet) | `200` `ImportSessionResponse` (`status: "uploaded"`, `row_count`, `detected_columns`) |
| `GET /v1/import-sessions/{session_id}/preview` | `imports:read` | `?limit=20` | `200` `{ "session_id": "...", "columns": [...], "rows": [...], "total_rows": 30 }` |
| `GET /v1/import-sessions/{session_id}/file` | `imports:read` | — | `200` Raw uploaded file download |
| `PUT /v1/import-sessions/{session_id}/mapping` | `imports:write` | `{ "column_mapping": { "PolicyID": "policy_id", "AV": "account_value" }, "defaults": {}, "key_columns"?: ["path_id", "month", "index_code"], "value_columns"?: ["level"] }` | `200` `ImportSessionResponse` (`status: "mapped"`) |
| `POST /v1/import-sessions/{session_id}/validate` | `imports:write` | — | `200` `ValidationRunResponse` (`id`, `status: "passed" \| "failed"`, `total_rows`, `valid_rows`, `error_count`, `warning_count`, `summary`) |
| `GET /v1/validation-runs/{validation_id}/issues` | `imports:read` | `?severity=&limit=100&offset=0` | `200` `ValidationIssueResponse[]` (`row_number`, `column_name`, `severity`, `code`, `message`) |
| `GET /v1/validation-runs/{validation_id}/rejected-records` | `imports:read` | `?limit=100&offset=0` | `200` `{ "validation_id": "...", "rejected_records": [...] }` |
| `POST /v1/import-sessions/{session_id}/commit` | `imports:write` | `{ "dataset_name"?: "...", "description"?: "..." }` | `200` `ImportCommitResponse` (`session_id`, `status: "committed"`, `committed_dataset_id`, `dataset_type`) |
| `GET /v1/import-sessions/{session_id}/log` | `imports:read` | — | `200` Audit/event log for the session |

#### Dataset Approval, Versioning & Input Browsing
| Method & Path | Permission | Request Body / Params | Response |
|---|---|---|---|
| `POST /v1/datasets/{dataset_kind}/{dataset_id}/approve` | `imports:approve` | `dataset_kind` in `inforce \| assumption \| factor \| scenario`. Four-eyes enforced (`actor != created_by`). | `200` Dataset object (`governance_status: "approved"`) |
| `POST /v1/datasets/{dataset_kind}/{dataset_id}/reject` | `imports:approve` | `{ "reason": "Incorrect cap column values" }` | `200` Dataset object (`governance_status: "rejected"`) |
| `GET /v1/datasets/{dataset_kind}/{dataset_id}/versions` | `imports:read` | — | `200` Version lineage list |
| `GET /v1/datasets/{dataset_kind}/{dataset_id}/compare` | `imports:read` | `?other_id={other_dataset_id}` | `200` `{ "base_id": "...", "other_id": "...", "row_count_diff": 0, "added_columns": [], "removed_columns": [], "changed_rows_sample": [...] }` |
| `GET /v1/projects/{project_id}/inputs` | `imports:read` | `?product_type=` | `200` `{ "inforce_files": [...], "assumption_tables": [...], "factor_tables": [...] }` |
| `GET /v1/projects/{project_id}/input-mappings` | `imports:read` | — | `200` Saved column mappings for project |
| `GET /v1/projects/{project_id}/validation-issues` | `imports:read` | — | `200` Recent validation issues across project |
| `GET /v1/inputs/inforce/{file_id}` | `imports:read` | — | `200` `InforceFileDetailResponse` |
| `GET /v1/inputs/inforce/{file_id}/records` | `imports:read` | `?limit=100&offset=0` | `200` Paginated inforce policy rows |
| `GET /v1/imports/inforce/{file_id}/records` | `imports:read` | `?page=1&page_size=50&filter_column=&filter_operator=eq\|neq\|gt\|gte\|lt\|lte\|contains&filter_value=&sort_by=policy_id&sort_order=asc\|desc` | `200` `{ "items": [...], "total": 30, "page": 1, "page_size": 50 }` |
| `GET /v1/inputs/assumption-tables/{table_id}` | `imports:read` | — | `200` `AssumptionTableDetailResponse` |
| `GET /v1/inputs/assumptions/{table_id}/records` | `imports:read` | `?limit=100&offset=0` | `200` Assumption rows |
| `GET /v1/inputs/factor-tables/{table_id}` | `imports:read` | — | `200` `FactorTableDetailResponse` |
| `GET /v1/inputs/factors/{table_id}/records` | `imports:read` | `?limit=500&offset=0` | `200` Factor rows |

---

### 4.5 Scenarios & Projection Sets (`/v1/scenarios`, `/v1/projection-sets`)

| Method & Path | Permission | Request Body / Params | Response |
|---|---|---|---|
| `GET /v1/projects/{project_id}/scenarios` | `registries:read` | — | `200` `ScenarioResponse[]` |
| `POST /v1/projects/{project_id}/scenarios` | `registries:write` | `{ "name": "Bull Equity Path", "scenario_type": "deterministic", "description": "Path 1 Bull", "overrides": [{ "target_variable": "path_id", "operation": "set", "value": 1, "start_step": 1, "end_step": 72 }] }` | `201` `ScenarioResponse` |
| `GET /v1/scenarios/{scenario_id}` | `registries:read` | — | `200` `ScenarioResponse` |
| `PATCH /v1/scenarios/{scenario_id}` | `registries:write` | `{ "name"?: "...", "description"?: "...", "overrides"?: [...] }` | `200` `ScenarioResponse` |
| `POST /v1/scenarios/{scenario_id}/validate` | `registries:write` | — | `200` `{ "scenario_id": "...", "is_valid": true, "status": "validated", "errors": [] }` |
| `POST /v1/scenarios/{scenario_id}/duplicate` | `registries:write` | `{ "new_name": "Bear Equity Path" }` | `201` `ScenarioResponse` |
| `GET /v1/projects/{project_id}/projection-sets` | `registries:read` | — | `200` `ProjectionSetResponse[]` |
| `POST /v1/projects/{project_id}/projection-sets` | `registries:write` | `{ "name": "RILA 72M 4-Scenario Set", "model_version_id": "...", "inforce_file_id": "...", "assumption_table_ids": [], "factor_table_ids": ["...", "...", "..."], "scenario_ids": ["...", "...", "...", "..."], "horizon_months": 72, "step_size": "monthly", "description": "..." }` | `201` `ProjectionSetResponse` |
| `GET /v1/projection-sets/{projection_set_id}` | `registries:read` | — | `200` `ProjectionSetResponse` |
| `PATCH /v1/projection-sets/{projection_set_id}` | `registries:write` | Partial update | `200` `ProjectionSetResponse` |
| `POST /v1/projection-sets/{projection_set_id}/validate` | `registries:write` | — | `200` `{ "projection_set_id": "...", "is_valid": true, "status": "validated", "checks": [...] }` |
| `POST /v1/projection-sets/{projection_set_id}/duplicate` | `registries:write` | `{ "new_name": "..." }` | `201` `ProjectionSetResponse` |
| `POST /v1/projection-sets/{projection_set_id}/scenarios` | `registries:write` | `{ "scenario_ids": ["..."] }` | `200` `ProjectionSetResponse` |

---

### 4.6 Execution Center: Run Sets, Runs, Run Steps & RunPackage (`/v1/run-sets`, `/v1/runs`)

| Method & Path | Permission | Request Body / Params | Response |
|---|---|---|---|
| `GET /v1/projects/{project_id}/run-sets` | `runs:read` | — | `200` `RunSetResponse[]` |
| `POST /v1/run-sets/preflight` | `runs:execute` | `{ "project_id": "...", "projection_set_id": "...", "scenario_ids"?: [...], "is_sandbox": false }` | `200` `{ "ready": true, "is_sandbox": false, "checks": [{ "code": "MODEL_PUBLISHED", "passed": true, "message": "..." }] }` |
| `POST /v1/run-sets` | `runs:execute` | `{ "project_id": "...", "name": "RILA 72M Block Run", "projection_set_id": "...", "scenario_ids"?: [...], "execution_mode": "sync" \| "async", "is_sandbox": false }` | `202 Accepted` `RunSetResponse` (contains `runs: CanonicalRunResponse[]`) |
| `GET /v1/run-sets/{run_set_id}` | `runs:read` | — | `200` `RunSetResponse` |
| `GET /v1/runs` | `runs:read` | `?project_id=&run_set_id=&status=&limit=100&offset=0` | `200` `CanonicalRunResponse[]` |
| `GET /v1/runs/{run_id}` | `runs:read` | — | `200` `CanonicalRunResponse` |
| `POST /v1/runs/{run_id}/cancel` | `runs:execute` | — | `200` `CanonicalRunResponse` (`status: "cancelled"`) |
| `POST /v1/runs/{run_id}/retry` | `runs:execute` | — | `200` `CanonicalRunResponse` (`attempt_count` incremented) |
| `GET /v1/runs/{run_id}/events` | `runs:read` | — | `200` `RunEventResponse[]` (`id`, `run_id`, `event_type`, `message`, `payload_json`, `created_at`) |
| `GET /v1/runs/{run_id}/attempts` | `runs:read` | — | `200` `RunAttemptResponse[]` |
| `GET /v1/runs/{run_id}/steps` | `runs:read` | — | `200` `RunStepResponse[]` (`id`, `run_id`, `step_key`, `step_name`, `status`, `attempt_number`, `started_at`, `completed_at`, `error_message`, `details`) |
| `GET /v1/runs/{run_id}/package` | `runs:read` | — | `200` `RunPackageResponse` (Immutable frozen RunPackage with `package_fingerprint`, `configuration.build`, `inputs`, `formulas`, `scenarios`, `artifacts`) |
| `GET /v1/runs/{run_id}/manifest` | `runs:read` | — | `200` Reproducibility manifest JSON |

---

### 4.7 Results, Aggregates, CSV Export, Trace & Run Comparisons (`/v1/runs/{run_id}/...`, `/v1/comparisons`)

| Method & Path | Permission | Query Params | Response |
|---|---|---|---|
| `GET /v1/projects/{project_id}/dashboard` | `projects:read` | — | `200` `ProjectDashboardResponse` |
| `GET /v1/runs/{run_id}/summary` | `runs:read` | — | `200` `RunSummaryResponse` (`run_id`, `status`, `policy_count`, `horizon_months`, `scenario_name`, `headline_metrics: Record<string, number>`, `artifacts`) |
| `GET /v1/runs/{run_id}/results` | `runs:read` | `?policy_id=&variable_name=&step=&page=1&page_size=100` | `200` `PaginatedResultsResponse` (`items: ResultRowResponse[]`, `total`, `page`, `page_size`) |
| `GET /v1/runs/{run_id}/aggregates` | `runs:read` | `?variable_name=&step=` | `200` `AggregateRowResponse[]` (`step`, `variable_name`, `aggregation_rule`, `value`, `policy_count`) |
| `GET /v1/runs/{run_id}/export.csv` | `runs:read` | `?policy_id=&variable_name=` | `200` `text/csv` stream (includes `run_id,policy_id,step,variable_name,value,segment_id,strategy_type,index_code`) |
| `GET /v1/runs/{run_id}/trace/policies` | `runs:read` | — | `200` `{ "run_id": "...", "policies": ["RILA-001", "RILA-002", ...] }` |
| `GET /v1/runs/{run_id}/trace` | `runs:read` | `?policy_id=RILA-001&step=12&variable_name=account_value_close` | `200` `TraceNodeResponse` (`run_id`, `policy_id`, `step`, `variable_name`, `computed_value`, `formula_expression`, `input_values`, `upstream_nodes`, `segment_id`, `strategy_type`, `index_code`) |
| `GET /v1/runs/{run_id}/trace/dependents` | `runs:read` | `?policy_id=RILA-001&step=12&variable_name=segment_1_interim_val` | `200` `{ "run_id": "...", "policy_id": "...", "step": 12, "variable_name": "...", "dependents": [...] }` |
| `GET /v1/comparisons` | `runs:read` | `?baseline_run_id={id1}&candidate_run_id={id2}&variable_name=account_value_close` | `200` `RunComparisonResponse` (`baseline_run_id`, `candidate_run_id`, `variable_name`, `diffs_by_step: [{ step, baseline_value, candidate_value, abs_diff, rel_diff }]`, `max_abs_diff`, `max_rel_diff`) |

---

### 4.8 Expected Results, 3-State Reconciliation & Disposition (`/v1/expected-results`, `/v1/reconciliations`)

| Method & Path | Permission | Request Body / Params | Response |
|---|---|---|---|
| `POST /v1/projects/{project_id}/expected-results` | `registries:write` | `{ "name": "FIA-001 Month 12 Anchor", "product_type": "FIA", "model_version_id"?: "...", "source_type": "actuarial_spreadsheet", "source_reference": "FIA_Anchor_v1.xlsx", "tolerance_abs": 0.01, "tolerance_rel": 0.0001, "metrics": [{ "metric_name": "account_value_close", "policy_id": "FIA-001", "step": 12, "expected_value": 51750.00, "tolerance_abs": 0.01, "tolerance_rel": 0.0001 }] }` | `201` `ExpectedResultResponse` (`status: "draft"`) |
| `GET /v1/projects/{project_id}/expected-results` | `registries:read` | `?product_type=&status=` | `200` `ExpectedResultResponse[]` |
| `GET /v1/expected-results/{expected_result_id}` | `registries:read` | — | `200` `ExpectedResultResponse` |
| `POST /v1/expected-results/{expected_result_id}/approve` | `models:approve` | — (Four-eyes enforced: `actor != created_by`) | `200` `ExpectedResultResponse` (`status: "approved"`) |
| `POST /v1/expected-results/{expected_result_id}/supersede` | `registries:write` | `{ "reason": "Updated cap benchmark" }` | `200` `ExpectedResultResponse` (`status: "superseded"`) |
| `POST /v1/runs/{run_id}/reconcile` | `runs:execute` | `{ "expected_result_id"?: "..." }` | `200` `ReconciliationDetailResponse` (`status: "PASS" \| "FAIL" \| "NOT_VERIFIABLE"`) |
| `GET /v1/runs/{run_id}/reconciliations` | `runs:read` | — | `200` `ReconciliationSummaryResponse[]` |
| `GET /v1/reconciliations/{reconciliation_id}` | `runs:read` | — | `200` `ReconciliationDetailResponse` |
| `POST /v1/reconciliations/{reconciliation_id}/disposition` | `models:approve` | `{ "action": "accept_exception" \| "reject", "rationale": "Rounding difference on M12 option budget", "signoff_reference"?: "ACT-SIGNOFF-2026-11" }` (Four-eyes enforced) | `200` `ReconciliationDetailResponse` |

---

### 4.9 M5 Governance: Rollforward Templates, Rollforward Jobs, Derived Datasets & Reports (`/v1/projects/{project_id}/...`)

#### Rollforward Templates & Jobs
| Method & Path | Permission | Request Body | Response |
|---|---|---|---|
| `GET /v1/projects/{project_id}/rollforward-templates` | `registries:read` | — | `200` `RollforwardTemplateResponse[]` |
| `POST /v1/projects/{project_id}/rollforward-templates` | `registries:write` | `{ "name": "Standard Reserve Rollforward", "basis": "STATUTORY", "description": "Opening + Credited - Surrenders = Closing", "movement_components": ["interest_credited", "gross_withdrawal", "fee"], "tolerance_abs": 0.01 }` | `201` `RollforwardTemplateResponse` |
| `GET /v1/rollforward-templates/{template_id}` | `registries:read` | — | `200` `RollforwardTemplateResponse` |
| `PATCH /v1/rollforward-templates/{template_id}` | `registries:write` | Partial update | `200` `RollforwardTemplateResponse` |
| `DELETE /v1/rollforward-templates/{template_id}` | `registries:write` | — | `204 No Content` |
| `POST /v1/rollforward-templates/{template_id}/versions` | `registries:write` | `{ "version_label": "v1.1", "definition_json": { "movement_components": ["interest_credited", "gross_withdrawal", "fee"] } }` | `201` `RollforwardTemplateResponse` |
| `POST /v1/rollforward-templates/{template_id}/publish` | `models:approve` | — | `200` `RollforwardTemplateResponse` (`status: "published"`) |
| `GET /v1/projects/{project_id}/rollforward-jobs` | `runs:read` | — | `200` `RollforwardJobResponse[]` |
| `POST /v1/projects/{project_id}/rollforward-jobs` | `runs:execute` | `{ "template_id": "...", "opening_run_id": "...", "closing_run_id": "...", "name": "Q3 to Q4 Rollforward" }` | `201` `RollforwardJobResponse` (computes real movements from persisted Run artifacts; fails if unexplained movement > tolerance) |
| `GET /v1/rollforward-jobs/{job_id}` | `runs:read` | — | `200` `RollforwardJobResponse` |
| `PATCH /v1/rollforward-jobs/{job_id}` | `runs:execute` | `{ "status"?: "...", "notes"?: "..." }` | `200` `RollforwardJobResponse` |

#### Derived Datasets & Reports
| Method & Path | Permission | Request Body | Response |
|---|---|---|---|
| `GET /v1/projects/{project_id}/derived-datasets` | `registries:read` | — | `200` `DerivedDatasetResponse[]` |
| `POST /v1/projects/{project_id}/derived-datasets` | `registries:write` | `{ "name": "Aggregated RILA Cashflows", "source_run_id": "...", "source_artifact_id": "...", "dataset_kind": "valuation_input", "schema_json": {}, "row_count": 72 }` | `201` `DerivedDatasetResponse` |
| `GET /v1/derived-datasets/{dataset_id}` | `registries:read` | — | `200` `DerivedDatasetResponse` |
| `PATCH /v1/derived-datasets/{dataset_id}` | `registries:write` | Partial update | `200` `DerivedDatasetResponse` |
| `DELETE /v1/derived-datasets/{dataset_id}` | `registries:write` | — | `204 No Content` |
| `POST /v1/derived-datasets/{dataset_id}/versions` | `registries:write` | `{ "version_label": "v1.1", "source_run_id": "...", "source_artifact_id": "..." }` | `201` `DerivedDatasetResponse` |
| `POST /v1/derived-datasets/{dataset_id}/publish` | `models:approve` | — | `200` `DerivedDatasetResponse` (`status: "published"`) |
| `GET /v1/projects/{project_id}/reports` | `runs:read` | — | `200` `ReportResponse[]` |
| `POST /v1/projects/{project_id}/reports` | `runs:execute` | `{ "name": "Q4 Valuation Executive Summary", "report_type": "valuation_summary", "source_run_id": "...", "reconciliation_id"?: "...", "rollforward_job_id"?: "...", "content_json": {} }` | `201` `ReportResponse` |
| `GET /v1/reports/{report_id}` | `runs:read` | — | `200` `ReportResponse` |
| `PATCH /v1/reports/{report_id}` | `runs:execute` | Partial update | `200` `ReportResponse` |
| `DELETE /v1/reports/{report_id}` | `runs:execute` | — | `204 No Content` |
| `POST /v1/reports/{report_id}/versions` | `runs:execute` | `{ "version_label": "v2", "content_json": {} }` | `201` `ReportResponse` |
| `POST /v1/reports/{report_id}/publish` | `models:approve` | — | `200` `ReportResponse` (`status: "published"`) |

---

## 5. Ready-to-Use TypeScript Interfaces (`src/types/mentoramp-api.ts`)

Copy-paste these interfaces directly into `MentorAmp-frontend/src/types/mentoramp-api.ts`:

```typescript
export type ProductType = "SPIA" | "MYGA" | "FIA" | "RILA";

export type UserRole = "admin" | "actuary" | "model_developer" | "reviewer" | "read_only";

export type Permission =
  | "projects:read"
  | "projects:write"
  | "registries:read"
  | "registries:write"
  | "imports:read"
  | "imports:write"
  | "imports:approve"
  | "models:approve"
  | "runs:read"
  | "runs:execute";

export type ModelVersionStatus =
  | "draft"
  | "validated"
  | "approved"
  | "published"
  | "rejected"
  | "archived";

export type DatasetGovernanceStatus = "draft" | "committed" | "approved" | "rejected";

export type CanonicalRunStatus =
  | "queued"
  | "validating"
  | "running"
  | "aggregating"
  | "succeeded"
  | "failed"
  | "cancelled";

export type ReconciliationStatus = "PASS" | "FAIL" | "NOT_VERIFIABLE";

export type SupportedValuationBasis = "STATUTORY" | "GAAP" | "FAIR_VALUE";
export type DeferredValuationBasis = "TAX" | "ECONOMIC";

export type FIAStrategyType = "ptp" | "spread";
export type RILAStrategyType = "buffer" | "floor";
export type IndexedStrategyType = FIAStrategyType | RILAStrategyType;
export type IndexCode = "SP500" | "RUSSELL2000";

export interface SystemStatusResponse {
  status: "ok" | "degraded";
  app: string;
  version: string;
  app_env: string;
  code_version: string;
  auth_mode: string;
  database: {
    status: string;
    latency_ms: number | null;
  };
  engine: {
    registered_functions: string[];
    products: ProductType[];
    execution_backend: string;
    engine_version: string;
  };
  time: string;
}

export interface CurrentUserResponse {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  permissions: Permission[];
}

export interface ResultRowResponse {
  run_id: string;
  policy_id: string;
  step: number;
  variable_name: string;
  value: number;
  segment_id?: "segment_1" | "segment_2" | null;
  strategy_type?: IndexedStrategyType | null;
  index_code?: IndexCode | null;
}

export interface TraceNodeResponse {
  run_id: string;
  policy_id: string;
  step: number;
  variable_name: string;
  computed_value: number;
  formula_expression: string | null;
  input_values: Record<string, number | string | null>;
  upstream_nodes: TraceNodeResponse[];
  segment_id?: "segment_1" | "segment_2" | null;
  strategy_type?: IndexedStrategyType | null;
  index_code?: IndexCode | null;
}

export interface ReconciliationMetricCheck {
  metric_name: string;
  policy_id: string | null;
  step: number | null;
  expected_value: number | null;
  actual_value: number | null;
  abs_diff: number | null;
  rel_diff: number | null;
  tolerance_abs: number;
  tolerance_rel: number;
  status: ReconciliationStatus;
  reason: string | null;
}

export interface ReconciliationDetailResponse {
  id: string;
  run_id: string;
  expected_result_id: string | null;
  status: ReconciliationStatus;
  total_checks: number;
  passed_checks: number;
  failed_checks: number;
  not_verifiable_checks: number;
  max_abs_error: number | null;
  checks: ReconciliationMetricCheck[];
  disposition_status: "pending" | "accepted_exception" | "rejected" | null;
  disposition_by: string | null;
  disposition_rationale: string | null;
  disposition_at: string | null;
  created_by: string | null;
  created_at: string;
}

export interface RunStepResponse {
  id: string;
  run_id: string;
  step_key: string;
  step_name: string;
  status: "pending" | "running" | "succeeded" | "failed" | "skipped";
  attempt_number: number;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
  details: Record<string, unknown>;
}

export interface RollforwardJobResponse {
  id: string;
  project_id: string;
  template_id: string;
  opening_run_id: string;
  closing_run_id: string;
  name: string;
  status: "pending" | "running" | "succeeded" | "failed";
  opening_balance: number;
  closing_balance: number;
  expected_closing: number;
  unexplained_movement: number;
  movements_json: Record<string, number>;
  validation_status: "PASS" | "FAIL";
  created_by: string | null;
  created_at: string;
}

export interface DerivedDatasetResponse {
  id: string;
  project_id: string;
  name: string;
  version: number;
  status: "draft" | "published" | "archived";
  dataset_kind: string;
  source_run_id: string;
  source_artifact_id: string;
  fingerprint: string;
  row_count: number;
  storage_uri: string | null;
  schema_json: Record<string, unknown>;
  created_by: string | null;
  created_at: string;
}
```

---

## 6. Error Envelope & Domain Error Code Matrix

All backend errors return a structured JSON envelope:
```json
{
  "detail": "Human-readable explanation of what failed",
  "code": "MACHINE_READABLE_ERROR_CODE",
  "details": {}
}
```

| HTTP Status | `code` | Trigger Condition | Recommended Frontend Handling |
|---|---|---|---|
| `400` / `422` | `INVALID_INDEXED_STATE` | `FIA`/`RILA` inforce row violates `fixed_av + segment_1_initial_value + segment_2_initial_value == account_value`, or uses wrong strategy (`buffer`/`floor` on `FIA`, `ptp`/`spread` on `RILA`), or missing required factor table | Highlight the offending policy row/column in Data Manager or Run Preflight modal |
| `400` / `422` | `VALIDATION_ERROR` | DSL syntax error, cyclic formula graph, or missing required input columns | Show inline field/formula errors |
| `400` | `TAXONOMY_BASIS_DEFERRED` | Caller requested `TAX` or `ECONOMIC` valuation basis | Keep `TAX` and `ECONOMIC` disabled in the UI with *"Deferred to Post-Launch"* badge |
| `400` | `VALUATION_VERTICAL_GATED` | Caller requested gated M5E regulatory vertical (`VM21`, `VM22`, `LDTI`, `DAC`, `MRB`, `ASC815`) before `ACT-DEC-01..09` sign-off | Display modal/badge explaining `ACT-DEC-01..09` actuarial sign-off gate |
| `400` | `UNTRUSTED_PROVENANCE_FORBIDDEN` | Caller included `base_package_document` or untrusted provenance fields in valuation run submission | Never send `base_package_document`; pass `source_run_id` or `derived_dataset_id` |
| `400` | `IMMUTABLE_BUILD_IDENTITY_MISSING` | Server lacks immutable build identity for governed RunPackage freeze | Show environment configuration alert banner |
| `401` | `UNAUTHORIZED` | Missing or expired Bearer JWT token | Redirect to `/login` and clear local token |
| `403` | `FORBIDDEN` | User role lacks required permission (e.g. `runs:execute`, `models:approve`) | Disable unauthorized buttons proactively based on `currentUser.permissions` |
| `403` | `SELF_APPROVAL_FORBIDDEN` | User attempted to approve/publish/disposition a ModelVersion, Dataset, ExpectedResult, or Reconciliation they created | Disable Approve/Publish button when `currentUser.id === item.created_by` and show Four-Eyes tooltip |
| `404` | `NOT_FOUND` | Requested entity does not exist or belongs to another project | Show 404 state or toast notification |
| `409` | `CONFLICT` / `INVALID_STATE_TRANSITION` | Attempted invalid lifecycle transition (e.g., publishing an unapproved model version, or modifying a frozen RunPackage) | Refresh entity state and show current lifecycle status |

---

## 7. End-to-End Frontend Verification Checklist for Sam

Before marking the M3/M4/M5 frontend integration complete, verify these 7 end-to-end flows against the running backend (`http://localhost:8000`):

- [ ] **1. Dynamic Product Discovery (`SPIA`, `MYGA`, `FIA`, `RILA`):**
  - App loads `GET /v1/system/status` and populates all 4 product types in the Model Library, Data Manager, and Product Registry.
- [ ] **2. Formula Studio Live Authoring & Unit Testing:**
  - Open an `FIA` or `RILA` draft model version, edit a formula expression, verify live `POST /v1/formulas/validate-expression` feedback, evaluate a sample preview via `POST /v1/formulas/preview`, and run formula unit tests via `POST /v1/formulas/{id}/run-tests`.
- [ ] **3. Four-Eyes Governance Across Models, Datasets & Benchmarks:**
  - Log in as `model_developer` (User A) -> create & validate a model version and commit an inforce dataset -> verify that the **Approve** and **Publish** buttons are disabled for User A with the Four-Eyes tooltip.
  - Log in as `actuary` / `reviewer` (User B) -> approve and publish the model version and approve the dataset.
- [ ] **4. M4 `FIA-001` & `RILA-001` Month-12 Split Anchor ($51,750.00):**
  - Upload a 1-policy `FIA-001` or `RILA-001` inforce file (`$25,000` fixed @ 3% + `$25,000` segment 1 @ 4% Base path) + required factor tables (`{code}_INDEX_PATHS`, `{code}_SURRENDER`, `{code}_OPTION_BUDGET`).
  - Run a 12-month projection and verify Month 12 `account_value_close = $51,750.00`, `fixed_av_close = $25,750.00`, `segment_1_interim_val = $26,000.00`, and `interest_credited = $1,750.00`.
- [ ] **5. M4 30-Policy RILA Representative Block (4 Scenarios × 72 Months):**
  - Create a Projection Set with the 30-policy RILA block and the 4 equity path scenarios (`Base=0`, `Bull=1`, `Bear=2`, `Stress=3`) over `72` months.
  - Launch the Run Set, watch live step progress (`GET /v1/runs/{id}/steps`), inspect the Multi-Segment Inspector (`segment_1` & `segment_2` with `strategy_type` and `index_code`), drill into the Cell-by-Cell Trace Tree (`GET /v1/runs/{id}/trace`), and verify on the Comparison Chart that Month 72 `account_value_close` satisfies `Bull > Base > Bear` and `Base != Stress`.
- [ ] **6. 3-State Reconciliation (`PASS`, `FAIL`, `NOT_VERIFIABLE`) & Reviewer Disposition:**
  - Run `POST /v1/runs/{id}/reconcile` without an approved benchmark -> verify amber `NOT_VERIFIABLE` badge.
  - Approve an Expected Result Benchmark (`$51,750.00` for `FIA-001` at Month 12) -> rerun reconciliation -> verify green `PASS` badge.
  - Test a deliberate mismatch (`FAIL`) and submit a second-user Reviewer Disposition (`POST /v1/reconciliations/{id}/disposition`).
- [ ] **7. M5 Rollforward Jobs, Derived Datasets & Reports:**
  - Create & publish a Rollforward Template, execute a Rollforward Job between two runs (`POST /v1/projects/{id}/rollforward-jobs`), verify `unexplained_movement == 0.0` and `validation_status == "PASS"`, and publish a Derived Dataset and Governed Report.
