# Milestone 7 Implementation Evidence

## Scope and classification

This branch implements the Phase 7 IDI, VA, PRT, LIFE-readiness, and governed
reporting work package from the approved `origin/main` baseline
`efb9657b084dff980d65326c5c244bfd043bd695`.

- Implementation commits on `feat/m7-products-life-reporting`:
  - `7943f059c64036fb5fe262c9a23a173c286aa922` (initial Phase 7 implementation)
  - `7028c12e70ee35d0378dae2c3a9e31ad0201bc0a` (initial evidence documentation)
  - `57510d97c24cacb2589a4c412f06bc97f313303e` (first hardening pass — reviewed)
  - Post-review focused correction pass (this commit — exact SHA recorded in `00_REVIEW_MANIFEST.md`)

### Post-Review Focused Corrections Applied (Review Sections 1–5)

1. **Report Version & Artifact Identity (`governance_service.py`, `reporting_service.py`, `api/governance.py`):**
   - `version_versioned()` copies reusable report configuration (`definition`) while stripping version-bound `"generated"` metadata and clearing `artifact_uri` / `artifact_fingerprint`.
   - Client-supplied `"generated"` definition keys are rejected (`422 RESERVED_DEFINITION_KEY`).
   - `reporting_service.payload()` and `export_csv()` validate report identity (`report_id`) and `report_version` binding; ungenerated versions return `409 REPORT_NOT_GENERATED` and never leak prior-version CSV artifacts.
   - `publish_versioned()` rejects publishing an ungenerated `Report` (`409 REPORT_NOT_GENERATED`) and rejects repeated publication of an already-published version (`409 ALREADY_PUBLISHED`), preserving immutability.
2. **IDI Incident Claim Duration Cohort Handling (`shared_modules/disability.py`, `idi_lite/config.py`):**
   - Selected illustrative convention (pending `ACT-P7-DEC-01` & `ACT-P7-DEC-02`): existing disabled lives are tracked explicitly by individual claim duration (`{duration_months: probability}`) in `disabled_cohorts_open` / `disabled_cohorts_surviving` / `disabled_cohorts_close`.
   - Mutually exclusive non-double-counting treatment for newly incident claims:
     - **Option A (`cohort_runoff`, `new_claim_dlr_factor == 0.0`):** new incident claims enter `disabled_cohorts_close` at duration `0` and pay duration-gated monthly benefits in subsequent months (`elimination_months < duration <= elimination_months + max_benefit_months`). Combining `cohort_runoff` with positive `new_claim_dlr_factor` raises `ValueError`.
     - **Option B (`dlr_incurred`, `new_claim_dlr_factor > 0.0`):** new incident claims are valued at incidence via `new_disabled * monthly_benefit * new_claim_dlr_factor` and are NOT added to benefit-paying `disabled_cohorts_close`, while pre-existing disabled cohorts run off by exact duration.
   - Controlled 4-month counterexample (`p_active_open=1.0`, `q_inc_m=0.1`, zero other decrements, `elimination=0`, `max_benefit=1`, `monthly_benefit=1000`, `dlr=0`):
     - **Before correction (pooled scalar duration):** `[0.0, 100.0, 0.0, 0.0]` (month 3 wrongly dropped to `0.0`).
     - **After correction (exact duration cohorts):** `[0.0, 100.0, 90.0, 81.0]` (`reserve_0 = 271.0`).
3. **VA Survival Exposure Consistency (`va_lite/config.py`, `reporting_service.py`):**
   - Selected illustrative cash-flow timing convention (pending `ACT-P7-DEC-03/04`, `ACT-DEC-01/02`): account-level quantities (`account_value_*`, `total_fees_collected`, `benefit_base_*`, `rider_claim_payment`, `nar_death_claim`) remain conditional per-surviving-policy values; expected insurer cash flows are consistently survival-weighted by `survival_open(t)`:
     - `expected_fees_collected = total_fees_collected * survival_open`
     - `rider_claims_incurred = rider_claim_payment * survival_open`
     - `death_claims_incurred = nar_death_claim * survival_open * q_mort_m`
     - `net_cash_flow = rider_claims_incurred + death_claims_incurred - expected_fees_collected`
   - Counterexample (100% mortality in Month 1, positive fees 2.4%/yr, no withdrawals):
     - **Before correction:** Months 2–3 still recognized positive conditional `total_fees_collected` as insurer income in `net_cash_flow` despite `survival_open = 0.0`.
     - **After correction:** Months 2–3 produce `expected_fees_collected = 0.0`, `rider_claims_incurred = 0.0`, `death_claims_incurred = 0.0`, `net_cash_flow = 0.0`.
