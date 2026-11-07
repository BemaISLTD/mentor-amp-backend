# MENTOR AMP — MILESTONE 5 FINAL ENGINEERING CLOSURE REPORT
## Comprehensive Technical Foundation Audit & Release Verification

**Document Version:** 1.0 (Milestone 5 Final Engineering Closure)  
**Date:** November 6, 2026  
**Governed Branch:** `feat/m5-valuation-frameworks-dagster`  
**Governed Baseline Commit:** `1ce25f1fd5f594a7ce21c0dd2cae64868e8b74eb` (`1ce25f1`)  
**Canonical Alembic Head:** `c9f4e2d8a615`  
**Engine Build Identity:** `m3-cpu-dsl-1`  
**Applicable Standards:** CAS, MP, W0R, ITD, Samuel's Controlling M5 Engineering Specification

---

## 1. Executive Summary & Review Scope

Mentor AMP Milestone 5 establishes the enterprise technical foundation for valuation orchestration, projection execution, cash-flow aggregation, rollforward attribution, governed reconciliation, and release approval. Over nine prior independent review cycles, significant progress was achieved in component-level capabilities. However, earlier review cycles identified vulnerabilities at component boundaries—most notably, the risk that downstream release gates might trust stored status strings or caller overrides rather than independently verifying the underlying evidence.

This engineering closure implements a comprehensive, source-to-sink verification of the entire Milestone 5 lifecycle:
$$\text{Governed Data} \longrightarrow \text{Model/Assumption Resolution} \longrightarrow \text{Run Submission} \longrightarrow \text{Frozen RunPackage} \longrightarrow \text{Projection Execution} \longrightarrow \text{Physical Parquet/JSON Artifacts} \longrightarrow \text{Valuation Engine} \longrightarrow \text{Rollforward Attribution} \longrightarrow \text{Governed Reconciliation} \longrightarrow \text{Reporting Manifest} \longrightarrow \text{Release Approval}$$

The final known P0 defect (`V9-P0-01: Incomplete independent rollforward verification`) has been completely resolved through an architectural domain helper (`evaluate_rollforward_evidence`) that independently recomputes arithmetic continuity from authenticated artifact evidence, enforces attempt-scoped lineage, validates governed tolerance policies, reconciles closing balances to persisted valuation reserves, and eliminates fabricated continuity.

All 637 automated tests collected across unit (391 passed in 286.88s), integration (219 passed, 2 skipped in 432.60s), and PostgreSQL (25 passed in 141.91s) suites execute cleanly with 100% pass rate (635 passed, 2 skipped). Targeted evidence suites include 19 rollforward closure tests (in 66.79s), 7 governance hardening tests (in 58.68s), and a clean combined run of 610 passed, 2 skipped.

---

## 2. Source-to-Sink Foundation Audit

### 2.1 Governed Data Ingestion
- **Authoritative Ingestion Source:** Projections ingest data exclusively from governed PostgreSQL `InforceFile` and `InforceRecord` tables managed by Data Manager (`data_manager_service.py`).
- **Identity & Fingerprints:** Every inforce file carries an immutable SHA-256 fingerprint computed across sorted canonical policy attributes.
- **Fail-Closed Boundary:** Projections unconditionally reject missing, corrupted, or unapproved inforce files (`404 INFORCE_FILE_NOT_FOUND`). Default or implicit data fallback is strictly forbidden (`assets.py` Dagster Config classes require explicit string IDs).
- **Tenant Isolation:** All inforce file and policy records are strictly bound to `project_id`. Cross-project access queries return HTTP 404 to eliminate information leakage.

### 2.2 Model, Assumptions & Scenario Governance
- **Authoritative Resolution:** Models and ModelVersions are resolved directly from PostgreSQL `Model` and `ModelVersion` tables (`models.py`, `run_package_service.py`).
- **Four-Eyes Segregation of Duties:** `ModelVersion.status` must be `"approved"` or `"published"`, with non-null `approved_at`, and distinct non-null `approved_by != created_by` (`400 MODEL_VERSION_NOT_APPROVED` / `400 MODEL_VERSION_APPROVAL_INVALID`).
- **Product Code Compatibility:** Model product codes must strictly match the project and inforce file product type (`400 MODEL_PRODUCT_MISMATCH`).
- **Zero Synthetic Identities:** Synthetic test constants (`model-spia-01`, `mv-spia-v1`, `model-default`) are unconditionally rejected by domain services.

