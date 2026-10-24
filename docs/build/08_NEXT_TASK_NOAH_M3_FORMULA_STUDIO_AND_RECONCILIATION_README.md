# NEXT TASK FOR NOAH — M3: GOVERNED FORMULA STUDIO DSL, EXPECTED-RESULT RECONCILIATION & MULTI-PRODUCT DEMO SEED

**Prepared by:** Samuel Ayinla (Technical Lead / Core Backend Architecture)  
**Assigned to:** Noah Enemali (Backend / Persistence / APIs / Engine Integration)  
**Baseline Branch:** `main` (`856efc96cb3e0b4a44b80267a57a15a01bc1544a`)  
**Feature Branch to Create:** `feat/m3-formula-studio-reconciliation`  
**Milestone Alignment:**
- `docs/build/01_HOW_WE_WORK_AND_MILESTONES.md` — **M3: Formula Governance + Reconciliation Against Expected Results**
- `docs/handoff/01_requirements/master-requirement-tracker.md` — `LR1-CORE-001` through `LR1-CORE-007`, `LR1-CORE-025`, `LR1-CORE-027`, `LR1-API-004`, `LR1-API-006`, `LR1-APP-003`, `LR1-APP-007`
- `docs/phase_1_backlog.md` — §1 (Multi-Product Seed), §2 (Non-Inforce Record Viewers), §3 (Governed Formula Expression Editing)

---

## 0. Context & Architectural Guardrails

M2B (5-Year MYGA Governed Valuation Engine, Run-Scoped Decrement Methodology, Artifact Attempt Provenance, and Model Version Governance) has been reviewed, verified against PostgreSQL, and merged into `main` (`856efc96cb3e0b4a44b80267a57a15a01bc1544a`).

First, pull the latest `main` branch before starting:

```bash
git checkout main
git pull origin main
git checkout -b feat/m3-formula-studio-reconciliation
```

### Non-Negotiable Guardrails
1. **Do NOT touch or migrate the shared `neondb` database.** All tests and verification must run against isolated SQLite and a disposable local PostgreSQL `*_test` database (`mentoramp_wp1_test`).
2. **Do NOT use `eval()` or `exec()` anywhere in the Formula DSL.** Formula expressions must be parsed and evaluated strictly via Python's `ast` module with an explicit node/function whitelist.
3. **Do NOT break existing SPIA or MYGA registered Python formulas (`spia.*`, `myga.*`).** Per `LR1-CORE-003`, standard formulas can use the controlled Formula Expression DSL while registered Python plug-in functions remain supported and tested.
4. **Do NOT introduce module-level mutable state.** Keep all execution and evaluation deterministic and run-scoped.

---

## 1. Workstream 1 — Controlled Formula Expression Language (DSL) & Formula Studio APIs

### 1.1 Why We Are Building This (`LR1-CORE-001` to `LR1-CORE-007`, `LR1-API-004`)
Currently:
- Product formulas in SPIA and MYGA are Python functions in `config.py` with display-only `expression_text` strings.
- `FormulaDefinition` in `backend/app/core/formula_engine/registry.py` omits `expression_text`, `explanation`, `unit`, `group_id`, and `illustrative`, and `PUT /v1/formulas/{id}` does not allow updating `expression_text` or `explanation`.
- Stakeholders need a real **Formula Studio** backend where actuaries can write, validate, preview/test, diff, and govern mathematical formulas in a safe, controlled expression language (DSL) without writing raw Python code or deploying backend changes.

### 1.2 The MentorAmp Formula Expression DSL (`backend/app/core/formula_engine/dsl.py`)
Create `backend/app/core/formula_engine/dsl.py` implementing a deterministic, safe mathematical expression parser, validator, dependency extractor, and evaluator using Python's built-in `ast` module:

1. **Allowed AST Nodes (Strict Whitelist):**
   - `ast.Expression`, `ast.Constant` (int, float, bool), `ast.Name` (variable identifiers)
   - `ast.BinOp` (`Add`, `Sub`, `Mult`, `Div`, `FloorDiv`, `Mod`, `Pow`)
   - `ast.UnaryOp` (`UAdd`, `USub`, `Not`)
   - `ast.BoolOp` (`And`, `Or`)
   - `ast.Compare` (`Eq`, `NotEq`, `Lt`, `LtE`, `Gt`, `GtE`)
   - `ast.IfExp` (ternary `a if cond else b`)
   - `ast.Call` (only when `func` is a direct `ast.Name` in the whitelisted function table — **never** `ast.Attribute`, `ast.Subscript`, `ast.Lambda`, `ast.Import`, comprehensions, or dunder names).

