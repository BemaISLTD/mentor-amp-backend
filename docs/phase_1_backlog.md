# Phase 1: Actionable Backlog

This document is the consolidated Phase 1 backlog produced from the Phase 0
task document, the API/database/persistence assessments in `test_data/`, and a
verification of the live backend source on 2026-09-28.

The goal is to close the gap between the current backend and the Launch Release
1.0 requirements. Each item must be re-verified with executable evidence before
it is considered complete.

## Verified Baseline

| Area | Verified state | Status |
| --- | --- | --- |
| Tests | Original `test_*.py` modules were empty placeholders | **Current evidence:** 259 tests pass and 12 are skipped when opt-in PostgreSQL tests have no database URL; all 11 PostgreSQL migration/concurrency tests pass separately |
| Seed data | No seed script existed | Complete for illustrative SPIA demo; broader product and workflow seed remains open |
| Authentication/RBAC | No auth dependency or user/RBAC models existed | **Complete:** JWT and the administrator, actuary, model developer, reviewer, and read-only permission matrix are enforced |
| API versioning | Public routers were mounted at root | **Complete:** routers now use `/v1` |
| Error contract | Default FastAPI `detail` responses were used | **Complete:** standardized error envelope added |
| Projects | POST and GET only | **Complete:** permission-protected PATCH and audited archival added; hard deletion is not exposed |
| Run APIs | `runs.py`, `results.py`, and `trace.py` were empty | **Complete for local execution:** consolidated run-set, result, comparison, and trace APIs are canonical at `/v1`; older backlog run routes are deprecated under `/v1/legacy-runs` and administrator-only |
| Data Manager | Imports were disconnected one-shot validators | **Complete:** governed upload, browser preview, mapping, validation, immutable version commit, peer approval, comparison, rejected records, and import history cover inforce, assumptions, factors, and scenarios |
| Run controls | Runs could not be cancelled or retried | **Complete:** pending/running cancellation and frozen-configuration retry are exposed through the canonical run API |
| Large outputs | Runner previously wrote outputs and traces to PostgreSQL | **Complete for local execution:** Parquet artifacts replace the removed legacy tables; production object storage remains open |
| Run manifests | Runs did not snapshot inputs/configuration | **Complete:** immutable version snapshot added |
| Reconciliation | No comparison service or API existed | **Complete:** deterministic on-demand comparison over immutable artifacts |

## 1. Environment, Recovery, and Testing

- **[BUILD] Implement the Pytest Suite — COMPLETE FOR CURRENT LOCAL SCOPE**
  - Build coverage for database connectivity, migrations, model constraints,
    validation, authentication/authorization, API behavior, engine behavior,
    and golden policies.
  - Current evidence: the clean Docker suite returned `259 passed, 12 skipped`
    when the opt-in PostgreSQL URL was absent; all 11 PostgreSQL
    migration/concurrency tests passed separately on a disposable PostgreSQL 15
    database.
- **[IMPROVE] Database Seeding Scripts — IN PROGRESS**
  - `scripts/seed_demo.py` idempotently creates synthetic SPIA users, project,
    inputs, scenarios, model, and a validated projection set. Broader product
    and workflow seed data remains open.
- **[IMPROVE] Governed Upload Recovery — COMPLETE FOR LOCAL ARTIFACT STORAGE**
  - Every upload is retained as an immutable raw artifact with its SHA-256
    fingerprint and persistent import-session history.
  - CSV, TSV, XLSX, and Parquet files share one preview, mapping, validation,
    rejected-record, commit, version, and approval lifecycle.
  - Canonical fingerprints are independent of upload format. Production object
    storage remains deferred behind the existing artifact-store interface.

## 2. API Contract and Security

- **[BUILD] Global Authentication and Authorization (JWT) — COMPLETE**
  - Build users/RBAC persistence, token issuance, `/v1/auth/me`, and `/v1/users`.
  - Project endpoints enforce stored `projects:read` and `projects:write`
    permissions plus project membership; product, asset, dashboard, and in-force
    record APIs now enforce the same project boundary.
  - Administrator, actuary, model developer, reviewer, and read-only roles use
    an explicit project, registry, import, and run permission matrix.
  - Dataset approval uses the separate `imports:approve` permission for admin,
    actuary, and reviewer roles; model developers cannot approve and committers
    cannot approve their own dataset versions.
  - Read and mutation routes enforce stored permissions rather than role names.
- **[IMPROVE] API Versioning and Error Handling — COMPLETE**
  - Public application routers are mounted below `/v1`.
  - Errors use `{"error": {"code": "...", "message": "..."}}`, with optional
    structured details.
- **[IMPROVE] Project Mutation Endpoints — COMPLETE**
  - Added permission-protected `PATCH /v1/projects/{project_id}` and
    `POST /v1/projects/{project_id}/archive`.
  - Archival records the actor, timestamp, reason, and before/after audit state.
    Archived projects and their child records are retained indefinitely as
    read-only governed records unless a separate authorized purge policy is
    designed. Permanent project deletion is not exposed.
  - Normal project reads and dashboards hide archived projects; authorized
    project reads can opt in with `include_archived=true`.
