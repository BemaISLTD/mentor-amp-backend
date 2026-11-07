# 00 REVIEW OVERVIEW AND MANIFEST (V4)
## MentorAmp Milestone 5 (Week 5) — Valuation Frameworks Foundation, Derived Dataset Registry, & Governed Dagster Orchestration

**Review Bundle:** `mentoramp_m5_senior_review_bundle_v4.zip`  
**Governed Branch:** `feat/m5-valuation-frameworks-dagster`  
**Alembic Head Revision:** `c9f4e2d8a615` (Single canonical head)  
**Package Generation Timestamp:** 2026-11-02T12:25:00-05:00  
**Status:** M5 FINAL SENIOR REVIEW BLOCKERS RESOLVED — PENDING SENIOR REVIEW  

---

### 1. Verification of Governed State & Invariants

| Invariant / Check | Specification Requirement | Repository Verification Result |
|---|---|---|
| **Branch** | `feat/m5-valuation-frameworks-dagster` | **VERIFIED** (`feat/m5-valuation-frameworks-dagster`) |
| **Governed Commit SHA** | Governed commit head | **`d3f1418`** |
| **Alembic Canonical Head** | `c9f4e2d8a615` (single head) | **VERIFIED** (`c9f4e2d8a615`) |
| **Unit Test Suite** | 100% green | **358 passed, 0 failed** (24.76s) |
| **Integration Test Suite** | 100% green (excluding opt-in) | **219 passed, 2 skipped, 0 failed** (486.83s) |
| **PostgreSQL Test Suite** | Real PostgreSQL verification | **24 passed, 0 failed** (141.88s) |
| **Total Automated Tests** | Parity across all suites | **601 passed, 2 skipped, 0 failed** |
| **Dagster Pure Orchestration** | Zero actuarial formulas in `assets.py` | **CONFIRMED** (Delegates 100% to `valuation_pipeline_service`) |
| **Dagster DB Persistence** | Mandatory `db: Session`, real DB entities | **CONFIRMED** (Runs, RunPackages, RunArtifacts, Details, Rollforwards, Reconciliations persisted) |
| **Dagster Real Retry** | Real transient error retry in action | **CONFIRMED** (`RetryRequested`, step restarted and succeeded on attempt 2) |
| **RunPackage Trusted Provenance** | Zero caller injection; PostgreSQL derived | **CONFIRMED** (Derived from source Run / RunPackage / Parquet on disk) |
| **RunPackage Build Identity** | Trusted `build_identity()` only | **CONFIRMED** (Fails closed 503 on missing/fallback) |
| **RunPackage Model Identity** | Zero fake defaults (`model-default`/`mv-default`) | **CONFIRMED** (Fails closed 400 `MODEL_IDENTITY_REQUIRED`) |
| **Physical Parquet Verification** | Physical file existence & checksum match | **CONFIRMED** (Resolves disk path, verifies SHA-256 match, fails closed) |
| **Distinct RunPackage vs Artifact FP**| Package FP != Parquet Artifact FP | **CONFIRMED** (Frozen separately and distinctly in valuation package) |
| **PostgreSQL Downgrade with Data** | Downgrade c9 -> b8 safe with existing rows | **CONFIRMED** (Staged backfill from `runs` table, NOT NULL, FKs, triggers verified) |

---

### 2. Core V4 Architectural Blockers Resolved

1. **Blocker 1 (P0): Dagster Real Database Persistence & Real Retry Demonstration**  
   - Removed `db=None` production path; all 8 pipeline operations strictly require active `db: Session`.
   - Eliminated silent `try: ... except Exception: rollback() continue` patterns; if database persistence fails, the stage fails closed immediately.
   - Reconciles against approved `ExpectedResultSet` (or evaluates `NOT_VERIFIABLE` if benchmark missing/unapproved, zero fake PASS).
   - Reporting stage consumes persisted identities (`valuation_run_id`, `project_id`) and creates an actual governed `RunArtifact` record in PostgreSQL with real Parquet/JSON file on disk and dynamic SHA-256 matching.
   - Comprehensive E2E test in `test_dagster_orchestration.py` proves real E2E materialization with actual governed database objects: canonical projection `Run`, `RunPackage`, `RunArtifact`, canonical valuation `Run`, `RunPackage`, `ValuationRunDetail`, `RollforwardSummary`, `ReconciliationReport`, and reporting `RunArtifact`.
   - Real Dagster Retry Demonstration: `test_dagster_real_retry_on_transient_infrastructure_failure` triggers an actual Dagster `RetryRequested(max_retries=2)` and proves that Dagster natively performs the retry in-process (`STEP_UP_FOR_RETRY -> STEP_RESTARTED`) and succeeds on attempt 2 without pipeline failure.