2. **Whitelisted Built-in Actuarial & Mathematical Functions:**
   - `min(a, b, ...)`
   - `max(a, b, ...)`
   - `abs(x)`
   - `round(x, ndigits=0)`
   - `exp(x)` — `math.exp(x)`
   - `log(x)` — natural log `math.log(x)` (raise structured domain error if `x <= 0`)
   - `sqrt(x)` — `math.sqrt(x)` (raise structured domain error if `x < 0`)
   - `pow(x, y)` — `math.pow(x, y)` (with exponent bounds check to prevent overflow DoS, e.g. `abs(y) <= 1200`)
   - `clamp(x, low, high)` — `max(low, min(high, x))`
   - `if_else(cond, true_val, false_val)` — functional conditional
   - `coalesce(val, fallback)` — returns `fallback` if `val is None` else `val`
   - `prior(var_name, default=0.0)` — resolves the prior time-step ($t - 1$) value of `var_name` from `RunData` state (or `default` at $t = 1$ / when absent). Note: `prior(x)` does **not** create a same-step topological cycle dependency on `x`; record it separately as a `prior_dependencies` reference or handle it cleanly in DAG validation.

3. **Automatic Dependency Extraction (`extract_dependencies(expression_text: str)`):**
   - Parse the expression AST and return:
     - `dependencies`: sorted list of same-step variable names referenced in the expression (excluding whitelisted function names and `prior(...)` first-argument variable names).
     - `prior_dependencies`: sorted list of variable names referenced inside `prior(...)`.
     - `functions_used`: sorted list of whitelisted functions called.

4. **Engine Integration (`dsl` Execution Mode):**
   - Support `function_ref="dsl"` (or `function_ref.startswith("dsl:")`) in `FormulaRegistry` and `ProjectionEngine`.
   - When a model version is validated (`catalog_service.model_version_checks` / `model_version_service.validate_version`):
     - A formula is valid if **either** (a) its `function_ref` is registered in `FORMULA_FUNCTIONS` (existing Python plug-in path), **or** (b) its `function_ref == "dsl"` (or `"dsl:<output_variable>"`) and its `expression_text` passes DSL syntax validation and every extracted dependency exists in the model version's variables/formulas.
   - During `ProjectionEngine` execution, DSL formulas evaluate deterministically using resolved variable values from `VariableResolver` / `RunData` and emit full `TraceLogger` entries (recording `expression_text`, resolved input values, and computed output).

### 1.3 Formula Studio Endpoints & Schema Enhancements
Update `backend/app/models/schemas.py`, `backend/app/core/formula_engine/registry.py`, `backend/app/api/formulas.py`, and `backend/app/api/modeling.py`:

1. **Complete `FormulaDefinition` & Registry Fields (`registry.py`, `formula.py`, & `schemas.py` — `CAS` §5, `MP` §5.1, `LR1-CORE-001`, `LR1-CORE-002`):**
   - Ensure `FormulaRegistry` and `FormulaDefinition` include and persist:
     - `expression_text`, `explanation`, `unit`, `group_id`, `illustrative`
     - `effective_date` (date | None), `owner_user_id` (str | None)
     - `test_cases` (JSON list of `{ "name": str, "inputs": dict, "prior_inputs": dict, "expected_output": float, "abs_tol": float }`)
   - Ensure `register()` and `update()` in `registry.py` persist all of these fields.
   - When `expression_text` is provided with `function_ref="dsl"` and `dependencies` is omitted or auto-derived, automatically populate `dependencies` from `extract_dependencies(expression_text)` and validate syntax before saving.

2. **`POST /v1/formulas/validate-expression` (`registries:read` — `LR1-CORE-004`):**
   - **Request Body:**
     ```json
     {
       "expression_text": "max(0.0, account_value_bom - withdrawal_amount)",
       "model_version_id": "<optional-uuid>",
       "output_variable": "<optional-str>"
     }
     ```
   - **Response (`200 OK`):**
     ```json
     {
       "valid": true,
       "expression_text": "max(0.0, account_value_bom - withdrawal_amount)",
       "dependencies": ["account_value_bom", "withdrawal_amount"],
       "prior_dependencies": [],
       "functions_used": ["max"],
       "unknown_variables": [],
       "errors": []
     }
     ```
   - If `model_version_id` is supplied, check that every variable in `dependencies` and `prior_dependencies` exists in that model version's `ModelVariableDefinition` or `FormulaRegistry.output_variable` list, reporting any missing names in `unknown_variables` and `errors`.

