# IMPLEMENTATION REPORT V2: M5 VALUATION FOUNDATIONS & SENIOR CORRECTIONS
## MentorAmp Milestone 5 (Week 5) — Comprehensive Engineering Report

### 1. Executive Summary
Following the senior review of `mentoramp_m5_senior_review_bundle.zip` (verdict: `REQUEST CHANGES — DO NOT MERGE YET` on commit `2e6a102`), a rigorous, surgical correction pass was completed across all 22 directives.

The core foundation of Milestone 5 is now fully secured, audited, and verified:
- **Zero IDOR leakage / project isolation:** Derived dataset lookups strictly enforce project ownership, active lifecycle status, approved governance state for governed runs, and physical SHA-256 fingerprint validation before prior output consumption.
- **Multi-segment dimensional identity:** Lookup mechanics resolve multi-segment RILA/FIA prior outputs using canonical dimensional keys (`policy_id`, `projection_month`, `scenario_id`, `coverage_id`, `rider_id`, `account_id`, `segment_id`, `cohort_id`) and reject ambiguous matches.
- **Valuation provenance sealed in RunPackage:** Valuation identity participates in `configuration["valuation"]` inside `run_package_fingerprint`, guaranteeing bit-for-bit reproducibility on rerun and detecting any parameter tampering.
- **Canonical Run ownership & zero drift:** Subordinated `ValuationRunDetail` to canonical `Run`, removing redundant columns and replacing them with property proxies.
- **PostgreSQL immutability hardened:** Trigger loopholes closed so approved/superseded datasets cannot have material fields mutated or be deleted. `valuation_run_details` cannot be deleted and cannot be modified once the parent run completes.
- **Real Dagster orchestration:** Replaced demonstration placeholders with real domain calculations, Polars-backed columnar aggregation, primitive calculations, rollforward validation, reconciliation with tolerance, dynamic SHA-256 manifests, and explicit `allow_retries=False` on deterministic failures.
- **Strict basis & methodology separation:** `SUPPORTED_LAUNCH_BASES = ("GAAP", "STATUTORY")` enforced; `TAX` strictly rejected for Launch 1.0 production runs.
- **Numerical rigor:** Rollforward continuity validated with `Decimal` arithmetic, eliminating IEEE 754 floating-point drift.
- **Gated accounting verticals:** Reference accounting engines for VM-21, VM-22, LDTI, DAC, MRB, and ASC 815 remain cleanly isolated and gated on `ACT-DEC-01` through `ACT-DEC-09`.

---

### 2. Architecture & Design Alignment

#### 2.1 Separation of Basis and Methodology
In strict accordance with CAS §7 and §15.6:
- `basis`: `GAAP`, `STATUTORY`, with `TAX` recognized as an extensible future basis (rejected by default in Launch 1.0 unless `allow_future=True`).
- `methodology`: `VM21`, `VM22`, `LDTI`, `DAC`, `MRB`, `ASC815`.
- Every governed run freezes both concepts independently in `Run` and inside `RunPackage.configuration["valuation"]`.

#### 2.2 Canonical Run Subordination
`ValuationRunDetail` is subordinate to `Run`:
```sql
CREATE TABLE valuation_run_details (
    run_id VARCHAR(36) PRIMARY KEY REFERENCES runs(id) ON DELETE RESTRICT,
    valuation_date DATE NOT NULL,
    discount_curve_fingerprint VARCHAR(64),
    configuration JSON NOT NULL DEFAULT '{}',
    summary_results JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```
All identity fields (`project_id`, `basis`, `methodology`, `methodology_version`, `status`) are accessed through property proxies to `self.run`, eliminating database state divergence.

#### 2.3 Derived Dataset Resolution Security & Dimensions
When the formula engine encounters `kind='prior_output'`, the Variable Resolver delegates to `DerivedDatasetService.lookup_derived_output_value`:
1. Validates that `dataset.project_id == context.project_id` (cross-project returns 404).
2. Verifies `dataset.status == 'approved'` when `governed_execution=True`.
3. Checks that the underlying Parquet artifact SHA-256 matches `dataset.fingerprint`.
4. Filters the dataset across all canonical dimensions.
5. If more than 1 row matches, raises `AmbiguousDerivedOutputError` to prevent silent corruption.

