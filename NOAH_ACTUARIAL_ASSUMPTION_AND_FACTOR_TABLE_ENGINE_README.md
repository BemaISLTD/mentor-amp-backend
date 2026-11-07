# NOAH CONTROLLING TASK SPECIFICATION: ACTUARIAL ASSUMPTION & FACTOR TABLE ENGINE
# Central Multidimensional Table Lookup, Reference Mortality Tables, Improvement Scales & Dynamic Behavioral Models

> **Document Type:** Controlling Engineering Task Document derived from CAS/MP/W0R/ITD  
> **Assigned Engineer:** Noah Enemali — Lead Actuarial Engine & Domain Implementation Owner  
> **Architecture & Oversight:** Samuel (Tech Lead / Core Architecture) & Actuarial Product Owner  
> **Controlling Baselines:** CAS §§6, 7.1, 15.4, 15.5; MP §§14.2, 14.5, 14.7; W0R §5, §18; ITD §§6, 7  
> **Governance Status:** DELEGATED INDEPENDENT ENGINEERING ASSIGNMENT — PENDING SENIOR REVIEW OF REVISED SPECIFICATION  
> **Git Tracking Branch:** `feat/assumption-factor-engine` (must branch from clean governed baseline)  
> **Delivery Target:** Standalone module `backend/app/core/assumptions/` + tests + reference fixtures  

---

## 1. DOCUMENT PURPOSE, AUTHORITY & RULES OF ENGAGEMENT

### 1.1 Controlling Purpose & Context
Noah Enemali has submitted the **Milestone 4 (FIA & RILA)** priority product vertical slice, which is currently queued for independent review. Simultaneously, **Milestone 5 (Valuation Frameworks & Dagster Orchestration)** is undergoing independent senior review, and Sam Yung is developing the frontend application screens.

To maintain engineering velocity without building on top of unreviewed code or risking merge conflicts, Noah is assigned to construct the **Central Actuarial Assumption & Factor Table Engine**.

This engine provides a high-performance, multidimensional table lookup, dynamic behavioral multiplier, mortality conversion, and improvement-scale platform. It will serve as the shared assumption spine for all current and future product engines (SPIA, MYGA, FIA, RILA, Variable Annuities, IDI, PRT, and Life).

### 1.2 Governing Authority Hierarchy
> [!IMPORTANT]
> **GOVERNING SPECIFICATION HIERARCHY:**
> This task document is subordinate to the authoritative project governing documents:
> 1. **`CAS`**: *MentorAmp Launch Release 1.0 — Consolidated Architecture and Delivery Specification* (§6 Product Scope, §7.1 Valuation Foundations, §15.4 Product Delivery, §15.5 Life Readiness).
> 2. **`MP`**: *MentorAmp Launch Release 1.0 — Greenfield E2E Weekly SDLC Master Plan v2* (§14.2 Architecture Contracts, §14.5 Valuation Frameworks, §14.7 Remaining Products).
> 3. **`W0R`**: *MentorAmp Week 0 Greenfield Project Initialization and Readiness Plan* (§5 Canonical Data Architecture, §18 Actuarial Decision Gates).
> 4. **`ITD`**: *MentorAmp Week 0 Independent Team Task Document* (§6 Samuel Tech Lead Rules, §7 Noah Engineering Ownership).
>
> If any statement in this task document conflicts with an authoritative source document, **the authoritative source document governs**. This assignment represents a newly delegated engineering task and must not be misrepresented as an already completed or formally accepted milestone.