3. **`POST /v1/formulas/preview` & `POST /v1/formulas/{formula_id}/run-tests` (`registries:read` — `CAS` §5, `MP` §5.2, `LR1-CORE-005`):**
   - **`POST /v1/formulas/preview` Request Body:**
     ```json
     {
       "expression_text": "round(max(0.0, account_value_bom - withdrawal_amount) * ((1.0 + credited_rate_annual) ** (1.0 / 12.0) - 1.0), 6)",
       "inputs": {
         "account_value_bom": 100000.0,
         "withdrawal_amount": 10000.0,
         "credited_rate_annual": 0.045
       },
       "prior_inputs": {}
     }
     ```
   - **`POST /v1/formulas/preview` Response (`200 OK`):**
     ```json
     {
       "valid": true,
       "result": 330.650932,
       "dependencies": ["account_value_bom", "credited_rate_annual", "withdrawal_amount"],
       "inputs_used": {
         "account_value_bom": 100000.0,
         "withdrawal_amount": 10000.0,
         "credited_rate_annual": 0.045
       },
       "errors": []
     }
     ```
   - **`POST /v1/formulas/{formula_id}/run-tests` (`registries:read`):**
     - Execute all persisted `test_cases` on the formula (using either its registered Python function in `FORMULA_FUNCTIONS` or its compiled DSL `expression_text`) and return `{ "formula_id": ..., "all_passed": bool, "total": int, "passed": int, "failed": int, "results": [{ "name": ..., "expected_output": ..., "actual_output": ..., "abs_diff": ..., "abs_tol": ..., "passed": bool, "error": str | None }] }`.

4. **`POST /v1/model-versions/{model_version_id}/formulas` (`registries:write` — `LR1-CORE-004`):**
   - Create a new formula directly inside a `draft` `ModelVersion` (auto-attaching to the model version's default `FormulaGroup` if `group_id` is not provided).
   - If the `ModelVersion` is `approved` or `published`, return `409 Conflict` via `require_mutable_model_version`.
   - Automatically invalidate model version validation status back to `draft` (`invalidate_model_validation`) and write an audit log entry.

5. **Dependency Paths & Impacted Calculations (`GET /v1/model-versions/{model_version_id}/dependency-analysis` — `CAS` §5, `MP` §5.4, `LR1-CORE-015`):**
   - Given a `model_version_id` and optional query parameter `?variable=<variable_name>`, return:
     - `execution_order`: full topological order of output variables in the model version
     - `upstream_dependencies`: all direct and transitive upstream variables/formulas required to compute `variable`
     - `downstream_impacted`: all direct and transitive downstream formulas/outputs impacted if `variable` changes (in topological order)

6. **Version Comparison & Rollback (`GET /v1/model-versions/{model_version_id}/compare/{other_version_id}` & `POST /v1/model-versions/{model_version_id}/activate` — `CAS` §5, §15.3, `MP` §5.2, `LR1-CORE-006`, `LR1-CORE-007`, `LR1-GATE-003`):**
   - **`GET /v1/model-versions/{model_version_id}/compare/{other_version_id}` (`projects:read`):**
     Compare two `ModelVersion` records and return a structured diff:
     - `metadata_changes`: changes in `basis`, `methodology`, `configuration`, `status`
     - `formula_changes`: `added`, `removed`, `modified` (showing `before` and `after` for `expression_text`, `function_ref`, `dependencies`, `unit`, `explanation`)
     - `variable_changes`: `added`, `removed`, `modified`
     - `published_output_changes`: `added`, `removed`, `modified`
   - **`POST /v1/model-versions/{model_version_id}/rollback` (`models:approve` or `registries:write`):**
     Allow rolling back / activating a previously `approved` or `published` `ModelVersion` as the model's `is_current=True` active version (demoting the prior current version's `is_current` flag) and recording an immutable `model_version.rollback_activated` audit event.

---

## 2. Workstream 2 — Expected-Result Benchmark Ingestion & Tolerance Reconciliation API

