# Senior Review Corrections Ledger (V4)
## Milestone 5 (Week 5) — Valuation Frameworks Foundation, Derived Dataset Registry, & Governed Dagster Orchestration

This document details the precise, surgical implementation corrections applied to resolve all **THREE remaining merge-blocking issues** identified in the senior review of the V3 codebase.

---

### Blocker 1 (P0): Dagster Real Database Persistence & Real Retry Demonstration

#### Finding
1. Dagster assets permitted a `db=None` production orchestration fallback path.
2. Certain persistence calls swallowed exceptions via `try: ... except Exception: rollback() continue` patterns, masking persistence failures while reporting success.
3. Reconciliation fabricated PASS without verifying against approved benchmarks.
4. Reporting exported in-memory summaries without persisting a canonical `RunArtifact` to PostgreSQL.
5. Previous tests asserted static retry policy definitions rather than proving an actual in-process execution retry during transient infrastructure failure.

#### Resolution
1. **Mandatory Database Session (`db: Session`):**
   - Eliminated the `db=None` production orchestration fallback across all 8 pipeline operations in `backend/app/services/valuation_pipeline_service.py` (`execute_inforce_import`, `execute_inforce_validation`, `execute_projection_simulation`, `execute_cash_flow_aggregation`, `execute_foundation_valuation`, `execute_rollforward_attribution`, `execute_governed_reconciliation`, `execute_reporting_manifest`).
   - If `db is None`, domain services immediately raise `ServiceError(500, "DATABASE_REQUIRED")`.
2. **Fail-Closed Persistence (Zero Swallowed Exceptions):**
   - Removed all `try...except rollback continue` blocks. Any database failure raises immediately, terminating the pipeline step cleanly.
   - An artifact ID is NEVER returned unless the physical file exists on disk AND the `RunArtifact` row is committed in PostgreSQL.
3. **Approved ExpectedResultSet Reconciliation:**
   - `execute_governed_reconciliation` accepts an optional `expected_result_set_id`.
   - If provided, it loads the benchmark from PostgreSQL, verifies `project_id` matching, verifies `status == "approved"`, and compares values against `tolerance`.
   - If no benchmark is provided, report status is strictly `NOT_VERIFIABLE`. A missing benchmark never fabricates a `PASS`.
   - The report is committed as a canonical `ReconciliationReport` entity in PostgreSQL.
4. **Governed Reporting RunArtifact Persistence:**
   - `execute_reporting_manifest` consumes persisted entity identities (`valuation_run_id`, `project_id`).
   - Writes the disclosure package JSON to artifact storage on disk, calculates its SHA-256 digest, and persists a canonical `RunArtifact(artifact_type="reporting", file_format="json", ...)` in PostgreSQL.
5. **Real E2E Asset Materialization & Database Verification:**
   - In `backend/tests/unit/test_dagster_orchestration.py`, `test_dagster_end_to_end_pipeline_materialization_and_physical_persistence` executes all 8 assets via Dagster `materialize()`.
   - Directly inspects PostgreSQL database records post-execution:
     - `runs`: Projection run and valuation run are present with `status="success"`.
     - `run_packages`: Immutable projection and valuation packages are persisted.
     - `run_artifacts`: Parquet cash flows and JSON disclosure manifest artifacts exist in DB and on disk with matching SHA-256 hashes.
     - `valuation_run_details`: Subordinate valuation detail row is linked to valuation run.
     - `rollforward_summaries`: Rollforward continuity row is persisted.
     - `reconciliation_reports`: Governed reconciliation report row is persisted with `status="NOT_VERIFIABLE"` (unbenchmarked) or `"PASS"` (benchmarked).
6. **Real Dagster Transient Failure Retry Demonstration:**
   - Implemented `test_dagster_real_retry_on_transient_infrastructure_failure` in `test_dagster_orchestration.py`.
   - A transient infrastructure failure raises Dagster's native `RetryRequested(max_retries=2, seconds_to_wait=0.05)`.
   - The test demonstrates that Dagster intercepts `RetryRequested`, logs step retry events (`STEP_UP_FOR_RETRY -> STEP_RESTARTED`), executes attempt 2, and succeeds cleanly without pipeline failure.

---

### Blocker 2 (P0): RunPackage Provenance from Trusted Sources Only

#### Finding
1. `submit_valuation_run()` accepted `base_package_document` directly from external submission payloads.
2. `freeze_valuation_run_package()` trusted caller-supplied build identities, formulas, and parameters instead of obtaining provenance from immutable database entities.
3. Model identity fell back to artificial defaults (`model-default`, `mv-default`).
4. Physical Parquet artifacts were not verified on disk during package freezing.
5. `source_run_package_fingerprint` was conflated with `source_artifact_fingerprint`.

#### Resolution
1. **Removed `base_package_document` from Payload Semantics:**
   - Removed `base_package_document` from `submit_valuation_run()` and `freeze_valuation_run_package()`.
   - External payloads cannot inject untrusted RunPackage documents, build fingerprints, or formulas.