### 1.3 Strict Separation of Duties & Engineering Isolation
> [!CAUTION]
> **ENGINEERING ISOLATION RULES:**
> 1. **Dedicated Branch:** Noah must work strictly on an isolated branch: `feat/assumption-factor-engine`.
> 2. **No Dependency on Unreviewed Branches:** This assignment must **NOT** require merging Samuel's unapproved Milestone 5 branch (`feat/m5-valuation-frameworks-dagster`) or Noah's unreviewed FIA/RILA branch (`feat/m4-fia-rila-foundation`).
> 3. **Clean Code Boundaries:** All implementation must reside strictly in:
>    * Implementation: `backend/app/core/assumptions/`
>    * Data Fixtures: `backend/app/core/assumptions/fixtures/`
>    * Unit Tests: `backend/tests/unit/test_assumption_table_engine.py`
> 4. **Protected Shared Files (DO NOT TOUCH):**
>    * Do **NOT** modify `backend/app/services/valuation_pipeline_service.py`.
>    * Do **NOT** modify Dagster orchestration files (`backend/app/orchestration/`).
>    * Do **NOT** modify the execution runners (`runner.py`, `run_execution_service.py`).
>    * Do **NOT** create or alter Alembic database migrations.
>    * Do **NOT** modify existing product files (`spia.py`, `myga.py`, or FIA/RILA files).
> 5. **No Guarantee of Zero Merge Conflicts:** Blanket claims of "zero merge conflicts" are forbidden. Noah must establish clean architectural interfaces, document exact dependency boundaries, and conduct an integration-readiness assessment prior to PR submission.

---

## 2. ARCHITECTURAL MOTIVATION & PROBLEM STATEMENT

Mentor AMP's existing computational engines currently rely on simplified mortality approximations or hardcoded scalars in test fixtures (e.g., flat $q_x = 0.010$, static $3.5\%$ surrender charge).

In real-world statutory valuation (VM-20, VM-21, VM-22) and GAAP LDTI (ASU 2018-12), assumptions require:
1. **Multidimensional Actuarial Lookups:** Tables indexed across discrete and continuous dimensions: $(\text{AttainedAge}, \text{Gender})$, $(\text{IssueAge}, \text{Duration}, \text{SmokerStatus})$, or $(\text{Duration}, \text{ProductTier})$.
2. **Mathematically Exact Decrement Conversions:** Clear distinction between **Constant Force of Mortality** and **Uniform Distribution of Deaths (UDD)** for annual-to-monthly decrement derivations.
3. **Rigorous Mortality Improvement Modeling:** Methodological separation between **1D age-based improvement vectors** (SOA Scale G2 for annuities) and **2D age-year improvement matrices** (SOA RPEC MP-2021 / MP-2020 for life and pension).
4. **Dynamic Policyholder Behavior:** Interest-rate sensitive surrender/lapse curves reflecting competitor yield spreads, surrender charge cliffs, and dynamic lapse multipliers.
5. **High-Throughput Vectorization:** Microsecond-latency batch lookups across $10,000$ to $100,000$ policy arrays using NumPy/PyArrow vectorization without slow Python loops.
6. **Immutable Provenance & Governance:** Every assumption table must be versioned, immutable, checksum-verified, and bound to four-eyes approval records before being referenced by a valuation run.

---

## 3. CORE ARCHITECTURAL SUBSYSTEMS & CODE DELIVERABLES

Noah will deliver the standalone module `backend/app/core/assumptions/`:

```text
backend/app/core/assumptions/
├── __init__.py
├── table_engine.py          # Multidimensional table lookup & interpolation engine
├── mortality.py             # Mortality representation, conversion & improvement scales
├── behavior.py              # Dynamic lapse, surrender & behavioral sensitivity models
├── registry.py              # In-memory cached table registry & schema validator
└── fixtures/
    ├── soa_2012_iam_basic.csv       # SOA 2012 IAM Basic Table (unloaded, Male/Female)
    ├── soa_2012_iam_period.csv      # SOA 2012 IAM Period Table (statutory loaded, Male/Female)
    ├── scale_g2_improvement.csv     # SOA Scale G2 1D Mortality Improvement Factors
    ├── mp_2021_sample_matrix.csv    # SOA RPEC MP-2021 2D Improvement Matrix (Sample/Extract)
    ├── soa_2015_vbt_composite.csv   # SOA 2015 VBT RR100 Composite Table
    ├── standard_surrender_tiers.csv # Illustrative 7-year & 10-year surrender charge tiers
    └── us_treasury_spot_curve.json  # Reference Treasury spot curve tenors
```

---

### 3.1 Deliverable 1: Multidimensional Table Lookup & Interpolation Engine (`table_engine.py`)

