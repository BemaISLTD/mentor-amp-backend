# Senior Review Corrections Ledger (V3)
## Milestone 5 (Week 5) — Valuation Frameworks Foundation, Derived Dataset Registry, & Governed Dagster Orchestration

This document details the surgical implementation corrections applied to resolve all findings from Senior Review V2.

---

### Directive 1 (P0): Make Dagster a Real Orchestrator

#### Finding
The previous `assets.py` violated core architecture boundaries by containing actuarial simulation loops, interest/surrender calculations, inline valuation formulas, and hardcoded rollforward values. Calculation data was passed primarily as in-memory dictionaries without proving physical persistence on disk.

#### Resolution
1. **Actuarial Logic Relocated to Domain Service:** Created `backend/app/services/valuation_pipeline_service.py` to house all domain calculations, file persistence, columnar aggregation, TVOM discounting, Decimal continuity, governed reconciliation, and reporting export.
2. **Dagster Asset Purity:** Refactored `backend/app/orchestration/dagster/assets.py` to contain **ZERO actuarial formulas**. Assets strictly:
   - Validate orchestration inputs (`InforceConfig`, `ProjectionConfig`, `ValuationConfig`, `ReconciliationConfig`);
   - Invoke corresponding `valuation_pipeline_service` functions;
   - Capture persisted Run, RunArtifact, and physical file identities;
   - Emit Dagster metadata and lineage;
   - Route failures: deterministic failures halt immediately (`allow_retries=False`), while transient errors retry via `DEFAULT_RETRY_POLICY`.
3. **Physical Parquet Persistence Verified:** `execute_projection_simulation` writes actual Parquet files to disk using `LocalParquetArtifactStore`. Downstream `execute_cash_flow_aggregation` reads the Parquet file from disk via Polars.
4. **Governed Reconciliation:** Integrates three-state reconciliation: when no external benchmark is provided, the report status evaluates to `NOT_VERIFIABLE` (no fake identity pass). When a benchmark is supplied, it strictly checks `diff <= tolerance` (`PASS` vs `FAIL`).
5. **Generic TVOM Discounting Preserved:** Clearly documented that generic present value discounting serves as foundation math without claiming VM-21 or VM-22 regulatory compliance.

---

### Directive 2 (P0): Finish Valuation RunPackage Integration

#### Finding
`freeze_valuation_run_package()` existed as an isolated helper and was not wired into a production submission flow. It also contained insecure fallbacks: zero build fingerprint, zero commit SHA, and `identity_source = fallback`.

#### Resolution
1. **Production Submission Path:** Created `submit_valuation_run()` in `backend/app/services/run_submission_service.py`. It authorizes project access, initializes canonical `Run`, freezes the valuation package via `freeze_valuation_run_package()`, attaches package fingerprint to `Run`, persists `RunPackage`, creates subordinate `ValuationRunDetail`, and logs a queued run event atomically.
2. **Eliminated Fallback Identity:** Completely removed all fallback identity generation. In `freeze_valuation_run_package()`, if build identity is missing, invalid, or marked as `fallback`, the service fails closed with HTTP 503 (`BUILD_IDENTITY_UNAVAILABLE`).
3. **Material Provenance Derived from Database:** When database session is available, `freeze_valuation_run_package()` queries trusted DB objects:
   - Verifies `run.project_id == project.id` (404 on cross-project);
   - Queries `source_projection_run_id` and ensures it belongs to `project.id` (404 on cross-project);
   - Queries `derived_dataset_id`, verifies project ownership (404 on cross-project), verifies `status == "approved"` (400 if unapproved), and verifies physical artifact content matches recorded fingerprint (400 on mismatch).
4. **Unit Test Coverage:** Added comprehensive unit tests in `test_run_package_valuation_reproducibility.py` exercising real service freeze, missing build identity rejection, foreign run rejection, unapproved dataset rejection, fingerprint mismatch rejection, reproducibility, and end-to-end `submit_valuation_run`.

---

### Directive 3 (P0): Variable Resolver Governance Errors Must Not Default