2. **Provenance Derived Exclusively from Trusted PostgreSQL Entities:**
   - **From Source Projection Run:**
     1. Loads `Run` from PostgreSQL and enforces `run.project_id == project_id` (404 on cross-project);
     2. Verifies terminal status is `completed` or `success` (400 `INVALID_SOURCE_RUN_STATUS` on `pending`/`running`/`failed`);
     3. Loads immutable persisted `RunPackage` from PostgreSQL;
     4. Verifies envelope integrity via `run_package.verify_envelope()`;
     5. Verifies package configuration digest via `run_package.verify()`;
     6. Derives model, formulas, variables, parameters, datasets, and scenarios directly from the trusted source package.
   - **From Approved Derived Dataset:**
     1. Loads `DerivedDataset` from PostgreSQL and enforces `project_id == project_id` (404 on cross-project);
     2. Enforces `status == "approved"` (400 `DATASET_NOT_APPROVED` on draft/unapproved);
     3. Verifies non-deleted and non-superseded status.
3. **Strict Build Identity (Always Fresh & Verified):**
   - For every new valuation run, build identity is obtained exclusively via trusted `build_identity()` call.
   - Fails closed with HTTP 503 (`BUILD_IDENTITY_UNAVAILABLE`) if build info is missing, zeroed, or marked `fallback`.
4. **Zero Fake Identity Defaults:**
   - Eliminated `model-default` and `mv-default` fallbacks.
   - If model identity cannot be established from trusted database provenance or validated project models, the service fails closed with HTTP 400 (`MODEL_IDENTITY_REQUIRED`).
5. **Physical Artifact Existence & Checksum Verification:**
   - In `freeze_valuation_run_package()`, the source run's `RunArtifact` is resolved.
   - The physical file path is resolved via `artifact_store.path_for_uri(storage_uri)`.
   - Asserts file exists on disk (fails closed with 400 `PHYSICAL_ARTIFACT_MISSING` if absent).
   - Computes SHA-256 digest of file bytes and asserts exact match against recorded `checksum_sha256` (fails closed with 400 `FINGERPRINT_MISMATCH` on tamper).
6. **Distinct Fingerprint Linage:**
   - The frozen valuation configuration explicitly records:
     - `source_run_package_fingerprint`: Canonical JSON hash of source run's configuration document.
     - `source_artifact_fingerprint`: SHA-256 checksum of physical Parquet artifact file.
   - The two fingerprints are fundamentally distinct and verified independently.

---

### Blocker 3 (P1): Make C9 -> B8 Downgrade Safe With Existing Data

#### Finding
The previous `c9f4e2d8a615` downgrade implementation added `NOT NULL` columns without backfilling existing rows from the canonical `runs` table, which would cause downgrade failures when applied to production databases containing existing valuation run details.

#### Resolution
1. **Staged Downgrade Strategy in Migration `c9f4e2d8a615`:**
   - **Phase 1 (Add Nullable Columns):** Adds `project_id`, `source_projection_run_id`, `derived_dataset_id`, `basis`, `methodology`, `methodology_version` as nullable.
   - **Phase 2 (SQL Backfill from Canonical Parent `runs`):**
     ```sql
     UPDATE valuation_run_details v
     SET project_id = r.project_id,
         source_projection_run_id = r.source_projection_run_id,
         derived_dataset_id = r.derived_dataset_id,
         basis = r.basis,
         methodology = r.methodology,
         methodology_version = r.methodology_version
     FROM runs r
     WHERE v.run_id = r.id;
     ```
   - **Phase 3 (Enforce Constraints, FKs, Checks, and Indexes):**
     - Sets `project_id`, `basis`, `methodology`, `methodology_version` to `NOT NULL`.
     - Adds foreign keys to `projects(id)`, `runs(id)`, `derived_datasets(id)`.
     - Adds check constraints `ck_valuation_basis` and `ck_valuation_methodology`.
     - Adds indexes `idx_valuation_run_details_project` and `idx_valuation_run_details_source_run`.
   - **Phase 4 (Revert `derived_datasets`):**
     - Drops `source_model_version_id` column and index.
   - **Phase 5 (Restore Original B8 Trigger):**
     - Recreates `trg_prevent_derived_dataset_tampering` with original b8 trigger logic.
2. **PostgreSQL Verification with Pre-Seeded Real Data:**
   - In `backend/tests/postgres/test_m5_postgres_governance.py`:
     - Upgrades empty database to `c9f4e2d8a615`.
     - Seeds a real project, user, canonical valuation `Run`, and `ValuationRunDetail` row under c9 head.
     - Executes `command.downgrade(config, "b8f3d1e7c504")`.
     - Asserts row survives downgrade and all restored columns (`project_id`, `basis`, `methodology`, `methodology_version`) match the parent `Run` row exactly.
     - Verifies foreign keys, check constraints, and b8 triggers block unauthorized operations.
     - Re-upgrades `b8f3d1e7c504 -> c9f4e2d8a615` and verifies c9 schema consistency and immutability triggers.