### 2.1 Why We Are Building This (`LR1-CORE-025`, `LR1-CORE-027`, `LR1-API-006`, `LR1-APP-007`)
In M2B, we built `backend/scripts/reconcile_myga_to_reference.py` to reconcile engine output against `samples/myga/expected_reference_myga_10001.csv`. Now, for M3, the platform must expose **Expected-Result Benchmark Registration and Tolerance Reconciliation via FastAPI** so the UI's **Reconciliation** screen can load expected results, compare actual run outputs against expected results within configurable tolerances (`abs_tol`, `rel_tol`), inspect cell-level diffs, and record reviewer disposition/sign-off.

### 2.2 Database Models & Alembic Migration
Create a clean Alembic migration extending the current head (`f8b9c0d1e2f3`) with:
1. **`expected_result_sets` (`ExpectedResultSet`):**
   - `id` (UUID string PK)
   - `project_id` (FK to `projects.id`, indexed)
   - `name` (str, e.g., `"MYGA-10001 Development Reference v2.0 (BOM)"`)
   - `product_code` (str, e.g., `"myga_5yr"`, `"spia_lite"`)
   - `version_label` (str, default `"v1"`)
   - `source_reference` (str, e.g., `"samples/myga/expected_reference_myga_10001.csv"`)
   - `timing_convention` (str | None, e.g., `"BOM_WITHDRAWAL_BEFORE_INTEREST"`)
   - `granularity` (str, `"policy_month"` or `"aggregate_year"`)
   - `row_count` (int)
   - `columns_detected` (JSON list of strings)
   - `rows_data` (JSON list of dicts — normalized expected rows)
   - `fingerprint` (str, SHA-256 hex digest of canonical rows)
   - `status` (str, `"validated"` / `"approved"`)
   - `created_by`, `created_at`, `updated_at`
2. **`reconciliation_reports` (`ReconciliationReport`):**
   - `id` (UUID string PK)
   - `project_id` (FK to `projects.id`, indexed)
   - `run_id` (FK to `runs.id`, indexed)
   - `expected_result_set_id` (FK to `expected_result_sets.id`, nullable if comparing against another source)
   - `policy_id_filter` (str | None)
   - `abs_tol` (float, default `1e-2`)
   - `rel_tol` (float, default `1e-4`)
   - `status` (str: `"PASS"` or `"FAIL"`)
   - `disposition_status` (str: `"pending"`, `"approved"`, `"exception_noted"`, `"rejected"`)
   - `disposition_notes` (str | None)
   - `disposition_by` (str | None), `disposition_at` (DateTime | None)
   - `summary` (JSON dict: `total_Variables_checked`, `total_cells_checked`, `cells_passed`, `cells_failed`, `max_abs_diff`, `max_rel_diff`, `per_variable_summary`)
   - `failures` (JSON list of failed cell comparisons: `policy_id`, `projection_month`, `variable`, `expected`, `actual`, `abs_diff`, `rel_diff`, `tolerance_used`)
   - `created_by`, `created_at`

### 2.3 Expected-Result & Reconciliation API Endpoints
Expose under `/v1`:
1. **`POST /v1/projects/{project_id}/expected-results` (`imports:write` or `registries:write`):**
   - Accept either multipart CSV upload (`file`, `name`, `product_code`, `version_label`, `timing_convention`) or JSON payload.
   - Normalize column aliases (supporting both canonical engine names like `account_value_eom` and reference aliases like `av_eom`, `interest_credited`, `surrender_charge_rate`, `csv_eom`, `death_benefit`, `surrender_benefit`, `net_cash_flow` using the exact mapping proven in `reconcile_myga_to_reference.py`).
   - Compute deterministic SHA-256 `fingerprint`, persist `ExpectedResultSet`, record audit event, and return metadata.
2. **`GET /v1/projects/{project_id}/expected-results` & `GET /v1/expected-results/{expected_result_id}` (`projects:read`):**
   - List expected result sets in the project; retrieve detail (including paginated or full rows).
