# NOAH CONTROLLING TASK SPECIFICATION: ACTUARIAL ASSUMPTION & FACTOR TABLE ENGINE
# Central Multidimensional Table Lookup, SOA Reference Tables, Mortality Scales & Dynamic Behavioral Multipliers

> **Document Type:** Controlling Engineering Task Document derived from CAS/MP/W0R/ITD  
> **Assigned Engineer:** Noah Enemali — Lead Actuarial Engine & Domain Implementation Owner  
> **Architecture & Oversight:** Antigravity (MentorAmp Core Architecture) & Actuarial Product Owner  
> **Controlling Baselines:** CAS §§6, 7.1, 15.4, 15.5; MP §§14.2, 14.5, 14.7; W0R §5, §18; ITD §§6, 7  
> **Status:** APPROVED FOR IMMEDIATE ENGINEERING EXECUTION  
> **Git Tracking Branch:** `feat/assumption-factor-engine` (branch from clean `main` or latest clean commit)  
> **Delivery Target:** Standalone module `backend/app/core/assumptions/` + tests + reference fixtures  

---

## 1. DOCUMENT PURPOSE, AUTHORITY & RULES OF ENGAGEMENT

### 1.1 Controlling Purpose & Current Context
Noah has completed and submitted the **Milestone 4 (FIA & RILA)** priority product vertical slice, which is currently queued for independent review. Simultaneously, **Milestone 5 (Valuation Frameworks & Dagster Orchestration)** is undergoing independent senior review, and Sam is actively developing the frontend screens.

To maintain engineering momentum without risking merge conflicts or building on top of unreviewed code, Noah is assigned to construct the **Central Actuarial Assumption & Factor Table Engine**.

This engine is a foundational, cross-cutting actuarial system that provides high-performance multidimensional table lookups, dynamic behavioral multipliers, standard mortality improvement scales, and industry-standard reference tables for all present and future product engines (SPIA, MYGA, FIA, RILA, VA, IDI, PRT, and Life).

### 1.2 Governing Authority Hierarchy
> [!IMPORTANT]
> **THIS SPECIFICATION DERIVES STRICTLY FROM THE FOUR GOVERNING AUTHORITIES:**
> 1. **`CAS`**: *MentorAmp Launch Release 1.0 — Consolidated Architecture and Delivery Specification* (§6, §7.1, §15.4, §15.5).
> 2. **`MP`**: *MentorAmp Launch Release 1.0 — Greenfield E2E Weekly SDLC Master Plan v2* (§14.2, §14.5, §14.7).
> 3. **`W0R`**: *MentorAmp Week 0 Greenfield Project Initialization and Readiness Plan* (§5, §18).
> 4. **`ITD`**: *MentorAmp Week 0 Independent Team Task Document* (§6, §7).
>
> If any statement in this task specification conflicts with an authoritative source document, **the authoritative source document wins**.

### 1.3 Strict Isolation & Zero-Conflict Architecture Invariant
> [!CAUTION]
> **STRICT ISOLATION RULES — PREVENTING MERGE CONFLICTS:**
> 1. **Brand-New Isolated Module:** All new code must reside exclusively in:
>    * Implementation: `backend/app/core/assumptions/`
>    * Standard Data Fixtures: `backend/app/core/assumptions/fixtures/`
>    * Test Suite: `backend/tests/unit/test_assumption_table_engine.py`
> 2. **DO NOT TOUCH EXISTING RUNNERS OR VALUATION SERVICES:**
>    * Do **NOT** modify `backend/app/services/valuation_pipeline_service.py`.
>    * Do **NOT** modify `backend/app/orchestration/dagster/`.
>    * Do **NOT** modify existing product files (`spia.py`, `myga.py`, or your submitted FIA/RILA branch).
> 3. **BRANCH DISCIPLINE:** Work exclusively on branch `feat/assumption-factor-engine`. Branch from `main` (or latest clean baseline). It must merge cleanly into `main` without depending on M4 or M5 PRs.

---

## 2. PROBLEM STATEMENT & ARCHITECTURAL MOTIVATION

