# MentorAmp Launch Release 1.0 — Backend Convergence & Integration Review Report

**Date:** November 10, 2026  
**Branch:** `feat/backend-convergence-apis-integration`  
**Base Revision:** `origin/main` (`ffe3948` — PR #9 M5 Valuation Frameworks & Dagster Orchestration)  
**Latest Convergence Commit:** `21a0234`  
**Alembic Migration Head:** `c9f4e2d8a615` (Verified canonical head)  
**Test Suite Status:** 104 Passed, 2 Skipped, 0 Failures (100% Pass Rate across unit, API, integration, and M5 rollforward suites)

---

## 1. Executive Summary & Accomplishments

This convergence release integrates all pending backend components from Noah's recent feature branches, resolves merge boundaries with the merged M5 baseline, exposes all necessary REST APIs for frontend consumption by Sam (`BemaISLTD/mentor-amp`), and establishes full automated regression verification.

### Key Milestones Completed:
1. **Noah's Recent Commits Merged & Safeguarded:**
   - `origin/feat/m4-fia-rila-foundation` at commit `b6701d2`: Post-withdrawal fixed-account crediting safeguards merged into main baseline (`d444958`).
   - `origin/feat/assumption-factor-engine` at commit `0e7d046`: Central Actuarial Assumption & Factor Table Engine merged (`08017c8`).
2. **Central Actuarial Assumption & Factor Table Engine REST APIs (`/v1/assumptions/...`):**
   - Implemented typed FastAPI endpoints in `backend/app/api/assumptions.py` backed by `backend/app/services/assumptions_service.py`.
   - Exposes multi-dimensional grid lookups, Scale G2 & MP mortality projections, annual-to-monthly decrement conversions (Constant Force & UDD), spot/par yield curve discounting and interpolation, and dynamic behavioral lapse schedules.
3. **M5 Governance & Reporting Promotion to `/v1`:**
   - Mounted `experimental_governance_router` directly under `/v1` so that Sam's frontend screens can consume `/v1/projects/{project_id}/rollforward-templates`, `/v1/projects/{project_id}/rollforward-jobs`, `/v1/projects/{project_id}/reports`, and `/v1/projects/{project_id}/derived-datasets` without getting 404s.
4. **Run Valuation Detail & Rollforward Attribution Endpoints:**
   - Implemented `GET /v1/runs/{run_id}/valuation-detail` to query `ValuationRunDetail` reserve balances and parameters.
   - Implemented `GET /v1/runs/{run_id}/rollforward` to query unbroken continuity rollforward items and mathematical verification status from `RollforwardSummary`.
5. **Comprehensive Automated Test Coverage:**
   - Added `backend/tests/unit/test_assumptions_api.py` (16 tests covering all assumption endpoints).
   - Added `backend/tests/unit/test_valuation_rollforward_api.py` (5 tests covering valuation detail, rollforward schedules, and governance routes).
   - Re-verified Noah's 41 engine unit tests (`test_assumption_table_engine.py`) and M4 test suite.

---

## 2. Integrated Commit Lineage

```
* 21a0234 feat(api): expose central assumption engine, rollforward, and valuation detail REST endpoints
* 08017c8 feat(assumptions): merge Noah central assumption engine into main
|\  
| * 0e7d046 fix: harden assumption engine safeguards
| * 867acc0 Central assumption & factor table engine, mortality, lapse/surrender
* | d444958 feat(m4): merge Noah M4 FIA/RILA indexed crediting foundation into main
|\| 
| * b6701d2 fix: validate fixed-account withdrawals after crediting
| * 999147b M4 FIA & RILA indexed crediting modules, shared option budget, buffer/floor
* | ffe3948 Merge pull request #9 from BemaISLTD/feat/m5-valuation-frameworks-dagster
```

---

## 3. API Surface Reference for Sam (Frontend Integration)

### A. Assumption & Factor Table APIs (`/v1/assumptions`)
| HTTP Method | Route | Description |
|---|---|---|
| `GET` | `/v1/assumptions/tables` | List all registered factor tables with metadata, shape, dimensions, fingerprints. |
| `GET` | `/v1/projects/{project_id}/assumptions/tables` | Project-scoped factor table list. |
| `GET` | `/v1/assumptions/tables/{table_id}` | Full table schema, dimensions, ranges, and sample grid preview. |
| `POST` | `/v1/assumptions/tables/{table_id}/lookup` | Multi-dimensional coordinate lookup with interpolation & boundary overrides. |
| `GET` | `/v1/assumptions/mortality/tables` | List registered mortality tables and improvement factors. |
| `POST` | `/v1/assumptions/mortality/project` | Project mortality rate using Scale G2 or MP matrix (fixed age or generational). |
| `POST` | `/v1/assumptions/mortality/monthly-decrement` | Convert annual qx into monthly decrement (Constant Force or UDD). |
| `GET` | `/v1/assumptions/curves` | List yield curves with tenors, rates, and compounding metadata. |
| `GET` | `/v1/assumptions/curves/{curve_id}` | Detailed curve points and discount factor curve. |
| `POST` | `/v1/assumptions/curves/{curve_id}/discount-factor` | Compute discount factor and spot rate for a specified tenor. |
| `POST` | `/v1/assumptions/behavior/baseline-lapse` | Evaluate duration-based baseline lapse rate using surrender tiers. |
| `POST` | `/v1/assumptions/behavior/evaluate` | Calculate effective lapse rate given competitor rate spread and sensitivity. |

### B. Valuation & Rollforward APIs
| HTTP Method | Route | Description |
|---|---|---|
| `GET` | `/v1/runs/{run_id}/valuation-detail` | Retrieve valuation reserve balances, headline metrics, and configuration. |
| `GET` | `/v1/runs/{run_id}/rollforward` | Retrieve ordered rollforward movement items with continuity verification. |
| `GET` | `/v1/projects/{project_id}/rollforward-templates` | List rollforward schedule templates for a project. |
| `GET` | `/v1/projects/{project_id}/rollforward-jobs` | List rollforward job executions for a project. |
| `GET` | `/v1/projects/{project_id}/reports` | List generated valuation and reconciliation reports. |
| `GET` | `/v1/projects/{project_id}/derived-datasets` | List derived valuation and cash flow datasets. |

---

## 4. Verification Evidence & Test Run Log

All tests executed in local Python 3.14.2 environment against PostgreSQL / SQLite test harnesses:
- **`backend/tests/unit/test_assumption_table_engine.py`**: 41 passed
- **`backend/tests/unit/test_assumptions_api.py`**: 16 passed
- **`backend/tests/unit/test_valuation_rollforward_api.py`**: 5 passed
- **`backend/tests/unit/test_shared_modules.py`**: 31 passed
- **`backend/tests/unit/test_indexed_inforce.py`**: 5 passed
- **`backend/tests/golden_policy/test_fia_golden.py`**: 2 passed, 1 skipped
- **`backend/tests/golden_policy/test_rila_golden.py`**: 4 passed, 1 skipped
- **Total:** 104 passed, 2 skipped, 0 failed.

---

## 5. Review Package Contents

The generated review zip archive contains:
1. `git_diff_from_main.patch`: Comprehensive patch against `origin/main` (`ffe3948`).
2. `git_diff_latest_commit.patch`: Focused patch of the API convergence commit (`21a0234`).
3. `git_log.txt`: Formatted git commit history and diff statistics.
4. `git_status.txt`: Clean working tree confirmation.
5. `test_results.txt`: Raw pytest execution stdout log.
6. `BACKEND_CONVERGENCE_REVIEW_REPORT.md`: This review document.
7. `backend/app/api/assumptions.py`: Assumption REST router implementation.
8. `backend/app/services/assumptions_service.py`: Central assumption registry service.
9. `backend/tests/unit/test_assumptions_api.py`: Automated tests for assumption APIs.
10. `backend/tests/unit/test_valuation_rollforward_api.py`: Automated tests for valuation and rollforward APIs.
