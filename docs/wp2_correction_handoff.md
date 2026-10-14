# WP2 correction handoff

## 1. Summary

The senior-review correction pass now has one canonical Parquet result path,
safe M1 legacy export, verified immutable evidence, model-aware and
concurrency-safe Data Manager versioning, bounded ingestion, deterministic run
cancel/retry, quarantined future APIs, currency-safe dashboards, exact golden
numbers, a tracked frontend contract, and reliable SQLite and PostgreSQL
evidence.

## 2. Branch / full HEAD SHA / PR

- Branch: feat/m2-convergence
- Base HEAD before the correction commit: d1c1690126c5d96b01c17639dc48d3667443d1fa
- Correction implementation commit: 1edb57b726fea9b75169a28cf12c4ae581da2c7f
- PR: use the existing feat/m2-convergence -> main PR, or create it after push

The implementation SHA above is immutable. The PR URL must be added by the
owner when the private repository PR is created or updated.

## 3. Finding disposition table

| Finding | Status | Main files | Evidence |
| --- | --- | --- | --- |
| P0-01 final suite | PASS | importers, exact golden tests | 268 passed; exit 0 |
| P0-02 M1 to Parquet | PASS | legacy_artifact_bridge.py, ab6d migration | populated M1 upgrade test |
| P0-03 second run architecture | PASS | main.py, legacy route removal test | public/OpenAPI 404 tests |
| P0-04 cancel race | PASS | run_finalization.py | both race orderings tested |
| P1-01 artifact integrity | PASS | artifacts.py | missing/checksum/count/schema/identity tests |
| P1-02 RunArtifact retention | PASS | immutability.py, ab6d migration | ORM and PostgreSQL trigger tests |
| P1-03 append-only audit | PASS | immutability.py, ab6d migration | ORM and PostgreSQL trigger tests |
| P1-04 model-aware validation | PASS | data_manager_service.py | table/scenario compatibility tests |
| P1-05 version concurrency | PASS | data_manager_service.py, ab6d migration | two-thread PostgreSQL test |
| P1-06 committed content | PASS | models, immutability.py, ab6d migration | ORM and direct SQL refusal |
| P1-07 resource limits | PASS | config.py, importers | size/row/column/sheet/malformed tests |
| P1-08 Parquet performance | PASS | artifacts.py, run_execution_service.py | filtered scan and one-file tests |
| P1-09 premature APIs | PASS | main.py, governance.py | absent from /v1 and OpenAPI |
| P1-10 self-publish | PASS | modeling.py | public publish removed |
| P1-11 frozen formulas | PASS | formula registry | update/delete return immutable error |
| P1-12 multi-scenario outputs | PASS | ImportSessionDataset, ab6d migration | all dataset IDs persisted |
| P1-13 frontend contract | PASS | wp2_frontend_api_contract.md | tracked schemas and flows |
| P1-14 mixed currencies | PASS | dashboard API/model | no combined scalar; currency retained |
| P2-01 retry concurrency | PASS | run_control_service.py, runs constraint | sequential and two-thread tests |
| P2-02 orphan files | PASS | artifact_reconciliation.py, operator script | dry-run and retained-evidence test |
| P2-03 XLS contract | PASS | importers | .xlsx accepted; .xls rejected |
| P2-04 exact goldens | PASS | SPIA formulas, execution service, tests | all six exact values |

## 4. Migration graph

Before convergence, backlog and correction work had separate history. The
joined graph has one head:

    ... -> e6a9c4d2f817 -> ab6d9e2f4013 -> d4f7a9c2e681

Alembic head: d4f7a9c2e681.

The legacy-bridge revision adds the verified bridge gate, Data Manager evidence
columns and association table, version uniqueness, retry uniqueness, retained
RunArtifact foreign key, audit retention, and PostgreSQL immutability triggers.
The forward revision d4f7a9c2e681 widens run_attempts.cleanup_status to 32
characters so retained_noncanonical terminal outcomes commit on PostgreSQL.

## 5. M1 legacy-result migration evidence

The PostgreSQL populated-M1 test creates two output rows and one trace row,
runs the operator bridge at the pre-drop revision, verifies two Parquet
artifacts by checksum and row count, deletes relational source rows only after
verification in the same transaction, upgrades to head, and confirms canonical
result, trace, service, and manifest readability.