Currently, existing engine slices (SPIA, MYGA, FIA, RILA) utilize simplified mortality vectors or hardcoded scalar assumptions in test fixtures (e.g., $q_x = 0.010$, flat $3.5\%$ surrender charge). 

However, real-world statutory and GAAP actuarial valuation (`CAS` §6, §7; `W0R` §5) demands:
1. **Multidimensional Actuarial Lookups:** Tables indexed by $(\text{Attained Age}, \text{Gender})$, $(\text{Issue Age}, \text{Duration}, \text{Underwriting Class})$, or $(\text{Duration}, \text{Product Tier})$.
2. **Dynamic Mortality Improvement:** Applying two-dimensional improvement scales ($MP\text{-}2021$ / Scale G2) where $q_x(t) = q_x(0) \cdot \prod_{s=1}^t (1 - f_x(s))$.
3. **Dynamic Policyholder Behavior:** Interest-rate sensitive surrender/lapse curves where lapses increase when market rates exceed credited contract rates.
4. **Vectorized High-Throughput Inner Loops:** Lookups must execute across arrays of 10,000 to 100,000 policies in milliseconds using NumPy/PyArrow, avoiding slow Python nested loops.

---

## 3. CORE DELIVERABLES SPECIFICATION

Noah will construct the module `backend/app/core/assumptions/` containing the following five major subsystems:

```text
backend/app/core/assumptions/
├── __init__.py
├── table_engine.py          # Multidimensional table lookup & interpolation engine
├── mortality.py             # Mortality representation, conversion & improvement scales
├── behavior.py              # Dynamic lapse, surrender & behavioral sensitivity models
├── registry.py              # In-memory cached table registry & schema validator
└── fixtures/
    ├── soa_2012_iam_period.csv      # 2012 Individual Annuity Mortality Table
    ├── soa_2015_vbt_unismoke.csv    # 2015 Valuation Basic Table (Relative Risk 100)
    ├── scale_g2_improvement.csv     # Scale G2 Mortality Improvement Factors
    ├── standard_surrender_tiers.csv # 7-year & 10-year surrender charge schedules
    └── us_treasury_spot_curve.json  # Reference Treasury spot curve tenors
```

---

### 3.1 Deliverable 1: Multidimensional Table Engine (`table_engine.py`)

#### A. Data Model & Data Structures
Implement strongly typed immutable structures for actuarial tables:

```python
from enum import Enum
from dataclasses import dataclass
from typing import Any, Sequence
import numpy as np

class InterpolationMethod(str, Enum):
    STEP_FLOOR = "STEP_FLOOR"          # Exact step down (e.g. durations 1.0 to 1.99 = duration 1 value)
    STEP_NEAREST = "STEP_NEAREST"      # Round to nearest integer grid point
    LINEAR = "LINEAR"                  # 1D linear interpolation between points
    BILINEAR = "BILINEAR"              # 2D bilinear interpolation across (x, y)
    CLAMP = "CLAMP"                    # Hold boundary constant outside grid

class BoundaryPolicy(str, Enum):
    FAIL_CLOSED = "FAIL_CLOSED"        # Raise ValueError if query point is outside table bounds
    CLAMP_TO_BOUNDS = "CLAMP_TO_BOUNDS"# Clamp query point to min/max grid coordinate
    ZERO_FILL = "ZERO_FILL"            # Return 0.0 outside bounds
```

#### B. `ActuarialTable` Class
The table must support 1D, 2D, and 3D coordinate grids:
* **1D Table:** e.g., $\text{Duration} \to \text{SurrenderChargeRate}$, $\text{Tenor} \to \text{SpotRate}$.
* **2D Table:** e.g., $(\text{AttainedAge}, \text{Gender}) \to q_x$, $(\text{Duration}, \text{PolicyYear}) \to \text{LapseRate}$.
* **3D Table:** e.g., $(\text{IssueAge}, \text{Duration}, \text{SmokerStatus}) \to q_x$.

