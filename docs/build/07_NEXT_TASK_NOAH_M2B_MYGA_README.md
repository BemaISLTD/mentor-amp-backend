# Next task for Noah: M2 Part B – 5-Year MYGA Product, Actuarial Validation, LifeLib Cross-Check Oracle & Data Manager Schema Support

| | |
|---|---|
| **Assigned to** | Noah Enemali (Backend Engineer) |
| **Assigned by / reviewer** | Samuel Ayinla (Technical Lead) |
| **Milestone** | M2 Part B (`01_HOW_WE_WORK_AND_MILESTONES.md` §3: "MYGA illustrative model (account-value rollforward)") |
| **Master Plan link** | MP §8.1 (Data Ingestion & Schemas), MP §13 (Noah owns Data Manager/Imports and Run Center), MP Weeks 2–4 Valuation Engine tasks |
| **Requirements covered** | LR1-MOD-001 (multi-product execution), LR1-MOD-002 (formula dependency graph), LR1-MOD-004 (deterministic reproduction); LR1-DATA-007..011 (Data Manager per-product schemas & audit); LR1-VAL-001 (monthly rollforward), LR1-VAL-002 (illustrative reserve valuation); LR1-ACT-001..007 (actuarial validation & lineage); LR1-API-003, 005; LR1-CORE-016 (audit & governance) |
| **Base commit** | `main` = `fffc396` (WP2 merged, single Alembic head `d4f7a9c2e681`, Neon DB upgraded to `d4f7a9c2e681`) |
| **Target Branch** | `feat/m2b-myga-product` |
| **Written** | 2026-10-18 |

---

## 1. The Task in One Paragraph

With M1 (SPIA) and WP2 (Data Manager lifecycle, Parquet artifact store, and run control) merged and verified on `main`, your next assignment is **M2 Part B**: building MentorAmp's second product—a **5-Year Multi-Year Guaranteed Annuity (MYGA)** (fixed deferred annuity with account-value rollforward). You will implement its 20 pure formula functions in topological order, enforce statutory actuarial mechanics (NAIC Model #805 Standard Nonforfeiture Law floor, linear Market Value Adjustment with symmetric charge cap, 10% free withdrawal, VM-22 dynamic lapses and post-guarantee shock lapses, annuitization window, expenses, and cash-flow ledger), integrate per-product inforce schema validation into the Data Manager (E-2), and verify the engine against an external, independent mathematical oracle combining **LifeLib (`MYGA_US_S @ 5d91f95`) as Reference-Only** and **NAIC / SOA standards** to machine precision ($\le \$0.000001$). At the finish line, MYGA runs seamlessly through the entire platform (Data Manager upload $\rightarrow$ validate $\rightarrow$ commit $\rightarrow$ approve $\rightarrow$ preflight $\rightarrow$ execute $\rightarrow$ Results Explorer $\rightarrow$ Trace $\rightarrow$ Compare), while the SPIA golden numbers remain frozen to the last decimal digit.

---

## 2. What Must Be True at the End (Acceptance Criteria)

Every acceptance criterion must be proved by automated tests or a reproducible script. The test suites are detailed in §7.

