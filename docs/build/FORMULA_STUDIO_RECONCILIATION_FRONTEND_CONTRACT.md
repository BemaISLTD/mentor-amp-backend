# Formula Studio and Reconciliation Frontend Contract

This is the frontend integration contract for the M3 Formula Studio, expected-result,
reconciliation, and actuarial-table viewer APIs. All routes use the `/v1` prefix and the
standard error envelope `{ "error": { "code": string, "message": string, "details"?: object } }`.

## Permissions

| Capability | Required permission |
| --- | --- |
| Validate/preview/test formulas | `registries:read` |
| Create or edit formulas and model versions | `registries:write` plus project editor/owner |
| Approve or roll back a model version | `models:approve` plus project access |
| Upload expected results | `imports:write` or `registries:write`, plus project editor/owner |
| Read expected results and reconciliations | `projects:read` plus project membership |
| Record reconciliation disposition | `models:approve` (or administrator) |

IDs are opaque strings. Dates use `YYYY-MM-DD`; timestamps are ISO-8601 UTC strings.

## Formula Studio schemas

```ts
interface FormulaTestCase {
  name: string;
  inputs: Record<string, unknown>;
  prior_inputs?: Record<string, unknown>;
  expected_output: number;
  abs_tol?: number;
}

interface FormulaDefinition {
  id: string;
  name: string;
  output_variable: string;
  function_ref: string; // "dsl" or a registered server plug-in
  dependencies: string[];
  category: string;
  product_applicability: string[];
  basis_applicability: string[];
  version: string;
  status: "draft" | "active" | "deprecated" | "needs_review" | "validated" | "approved";
  model_version_id: string | null;
  expression_text: string | null;
  explanation: string | null;
  unit: string | null;
  group_id: string | null;
  illustrative: boolean;
  effective_date: string | null;
  owner_user_id: string | null;
  test_cases: FormulaTestCase[];
}
```

### Validate and preview

`POST /v1/formulas/validate-expression`

```ts
// request
{ expression_text: string; model_version_id?: string; output_variable?: string }

// 200 response
{
  valid: boolean;
  expression_text: string;
  dependencies: string[];
  prior_dependencies: string[];
  functions_used: string[];
  unknown_variables: string[];
  errors: string[];
}
```

`POST /v1/formulas/preview`

```ts
// request
{ expression_text: string; inputs: Record<string, unknown>; prior_inputs?: Record<string, unknown> }

// 200 response
{
  valid: true;
  result: number | boolean;
  dependencies: string[];
  inputs_used: Record<string, unknown>;
  errors: [];
}
```

Unsafe or invalid expressions return `422`; the server never executes raw Python. Supported
DSL functions are `min`, `max`, `abs`, `round`, `exp`, `log`, `sqrt`, `pow`, `clamp`,
`if_else`, `coalesce`, and `prior`.

### Create, edit, and test

`POST /v1/model-versions/{modelVersionId}/formulas` creates a formula only in a mutable model
version and returns `FormulaDefinition` with status `201`. Omit `dependencies` to derive them
from the DSL expression; omit `group_id` to use/create the default group.

```ts
interface CreateFormulaRequest {
  name: string;
  output_variable: string;
  function_ref?: string;
  dependencies?: string[];
  expression_text?: string;
  explanation?: string;
  unit?: string;
  group_id?: string;
  illustrative?: boolean;
  effective_date?: string;
  owner_user_id?: string;
  test_cases?: FormulaTestCase[];
  category?: string;
  product_applicability?: string[];
  basis_applicability?: string[];
}
```

Use `PUT /v1/formulas/{formulaId}` for partial formula updates. Approved, published,
superseded, or active published versions return `409 MODEL_VERSION_IMMUTABLE`.

`POST /v1/formulas/{formulaId}/run-tests` returns:

```ts
interface FormulaTestRun {
  formula_id: string;
  all_passed: boolean;
  total: number;
  passed: number;
  failed: number;
  results: Array<{
    name: string;
    expected_output: number;
    actual_output: number | null;
    abs_diff: number | null;
    abs_tol: number;
    passed: boolean;
    error: string | null;
  }>;
}
```

### Dependencies, comparison, governance, and rollback

- `GET /v1/model-versions/{id}/dependency-analysis?variable={name}` returns
  `{ model_version_id, variable, execution_order, upstream_dependencies, downstream_impacted }`.
- `GET /v1/model-versions/{id}/compare/{otherId}` returns `metadata_changes` and
  `formula_changes`, `variable_changes`, and `published_output_changes`, each split into
  `added`, `removed`, and `modified` collections where applicable.
