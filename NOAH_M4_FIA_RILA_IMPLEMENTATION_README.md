# NOAH CONTROLLING TASK SPECIFICATION: MILESTONE 4 (WEEK 4)
# Shared Indexed Crediting Foundation, FIA & RILA Priority Product Verticals, and Representative RILA Block

> **Document Type:** Controlling Engineering Task Document derived from CAS/MP/W0R/ITD — pending Project Owner approval of reference-product actuarial decisions  
> **Assigned Engineer:** Noah Enemali — Full-stack / backend implementation owner for M4  
> **Architecture & Oversight:** Antigravity (MentorAmp Core Architecture) & Actuarial Product Owner  
> **Controlling Baseline:** Milestone 4 / SDLC Master Plan Week 4  
> **Status:** PENDING PROJECT OWNER APPROVAL OF REFERENCE-PRODUCT ACTUARIAL DECISIONS  
> **Target Date:** Greenfield Launch SDLC Week 4  
> **Git Tracking Branch:** `feat/m4-fia-rila-foundation`  

---

## 1. DOCUMENT PURPOSE, AUTHORITY & RULES OF ENGAGEMENT

### 1.1 Controlling Purpose & Status
This document is the **controlling engineering task document** for Noah Enemali for **MentorAmp Milestone 4 / Week 4**. It specifies the software architecture, modular calculation primitives, variable bindings, database metadata, API contracts, security boundaries, Parquet analytical storage, test harnesses, and handoff criteria required to construct:
1. The **Shared Indexed Crediting & Segment Accounting Foundation** (`backend/app/products/shared_modules/`).
2. The **Fixed Indexed Annuity (FIA)** priority product vertical slice (`backend/app/products/fia_lite/`).
3. The **Registered Index-Linked Annuity (RILA)** priority product vertical slice (`backend/app/products/rila_lite/`).
4. The **Representative Multi-Segment RILA Block** execution harness with policy-level outputs, aggregate summaries, cell-level trace, Parquet persistence, and reconciliation (`CAS` §15.7).

> [!IMPORTANT]
> **THIS DOCUMENT IS NOT AN AUTHORITATIVE ACTUARIAL SOURCE.**
> The authoritative project requirements remain exclusively the governing project documents:
> - **CAS** — *Consolidated Architecture and Delivery Specification*
> - **MP** — *Greenfield E2E Weekly SDLC Master Plan v2*
> - **W0R** — *Week 0 Project Initialization and Readiness Plan*
> - **ITD** — *Week 0 Independent Team Task Document*
>
> If any statement in this task specification or in referenced external research conflicts with an authoritative source document, **the authoritative source wins**. This document provides the engineering structure and candidate configurations; specific reference-product actuarial designs remain subject to Project Owner approval.

### 1.2 Strict Separation of Duties (Option 1 vs Option 2)
> [!WARNING]
> **DO NOT TOUCH OPTION 1.**
> Option 1 (`MYGA-100001` sample/reference CSV work, inforce data curation, and reconciliation baseline) is being handled **personally and exclusively by the Project Owner (Samuel)**.
> Noah is assigned **Option 2 (Milestone 4 / Week 4)** only. Noah must **NOT** modify, overwrite, or mix any MYGA sample reference CSV tasks into this milestone.

### 1.3 Scope Classification Taxonomy
To eliminate ambiguity between governing requirements, candidate reference designs, and external market research, every capability and feature in this specification is categorized under one of three strict labels:

1. `AUTHORITATIVE MINIMUM CAPABILITY`: Mandated directly by `CAS`, `MP`, `W0R`, or `ITD`. Must be supported by the engine architecture.
2. `PROPOSED REFERENCE CONFIGURATION — OWNER APPROVAL REQUIRED`: A specific concrete design proposal (parameters, strategy types, tables, schedules) submitted to the Project Owner / Actuarial Product Owner for selection and sign-off.
3. `RESEARCH-BACKED / FUTURE VARIANT`: Derived from external industry practice, NAIC guidelines, SEC filings, or academic sources. Advisory only; must not enter production models without formal owner approval.

### 1.4 The Shared Calculation Spine Invariant (Anti-Silo Architecture Rule)
> [!CAUTION]
> **NO STANDALONE OR SILO ENGINES PERMITTED.**
> Product logic must **NEVER** be buried inside FastAPI endpoints, UI components, ad-hoc scripts, Dagster ops, or standalone calculation functions that bypass the core projection engine.
> Every product calculation for FIA and RILA **MUST** be implemented as:
> `Registered Formulas` $\to$ `Variable Resolver` $\to$ `Topological Dependency DAG` $\to$ `Projection Engine` $\to$ `Parquet Artifact Storage` $\to$ `Trace Engine` $\to$ `Reconciliation Service`.
> Both FIA and RILA must execute through `app.core.projection_engine.engine.run_projection()` using `RunData` contracts identical to MYGA and SPIA.

### 1.5 Role Definition & Engineering Ownership
* **Noah Enemali (Full-stack / Backend Implementation Owner):** Responsible for building modular shared calculation primitives, configuring product variable and formula bindings, wiring Data Manager schemas, implementing analytical Parquet persistence, building the representative block harness, passing all unit and integration tests, and packaging the final review artifact.
* **Actuarial Product Owner:** Exclusively responsible for formula intent, reference-product design selections, statutory/regulatory interpretation, independent expected-result generation, tolerance profiles, and final actuarial acceptance. Noah must not invent final actuarial values.

---

## 2. EXECUTION GATE: WHAT NOAH CAN START NOW VS PENDING DECISIONS

To ensure forward velocity without stalled development, Noah is authorized to immediately implement all generic engineering infrastructure. Contract-specific actuarial decisions will be reviewed and signed off in parallel.