| # | Acceptance Criterion | Verification Method |
|---|---|---|
| **AC-1** | **Product Registration:** MYGA is registered alongside SPIA. `GET /v1/system/status` returns `products: ["SPIA", "MYGA"]`. | Automated test in `tests/integration/test_products_api.py`. |
| **AC-2** | **SPIA Regression Lock:** SPIA golden numbers remain **bit-for-bit identical** to 15 decimal digits (§7.1). Not a single digit changes. | `pytest tests/unit/test_spia_illustrative_engine.py tests/integration/test_m1_end_to_end.py`. |
| **AC-3** | **Pure Formula Functions:** 20 functions implemented in `backend/app/products/myga_lite/config.py` in strict topological order. Every function has expression text, plain-English explanation, AST/bytecode fingerprint, and is marked `illustrative=True`. | Unit tests in `tests/unit/test_myga_formulas.py`. |
| **AC-4** | **Cent-Level Worked Example Verification:** Unit tests reproduce the LifeLib MYGA worked example (anchor contract with $4,000 withdrawal at month 12) **to the cent** across all AV, MGSV, and surrender benefit columns (§7.2). | `pytest tests/unit/test_myga_worked_example.py` (no database required). |
| **AC-5** | **Actuarial Standards Compliance:** Independent tests confirm: (a) NAIC Model #805 87.5% premium floor accumulating at 2.80% minimum rate with gross withdrawal deductions; (b) linear MVA with symmetric charge cap $|\text{MVA}| \le \text{charge}$; (c) 10% free withdrawal exempt from charge and MVA; (d) VM-22 dynamic lapse with 50 bp buffer, exponents 2.0/2.5, and 90% shock lapse at guarantee reset; (e) 1% annuitization window; (f) cash-flow ledger composition. | Unit tests in `tests/unit/test_myga_actuarial_pitfalls.py`. |
| **AC-6** | **LifeLib Oracle Cross-Check ($\le \$0.000001$):** On the new-business anchor cell across all months to maturity (under Base and Rates Up scenarios), every ledger line (AV, MGSV, death claims, surrender claims, annuitization, net cash flow) matches the LifeLib `MYGA_US_S` model to within $\mathbf{\$0.000001}$ ($10^{-6}$). | `python tools/lifelib_crosscheck/myga_crosscheck.py` (§7.3). |
| **AC-7** | **Data Manager Per-Product Validation (E-2):** `data_manager_service.py` validates inforce files according to product type. Uploading a MYGA inforce file requires MYGA fields (`guarantee_period_years`, `declared_rate`, `gmir`, `gmsv_rate`, etc.) and does not demand SPIA's `monthly_payment`. SPIA validation remains unchanged. | `tests/integration/test_data_manager_myga.py`. |
| **AC-8** | **E2E Valuation Run via API:** Run Set "MYGA Illustrative × {Base, Rates Up 150bp}" on a 25-policy inforce block executes to `success` via `POST /v1/run-sets`. Parquet artifacts are created in `backend/artifacts/` with verified row counts and SHA-256 checksums. | `tests/integration/test_myga_end_to_end.py`. |
| **AC-9** | **Trace Explorer Lineage Chain:** Inspecting `surrender_benefit` for any policy-month produces the unbroken calculation chain: $AV \rightarrow \text{free amount} \rightarrow \text{excess} \rightarrow \text{MVA (capped)} \rightarrow \text{charge} \rightarrow \text{surrender value} \rightarrow \text{MGSV floor} \rightarrow \text{benefit}$. | `GET /v1/runs/{id}/trace` test. |
| **AC-10** | **Determinism & Performance:** Two identical runs produce bitwise-identical Parquet files. Projection of 25 policies $\times$ 480 months completes in $< 60\text{ seconds}$ locally. | Integration test assertion. |
| **AC-11** | **Frontend Contract & Documentation:** `docs/build/02_BACKEND_FRONTEND_CONTRACT.md` updated with Section F.9 covering MYGA variables, published outputs, units, and aggregation rules. | Review by Samuel Ayinla. |
| **AC-12** | **Clean Governance & Review Package:** Zero AI co-author lines. PR contains a generated review ZIP with `git status`, `git diff`, `git log`, and full test transcripts (§8). | PR inspection. |

---

## 3. Authoritative References & Mandatory Rules

### 3.1 Authoritative Hierarchy
1. **CAS (Consolidated Architecture Specification):**
   - **§3.1:** What "implemented" means (5 engineering rules).
   - **§3.3:** Synthetic data rules (all demo portfolios must be synthetic).
   - **§8.1:** Data ingestion, schema validation, and rejected records.
   - **§10.1 & §10.3:** Attempt-scoped Parquet storage, role separation, and immutable audit logs.
   - **§12 & §12.1:** Testing, governance, and immutable run package/manifest fingerprints.
2. **MP (SDLC Master Plan):**
   - **§2:** Definition of Done.
   - **§8.1:** Ingestion and mapping flexibility.
   - **§13:** Noah's ownership of imports, mapping, and the Run Center.

### 3.2 LifeLib Reference-Only Legal & Technical Boundaries
> [!CRITICAL]
> **LIFELIB IS REFERENCE-ONLY. NEVER COPY CODE, TEXT, OR CSV FILES.**
> The staging repository `lifelib-dev/lifelib-products` (commit `5d91f95`) is an **unlicensed draft**. Copying its implementation, docstrings, or data files into MentorAmp repositories is strictly prohibited.
> 
> **How we use it:**
> 1. Use the **mathematics** in LifeLib's MYGA technical notes as an independent technical specification.
> 2. Use its worked example and model output as an **external mathematical oracle / answer key**.
> 3. Write every line of MentorAmp code, docstrings, and comments **in our own words and architecture**.
> 4. Run LifeLib strictly via an external verification script (`tools/lifelib_crosscheck/myga_crosscheck.py`) in a temporary virtual environment at test time. Do not commit LifeLib into Git.