### 2.3 RunPackage Integrity & Reproducibility
- **Immutable Freezing:** Valuation run packages are frozen prior to projection execution (`freeze_valuation_run_package`), generating an immutable SHA-256 `package_fingerprint` across canonical JSON representations of model, assumptions, scenarios, policies, and build identities.
- **Database Required:** Freezing requires an active database session (`500 DATABASE_REQUIRED`).
- **Rejection of Untrusted Caller Provenance:** Caller-supplied `base_package_document` is forbidden (`400 UNTRUSTED_PROVENANCE_FORBIDDEN`). Provenance is derived exclusively from committed source `Run` rows, verified `RunPackage` rows, and physical Parquet files (or four-eyes approved `DerivedDataset` records).
- **Reproducibility:** Two runs configured with identical models, assumptions, scenarios, and policies produce bitwise-identical package fingerprints.

### 2.4 Projection Execution & Artifact Storage
- **Shared Projection Engine:** Projections execute through the unified `run_execution_service.execute_run` and `execute_projection_simulation`, ensuring consistent execution semantics across manual API and automated Dagster runs.
- **Physical Parquet Integrity:** Output Parquet files are written to attempt-scoped disk directories (`runs/{project_id}/{run_id}/attempt_{attempt_number}/`). File existence, 64-hex SHA-256 byte checksums, positive row counts, and required schema columns (`policy_id`, `time_step`, `net_cash_flow`, `account_value`) are verified fail-closed.
- **Attempt Tracking & Stale Artifact Fallback Elimination:** Downstream aggregation queries filter strictly on `RunArtifact.attempt_number == source_run.accepted_attempt_number`. Arbitrary `art_query.first()` fallbacks are eliminated.

### 2.5 Valuation Framework Foundations
- **Basis & Methodology Separation:** Statutory, GAAP, and Tax bases are maintained as disjoint domains from methodology specifications (e.g., `VM22`, `AG43`, `LDTI`, `HISTORICAL_AMORTIZED`). Validated via `validate_basis_methodology_pairs`.
- **Valuation Date Binding:** All cash flows, discount factors, and reserves strictly anchor to the explicit `valuation_date` frozen in the run package.
- **No Fabricated Regulatory Results:** Generic present-value discount unwinds are explicitly labeled `ILLUSTRATIVE_GENERIC_PV_NON_REGULATORY` with `regulatory_accounting_asserted=False`. Regulatory accounting results are never fabricated. Reference verticals remain gated under `ACT-DEC-01` through `ACT-DEC-09`.

### 2.6 Rollforward Attribution Engine
- **Authentic Balance Sources:** Opening and closing balances are derived strictly from persisted valuation detail rows and cash-flow outputs.
- **Mathematical Continuity:** Period-to-period continuity requires:
  $$\text{ExpectedClosing} = \text{OpeningBalance} + \sum \text{VerifiedMovements}$$
  $$|\text{ActualClosing} - \text{ExpectedClosing}| \le \text{tolerance}$$
- **Governed Tolerances:** Tolerances are validated against approved policies (`GOVERNED_APPROVED_TOLERANCE_POLICIES`). Caller-specified tolerances are bounded by `max_tolerance = 0.05`. Nonfinite (`NaN`, `Inf`) and negative tolerances are rejected.
- **Elimination of Balancing Residuals:** Balancing residual aliases (`balancing_residual`, `balancing_adjustment`, `reconciliation_discrepancy`, `plug_balance`, `residual_plug`, `plug_residual`) are strictly forbidden (`422 UNEXPLAINED_MOVEMENT_FORBIDDEN`). Unverified movement sources block release approval.

### 2.7 Governed Reconciliation Gate
- **Four-Eyes Approved Benchmarks:** Expected result sets must be approved with four-eyes segregation (`approved_by != created_by`). Unapproved benchmarks or missing sets return `NOT_VERIFIABLE`.
- **Authoritative Actual Values:** Actual valuation metrics are read directly from `ValuationRunDetail.summary_results[target_metric]`. Blind fallback to unrelated keys is forbidden.
- **Multidimensional Scoped Matching:** Multi-row benchmarks must match the valuation run exactly on `(basis, methodology, valuation_date, cohort_id, scenario_id)` unless explicitly designated as `scope_level in ("PORTFOLIO", "AGGREGATE")`.
- **No Reliance on Stored Status:** A stored `PASS` string is never sufficient on its own. The release gate independently validates the underlying report records, checksums, and parameters.

