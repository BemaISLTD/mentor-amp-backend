# WP2 frontend API contract

This is the authoritative frontend contract for WP2 Data Manager and run
cancel/retry. It describes the tested public /v1 API. Deferred endpoints under
/internal/experimental are not a frontend contract.

## Common rules

- Send Authorization: Bearer TOKEN.
- IDs are opaque strings; timestamps are ISO 8601 UTC strings.
- Errors have the shape {error: {code, message, details?}}.
- Hidden or inaccessible project-owned objects return 404.
- Writes to archived projects return 409 PROJECT_ARCHIVED.
- Data Manager readers need imports:read; writers need imports:write and
  owner/editor membership; reviewers need imports:approve.
- Cancel/retry needs runs:execute and owner/editor membership.

## Data Manager schemas

    type ImportCategory =
      "liability_inforce" | "assumption_table" | "factor_table" | "scenario";
    type DatasetKind = "inforce" | "assumption" | "factor" | "scenario";
    type ImportStatus =
      "open" | "uploaded" | "mapped" | "validated" | "needs_review"
      | "invalid" | "committed";

    interface MappingField {
      source_column: string;
      target_field: string;
      transform?: "strip" | "upper" | "lower" | "integer" | "number" | "date_iso";
    }
    interface DatasetRef { kind: DatasetKind; id: string }
    interface ImportSession {
      id: string;
      project_id: string;
      category: ImportCategory;
      name: string;
      version_label: string | null;
      replaces_dataset_id: string | null;
      status: ImportStatus;
      options: Record<string, unknown>;
      original_filename: string | null;
      file_format: "csv" | "tsv" | "xlsx" | "parquet" | null;
      raw_fingerprint: string | null;
      raw_size_bytes: number | null;
      row_count: number;
      columns: string[];
      detected_types: Record<string, string>;
      mapping_fields: MappingField[];
      mapping_profile_id: string | null;
      created_by: string;
      updated_by: string;
      committed_by: string | null;
      committed_at: string | null;
      dataset_kind: DatasetKind | null;
      dataset_id: string | null;
      datasets: DatasetRef[];
      dataset_ids: string[];
      created_at: string;
      updated_at: string;
    }
    interface ValidationRun {
      id: string;
      import_session_id: string;
      status: "passed" | "invalid";
      error_count: number;
      warning_count: number;
      accepted_count: number;
      rejected_count: number;
      canonical_fingerprint: string | null;
      fingerprint_scheme: string | null;
      created_by: string;
      created_at: string;
      completed_at: string;
    }
    interface ValidationIssue {
      id: number;
      severity: "error" | "warning";
      code: string;
      message: string;
      row_number: number | null;
      column_name: string | null;
      value: unknown;
    }

## Required lifecycle

The UI flow is:

    open -> upload -> preview -> map -> validate -> commit -> approve/reject

### Open

**POST /v1/import-sessions** returns 201 ImportSession.

Example body:

    {
      "project_id": "project-id",
      "category": "liability_inforce",
      "name": "September inforce",
      "version_label": "2026.09",
      "replaces_dataset_id": null,
      "options": {
        "valuation_date": "2026-09-30",
        "expected_record_count": 25000
      }
    }

For assumption/factor tables, options may include table_type, lookup_keys,
value_column and model_version_id. Scenario imports require model_version_id;
structural_only: true explicitly requests non-execution-ready validation.

### Upload and preview

- **POST /v1/import-sessions/{id}/files** uses multipart/form-data field file.
  It returns the updated session, file_id, and up to 20 preview rows.
- **GET /v1/import-sessions/{id}/preview?rows=20** returns
  {import_session_id, columns, detected_types, row_count, sample_rows,
  raw_fingerprint}.
- **GET /v1/import-sessions/{id}/file** downloads the retained raw file.

Accepted extensions are .csv, .tsv, .xlsx and .parquet. .xls is rejected.
Malformed files return 400 MALFORMED_IMPORT_FILE. Limits return
413 IMPORT_FILE_TOO_LARGE or 413 IMPORT_LIMIT_EXCEEDED.

