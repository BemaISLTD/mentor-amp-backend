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
| Tests | Original `test_*.py` modules are empty placeholders | Test suite remains open; 12 API tests added |
| Seed data | No seed script exists | Open |
| Authentication/RBAC | No auth dependency or user/RBAC models existed | **In progress:** JWT, admin/actuary roles, and permission enforcement implemented; expanded role matrix pending |
| API versioning | Public routers were mounted at root | **Complete:** routers now use `/v1` |
| Error contract | Default FastAPI `detail` responses were used | **Complete:** standardized error envelope added |
| Projects | POST and GET only | **In progress:** PATCH added; destructive DELETE withheld pending an archive and retention contract |
| Run APIs | `runs.py`, `results.py`, and `trace.py` are empty | Open |
| Large outputs | `run_outputs` and `trace_logs` use PostgreSQL | Open; replace before production run APIs |
| Run manifests | Runs do not snapshot inputs/configuration | Open |
| Reconciliation | No service, persistence, or API exists | Open |

## 1. Environment, Recovery, and Testing

- **[BUILD] Implement the Pytest Suite — IN PROGRESS**
  - Build coverage for database connectivity, migrations, model constraints,
    validation, authentication/authorization, API behavior, engine behavior,
    and golden policies.
  - Current evidence: API contract and authentication suites;
    `docker compose exec -T api pytest -q` returned `12 passed`.
- **[IMPROVE] Database Seeding Scripts — OPEN**
  - Add an idempotent `seed.py` using synthetic users, roles, permissions,
    projects, products, and representative actuarial metadata.
- **[IMPROVE] Upload Recovery — OPEN**
  - Define transaction boundaries and cleanup behavior for partially failed
    uploads, including orphaned local/object-storage files.

## 2. API Contract and Security

- **[BUILD] Global Authentication and Authorization (JWT) — IN PROGRESS**
  - Build users/RBAC persistence, token issuance, `/v1/auth/me`, and `/v1/users`.
  - Project endpoints enforce stored `projects:read` and `projects:write`
    permissions; uploads and registries remain restricted to admin/actuary roles.
  - Define the permission matrix for model developer, reviewer, and read-only
    roles before considering the authorization model complete.
- **[IMPROVE] API Versioning and Error Handling — COMPLETE**
  - Public application routers are mounted below `/v1`.
  - Errors use `{"error": {"code": "...", "message": "..."}}`, with optional
    structured details.
- **[IMPROVE] Project Mutation Endpoints — IN PROGRESS**
  - Added permission-protected `PATCH /v1/projects/{project_id}`.
  - Permanent project deletion is not exposed. Define an audited archive or
    soft-delete lifecycle and retention policy before adding removal behavior.
- **[BUILD] File Record Viewers — OPEN**
  - Add paginated, filterable record endpoints suitable for spreadsheet-style
    viewers. Define safe filter operators and maximum page sizes.
- **[BUILD] Dashboard Contract — OPEN**
  - Build `GET /v1/dashboard/stats` or explicitly defer it in the Launch 1.0
    frontend contract.
- **[CONDITIONAL] `/imports` to `/files` Adapter — DEFERRED**
  - Implement only after the frontend architecture is finalized.
- **[CONDITIONAL] `/formulas` to `/tables` Adapter — DEFERRED**
  - Implement only after the frontend architecture is finalized.

## 3. Database Schema and Governance

- **[BUILD] User and RBAC Schemas — COMPLETE**
  - Build `users`, `roles`, `permissions`, `user_roles`, and
    `role_permissions` with uniqueness and foreign-key constraints.
- **[BUILD] Audit Logs — OPEN**
  - Track actor, action, entity, before/after values, and timestamp for metadata
    mutations.
- **[IMPROVE] Actor Audit Fields — BLOCKED BY USERS**
  - Add `created_by` and `updated_by` to auditable metadata after users exist.
- **[IMPROVE] Soft Deletes — OPEN**
  - Define retention rules and add `deleted_at` to auditable actuarial metadata;
    avoid accidental hard deletion of governed records.
- **[BUILD] Products and Assets — OPEN**
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

- **[REPLACE] PostgreSQL `run_outputs` and `trace_logs` — OPEN**
  - Write compressed, partitioned Parquet to a local data lake or S3-compatible
    object storage. Keep only metadata, locations, fingerprints, and summaries
    in PostgreSQL.
  - Define an interface supporting local development and production object
    storage without changing engine code.
- **[BUILD] Immutable Run Manifests — OPEN**
  - Snapshot data-file, assumption, factor, scenario, formula/model, code, and
    storage versions when a run is queued.
- **[BUILD] Run, Result, and Trace APIs — BLOCKED BY STORAGE/MANIFESTS**
  - Build execution, status polling, summary, cashflow, event, and trace
    endpoints after the target persistence boundary exists.
- **[BUILD] Reconciliation Service — BLOCKED BY ANALYTICAL STORAGE**
  - Compare prior/current output datasets and return reserve bridges and exact
    variance components.

## Implementation Order

1. API foundation: versioning, error contract, project mutations, and initial
   endpoint tests. **In progress:** project archival remains open.
2. Users/RBAC schema, authentication endpoints, JWT configuration, and router
   authorization tests. **In progress:** the initial admin/actuary permission
   model is enforced; the expanded role matrix remains open.
3. Audit logs and actor fields.
4. Local/S3 Parquet storage interface and immutable run manifests.
5. Run, status, result, trace, and reconciliation services.
6. Product, asset, workflow, reporting, dashboard, and record-viewer domains.
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
- Permission-protected project PATCH: `backend/app/api/projects.py`
- Contract tests: `backend/tests/integration/test_api_contract.py`
- Auth/RBAC migration: `backend/app/db/migrations/versions/2f6d51e920a4_add_users_and_rbac.py`
- Authentication tests: `backend/tests/integration/test_auth_api.py`
- Verification command: `docker compose exec -T api pytest -q`