4. **Report Completeness & Rollforward Continuity Controls (`reporting_service.py`):**
   - Records `source_run_status`, `accepted_attempt_number`, `policy_error_count`, `policy_warning_count`, `is_complete`, `completeness_status`, and `completeness_notice` in report metadata and CSV exports (`X-MentorAmp-Complete` header).
   - `partial_success` reports are explicitly marked `INCOMPLETE_PARTIAL_RUN` / `PARTIAL_INCOMPLETE` and are never presented as unqualified complete financial disclosures.
   - Persisted rollforward continuity is independently verified before declaring `AVAILABLE`: failed/tampered continuity returns `CONTINUITY_FAILED`, unverified stored flags return `NOT_VERIFIABLE`, and `asu_2018_12_disclosure_supporting` refuses `AVAILABLE` (and never substitutes zeros) whenever continuity fails or is unverified.
5. **LIFE Nested Coverage/Rider Validation (`inforce_registry.py`):**
   - Hardened optional nested `coverages` and `riders` list entries to validate dict structure, distinct non-empty IDs/codes, finite positive face amounts/limits, valid nested plan types and TERM coverage years, and finite non-negative rider premiums (zero DB/Alembic schema changes).

All model inputs, examples, and numerical anchors delivered here are synthetic
engineering fixtures. They are not production-approved actuarial assumptions,
accounting statements, or owner-signed reference models.

- Production approval: `false`
- Reporting classification: `DISCLOSURE_SUPPORTING_INFRASTRUCTURE_NON_FINAL`
- Actuarial decision status: `ACT-DEC-01..09` and `ACT-P7-DEC-01..07` remain open
- Owner-signed IDI/VA/PRT oracle tests remain explicitly skipped

## Delivered components

- Pure shared primitives for disability, variable-annuity, and PRT benefits.
- Shared-engine vertical slices for IDI, VA, and PRT; all are registered through
  the existing product and inforce registries.
- Canonical Data Manager schemas for IDI, VA, PRT, and LIFE, including LIFE
  premium-frequency fidelity and multi-coverage/rider validation.
- LIFE v1/v2 immutable retention and four-eyes approval evidence without a DB
  model or Alembic change. Full LIFE actuarial calculations remain excluded.
- Governed report materialization from checksum-verified accepted Parquet
  artifacts with honest unavailable states, non-final flags, immutable
  publication, CSV export, and audit events.
- Synthetic samples and isolated `seed_phase7.py` / `review_phase7.py` scripts.
- Unit, engineering-anchor, and isolated end-to-end tests.

## Numerical engineering anchors

- IDI elimination, maximum-benefit, waiver-of-premium, and decrement behavior is
  tested independently in the shared-module and golden-policy suites.
- VA explicitly distinguishes finite GMWB from lifetime GLWB and validates all
  three supported fee-conversion methods.
- PRT life-only month-one expected benefit is independently reconciled as:

  `1000 * 0.98 ** (1 / 12)`

  The isolated review runner requires the exact value to return `PASS` at
  `abs_tol=1e-6`, and the same value plus `$1.00` to return `FAIL`.

## Reproducible evidence runner

From `backend/`:

```powershell
python -m scripts.review_phase7 --output ../.phase7-review
```

The runner creates a temporary SQLite database, executes all IDI/VA/PRT
scenario runs, verifies frozen RunPackages and physical Parquet checksums,
builds trace trees, performs PASS/FAIL reconciliation, confirms LIFE v1/v2
four-eyes evidence, and generates, reads, exports, publishes, and versions a
governed report. It writes `phase7-evidence.json` under the selected output
directory and never connects to the configured application database.

## Database and protected-area proof

- Canonical Alembic head remains `c9f4e2d8a615`.
- No migration or ORM schema files are modified.
- No Phase 6 orchestration, PySpark/GPU, CI workflow, frontend, or existing
  SPIA/MYGA/FIA/RILA product baseline file is modified.