2. **Blocker 2 (P0): RunPackage Provenance from Trusted Sources Only**  
   - Removed `base_package_document` from external valuation submission payload semantics. External callers cannot inject arbitrary base packages or fake build SHAs.
   - Valuation provenance is derived strictly from PostgreSQL:
     - If source projection run: loads `Run` from PostgreSQL, verifies `project_id`, validates terminal status `completed`/`success` (rejects `pending`, `running`, `failed`), loads immutable persisted `RunPackage`, verifies envelope (`verify_envelope`) and configuration fingerprint (`verify`), and derives model, formulas, variables, assumptions, scenarios, and datasets.
     - If derived dataset: loads `DerivedDataset` from PostgreSQL, verifies `project_id`, requires `status == "approved"`, and derives lineage.
   - Build identity: Always acquired strictly from trusted `build_identity()` mechanism for the new valuation execution. Fails closed (503 `BUILD_IDENTITY_UNAVAILABLE`) if missing, zeroed, or marked `fallback`.
   - Eliminated fake defaults: Zero `model-default`/`mv-default` injection; fails closed (400 `MODEL_IDENTITY_REQUIRED`) if model identity cannot be established from trusted database provenance.
   - Distinct source artifact identity: `source_run_package_fingerprint` (configuration hash) is strictly distinct from `source_artifact_fingerprint` (Parquet byte SHA-256).
   - Physical artifact verification: Resolves physical file path from `RunArtifact.storage_uri`, asserts file exists on disk, reads bytes, and asserts SHA-256 matches recorded checksum. Fails closed with 400 (`PHYSICAL_ARTIFACT_MISSING` or `FINGERPRINT_MISMATCH`) without swallowing exceptions.

3. **Blocker 3 (P1): Make C9 -> B8 Downgrade Safe With Existing Data**  
   - Corrected Alembic migration `c9f4e2d8a615.downgrade()`:
     - Staged strategy: (1) Adds legacy columns as nullable (`project_id`, `source_projection_run_id`, `derived_dataset_id`, `basis`, `methodology`, `methodology_version`); (2) Backfills existing `valuation_run_details` rows from canonical parent `runs` table (`UPDATE valuation_run_details v SET project_id = r.project_id ... FROM runs r WHERE v.run_id = r.id;`); (3) Applies `NOT NULL` constraints, foreign keys (`projects.id`, `runs.id`, `derived_datasets.id`), check constraints (`ck_valuation_basis`, `ck_valuation_methodology`), and indexes without dummy defaults; (4) Drops `source_model_version_id` column and index from `derived_datasets`; (5) Restores b8 trigger function and trigger.
   - Real PostgreSQL test with data before downgrade: `test_m5_postgres_governance.py` seeds a real project, user, canonical valuation `Run`, and `ValuationRunDetail` row under c9 head BEFORE downgrade. Downgrades c9 -> b8, verifies the row survived and its columns were populated from parent `Run`. Verifies all foreign keys, check constraints, indexes, and b8 triggers. Re-upgrades b8 -> c9 and verifies schema consistency.

---

### 3. Complete Review Bundle Manifest

The bundle `mentoramp_m5_senior_review_bundle_v4.zip` contains the following items:

#### Category 1: Review Overview & Narrative Reports
1. `00_REVIEW_OVERVIEW_AND_MANIFEST.md` — This controlling overview and manifest document.
2. `IMPLEMENTATION_REPORT_V4.md` — Executive engineering report detailing V4 architecture.
3. `SENIOR_REVIEW_CORRECTIONS_V4.md` — Itemized 3-blocker resolution ledger.

#### Category 2: Git Patches & Specification Tracking
4. `git_diff_since_2e6a102.patch` — Unified git diff against baseline commit `2e6a102`.
5. `git_diff_since_v3.patch` — Unified git diff against V3 bundle head.
6. `git_diff_full_m5.patch` — Full Milestone 5 unified git diff against `main`.
7. `SAMUEL_M5_VALUATION_FRAMEWORKS_DAGSTER_IMPLEMENTATION_README.md` — Controlling engineering specification.
8. `PROJECT_MASTER_TRACKER.md` — Synchronized living requirements traceability ledger.