### 3.3 Database & Artifact Safety Rules
- **Shared Neon DB (`neondb`):** Under no circumstances may you run migrations, seeds, or destructive commands against the shared Neon database during this work. All testing must be conducted against disposable local/Docker PostgreSQL databases (e.g., `mentor_amp_wp2_test`).
- **Parquet Artifacts:** Never delete or overwrite existing artifacts in `backend/artifacts/`.
- **Git Attribution:** Never add `Co-Authored-By:` or AI attribution lines to Git commits or PR descriptions.

---

## 4. The 5-Year MYGA v0.1 Product Specification

We adopt the representative 5-year MYGA product configuration (based on standard U.S. fixed deferred annuity practice and LifeLib's anchor cell):

| Parameter | Specification | Technical Note |
|---|---|---|
| **Contract Form** | Single Premium Deferred Annuity (SPDA) | Initial premium $P = \$100,000$ (anchor cell). |
| **Guarantee Period ($n$)** | 5 Years (60 months) | Surrender charge period and MVA period match guarantee period. |
| **Credited Rate** | 4.45% effective annual (initial period) | Credited monthly in arrears at $r_m = (1 + 0.0445)^{1/12} - 1$. |
| **GMIR (Guaranteed Min Interest Rate)** | 0.25% annual floor | Statutory policy minimum rate for renewals. |
| **Renewal Mechanism** | `rollover` | At the end of each 5-year window, a 1-month fee-free window opens. Rate is redeclared as $\max(\text{GMIR}, \text{market\_rate} - \text{renewal\_spread})$, renewal surrender charges start, and the MVA reference yield is re-locked. |
| **Surrender Charges** | Initial: 9%, 8%, 7%, 6%, 5%<br>Renewal: 5%, 4%, 3%, 2%, 1% | Renewal charges capped by attained age: 4% at 94, 3% at 95, 2% at 96, 1% at 97, 0% at 98+. Charge is 0% during the renewal window month. |
| **Free Withdrawal** | 10% annual allowance | Year 1: $10\% \times \text{premium}$. Later years: $10\% \times \text{AV at previous contract anniversary}$. Free amount is **exempt from both surrender charge and MVA**. |
| **Market Value Adjustment (MVA)** | Linear formula with symmetric cap | $\mu = (i_0 - i_t) \times T$. $T = \text{years to contract year end} + \text{whole years left in MVA period}$. Symmetric cap: $|\text{MVA}| \le \text{surrender charge}$. $i_0 = 5.00\%$ at issue. |
| **NAIC Model #805 Floor (MGSV)** | Standard Nonforfeiture Law for Deferred Annuities | Initial value $87.5\% \times \text{premium}$, accumulating monthly at $(1 + 0.028)^{1/12}$. Withdrawals deducted **gross**. Contract charge = $\$0$. |
| **Surrender Benefit** | $\max(AV + \text{MVA} - \text{charge}, \text{MGSV})$ | **Strict composition order:** $AV \rightarrow \text{MVA} \rightarrow \text{charge} \rightarrow \text{floor}$. Floor is a lower bound, never a separate cash flow. |
| **Death Benefit** | $\max(AV, \text{surrender benefit})$ | No surrender charge, no MVA. Never less than surrender benefit. |
| **Mortality** | Synthetic Gompertz 2026 table | Identical lookup to SPIA (`SYNTH_MORT_GOMPERTZ_2026`). |
| **Base Lapse Rate** | 1.0% annual during guarantee period; **75.0% shock lapse** in the year following guarantee expiration. | Multiplied by VM-22 GMIR factor ($1.25$ at GMIR 0.25%), clamped to $[0.5\%, 90.0\%]$. Result: Years 1–5 = 1.25%, Year 6 = 90.0%, repeating every 5 years. |
| **Dynamic Lapse (VM-22)** | VM-22 §6.B.5 formulation | 50 bp buffer, exponent 2.0 during charge period, 2.5 thereafter, MVA factor 0.35. **Zero in Base scenario** (where market rate = credited rate). |
| **Partial Withdrawals** | 0 in standard valuation runs | Variable present for scenario and sensitivity overrides. |
| **Annuitization** | 1.0% of in-force during renewal window | 0 outside the 1-month renewal window. |
| **Expenses** | Acquisition: 2.0% of premium at issue (month 1). Maintenance: \$50/year (\$4.1667/month), inflating at 2.5% annually. | Applied to active in-force. |
| **Maturity** | Payout of account value at attained age 100. | Contract ends. |
| **Illustrative Reserve** | PV of future liability outgo at scenario discount rate (default 4.50%). | Published alongside cash surrender value. |

---

## 5. Topological Formula Architecture (The 20 Pure Functions)

Implement these 20 functions as pure, side-effect-free, deterministic Python functions in `backend/app/products/myga_lite/config.py`:

```
               [1. Time & Window: gp_index, year_in_gp, in_window]
                                      │
               ┌──────────────────────┴──────────────────────┐
               ▼                                             ▼
       [2. credited_rate]                            [3. i0_locked]
               │                                             │
               ▼                                             │
  [4. av_open, mgsv_open]                                    │
               │                                             │
               ▼                                             │
    [5. free_wd_available]                                   │
               │                                             │
               ▼                                             │
    [6. av_before_credit]                                    │
               │                                             │
        ┌──────┴──────┐                                      │
        ▼             ▼                                      │
  [7. av_close]  [8. mgsv_close]                             │
        │             │                                      │
        ▼             │                                      │
[9. surrender_charge_rate]                                   │
        │             │                                      │
        ▼             │                                      │
[10. surrender_excess]                                       │
        │             │                                      │
        ▼             │                                      │
[11. surrender_charge]│                                      │
        │             │                                      │
        ├─────────────┼──────────────────┐                   │
        │             │                  ▼                   ▼
        │             │           [12. mva_term] ──► [13. mva_rate, mva]
        │             │                  │                   │
        ▼             ▼                  └───────────┬───────┘
[14. surrender_value, surrender_benefit] <───────────┘
        │
        ├─────────────────────────────┐
        ▼                             ▼
[15. death_benefit]       [16. q, lapse, annuitization rates]
        │                             │
        └──────────────┬──────────────┘
                       ▼
            [17. inforce rollforward]
                       │
                       ▼
              [18. Cash-flow Ledger]
                       │
                       ▼
          [19. liability_outgo & PV]
                       │
                       ▼
                 [20. reserve]
```

### 5.1 Function Details & Expressions

1. **`gp_index, year_in_gp, in_window`**:
   $$\text{gp\_index} = \lfloor(y - 1) / n\rfloor + 1$$
   $$\text{year\_in\_gp} = y - n \times (\text{gp\_index} - 1)$$
   $$\text{in\_window} = (\text{gp\_index} > 1) \land (\text{year\_in\_gp} == 1) \land (\text{month\_in\_year} == 0)$$
2. **`credited_rate`**:
   In GP 1: $\text{declared\_rate}$. At start of later GPs: $\max(\text{gmir}, \text{market\_rate} - \text{renewal\_spread})$. Otherwise: previous month's value.
3. **`i0_locked`**:
   In GP 1: $\text{mva\_ref\_yield\_at\_issue}$. Re-locked to $\text{mva\_ref\_yield}$ at each renewal window.
4. **`av_open, mgsv_open`**:
   Month 1: input state (`account_value`, `mgsv_value`). Month $k > 1$: previous month's `av_close`, `mgsv_close`.
5. **`free_wd_available`**:
   Reset in anniversary month ($\text{month\_in\_year} == 0$) to $0.10 \times (\text{premium if } y == 1 \text{ else } \text{av\_open})$. Reduced by withdrawals taken.
6. **`av_before_credit`**:
   $$\text{av\_before\_credit} = \text{av\_open} - \text{withdrawal}$$
7. **`av_close`**:
   $$\text{av\_close} = \text{av\_before\_credit} \times (1 + \text{credited\_rate})^{1/12}$$
8. **`mgsv_close`**:
   $$\text{mgsv\_close} = (\text{mgsv\_open} - \text{withdrawal} - \text{contract\_charge}/12) \times (1 + \text{gmsv\_rate})^{1/12}$$
9. **`surrender_charge_rate`**:
   Lookup by $(\text{schedule}, \text{year\_in\_gp})$. If in renewal, apply age cap: $\min(\text{table\_rate}, \text{cap}(age))$. In window: $0.0$.
10. **`surrender_excess`**:
    $$\text{surrender\_excess} = \max(0.0, \text{av\_close} - \text{free\_wd\_available})$$
11. **`surrender_charge`**:
    $$\text{surrender\_charge} = \text{surrender\_charge\_rate} \times \text{surrender\_excess}$$
12. **`mva_term`**:
    $$T = \frac{12y - p}{12} + (n \times \text{gp\_index} - y)$$
13. **`mva_rate, mva`**:
    $$\mu = (i_{0,\text{locked}} - \text{mva\_ref\_yield}) \times T \quad (\mu = 0 \text{ in window})$$
    $$\text{mva} = \text{clamp}(\mu \times \text{surrender\_excess}, -\text{surrender\_charge}, +\text{surrender\_charge})$$
14. **`surrender_value, surrender_benefit`**:
    $$\text{surrender\_value} = \text{av\_close} + \text{mva} - \text{surrender\_charge}$$
    $$\text{surrender\_benefit} = \max(\text{surrender\_value}, \text{mgsv\_close})$$
15. **`death_benefit`**:
    $$\text{death\_benefit} = \max(\text{av\_close}, \text{surrender\_benefit})$$
16. **`q_monthly, lapse_rate_annual, lapse_monthly, annuitization_rate`**:
    - Mortality: $q_m = 1 - (1 - q_x)^{1/12}$ from synthetic Gompertz.
    - Base lapse $w_{\text{base}}$: $1.0\%$ in guarantee, $75.0\%$ in shock year (year $6, 11, \dots$).
    - Annual lapse $w = \text{clamp}(w_{\text{base}} \times G_{\text{factor}} + \text{dynamic\_lapse}, 0.5\%, 90.0\%)$.
    - Monthly lapse $w_m = 1 - (1 - w)^{1/12}$.
    - Annuitization: $1.0\%$ if $\text{in\_window}$, else $0.0$.
17. **`inforce_open, inforce_close`**:
    $\text{inforce\_open} = 1.0$ at valuation date.
    $$\text{inforce\_close} = \text{inforce\_open} \times (1 - a)(1 - q_m)(1 - w_m)$$
18. **`ledger` (Monthly Cash Flows)**:
    - $\text{death\_claims} = \text{inforce\_open} \times (1 - a) \times q_m \times \text{death\_benefit}$
    - $\text{surrender\_claims} = \text{inforce\_open} \times (1 - a) \times (1 - q_m) \times w_m \times \text{surrender\_benefit}$
    - $\text{annuitization\_transfer} = \text{inforce\_open} \times a \times \text{av\_before\_credit}$
    - $\text{maturity\_benefit} = \text{inforce\_open} \times \text{av\_close}$ at attained age 100.
    - $\text{maintenance\_expense} = \text{inforce\_open} \times (50.0 / 12.0) \times 1.025^{y - 1}$
    - $\text{premium} = P$ at month 1 (new business only); $\text{commission} = 0.02 \times P$ at month 1.
19. **`liability_outgo, pv_liability_outgo`**:
    Sum of benefit claims and expenses, discounted monthly at scenario rate.
20. **`reserve`**:
    Backward summation of `pv_liability_outgo` from projection horizon to valuation date.

---

## 6. Engineering Requirements & Platform Integration

### 6.1 Platform Components to Create/Modify
| ID | Component | Location | Action |
|---|---|---|---|
| **E-1** | Product Registry | `backend/app/products/registry.py`<br>`backend/app/products/myga_lite/` | Create `myga_lite/config.py` and `myga_lite/golden.py`. Register MYGA in `register_all_products()`. `GET /v1/system/status` must return `["SPIA", "MYGA"]`. |
| **E-2** | Data Manager Per-Product Validation | `backend/app/services/data_manager_service.py` | Replace hardcoded `INFORCE_FIELDS` with dynamic schema lookup keyed by `product_type`. Validate MYGA fields when `product_type == "MYGA"`. |
| **E-3** | Declarative Model Definition | `backend/app/products/myga_lite/config.py` | Export `MODEL_DEFINITION` containing all variables, formulas, formula groups, and published outputs. Add model seed/loader script. |
| **E-4** | Lookup Keys & Constraints | `backend/app/core/formula_engine/` | Ensure formula engine handles multi-part integer keys `(schedule, year_in_gp)` and validates `non_increasing` constraints on `inforce_close`. |
| **E-5** | Synthetic Data Portfolio | `samples/myga/` | Create 25 synthetic in-force MYGA policies (`synthetic_myga_inforce.csv`), 1 new-business anchor policy (`anchor_policy.csv`), surrender charge table (`myga_surrender_charge.csv`), and age cap table (`myga_sc_age_cap.csv`). README must state `SYNTHETIC`. |

### 6.2 Data Manager Dynamic Inforce Schema (E-2 Detail)
In `backend/app/services/data_manager_service.py`, refactor `_validate_inforce` to select required fields dynamically:

```python
PRODUCT_INFORCE_SCHEMAS = {
    "SPIA": {
        "policy_id": "string",
        "product_type": "string",
        "issue_date": "date",
        "issue_age": "integer",
        "gender": "string",
        "premium": "number",
        "monthly_payment": "number",
    },
    "MYGA": {
        "policy_id": "string",
        "product_type": "string",
        "issue_date": "date",
        "issue_age": "integer",
        "gender": "string",
        "premium": "number",
        "guarantee_period_years": "integer",
        "declared_rate": "number",
        "gmir": "number",
        "gmsv_rate": "number",
        "mva_ref_yield_at_issue": "number",
        "account_value": "number",
        "mgsv_value": "number",
        "free_wd_base": "number",
    },
}
```
If a row has an unrecognized `product_type`, flag an error issue `INVALID_PRODUCT_TYPE`. Ensure SPIA validation continues to pass 100% of existing tests.

---

## 7. Actuarial Pitfalls & Test Execution Protocol

### 7.1 Regression Check: SPIA Golden Numbers
Before writing any MYGA code and before opening your PR, run the SPIA regression suite:
```powershell
pytest backend/tests/unit/test_spia_illustrative_engine.py backend/tests/integration/test_m1_end_to_end.py
```
**Required SPIA Golden Values (must match to 15 decimal digits):**
- **Base Reserve (600m, 4.5%):** `4,889,731.244841259`
- **Low Interest Rate Reserve (600m, 3.0%):** `5,541,437.408305269`
- **Total Undiscounted Payments:** `7,419,301.206836598`
- **Base 24-Month Reserve:** `893,043.9157255866`
- **Low Rate 24-Month Reserve:** `906,298.662195956`
- **Trace `pv_expected_payment` (`SPIA-0001`, m12, Base):** `1,533.2093195295497`

### 7.2 Unit Test: LifeLib Worked Example (Cent-Level Precision)
Test the anchor policy: Male 60, $\$100,000$ single premium, $n=5$, declared rate $4.45\%$, GMSV rate $2.80\%$, charges $9/8/7/6/5\%$, free withdrawal $10\%$. A free withdrawal of $\$4,000$ occurs at month $t=12$.

Target values from the technical notes:
- $t=0$: $AV = \$100,000.00$, $\text{Withdrawal} = \$0.00$, $AV_{\text{close}} = \$100,363.48$, $MGSV = \$87,701.59$
- $t=11$: $AV_{\text{open}} = \$104,071.72$, $AV_{\text{close}} = \$104,450.00$, $MGSV = \$89,950.00$
- $t=12$: $AV_{\text{open}} = \$104,450.00$, $\text{Withdrawal} = \$4,000.00$, $AV_{\text{close}} = \$100,815.11$, $MGSV = \$86,148.02$
- $t=23$: $AV_{\text{open}} = \$104,540.04$, $AV_{\text{close}} = \$104,920.03$, $MGSV = \$88,356.60$
- $t=29$ ($y=3$, charge $7\%$, $i_0=5.00\%$, $i_t=6.50\%$): Free allowance $=\$10,492.00$, Excess $=\$96,737.09$, Charge $=\$6,771.60$, $T=2.5\text{ yrs}$, $\mu=-3.75\%$, $\text{MVA}=-\$3,627.64$ (cap not binding), Surrender Value $=\$96,829.85$, $MGSV=\$89,585.05$, **Surrender Benefit $=\$96,829.85$**.
- $t=5$, Stress Scenario ($y=1$, charge $9\%$, $i_t=10.00\%$): $AV=\$102,200.78$, Free allowance $=\$10,000.00$, Excess $=\$92,200.78$, Charge $=\$8,298.07$, $T=4.5\text{ yrs}$, $\mu=-22.5\%$, raw $\text{MVA}=-\$20,745.18 \rightarrow$ **capped at $-\$8,298.07$**, Surrender Value $=\$85,604.64$, $MGSV=\$88,716.54$, **Surrender Benefit $=\$88,716.54$** (MGSV floor binds, adding $\$3,111.90$).

### 7.3 Independent LifeLib Cross-Check Oracle ($\le \$0.000001$)
Build and run the standalone verification script in `tools/lifelib_crosscheck/myga_crosscheck.py` (following the exact pattern of `reviews/lifelib_crosscheck_2026-10-15/spia_crosscheck.py`).

**Execution Command:**
```powershell
python -I tools/lifelib_crosscheck/myga_crosscheck.py <path_to_lifelib_products>/lifelib/libraries/uslib/products/fixed_deferred_annuity . scratch/lifelib_myga_work
```
**Verification Threshold:**
Across all months to maturity for both Base ($i_t=5.00\%$) and Rates Up ($i_t=6.50\%$), the following outputs must agree with LifeLib's `MYGA_US_S` model to within $\mathbf{\$0.000001}$:
- Active in-force count
- Premium and withdrawal cash flows
- Death, surrender, annuitization, and maturity claims
- Commission and maintenance expenses
- Net cash flows
- Account value and MGSV floor

### 7.4 Nine Actuarial Pitfalls Noah Must Explicitly Guard Against
In `tests/unit/test_myga_actuarial_pitfalls.py`, write explicit unit tests proving each pitfall is prevented:
1. **Order of Composition:** MVA is calculated on excess and applied **before** deducting the surrender charge. If charge is deducted before MVA, it reduces the base for MVA, which is actuarially incorrect.
2. **Floor Sequencing:** The MGSV floor must be evaluated **after** MVA and surrender charge. Applying the floor before MVA destroys the statutory downside protection.
3. **Free Withdrawal Exemption:** The 10% free withdrawal allowance must be exempt from **both** the surrender charge and the MVA.
4. **Gross Deduction from Floor:** Any withdrawal is deducted **gross** (full withdrawal amount) from the MGSV floor, without any surrender charge offset.
5. **Statutory Floor Rate Limit:** NAIC Model #805 sets a minimum statutory accumulation rate of $0.15\%$ (15 bp), not $1.00\%$.
6. **Renewal Charge Reset:** In Year 6 (and every subsequent 5-year cycle), surrender charges reset according to the renewal schedule, and the duration clock restarts.
7. **Window Fee Waiver:** During the 1-month window at the end of each guarantee period, both the surrender charge and the MVA are strictly $0.00$.
8. **Shock Lapse Cadence:** The VM-22 lapse rate surges to $90.0\%$ in Year 6 (contract year 6), Year 11, etc., driven by guarantee expiration.
9. **Internal Values vs. Cash Flows:** Interest credits, surrender charges, MVA adjustments, and MGSV movements are internal account adjustments. They must **never** appear as cash flow rows in the financial ledger.

---

## 8. Deliverables & Handoff Checklist

When your work is complete, prepare your pull request and handoff package as follows:

1. **Feature Branch & PR:**
   - Create and push to `origin/feat/m2b-myga-product` (branched from `main` at `fffc396`).
   - Open a Pull Request into `main`. **No self-merging.** Samuel Ayinla will conduct the technical and actuarial review.
   - **Zero AI Attribution:** Ensure `git log` contains no `Co-Authored-By:` lines.
2. **Review ZIP Package:**
   Generate a review package for external AI verification containing:
   - `git_status.txt` (`git status`)
   - `git_diff.patch` (`git diff origin/main...HEAD`)
   - `git_log.txt` (`git log -n 10 --oneline`)
   - `test_results.txt` (full pytest run output)
   - `lifelib_crosscheck_myga.json` (oracle comparison output proving $\le \$0.000001$ agreement)
   - Store as `reviews/M2B_MYGA_PR_REVIEW.zip`.
3. **New Frozen Golden Numbers:**
   Record the new frozen MYGA in-force golden numbers (Base reserve, Rates Up reserve, undiscounted outgo, and trace verification point) in `backend/app/products/myga_lite/golden.py` and document them in the PR description.
4. **Contract Updates:**
   Submit accompanying updates to `docs/build/02_BACKEND_FRONTEND_CONTRACT.md` (§F.9) documenting MYGA published outputs for Sam Yung's frontend screens.