- Shared-file changes are additive registry, normalization, CORS/header, and
  Data Manager validation hooks required by the approved work package.
- Dagster is pinned in `backend/requirements.txt` because the integrated M5
  graph is imported by the normal application test/runtime path.

## Merge Convergence Assessment vs Phase 6 (`origin/feat/m6-scale-execution-integration`)

A non-destructive `git merge-tree --write-tree` check against Samuel's Phase 6 branch (`origin/feat/m6-scale-execution-integration` at `f380f51ca55e5bca6dc1721bd76f2aaf63a97136`) confirms zero unintended modifications to Phase 6 files and identifies exactly **4 overlapping files** requiring Technical Lead coordination at merge time:

1. `backend/app/products/registry.py` — Phase 6 added `PROSPECTIVE_RESERVE_DISCOUNTING` to `REGISTERED_INTERNAL_REFERENCES` (kept separate from commercial `REGISTERED_PRODUCTS`); Phase 7 added `IDI`, `VA`, `PRT` to `REGISTERED_PRODUCTS`. Resolution: retain both cleanly.
2. `backend/app/services/run_submission_service.py` — Phase 6 added execution-backend planning (`execution_backend`, `fallback_policy`, `execution_options`); Phase 7 added the `LIFE` `MODEL_NOT_CONFIGURED` execution guard. Resolution: preserve both additive sections.
3. `backend/tests/integration/test_products_api.py` — Phase 6 asserts internal reference separation; Phase 7 asserts 7 launch products registered. Resolution: combine both assertions.
4. `backend/tests/unit/test_indexed_inforce.py` — Minor additive product-count/registry assertions from both branches. Resolution: align expected registered product set.

No Phase 6 code or branch was modified.

## Verification record

Final verification commands (executed in isolated worktree after all review corrections):

```powershell
python -m pytest -v tests/unit/test_phase7_reporting_disclosures.py tests/unit/test_phase7_shared_modules.py tests/unit/test_phase7_inforce_schemas.py tests/unit/test_phase7_seed.py tests/golden_policy/test_phase7_idi_golden.py tests/golden_policy/test_phase7_va_prt_golden.py tests/integration/test_phase7_data_manager_workflow.py tests/integration/test_phase7_e2e_workflow.py tests/integration/test_actuarial_governance.py
python -m scripts.review_phase7 --output <evidence-dir>
python -m pytest -q tests
python -m pytest -v tests/postgres tests/integration/test_phase7_e2e_workflow.py
```

Exact post-correction verification results:

- Targeted Phase 7 & governance regression suite (including all new defect regressions & counterexamples): **`65 passed, 3 skipped, 0 failed`** in `57.30s` (3 skips are strictly the owner-approved IDI, VA, and PRT golden workbook placeholders).
- Isolated Phase 7 E2E evidence runner (`scripts.review_phase7`): **PASS** (`phase7-evidence.json` generated cleanly).
- Full SQLite regression suite (`python -m pytest -q tests`): **`808 passed, 32 skipped, 0 failed, 1 warning`** in `756.28s` (zero regressions to SPIA, MYGA, FIA, RILA, or M0–M5).
- Disposable PostgreSQL 18 governance/migration suite plus Phase 7 E2E (`mentoramp_phase7_disposable_test`, removed after verification): **`26 passed, 0 failed, 1 warning`** in `103.61s`. Canonical Alembic head verified: **`c9f4e2d8a615 (head)`** (zero new migrations).
- Hosted GitHub Actions CI status on `feat/m7-products-life-reporting`: **NOT RUN (`0` runs)** — this branch is based on `origin/main` (`efb9657`), which predates `.github/workflows/` (introduced on Phase 6 `feat/m6-scale-execution-integration`). Hosted CI success is not claimed for this branch.

The 32 skips on SQLite consist of 25 PostgreSQL-gated tests (`MENTORAMP_TEST_POSTGRES_URL` — all 25 executed and passed in the disposable PostgreSQL 18 run above), 3 owner-signed IDI/VA/PRT actuarial golden placeholders, and 4 other explicitly gated/environment-specific tests. None is claimed as an owner-approved production actuarial model; `ACT-DEC-01..09`, `ACT-P7-DEC-01..07`, and `CAS` Section 17 remain **OPEN**.