```
                    ┌────────────────────────────────────────────────────────┐
                    │               PHASE 4 WORKFLOW GATING                  │
                    └────────────────────────────────────────────────────────┘
                                                │
         ┌──────────────────────────────────────┴──────────────────────────────────────┐
         ▼                                                                             ▼
┌─────────────────────────────────┐                                   ┌─────────────────────────────────┐
│     WHAT NOAH CAN START NOW     │                                   │     REQUIRES OWNER DECISION     │
│  (Generic / Non-Semantic Infra) │                                   │     BEFORE FORMULA LOCK         │
├─────────────────────────────────┤                                   ├─────────────────────────────────┤
│ • Segment state structures      │                                   │ • Selected FIA strategy         │
│ • Cap / PR / Buffer primitives  │                                   │ • Exact option-budget method    │
│ • Variable metadata support     │                                   │ • Model 805 reference case      │
│ • Formula/Resolver scaffolding  │                                   │ • Surrender / MVA mechanics     │
│ • Scenario lookup interfaces    │                                   │ • Death benefit rule            │
│ • Factor table infrastructure   │                                   │ • Selected RILA terms/indices   │
│ • Parquet schema extensions     │                                   │ • Interim-value methodology     │
│ • RunPackage identity fields    │                                   │ • Active withdrawal rule        │
│ • Security & IDOR test harness  │                                   │ • Rollover / reinvestment rule  │
│ • Synthetic block generator     │                                   │ • Observation & fee timing      │
│ • Backward-compatibility suite  │                                   │ • Actuarial reconciliation tols │
└─────────────────────────────────┘                                   └─────────────────────────────────┘
         │                                                                             │
         ▼                                                                             ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ EXIT GATE: Engineering infrastructure verified; reference runs await independent expected oracles    │
└──────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 2.1 What Noah Can Start Immediately
Noah may immediately implement the following generic, non-semantic components:
- Reusable segment identity, allocation, and state tracking structures (`shared_modules/segment.py`).
- Pure mathematical primitives for capped returns, participation rates, spreads, buffers, and floors (`shared_modules/indexed_crediting.py`, `buffer_floor.py`).
- Fixed interest compounding primitives (`shared_modules/fixed_crediting.py`).
- Configurable partial withdrawal adjustment primitives supporting both pro-rata and dollar-for-dollar modes (`shared_modules/withdrawal.py`).
- Inforce schema definitions and column aliases in `inforce_registry.py`.
- Factor-table lookup infrastructure and range-matching helpers (`shared_modules/option_budget.py`).
- Variable Registry and Resolver definitions for FIA and RILA dimensions.
- Parquet schema extensions for multi-segment output partitioning.
- RunPackage manifest extensions for frozen scenario and factor-table fingerprints.
- Synthetic representative-block generator harness (`rila_representative_block_inforce.csv`).
- Comprehensive unit tests verifying pure mathematical primitives using explicit, hand-calculated illustrative numbers.
- Backward-compatibility tests proving SPIA and MYGA runs remain 100% green.

### 2.2 What Requires Owner Decision Before Formula Lock
Noah must design generic interfaces with configurable switches around the following decisions, but must **NOT** hardcode final actuarial values without formal approval:
- The selected reference strategy for FIA (Annual PTP vs Spread vs Averaging).
- The exact option-budget derivation and factor table contents.
- The applicable NAIC Model 805 statutory interest rate and nonforfeiture formula version.
- Specific surrender charge schedules, MVA multipliers, and free withdrawal percentages.
- The primary RILA interim-value methodology (AG 54 hypothetical portfolio vs prorated intrinsic).
- Active-segment partial withdrawal reduction basis (pro-rata vs dollar-for-dollar).
- Segment maturity rollover behavior (auto-renew vs sweep to fixed).
- Index observation date convention and fee/claim deduction order.
- Governing actuarial reconciliation tolerances.

---

## 3. AUTHORITATIVE REQUIREMENTS TRACEABILITY MATRIX (MILESTONE 4)

| Requirement ID | Authoritative Source | Scope Category | Requirement Description | Noah's Implementation Deliverable |
| :--- | :---: | :---: | :--- | :--- |
| **AUTH-CAS-06-FIA** | `CAS` §6, Table p.6 | `AUTHORITATIVE MINIMUM CAPABILITY` | **FIA Product Scope:** Fixed and indexed allocations, strategies, caps, participation rates, spreads, option-budget or factor inputs, indexed crediting, account value, withdrawals, fees, benefits, and cash flows. | `backend/app/products/fia_lite/config.py`, shared crediting and option-budget primitives |
| **AUTH-CAS-06-RILA** | `CAS` §6, Table p.6 | `AUTHORITATIVE MINIMUM CAPABILITY` | **RILA Product Scope:** Segments, terms, indices, participation rates, buffers, floors, interim and maturity crediting, account value, withdrawals, fees, benefits, scenario paths, and full-block execution. | `backend/app/products/rila_lite/config.py`, shared buffer/floor and segment primitives |
| **AUTH-CAS-06-MOD** | `CAS` §6.1 | `AUTHORITATIVE MINIMUM CAPABILITY` | **Product Modularity Rule:** Products must reuse shared modules for mortality, decrements, survival, account value, segment accounting, crediting, interest, fees, expenses, premiums, withdrawals, claims, benefits, riders, present values, discount curves, aggregation, and reporting. Do not create separate engines. | Modular primitives in `backend/app/products/shared_modules/` |
| **AUTH-CAS-15-004** | `CAS` §15, Item 4 | `AUTHORITATIVE MINIMUM CAPABILITY` | **Reference Execution:** SPIA, MYGA, FIA, and RILA each complete an agreed end-to-end reference run using the shared engine. | Golden reference run tests for FIA and RILA |
| **AUTH-CAS-15-007** | `CAS` §15, Item 7 | `AUTHORITATIVE MINIMUM CAPABILITY` | **Representative RILA Block:** A representative RILA block executes with policy-level and aggregate outputs and supports trace to a selected policy, scenario, time, and formula. | Sized multi-policy, multi-segment RILA block execution harness |
| **AUTH-MP-14-W4** | `MP` §14.4 | `AUTHORITATIVE MINIMUM CAPABILITY` | **Week 4 SDLC Milestone:** Technical lead, backend, Full-Stack, Actuarial Owner, and QA responsibilities; weekly exit gate criteria. | Milestone 4 execution package & test evidence |
| **AUTH-MP-06-MOD** | `MP` §6 | `AUTHORITATIVE MINIMUM CAPABILITY` | **Shared Product Modules:** Account value and segment accounting, crediting and interest, fees and expenses, premiums, withdrawals, claims, benefits. | Shared primitives in `shared_modules/` |
| **AUTH-W0R-DATA** | `W0R` §8, §9 | `AUTHORITATIVE MINIMUM CAPABILITY` | **Canonical Data Model:** Segment, account, cohort, scenario set, economic period, cash-flow categorization, and trace identifier. | Dimension support in inforce schema & run data |
| **AUTH-ITD-ACT** | `ITD` §9 | `AUTHORITATIVE MINIMUM CAPABILITY` | **Actuarial Deliverables:** FIA allocation/strategy/cap/spread/option budget cases; RILA policy/segment/buffer/floor/interim crediting cases. | Actuarial test cases and decision documentation |

---

## 4. AUDIT & CATEGORIZATION OF EXISTING CODEBASE ASSETS (MP §15.1)

In accordance with SDLC Master Plan §15.1 (*Existing Implementation Comparison*), every file in the target directories has been individually evaluated:

### 4.1 Shared Modules Directory (`backend/app/products/shared_modules/`)

| File Name | Current Size | MP §15.1 Decision | Rationale & Noah's Action |
| :--- | :---: | :---: | :--- |
| `indexed_crediting.py` | 0 bytes | **BUILD** | **Required for M4.** Implement reusable mathematical primitives for point-to-point (cap, PR, spread) and averaging index crediting. |
| `buffer_floor.py` | 0 bytes | **BUILD** | **Required for M4.** Implement reusable mathematical primitives for buffer and floor downside payoffs. |
| `segment.py` | 0 bytes | **BUILD** | **Required for M4.** Implement generic multi-segment tracking, term maturity detection, and active segment state rollforward. |
| `option_budget.py` | 0 bytes | **BUILD** | **Required for M4.** Implement option budget factor table lookup and derivation primitives. |
| `fixed_crediting.py` | 0 bytes | **BUILD** | **Required for M4.** Implement compound monthly fixed interest crediting primitive reusable by FIA and RILA fixed strategies. |
| `account_value.py` | 0 bytes | **BUILD** | **Required for M4.** Implement multi-bucket account value aggregation (Fixed Account + Indexed Segments). |
| `withdrawal.py` | 0 bytes | **BUILD** | **Required for M4.** Implement configurable partial withdrawal adjustments (supporting pro-rata and dollar-for-dollar modes). |
| `surrender_charge.py` | 0 bytes | **BUILD** | **Required for M4.** Implement schedule-based surrender charge rate lookup and basic MVA factor lookup primitives. |
| `death_benefit.py` | 0 bytes | **DEFER** | **Not a separate M4 module.** Death benefit formulas for FIA/RILA reference models are simple policy cash-flow mappings configured directly in `config.py`. |
| `fees.py` | 0 bytes | **DEFER** | **Not a separate M4 module.** Contract fees (e.g. administrative fees, rider charges) are configured as parameterized formulas in `config.py`. |
| `reserve.py` | 0 bytes | **DEFER** | **Out of M4 Scope.** Statutory/GAAP reserving frameworks (VM-21, VM-22, LDTI, DAC, MRB) belong to Week 5 (`MP` §14.5). Do not build generic reserve frameworks in M4. |
| `mortality.py` | 1,300 bytes | **REUSE** | Operational. Reused for demographic decrement lookups. |
| `survival_probability.py`| 1,555 bytes | **REUSE** | Operational. Reused for decrement rollforwards. |
| `payment_stream.py` | 1,667 bytes | **DO NOT COUNT** | SPIA-specific payment logic; outside FIA/RILA scope. |
| `present_value.py` | 1,005 bytes | **REUSE** | Available for discounting projected cash flows where required. |

### 4.2 Product Verticals (`fia_lite/` and `rila_lite/`)

| File Name | Current Size | MP §15.1 Decision | Action Required by Noah |
| :--- | :---: | :---: | :--- |
| `fia_lite/config.py` | 0 bytes | **BUILD** | Complete FIA product definition: variables, registered formulas, published outputs, factor bindings. |
| `fia_lite/golden.py` | 0 bytes | **BUILD** | Golden policy definitions with independent actuarial expected values. |
| `fia_lite/__init__.py` | 0 bytes | **BUILD** | Module exports. |
| `rila_lite/config.py` | 0 bytes | **BUILD** | Complete RILA product definition: variables, segment formulas, buffer/floor crediting, published outputs. |
| `rila_lite/golden.py` | 0 bytes | **BUILD** | Golden policy definitions with multi-segment allocations across economic scenarios. |
| `rila_lite/__init__.py` | 0 bytes | **BUILD** | Module exports. |

### 4.3 Core Engine & Infrastructure

| Component / Subsystem | Location | MP §15.1 Decision | Action Required by Noah |
| :--- | :--- | :---: | :--- |
| **Projection Engine** | `backend/app/core/projection_engine/engine.py` | **REUSE** | Execute FIA/RILA runs without engine changes (`m3-cpu-dsl-1`). |
| **Formula DSL & Registry** | `backend/app/core/formula_engine/` | **REUSE** | Register FIA/RILA formula functions via `@register_function`. |
| **Variable Registry** | `backend/app/core/variable_registry/` | **REUSE** | Resolve FIA/RILA inputs, parameters, and table lookups. |
| **Dependency Graph** | `backend/app/core/dependency_engine/` | **REUSE** | Sort formula dependency DAGs automatically. |
| **Inforce Registry** | `backend/app/products/inforce_registry.py` | **IMPROVE** | Register `FIA` and `RILA` canonical inforce schemas and validation rules. |
| **Product Registry** | `backend/app/products/registry.py` | **IMPROVE** | Register `fia_lite` and `rila_lite` formula functions in `register_all_products()`. |
| **Artifact / Parquet Storage** | `backend/app/core/artifacts.py` | **REUSE** | Write policy-level, segment-level, and cell trace outputs to Parquet. |
| **Reconciliation Service**| `backend/app/services/reconciliation_service.py` | **REUSE** | Run scenario-aware 3-state reconciliation against approved expected results. |

---

## 5. DATA MANAGER & INPUT GOVERNANCE INTEGRATION

MentorAmp maintains a single, governed data ingestion and management layer. Noah must **NOT** create a separate FIA/RILA ingestion subsystem. All input datasets must flow through the existing Data Manager pipeline.

### 5.1 Ingested Dataset Governance Matrix
The following datasets must be supported, validated, and versioned for Milestone 4:

| Dataset Category | Content & Format | Schema Requirements | Validation & Fingerprint Rules |
| :--- | :--- | :--- | :--- |
| **FIA Inforce** | Policy records, demographic fields, premium, strategy allocations (CSV/Parquet). | Registered in `inforce_registry.py`. Requires `policy_id`, `issue_date`, `issue_age`, `gender`, `premium`, `alloc_fixed_pct`, `alloc_indexed_pct`. | Sum of allocation percentages must equal 100% ($\pm 10^{-6}$). Reject negative premiums. SHA-256 fingerprint frozen in run manifest. |
| **RILA Inforce** | Policy records with multi-segment allocations, terms, buffers/floors (CSV/Parquet). | Registered in `inforce_registry.py`. Supports segment dimensions (`alloc_seg1_pct`, `term_seg1`, `buffer_seg1`, etc.). | Allocation sum = 100%. Buffer/floor rates bounded $[0.0, 1.0]$. Issue age within allowed product range. SHA-256 frozen. |
| **Index Observations** | Historical and projected index levels (S&P 500, Russell 2000, NASDAQ 100) by date/month. | Columns: `[index_code, observation_date, projection_month, index_level]`. | Index levels must be strictly positive ($> 0.0$). No missing monthly steps. SHA-256 frozen. |
| **Option-Budget Factor Tables** | Option budget and volatility bands mapped to caps and participation rates. | Columns: `[option_budget_band, implied_vol_band, index_code, term_years, max_cap, max_pr]`. | Exact range covering without gaps. Non-negative caps. SHA-256 frozen. |
| **Economic Scenario Sets**| Projected interest rates, discount curves, and equity index returns. | Compatible with scenario schema (`scenario_id`, `month`, `yield`, `equity_return`). | Deterministic or stochastic flag recorded. Real-world vs risk-neutral indicator recorded. SHA-256 frozen. |
| **Expected Results** | Independently prepared benchmark values for reconciliation. | Columns: `[scenario_id, policy_id, month, variable_name, expected_value]`. | Must undergo 4-eyes approval workflow (`approved_by`, `approval_date`). SHA-256 frozen. |

### 5.2 Mandatory Audit Attributes
For every dataset ingested through Data Manager, the system must capture and persist:
- `project_id` and project ownership;
- `dataset_id`, `name`, and semantic `version`;
- `schema_version` and canonical mapping profile;
- Source file format (CSV, TSV, Excel, Parquet);
- Canonical raw SHA-256 hash/fingerprint;
- Ingestion validation status (`VALID`, `WARNING`, `REJECTED`);
- Record count, accepted count, and detailed rejected record log;
- Effective date and creation timestamp;
- RunPackage frozen identity.

---

## 6. FORMULA REGISTRY & FORMULA STUDIO INTEGRATION

MentorAmp enforces a strict architectural boundary between controlled actuarial formulas and internal execution primitives.

### 6.1 The Formula Boundary Architecture
```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       FORMULA STUDIO & GOVERNANCE                           │
│  • High-level actuarial business formulas visible to users & actuaries      │
│  • Represented in MentorAmp DSL / registered formula definitions           │
│  • Examples: Credited Rate, Account Value Rollforward, Cash Surrender Value │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ calls
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     SHARED MATHEMATICAL PRIMITIVES                          │
│  • Pure, stateless Python functions in shared_modules/                      │
│  • Unit-tested with hand-calculated illustrative vectors                    │
│  • Examples: buffer_payoff(), ptp_cap_pr_crediting(), pro_rata_scaling()    │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 6.2 Formula Representation Rules
1. **No Giant DSL Expressions:** Complex multi-step mathematical operations (such as Black-Scholes option pricing or multi-segment list rollforwards) must **not** be forced into awkward, unreadable DSL strings. They must be encapsulated in pure Python primitives in `shared_modules/` and registered with `@register_function`.
2. **No Actuarial Logic Hiding:** Core contract logic (which strategies are active, deduction ordering, benefit definitions) must remain exposed as registered formulas visible in Formula Studio.
3. **Formula Metadata Requirements:** Every registered formula must record:
   - `name` and `output_variable`;
   - `function_ref` matching the registered Python entrypoint;
   - Explicit `dependencies` list for topological sorting;
   - `unit` (e.g. `USD`, `rate`, `ratio`);
   - Product applicability (`FIA`, `RILA`);
   - Version label and effective date.

