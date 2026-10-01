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
| Tests | Original `test_*.py` modules were empty placeholders | Test suite remains open; 46 automated tests now cover implemented domains |
| Seed data | No seed script exists | Open |
| Authentication/RBAC | No auth dependency or user/RBAC models existed | **Complete:** JWT and the administrator, actuary, model developer, reviewer, and read-only permission matrix are enforced |
| API versioning | Public routers were mounted at root | **Complete:** routers now use `/v1` |
| Error contract | Default FastAPI `detail` responses were used | **Complete:** standardized error envelope added |
| Projects | POST and GET only | **Complete:** permission-protected PATCH and audited archival added; hard deletion is not exposed |
| Run APIs | `runs.py`, `results.py`, and `trace.py` were empty | **Complete for local execution:** queue, status, manifest, result, summary, and trace APIs added |
| Large outputs | Runner previously wrote outputs and traces to PostgreSQL | **In progress:** runner now writes Parquet; legacy tables remain |
| Run manifests | Runs did not snapshot inputs/configuration | **Complete:** immutable version snapshot added |
| Reconciliation | No comparison service or API existed | **Complete:** deterministic on-demand comparison over immutable artifacts |

## 1. Environment, Recovery, and Testing

- **[BUILD] Implement the Pytest Suite — IN PROGRESS**
  - Build coverage for database connectivity, migrations, model constraints,
    validation, authentication/authorization, API behavior, engine behavior,
    and golden policies.
  - Current evidence: API contract, authentication/RBAC, upload recovery, and
    record viewer suites; `docker compose exec -T api pytest -q` returned
    `46 passed`.
- **[IMPROVE] Database Seeding Scripts — OPEN**
  - Add an idempotent `seed.py` using synthetic users, roles, permissions,
    projects, products, and representative actuarial metadata.
- **[IMPROVE] Upload Recovery — COMPLETE FOR LOCAL UPLOADS**
  - Uploads use atomic staging, sanitize client filenames, and remove staged or
    partially copied files on success and failure.
  - In-force database writes commit only after staging cleanup and roll back on
    parsing, validation, storage, cleanup, or commit failures.
  - Reapply the same lifecycle when an object-storage-backed import path is added.

## 2. API Contract and Security

- **[BUILD] Global Authentication and Authorization (JWT) — COMPLETE**
  - Build users/RBAC persistence, token issuance, `/v1/auth/me`, and `/v1/users`.
  - Project endpoints enforce stored `projects:read` and `projects:write`
    permissions; uploads and registries remain restricted to admin/actuary roles.
  - Administrator, actuary, model developer, reviewer, and read-only roles use
    an explicit project, registry, import, and run permission matrix.
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
- **[IMPROVE] Actor Audit Fields — IN PROGRESS**
  - Projects, products, and asset positions now record actors. Extend the same contract
    to the remaining auditable actuarial metadata as their mutation APIs are
    implemented.
- **[IMPROVE] Soft Deletes — IN PROGRESS**
  - Project archival and its retention contract are complete. Archived projects
    reject project, import, catalog, asset, mapping, and run mutations while
    retaining historical data.
  - Extend soft deletion to remaining auditable actuarial metadata and avoid
    accidental hard deletion of governed records.
- **[BUILD] Products and Assets — COMPLETE**
  - Build `products`, `asset_positions`, and `product_mappings`.
- **[BUILD] Actuarial Workflows — OPEN**
  - Build `rollforward_templates`, `rollforward_jobs`, and `rollforward_steps`.
- **[BUILD] Reporting Metadata — OPEN**
  - Build metadata persistence for reports and derived datasets; large report
    bodies belong in analytical storage.
- **[BUILD] Execution Metadata — OPEN**
  - Add `run_steps`; decide whether projection keys require a normalized model.
- **[BUILD] Model Versioning — OPEN**
  - Add model-level version tracking beyond individual formula versions.

## 4. Actuarial Execution and Large Data

> [!CAUTION]
> This is the primary architectural risk. The analytical storage boundary must
> be completed before production run/result/trace APIs are built.

- **[REPLACE] PostgreSQL `run_outputs` and `trace_logs` — IN PROGRESS**
  - Write compressed, partitioned Parquet to a local data lake or S3-compatible
    object storage. Keep only metadata, locations, fingerprints, and summaries
    in PostgreSQL.
  - Define an interface supporting local development and production object
    storage without changing engine code.
- **[BUILD] Immutable Run Manifests — COMPLETE**
  - Snapshot data-file, assumption, factor, scenario, formula/model, code, and
    storage versions when a run is queued.
- **[BUILD] Run, Result, and Trace APIs — COMPLETE FOR LOCAL EXECUTION**
  - Build execution, status polling, summary, cashflow, event, and trace
    endpoints after the target persistence boundary exists.
  - Replace the in-process background-task adapter with a durable worker queue
    before multi-instance production deployment.
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
4. Local/S3 Parquet storage interface and immutable run manifests. **In progress:**
   local Parquet and manifests are complete; S3 and legacy table removal remain.
5. Run, status, result, and trace APIs. **Complete for local execution.**
   Durable workers remain open; reconciliation is complete.
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
- Parquet storage tests: `backend/tests/unit/test_artifact_storage.py`
- Products/assets migration: `backend/app/db/migrations/versions/c48a2d7159be_add_products_and_assets.py`
- Products/assets tests: `backend/tests/integration/test_products_api.py`
- Dashboard API: `backend/app/api/dashboard.py`
- Dashboard tests: `backend/tests/integration/test_dashboard_api.py`
- Verification command: `docker compose exec -T api pytest -q`