- **[BUILD] File Record Viewers — COMPLETE FOR IN-FORCE FILES**
  - `GET /v1/imports/inforce/{file_id}/records` returns stable, paginated rows
    with file metadata and a maximum page size of 200.
  - Filtering is restricted to `policy_id` and columns declared by the file,
    using allowlisted text and numeric operators.
- **[BUILD] Dashboard Contract — COMPLETE**
  - `GET /v1/dashboard/stats` returns project-scoped product, latest or
    requested asset snapshot, run, and in-force file statistics.
- **[CONDITIONAL] `/imports` to `/files` Adapter — DEFERRED**
  - Implement only after the frontend architecture is finalized.
- **[CONDITIONAL] `/formulas` to `/tables` Adapter — DEFERRED**
  - Implement only after the frontend architecture is finalized.

## 3. Database Schema and Governance

- **[BUILD] User and RBAC Schemas — COMPLETE**
  - Build `users`, `roles`, `permissions`, `user_roles`, and
    `role_permissions` with uniqueness and foreign-key constraints.
- **[BUILD] Audit Logs — COMPLETE**
  - Track actor, action, entity, before/after values, and timestamp for metadata
    mutations.
- **[IMPROVE] Actor Audit Fields — COMPLETE**
  - Projects, products, asset positions, variables, formulas, model versions,
    model-variable definitions, Projection Sets, rollforward metadata, reports,
    and derived datasets record mutation actors and transactional before/after
    audit history through their mutation APIs.
  - Formula `created_by` predates the actor migration; existing rows remain
    nullable/unchanged.
- **[IMPROVE] Soft Deletes — COMPLETE FOR GOVERNED DELETE APIS**
  - Project archival and its retention contract are complete. Archived projects
    reject project, import, catalog, asset, mapping, and run mutations while
    retaining historical data.
  - Variable, formula, model-version, rollforward-template, report, and
    derived-dataset delete APIs retain governed rows with deletion actor and
    timestamp. Normal reads and authorization resolvers exclude archived rows.
- **[BUILD] Products and Assets — COMPLETE**
  - Build `products`, `asset_positions`, and `product_mappings`.
- **[BUILD] Actuarial Workflows — COMPLETE**
  - Versioned, publishable rollforward templates persist ordered steps; jobs copy
    the published definition and expose governed lifecycle transitions.
- **[BUILD] Reporting Metadata — COMPLETE**
  - Versioned report definitions and derived-dataset lineage/schema/artifact
    metadata are persisted. Large bodies remain in analytical storage by URI.
- **[BUILD] Execution Metadata — COMPLETE**
  - Runs persist verification, calculation, and finalization steps with status,
    progress, metrics, and failure details; the run-step API exposes them.
  - Projection output keys remain in Parquet because normalized projection-value
    rows would violate the established analytical-storage boundary.
- **[BUILD] Governed Data Manager — COMPLETE**
  - Persistent import sessions implement upload → preview → map → validate →
    commit immutable version → peer approve/reject for liability inforce,
    assumption tables, factor tables, and scenarios.
  - Mapping profiles and dataset versions are immutable chains. Validation issues,
    rejected rows, actors, raw/canonical fingerprints, and import events remain
    queryable. Legacy `/v1/imports/*` writes are deprecated one-shot wrappers over
    the same lifecycle.
- **[BUILD] Model Versioning — COMPLETE**
  - Whole-model versions track parent, sequence, configuration, change summary,
    actor, publication, and archival metadata; child formula groups, formulas,
    dependencies, variable definitions, and published outputs clone from a parent.
  - Published model versions are immutable and use the existing runnable
    `approved` lifecycle state.

## 4. Actuarial Execution and Large Data

> [!CAUTION]
> This is the primary architectural risk. The analytical storage boundary must
> be completed before production run/result/trace APIs are built.

- **[REPLACE] PostgreSQL `run_outputs` and `trace_logs` — COMPLETE FOR LOCAL EXECUTION**
  - Compressed, partitioned Parquet stores outputs and traces locally; PostgreSQL
    retains artifact metadata, locations, fingerprints, and summaries only.
  - The legacy high-volume tables and ORM models are removed. The removal
    migration refuses to discard non-empty legacy tables. A disposable development
    database can be reset; export is necessary only when its old results must be
    preserved. No exporter is in the current development scope.
  - M1 execution writes attempt-scoped Parquet artifacts; analytical reads expose
    only an accepted attempt. Failed-attempt artifacts remain noncanonical
    evidence and are not exposed through result APIs.
  - Add an S3-compatible implementation of the artifact-store interface before
    production deployment; engine code must remain storage-backend independent.
- **[BUILD] Immutable Run Manifests — COMPLETE**
  - Snapshot data-file, assumption, factor, scenario, formula/model, code, and
    storage versions when a run is queued.
- **[BUILD] Run, Result, and Trace APIs — COMPLETE FOR LOCAL EXECUTION**
  - Build execution, status polling, summary, cashflow, event, and trace
    endpoints after the target persistence boundary exists.
  - Replace the in-process background-task adapter with a durable worker queue
    before multi-instance production deployment.