---

## 7. VARIABLE REGISTRY & RESOLVER INTEGRATION

Every input, intermediate value, and published output for FIA and RILA must be cataloged in the Variable Registry and resolved through `app.core.variable_registry.resolver`.

### 7.1 Variable Specification Metadata
For every material FIA/RILA variable, Noah must register a `VariableSpec` specifying:
- `name`: Canonical snake_case identifier (e.g. `segment_1_interim_val`);
- `display_name`: Human-readable label for UI and reports;
- `kind`: `input`, `formula`, `table`, or `output`;
- `data_type`: `number`, `string`, `boolean`, `date`, or `array`;
- `unit`: `USD`, `rate`, `index_points`, or `dimensionless`;
- `source`: Source mapping configuration (`inforce`, `table`, `scenario`, `formula`);
- `timing`: `BOP` (Beginning of Period) or `EOP` (End of Period);
- `stock_flow`: `stock` (balance sheet point-in-time) or `flow` (cumulative volume over period);
- `aggregation`: `beginning_of_period`, `end_of_period`, or `sum`;
- `allow_scenario_override`: Boolean indicating whether scenario datasets can override this variable;
- `trace_level`: Level of detail recorded in trace artifacts.

### 7.2 Resolution Provenance
When resolving variables during engine execution:
- Lookups must record exact source table and lookup keys;
- Missing required table lookups must raise an explicit `EngineError` (no silent fallbacks to `0.0` or defaults);
- Scenarios override model defaults only when `allow_scenario_override == True`.