The migration refuses to drop non-empty legacy tables that have not been
bridged. Bridge-created files are removed if the transaction fails.

## 6. Execution architecture

The only public execution path is:

    frozen RunPackage -> verified build/formulas/datasets -> RunAttempt
    -> pure engine -> attempt-scoped Parquet -> terminal finalization
    -> accepted attempt -> immutable manifest

Public readers select only the accepted attempt. The old run/result/trace
routers are not mounted and do not appear in OpenAPI.

## 7. Data Manager lifecycle

The implemented flow is open, upload, preview, map, validate, inspect evidence,
commit immutable version, independently approve/reject, list history, and
compare. It covers liability inforce, assumptions, factors, and scenarios.

Raw and canonical artifacts retain checksums. ImportSessionDataset records every
output of multi-scenario imports. Version allocation locks the predecessor and
rejects stale concurrent replacements. Committed content is immutable in the
ORM and PostgreSQL.

## 8. Cancel/retry

Pending cancellation is immediate and unclaimable. Running cancellation is
cooperative between policies; finalization locks the run and treats an accepted
cancel request as authoritative. Partial artifacts remain noncanonical.

Retry is allowed for failed, cancelled, and partial-success runs. It copies the
exact frozen package into a new linked run. Repeated or concurrent requests for
one source return one successor, enforced by source-row locking and a unique
database constraint.

## 9. Evidence integrity

- Parquet reads verify checksum, row count, schema, run ID, and attempt number.
- Raw uploads verify checksum and byte size.
- Canonical and rejected Data Manager evidence verify checksum and row count.
- RunArtifact metadata is update/delete protected in ORM and PostgreSQL.
- AuditLog rows are append-only in ORM and PostgreSQL.
- Missing, corrupt, incompatible, or misidentified evidence returns a stable
  error and is not silently read.

## 10. Files changed

- backend/app/api/dashboard.py — currency-preserving dashboard aggregation.
- backend/app/api/data_manager.py — governed routes and rejected-row pagination.
- backend/app/api/governance.py — public run steps and experimental metadata split.
- backend/app/api/imports.py — bounded canonical legacy wrappers.
- backend/app/api/modeling.py — removes unsafe public model self-publish.
- backend/app/config.py — import and artifact batching limits.
- backend/app/core/artifacts.py — verified lazy Parquet reads.
- backend/app/core/formula_engine/registry.py — frozen-version formula guard.
- backend/app/data/importers/base.py — bounded parser contract.
- backend/app/data/importers/csv_importer.py — streaming bounded CSV.
- backend/app/data/importers/excel_importer.py — read-only bounded XLSX.
- backend/app/data/importers/tsv_importer.py — streaming bounded TSV.
- backend/app/db/immutability.py — reusable update/delete protection.
- backend/app/db/models/__init__.py — association-model registration.
- backend/app/db/models/assumption.py — version uniqueness and immutability.
- backend/app/db/models/audit_log.py — retained actor reference and append-only guard.
- backend/app/db/models/data_manager.py — session outputs and artifact evidence.
- backend/app/db/models/factor.py — version uniqueness and immutability.
- backend/app/db/models/inforce.py — version and record immutability.
- backend/app/db/models/run.py — one successor retry per source.
- backend/app/db/models/run_artifact.py — retained immutable evidence.
- backend/app/db/models/scenario.py — version uniqueness and immutability.
- backend/app/main.py — canonical public routers and experimental quarantine.
- backend/app/models/dashboard.py — currency-aware response schema.
- backend/app/products/spia_lite/config.py — deterministic formula boundaries.
- backend/app/services/artifact_reconciliation.py — safe orphan discovery/deletion.
- backend/app/services/data_manager_service.py — complete governed lifecycle.
- backend/app/services/legacy_artifact_bridge.py — verified relational-to-Parquet bridge.
- backend/app/services/results_service.py — filtered verified reads.
- backend/app/services/run_control_service.py — idempotent frozen retry.
- backend/app/services/run_execution_service.py — batching and exact deterministic totals.
- backend/app/services/run_finalization.py — cancellation-authoritative finalization.
- backend/app/services/trace_service.py — filtered verified trace reads.
- backend/app/db/migrations/versions/ab6d9e2f4013_finalize_legacy_artifact_bridge.py — forward correction revision.
- backend/scripts/migrate_legacy_run_artifacts.py — explicit legacy bridge command.
- backend/scripts/reconcile_orphan_artifacts.py — dry-run-first orphan operator command.
- backend/tests/integration/test_actuarial_governance.py — quarantine and formula tests.
- backend/tests/integration/test_dashboard_api.py — mixed-currency tests.
- backend/tests/integration/test_data_manager.py — full lifecycle and limits.
- backend/tests/integration/test_data_manager_rebuild.py — uploaded-file exact goldens.
- backend/tests/integration/test_legacy_run_routes_removed.py — canonical route proof.
- backend/tests/integration/test_m1_end_to_end.py — exact trace golden.
- backend/tests/integration/test_run_api.py — removed obsolete duplicate tests.
- backend/tests/integration/test_run_control.py — cancel/retry/race/exact tests.
- backend/tests/integration/test_wp1_authorization.py — canonical authorization matrix.
- backend/tests/integration/test_wp1_run_safety.py — accepted-attempt safety.
- backend/tests/postgres/test_wp1_migrations_postgres.py — migration and concurrency proofs.
- backend/tests/unit/test_artifact_storage.py — integrity/filter/orphan tests.
- backend/tests/unit/test_importers.py — parser limits and Windows descriptor regression.
- backend/tests/unit/test_wp1_alembic_history.py — one-head assertions.
- docs/phase_1_backlog.md — truthful verified status.
- docs/wp2_frontend_api_contract.md — authoritative frontend contract.
- docs/wp2_correction_handoff.md — this evidence report.