#### C. Required Methods:
```python
class ActuarialTable:
    def __init__(
        self,
        table_id: str,
        name: str,
        dimensions: list[str],          # e.g., ["age", "gender"] or ["duration"]
        grid_coordinates: dict[str, Sequence[Any]], # e.g., {"age": [0..120], "gender": ["M", "F"]}
        values: np.ndarray,             # N-dimensional float numpy array
        interpolation: InterpolationMethod = InterpolationMethod.STEP_FLOOR,
        boundary_policy: BoundaryPolicy = BoundaryPolicy.FAIL_CLOSED,
        metadata: dict[str, Any] | None = None,
    ):
        ...

    def lookup_scalar(self, **coordinates: Any) -> float:
        """Single policy point lookup with boundary and interpolation handling."""
        ...

    def lookup_vector(self, **coordinates_vectors: np.ndarray) -> np.ndarray:
        """High-performance vectorized batch lookup across NumPy arrays.
        Example: lookup_vector(age=np.array([45, 62, 70]), gender=np.array(['M', 'F', 'M']))
        Returns: np.ndarray of shape (3,) with extracted rates.
        """
        ...
```

---

### 3.2 Deliverable 2: Mortality & Longevity Improvement Engine (`mortality.py`)

#### A. Actuarial Decrement Primitives
Implement mathematically exact decrement conversion formulas:
1. **Annual $q_x$ to Monthly $q_x^{(12)}$:**
   Under uniform distribution of deaths (UDD) or constant force of mortality:
   $$q_x^{(12)} = 1 - (1 - q_x)^{1/12}$$
2. **Force of Mortality ($\mu_x$):**
   $$\mu_x = -\ln(1 - q_x)$$
3. **Survival Probability:**
   $$p_x = 1 - q_x, \quad p_x^{(12)} = (1 - q_x)^{1/12}$$

#### B. Mortality Improvement Projection
Support actuarial improvement scales (e.g. Scale G2, MP-2021):
Given base mortality $q_x(0)$ at base year $Y_0$, projection year $Y_t$, and annual improvement factor $f_x$:
$$q_x(t) = q_x(0) \cdot (1 - f_x)^{\max(0, Y_t - Y_0)}$$

#### C. Terminal Age Safeguard
* Must strictly enforce terminal omega $\omega$:
  For all attained ages $x \ge \omega$ (typically $\omega = 120$ or $121$):
  $$q_x = 1.0, \quad p_x = 0.0$$
* Prevents infinite projection loops or negative survivorship.

---

### 3.3 Deliverable 3: Dynamic Behavioral Multipliers (`behavior.py`)

Implement dynamic policyholder surrender/lapse and withdrawal behaviors (`CAS` §6, §7):

#### A. Base Lapse & Shock Lapse Curve
A duration-dependent baseline lapse structure:
* Low surrender during early surrender charge years (e.g., $1.5\% - 3\%$).
* **Surrender Expiration Shock:** A sharp spike in lapse rate at the exact month/year the surrender charge cliff expires (e.g., Year 7 or Year 10 jumping to $15\% - 25\%$).
* Graded ultimate lapse rate in post-surrender years (e.g., $6\% - 8\%$).

#### B. Dynamic Interest Rate Lapse Multiplier
Actuarial formula adjusting lapse rate based on competitor interest rate differentials:
$$\text{Differential}(t) = \max\left(0.0, r_{\text{market}}(t) - r_{\text{contract}}(t) - \text{Buffer}\right)$$
$$\text{LapseMultiplier}(t) = 1.0 + \alpha \cdot \left(\frac{\text{Differential}(t)}{\text{Threshold}}\right)^\beta$$
$$\text{EffectiveLapse}(t) = \min(\text{MaxLapseRate}, \text{BaseLapse}(t) \cdot \text{LapseMultiplier}(t))$$

Where:
* $r_{\text{market}}(t)$: Current benchmark yield (e.g. 5-year Treasury or competitor credited rate).
* $r_{\text{contract}}(t)$: Actual credited rate or minimum guaranteed rate on the policy.
* $\text{Buffer}$: Inactive differential threshold (e.g., $0.005$ or 50 bps).
* $\alpha, \beta$: Actuarial sensitivity parameters (e.g., $\alpha = 1.5, \beta = 1.2$).
* $\text{MaxLapseRate}$: Governed ceiling (e.g., $0.50$ or $50\%$).

---

### 3.4 Deliverable 4: Standard SOA Reference Fixtures (`fixtures/`)