---

## 8. SHARED MATHEMATICAL PRIMITIVES (`shared_modules/`)

Noah must construct pure, stateless, unit-tested mathematical primitives in `backend/app/products/shared_modules/`.

### 8.1 Index Crediting Primitives (`indexed_crediting.py`)
```python
def ptp_cap_pr_crediting(
    index_return: float,
    cap: float,
    participation_rate: float = 1.0,
    floor: float = 0.0,
) -> float:
    """Pure mathematical primitive: Capped return with participation rate and floor."""
    participated = participation_rate * index_return
    capped = min(cap, participated)
    result = max(floor, capped)
    return 0.0 if abs(result) < 1e-12 else result

def ptp_spread_crediting(
    index_return: float,
    spread: float,
    floor: float = 0.0,
) -> float:
    """Pure mathematical primitive: Return with deductible spread."""
    result = max(floor, index_return - spread)
    return 0.0 if abs(result) < 1e-12 else result

def monthly_average_return(
    monthly_index_levels: list[float],
    start_index_level: float,
) -> float:
    """Pure mathematical primitive: 12-month average return."""
    if not monthly_index_levels or start_index_level <= 0.0:
        raise ValueError("Invalid index levels for monthly averaging")
    avg_level = sum(monthly_index_levels) / len(monthly_index_levels)
    return (avg_level - start_index_level) / start_index_level
```

### 8.2 Downside Protection Primitives (`buffer_floor.py`)
```python
def buffer_payoff(
    index_return: float,
    cap: float,
    buffer_rate: float,
    participation_rate: float = 1.0,
) -> float:
    """Pure mathematical primitive: Buffer downside protection with upside cap."""
    if index_return >= 0.0:
        return min(cap, participation_rate * index_return)
    elif index_return >= -buffer_rate or math.isclose(index_return, -buffer_rate, abs_tol=1e-12):
        return 0.0
    else:
        return index_return + buffer_rate

def floor_payoff(
    index_return: float,
    cap: float,
    floor_rate: float,
    participation_rate: float = 1.0,
) -> float:
    """Pure mathematical primitive: Floor downside protection with upside cap."""
    if index_return >= 0.0:
        return min(cap, participation_rate * index_return)
    elif index_return >= -floor_rate:
        return index_return
    else:
        return -floor_rate
```

### 8.3 Multi-Segment State Tracking & Rollforward (`segment.py`)
```python
from dataclasses import dataclass

@dataclass
class SegmentState:
    segment_id: str
    strategy_type: str
    index_code: str
    term_months: int
    elapsed_months: int
    start_index_level: float
    base_balance: float
    current_interim_value: float
    cap: float
    downside_rate: float # buffer or floor rate
    is_matured: bool = False

def rollforward_segment_term(
    segment: SegmentState,
    current_index_level: float,
    withdrawal_amount: float = 0.0,
    withdrawal_mode: str = "pro_rata",
) -> SegmentState:
    """Increment segment duration, apply withdrawals, and detect maturity."""
    # Logic advances elapsed_months, calculates interim value or maturity payoff
```

### 8.4 Partial Withdrawal Adjustment Primitives (`withdrawal.py`)
```python
def apply_partial_withdrawal(
    base_balance: float,
    current_value: float,
    withdrawal_amount: float,
    mode: str = "pro_rata",
) -> tuple[float, float]:
    """Reduce base balance and current value under specified withdrawal mode.
    
    Returns (new_base_balance, new_current_value).
    """
    if withdrawal_amount <= 0.0:
        return base_balance, current_value
    if withdrawal_amount > current_value:
        raise ValueError("Withdrawal exceeds available balance")
        
    if mode == "pro_rata":
        ratio = withdrawal_amount / current_value
        new_base = base_balance * (1.0 - ratio)
        new_val = current_value - withdrawal_amount
        return new_base, new_val
    elif mode == "dollar_for_dollar":
        new_base = max(0.0, base_balance - withdrawal_amount)
        new_val = current_value - withdrawal_amount
        return new_base, new_val
    else:
        raise ValueError(f"Unsupported withdrawal mode: {mode}")
```