### 2.8 Reporting Manifest & Release Gate
- **Approval vs Materialization:** Successful technical materialization (`TECHNICAL_MATERIALIZATION_UNVERIFIED_NON_APPROVED`) is strictly decoupled from release approval (`GOVERNED_RECONCILED_FOUNDATION_PACKAGE`).
- **Fail-Closed Release Gate:** `execute_reporting_manifest` halts execution (`422 REPORTING_APPROVAL_GATE_FAILED`) or returns a quarantined diagnostic manifest unless:
  1. Canonical valuation run has completed with an accepted attempt.
  2. Frozen `RunPackage` exists with verified fingerprint.
  3. No reconciliation report has failed or errored.
  4. An approved, passing reconciliation report exists for the material reserve metric (`present_value_reserve`).
  5. Rollforward evidence satisfies all 9 verification invariants in `evaluate_rollforward_evidence`.

### 2.9 Dagster Orchestration & PostgreSQL Persistence
- **8 Production Assets:** Inforce import, policy aggregation, baseline projection, scenario projection, cash flow aggregation, rollforward attribution, benchmark reconciliation, and reporting manifest.
- **PostgreSQL Transaction Boundaries:** Real database commits at each asset boundary. Partial persistence failures trigger clean recovery.
- **Idempotent Retry on Same `run_id`:** On transient projection failure, Dagster triggers native retry (`STEP_UP_FOR_RETRY` $\rightarrow$ `STEP_RESTARTED` $\rightarrow$ `STEP_SUCCESS`), resetting `Run.status` to `PENDING` and incrementing to Attempt 2 on the exact same `run_id` with zero duplicate `Run` rows.
- **Deterministic Halt:** Valuation taxonomy errors fail immediately with `allow_retries=False`.

---

## 3. Deep Dive: V9-P0-01 Defect & Complete Architectural Closure

### 3.1 Defect Description & Inherent Vulnerability
The V9 review identified that while the reporting manifest gate authenticated the rollforward artifact and compared information against database records, it trusted the stored `is_continuous: True` boolean from the artifact rather than independently summing the movement components. Furthermore, it did not strictly verify attempt-lineage semantics against `RunAttempt` records, nor did it reconcile the rollforward closing balance with the persisted valuation reserve metric.

This created an inherent vulnerability: an artifact with opening 100, movements +20, and closing 150 that was maliciously or erroneously marked `is_continuous: True` would have been released.

### 3.2 Architectural Correction: `evaluate_rollforward_evidence`
In `backend/app/services/valuation_pipeline_service.py`, a pure domain verification helper `evaluate_rollforward_evidence(db, val_run, val_detail, val_pkg, rollforward_result)` was implemented, enforcing 9 rigorous verification gates:

1. **Terminal Status Check:** Rejects valuation runs with non-terminal status (`PENDING`, `RUNNING`) as well as `FAILED` or `CANCELLED`. Strictly requires `val_run.status in VALID_SUCCESS_STATUSES`.
2. **Authoritative Execution Attempt Contract & Lineage Verification:**
   - Canonical valuation runs in `execute_foundation_valuation` explicitly create and persist a canonical `RunAttempt` row (`attempt_number=1`, `status="success"`).
   - In `evaluate_rollforward_evidence`, the silent fallback `accepted_attempt_number or 1` is removed.
   - When `RunAttempt` records exist: requires non-null `accepted_attempt_number` matching a record whose status is strictly `SUCCESS` or `PARTIAL_SUCCESS`. Superseded attempts are rejected.
   - Direct valuation execution contract (zero `RunAttempt` rows): strictly requires `val_run.run_type == "valuation"`, `val_run.attempt_count <= 1`, terminal success status, and valid `accepted_attempt_number >= 1` (or 1 if omitted for single attempt). If `attempt_count > 1` without attempt records, release is rejected.
   - Non-valuation runs (e.g. projection runs) strictly require `RunAttempt` records and fail closed if absent.
3. **Artifact Attempt Binding:**
   - Queries `RunArtifact` with `run_id == val_run.id` and `artifact_type == "rollforward_summary"`.
   - Strictly filters `art.attempt_number == canonical_attempt_num`, preventing selection of artifacts from superseded, failed, or unaccepted attempts.
4. **Physical Storage & SHA-256 Checksum Integrity:**
   - Verifies the physical file exists on disk via `artifact_storage.get_artifact_path(art)`.
   - Computes raw byte SHA-256 checksum and asserts bitwise match against `art.checksum_sha256`.
5. **Payload Parsing & Binding:**
   - Parses artifact JSON document.
   - Strictly validates `valuation_run_id == val_run.id` and `project_id == val_run.project_id`.