- backend/app/db/migrations/versions/d4f7a9c2e681_widen_attempt_cleanup_status.py - PostgreSQL terminal-outcome correction.

The excluded local files docs/build/02_BACKEND_FRONTEND_CONTRACT.md and
docs/delivery_acceptance_report.md remain untracked and are not part of this
handoff.

## 11. SQLite test results

Named container mentor_amp_cleanup_status_full:

- 268 passed
- 0 failed
- 19 skipped because PostgreSQL is opt-in in this run
- 1 Starlette dependency deprecation warning
- duration 179.72 seconds
- exit code 0

Native Windows Python 3.13:

- 269 passed
- 0 failed
- 18 skipped because PostgreSQL is opt-in in this run
- duration 129.25 seconds
- exit code 0

## 12. PostgreSQL test results

Named container mentor_amp_cleanup_status_pg_full against disposable PostgreSQL 15:

- 18 passed
- 0 failed
- 0 skipped
- 34 SQLAlchemy deprecation warnings from an older RBAC migration
- 1 Starlette dependency deprecation warning
- duration 141.86 seconds
- exit code 0

## 13. Migration path results

| Path | Result |
| --- | --- |
| empty database to head | PASS |
| either pre-join branch head to join/head | PASS |
| populated M1 legacy result/trace to head | PASS |
| backlog head to head | PASS |
| correction head to head | PASS |
| supported downgrade then upgrade | PASS |
| one canonical Alembic head | PASS |

All paths ran only on the disposable database mentor_amp_wp2_test.

## 14. Golden-number table

| Result | Measured | Result |
| --- | ---: | --- |
| Base reserve, 600 months | 4,889,731.244841259 | PASS |
| Low Interest Rate reserve, 600 months | 5,541,437.408305269 | PASS |
| Undiscounted expected payments | 7,419,301.206836598 | PASS |
| Base reserve, 24 months | 893,043.9157255866 | PASS |
| Low Interest Rate reserve, 24 months | 906,298.662195956 | PASS |
| SPIA-0001 month-12 PV trace | 1,533.2093195295497 | PASS |

Assertions use exact equality. Two identical submissions retain the same frozen
configuration fingerprint and deterministic aggregates.

## 15. Data Manager acceptance tests

PASS: CSV, TSV, XLSX, Parquet, renamed mappings, profile reuse, missing field,
duplicate ID/key, invalid type, warning-only input, future date, invalid
probability, model lookup/value/unit compatibility, scenario target/overlap
governance, empty/unsupported/malformed/oversized files, cross-format
fingerprints, immutable v1/v2, concurrent allocation, comparison, permissions,
self-approval refusal, immutable audit/content, checksum failure, multi-scenario
output persistence, raw download, issue/rejected-row evidence and pagination.

