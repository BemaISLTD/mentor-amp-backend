# MentorAMP M3 — Frontend Implementation Specification
## Formula Studio, Actuarial Reconciliation & Model Governance

**Audience:** Sam (Frontend Engineering Lead)  
**Backend Branch:** `feat/m3-formula-studio-reconciliation` (Commit: `f6708d1`)  
**Target Release:** Milestone 3 (M3)  
**Base API URL:** `/v1` (or your configured `NEXT_PUBLIC_API_URL`)

---

## 1. Overview & Architecture

The M3 backend introduces two major actuarial capabilities:
1. **Formula Studio**: An interactive, safe AST-based formula engine supporting recursive state formulas (using `prior(...)`), live expression validation, interactive execution previews, governed test cases, and DAG dependency visualization.
2. **Expected Results & Actuarial Reconciliation**: Benchmark dataset ingestion (CSV/JSON), compatibility validation against frozen runs, actuarial roll-forward reconciliation (flows summed, stocks/balances taken at period-end), immutable audit reports, and actuary reviewer disposition workflows.

---

## 2. API Specifications: Formula Studio

### 2.1. Live Expression Validation
Validates safe DSL syntax and checks input variable existence against a target Model Version.

* **Endpoint:** `POST /v1/formulas/validate-expression`
* **Permissions:** `registries:read`
* **Request Schema:**
  ```json
  {
    "expression_text": "prior(reserve) * (1 + credited_rate / 12) - monthly_benefit",
    "model_version_id": "8a3e9c1d-4f2b-4e8a-9c1d-4f2b4e8a9c1d",
    "output_variable": "reserve"
  }
  ```
* **Response Schema (200 OK):**
  ```json
  {
    "valid": true,
    "expression_text": "prior(reserve) * (1 + credited_rate / 12) - monthly_benefit",
    "dependencies": ["credited_rate", "monthly_benefit"],
    "prior_dependencies": ["reserve"],
    "functions_used": [],
    "unknown_variables": [],
    "errors": []
  }
  ```
* **Error Response (422 Unprocessable Entity):**
  ```json
  {
    "detail": {
      "code": "DSL_SYNTAX_ERROR",
      "message": "Syntax error at line 1, column 18: unexpected token",
      "details": { "line": 1, "column": 18 }
    }
  }
  ```

---

### 2.2. Interactive Formula Preview
Evaluates an expression in real-time given current and prior timestep input values.

* **Endpoint:** `POST /v1/formulas/preview`
* **Permissions:** `registries:read`
* **Request Schema:**
  ```json
  {
    "expression_text": "prior(balance) * 1.05 + deposit",
    "inputs": {
      "deposit": 1000.0
    },
    "prior_inputs": {
      "balance": 20000.0
    }
  }
  ```
* **Response Schema (200 OK):**
  ```json
  {
    "valid": true,
    "result": 22000.0,
    "dependencies": ["deposit"],
    "inputs_used": {
      "deposit": 1000.0
    },
    "errors": []
  }
  ```

---

### 2.3. Run Governed Test Cases
Executes the formula's configured test cases against the engine with tolerance checking.

* **Endpoint:** `POST /v1/formulas/{formula_id}/run-tests`
* **Permissions:** `registries:read`
* **Response Schema (200 OK):**
  ```json
  {
    "formula_id": "f8a1c2d3-4e5f-6a7b-8c9d-0e1f2a3b4c5d",
    "all_passed": true,
    "total": 2,
    "passed": 2,
    "failed": 0,
    "results": [
      {
        "name": "Base deposit case",
        "expected_output": 22000.0,
        "actual_output": 22000.0,
        "abs_diff": 0.0,
        "abs_tol": 0.01,
        "passed": true,
        "error": null
      },
      {
        "name": "Zero prior case",
        "expected_output": 1000.0,
        "actual_output": 1000.0,
        "abs_diff": 0.0,
        "abs_tol": 0.01,
        "passed": true,
        "error": null
      }
    ]
  }
  ```

---