#### A. Strongly Typed Data Structures
```python
from enum import Enum
from dataclasses import dataclass
from typing import Any, Sequence
import numpy as np

class InterpolationMethod(str, Enum):
    STEP_FLOOR = "STEP_FLOOR"          # Exact step down (e.g., duration 1.0 to 1.99 -> duration 1 rate)
    STEP_NEAREST = "STEP_NEAREST"      # Nearest grid point
    LINEAR = "LINEAR"                  # 1D linear interpolation
    BILINEAR = "BILINEAR"              # 2D bilinear interpolation across continuous axes
    MULTILINEAR = "MULTILINEAR"        # N-dimensional multilinear hypercube interpolation

class BoundaryPolicy(str, Enum):
    FAIL_CLOSED = "FAIL_CLOSED"        # Raise TableOutOfBoundsError if coordinate is outside grid
    CLAMP_TO_BOUNDS = "CLAMP_TO_BOUNDS"# Clamp query coordinate to min/max grid coordinate
    EXTRAPOLATE_FLAT = "EXTRAPOLATE_FLAT"# Hold boundary value constant beyond edge

class DimensionType(str, Enum):
    CATEGORICAL = "CATEGORICAL"        # Exact matching string tokens (e.g., "M", "F", "SMOKER")
    INTEGER = "INTEGER"                # Discrete integer steps (e.g., age 0..120, duration 1..30)
    CONTINUOUS = "CONTINUOUS"          # Floating point continuum (e.g., fractional age, interest rate)
```

#### B. Dimensional Schema & Categorical Strictness
1. **Exact Categorical Matching:**
   * When querying categorical dimensions (`sex`, `smoker_status`, `underwriting_class`), the engine **MUST** match exact recognized tokens:
     * `Sex` / `Gender`: `"MALE"`, `"FEMALE"`, `"UNISEX"` (or canonical short forms `"M"`, `"F"`, `"U"`).
     * `SmokerStatus`: `"SMOKER"`, `"NON_SMOKER"`, `"UNISMOKE"`.
     * `UnderwritingClass`: `"STANDARD"`, `"PREFERRED"`, `"PREFERRED_PLUS"`, `"SUBSTANDARD"`.
   * **No Sloppy Fallbacks:** If a policy provides `"UNKNOWN"` or an invalid token, the engine **MUST fail closed** (`KeyError` or `ValueError`). Never silently default an unknown sex or smoker status.
2. **Missing Data Policy:**
   * If any required coordinate is omitted from the lookup query, the engine **MUST fail closed** (`TableDimensionMissingError`).
   * No table coordinate may map to `NaN` or `None`. Tables with incomplete grids are rejected upon registration.

#### C. Vectorized vs Scalar Equivalence Contract
The engine must expose two execution signatures:
1. `lookup_scalar(**coordinates: Any) -> float`: Evaluates a single policy point.
2. `lookup_vector(**coordinate_arrays: np.ndarray) -> np.ndarray`: Evaluates an array of $N$ policy points simultaneously.
* **Equivalence Invariant:** For any valid coordinate set, `lookup_scalar` and `lookup_vector` **MUST** produce identical results within a numerical tolerance of $|a - b| \le 10^{-12}$.

---

### 3.2 Deliverable 2: Mortality Decrements & Longevity Improvement (`mortality.py`)

#### A. Actuarial Decrement Primitives (Constant Force vs. UDD)
Noah must implement and clearly distinguish both conversion methodologies in code and docstrings:

1. **Constant Force of Mortality (Standard Default):**
   * Assumes constant hazard $\mu_x$ across the annual interval $[x, x+1)$:
     $$\mu_x = -\ln(1 - q_x) = -\ln(p_x)$$
   * For monthly compounding ($m = 12$):
     $$_{1/12}p_x = e^{-\mu_x / 12} = (p_x)^{1/12} = (1 - q_x)^{1/12}$$
     $$q_x^{(12)} = 1 - _{1/12}p_x = 1 - (1 - q_x)^{1/12}$$
   * Under constant force, the monthly decrement rate $q_x^{(12)}$ is constant across all 12 months.