---

## 9. FIXED INDEXED ANNUITY (FIA) PRODUCT VERTICAL (`fia_lite/`)

### 9.1 Scope Classification of FIA Features

| Feature / Component | Scope Classification | Provenance & Rules |
| :--- | :---: | :--- |
| **Fixed & Indexed Allocations** | `AUTHORITATIVE MINIMUM CAPABILITY` | `CAS` §6 requires allocation across fixed and indexed accounts. |
| **Caps, Participation Rates, Spreads** | `AUTHORITATIVE MINIMUM CAPABILITY` | `CAS` §6 requires index crediting with caps, participation, and spreads. |
| **Option Budget / Factor Inputs** | `AUTHORITATIVE MINIMUM CAPABILITY` | `CAS` §6 requires factor/option-budget trace and scenario pathways. |
| **Proposed Reference Strategy: Annual PTP Cap**| `PROPOSED REFERENCE CONFIGURATION — OWNER APPROVAL REQUIRED`| Primary candidate for initial Launch vertical (`ACT-M4-DEC-01`). |
| **Proposed Reference Strategy: Monthly Average**| `PROPOSED REFERENCE CONFIGURATION — OWNER APPROVAL REQUIRED`| Secondary candidate strategy (`ACT-M4-DEC-01`). |
| **Contractual Non-Negative Credit Floor (0.0%)**| `PROPOSED FIA REFERENCE CONTRACT RULE — OWNER APPROVAL REQUIRED`| Standard reference contract rule guaranteeing no index losses. |
| **Option Budget Linear Derivation** | `PROPOSED REFERENCE CONFIGURATION — OWNER APPROVAL REQUIRED` | Candidate formula: $OB_t = \max(OB_{\min}, \text{earned} - \text{spread} - \text{fee})$. |
| **Model 805 Nonforfeiture Reference Case** | `ACTUARIAL/REGULATORY REFERENCE CASE — OWNER APPROVAL REQUIRED` | Statutory MGSV accumulation subject to confirmed interest rate. |

### 9.2 Product Definition File (`backend/app/products/fia_lite/config.py`)
Noah must construct `config.py` declaring:
- `PRODUCT_CODE = "FIA"`
- `PRODUCT_NAME = "Illustrative Fixed Indexed Annuity"`
- Variables: `account_value_open`, `account_value_close`, `fixed_av_close`, `indexed_av_close`, `index_return`, `credited_rate`, `option_budget`, `gross_withdrawal`, `surrender_charge`, `cash_surrender_value`, `death_benefit`.
- Formulas: Registered formulas calculating monthly interest, index returns, anniversary crediting, withdrawals, and ending account value.

---

## 10. REGISTERED INDEX-LINKED ANNUITY (RILA) PRODUCT VERTICAL (`rila_lite/`)

### 10.1 Scope Classification of RILA Features

| Feature / Component | Scope Classification | Provenance & Rules |
| :--- | :---: | :--- |
| **Multi-Segment Tracking** | `AUTHORITATIVE MINIMUM CAPABILITY` | `CAS` §6 requires segment tracking across terms and indices. |
| **Downside Buffers and Floors** | `AUTHORITATIVE MINIMUM CAPABILITY` | `CAS` §6 requires buffer and floor mechanics. |
| **Interim and Maturity Crediting** | `AUTHORITATIVE MINIMUM CAPABILITY` | `CAS` §6 requires interim and maturity crediting. |
| **Full-Block Execution & Trace** | `AUTHORITATIVE MINIMUM CAPABILITY` | `CAS` §6, §15.7 mandates representative RILA block execution. |
| **AG 54 Hypothetical Replicating Portfolio** | `PROPOSED REFERENCE CONFIGURATION — OWNER APPROVAL REQUIRED` | Candidate reference methodology under NAIC AG 54 (`ACT-M4-DEC-08`). |
| **Prorated Intrinsic Linear Interim Value** | `PROPOSED REFERENCE CONFIGURATION — OWNER APPROVAL REQUIRED` | Alternative candidate; cannot claim AG 54 compliance (`ACT-M4-DEC-08`). |
| **Pro-Rata Segment Withdrawal Reduction** | `PROPOSED REFERENCE CONFIGURATION — OWNER APPROVAL REQUIRED` | Proposed standard for active segment withdrawals (`ACT-M4-DEC-09`). |
| **Dual-Direction Buffer Payoff** | `RESEARCH-BACKED / FUTURE VARIANT` | Advisory market feature; turns mild losses into gains. Not required for Launch. |

### 10.2 Product Definition File (`backend/app/products/rila_lite/config.py`)
Noah must construct `config.py` declaring:
- `PRODUCT_CODE = "RILA"`
- `PRODUCT_NAME = "Illustrative Registered Index-Linked Annuity"`
- Multi-segment variable dimensions:
  - `segment_1_base`, `segment_1_interim_val`, `segment_1_credited_rate`
  - `segment_2_base`, `segment_2_interim_val`, `segment_2_credited_rate`
  - `total_account_value_close` = $\sum \text{Segment Values} + \text{Fixed AV}$

---

## 11. REPRESENTATIVE RILA BLOCK EXECUTION HARNESS (`CAS` §15.7)

### 11.1 Authoritative Acceptance Mandate
`CAS` §15.7 states:
> *"A representative RILA block executes with policy-level and aggregate outputs and supports trace to a selected policy, scenario, time, and formula."*

### 11.2 Proposed Synthetic Reference Block Design
> [!NOTE]
> *PROPOSED SYNTHETIC REFERENCE BLOCK DESIGN — OWNER APPROVAL REQUIRED (`ACT-M4-DEC-14`)*

- **Inforce Dataset:** `backend/tests/fixtures/rila_representative_block_inforce.csv`
- **Proposed Sizing:** 30 representative synthetic policies.
- **Demographic Spread:** Ages 45, 52, 58, 62, 67, 72 (Male/Female). Premiums $50k to $1M.
- **Segment Allocations:**
  - Conservative: 50% Fixed, 50% 1-Yr S&P 500 Buffer 10%.
  - Balanced: 40% 1-Yr S&P 500 Buffer 10%, 40% 3-Yr Russell 2000 Buffer 15%, 20% Fixed.
  - Floor Protected: 50% 1-Yr S&P 500 Floor 10%, 50% 3-Yr S&P 500 Floor 15%.
- **Projection Horizon:** 72 projection months.
- **Scenario Paths:** Base (+7% equity), Bull (+14% equity), Bear (-25% drop testing buffer breach), Rate Shock (rates surge to 7.50%).

---

## 12. SCENARIO & MARKET-DATA ARCHITECTURE

Noah must reuse MentorAmp's governed scenario consumption pipeline:
1. **Scenario Preservation Invariant:** Scenario identity (`scenario_id`, `scenario_set_id`) must be strictly preserved through:
   `Input Dataset` $\to$ `Variable Resolver` $\to$ `Projection Engine` $\to$ `Parquet Storage` $\to$ `Trace` $\to$ `Reconciliation`.