### 2.4. Model Version Formula Creation
Attaches a new formula directly to a Model Version (scoped to the project).

* **Endpoint:** `POST /v1/model-versions/{model_version_id}/formulas`
* **Permissions:** `registries:write`
* **Request Schema:**
  ```json
  {
    "name": "Reserve Evolution",
    "output_variable": "reserve",
    "function_ref": "dsl",
    "expression_text": "prior(reserve) + interest - benefit_payment",
    "dependencies": ["interest", "benefit_payment"],
    "group_id": "optional-formula-group-uuid",
    "explanation": "Calculates month-end reserve roll-forward.",
    "unit": "USD",
    "test_cases": [
      {
        "name": "Baseline Case",
        "inputs": { "interest": 50.0, "benefit_payment": 20.0 },
        "prior_inputs": { "reserve": 1000.0 },
        "expected_output": 1030.0,
        "abs_tol": 0.01
      }
    ]
  }
  ```
* **Response Schema (201 Created):** Returns full `FormulaDefinition` object.

---

### 2.5. Governed Formula Update
Updates an existing formula. Automatically transitions model version to require re-validation.

* **Endpoint:** `PUT /v1/formulas/{formula_id}`
* **Permissions:** `registries:write`
* **Request Schema (`FormulaUpdateRequest`):**
  ```json
  {
    "name": "Reserve Evolution v2",
    "expression_text": "prior(reserve) + interest - benefit_payment",
    "dependencies": ["interest", "benefit_payment"],
    "explanation": "Updated actuarial formula",
    "test_cases": [
      {
        "name": "Test 1",
        "inputs": { "interest": 10.0, "benefit_payment": 5.0 },
        "prior_inputs": { "reserve": 500.0 },
        "expected_output": 505.0,
        "abs_tol": 0.01
      }
    ]
  }
  ```
* **Response Schema (200 OK):** Returns updated `FormulaDefinition`.

---

### 2.6. Dependency Analysis & DAG Visualization
Returns the topological execution order and clearly distinguishes time-shifted dependencies (`prior(...)`) from intra-period same-step dependencies.

* **Endpoint:** `GET /v1/model-versions/{model_version_id}/dependency-analysis?variable={optional_target}`
* **Permissions:** `projects:read`
* **Response Schema (200 OK):**
  ```json
  {
    "version_id": "8a3e9c1d-4f2b-4e8a-9c1d-4f2b4e8a9c1d",
    "status": "draft",
    "is_current": false,
    "execution_order": ["credited_rate", "interest", "benefit_payment", "reserve"],
    "same_step_dependencies": {
      "reserve": ["interest", "benefit_payment"],
      "interest": ["credited_rate"]
    },
    "prior_dependencies": {
      "reserve": ["reserve"]
    },
    "upstream": ["interest", "benefit_payment", "credited_rate"],
    "downstream": [],
    "circular": false,
    "target_variable": "reserve"
  }
  ```
* **Frontend UI Implementation Tip:**
  * Render `same_step_dependencies` as solid arrows within step $t$.
  * Render `prior_dependencies` as dashed or distinctive curved lines labeled `$t-1$` to visually reassure the user that recursive state formulas are not circular errors.

---

## 3. API Specifications: Expected Results & Reconciliation

### 3.1. Upload Expected Result Benchmark
Accepts multipart CSV file upload (up to 10 MB and 50,000 rows) or JSON payload.

* **Endpoint:** `POST /v1/projects/{project_id}/expected-results`
* **Headers:** `Content-Type: multipart/form-data`
* **Form Fields:**
  * `file`: Binary CSV file
  * `name`: String (e.g. `"Milliman MYGA 10-Year Benchmark"`)
  * `product_code`: String (e.g. `"MYGA"` or `"SPIA"`)
  * `version_label`: String (e.g. `"v1.0"`)
  * `timing_convention`: String (e.g. `"eom"` or `"bom"`)
  * `granularity`: `"policy_month"` or `"aggregate_year"`