2. **Uniform Distribution of Deaths (UDD):**
   * Assumes deaths are distributed uniformly over the year:
     $$_t q_x = t \cdot q_x \quad \text{for } 0 \le t \le 1$$
   * The conditional mortality rate for month $k \in \{1, 2, \dots, 12\}$ conditional on surviving to month $k$ is:
     $$q_{x + \frac{k-1}{12}}^{(12)} = \frac{\frac{1}{12} q_x}{1 - \frac{k-1}{12} q_x}$$
   * Under UDD, the monthly rate strictly increases each month through the year.

> [!CAUTION]
> **DO NOT CONFUSE UDD WITH CONSTANT FORCE.**
> Document the mathematical derivation of each method in docstrings and provide separate functions:
> `annual_to_monthly_constant_force(qx: float) -> float`
> `annual_to_monthly_udd(qx: float, month_index: int) -> float`

#### B. Methodological Separation: Scale G2 vs. RPEC MP Series
Noah must treat **Scale G2** and **MP-2021** as structurally distinct methodologies:

1. **SOA Scale G2 (1D Vector Projection):**
   * Designed specifically for individual annuities.
   * Defined as a **one-dimensional vector** of improvement factors indexed by age $x$ and sex $s$: $G2(x, s)$.
   * Assumes constant annual improvement over calendar time:
     $$q(x, s, Y) = q(x, s, Y_{\text{base}}) \cdot (1 - G2(x, s))^{\max(0, Y - Y_{\text{base}})}$$

2. **SOA RPEC MP Series / MP-2021 (2D Matrix Projection):**
   * Designed for US pension and life insurance valuations.
   * Defined as a **two-dimensional matrix** indexed by attained age $x$, sex $s$, and calendar year $y$: $MP(x, s, y)$.
   * Factors vary by both age and calendar year to capture cohort dynamics and long-term trend convergence:
     $$q(x, s, Y) = q(x, s, Y_{\text{base}}) \cdot \prod_{y = Y_{\text{base}} + 1}^{Y} (1 - MP(x, s, y))$$

#### C. Terminal Age Safeguard ($\omega$)
* For all attained ages $x \ge \omega$ (where $\omega = 120$ for 2012 IAM and 2015 VBT):
  $$q_x = 1.0, \quad p_x = 0.0$$
* The engine must enforce this boundary unconditionally, preventing negative survivorship or division-by-zero errors in life annuity factors.

---

### 3.3 Deliverable 3: Illustrative Behavioral & Dynamic Multiplier Models (`behavior.py`)

> [!WARNING]
> **ILLUSTRATIVE STATUS NOTICE:**
> All behavioral curves, surrender tiers, and lapse formulas in this section are **PROPOSED ILLUSTRATIVE MODELS FOR SYSTEM ARCHITECTURE VALIDATION**. They must NOT be presented or documented as regulatory, NAIC-approved, or industry-standard numbers until formally signed off by the Actuarial Product Owner under `ACT-DEC-01`.

#### A. Baseline Surrender & Shock Lapse Schedule
* A duration-dependent baseline lapse structure:
  * Low early-year lapse during the surrender charge period (e.g., $1.5\% - 3.0\%$).
  * **Cliff Expiration Shock:** An illustrative spike in lapse rate occurring at the exact policy anniversary when surrender charges expire (e.g., Year 7 or Year 10 jumping to an illustrative $15\% - 25\%$).
  * Graded post-surrender ultimate lapse rate (e.g., $6\% - 8\%$).

#### B. Dynamic Interest Rate Lapse Multiplier
An illustrative model scaling lapse rates based on competitor rate differentials:
$$\text{Spread}(t) = \max\left(0.0, r_{\text{market}}(t) - r_{\text{contract}}(t) - \text{Buffer}\right)$$
$$\text{Multiplier}(t) = 1.0 + \alpha \cdot \left(\frac{\text{Spread}(t)}{\text{Threshold}}\right)^\beta$$
$$\text{EffectiveLapse}(t) = \min(\text{MaxLapseRate}, \text{BaseLapse}(t) \cdot \text{Multiplier}(t))$$