#### Finding
The Variable Resolver caught all exceptions from `prior_output` lookup and defaulted to `default_value`, masking security and governance violations (e.g., cross-project access, unapproved datasets, fingerprint tampering).

#### Resolution
1. **Exception Isolation:** In `backend/app/core/variable_registry/resolver.py`, `DerivedDatasetGovernanceError`, `AmbiguousDerivedOutputError`, and `ServiceError` are explicitly re-raised and never swallowed.
2. **Strict Default Fallback:** Default value fallback is strictly conditioned on `value is None and error is None` (i.e. legitimate missing data row from a valid, approved, active source).
3. **Unit Test Verification:** Added `test_prior_output_variable_resolver_does_not_default_on_governance_errors` in `test_derived_dataset_registry.py` verifying that foreign-project datasets, unapproved datasets, superseded datasets, fingerprint mismatches, and ambiguous multi-segment matches NEVER return `default_value`.

---

### Directive 4 (P1): Close Approved Dataset Status-Downgrade Loophole

#### Finding
The PostgreSQL trigger allowed an approved dataset to downgrade status to `draft` or `validated` if material columns were untouched in that same update transaction, opening a window to mutate evidence in a subsequent transaction.

#### Resolution
1. **Narrowed Status Transitions:** Hardened `derived_dataset_immutability_trigger` in migration `c9f4e2d8a615` so that when `OLD.status = 'approved'`, `NEW.status` is strictly constrained to `('approved', 'superseded')`. Attempts to downgrade to `'draft'`, `'validated'`, or any other status raise an exception.
2. **Soft-Delete Mutation Blocked:** The trigger strictly prohibits setting `deleted_at` or `deleted_by` on approved or superseded datasets.
3. **PostgreSQL Test Verification:** Added real PostgreSQL test cases in `test_m5_postgres_governance.py` proving `approved -> draft`, `approved -> validated`, and `superseded -> draft` transitions are blocked by the database.

---

### Directive 5 (P1): Fix Alembic Downgrade Fidelity

#### Finding
`c9f4e2d8a615.downgrade()` did not restore the exact `b8f3d1e7c504` schema. Columns were restored without their original non-null constraints, foreign keys, check constraints, indexes, and prior triggers.

#### Resolution
1. **Faithful Schema Restoration:** Updated `c9f4e2d8a615.downgrade()` to:
   - Add columns `project_id`, `basis`, `methodology`, `methodology_version`, `source_projection_run_id`, `derived_dataset_id` with non-null constraints;
   - Restore foreign keys to `projects.id`, `runs.id`, `derived_datasets.id`;
   - Restore check constraints `ck_valuation_basis` and `ck_valuation_methodology`;
   - Restore indexes `idx_valuation_run_details_project` and `idx_valuation_run_details_source_run`;
   - Drop `source_model_version_id` column and foreign key;
   - Recreate the original b8 trigger function and trigger.
2. **Full Lifecycle Test:** `test_m5_postgres_governance.py` executes zero -> c9 -> b8 (verifying columns, nullability, FKs, indexes, check constraints, triggers) -> c9 (re-verifying c9 schema and triggers) on live PostgreSQL.

---

### Directive 6: Evidence & Git State Cleanup

#### Resolution
1. **Explicit Commit SHAs:**
   - Implementation Commit SHA: `5ebe8b2`
   - Governed Branch: `feat/m5-valuation-frameworks-dagster`
   - Alembic Canonical Head: `c9f4e2d8a615`
2. **Clean Working Tree:** Verified git working tree is clean with zero unrelated untracked files.
3. **Fresh Unified Patches:**
   - `git_diff_since_2e6a102.patch` (diff from baseline `2e6a102` to HEAD)
   - `git_diff_since_v2.patch` (diff from V2 documentation `797fe0d` to HEAD)
   - `git_diff_full_m5.patch` (complete M5 diff against `main`)
4. **Fresh Test Logs:** Generated full test logs for unit (352 passed), integration (219 passed, 2 skipped), and PostgreSQL (1 passed) suites.