3. **`POST /v1/runs/{run_id}/reconcile` (`runs:write` or `projects:read` — `CAS` §5, §11, `MP` §5.8, `LR1-CORE-025`, `LR1-CORE-027`):**
   - **Request Body:**
     ```json
     {
       "expected_result_set_id": "<uuid>",
       "policy_id": "MYGA-10001",
       "level": "policy_month",
       "abs_tol": 0.01,
       "rel_tol": 0.0001,
       "variables": null
     }
     ```
   - Support multi-level comparison (`level: "policy_month" | "aggregate_year" | "both"`):
     - At `policy_month` level, compare every overlapping `(policy_id, projection_month, variable)` against the `ExpectedResultSet`.
     - At `aggregate_year` / portfolio-total level, compare portfolio aggregates by `projection_year` (or summed across months/policies) against expected aggregate totals.
     - Include a `source_to_output_check` in `summary` verifying opening in-force policy count and initial premium/account value from the run's frozen input in-force dataset against month-1 run outputs (`CAS` §5 "source-to-output totals", `actuarial_reference_model_plan.md` §7).
   - Apply the tolerance rule: a cell passes if `abs(actual - expected) <= max(abs_tol, rel_tol * abs(expected))`.
   - Persist and return the `ReconciliationReport` (`status: "PASS"` if `cells_failed == 0`, else `"FAIL"`), along with `per_variable_summary`, `source_to_output_check`, and `failures`.
4. **`GET /v1/runs/{run_id}/reconciliations` & `GET /v1/reconciliations/{reconciliation_id}` (`projects:read`):**
   - List all reconciliation reports for a run and fetch a specific reconciliation report.
5. **`POST /v1/reconciliations/{reconciliation_id}/disposition` (`models:approve` or `admin`):**
   - **Request Body:**
     ```json
     {
       "disposition_status": "approved",
       "disposition_notes": "Verified exact 0.00e+00 drift across all 60 months against BOM reference v2.0."
     }
     ```
   - Transition `disposition_status` (`approved`, `exception_noted`, `rejected`), record `disposition_by`, `disposition_at`, and emit an immutable audit log entry (`LR1-CORE-027`).

---

## 3. Workstream 3 — Multi-Product Governed Demo Seeder (`seed_demo.py`) & Table Record Viewer

### 3.1 Expand `backend/scripts/seed_demo.py` to Seed Both SPIA and 5-Year MYGA (`phase_1_backlog.md` §1)
Currently `backend/scripts/seed_demo.py` only seeds the M1 SPIA project.
Update `backend/scripts/seed_demo.py` so that running `python backend/scripts/seed_demo.py`:
1. Remain 100% **idempotent** (running twice creates no duplicates) and backward-compatible with existing tests that call `seed(db)` for SPIA.
2. Add `seed_myga(db)` (and invoke both `seed(db)` and `seed_myga(db)` in `main()` — or provide `seed_all(db)`):
   - Seeds project `"MentorAmp Demo — 5-Year MYGA (Illustrative)"` (or adds the 5-Year MYGA model and datasets so both SPIA and 5-Year MYGA are ready out of the box).
   - Seeds the **5-Year MYGA (Illustrative)** model (`myga_5yr`), model version `v1`, variables, formulas, and published outputs, and transitions the model version through `validated` $\rightarrow$ `approved` $\rightarrow$ `published`.
   - Loads and validates all MYGA sample datasets from `samples/myga/`:
     - `synthetic_myga_inforce_25.csv` (and `synthetic_myga_inforce_10001.csv`)
     - `synthetic_mortality_gompertz.csv`
     - `myga_surrender_charge_schedule.csv`
     - `myga_surrender_charge_age_cap.csv`
     - Deterministic scenarios: `Base` and `Rates Up (+150 bps)`
   - Marks the seeded datasets as `approved` (with `approved_by=user.id`, `approved_at`) so the Projection Set is immediately runnable under M2A/M2B governance rules.
   - Seeds the validated MYGA `ProjectionSet` (`"5-Year MYGA Illustrative — Q4 2026"`) and seeds the `ExpectedResultSet` from `samples/myga/expected_reference_myga_10001.csv`.

### 3.2 Paginated Record Viewers for Assumption & Factor Tables (`phase_1_backlog.md` §2)
In `backend/app/api/inputs.py` (or `data_manager.py`):
- Add `GET /v1/inputs/assumptions/{table_id}/records?limit=100&offset=0&search=`
- Add `GET /v1/inputs/factors/{table_id}/records?limit=100&offset=0&search=`
- Return `{ "table_id": ..., "total": ..., "limit": ..., "offset": ..., "columns": [...], "records": [...] }` so the frontend Data Manager can paginate assumption and factor table rows with the same pattern as `GET /v1/inputs/inforce/{file_id}/records`.

---

## 4. Required Tests & Acceptance Evidence

Create dedicated unit and integration test suites covering all three workstreams:

