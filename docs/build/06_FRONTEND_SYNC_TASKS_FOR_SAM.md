# Frontend Tasks & Integration Master Specification: Bringing MentorAmp UI to Full M2B Production Parity

| Metadata | Details |
|---|---|
| **Target Audience** | Sam Yung (Lead Frontend Engineer) |
| **From / Reviewer** | Samuel Ayinla (Technical Lead / Core Backend Architecture) |
| **Backend Release** | Milestone 2B (M2B) — 5-Year MYGA Governed Valuation Engine & Governance Hardening |
| **Committed Backend HEAD** | `5e48606c98b2ae4895cf76d5e9ccf0901bfbd501` (Branch: `feat/m2b-delivery`) |
| **Backend API Base Path** | `/v1` |
| **Status** | **APPROVED FOR FRONTEND CONSUMPTION** |

---

## 0. Executive Summary: What Noah and Samuel Just Delivered in M2B

Milestone 2B delivers the **5-Year Multi-Year Guarantee Annuity (MYGA)** into MentorAmp's unified, governed calculation engine.

### Key Architectural Truths for the Frontend:
1. **Unified Engine Architecture:** There are **no separate product calculation engines or custom run routes** for MYGA. MYGA runs through the same declarative formula graph, Variable Resolver, and execution pipeline (`POST /v1/run-sets`) as SPIA.
2. **Dynamic Product Discovery:** The backend registers products dynamically. `GET /v1/system/status` returns `engine.products: ["SPIA", "MYGA"]`. The frontend must not hardcode SPIA-only assumptions.
3. **Illustrative Status Badging:** All MYGA runs, summaries, outputs, and exports carry `illustrative: true`. All MYGA screens must prominently display the **`Illustrative`** badge. The model uses contractual minimum guarantees and illustrative decrement assumptions (not statutory Model #805 or VM-22 certifications).
4. **Actuarial Death Benefit Decision Framing:** The death benefit is documented in backend metadata as `STATUS: NEEDS ACTUARIAL PRODUCT OWNER DECISION` (currently calculating Option A: Account Value post beginning-of-month withdrawal). The UI should display the active option and indicate that final statutory filing options (Option B: $\max(\text{AV}, \text{CSV})$ or Option C: $\max(\text{AV}, \text{MGSV})$) are pending product owner confirmation.
5. **Two-Person Data & Model Governance:** 
   - A user cannot approve their own dataset import (`409 SELF_APPROVAL_FORBIDDEN`).
   - A user who modified a model version cannot approve it (segregation of duties).
   - Approval requires a second user with the `reviewer` or `admin` role (`models:approve` permission).
6. **Strict Schema Versioning:** Inforce files default to `v1` (`mentoramp.myga.inforce/v1`). Unsupported explicit versions (e.g. `v99`) fail validation immediately with code `INFORCE_SCHEMA_VERSION_NOT_SUPPORTED`.

---

## 1. Screen-by-Screen Implementation Guide (What to Add & Wire)

### Screen 1: Topbar & Global Navigation Shell (`AppShell.tsx`, `Topbar.tsx`, `Sidebar.tsx`)
- **System Discovery:** On load, call `GET /v1/system/status`.
  * If `engine.products` contains `"MYGA"`, show `"MYGA"` in all product dropdowns across the application.
  * Display system health indicator and backend engine version (`v1.0`).
- **Product Filter / Context:** Allow user to filter by Product (`All`, `SPIA`, `MYGA`).
- **Illustrative Badge Component:** Create a reusable `<IllustrativeBadge product="MYGA" />` component.
  * Visual: Amber pill badge with label `Illustrative Model`.
  * Tooltip: `"Uses contractual minimum guarantees and illustrative decrement assumptions. Not certified for statutory NAIC Model #805 or VM-22 reporting."`

---

### Screen 2: Model Library & Workspace (`ModelLibrary.jsx`, `ModelWorkspace.jsx`)
- **Backend Endpoints:**
  * `GET /v1/models?project_id={id}`
  * `GET /v1/models/{id}/versions`
  * `GET /v1/model-versions/{id}/structure`
  * `GET /v1/model-versions/{id}/variables`
  * `POST /v1/projects/{id}/models`
  * `POST /v1/models/{id}/versions`
  * `POST /v1/model-versions/{id}/validate`
  * `POST /v1/model-versions/{id}/approve`
  * `POST /v1/model-versions/{id}/reject`
  * `POST /v1/model-versions/{id}/publish`
- **UI Elements & Actions to Add:**
  1. **Lifecycle Badges:** Handle 6 distinct version statuses: `draft`, `needs_review`, `validated`, `approved`, `published`, `archived`.
  2. **"Validate Model" Action Button:** Triggers `POST /v1/model-versions/{id}/validate`. Surfaces formula syntax check and unresolved variable warnings.
  3. **"Approve Version" Button:** Triggers `POST /v1/model-versions/{id}/approve`.
     * *Disabled Rule:* Disable with tooltip `"Segregation of duties: You modified this version and cannot approve it"` if `user.id === version.created_by_user_id || user.id === version.last_modified_by_user_id`. Requires `reviewer` or `admin` role.
  4. **"Reject Version" Button:** Opens a modal dialog prompting for `rejection_reason` (string, minimum 5 characters), then sends `POST /v1/model-versions/{id}/reject` with `{"rejection_reason": "..."}`.
  5. **"Publish Version" Button:** Triggers `POST /v1/model-versions/{id}/publish`. Once published, display a padlock icon ("Write-once published version").
  6. **Actuarial Decision Marker:** In the model structure/variable tab, display an info alert next to `death_benefit`:
     * Label: `STATUS: NEEDS ACTUARIAL PRODUCT OWNER DECISION`
     * Active Formula: `Option A: Account Value (post beginning-of-month withdrawal)`
     * Alternative Options: `Option B: max(AV, CSV)`, `Option C: max(AV, MGSV)`

---

### Screen 3: Data Manager (`DataManager.tsx`)
- **Backend Endpoints:**
  * `POST /v1/projects/{id}/import-sessions`
  * `POST /v1/import-sessions/{id}/upload`
  * `GET /v1/import-sessions/{id}/preview`
  * `GET /v1/mapping-profiles?project_id={id}&category={category}`
  * `POST /v1/mapping-profiles`
  * `POST /v1/import-sessions/{id}/validate`
  * `GET /v1/validation-runs/{id}/issues`
  * `GET /v1/validation-runs/{id}/rejected-records`
  * `POST /v1/import-sessions/{id}/commit`
  * `POST /v1/datasets/{category}/{id}/approve`
  * `POST /v1/datasets/{category}/{id}/reject`
  * `GET /v1/inputs/inforce/{id}/records?page=1&page_size=50`
- **UI Elements & Actions to Add:**
  1. **Four Category Tabs:**
     - `Liability In-force` (`liability_inforce`)
     - `Assumption Tables` (`assumption_table`)
     - `Factor Tables` (`factor_table`) — **REQUIRED FOR MYGA** (Surrender charges & SC Age caps)
     - `Economic Scenarios` (`scenario`) — **REQUIRED FOR MYGA** (Market rate, MVA reference yield, discount rate)
  2. **In-force Schema Selection:**
     - Expose schema selector: `mentoramp.myga.inforce/v1` (for MYGA) and `mentoramp.spia.inforce/v1` (for SPIA).
     - Inform user that omitting schema defaults safely to `v1`, while unknown schemas (e.g. `v99`) are rejected with `INFORCE_SCHEMA_VERSION_NOT_SUPPORTED`.
  3. **Harmonized Aliasing Support:** In the mapping step, if user CSV has `guarantee_term_years` or `credited_rate`, auto-suggest canonical names `guarantee_period_years` and `declared_rate`.
  4. **Validation Error Panel:** Render structured error issues returned by `/issues`:
     - Line number, record key (`policy_id`), offending column, and error code.
     - Specific codes to format nicely: `GMSV_RATE_BELOW_MINIMUM`, `INVALID_GUARANTEE_PERIOD`, `NEGATIVE_AMOUNT`, `VALUE_NOT_FINITE`, `TYPE_MISMATCH`.
  5. **Approval Segregation:** If `current_user.id === dataset.uploaded_by_user_id`, display `"Awaiting second-person approval (Reviewer role required)"` and disable the "Approve" button.
  6. **In-force Record Viewer:** Add a "View In-force Data" button that calls `GET /v1/inputs/inforce/{id}/records?page={p}&page_size=50` with server-side pagination and column sorting.

---

### Screen 4: Projection Sets Library (`ProjectionSetsLibrary.jsx`, `ProjectionSetCreateDialog.tsx`)
- **Backend Endpoints:**
  * `GET /v1/projects/{id}/projection-sets`
  * `GET /v1/projection-sets/{id}`
  * `POST /v1/projects/{id}/projection-sets`
  * `PATCH /v1/projection-sets/{id}`
  * `POST /v1/projection-sets/{id}/duplicate`
  * `POST /v1/projection-sets/{id}/scenarios`
  * `POST /v1/projection-sets/{id}/validate`
- **UI Elements & Actions to Add:**
  1. **Product Selection:** Select `MYGA` or `SPIA`. Selecting `MYGA` filters compatible model versions and dataset categories.
  2. **Factor Tables Multi-Selector:** For MYGA, allow selecting:
     * Surrender Charge table (e.g. `myga_surrender_charge`)
     * Surrender Charge Age Cap table (e.g. `myga_sc_age_cap`)
  3. **Run Parameters Form:**
     * `decrement_methodology`: Dropdown with options:
       - `Best Estimate (default)` (`"best_estimate"`)
       - `Zero Decrements (pure account rollforward)` (`"zero_decrements"`)
  4. **Trace Scope Configuration:**
     * Mode selector: `None` (`"none"`), `Selected Policies` (`"selected_policies"`), `All Policies` (`"all"`).
     * If `selected_policies`: comma-separated input field (default placeholder: `MYGA-0001`).
  5. **Output Variables Multi-Select (Default Pre-selected for MYGA):**
     * `account_value_close`, `mgsv_close`, `surrender_benefit`, `death_benefit`, `death_claims`, `surrender_claims`, `annuitization_transfer`, `maturity_benefit`, `maintenance_expense`, `liability_outgo`, `net_cash_flow`, `reserve`.
  6. **"Validate Configuration" Button:** Calls `/v1/projection-sets/{id}/validate`. If validated, show green shield icon ("Validated for Execution").

---

### Screen 5: Execution Center (`ExecutionCenter.jsx`, `RunSetWorkflow.jsx`)
- **Backend Endpoints:**
  * `POST /v1/run-sets`
  * `GET /v1/run-sets/{id}`
  * `GET /v1/runs?project_id={id}`
  * `GET /v1/runs/{id}`
  * `POST /v1/runs/{id}/cancel`
  * `POST /v1/runs/{id}/retry`
  * `GET /v1/runs/{id}/package`
  * `GET /v1/runs/{id}/manifest`
  * `GET /v1/runs/{id}/attempts`
- **UI Elements & Actions to Add:**
  1. **Run Status Vocabulary:** Fix status mappings:
     * **Runs:** `pending`, `running`, `success`, `partial_success`, `failed`, `cancelled`.
     * **Run Sets:** `queued`, `running`, `success`, `partial_success`, `failed`, `cancelled`.
  2. **"Cancel Run" Button:** Call `POST /v1/runs/{id}/cancel`. Enabled only when `can_cancel === true`.
  3. **"Retry Run" Button:** Call `POST /v1/runs/{id}/retry`. Enabled only when `can_retry === true` (status is `failed` or `cancelled`).
  4. **Run Package Drawer / Modal ("View Frozen Package"):**
     * Show build identity, git commit SHA, model fingerprint, and execution parameter `decrement_methodology`.
  5. **Attempt History List:** Show list of execution attempts (`attempt_number`, `started_at`, `status`, Parquet artifact checksum).

---

### Screen 6: Results Explorer (`ResultsExplorer.jsx`)
- **Backend Endpoints:**
  * `GET /v1/runs/{id}/summary`
  * `GET /v1/runs/{id}/results?page=1&page_size=50`
  * `GET /v1/runs/{id}/export.csv`
  * `GET /v1/runs/{id}/reports/reconciliation?baseline_run_id={id}`
- **UI Elements & Actions to Add:**
  1. **Illustrative Status Badge:** Display amber `Illustrative Model Result` badge next to the Run Name.
  2. **Incomplete Results Handling:** If `run.status === "partial_success"`:
     * Display amber warning banner: `"This run completed partially. Results are incomplete."`
     * Include button: `"Show Incomplete Results"`.
     * Pass `include_partial=true` query parameter to summary, results, and export endpoints only after user confirmation.
  3. **MYGA Metric Tiles:**
     * **Prospective Reserve:** Displays `summary.headline.value` (Formatted as currency, e.g. `$4,163,674.90`).
     * **Undiscounted Liability Outgo:** Displays `summary.totals.liability_outgo.value` (e.g. `$5,298,179.06`).
     * **Surrender Claims:** Displays `summary.totals.surrender_claims.value`.
     * **Death Claims:** Displays `summary.totals.death_claims.value`.
     * **Maintenance Expenses:** Displays `summary.totals.maintenance_expense.value`.
  4. **Policy Results Grid:** Paged tabular display of monthly projection outputs:
     * Columns: Month, Policy ID, Account Value, MGSV Floor, Surrender Benefit, Death Benefit, Liability Outgo.
  5. **CSV Export Completeness Header:** When downloading `/export.csv`, check response header `X-MentorAmp-Complete`. If `"false"`, show toast notification: `"Warning: Downloaded export contains incomplete projection rows."`

---

### Screen 7: Reserve Investigation & Trace Explorer (`ReserveInvestigation.jsx`)
- **Backend Endpoints:**
  * `GET /v1/runs/{run_id}/trace?policy_id={policy_id}&month={month}&variable={variable}&depth=8`
  * `GET /v1/runs/{run_id}/trace/dependents`
- **UI Elements & Actions to Add:**
  1. **Policy & Month Selector:**
     * Policy ID input: Defaults to traced policies (e.g. `MYGA-0001`).
     * Projection Month slider / input: 1 to 480 (default: 30).
     * Variable dropdown: `surrender_benefit`, `account_value_close`, `liability_outgo`, `reserve`.
  2. **Hierarchical Tree Explorer:**
     * Render the nested tree from `root` and recursive `children` nodes.
     * Each tree node displays: Variable name, computed value, formula expression, and source type (`formula`, `table_lookup`, `input`, `prior_period`).
  3. **Decomposition View for MYGA Surrender Benefit:**
     * Visually display how contractual surrender value is computed and floored:
       $$\text{surrender\_benefit} = \max(\text{surrender\_value}, \text{mgsv\_close})$$
       $$\text{surrender\_value} = \text{account\_value\_close} + \text{market\_value\_adjustment} - \text{surrender\_charge}$$
       $$\text{surrender\_excess} = \text{account\_value\_close} - \text{free\_withdrawal\_available}$$
       $$\text{MVA bounds: } [-\text{surrender\_charge}, +\text{surrender\_charge}]$$
  4. **Factor Table Source Inspection:**
     * Clicking the `surrender_charge_rate` node displays the table name (`myga_surrender_charge`), lookup keys (`schedule = "initial"`, `year_in_gp = 3`), and rate matched (`0.07` / $7\%$).

---

### Screen 8: Admin & Security Settings (`AdminSettings.tsx`)
- **Backend Endpoints:**
  * `GET /v1/users/` (admin only)
  * `POST /v1/users/` (admin only)
  * `POST /v1/projects/{id}/members`
  * `GET /v1/audit-logs/?project_id={id}`
- **UI Elements & Actions to Add:**
  1. **User Management Table:**
     * List all users with email, full name, and roles.
     * Roles: `admin`, `actuary`, `model_developer`, `reviewer`, `read_only`.
  2. **Create User Modal:** Allows admin to provision users (needed to create the second reviewer account for dual-control approvals).
  3. **Project Membership Management:** Assign users to projects as `owner`, `editor`, or `viewer`.
  4. **Audit Trail Explorer:** Chronological table of audit events (`timestamp`, `actor`, `action`, `entity_type`, `before`/`after` diff preview).

---

## 2. Complete API Contracts, Request Schemas & Payloads

### A. System Status
- **Method & Route:** `GET /v1/system/status`
- **Response Shape (200 OK):**
```json
{
  "status": "healthy",
  "app": "MentorAmp Backend",
  "version": "1.0.0",
  "app_env": "development",
  "database": {
    "status": "connected",
    "latency_ms": 1.25
  },
  "engine": {
    "registered_functions": 35,
    "products": ["SPIA", "MYGA"],
    "execution_backend": "declarative_graph",
    "engine_version": "v1.0"
  },
  "time": "2026-10-24T03:30:00Z"
}
```

---

### B. In-force Data Schema (`mentoramp.myga.inforce/v1`)
All 14 fields required for MYGA inforce:

| Field Name | Type | Unit / Format | Description & Rules |
|:---|:---|:---|:---|
| `policy_id` | String | Unique ID | Stable policy identifier (e.g. `"MYGA-0001"`) |
| `product_type` | String | Code | Must be strictly `"MYGA"` |
| `issue_date` | Date | `YYYY-MM-DD` | ISO 8601 policy inception date |
| `issue_age` | Integer | Years | $40 \le \text{age} \le 85$ |
| `gender` | String | `"M"` or `"F"` | Attained mortality lookup key |
| `premium` | Float | USD | Single gross premium ($> 0$) |
| `guarantee_period_years` | Integer | Years | Term of initial rate guarantee (must be `5`) |
| `declared_rate` | Float | Decimal annual | Guaranteed credited interest rate (e.g. `0.045`) |
| `gmir` | Float | Decimal annual | Guaranteed minimum interest rate floor (e.g. `0.0025`) |
| `gmsv_rate` | Float | Decimal annual | Nonforfeiture floor rate ($\ge 0.0015$, e.g. `0.015`) |
| `mva_ref_yield_at_issue` | Float | Decimal annual | Benchmark CMT reference yield at issue (e.g. `0.050`) |
| `account_value` | Float | USD | Opening account value balance ($\ge 0$) |
| `mgsv_value` | Float | USD | Opening nonforfeiture floor ($87.5\%$ premium load) |
| `free_wd_base` | Float | USD | Free withdrawal base amount (typically equals opening AV) |

---

### C. Factor Tables Specifications

#### 1. Surrender Charges (`factor_table`)
- **Table Name:** `myga_surrender_charge`
- **Columns:**
  * `schedule`: String (`"initial"` or `"renewal"`)
  * `year_in_gp`: Integer (`1`, `2`, `3`, `4`, `5`)
  * `rate`: Float decimal (e.g. `0.09`, `0.08`, `0.07`, `0.06`, `0.05`)

#### 2. Age Caps (`factor_table`)
- **Table Name:** `myga_sc_age_cap`
- **Columns:**
  * `attained_age`: Integer (e.g. $40, 41, \dots, 90$)
  * `cap`: Float decimal maximum allowable charge rate (stepping down to `0.00` at age 85+)

---

### D. Economic Scenarios (`scenario`)
- **Columns:** `scenario_name`, `target_variable`, `operation`, `value`, `applies_from_period`, `applies_to_period`.
- **Governed MYGA Variables:**
  * `market_rate`: Reinvestment yield benchmark (`operation: "set"`). Base = `0.0445`, Rates Up = `0.0595`.
  * `mva_ref_yield`: Prevailing CMT reference yield (`operation: "set"`). Base = `0.050`, Rates Up = `0.065`.
  * `discount_rate_annual`: Liability valuation discount rate (`operation: "set"`, constant at `0.045`).
  * `withdrawal`: Monthly policyholder withdrawal amount (`operation: "set"`, defaults to `0.0`).

---

### E. Projection Set Creation Payload
- **Method & Route:** `POST /v1/projects/{project_id}/projection-sets`
- **Request Body:**
```json
{
  "name": "MYGA Illustrative Base and Rates Up",
  "model_version_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "inforce_file_ids": ["c34c6996-c2f8-45e3-869c-7c2e19f0298a"],
  "assumption_table_ids": ["a12f6890-410a-4299-bb63-54890123ef01"],
  "factor_table_ids": [
    "f2389104-5812-4a01-9213-481902481023",
    "b8912401-4192-4912-8192-389102481204"
  ],
  "scenario_ids": [
    "e9124012-4812-4912-8912-491204812048",
    "d8192401-4124-4812-9812-489120481204"
  ],
  "valuation_date": "2026-12-31",
  "horizon_months": 480,
  "time_step": "monthly",
  "output_variables": [
    "account_value_close",
    "mgsv_close",
    "surrender_benefit",
    "death_benefit",
    "death_claims",
    "surrender_claims",
    "annuitization_transfer",
    "maturity_benefit",
    "maintenance_expense",
    "liability_outgo",
    "net_cash_flow",
    "reserve"
  ],
  "trace_scope": {
    "mode": "selected_policies",
    "policy_ids": ["MYGA-0001"]
  },
  "parameters": {
    "decrement_methodology": "best_estimate"
  }
}
```

---

### F. Run Set Submission & Summary Payloads
- **Submit Run Set:** `POST /v1/run-sets`
  ```json
  {
    "project_id": "c3722056-7620-4d61-b524-0b5499e3874a",
    "name": "MYGA Valuation Run Set",
    "projection_set_ids": ["p1234567-89ab-cdef-0123-456789abcdef"],
    "scenario_ids": ["e9124012-4812-4912-8912-491204812048"]
  }
  ```
- **Read Summary:** `GET /v1/runs/{run_id}/summary`
  ```json
  {
    "run_id": "5e83be6c-556d-49b5-a9c4-c8f68f122338",
    "status": "success",
    "failed_policy_count": 0,
    "illustrative": true,
    "headline": {
      "variable": "reserve",
      "value": 4163674.90181,
      "unit": "USD"
    },
    "totals": {
      "liability_outgo": { "value": 5298179.05845, "unit": "USD" },
      "surrender_claims": { "value": 3120481.12045, "unit": "USD" },
      "death_claims": { "value": 1845698.24102, "unit": "USD" },
      "maintenance_expense": { "value": 331999.69698, "unit": "USD" }
    }
  }
  ```

---

### G. Trace Explorer Response Shape
- **Method & Route:** `GET /v1/runs/{id}/trace?policy_id=MYGA-0001&month=30&variable=surrender_benefit&depth=8`
- **Response Structure (200 OK):**
```json
{
  "run_id": "5e83be6c-556d-49b5-a9c4-c8f68f122338",
  "policy_id": "MYGA-0001",
  "month": 30,
  "variable": "surrender_benefit",
  "root": {
    "variable": "surrender_benefit",
    "value": 51979.59926,
    "formula": "max(surrender_value, mgsv_close)",
    "source": { "type": "formula" },
    "children": [
      {
        "variable": "surrender_value",
        "value": 51979.59926,
        "formula": "account_value_close + market_value_adjustment - surrender_charge",
        "source": { "type": "formula" },
        "children": [
          {
            "variable": "account_value_close",
            "value": 53421.15234,
            "source": { "type": "formula" },
            "children": []
          },
          {
            "variable": "market_value_adjustment",
            "value": 0.0,
            "source": { "type": "formula" },
            "children": []
          },
          {
            "variable": "surrender_charge",
            "value": 1441.55308,
            "formula": "surrender_excess * surrender_charge_rate",
            "source": { "type": "formula" },
            "children": [
              {
                "variable": "surrender_charge_rate",
                "value": 0.07,
                "source": {
                  "type": "table_lookup",
                  "table": "myga_surrender_charge",
                  "keys": { "schedule": "initial", "year_in_gp": 3 }
                },
                "children": []
              }
            ]
          }
        ]
      },
      {
        "variable": "mgsv_close",
        "value": 46820.12543,
        "source": { "type": "formula" },
        "children": []
      }
    ]
  }
}
```

---

## 3. Reference Golden Targets (For UI Validation Testing)

When running the 25-policy synthetic portfolio (`valuation_date: "2026-12-31"`, 480-month horizon), verify that the UI renders the exact expected golden numbers:

| Metric / Checkpoint | Base Scenario | Rates Up 150bp Scenario |
|:---|---:|---:|
| **Prospective Reserve** | **$4,163,674.90** | **$4,201,201.55** |
| **Total Liability Outgo** | **$5,298,179.06** | **$5,358,149.66** |
| **Policy `MYGA-0001` Month 30 Surrender Benefit** | **$51,979.60** | — |
| **Failed Policy Count** | **0** | **0** |
| **Execution Status** | `success` | `success` |

---

## 4. End-to-End Acceptance Test Checklist for Sam

Follow this 7-step test sequence to verify frontend merge-readiness:

- [ ] **Test 1: System Discovery & Badging:**
  * Open MentorAmp with backend running.
  * Verify `GET /v1/system/status` returns `MYGA`. Confirm `MYGA` appears in the Model, Input, and Projection Set product dropdowns.
  * Verify the amber `Illustrative Model` badge is displayed.
- [ ] **Test 2: Two-User Dual Control Dataset Approval:**
  * Log in as `admin` (or `actuary`). Upload `synthetic_myga_inforce.csv` through Data Manager.
  * Confirm that the uploader sees `"Awaiting Reviewer Approval"` and the "Approve" button is disabled.
  * Log in as `reviewer`. Verify the "Approve" button is enabled. Click "Approve" and confirm dataset transitions to `approved`.
- [ ] **Test 3: Model Version Validation & Publication:**
  * Open MYGA model version. Click "Validate". Verify validation passes.
  * Attempt approval with modifying user $\rightarrow$ confirm `409` or disabled button.
  * Approve with secondary reviewer $\rightarrow$ Click "Publish". Confirm version becomes locked (`published`).
  * Verify the Actuarial Decision Alert is visible on `death_benefit` (`STATUS: NEEDS ACTUARIAL PRODUCT OWNER DECISION`).
- [ ] **Test 4: Projection Set Assembly:**
  * Create projection set with:
    - Model: Published MYGA model version.
    - In-force: Approved MYGA block (25 policies).
    - Mortality: `SYNTH_MORT_GOMPERTZ_2026`.
    - Factor Tables: `myga_surrender_charge` and `myga_sc_age_cap`.
    - Scenarios: `Base` and `Rates Up 150bp`.
    - Parameters: `decrement_methodology: "best_estimate"`.
    - Trace Scope: `mode: "selected_policies"`, `policy_ids: ["MYGA-0001"]`.
  * Click "Validate Projection Set" $\rightarrow$ confirm green validated badge.
- [ ] **Test 5: Execution & Run Control:**
  * Submit Run Set. Observe live progress indicator transition from `queued` $\rightarrow$ `running` $\rightarrow$ `success`.
  * Test Cancel / Retry on a test run to confirm button visibility and state updates.
- [ ] **Test 6: Results Verification:**
  * In Results Explorer, confirm:
    - Base Reserve: `$4,163,674.90`.
    - Rates Up Reserve: `$4,201,201.55`.
    - Outgo: `$5,298,179.06` (Base) and `$5,358,149.66` (Rates Up).
  * Download CSV export. Confirm file downloads cleanly and completeness badge shows "Complete".
- [ ] **Test 7: Trace Explorer Investigation:**
  * Navigate to Investigate screen. Select Policy `MYGA-0001`, Month 30, Variable `surrender_benefit`.
  * Verify the interactive tree expands to show:
    - `surrender_benefit`: `$51,979.60`.
    - Account Value close, MVA ($0.00$), Surrender Charge ($1,441.55$), and MGSV floor ($46,820.13$).
    - Surrender charge rate lookup node: shows table `myga_surrender_charge` with rate `0.07`.