2. **Never Collapse Scenarios:** Reconciliation must match cells using composite keys including `scenario_id`. Base and Stress rows must never be averaged or collapsed.
3. **Missing Market Data Guard:** If an index observation is missing for a required projection month, execution must fail with a descriptive `EngineError` (`error_type="MISSING_SCENARIO_DATA"`).

---

## 13. CANONICAL CASH-FLOW & RESULT DATA MODEL

Outputs must preserve sufficient dimensional granularity to feed Week 5 valuation frameworks (VM-21, VM-22, LDTI, DAC, MRB) without rerunning product formulas:
- **Run Identity:** `project_id`, `model_id`, `run_id`, `valuation_date`, `execution_backend`.
- **Policy & Segment Identity:** `policy_id`, `product_code`, `account_id`, `segment_id`, `strategy_type`.
- **Scenario & Time:** `scenario_id`, `projection_month`, `policy_year`, `duration_months`.
- **Cash Flow Semantics:** `cash_flow_category`, `source_formula`, `sign_convention` (positive for inflows, negative for outflows), `currency` (`USD`), `timing_convention` (`BOP` or `EOP`).
- **Audit Lineage:** `trace_id`, `formula_version`, `factor_version`.

---

## 14. ANALYTICAL PARQUET STORAGE ARCHITECTURE

MentorAmp maintains a strict separation between relational transactional storage and columnar analytical storage:
* **PostgreSQL:** Stores run definitions, model versions, approval governance, validation states, and dataset manifests.
* **Columnar Parquet:** Stores large multi-policy, multi-scenario, multi-month cash flows, segment balances, and execution traces.

Noah must partition outputs via `app.core.artifacts.RunArtifactBuffer`:
1. `outputs.parquet`: Policy-level monthly results.
2. `aggregate_summary.parquet`: Block-level monthly sums.
3. `traces.parquet`: Formula-level derivation rows for traced policies.

---

## 15. FINE-GRAINED CELL TRACE ARCHITECTURE

Trace instrumentation must allow a senior auditor to inspect any cell and immediately verify:
- Exact policy ID, segment ID, scenario ID, and projection month;
- Formula function name and formula version;
- Inforce input parameters;
- Factor table lookup keys and exact returned factor;
- Raw index return and intermediate calculation variables;
- Applied cap, participation rate, spread, buffer, or floor;
- Final output value;
- Any warning or error raised during execution.

Traced rows are buffered during calculation and written directly to `traces.parquet`.

---

## 16. RECONCILIATION ENGINE INTEGRATION

Milestone 4 must integrate with the hardened M3 Reconciliation Service (`reconciliation_service.py`):
1. **Three-State Reconciliation Semantics:**
   - `PASS`: All calculated cells match approved expected values within governed tolerances.
   - `FAIL`: One or more cells exceed tolerance.
   - `NOT_VERIFIABLE`: Missing trace links or missing source evidence. Missing evidence must **NEVER** return `PASS`.
2. **Scenario Matching:** Comparison keys strictly bind `(policy_id, scenario_id, projection_month, variable_name)`.
3. **Explicit Aggregation Rules:**
   - Cash flows $\to$ `sum`.
   - Ending balances (AV, CSV) $\to$ `end_of_period`.
   - Beginning balances $\to$ `beginning_of_period`.

---

## 17. GOLDEN POLICY & EXPECTED-RESULT GOVERNANCE (HARD GATE)

> [!CAUTION]
> **PRODUCTION CODE CANNOT ACT AS ITS OWN ORACLE.**
> Acceptance of FIA and RILA product verticals strictly requires comparison against **independently prepared actuarial benchmark spreadsheets or reference models**.
>
> If independent expected results are pending from the Actuarial Product Owner:
> `ENGINEERING IMPLEMENTATION MAY PROCEED`, but
> `ACTUARIAL VERIFICATION / ACCEPTANCE REMAINS BLOCKED`.

Expected-result datasets must record:
- Independent author and preparation source;
- Dataset SHA-256 fingerprint;
- Target product, model version, and scenario ID;
- Four-eyes approval metadata (`approved_by`, `approval_date`);
- Governed numerical tolerance profile.

---

## 18. RUNPACKAGE & MANIFEST REQUIREMENTS

Every completed FIA and RILA run must freeze an immutable `RunPackage` manifest containing:
- Application version and engine version (`m3-cpu-dsl-1`);
- Git commit hash and build identifier;
- Project ID, model ID, product code (`FIA` or `RILA`);
- SHA-256 `model_definition_fingerprint`;
- SHA-256 `validation_fingerprint`;
- SHA-256 `approved_fingerprint` (matching executable model for governed runs);
- Inforce dataset fingerprint, scenario set fingerprint, factor table fingerprint;
- Valuation date, projection horizon, execution timing convention;
- Start/completion timestamps and final execution status.

---

## 19. DATABASE SCHEMA & MIGRATION GOVERNANCE

If Milestone 4 requires extending relational models:
1. **Alembic Single Canonical Head:** All migrations must extend sequentially from the current head (`a6c1f4e8d203`).
2. **Zero-to-Head Testing:** Must be verified on clean PostgreSQL (`alembic downgrade base` $\to$ `alembic upgrade head`).
3. **Production Parity:** Tested against disposable PostgreSQL; no SQLite-only workarounds.
4. **Relational Integrity:** Foreign keys, indexes, and unique constraints applied deliberately; no orphaned records.

---

## 20. API CONTRACT & FASTAPI SERVICE BOUNDARIES

All API interactions must preserve established architecture:
- Route endpoints remain thin HTTP controllers in `backend/app/api/`;
- Actuarial calculations remain strictly in `projection_engine/` and `shared_modules/`;
- Strict Pydantic schemas for request validation and response envelopes;
- Project ownership and authorization enforced on all endpoints;
- Consistent error envelopes (`detail`, `error_code`, `timestamp`) with no internal server leakage.

---

## 21. SECURITY & CROSS-PROJECT ISOLATION

Milestone 4 test suites must verify multi-project security:
- **IDOR Prevention:** A run in Project A cannot bind or execute inforce datasets, models, factor tables, or scenarios belonging to Project B.
- **Path Traversal Protection:** Parquet artifact loading and trace exports must sanitize file paths against directory traversal.
- **Governance Tampering:** Blocked execution if an unapproved user attempts to trigger a governed run without required approval fingerprints.

---

## 22. NUMERICAL CORRECTNESS, BOUNDARY TESTING & DETERMINISM

Noah must implement unit tests covering all mathematical boundary conditions:

| Mathematical Boundary Condition | Software Precision Test Guard | Actuarial Expectation |
| :--- | :--- | :--- |
| **Exact Buffer Boundary ($R_T = -B$)** | `math.isclose(R, -B, abs_tol=1e-12)` | Credited return is strictly $0.0\%$. |
| **Exact Floor Boundary ($R_T = -F$)** | `math.isclose(R, -F, abs_tol=1e-12)` | Credited return is strictly $-F$. |
| **Exact Cap Boundary ($R_T = \text{Cap}$)** | `math.isclose(R, Cap, abs_tol=1e-12)` | Credited return is strictly $\text{Cap}$. |
| **Zero Return Normalization ($R_T = 0.0$)** | `if abs(val) < 1e-12: val = 0.0` | Prevents negative zero (`-0.0`). |
| **FIA Negative Equity Returns** | Index drops -40% | Credited return is strictly $\ge 0.0\%$. |
| **Zero Account Value** | Full surrender reduces AV to $0.0$ | No subsequent interest crediting or fee deduction. |
| **Deterministic Run Parity** | Run identical block twice with same seed | Parquet outputs and SHA-256 fingerprints match identically. |