1. **`backend/tests/unit/test_formula_dsl.py`**
   - `DSL-01`: Valid mathematical and actuarial expressions (`min`, `max`, `abs`, `round`, `exp`, `log`, `sqrt`, `pow`, `clamp`, `if_else`, `coalesce`, `prior`, ternary `a if cond else b`) parse, extract exact dependencies, and evaluate to exact expected values.
   - `DSL-02`: Security rejection tests — attempts to use `__import__`, `eval`, `exec`, attribute access (`x.__class__`), subscripts (`x[0]`), lambdas, comprehensions, or unwhitelisted functions are rejected with structured `FormulaSyntaxError` / `422`.
   - `DSL-03`: Domain safety tests — division by zero, `log(0)`, `log(-1)`, `sqrt(-1)`, and extreme exponent `10 ** 10000` fail cleanly with structured errors instead of crashing the worker.

2. **`backend/tests/integration/test_formula_studio_api.py`**
   - `FSTUDIO-01`: `POST /v1/formulas/validate-expression` validates syntax, extracts dependencies, and detects unknown variables against a `model_version_id`.
   - `FSTUDIO-02`: `POST /v1/formulas/preview` evaluates an expression against sample inputs (including `prior_inputs`) and returns the exact numerical result.
   - `FSTUDIO-03`: Creating/editing a DSL formula in a `draft` `ModelVersion` (`POST /v1/model-versions/{id}/formulas`, `PUT /v1/formulas/{id}`), validating, approving, publishing, and executing a real `ProjectionEngine` run proves that the custom DSL formula executes end-to-end and appears in the calculation trace (`GET /v1/runs/{id}/trace`).
   - `FSTUDIO-04`: Editing or creating a formula on an `approved` or `published` `ModelVersion` is rejected with `409 Conflict`.
   - `FSTUDIO-05`: `GET /v1/model-versions/{id}/compare/{other_id}` returns accurate diffs of modified formula expressions and variables.
   - `FSTUDIO-06`: Persisting `test_cases` on a formula and calling `POST /v1/formulas/{id}/run-tests` executes all stored test cases and returns accurate pass/fail results (`LR1-CORE-002`, `LR1-CORE-005`).
   - `FSTUDIO-07`: `GET /v1/model-versions/{id}/dependency-analysis` returns topological order, upstream dependencies, and downstream impacted variables (`LR1-CORE-015`), and `POST /v1/model-versions/{id}/rollback` activates a prior approved version as `is_current=True` with an audit log (`LR1-CORE-007`).

3. **`backend/tests/integration/test_expected_result_reconciliation.py`**
   - `RECON-01`: Upload `samples/myga/expected_reference_myga_10001.csv` via `POST /v1/projects/{id}/expected-results` and verify SHA-256 fingerprint and row count (`60`).
   - `RECON-02`: Execute a real MYGA run for `MYGA-10001` and call `POST /v1/runs/{run_id}/reconcile` against the uploaded expected result set — verify `status == "PASS"`, `cells_failed == 0`, `max_abs_diff == 0.0`, and `source_to_output_check` passes.
   - `RECON-03`: Perturb an expected value (or use a tighter tolerance against a modified run) and verify `POST /v1/runs/{run_id}/reconcile` returns `status == "FAIL"` with exact cell-level failure details (`policy_id`, `projection_month`, `variable`, `expected`, `actual`, `abs_diff`, `rel_diff`).
   - `RECON-04`: Reviewer disposition (`POST /v1/reconciliations/{id}/disposition`) transitions status to `approved` / `exception_noted` and records an audit log entry; non-reviewer receives `403 Forbidden`.
   - `RECON-05`: Aggregate-level reconciliation (`level="aggregate_year"`) compares yearly portfolio totals within configured tolerances.

4. **`backend/tests/integration/test_seed_demo_multi_product.py`**
   - `SEED-01`: Running `seed(db)` and `seed_myga(db)` twice is idempotent and leaves both SPIA and 5-Year MYGA validated/approved and ready to run.
   - `VIEWER-01`: `GET /v1/inputs/assumptions/{id}/records` and `GET /v1/inputs/factors/{id}/records` paginate and filter rows accurately.

---

## 5. Delivery & Handback Checklist for Noah

Before handing back:
1. Ensure all migrations upgrade cleanly from base to the new Alembic head on both SQLite and disposable PostgreSQL (`mentoramp_wp1_test`).
2. Run the full test suite (`pytest -q`) and verify **0 failures**.
3. Commit all code, migrations, tests, and documentation on `feat/m3-formula-studio-reconciliation`, push to `origin`, and verify `git status --short` is completely clean.