- **[BUILD] Run Cancel and Retry — COMPLETE FOR LOCAL EXECUTION**
  - Pending cancellation is immediate; running cancellation is cooperative
    between policies and produces terminal manifest evidence while retaining
    partial artifacts as noncanonical evidence.
  - Failed, cancelled, and partial-success runs can create a new linked run from
    the exact frozen configuration. Build-identity mismatches and successful-run
    retries are refused.
- **[BUILD] Reconciliation Service — COMPLETE**
  - Compare prior/current output datasets and return reserve bridges and exact
    variance components.

## Implementation Order

1. API foundation: versioning, error contract, project mutations, and initial
   endpoint tests. **Complete:** audited project archival enforces read-only
   retention without exposing hard deletion.
2. Users/RBAC schema, authentication endpoints, JWT configuration, and router
   authorization tests. **Complete:** expanded roles and route-level read,
   write, and execute permissions are enforced.
3. Audit logs and project actor fields. **Complete.**
4. Local/S3 Parquet storage interface and immutable run manifests. **Complete for
   local execution:** local Parquet, manifests, and legacy table removal are
   complete; S3 remains deferred.
5. Run, status, result, and trace APIs. **Complete for local execution.**
   Cancel/retry and reconciliation are complete; durable workers remain open.
6. Product, asset, and dashboard domains. **Complete.** Workflow, reporting,
   and record-viewer domains remain open.
7. Conditional `/files` and `/tables` adapters after frontend confirmation.

## Completion Requirements for Every Item

Before an item moves to complete, record:

- owner and dependencies;
- acceptance criteria tied to a Launch 1.0 requirement;
- migration/API compatibility impact;
- automated test or reproducible verification command;
- implementation and evidence paths;
- follow-up risks or explicitly deferred behavior.

## Current Evidence

- Versioned routes: `backend/app/main.py`
- Standard error handlers: `backend/app/api/errors.py`
- Permission-protected project PATCH and archival: `backend/app/api/projects.py`
- Project lifecycle enforcement: `backend/app/core/project_lifecycle.py`
- Project archival migration: `backend/app/db/migrations/versions/e2b5d8f0c316_add_project_archival.py`
- Contract tests: `backend/tests/integration/test_api_contract.py`
- Auth/RBAC migration: `backend/app/db/migrations/versions/2f6d51e920a4_add_users_and_rbac.py`
- Expanded RBAC migration: `backend/app/db/migrations/versions/d1a4c7e9b205_expand_builtin_rbac_roles.py`
- Authentication tests: `backend/tests/integration/test_auth_api.py`
- Upload recovery implementation: `backend/app/api/imports.py`
- Upload recovery tests: `backend/tests/unit/test_importers.py`
- In-force record viewer tests: `backend/tests/integration/test_inforce_record_viewer.py`
- Audit migration: `backend/app/db/migrations/versions/7c3f19ad0e82_add_audit_logging.py`
- Audit API: `backend/app/api/audit_logs.py`
- Artifact metadata migration: `backend/app/db/migrations/versions/91b4e26d7fa0_add_run_manifests_and_artifacts.py`
- Legacy run-storage removal: `backend/app/db/migrations/versions/a7f4c2d9e180_remove_legacy_run_storage.py`
- Parquet storage tests: `backend/tests/unit/test_artifact_storage.py`
- M1 execution integration: `backend/app/services/run_execution_service.py`
- Data Manager API and service: `backend/app/api/data_manager.py`,
  `backend/app/services/data_manager_service.py`
- Data Manager migration: `backend/app/db/migrations/versions/f3b8d6a1e240_data_manager_lifecycle.py`
- Data Manager tests: `backend/tests/integration/test_data_manager.py`,
  `backend/tests/integration/test_data_manager_rebuild.py`
- Run controls: `backend/app/services/run_control_service.py`,
  `backend/app/api/execution.py`
- Run-control migration: `backend/app/db/migrations/versions/e6a9c4d2f817_run_cancel_retry.py`
- Run-control tests: `backend/tests/integration/test_run_control.py`
- Attempt-scoped artifact migration: `backend/app/db/migrations/versions/f9d3e5a7b012_attempt_scoped_artifacts.py`
- Registry actor migration: `backend/app/db/migrations/versions/c6e1a4b9d203_registry_actor_fields.py`
- M1 end-to-end tests: `backend/tests/integration/test_m1_end_to_end.py`
- PostgreSQL migration/concurrency tests: `backend/tests/postgres/test_wp1_migrations_postgres.py`
- Products/assets migration: `backend/app/db/migrations/versions/c48a2d7159be_add_products_and_assets.py`
- Products/assets tests: `backend/tests/integration/test_products_api.py`
- Dashboard API: `backend/app/api/dashboard.py`
- Dashboard tests: `backend/tests/integration/test_dashboard_api.py`
- Verification command: `pytest tests/unit tests/integration -q`; PostgreSQL
  verification uses `MENTORAMP_TEST_POSTGRES_URL` pointing only to a disposable
  database whose name contains `test`.