---

## 23. PERFORMANCE & FUTURE EXECUTION COMPATIBILITY

Milestone 4 executes on local CPU. However, the design must remain compatible with later PySpark and GPU execution (Week 6 / `MP` §14.6):
- **CPU Source of Truth:** Local CPU is the reference baseline for numerical parity.
- **No Global Mutable State:** Formulas and shared functions must be pure and stateless.
- **Serializable Structures:** All segment contexts and parameters must be pickle-serializable.
- **No Per-Month AST Parsing:** Expressions compiled once at model initialization.

---

## 24. FRAMEWORK SEPARATION (WEEK 4 MECHANICS VS WEEK 5 FRAMEWORKS)

`CAS` §7 and `MP` §7 establish the **Framework Separation Rule**:
* **Milestone 4 Scope:** Pure contract cash flows, account rollforwards, segment crediting, withdrawals, and base product outputs.
* **Week 5 Scope (Deferred):** VM-21 (stochastic CTE reserves), VM-22 (statutory CARVM), LDTI (NPR remeasurement), DAC (constant-basis amortization), MRB (fair-value market risk benefits), and ASC 815 (embedded derivative accounting).
* Noah must **NOT** embed reserve calculations inside FIA/RILA product formulas. Milestone 4 outputs will feed those frameworks in Week 5.

---

## 25. OUTPUT-AS-INPUT & DERIVED-DATASET COMPATIBILITY

Projected FIA and RILA cash flows and segment states will serve as input datasets for downstream reserve engines:
- Analytical outputs must retain version, schema, and source run identity;
- Output tables must be registerable in Data Manager as derived datasets;
- Lineage metadata must link derived outputs back to the producing RunPackage.

---

## 26. BACKWARD COMPATIBILITY & REGRESSION INVARIANT

Milestone 4 must introduce zero regressions across existing capabilities:
- **SPIA Reference Workflows:** Must continue running and passing reconciliation.
- **MYGA Illustrative Workflows:** Must continue running and passing reconciliation.
- **Existing Test Suite:** All 512+ existing tests across `backend/tests/` must remain 100% green.

---

## 27. EXTERNAL RESEARCH FINDINGS & MARKET CONTEXT

> [!NOTE]
> *RESEARCH-BACKED RECOMMENDATION — NOT ORIGINAL AUTHORITATIVE REQUIREMENT*

External actuarial and regulatory research provides valuable context for candidate designs:
1. **NAIC Actuarial Guideline 54 (AG 54):** Defines the *Hypothetical Portfolio Methodology* (bond + replicating European call/put options) as the statutory benchmark for index-linked variable annuity interim values. Linear/prorated methods are used in some retail contracts for simplicity, but carriers must document consistency with fair value.
2. **NAIC Model #805 vs AG 35:** Model #805 governs nonforfeiture minimum account values ($MGSV = 87.5\% \times \text{Premium}$ accumulated at statutory nonforfeiture rates). AG 35 governs statutory CARVM reserve calculations and hedging asset valuation.
3. **Dual Direction Payoff:** An innovative retail RILA feature that converts market losses within the buffer into positive returns ($R_{\text{credited}} = |R_T|$). Useful for competitive benchmarking, but optional for Launch 1.0.

---

## 28. ACTUARIAL OWNER DECISION REGISTER (M4 DECISIONS)

The following 15 decisions are cataloged for Actuarial Product Owner sign-off:

| Decision ID | Scope & Subject | Candidate Options | Recommended Engineering Default | Blocking Gate |
| :---: | :--- | :--- | :--- | :---: |
| **ACT-M4-DEC-01** | **FIA Reference Strategy Selection** | 1. Annual PTP Cap & PR.<br>2. Annual PTP Spread.<br>3. Monthly Average Cap. | **Option 1 (Annual PTP Cap & PR)**. | Blocks FIA Model Finalization |
| **ACT-M4-DEC-02** | **FIA Option-Budget Methodology** | 1. Governed factor table lookup.<br>2. Analytical Black-Scholes formula. | **Option 1 (Factor Table Lookup)**. | Blocks FIA Verification |
| **ACT-M4-DEC-03** | **FIA Nonforfeiture (Model 805) Rule** | 1. Model 805 standard (87.5% @ 3.00%).<br>2. Alternate statutory rate/rule. | **Option 1** subject to confirmed rate. | Blocks FIA Statutory Acceptance |
| **ACT-M4-DEC-04** | **FIA Withdrawal & MVA Mechanics** | 1. Pro-rata withdrawal, duration SC schedule, standard MVA.<br>2. Alternate schedule. | **Option 1** reusing MYGA-style tables. | Blocks FIA Verification |
| **ACT-M4-DEC-05** | **FIA Death-Benefit Mechanics** | 1. Account value.<br>2. Greater of AV and MGSV. | **Option 2 (Greater of AV and MGSV)**. | Blocks FIA Acceptance |
| **ACT-M4-DEC-06** | **RILA Reference Segments & Terms** | 1. 1-Yr S&P 500 Buffer 10%, 3-Yr Russell Buffer 15%.<br>2. Alternate terms/indices. | **Option 1** covering 1-yr and 3-yr durations. | Blocks RILA Model Finalization |
| **ACT-M4-DEC-07** | **RILA Maturity Payoff Rules** | 1. Buffer and Floor standard payoffs.<br>2. Include Dual Direction. | **Option 1 (Buffer & Floor standard)**. | Blocks RILA Implementation |
| **ACT-M4-DEC-08** | **RILA Interim-Value Methodology** | 1. AG 54 Replicating Option Portfolio.<br>2. Prorated Intrinsic Approximation. | **Configurable interface; Option 1 preferred if oracle exists.** | Blocks RILA Interim Value Implementation |
| **ACT-M4-DEC-09** | **Active Segment Withdrawal Adjustment** | 1. Pro-Rata reduction.<br>2. Dollar-for-Dollar reduction. | **Option 1 (Pro-Rata Reduction)**. | Blocks RILA Withdrawal Finalization |
| **ACT-M4-DEC-10** | **Segment Maturity Rollover Rule** | 1. Auto-renew into same strategy.<br>2. Sweep to Fixed Account. | **Option 1 (Auto-Renew)**. | Blocks RILA Multi-Year Projection |
| **ACT-M4-DEC-11** | **Index Observation Timing Convention**| 1. End of Month (EOM) spot value.<br>2. Exact trading anniversary. | **Option 1 (EOM Spot Value)**. | Blocks RILA Implementation |
| **ACT-M4-DEC-12** | **Contract Fee Timing & Order** | 1. Deduct at beginning of month.<br>2. Deduct at end of month after interest. | **Option 2 (EOM after crediting)**. | Blocks Verification |
| **ACT-M4-DEC-13** | **Benefit / Claim Timing Convention** | 1. Mid-month average.<br>2. End-of-month post crediting. | **Option 2 (EOM post crediting)**. | Blocks Verification |
| **ACT-M4-DEC-14** | **Synthetic RILA Block Sizing & Paths** | 1. 30 policies, 4 scenarios, 72 months.<br>2. Alternate block composition. | **Option 1** as proposed synthetic test block. | Blocks Block Acceptance |
| **ACT-M4-DEC-15** | **Governed Reconciliation Tolerances** | 1. Absolute $1.00, relative 0.01% on AV.<br>2. Tighter/custom tolerances. | **Governed tolerance profile per reference case.** | Blocks Final Acceptance |