* **Response Schema (201 Created):**
  ```json
  {
    "id": "e8a1c2d3-4f5a-6b7c-8d9e-0f1a2b3c4d5e",
    "project_id": "proj-uuid",
    "name": "Milliman MYGA 10-Year Benchmark",
    "product_code": "MYGA",
    "version_label": "v1.0",
    "granularity": "policy_month",
    "timing_convention": "eom",
    "row_count": 1200,
    "columns_detected": ["policy_id", "projection_month", "reserve", "net_cash_flow"],
    "fingerprint": "a1b2c3d4e5f6...64-chars",
    "status": "validated",
    "created_at": "2026-10-28T14:30:00Z"
  }
  ```

---

### 3.2. List & Inspect Expected Results
* **List:** `GET /v1/projects/{project_id}/expected-results`
* **Get Details:** `GET /v1/expected-results/{expected_result_id}?include_rows=true`

---

### 3.3. Execute Run Reconciliation
Compares actual projection engine outputs from a Run against an Expected Result Set.

* **Endpoint:** `POST /v1/runs/{run_id}/reconcile`
* **Permissions:** `projects:read`
* **Request Schema (`ReconcileRequest`):**
  ```json
  {
    "expected_result_set_id": "e8a1c2d3-4f5a-6b7c-8d9e-0f1a2b3c4d5e",
    "level": "both",
    "abs_tol": 0.01,
    "rel_tol": 0.0001,
    "variables": ["reserve", "net_cash_flow"],
    "policy_id": null
  }
  ```
  * `level`: `"policy_month"`, `"aggregate_year"`, or `"both"`.
  * `abs_tol`: Absolute tolerance (default `0.01`).
  * `rel_tol`: Relative tolerance (default `0.0001`).
  * `variables`: Optional list of variable names to restrict comparison.
* **Response Schema (201 Created):** Returns full `ReconciliationReport` object (see Section 3.4).

---

### 3.4. Reconciliation Report Structure & Audit Scope
* **Endpoint:** `GET /v1/reconciliations/{reconciliation_id}`
* **Permissions:** `projects:read`
* **Response Schema (200 OK):**
  ```json
  {
    "id": "rec-report-uuid",
    "project_id": "proj-uuid",
    "run_id": "run-uuid",
    "expected_result_set_id": "e8a1c2d3-4f5a-6b7c-8d9e-0f1a2b3c4d5e",
    "status": "PASS",
    "level": "both",
    "variables_requested": ["reserve", "net_cash_flow"],
    "expected_result_fingerprint": "a1b2c3d4e5f6...64-chars",
    "expected_result_version": "v1.0",
    "timing_convention_snapshot": "eom",
    "granularity_snapshot": "policy_month",
    "abs_tol": 0.01,
    "rel_tol": 0.0001,
    "disposition_status": "pending",
    "disposition_notes": null,
    "disposition_by": null,
    "disposition_at": null,
    "disposition_history": [],
    "summary": {
      "status": "PASS",
      "cells_checked": 2400,
      "cells_failed": 0,
      "max_abs_diff": 0.0042,
      "max_rel_diff": 0.00001,
      "by_level": {
        "policy_month": { "cells_checked": 2400, "cells_failed": 0 },
        "aggregate_year": { "cells_checked": 20, "cells_failed": 0 }
      },
      "by_variable": {
        "reserve": { "cells_checked": 1200, "cells_failed": 0, "max_abs_diff": 0.002 },
        "net_cash_flow": { "cells_checked": 1200, "cells_failed": 0, "max_abs_diff": 0.0042 }
      },
      "source_to_output_check": {
        "status": "PASS",
        "passed": true,
        "inforce_source_matched": true,
        "inforce_rows_checked": 100,
        "opening_balances_matched": true,
        "detail": "Inforce records and initial state verified against run output."
      }
    },
    "failures": [],
    "calculated_at": "2026-10-28T14:35:00Z"
  }
  ```