#### Category 3: Database Migrations & Data Models
9. `backend/app/db/migrations/versions/c9f4e2d8a615_m5_harden_governance_immutability.py` — Hardened migration with staged downgrade and data backfill from `runs`.
10. `backend/app/db/migrations/versions/b8f3d1e7c504_m5_valuation_frameworks_metadata.py` — Baseline valuation metadata migration.
11. `backend/app/db/models/governance.py` — Derived dataset and governance models.
12. `backend/app/db/models/valuation_detail.py` — Subordinate valuation detail model with property proxies.
13. `backend/app/models/schemas.py` — Projection context with canonical dimensions.

#### Category 4: Core Domain Logic & Valuation Services
14. `backend/app/core/valuation/taxonomy.py` — Basis and methodology separation rules.
15. `backend/app/core/valuation/rollforward.py` — Decimal rollforward continuity validator.
16. `backend/app/core/valuation/primitives.py` — Mathematical discounting and tail-metric primitives.
17. `backend/app/core/variable_registry/resolver.py` — Governed prior-output variable resolver.
18. `backend/app/core/execution/run_package.py` — RunPackage envelope verification with valuation block.
19. `backend/app/services/derived_dataset_service.py` — Governed derived dataset service with dimension lookup.
20. `backend/app/services/run_package_service.py` — Valuation provenance freezing service (fail-closed build identity, PostgreSQL lineage, physical artifact check).
21. `backend/app/services/run_submission_service.py` — Governed valuation run submission path (`submit_valuation_run`).
22. `backend/app/services/valuation_pipeline_service.py` — Pure actuarial pipeline domain service with mandatory DB session.

#### Category 5: Dagster Software-Defined Assets & Orchestration
23. `backend/app/orchestration/dagster/assets.py` — Pure 8-stage orchestrator with dynamic DB session and retry routing.
24. `backend/app/orchestration/dagster/definitions.py` — Dagster definitions entrypoint.
25. `backend/app/orchestration/dagster/jobs.py` — Dagster pipeline job definition.

#### Category 6: Automated Test Suites
26. `backend/tests/unit/test_derived_dataset_registry.py` — Security, lifecycle, and non-defaulting resolver tests.
27. `backend/tests/unit/test_run_package_valuation_reproducibility.py` — Blocker 2 provenance, physical artifact, distinct fingerprint, and fail-closed tests.
28. `backend/tests/unit/test_rollforward_continuity.py` — Decimal continuity and parent run binding tests.
29. `backend/tests/unit/test_valuation_primitives.py` — Valuation primitive math tests.
30. `backend/tests/unit/test_dagster_orchestration.py` — Blocker 1 real E2E materialization, PostgreSQL row verification, and real Dagster retry demonstration tests.
31. `backend/tests/unit/test_basis_methodology_separation.py` — Basis taxonomy and launch basis tests.
32. `backend/tests/postgres/test_m5_postgres_governance.py` — Blocker 3 real PostgreSQL lifecycle, downgrade with pre-existing data, and trigger immutability tests.
33. `backend/tests/postgres/test_wp1_migrations_postgres.py` — Full PostgreSQL migration and concurrency suite verified against canonical head `c9f4e2d8a615`.

#### Category 7: Verification Logs & Audit Evidence
34. `unit_tests.log` — Full pytest output for unit test suite (358 passed).
35. `integration_tests.log` — Full pytest output for integration test suite (219 passed, 2 skipped).
36. `postgres_tests.log` — Full pytest output for PostgreSQL test suite (24 passed).
37. `dagster_execution_evidence.log` — Full pytest output demonstrating Dagster E2E persistence and real retry (6 passed).
38. `run_package_reproducibility_evidence.log` — Full pytest output demonstrating RunPackage reproducibility & trusted provenance (28 passed).
39. `migration_evidence.log` — Alembic migration head and schema verification evidence.
40. `migration_downgrade_with_data.log` — Alembic downgrade with real pre-existing data verification evidence.
41. `derived_dataset_evidence.log` — Derived dataset governance and security evidence.
42. `git_status.log` — Clean working tree audit log.
43. `git_log.log` — Recent commit audit log.