### Map and reuse profiles

**PUT /v1/import-sessions/{id}/mapping**

Use either {profile_id: "..."} or {fields: MappingField[]}.

- **GET /v1/mapping-profiles?project_id={id}&category={category}**
- **POST /v1/mapping-profiles** with {project_id, category, name, fields}

Profiles are versioned. Saving the same project/category/name creates the next
profile version.

### Validate and inspect evidence

**POST /v1/import-sessions/{id}/validate** returns 201 ValidationRun.
Warnings produce needs_review; errors produce invalid and block commit.

- **GET /v1/validation-runs/{id}/issues** returns
  {validation: ValidationRun, issues: ValidationIssue[]}.
- **GET /v1/validation-runs/{id}/rejected-records?limit=50&offset=0** returns
  {rejected_records, total, limit, offset}. Each record contains id, row_number,
  raw_record, canonical_record and reasons.

Model-aware issue codes include MODEL_CONTEXT_REQUIRED,
MODEL_VERSION_NOT_FOUND, MODEL_TABLE_IDENTITY_MISMATCH,
MODEL_LOOKUP_KEYS_MISMATCH, MODEL_VALUE_COLUMN_MISMATCH,
MODEL_VALUE_TYPE_CONFLICT and MODEL_UNIT_MISMATCH.

### Commit

**POST /v1/import-sessions/{id}/commit** returns 201.

A scenario file can create multiple datasets. The authoritative output is the
session's datasets and dataset_ids arrays. dataset_id is only a compatibility
alias for the first output.

Replacement imports name replaces_dataset_id. If another replacement wins
first, the loser receives 409 STALE_DATASET_VERSION. Committed content is
immutable; semantic changes require a new version.

### Independent review

- **POST /v1/datasets/{kind}/{id}/approve**
- **POST /v1/datasets/{kind}/{id}/reject**

Body: {reason: "Independent review completed"}.

The committer cannot review their own dataset. Self-review returns
409 SELF_APPROVAL_FORBIDDEN.

### History and comparison

- **GET /v1/projects/{project_id}/import-sessions?category={category}**
- **GET /v1/import-sessions/{id}**
- **GET /v1/import-sessions/{id}/log**
- **GET /v1/datasets/{kind}/{id}/versions**
- **GET /v1/datasets/{kind}/{id}/compare?other_id={id}&limit=50**

Comparison is bounded to at most 200 records.

## Run cancel and retry

Run responses include:

    interface RunControlFields {
      status: "pending" | "running" | "success" | "partial_success"
        | "failed" | "cancelled";
      cancel_requested_at: string | null;
      cancel_requested_by: {id: string; full_name: string | null} | null;
      retried_from_run_id: string | null;
      can_cancel: boolean;
      can_retry: boolean;
      attempt_count: number;
      accepted_attempt_number: number | null;
    }

### Cancel

**POST /v1/runs/{id}/cancel** has no body and returns 202 with the run.

- Pending runs become cancelled immediately.
- Running runs record a request and stop between policies.
- Terminal runs return 409 RUN_NOT_CANCELLABLE.
- Cancelled/failed/partial evidence is not exposed as canonical results.

### Retry

**POST /v1/runs/{id}/retry** has no body and returns 202 with a pending child.

- Allowed for failed, cancelled and partial_success.
- The exact frozen package is reused; live model/data rows are not rebuilt.
- retried_from_run_id identifies the source.
- Repeated or concurrent calls for one source return the same successor.
- Success returns 409 RUN_NOT_RETRYABLE.
- Missing or changed evidence returns a stable package/build integrity error.

Render actions from can_cancel and can_retry, but still handle 409 because the
state can change between read and click.

## Deprecated wrappers

/v1/imports/inforce, /v1/imports/assumptions,
/v1/imports/factors and /v1/imports/scenarios remain temporary deprecated
wrappers. New frontend code must use /v1/import-sessions.

Legacy run result and trace routers are removed. Use the canonical run routes
shown in /openapi.json.