6. **Governed Tolerance Policy Enforcement:**
   - Resolves `tolerance_policy_id` against `GOVERNED_APPROVED_TOLERANCE_POLICIES`.
   - Enforces approved policy parameters (`max_tolerance = 0.05`).
   - Rejects unapproved policy IDs, negative tolerances, nonfinite tolerances (`NaN`, `Inf`), or tolerances exceeding governed limits.
   - Caller cannot override a failed comparison with an arbitrary tolerance.
7. **Independent Arithmetic Continuity Recomputation:**
   - Extracts `opening_balance`, `movements` dictionary, and `actual_closing`.
   - Evaluates each movement: validates keys, forbids residual balancing aliases, requires finite float amounts.
   - Recomputes:
     $$\text{ExpectedClosing} = \text{OpeningBalance} + \sum_{k} \text{movements}[k]$$
     $$\text{Difference} = \text{ActualClosing} - \text{ExpectedClosing}$$
   - Enforces $|\text{Difference}| \le \text{effective\_tolerance}$.
   - Rejects stored status strings.
8. **Valuation Reserve Reconciliation:**
   - Compares rollforward `actual_closing` directly against `val_detail.summary_results.get("present_value_reserve")`.
   - Requires absolute difference $\le 10^{-6}$.
9. **PostgreSQL Evidence Agreement:**
   - Queries `RollforwardSummary` and associated `RollforwardMovement` records from PostgreSQL.
   - Asserts exact agreement across summary ID, opening balance, closing balance, recomputed difference, cohort, basis, methodology, trace identity, and component movement amounts.

---

## 4. Mandated Negative & Adversarial Verification Evidence

The dedicated verification test suite `backend/tests/unit/test_m5_final_rollforward_closure.py` demonstrates 100% rejection across all 15 negative test conditions, plus positive governed release, canonical attempt creation, direct valuation contract verification, and adversarial halt (19 tests total):

| Test Identifier | Test Function Name | Tested Condition | Rejection / Verification Proof | Status |
| :--- | :--- | :--- | :--- | :---: |
| **NEG-01** | `test_negative_01_unbalanced_movements_claimed_pass` | Opening 100, movements +20, closing 150, claimed PASS | Rejected: Recomputed difference = 30.0 exceeds tolerance 0.05 | **PASSED** |
| **NEG-02** | `test_negative_02_closing_balance_mismatches_persisted_reserve` | Closing 135,000 vs persisted reserve 125,000 | Rejected: Rollforward closing disagrees with persisted reserve | **PASSED** |
| **NEG-03** | `test_negative_03_artifact_from_unsuccessful_or_superseded_attempt` | Attempt 1 artifact selected when Attempt 2 is accepted | Rejected: No rollforward artifact found matching accepted attempt | **PASSED** |
| **NEG-04** | `test_negative_04_missing_accepted_attempt_artifact` | Accepted attempt has no registered rollforward artifact | Rejected: No rollforward summary artifact registered for accepted attempt | **PASSED** |
| **NEG-05** | `test_negative_05_manipulated_physical_file` | Physical bytes tampered on disk after generation | Rejected: SHA-256 byte checksum mismatch | **PASSED** |
| **NEG-06** | `test_negative_06_checksum_attached_to_wrong_run_or_summary` | Authentic artifact for Run A attached to Run B | Rejected: Artifact valuation_run_id mismatch | **PASSED** |
| **NEG-07** | `test_negative_07_tampered_component_amounts` | Database movement tampered relative to artifact | Rejected: DB movements disagree with artifact components | **PASSED** |
| **NEG-08** | `test_negative_08_unauthorized_tolerance_policy` | Unapproved policy ID or excessive tolerance (0.50) | Rejected: Unauthorized tolerance policy / tolerance exceeded | **PASSED** |
| **NEG-09** | `test_negative_09_nonfinite_or_forbidden_balancing_movement` | Forbidden `balancing_residual` name and `NaN` values | Rejected: Forbidden residual alias / nonfinite movement | **PASSED** |
| **NEG-10** | `test_negative_10_forged_verified_marker_without_authenticated_evidence` | Forged `VERIFIED` marker with missing DB movement rows | Rejected: Rollforward movement records missing in database | **PASSED** |
| **POS-01** | `test_positive_01_authentic_passing_rollforward_and_governed_release` | Authentic rollforward with exact arithmetic & DB match | Approved: `GOVERNED_RECONCILED_FOUNDATION_PACKAGE` emitted | **PASSED** |
| **ADV-01** | `test_adversarial_reconciliation_failure_halts_release` | Reconciliation failure with `require_approved_reconciliation=True` | Halted: Raises `ServiceError(422, "REPORTING_APPROVAL_GATE_FAILED")` | **PASSED** |
| **NEG-11** | `test_negative_11_pending_run_status_rejected_for_release` | Valuation run in `pending` status submitted for release | Rejected: Run status 'pending' not in valid terminal success | **PASSED** |
| **NEG-12** | `test_negative_12_running_run_status_rejected_for_release` | Valuation run in `running` status submitted for release | Rejected: Run status 'running' not in valid terminal success | **PASSED** |
| **NEG-13** | `test_negative_13_multi_attempt_without_run_attempt_records_rejected` | Multi-attempt run (`attempt_count=2`) lacking attempt records | Rejected: Multi-attempt run requires RunAttempt records | **PASSED** |
| **NEG-14** | `test_negative_14_non_valuation_run_type_without_attempt_records_rejected` | Non-valuation run (`run_type="projection"`) without attempt records | Rejected: Non-valuation run strictly requires RunAttempt records | **PASSED** |
| **NEG-15** | `test_negative_15_invalid_accepted_attempt_number_rejected` | Valuation run with invalid `accepted_attempt_number=0` | Rejected: Invalid accepted attempt number < 1 | **PASSED** |
| **POS-02** | `test_positive_02_direct_valuation_execution_creates_canonical_run_attempt` | `execute_foundation_valuation` persists canonical RunAttempt | Verified: Canonical RunAttempt(attempt_number=1, status='success') created | **PASSED** |
| **POS-03** | `test_positive_03_direct_valuation_contract_single_attempt_without_attempt_records` | Direct valuation contract: single attempt, zero attempt rows | Approved: Verified under single-attempt direct valuation contract | **PASSED** |