#### In the event of differences (`status: "FAIL"`):
`failures` contains individual cell difference records (capped at 1,000 items):
```json
{
  "level": "policy_month",
  "policy_id": "POL-00123",
  "projection_month": 12,
  "variable": "reserve",
  "actual": 10543.20,
  "expected": 10500.00,
  "abs_diff": 43.20,
  "rel_diff": 0.0041,
  "abs_tol": 0.01,
  "rel_tol": 0.0001
}
```

#### Three-State Source-to-Output Check
The summary contains `source_to_output_check.status`:
* `PASS`: Inforce policies and opening account values match run outputs.
* `FAIL`: Mismatch between source inforce and model output.
* `NOT_VERIFIABLE`: Source inforce or balances were not frozen/available. Show a yellow warning pill `"NOT VERIFIABLE"`.

---

### 3.5. Actuary Reviewer Disposition Workflow
Reviewers disposition the report (approval, approval with exception, or rejection).

* **Endpoint:** `POST /v1/reconciliations/{reconciliation_id}/disposition`
* **Permissions:** Requires `models:approve` or `admin` role.
* **Request Schema (`DispositionRequest`):**
  ```json
  {
    "disposition_status": "approved_with_exception",
    "disposition_notes": "Variance in month 12 reserve accepted due to timing convention change from EOM to BOM in external benchmark."
  }
  ```
* **Validation Rules:**
  * If report `status == "FAIL"`: Cannot select `"approved"`.
  * If selecting `"approved_with_exception"`: `disposition_notes` is strictly **mandatory** (minimum 5 characters).
  * Allowed statuses: `"approved"`, `"approved_with_exception"`, `"exception_noted"`, `"rejected"`.
* **Response Schema (200 OK):** Returns updated `ReconciliationReport` with new entry appended to `disposition_history`.

---

## 4. UI/UX Component Specifications

### 4.1. Formula Studio Component (`/models/[modelId]/versions/[versionId]/formulas`)
1. **Left Panel: Formula List & DAG:**
   * Tree or list of formulas grouped by `FormulaGroup`.
   * Status badge (`draft`, `validated`, `approved`).
   * "Add Formula" button opening the Formula Drawer.
2. **Main Panel: Monaco/Code Editor:**
   * Syntax highlighting for Python-like arithmetic (`+`, `-`, `*`, `/`, `**`, `min`, `max`, `prior(...)`).
   * Inline validation badge triggered on debounce (300ms) calling `/v1/formulas/validate-expression`.
   * Display detected variables with color tags:
     * Blue tag: Current timestep inputs ($t$).
     * Purple tag: Time-shifted state dependencies ($t-1$, e.g. `prior(reserve)`).
     * Red tag: Unknown variables (not found in model version).
3. **Bottom Panel: Preview & Test Runner Tabs:**
   * **Tab 1: Live Preview:** Simple key-value inputs for current timestep and prior timestep values. "Evaluate" button displaying result.
   * **Tab 2: Test Suite:** Table of test cases with `Expected Output`, `Tolerance`, `Actual Output`, and `Status (Pass/Fail)`. "Run All Tests" button calling `POST /v1/formulas/{id}/run-tests`.
4. **DAG Graph View Modal:**
   * Visual graph rendering execution order.
   * Render `same_step_dependencies` as solid connecting arrows.
   * Render `prior_dependencies` as dashed curved feedback arrows.

---

### 4.2. Expected Results Benchmarks Component (`/projects/[projectId]/benchmarks`)
1. **Upload Modal:**
   * Drag-and-drop CSV uploader (max 10MB).
   * Fields: Benchmark Name, Product Code (`MYGA`, `SPIA`, etc.), Version Label (`v1.0`), Granularity radio button (`policy_month` vs `aggregate_year`), Timing Convention dropdown (`eom`, `bom`).
2. **Benchmark Table:**
   * Displays Name, Product, Granularity, Row Count, Fingerprint (truncated SHA-256 with copy button), Created At.
   * "View Data" drawer showing first 50 rows via `GET /v1/expected-results/{id}?include_rows=true`.

---

