# Backend Handoff: Frontend MYGA Integration Testing and Fixes

**Project:** MentorAmp

**Scope:** MYGA frontend integration against the checked-out M2B backend

**API base:** `http://localhost:8001/v1`

**Frontend:** `http://localhost:5173`


---

## 1. Executive summary

The frontend was launched against the live local backend and exercised through the authenticated UI. The MYGA workflow was tested end to end through the same governed pipeline described in the integration documents:

1. Dynamic product discovery and system status
2. Governed MYGA input data import and approval
3. MYGA projection-set configuration and validation
4. Run-set submission and execution
5. Cancellation and retry lifecycle
6. Results, aggregates, CSV export, and provenance
7. Trace-policy discovery and recursive trace retrieval
8. Frontend rendering of the resulting live data

The frontend now successfully consumes the live MYGA backend data and renders the completed 25-policy result set. Two frontend issues found during live UI testing were fixed:

- MYGA summary totals were rendered as `$NaN` because the backend returns totals as `{ value, unit, label }` objects.
- Trace investigation initially opened on month `0` with `account_value_close`, although the available captured trace begins at month `1`. The selector now defaults to month `1`.

The frontend production build and lint checks pass after the fixes.

---

## 2. Backend connectivity and system discovery

### Tested routes

```text
GET /health
GET /openapi.json
POST /v1/auth/token
GET /v1/system/status
```

### Observed results

```json
{
  "status": "ok",
  "app": "MentorAmp",
  "version": "0.1.0",
  "app_env": "local",
  "auth_mode": "jwt",
  "database": {
    "status": "ok",
    "latency_ms": 0
  },
  "engine": {
    "registered_functions": 101,
    "products": ["SPIA", "MYGA"],
    "execution_backend": "cpu",
    "engine_version": "m1-cpu-1"
  }
}
```

The frontend dynamically exposes MYGA in the global product selector and displays the actual backend status and engine identifier. It does not rely on a hardcoded SPIA-only product list.

---

## 3. Governed MYGA input workflow tested

The live test database was initialized with the MYGA model and disposable project data. The following MYGA input categories were exercised through the backend and then confirmed in the Data Manager UI:

- Liability in-force data
- Mortality assumption table
- Surrender-charge factor table
- Surrender-charge age-cap factor table
- Economic scenario data

The Data Manager rendered committed import history for the live test data, including:

```text
live_myga_scenario.csv                         committed
myga_sc_age_cap.csv                            committed
myga_surrender_charge.csv                     committed
synthetic_mortality_gompertz.csv               committed
synthetic_myga_inforce.csv                     committed
```

An intentionally invalid mortality import was also present in the history, confirming that the invalid/committed distinction is visible in the UI.

### Contract alignment implemented in the frontend

The frontend follows the checked-out backend contract rather than blindly copying the original guide where the implementation differs:

- Import sessions use the actual import-session routes.
- Upload uses the backend file-upload route.
- In-force records use `limit`/`offset`, not unsupported `page`/`page_size` parameters.
- Dataset approval uses the backend committer identity (`committed_by`).
- Approval permissions use the actual `imports:approve` capability.
- Self-approval errors such as `SELF_APPROVAL_FORBIDDEN` are surfaced.
- Model rejection uses `{ "reason": "..." }`.
- Audit-log queries do not send the unsupported `project_id` filter.

---

## 4. Projection-set configuration tested

A live MYGA projection set was created and validated using the governed inputs. The configuration included:

- MYGA model version
- Approved MYGA in-force data
- Mortality assumption table
- Surrender-charge factor table
- Surrender-charge age-cap factor table
- Economic scenario
- Monthly horizon of 480 months
- `best_estimate` decrement methodology
- Selected-policy trace scope for `MYGA-0001`
- The documented MYGA output variables, including:
  - `account_value_close`
  - `mgsv_close`
  - `surrender_benefit`
  - `death_benefit`
  - `death_claims`
  - `surrender_claims`
  - `annuitization_transfer`
  - `maturity_benefit`
  - `maintenance_expense`
  - `liability_outgo`
  - `net_cash_flow`
  - `reserve`

The Projection Sets UI displayed the validated configuration and the MYGA-specific required data categories.

---

## 5. Run execution and lifecycle testing

### Run submission

The frontend submitted a real MYGA run through the unified route:

```text
POST /v1/run-sets
```

No custom MYGA execution endpoint was used.

### Successful run

Live completed run:

```text
Run ID: c60e1e69-be51-44c7-a7f0-88115dfa2e3e
Name: Live MYGA Acceptance Projection · Base (retry)
Status: success
Policies: 25 / 25
Projection horizon: 480 months
Trace mode: selected_policies
Traced policy: MYGA-0001
```

The Runs workspace displayed the backend execution events, including queueing, claim, package loading, policy completion, completion, and finalization.

### Cancellation and retry

The live cancel/retry probe produced the expected lifecycle:

```text
Original run:  b6c2faca-273d-4c89-b6ba-4aa33fdce7ee
Status:        cancelled
Progress:      0 / 25 policies

Retry run:     c60e1e69-be51-44c7-a7f0-88115dfa2e3e
Status:        success
Progress:      25 / 25 policies
```

The UI displayed:

- Cancel request and cancellation events
- Retry creation event
- Frozen configuration carried into the retry
- Per-policy progress
- Final successful retry result

The frontend gates cancellation and retry actions using the backend capability fields rather than assuming that every status supports both actions.

---

## 6. Results and regression benchmark verification

### Tested routes

```text
GET /v1/runs/{run_id}/summary
GET /v1/runs/{run_id}/results
GET /v1/runs/{run_id}/aggregates
GET /v1/runs/{run_id}/export.csv
GET /v1/comparisons?baseline_run_id=...&current_run_id=...
```

### Live summary values

The completed MYGA run returned:

```text
Illustrative: true
Policies: 25
Periods: 480
Output rows: 144,025
```

The frontend displayed the following live values:

| Metric | Live value |
|---|---:|
| Prospective reserve | $4,163,674.90 |
| Liability outgo | $5,298,179.06 |
| Surrender claims | $4,819,285.88 |
| Death claims | $334,282.68 |
| Maintenance expense | $7,391.72 |

The reserve and liability-outgo values match the documented 25-policy frozen regression benchmarks after currency rounding.

The Results Explorer displayed the complete paginated result grid. The first rows included policy `MYGA-0001`, month `0`/`1`, reserve, account value, death claims, surrender claims, maintenance expense, and liability outgo values.

### CSV export

The live CSV export returned:

```text
HTTP status: 200
X-MentorAmp-Complete: true
```

The frontend reads `X-MentorAmp-Complete` and is prepared to warn the user when a partial export is returned.

### Partial-result behavior

A cancelled run remained unavailable as a result-bearing run. The frontend only requests incomplete result rows or CSV data after explicit user confirmation and sends `include_partial=true` only to routes that support it. It does not send that unsupported parameter to the summary route.

---

## 7. Trace and investigation testing

### Tested routes

```text
GET /v1/runs/{run_id}/trace/policies
GET /v1/runs/{run_id}/trace?policy_id=...&month=...&variable=...&depth=...
```

The live trace-policy route returned:

```json
{
  "mode": "selected_policies",
  "policy_ids": ["MYGA-0001"],
  "months": 480,
  "complete": true
}
```

The documented benchmark trace was then queried successfully:

```text
Policy: MYGA-0001
Month: 30
Variable: surrender_benefit
Depth: 8
```

Observed root result:

```text
Value: 51,979.599258 USD
Illustrative: true
Root formula: surrender_benefit = max(surrender_value, mgsv_close)
Child nodes: 2
```

This matches the documented anchor benchmark of approximately `$51,979.60`.

The frontend renders the recursive trace structure, formula metadata, values, source/kind information, and child nodes when returned by the backend. It also shows an explicit unavailable state when a run was not executed with trace capture.

---

## 8. Frontend fixes made during live testing

### Fix 1: MYGA result tiles incorrectly displayed `$NaN`

**File:** `src/pages/results-explorer/ResultsExplorer.jsx`

**Cause:** The backend summary contract returns totals like:

```json
{
  "liability_outgo": {
    "label": "Total Liability Outgo",
    "value": 5298179.05844914,
    "unit": "USD"
  }
}
```

The frontend originally passed the entire object into the currency formatter. JavaScript converted the object to `NaN`, producing visible `$NaN` values in the MYGA metric tiles.

**Fix:** The frontend now unwraps `total.value` when a total is an object, while preserving compatibility with primitive numeric values. The reserve tile also falls back to `summary.headline.value` when a reserve total is not separately returned.

**Result:** The live UI now displays numeric MYGA totals correctly.

### Fix 2: Trace investigation opened on an unavailable month/variable combination

**File:** `src/pages/reserve-investigation/ReserveInvestigation.jsx`

**Cause:** The live trace selector defaulted to month `0` while its first output variable was `account_value_close`. The captured trace has a valuation-month row for some variables, but `account_value_close` is available for the selected policy beginning at month `1`. Opening the default selection therefore produced an avoidable `No trace` response.

**Fix:** The live investigation selector now defaults to month `1` and resets to month `1` when the selected run changes.

**Result:** The default trace target is aligned with the first captured projection month. The documented month-30 surrender-benefit trace remains available and was verified directly against the backend.

### Existing contract fix: dataset approval payload

**File:** `src/pages/data-manager/DataManager.tsx`

The dataset approval request was aligned with the backend review contract. The frontend now sends the required review payload and surfaces backend segregation-of-duties responses.

---


## 9. Final status

The requested M2B MYGA frontend integration is live-tested against the running backend. The governed data path, projection configuration, execution lifecycle, results, CSV export, and trace retrieval are connected and verified. The frontend fixes listed above were made from observed live behavior, not only static code review.