---

## 5. Comprehensive Test Execution & Regression Evidence

The entire Milestone 5 foundation has been verified across distinct test suites and dedicated evidence logs:

| Test Suite / Log File | Scope & Invariants Tested | Tests Passed | Tests Skipped | Tests Failed | Execution Duration |
| :--- | :--- | :---: | :---: | :---: | :---: |
| `evidence/unit_tests.log` | Core models, services, DSL matrix, rules, Dagster & rollforward closure | 391 | 0 | 0 | 286.88s (0:04:46) |
| `evidence/integration_tests.log` | API endpoints, multi-tenancy, scenario runs, and ingestion | 219 | 2 | 0 | 432.60s (0:07:12) |
| `evidence/postgres_tests.log` | PostgreSQL triggers, transactions, crash recovery, and 8 Dagster assets | 25 | 0 | 0 | 141.91s (0:02:21) |
| `evidence/governance_hardening_evidence.log` | V7/V8 governance hardening, four-eyes approval, and retry lineage | 7 | 0 | 0 | 58.68s |
| `evidence/rollforward_closure_evidence.log` | Dedicated V9-P0-01 rollforward closure suite (15 negative + 3 positive + 1 adv) | 19 | 0 | 0 | 66.79s (0:01:06) |
| `evidence/combined_tests.log` | Single-invocation full unit and integration test suite | 610 | 2 | 0 | Captured / Clean |
| **Total Non-Redundant Foundation** | **Distinct Test Suite Coverage (Unit + Integration + PostgreSQL)** | **635** | **2** | **0** | **100% Pass** |

*Note 1: The 2 skipped integration tests (`test_spia_product_golden` and `test_external_lifelib_parity`) require external proprietary LifeLib binaries and are intentionally skipped in local CI per `CAS` §17.*  
*Note 2: Targeted sub-suites (`rollforward_closure_evidence.log` and `governance_hardening_evidence.log`) are subsets of `unit_tests.log` and are reported with exact durations without double-counting in the total.*

---

## 6. Gated Reference Verticals Statement

In strict accordance with the Controlling Engineering Specification:
- Reference actuarial verticals for VM-21, VM-22, LDTI, DAC, MRB, and ASC 815 remain **STRICTLY GATED** under formal actuarial decisions `ACT-DEC-01` through `ACT-DEC-09`.
- No claim of full actuarial vertical completion is asserted.
- The technical foundation (orchestration, persistence, aggregation, rollforward arithmetic, reconciliation gate, reporting release) is 100% closed, tested, and ready for Milestone 6 scale paths.
- The working branch remains `feat/m5-valuation-frameworks-dagster`. No merge to `main` has occurred.