### 4.3. Run Reconciliation Dashboard (`/runs/[runId]/reconciliation`)
1. **Reconciliation Launcher:**
   * Dropdown selecting benchmark from project expected results matching run product.
   * Granularity selection (`policy_month`, `aggregate_year`, `both`).
   * Tolerances input: Absolute (default `0.01`), Relative (default `0.0001`).
   * "Run Reconciliation" button calling `POST /v1/runs/{runId}/reconcile`.
2. **Results Header:**
   * Large Status Banner: Green `PASS` or Red `FAIL`.
   * Metric Cards: Total Cells Checked, Total Failed, Max Absolute Diff, Max Relative Diff.
   * Source Verification Pill: Green `SOURCE VERIFIED`, Red `SOURCE MISMATCH`, or Yellow `SOURCE NOT VERIFIABLE`.
3. **Breakdown Tables:**
   * By Variable table showing cells checked, max difference, and status.
   * By Granularity table (`policy_month` vs `aggregate_year`).
4. **Cell Difference Viewer (on FAIL):**
   * Filterable/searchable table of failing cells with columns: Level, Policy ID, Month, Variable, Actual, Expected, Difference, Tolerance.
5. **Actuary Sign-Off Action:**
   * "Review & Sign-Off" button opening the Disposition Modal.
   * Disposition Modal:
     * Radio buttons: `Approve`, `Approve with Exception`, `Reject`.
     * If report is `FAIL`, disable `Approve` option with explanatory tooltip: *"Cannot approve failing report without exception."*
     * Textarea for `Reviewer Notes` (required if exception selected).
     * Submits to `POST /v1/reconciliations/{id}/disposition`.
   * Timeline widget displaying `disposition_history` entries (timestamp, user, action, notes).

---

## 5. Error Code Reference

When handling `422` or `409` errors, inspect `error.code` in the JSON response:

| Error Code | HTTP Status | Description / User Message |
| :--- | :--- | :--- |
| `PAYLOAD_TOO_LARGE` | 422 | Upload file exceeds 10 MB limit. |
| `DUPLICATE_EXPECTED_RESULT_KEY` | 422 | The uploaded benchmark contains duplicate keys (e.g. duplicate Policy ID and Month). |
| `EXPECTED_RESULTS_PRODUCT_MISMATCH` | 422 | Benchmark product does not match the model product (e.g. comparing MYGA against SPIA). |
| `EXPECTED_RESULTS_TIMING_MISMATCH` | 422 | Cash flow timing mismatch (e.g. EOM vs BOM). |
| `EXPECTED_RESULTS_GRANULARITY_MISMATCH` | 422 | Requested level is incompatible with benchmark granularity. |
| `RECONCILIATION_DISPOSITION_INVALID` | 422 | Attempted to approve a failed report without exception, or missing mandatory reviewer notes. |
| `FORMULA_OUTPUT_NOT_DEFINED` | 422 | The target output variable is not registered in this model version. |
| `DSL_SYNTAX_ERROR` | 422 | Syntax error in formula expression string. |
| `MODEL_VERSION_MUTATION_FORBIDDEN` | 409 | Model version is approved/published and cannot be modified. |

---

## 6. End-to-End Testing Walkthrough for Sam

1. **Test Formula Studio:**
   * Create a draft model version or navigate to an existing draft.
   * Call `POST /v1/formulas/validate-expression` with `expression_text: "prior(balance) * 1.05 + deposit"`. Confirm `prior_dependencies: ["balance"]`.
   * Add test case `{ "inputs": { "deposit": 100 }, "prior_inputs": { "balance": 1000 }, "expected_output": 1150 }`.
   * Call `POST /v1/formulas/{id}/run-tests` and verify `all_passed: true`.
2. **Test Expected Results & Reconciliation:**
   * Upload an expected results CSV to `/v1/projects/{projectId}/expected-results`.
   * Run a projection run for the matching product.
   * Call `POST /v1/runs/{runId}/reconcile` with `level: "both"`.
   * Verify the report renders `PASS` or `FAIL` with `source_to_output_check`.
   * Submit reviewer disposition via `POST /v1/reconciliations/{id}/disposition` and verify the audit history updates.