#### 2.4 Dagster Orchestration Pipeline
The 8-stage software-defined asset graph:
`inforce_data_asset` -> `validation_asset` -> `projection_run_asset` -> `aggregation_asset` -> `valuation_asset` -> `rollforward_asset` -> `reconciliation_report_asset` -> `reporting_asset`
- Orchestrates real domain calculations.
- Aggregation uses Polars: flows sum across time steps, stocks take period-end values per CAS §8.
- Deterministic errors (schema validation failure, rollforward discontinuity) raise `Failure(allow_retries=False)`.
- Transient contention retries up to 3 times via `DEFAULT_RETRY_POLICY`.
- Final disclosure package computes actual SHA-256 over canonical JSON of upstream lineage and results.

---

### 3. Automated Test Verification Summary

| Test Suite | Tests Run | Passed | Skipped | Failed | Pass Rate |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Unit Test Suite (`backend/tests/unit`) | 342 | 342 | 0 | 0 | **100%** |
| Integration Test Suite (`backend/tests/integration`) | 221 | 219 | 2 | 0 | **100%** |
| PostgreSQL Governance Suite (`backend/tests/postgres`) | 1 | 1 | 0 | 0 | **100%** |
| **Total Automated Tests** | **564** | **562** | **2** | **0** | **100%** |

*(2 integration tests skipped are opt-in benchmark scenarios per project standards)*

---

### 4. Database Migrations & Immutability Verification (Real PostgreSQL)
Tested against real PostgreSQL 17 (`mentoramp_m5_disposable_test` on `127.0.0.1:5432`):
- Clean schema initialization and upgrade from base to `c9f4e2d8a615`: **SUCCESS**.
- Downgrade from `c9f4e2d8a615` to `b8f3d1e7c504` and re-upgrade to `c9f4e2d8a615`: **SUCCESS**.
- Foreign keys (`source_model_id -> models.id`, `source_model_version_id -> model_versions.id`): **VERIFIED**.
- Immutability trigger on `derived_datasets`:
  - Deleting approved dataset: **BLOCKED**.
  - Mutating approved dataset fingerprint: **BLOCKED**.
  - Exploit updating status to 'superseded' while altering fingerprint: **BLOCKED**.
  - Legitimate supersession (status change only): **ALLOWED**.
  - Deleting or mutating superseded dataset: **BLOCKED**.
  - Reverting superseded status back to approved: **BLOCKED**.
- Immutability trigger on `valuation_run_details`:
  - Deleting details row: **BLOCKED**.
  - Updating details row after parent Run status is completed/success: **BLOCKED**.

---

### 5. Review Artifact Manifest
All required review items are packaged into `mentoramp_m5_senior_review_bundle_v2.zip`:
1. `IMPLEMENTATION_REPORT_V2.md`
2. `SENIOR_REVIEW_CORRECTIONS_V2.md`
3. `git_diff_since_2e6a102.patch`
4. `git_diff_full_m5.patch`
5. `SAMUEL_M5_VALUATION_FRAMEWORKS_DAGSTER_IMPLEMENTATION_README.md`
6. `PROJECT_MASTER_TRACKER.md`
7. `backend/app/db/migrations/versions/c9f4e2d8a615_m5_harden_governance_immutability.py`
8. `backend/app/db/migrations/versions/b8f3d1e7c504_m5_valuation_frameworks_metadata.py`
9. `backend/app/db/models/governance.py`
10. `backend/app/db/models/valuation_detail.py`
11. `backend/app/models/schemas.py`
12. `backend/app/core/valuation/taxonomy.py`
13. `backend/app/core/valuation/rollforward.py`
14. `backend/app/core/valuation/primitives.py`
15. `backend/app/core/variable_registry/resolver.py`
16. `backend/app/core/execution/run_package.py`
17. `backend/app/services/derived_dataset_service.py`
18. `backend/app/services/run_package_service.py`
19. `backend/app/orchestration/dagster/assets.py`
20. `backend/app/orchestration/dagster/definitions.py`
21. `backend/app/orchestration/dagster/jobs.py`
22. `backend/tests/unit/test_derived_dataset_registry.py`
23. `backend/tests/unit/test_run_package_valuation_reproducibility.py`
24. `backend/tests/unit/test_rollforward_continuity.py`
25. `backend/tests/unit/test_valuation_primitives.py`
26. `backend/tests/unit/test_dagster_orchestration.py`
27. `backend/tests/unit/test_basis_methodology_separation.py`
28. `backend/tests/postgres/test_m5_postgres_governance.py`