- Existing lifecycle commands remain canonical:
  `POST /v1/model-versions/{id}/validate`, `/approve`, and `/publish`.
- `POST /v1/model-versions/{id}/rollback` makes a previously approved/published version
  current, supersedes the replaced current version, and returns the activated model version.

## Expected results

`POST /v1/projects/{projectId}/expected-results` accepts either JSON or multipart CSV.

```ts
interface ExpectedResultUpload {
  name: string;
  product_code: string;
  version_label?: string;
  source_reference?: string;
  timing_convention?: string;
  granularity?: "policy_month" | "aggregate_year";
  rows: Array<Record<string, string | number | boolean | null>>;
}

interface ExpectedResultSet {
  id: string;
  project_id: string;
  name: string;
  product_code: string;
  version_label: string;
  source_reference: string;
  timing_convention: string | null;
  granularity: "policy_month" | "aggregate_year";
  row_count: number;
  columns_detected: string[];
  fingerprint: string;
  status: "validated";
  created_by: string | null;
  created_at: string;
  updated_at: string;
  rows?: Array<Record<string, unknown>>;
}
```

For multipart upload, send fields `file`, `name`, `product_code`, and optionally
`version_label`, `timing_convention`, and `granularity`. Use:

- `GET /v1/projects/{projectId}/expected-results` to list metadata.
- `GET /v1/expected-results/{id}?include_rows=true` for detail and normalized rows.

The backend normalizes common reference aliases, computes a deterministic SHA-256 fingerprint,
and derives `projection_year` from `projection_month` when needed.

## Reconciliation

`POST /v1/runs/{runId}/reconcile`

```ts
interface ReconcileRequest {
  expected_result_set_id: string;
  policy_id?: string;
  level?: "policy_month" | "aggregate_year" | "both";
  abs_tol?: number; // default 0.01
  rel_tol?: number; // default 0.0001
  variables?: string[];
}

interface ReconciliationReport {
  id: string;
  project_id: string;
  run_id: string;
  expected_result_set_id: string | null;
  policy_id_filter: string | null;
  abs_tol: number;
  rel_tol: number;
  status: "PASS" | "FAIL";
  disposition_status: "pending" | "approved" | "exception_noted" | "rejected";
  disposition_notes: string | null;
  disposition_by: string | null;
  disposition_at: string | null;
  summary: {
    total_variables_checked: number;
    total_cells_checked: number;
    cells_passed: number;
    cells_failed: number;
    max_abs_diff: number;
    max_rel_diff: number;
    per_variable_summary: Record<string, {
      cells_checked: number; cells_passed: number; cells_failed: number;
      max_abs_diff: number; max_rel_diff: number;
    }>;
    source_to_output_check: Record<string, unknown>;
  };
  failures: Array<{
    level: "policy_month" | "aggregate_year";
    variable: string;
    expected: number;
    actual: number | null;
    abs_diff: number | null;
    rel_diff: number | null;
    tolerance_used: number;
    policy_id?: string;
    projection_month?: number;
    projection_year?: number;
  }>;
  created_by: string | null;
  created_at: string;
}
```

Tolerance passes when `abs(actual - expected) <= max(abs_tol, rel_tol * abs(expected))`.
Use `GET /v1/runs/{runId}/reconciliations` and `GET /v1/reconciliations/{id}` to reload
reports. A reviewer records sign-off with:

`POST /v1/reconciliations/{id}/disposition`

```json
{ "disposition_status": "approved", "disposition_notes": "Reviewed against source." }
```

## Assumption and factor record viewers

- `GET /v1/inputs/assumptions/{tableId}/records?limit=100&offset=0&search=`
- `GET /v1/inputs/factors/{tableId}/records?limit=100&offset=0&search=`

Both return `{ table_id, total, limit, offset, columns, records }`. `limit` is
1–200. Search is a case-insensitive text match across row values.

## Seeded demo availability

Running `python backend/scripts/seed_demo.py` idempotently prepares SPIA and the synthetic
25-policy 5-Year MYGA demo. The MYGA project contains a published model, approved mortality,
surrender factors, Base and Rates Up (+150 bps) scenarios, and a validated projection set.
The separate `MYGA-10001` package is conditionally seeded when both supplied source files are
present in `samples/myga/`: the reference in-force becomes the 60-month demo Projection Set input,
and the expected-result CSV is registered idempotently. The backend does not generate or
substitute actuarial reference results when those files are absent.