Noah will package the following verified, industry-standard CSV/JSON reference tables with recorded SHA-256 fingerprints:

1. **`soa_2012_iam_period.csv`:**
   * Source: Society of Actuaries 2012 Individual Annuity Mortality Period Table.
   * Columns: `age` (0 to 120), `male_qx`, `female_qx`.
2. **`scale_g2_improvement.csv`:**
   * Source: SOA Scale G2 Mortality Improvement Rates.
   * Columns: `age` (0 to 120), `male_fx`, `female_fx`.
3. **`soa_2015_vbt_unismoke.csv`:**
   * Source: SOA 2015 Valuation Basic Table (Relative Risk 100, Composite).
   * Columns: `issue_age`, `duration`, `male_qx`, `female_qx`.
4. **`standard_surrender_tiers.csv`:**
   * 7-Year Schedule: Year 1–7 = `[0.07, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01, 0.00]`.
   * 10-Year Schedule: Year 1–10 = `[0.10, 0.09, 0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01, 0.00]`.
5. **`us_treasury_spot_curve.json`:**
   * Reference tenors: `["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]` with annual spot yields.

---

### 3.5 Deliverable 5: In-Memory Cache & Registry (`registry.py`)

Implement `AssumptionRegistry` to manage table loading, validation, and zero-copy in-memory caching:
* `register_table(table: ActuarialTable) -> None`
* `load_table_from_csv(file_path: str, table_id: str, ...) -> ActuarialTable`
* `get_table(table_id: str) -> ActuarialTable`
* `has_table(table_id: str) -> bool`
* **Immutability Guard:** Once registered, an `ActuarialTable` is frozen and cannot be mutated in place.

---

## 4. ACCEPTANCE CRITERIA & TEST SPECIFICATION

Noah must deliver a dedicated unit test suite at `backend/tests/unit/test_assumption_table_engine.py` verifying:

1. **Exact Benchmark Matching:**
   * Assert lookup values on `soa_2012_iam_period.csv` match published SOA values at key sample test ages (e.g., Male Age 65 $q_x = 0.009419$, Female Age 65 $q_x = 0.006240$).
2. **Mortality Scale Projection Test:**
   * Assert $q_x(10)$ projected 10 years with Scale G2 accurately applies $q_x \cdot (1 - f_x)^{10}$.
3. **Terminal Omega Test:**
   * Assert age $\ge 120$ yields $q_x = 1.0$ and $p_x = 0.0$ without overflow or infinite recursion.
4. **Vectorized Performance Test:**
   * Benchmark batch lookup of 100,000 policies across $(\text{Age}, \text{Gender})$: must execute in under **50 milliseconds** using NumPy vectorized lookups.
5. **Dynamic Behavior Sensitivity Test:**
   * Test dynamic lapse formula: when $r_{\text{market}} = r_{\text{contract}}$, multiplier is exactly $1.0$; when $r_{\text{market}} > r_{\text{contract}} + \text{buffer}$, multiplier scales up smoothly and clamps at $\text{MaxLapseRate}$.
6. **Boundary Fail-Closed Rejection:**
   * Negative durations or negative ages raise `ValueError` under `BoundaryPolicy.FAIL_CLOSED`.
7. **Zero Regression on Full Suite:**
   * Running `pytest backend/tests/unit` must pass 100% of existing tests + all new assumption tests.

---

## 5. REPOSITORY HANDOFF & REVIEW CHECKLIST

When complete, Noah will produce:
1. All source code in `backend/app/core/assumptions/`.
2. Standard reference fixtures in `backend/app/core/assumptions/fixtures/`.
3. Unit test suite `backend/tests/unit/test_assumption_table_engine.py` (all tests passing).
4. Clean Git branch `feat/assumption-factor-engine` with zero untracked files.
5. Summary review notes documenting table lookup throughput, benchmark verification, and formulas implemented.

---

> [!NOTE]
> **NEXT INTEGRATION STEP (POST-MERGE):**
> Once Milestone 5 and FIA/RILA are merged into `main`, this assumption engine will be bound to the Variable Registry and Projection Engine so that any product formula can write:
> `qx = lookup_mortality(age, gender, table="SOA_2012_IAM", scale="G2")`
> directly in the DSL!