## 16. Cancel/retry acceptance tests

PASS: pending cancel, running cancel, both finalization race orderings, terminal
cancel refusal, mixed RunSet outcomes, failed/cancelled/partial retry, successful
retry refusal, changed-build refusal, frozen-package identity, repeated retry,
and concurrent retry on PostgreSQL.

## 17. AC-1 through AC-13

| AC | Status | Evidence |
| --- | --- | --- |
| AC-1 one integration branch | PASS | feat/m2-convergence |
| AC-2 one head/migration paths | PASS | 15 PostgreSQL tests |
| AC-3 canonical evidence architecture | PASS | public Parquet accepted-attempt path |
| AC-4 full Data Manager lifecycle | PASS | API and integration suite |
| AC-5 raw/mapping/validation/rejection/log | PASS | persisted evidence |
| AC-6 alternate-layout mapping | PASS | renamed-column test |
| AC-7 validation categories | PASS | model/data/scenario matrix |
| AC-8 immutable versions | PASS | ORM, trigger and concurrency tests |
| AC-9 independent approval/audit | PASS | permission/self-review/append-only tests |
| AC-10 cancel/retry | PASS | race and retry matrix |
| AC-11 file rebuild goldens | PASS | six exact assertions |
| AC-12 wrappers/frontend contract | PASS | all four wrappers and tracked contract |
| AC-13 tests/report | PASS | sections 11–16 and this report |

## 18. Frontend contract changes

docs/wp2_frontend_api_contract.md documents session statuses, upload/preview,
mapping/profile reuse, validation, rejected rows, multi-dataset commit,
approval/rejection, versions/compare, raw download, pagination, permissions,
archive behavior, cancel/retry fields and examples, stable errors, and
deprecated wrappers. Public OpenAPI reflects the implemented routes; deferred
governance metadata is omitted.

## 19. Performance

Representative Docker workload: 25 policies, 600 months, one Base scenario.
These are development-machine observations, not production capacity claims.

| Measurement | Observed |
| --- | ---: |
| upload | 1,165 bytes / 25 rows |
| upload parse/store | 0.1693 s |
| mapping | 0.0233 s |
| validation | 0.1376 s |
| commit/canonical write | 0.0750 s |
| run load | 0.0162 s |
| engine calculation | 6.7808 s |
| output artifact write | 1.2360 s |
| trace artifact write | 0.8955 s |
| DB commit time | 0.0069 s |
| run execution total | 9.4170 s internal / 9.4957 s caller |
| output artifact files/rows | 1 / 60,025 |
| trace artifact files/rows | 1 / 23,403 |
| aggregate query | 0.2873 s |
| selected-policy query | 0.0369 s, one returned row |
| process maximum RSS | about 276 MiB |

Filtered result/trace queries push predicates into Polars lazy scans after
checksum/count/identity verification; they do not first materialize all rows
into Python. File count is bounded by row target rather than policy count.

## 20. Known remaining issues

- Local filesystem storage is not production object storage.
- FastAPI TestClient emits one Starlette/httpx deprecation warning.
- The old RBAC migration emits SQLAlchemy Result.tuples deprecation warnings.
- A native Windows Python environment was unavailable; descriptor closure has a
  regression test, while all executable tests ran in Linux containers.
- Raw download returns the retained file as one response body; large production
  object streaming remains future infrastructure work.

## 21. Deferred out-of-scope work

MYGA, FIA, RILA, IDI, VA, PRT, VM-21, VM-22, LDTI, DAC, MRB, ASC 815, Formula
Studio UI, business expected-results reconciliation, Dagster, PySpark, GPU,
cloud/S3, a production worker queue, a full retention product, and new actuarial
assumptions were not added. Rollforward/report/derived-dataset metadata remains
internal experimental scaffolding rather than a claimed public capability.

## 22. Shared DB status

The shared neondb was not migrated, reset, seeded, or used for correction
testing. All destructive migration tests ran only against the disposable local
PostgreSQL database named mentor_amp_wp2_test.