---

## 29. STEP-BY-STEP IMPLEMENTATION ROADMAP FOR NOAH

```
WP 1: Shared Mathematical Primitives (shared_modules/)
      ├── indexed_crediting.py
      ├── buffer_floor.py
      ├── segment.py
      ├── fixed_crediting.py
      ├── account_value.py
      ├── withdrawal.py
      ├── surrender_charge.py
      └── option_budget.py
            │
            ▼
WP 2: FIA Vertical Slice (fia_lite/)
      ├── config.py (Variables, Formulas, Models)
      ├── golden.py (Actuarial test case definitions)
      └── Inforce & Product Registration
            │
            ▼
WP 3: RILA Vertical Slice (rila_lite/)
      ├── config.py (Multi-segment variables & formulas)
      ├── golden.py (Multi-segment test case definitions)
      └── Inforce & Product Registration
            │
            ▼
WP 4: Representative RILA Block Execution Harness
      ├── Canonical Synthetic Inforce CSV (30 policies)
      ├── Scenario & Market Data Bindings
      └── Parquet Persistence (Policy, Aggregate, Cell Trace)
            │
            ▼
WP 5: Verification & Reconciliation Test Suite
      ├── Unit tests for all shared primitives
      ├── Golden policy tests (FIA and RILA)
      ├── Representative block integration test
      └── Full regression run (512+ existing tests green)
```

---

## 30. COMPREHENSIVE VERIFICATION & TEST MATRIX

Noah's test suite must provide comprehensive coverage across:
1. **Shared Primitives Unit Tests:** Exact boundary testing ($R = -B$, $R = -F$, $R = \text{Cap}$, zero return) with hand-calculated illustrative vectors.
2. **Formula & Variable Resolver Tests:** Verifying registered formulas resolve inputs correctly from inforce records, tables, and prior outputs.
3. **FIA Reference Policy Tests:** Multi-year rollforward verifying fixed crediting, anniversary index crediting, and surrender charges.
4. **RILA Reference Policy Tests:** Multi-segment tracking across 1-year and 3-year terms under Base, Bull, and Bear scenarios.
5. **Representative RILA Block Test:** Sized 30-policy block execution verifying policy Parquet output, aggregate totals, and sample cell trace.
6. **Security & IDOR Tests:** Cross-project dataset, model, and scenario isolation.
7. **PostgreSQL Migration Tests:** Single canonical Alembic head verification.
8. **Regression Test Suite:** 100% pass rate across all 512+ existing unit and integration tests.

---

## 31. REQUIRED HANDOFF REVIEW PACKAGE (`noah_m4_fia_rila_review.zip`)

Upon milestone completion, Noah must package and deliver `noah_m4_fia_rila_review.zip` containing:
1. Formal completion report detailing all built components;
2. Remote tracking branch name and baseline commit SHA;
3. Final clean commit SHA (`git status --short` must be empty);
4. Comprehensive changed-file inventory and full git patch;
5. Alembic migration head confirmation;
6. Disposable PostgreSQL migration verification log;
7. Shared primitives unit test execution logs;
8. FIA golden-policy test execution logs;
9. RILA golden-policy test execution logs;
10. Representative RILA block test execution logs and aggregate summary;
11. Sample Parquet schema inspection output (`outputs.parquet`, `traces.parquet`);
12. Cell trace sample output for designated policy;
13. Scenario preservation proof (verifying Base and Stress remain distinct);
14. Governed RunPackage manifest JSON showing frozen SHA-256 fingerprints;
15. Reconciliation report JSON showing three-state evaluation;
16. Security & IDOR test execution logs;
17. SPIA regression test output;
18. MYGA regression test output;
19. Full pytest repository execution output (all tests passing);
20. Data dictionary of newly registered FIA and RILA variables;
21. Catalog of unresolved actuarial decisions requiring owner confirmation;
22. Known limitations and technical debt disclosures.

---

## 32. TRACKER UPDATE & LIFECYCLE PROGRESSION RULES

Noah must strictly adhere to the project lifecycle status definitions:
- **`IMPLEMENTED`:** Code exists and performs intended functions, unit tests pass locally, but independent senior actuarial re-review has not yet occurred.
- **`VERIFIED`:** Code has passed senior re-review against independently prepared expected results within approved tolerances.
- **`ACCEPTED`:** Formal sign-off by Project Owner and Actuarial Owner.

Milestone 4 will be recorded in `PROJECT_MASTER_TRACKER.md` as `IMPLEMENTED, PENDING SENIOR ACTUARIAL RE-REVIEW` until the handoff package is independently inspected.

---

## 33. ACCEPTANCE CHECKLIST & DEFINITION OF DONE (DoD)

Before submitting Milestone 4 for review, Noah must verify that every condition is met:

- [ ] **Shared Modular Architecture:** All mathematical crediting and segment primitives reside in `shared_modules/`; zero product logic inside `projection_engine/engine.py`.
- [ ] **Zero-Byte Stubs Handled:** Required modules (`indexed_crediting.py`, `buffer_floor.py`, `segment.py`, `fixed_crediting.py`, `account_value.py`, `withdrawal.py`, `surrender_charge.py`, `option_budget.py`, `fia_lite/config.py`, `rila_lite/config.py`) are implemented. Unneeded frameworks (`reserve.py`, `death_benefit.py`, `fees.py`) are deferred.
- [ ] **Registry Integration:** `register_all_products()` registers `['SPIA', 'MYGA', 'FIA', 'RILA']` idempotently.
- [ ] **Boundary Precision Guards:** Software tests confirm that $R_T = -B \implies 0.0$ and $R_T = -F \implies -F$ without float representation anomalies.
- [ ] **Configurable Withdrawal Primitives:** Withdrawal logic supports both pro-rata and dollar-for-dollar modes without hardcoded unapproved mandates.
- [ ] **Representative Block Execution (`CAS` §15.7):**
  - [ ] Multi-policy block executes across multiple scenarios and projection months.
  - [ ] Policy outputs and aggregate summaries written to Parquet via `RunArtifactBuffer`.
  - [ ] Granular cell trace verified for designated sample policy.
  - [ ] RunPackage manifest contains immutable SHA-256 fingerprints.
- [ ] **Independent Oracle Requirement:** Production outputs do not act as their own actuarial oracle. Where expected results are pending owner approval, tests are marked pending verification.
- [ ] **Option 1 Isolation:** `MYGA-100001` files are completely untouched.
- [ ] **Regression Suite Green:** All existing 512+ pytest tests pass without failure.