Where:
* $r_{\text{market}}(t)$: External benchmark rate (e.g., 5-year Treasury yield).
* $r_{\text{contract}}(t)$: Policy credited rate or minimum guaranteed interest rate.
* $\text{Buffer}$: Inactive differential threshold (e.g., illustrative 50 bps).
* $\alpha, \beta$: Illustrative sensitivity coefficients (e.g., $\alpha = 1.5, \beta = 1.2$).
* $\text{MaxLapseRate}$: Governed ceiling (e.g., illustrative $50\%$).

---

### 3.4 Deliverable 4: Reference Table Registry & Data Licensing (`registry.py` & `fixtures/`)

#### A. Documented Reference Tables
For every published table included in `fixtures/`, Noah must document:
1. **Full Name & Authoritative Source:** (e.g., Society of Actuaries, NAIC Valuation Manual).
2. **Table Version & Variant:** (e.g., Basic Unloaded vs Period Loaded).
3. **Applicable Dimensions:** (e.g., `age` [0..120], `gender` ["M", "F"]).
4. **Licensing & Usage Constraints:** Public domain actuarial research; commercial redistribution notices.
5. **Physical Checksum (SHA-256):** Must match the raw fixture file bytes.

| Table File | Authoritative Source & Variant | Dimensions | Licensing / Terms | SHA-256 Fingerprint |
| :--- | :--- | :--- | :--- | :--- |
| `soa_2012_iam_basic.csv` | SOA 2012 Individual Annuity Reserving Table (Basic Table 1A/1B, Unloaded) | `age` (0..120), `sex` ("M", "F") | SOA Public Actuarial Research | To be calculated from authoritative CSV |
| `soa_2012_iam_period.csv` | SOA 2012 Individual Annuity Mortality Period Table (Statutory Margin Loaded) | `age` (0..120), `sex` ("M", "F") | NAIC Model Regulation #821 / SOA | To be calculated from authoritative CSV |
| `scale_g2_improvement.csv` | SOA Scale G2 Mortality Improvement Rates (Table 2 of 2012 IAM Report) | `age` (0..120), `sex` ("M", "F") | SOA Public Actuarial Research | To be calculated from authoritative CSV |
| `mp_2021_sample_matrix.csv` | SOA RPEC MP-2021 Mortality Improvement Matrix (Ages 20..100, Years 2010..2025 extract) | `age` (20..100), `year` (2010..2025), `sex` ("M", "F") | SOA RPEC Public Tool | To be calculated from authoritative CSV |
| `soa_2015_vbt_composite.csv` | SOA 2015 Valuation Basic Table (RR100 Composite, Male/Female) | `age` (18..90), `duration` (1..25), `sex` ("M", "F") | SOA Public Mortality Research | To be calculated from authoritative CSV |
| `standard_surrender_tiers.csv` | *Illustrative Development Schedule* (7-Yr & 10-Yr Tiers) | `duration` (1..15), `tier` ("7_YR", "10_YR") | Proprietary Development Fixture | Generated and documented |
| `us_treasury_spot_curve.json` | *Illustrative Spot Yield Curve* (1M to 30Y tenors) | `tenor` ("1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y") | US Department of Treasury Public Data | Recorded from published curve |

#### B. Pending Dependencies & Synthetic Fixtures Rule
> [!IMPORTANT]
> **NO FABRICATION OF OFFICIAL DATA:**
> If an authoritative SOA CSV file cannot be obtained with verified provenance, Noah must:
> 1. Label the fixture explicitly as synthetic (e.g., `synthetic_sample_soa_2012_iam.csv`).
> 2. Document the item in the **Unresolved Actuarial Dependencies Register** (`ACT-DEP-TABLE-01`).
> 3. Never invent reference values or assert that a fabricated number is published SOA data.

---

### 3.5 Deliverable 5: Assumption Governance & Immutability Envelope

Every registered `ActuarialTable` must be encapsulated in an immutable governance metadata envelope:

```python
@dataclass(frozen=True)
class TableGovernanceEnvelope:
    table_id: str                      # e.g., "soa-2012-iam-basic-v1"
    version_label: str                 # e.g., "1.0.0"
    source_reference: str              # e.g., "SOA 2012 IAM Report, Table 1A/1B"
    effective_date: str                # e.g., "2026-01-01"
    status: str                        # "draft", "validated", "approved"
    created_by: str                    # Non-null user ID
    approved_by: str | None            # Four-eyes approver (approved_by != created_by)
    approved_at: str | None            # ISO-8601 timestamp
    fingerprint_sha256: str            # Cryptographic SHA-256 of coordinates + data array
```

#### Forward Architectural Compatibility:
* **RunPackage Alignment:** When an assumption table is referenced in a future valuation run, its `table_id`, `version_label`, and `fingerprint_sha256` will bind into the `RunPackage` configuration envelope without requiring schema alterations.
* **Variable Registry Alignment:** Product formulas in the DSL will invoke table lookups through standard registered variable calls (e.g. `lookup_mortality(age, sex, table_id="soa-2012-iam-basic-v1")`).

---

## 4. ACCEPTANCE CRITERIA & VERIFICATION TEST SUITE

Noah must deliver a comprehensive test suite at `backend/tests/unit/test_assumption_table_engine.py`. Tests must pass 100% and satisfy the following acceptance gates:

### 4.1 Verified Reference-Value Tests
* **SOA 2012 IAM Verification:** If using official SOA fixtures, assert exact match against published primary sources at sample control points:
  * E.g., Table 1A (Male Basic, 2012): Verify exact published values at ages 30, 50, 65, 80, 95.
  * E.g., Table 1B (Female Basic, 2012): Verify exact published values at ages 30, 50, 65, 80, 95.
* If synthetic development fixtures are used, verify internal consistency against documented synthetic equations.

### 4.2 Scalar vs. Vectorized Equivalence Tests
* Assert that `lookup_scalar` and `lookup_vector` produce identical results within $|a - b| \le 10^{-12}$ across 1,000 randomly generated query coordinate pairs.

### 4.3 Multidimensional Interpolation Tests
* Test linear interpolation between grid tenors (e.g. spot curve between 1-Year and 2-Year yields).
* Test 2D bilinear interpolation across continuous fractional ages and durations.
* Test step-floor behavior for integer policy duration schedules.

### 4.4 Boundary & Missing-Data Negative Tests
* Assert query coordinate outside table bounds raises `TableOutOfBoundsError` under `BoundaryPolicy.FAIL_CLOSED`.
* Assert `BoundaryPolicy.CLAMP_TO_BOUNDS` holds boundary value constant.
* Assert missing coordinate raises `TableDimensionMissingError`.
* Assert unrecognized categorical value (e.g., `sex="INVALID"`) raises `ValueError`.
* Assert attained age $\ge \omega$ enforces $q_x = 1.0, p_x = 0.0$.

### 4.5 Decrement & Improvement Methodology Tests
* Assert `annual_to_monthly_constant_force(qx)` matches $1 - (1 - q_x)^{1/12}$.
* Assert `annual_to_monthly_udd(qx, month)` produces month-varying rates matching the UDD formula.
* Assert Scale G2 1D projection applies $q_x \cdot (1 - G2_x)^t$.
* Assert RPEC MP-2021 2D projection applies $\prod (1 - MP(x, y))$.

### 4.6 Immutability & Four-Eyes Governance Tests
* Assert modifying table array in-place raises `ValueError` (read-only buffer).
* Assert self-approved governance envelope (`approved_by == created_by`) is rejected.
* Assert table tampering invalidates `fingerprint_sha256`.

### 4.7 Repeatable Performance Benchmark (Documented Hardware)
* Execute a vectorized lookup benchmark across $100,000$ policy points.
* Log execution time, memory usage, and hardware specifications (CPU model, RAM, OS).
* **Important:** Performance numbers must be documented as factual timing logs; they do NOT substitute for numerical and actuarial correctness proof.

### 4.8 Full Regression Invariant
* Executing `pytest backend/tests/unit` must pass **372 existing unit tests + all new assumption tests** with zero failures and zero errors.

---

## 5. REQUIREMENTS TRACEABILITY MATRIX

| Requirement ID | Authoritative Document Reference | Specification Section | Implementation Responsibility |
| :--- | :--- | :--- | :--- |
| **REQ-ASSUMP-01** | `CAS` §6, §7.1; `W0R` §5 | Section 3.1: Table Engine | Multidimensional 1D/2D/3D table lookup & interpolation |
| **REQ-ASSUMP-02** | `CAS` §7.1; `W0R` §18 | Section 3.2: Mortality & Decrements | Constant force vs UDD decrement conversions |
| **REQ-ASSUMP-03** | `CAS` §6; `MP` §14.5 | Section 3.2: Longevity Improvement | Scale G2 (1D) and RPEC MP-2021 (2D) methodologies |
| **REQ-ASSUMP-04** | `CAS` §6, §7.2; `W0R` §18 | Section 3.3: Dynamic Behavior | Illustrative interest-rate sensitive lapse multipliers |
| **REQ-ASSUMP-05** | `CAS` §8, §15.11; `W0R` §5 | Section 3.4, 3.5: Governance & Provenance | Immutable envelopes, SHA-256 fingerprints, 4-eyes approval |
| **REQ-ASSUMP-06** | `ITD` §6, §7 | Section 1.3: Separation of Duties | Independent branch execution; zero shared-file regression |

---

## 6. UNRESOLVED ACTUARIAL & DATA-SOURCE DEPENDENCIES REGISTER

The following dependencies are documented and must be resolved before final production model lock:

| Dependency ID | Dependency Title | Description & Impact | Resolution Path | Owner |
| :--- | :--- | :--- | :--- | :--- |
| **ACT-DEP-TABLE-01** | SOA Official CSV Acquisition | Full official CSV files for 2012 IAM and 2015 VBT require licensing and verified source curation. Synthetic development fixtures are permitted in the interim. | Acquire and verify official SOA tables from soa.org with documented checksums. | Actuarial Product Owner |
| **ACT-DEP-TABLE-02** | VM-20 / VM-22 Prescribed Tables | Confirmation of whether statutory valuation requires specific loaded tables (e.g. 2012 IAM Period Table) or company experience blends. | Formally document under `ACT-DEC-04`. | Actuarial Product Owner |
| **ACT-DEP-TABLE-03** | Production Dynamic Lapse Calibration | Calibration of sensitivity parameters ($\alpha, \beta, \text{Buffer}, \text{Threshold}$) for fixed and indexed annuity dynamic lapses. | Empirical industry calibration study before Phase 9 stress testing. | Actuarial Product Owner |

---

## 7. NOAH'S HANDOFF & EXECUTION CHECKLIST

Noah will execute this assignment according to the following phased plan:

1. **Step 1: Branch Setup**
   * Create and switch to branch `feat/assumption-factor-engine` from a clean baseline.
2. **Step 2: Table Data Structures & Interpolation (`table_engine.py`)**
   * Build `ActuarialTable`, dimensions, interpolation methods, and boundary policies.
   * Verify scalar and vectorized lookups yield bitwise equivalence.
3. **Step 3: Decrements & Mortality Improvement (`mortality.py`)**
   * Implement constant-force and UDD conversions.
   * Implement Scale G2 (1D) and MP-2021 (2D) projection models with terminal age $\omega$ enforcement.
4. **Step 4: Dynamic Behavioral Models (`behavior.py`)**
   * Implement illustrative duration lapse curves and interest-rate sensitivity functions.
5. **Step 5: Reference Fixtures & Registry (`registry.py`, `fixtures/`)**
   * Package documented reference fixtures (or labeled synthetic development fixtures) with SHA-256 checksums.
   * Implement immutable in-memory registry.
6. **Step 6: Unit Test Suite & Performance Benchmark (`test_assumption_table_engine.py`)**
   * Implement all 8 acceptance test categories; run regression suite.
7. **Step 7: Packaging & Delivery**
   * Produce Git patch, test execution log, and review summary notes. Stop for independent senior review.
