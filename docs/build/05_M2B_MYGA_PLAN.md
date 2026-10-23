# M2 Part B: the MYGA model (and the SPIA cross-check against LifeLib)

| | |
|---|---|
| **Owner** | Samuel Ayinla (engine, formulas, model). Noah Enemali owns one piece: per-product inforce schemas in the Data Manager (§7, E-2) |
| **Reviewer** | Samuel Ayinla (Technical Lead) |
| **Milestone** | M2 Part B (`01_HOW_WE_WORK_AND_MILESTONES.md` §3: "MYGA illustrative model (account-value rollforward)") |
| **Reference material** | LifeLib U.S. library, as Eko suggested on 7 October: `lifelib-dev/lifelib-products`, folder `lifelib/libraries/uslib/products/fixed_deferred_annuity/` (MYGA) and `immediate_annuity/` (SPIA), commit `5d91f95` (2026-09-20) |
| **Open questions it answers** | Q-MYGA-1, 2, 3 and 5 in `03_QUESTIONS_AND_DOCUMENT_REQUESTS_FOR_PRODUCT_OWNER.md` now have a public reference answer. Q-MYGA-4 and Q-MYGA-6 (which reserve basis) are still open |
| **Base commit** | `main` = `fffc396` (WP2 merged; shared Neon DB upgraded to `d4f7a9c2e681` on 2026-10-15) |
| **Written** | 2026-10-15 |

---

## 1. The task in one paragraph

Build MentorAmp's second product, a **5-year MYGA** (multi-year guaranteed annuity, a fixed deferred annuity). It covers:
- an account value that earns a declared rate;
- surrender charges, a 10% free withdrawal and a market value adjustment (MVA);
- the Model #805 minimum surrender value floor;
- deaths, lapses (with the big lapse "shock" when the guarantee period ends) and annuitization;
- expenses and a cash-flow ledger;
- an illustrative reserve.

We copy no LifeLib code: we use its **MYGA technical notes as the specification** and its **worked example and model as the answer key**. At the end, a MYGA run goes through the same path as SPIA (upload → run → results → trace → compare). LifeLib's MYGA model, run on our inputs, produces the same cash flows to rounding noise, exactly as it now does for SPIA (§2).

## 2. Already done: SPIA cross-checked against LifeLib (2026-10-15)

**Method.** LifeLib's SPIA model (`SPIA_US_S`) was run **unchanged** (formulas untouched) on a copy whose input files were replaced with ours:
- our synthetic Gompertz mortality table;
- zero mortality improvement;
- our 25 synthetic policies: single life, life only, monthly in arrears, no cost-of-living increases.

One setting was changed: LifeLib's A/E mortality factor (1.084) was set to 1.0, because we apply none.

LifeLib projects from each policy's issue date; we project from the valuation date (2026-12-31). So our month *k* was compared with LifeLib month *m_if + k − 1*, divided by LifeLib's probability of being alive at the valuation date. Here *m_if* is the months in force at valuation.

**Result (all 25 policies × 600 months):**

| Check | Largest difference |
|---|---|
| Expected payment, any policy, any month | $0.0000000000032 (relative 2.5 × 10⁻¹⁵) |
| Survival probability | 1.2 × 10⁻¹⁵ |
| Base reserve (4.5%): LifeLib cash flows discounted our way vs our golden 4,889,731.244841259 | $0.00000002 |
| Low Interest Rate reserve (3.0%) vs our golden 5,541,437.408305269 | $0.00000002 |
| Per-policy PV, Base | $0.0000000002 |

**What this proves.** An independent implementation by a third party, given the same inputs, produces exactly our cash flows and reserves. It checks:
- the monthly mortality conversion;
- the survival recursion;
- the timing (in arrears);
- age stepping on each policy anniversary measured from the issue date;
- starting from the valuation date part-way through a policy;
- the discounting.

**What it does not prove.** The assumptions are still synthetic. Note that LifeLib's own tables are also labelled "illustrative, NOT 2012 IAM Basic". The formulas also still need the Actuarial Product Owner's approval. So SPIA stays labelled **ILLUSTRATIVE — NOT ACTUARIALLY APPROVED**, with this evidence attached: *"mechanics independently cross-checked against LifeLib SPIA_US_S @ 5d91f95"*. M3 approval still needs Eko's reference case.

**Record:** `reviews/lifelib_crosscheck_2026-10-15/` (git-ignored) contains `spia_crosscheck.py`, `spia_crosscheck.json` and `spia_crosscheck_per_policy.csv`. To re-run, see §9.4.

## 3. Rules for using LifeLib

1. **Reference, never copy.** `lifelib-products` is a draft staging repo with **no licence file**. The main `lifelib` repo is MIT, but this library has not been merged into it yet. Until it is merged, or the maintainer gives written permission:
   - do not copy LifeLib code, documentation text or CSV files into our repos;
   - write our own code and our own words from the mathematics;
   - cite the technical notes as the source of each mechanic.
2. **Numbers are fine to use as test targets.** Worked-example values (for example, `AV(11) = 104,450.00`) are results of public formulas. Cite them in our tests as "LifeLib MYGA technical notes, worked example".
3. **Running LifeLib is local only.** The cross-check fetches LifeLib at a pinned commit into a scratch folder at run time. It is never committed or shipped.
4. **Synthetic data only** (CAS §3.3). LifeLib's parameter values come from public product documents, which is fine for an illustrative demo product. Label everything **ILLUSTRATIVE — NOT ACTUARIALLY APPROVED**.
5. Eko said "not necessarily to copy these models". This plan uses them as a **specification and an answer key**, which is what he asked for.

## 4. What must be true at the end (acceptance criteria)

| # | At the end of this task… |
|---|---|
| **AC-1** | A MYGA product is registered next to SPIA. `GET /v1/system/status` lists `["SPIA", "MYGA"]`, and the SPIA golden numbers (§9.1) are unchanged to the last digit. |
| **AC-2** | Every MYGA formula is a pure function with its expression text and a plain-English explanation, labelled illustrative (same pattern as `spia_lite/config.py`). |
| **AC-3** | Unit tests reproduce the LifeLib MYGA worked example to the cent, with no database (§9.2). |
| **AC-4** | Run through the engine, a **new-business** MYGA policy identical to LifeLib's anchor cell gives every ledger line equal to LifeLib's within $0.000001 for every month to maturity, in both the base and stress rate scenarios (§9.3). |
| **AC-5** | A synthetic **in-force** MYGA block (25 policies, valued 2026-12-31) can be uploaded through the Data Manager, validated, committed and approved with **MYGA's own field list**. SPIA uploads still validate exactly as before. |
| **AC-6** | A Run Set "MYGA Illustrative × {Base, Rates Up}" runs to `success` through `POST /v1/run-sets`. Results, aggregates, CSV export, trace and comparison all work, and the numbers are recorded as MYGA golden numbers. |
| **AC-7** | The trace of `surrender_benefit` for one policy-month shows the full chain: account value → free amount → excess → MVA (with cap) → surrender charge → surrender value → Model #805 floor → benefit, with every input value and source. |
| **AC-8** | The LifeLib cross-check harness for the in-force block (same method as §2) agrees to ≤ $0.000001 per ledger line. |
| **AC-9** | Same inputs give the same outputs (determinism), and 25 policies × 480 months finish in under 60 s locally. |
| **AC-10** | Docs updated: `02_BACKEND_FRONTEND_CONTRACT.md` §F gets a MYGA section (variables, outputs, units), and Sam gets the MYGA output list for the screens (they are designed around a MYGA model). |

## 5. The MYGA v0.1 product

We adopt LifeLib's representative 5-year MYGA, its "anchor cell" configuration. Values marked **[std]** are LifeLib's own modelling choices, not sourced facts.

| Item | v0.1 value |
|---|---|
| Premium | Single premium (anchor: $100,000) |
| Guarantee period *n* | 5 years; surrender-charge and MVA periods equal it |
| Declared rate | 4.45% effective annual in the first guarantee period, credited monthly at `(1.0445)^(1/12)` |
| Minimum guaranteed rate (GMIR) | 0.25% |
| Renewal | `rollover` **[std]**: at each guarantee-period end there is a 1-month window (no charge, no MVA). Then the rate is redeclared as `max(GMIR, market_rate − renewal_spread)` with spread 0, a renewal charge schedule starts, and the MVA reference yield is re-locked |
| Surrender charge | Initial 9, 8, 7, 6, 5%; renewal 5, 4, 3, 2, 1%; renewal charge capped by attained age (4% at 94 … 0% at 98+) |
| Free withdrawal | 10% of premium in year 1, 10% of the account value at the last anniversary after that. Free amount exempt from **both** charge and MVA **[std]** |
| MVA | Linear: `μ = (i0 − it) × T`, with `T` = years to the end of the contract year plus whole years left in the MVA period. Symmetric cap `|MVA| ≤ surrender charge` **[std]**. `i0` = 5.00% at issue **[std]**; `it` comes from the scenario |
| Minimum surrender value (Model #805) | Starts at 87.5% of premium, accumulates at 2.80%. Withdrawals deducted gross; contract charge $0 |
| Surrender benefit | `max(AV + MVA − charge, MGSV)`, composed in exactly this order: account value → MVA → charge → floor |
| Death benefit | Account value, no charge, no MVA, never below the surrender benefit |
| Mortality | Our synthetic Gompertz table (same as SPIA). LifeLib also uses a plain table lookup here |
| Base lapse | 1% a year inside a guarantee period, 75% in the contract year after it ends (the shock). Multiplied by the VM-22 GMIR factor (1.25 at a GMIR of 0.25%), then clamped to [0.5%, 90%]. So years 1–5 run at 1.25%, year 6 at 90%, and the pattern repeats every 5 years |
| Dynamic lapse | VM-22 §6.B.5 form (buffer 50 bp; exponent 2.0 during the charge period, 2.5 after; MVA lapse factor 0.35 **[std]**). **Exactly zero in the Base scenario**, because market rate = credited rate |
| Partial withdrawals | 0 in the base run (variable present so tests can set one) |
| Annuitization | 1% of in-force in each guarantee-period-end window, 0 otherwise **[std]** |
| Expenses | Commission 2% of premium at issue (new business only); maintenance $50 a year, paid monthly, inflating 2.5% a year **[std]** |
| Maturity | The account value is paid out at attained age 100 |
| Reserve | **Illustrative only:** PV of future liability outgo (benefits + expenses) at the scenario discount rate (default 4.50%), using the same valuation step as SPIA. The cash surrender value is published next to it. CARVM / VM-22 / GAAP wait for Eko's answer to Q-MYGA-4 and Q-MYGA-6 |

**Not in v0.1 (switches LifeLib has; add later if Eko wants them):**
- the geometric and declared-differential MVA families;
- the other four MVA cap rules;
- `annual_redeclare` renewal;
- the `net_of_charges` floor convention and the `interest_only` free withdrawal;
- IRC §72 tax basis;
- real 2012 IAM / G2 / VM-22 tables (licence question, D3).

## 6. The model in MentorAmp terms

### 6.1 Time
Our engine numbers months *k* = 1, 2, … from the valuation date, and `policy_month` *p* = months in force + *k*. LifeLib's month index is *t* = *p* − 1, its contract year is *y* = `policy_year`, and `month_in_year = (p − 1) mod 12` (0 = anniversary month). Elective events happen at the **start** of the month and interest is credited at the **end**. Deaths, then lapses, are decremented at the end, on the post-interest values.

### 6.2 Inputs (inforce columns, provisional until the data dictionary arrives)
`policy_id`, `product_type` (= MYGA), `issue_date`, `issue_age`, `gender`, `premium`, `guarantee_period_years`, `declared_rate`, `gmir`, `gmsv_rate`, `mva_ref_yield_at_issue`, and the **state at valuation**:
- `account_value`
- `mgsv_value`
- `free_wd_base` (premium in year 1, otherwise the account value at the last anniversary)

v0.1 data rule: every in-force policy is still in its first guarantee period at valuation, with no past withdrawals. The data generator then derives the valuation state exactly from the issue date. For a new-business policy, *m_if* = 0 and the state equals the premium.

### 6.3 Tables and scenario variables
- **Tables** (via the Data Manager):
  - synthetic mortality (reuse SPIA's file);
  - `MYGA_SURRENDER_CHARGE` keyed by (schedule 1 = initial / 2 = renewal, year in schedule);
  - `MYGA_SC_AGE_CAP` keyed by age.
- **Scenario-overridable variables:** `mva_ref_yield` (*it*), `market_rate` (*MR*), `renewal_spread`, `discount_rate_annual`, plus each behaviour parameter so we can run sensitivities.
- **Demo scenarios:**
  - **Base:** *it* = 5.00% = *i0*, so the MVA is 0; *MR* = credited rate, so the dynamic lapse is 0.
  - **Rates Up 150 bp:** *it* = 6.50%, *MR* = 5.95%.
- **Cross-check scenarios** copy LifeLib's: base *it* = 6.50% and stress *it* = 10.00%, both with *MR* = 4.45%.

### 6.4 Formulas (monthly, in dependency order)
Write each formula as one registered pure function, in our own code and words.

| # | Output | Expression |
|---|---|---|
| 1 | `gp_index`, `year_in_gp`, `in_window` | `gp_index = (y − 1) // n + 1`; `year_in_gp = y − n(gp_index − 1)`; `in_window = gp_index > 1 and year_in_gp = 1 and month_in_year = 0` |
| 2 | `credited_rate` | GP 1: `declared_rate`; at the start of each later GP: `max(gmir, market_rate − renewal_spread)`; otherwise the previous month's value |
| 3 | `i0_locked` | GP 1: `mva_ref_yield_at_issue`; re-locked to `mva_ref_yield` at each renewal |
| 4 | `av_open`, `mgsv_open` | Month 1: the valuation-state inputs; then the previous month's `av_close` / `mgsv_close` |
| 5 | `free_wd_available` | Reset to `0.10 × (premium if y = 1 else av_open)` in the anniversary month, reduced by free withdrawals taken |
| 6 | `av_before_credit` | `av_open − withdrawal` |
| 7 | `av_close` | `av_before_credit × (1 + credited_rate)^(1/12)` |
| 8 | `mgsv_close` | `(mgsv_open − withdrawal − contract_charge/12) × (1 + gmsv_rate)^(1/12)` |
| 9 | `surrender_charge_rate` | Table by (schedule, `year_in_gp`), age-capped in renewal; 0 in the window |
| 10 | `surrender_excess` | `max(0, av_close − free_wd_available)` |
| 11 | `surrender_charge` | `surrender_charge_rate × surrender_excess` |
| 12 | `mva_term` | `(12y − p)/12 + (n × gp_index − y)` |
| 13 | `mva_rate`, `mva` | `μ = (i0_locked − mva_ref_yield) × mva_term`, 0 in the window; `mva = clamp(μ × excess, −charge, +charge)` |
| 14 | `surrender_value`, `surrender_benefit` | `av_close + mva − surrender_charge`; `max(surrender_value, mgsv_close)` |
| 15 | `death_benefit` | `max(av_close, surrender_benefit)` |
| 16 | `q_monthly`, `lapse_rate_annual`, `lapse_monthly`, `annuitization_rate` | Mortality as SPIA; lapse = clamp(base(y) × G + dynamic term, 0.5%, 90%), monthly = `1 − (1 − w)^(1/12)`; annuitization 1% in the window |
| 17 | `inforce_open`, `inforce_close` | 1.0 at valuation (we value given in force); `close = open × (1 − a)(1 − q)(1 − w)`, non-increasing |
| 18 | Ledger | `death_claims = open(1 − a)q × death_benefit`; `surrender_claims = open(1 − a)(1 − q)w × surrender_benefit`; `annuitization_transfer = open × a × av_before_credit`; `maturity_benefit` at age 100; `maintenance_expense = open × (50/12) × 1.025^(y − 1)`; `premium` and `commission` at *p* = 1 only |
| 19 | `liability_outgo`, `pv_liability_outgo` | Sum of the benefit and expense lines; discounted like SPIA |
| 20 | `reserve` | Valuation step on `liability_outgo` (illustrative) |

**Internal amounts are not cash flows.** Interest, the charge, the MVA and the floor movement only change *amounts*. A binding floor raises `surrender_benefit`; it is never a separate line.

### 6.5 Published outputs (for Results and Sam's screens)
- `account_value`
- `cash_surrender_value` (= `surrender_benefit`)
- `mgsv`
- `inforce`
- `death_claims`
- `surrender_claims`
- `liability_outgo`
- `reserve`
- `credited_rate`

Aggregation:
- `account_value`, `cash_surrender_value` and `mgsv` are **in-force weighted**, then summed.
- `reserve` is end of period.
- The flows are summed.

## 7. Platform changes needed (small)

| # | Change | Owner |
|---|---|---|
| **E-1** | `app/products/registry.py`: register MYGA functions next to SPIA; new folder `app/products/myga_lite/` (fill the empty `shared_modules` files `account_value.py`, `surrender_charge.py`, `withdrawal.py`, `death_benefit.py`, `fixed_crediting.py` only if the code is truly shared) | Samuel |
| **E-2** | Data Manager: replace the single hard-coded `INFORCE_FIELDS` (it requires SPIA's `monthly_payment`, so a MYGA file fails today) with **per-product field lists**, chosen by `product_type` or the model version. SPIA behaviour must not change | **Noah** |
| **E-3** | Seed or definition load for the MYGA model (model, version, variables, formulas, published outputs), same path as SPIA. Data comes in through the Data Manager | Samuel |
| **E-4** | Confirm that table lookups accept integer keys produced by formulas (schedule 1/2, `year_in_gp`), and that `non_increasing` works on `inforce_close` | Samuel |
| **E-5** | Synthetic MYGA data generator in `samples/myga/`: 25 in-force policies plus one new-business anchor policy, with a README stating SYNTHETIC | Samuel |

No new database tables are expected. If one turns out to be needed, it is a new Alembic revision; never edit a pushed one.

## 8. Order of work

1. **Formulas and unit tests first** (no database): items 1–20 as pure functions; reproduce the worked example (§9.2).
2. **Model definition and engine run** on the new-business anchor policy; build the **MYGA cross-check harness** (copy the SPIA method) and get every ledger line equal to LifeLib (AC-4).
3. **In-force block:** generator, Noah's E-2, upload, run Base and Rates Up, record the golden numbers (AC-5, AC-6, AC-8).
4. **API, trace and contract:** AC-7, AC-10; give Sam the MYGA output list.
5. **Report** (§10).

## 9. What to test and the expected results

### 9.1 SPIA golden numbers (must not change by a single digit)
- Base reserve **4,889,731.244841259**
- Low Interest Rate **5,541,437.408305269**
- Total payments **7,419,301.206836598**
- Trace SPIA-0001, month 12, `pv_expected_payment` = **1,533.2093195295497**

### 9.2 MYGA worked example (LifeLib MYGA technical notes, "Worked example")
Male 60, $100,000, *n* = 5, 4.45%, GMSV 2.80%, charges 9/8/7/6/5, free 10%. A free withdrawal of $4,000 at the start of LifeLib month *t* = 12. Values to the cent; *t* = 0-based LifeLib month = our `policy_month − 1`.

| *t* | AV start | Withdrawal | AV after withdrawal | AV end | MGSV end |
|---|---|---|---|---|---|
| 0 | 100,000.00 | 0 | 100,000.00 | 100,363.48 | 87,701.59 |
| 1 | 100,363.48 | 0 | 100,363.48 | 100,728.28 | 87,903.65 |
| 2 | 100,728.28 | 0 | 100,728.28 | 101,094.40 | 88,106.17 |
| 11 | 104,071.72 | 0 | 104,071.72 | 104,450.00 | 89,950.00 |
| 12 | 104,450.00 | 4,000.00 | 100,450.00 | 100,815.11 | 86,148.02 |
| 23 | 104,540.04 | 0 | 104,540.04 | 104,920.03 | 88,356.60 |
| 29 | 106,840.74 | 0 | 106,840.74 | 107,229.09 | 89,585.05 |

Exact checks at full precision (relative 10⁻¹²):
- `AV(11) = 100,000 × 1.0445`
- `AV(23) = 100,450 × 1.0445`
- `MGSV(11) = 87,500 × 1.028`
- `MGSV(23) = (89,950 − 4,000) × 1.028`

The $4,000 is entirely free: the year-2 allowance is 10,445.00, and 6,445.00 remains after it.

**Surrender at *t* = 29** (year 3, charge 7%, *i0* = 5.00%, *it* = 6.50%):

| Free amount | Excess | Charge | MVA term | μ | MVA | Surrender value | Floor | **Benefit** |
|---|---|---|---|---|---|---|---|---|
| 10,492.00 | 96,737.09 | 6,771.60 | 2.5 | −3.75% | −3,627.64 (cap not binding) | 96,829.85 | 89,585.05 | **96,829.85** |

**Surrender at *t* = 5, stress** (year 1, charge 9%, *it* = 10.00%):

| AV | Free amount | Excess | Charge | MVA term | μ | MVA raw → capped | Surrender value | Floor | **Benefit** |
|---|---|---|---|---|---|---|---|---|---|
| 102,200.78 | 10,000.00 | 92,200.78 | 8,298.07 | 4.5 | −22.5% | −20,745.18 → **−8,298.07** | 85,604.64 | 88,716.54 | **88,716.54** (floor adds 3,111.90) |

Also test LifeLib's listed pitfalls, written as our own tests:
- composition order (charge before MVA would be wrong);
- floor before MVA would remove the downside protection;
- the free amount is exempt from the MVA;
- withdrawals are deducted gross;
- the statutory floor rate minimum is 15 bp, not 1%;
- the charge clock resets at renewal;
- the shock lapse is 90% in year 6, repeated every 5 years;
- the MVA is zero in the window;
- interest, charges and the MVA never appear as cash-flow lines.

### 9.3 Engine vs LifeLib (anchor cell, full projection)
Run LifeLib's `MYGA_US_S` on its own anchor point (base and stress rate scenarios), with its mortality table replaced by our synthetic table. Run our engine on the identical new-business policy. The anchor point carries LifeLib's $4,000 withdrawal at *t* = 12, so set ours with a scenario override on `withdrawal` for projection month 13 only. For every month to maturity, these lines must agree within **$0.000001**:
- in-force;
- premiums;
- withdrawals;
- death, lapse, annuitization and maturity claims;
- commissions;
- expenses;
- net cash flow;
- account value and floor.

A spot check from LifeLib's own run with its own table: lapse claim at *t* = 0 = 91.84, death claim = 46.07, net cash flow = 97,857.92. This confirms the harness reads the right columns.

### 9.4 Re-running the SPIA cross-check
It needs a separate Python 3.12 virtual environment with `modelx`, `pandas` and `pyarrow`, plus a sparse checkout of `lifelib-products` at `5d91f95`. Then run:

```text
python -I spia_crosscheck.py <lifelib>/lifelib/libraries/uslib/products/immediate_annuity  <MentorAmp repo>  <scratch work dir>
```

It reads our Base and Low Interest Rate results from `backend/artifacts/`. Expected: every difference below $0.000001. Proposed home in the repo: `tools/lifelib_crosscheck/`, our scripts only, with LifeLib fetched at run time and never committed.

### 9.5 Platform
- SPIA import and run unchanged.
- A MYGA upload missing a MYGA field is rejected with a clear message.
- A MYGA run is cancellable and retryable like SPIA.
- The trace shows the AC-7 chain.
- Re-running the same Run Set gives identical numbers.

## 10. What to hand back
1. A PR to `main` (reviewed, no self-merge, no AI co-author lines) with E-1 to E-5 and the tests.
2. Test output: unit tests, the LifeLib cross-check JSON (anchor and in-force), and the SPIA golden numbers unchanged.
3. The MYGA golden numbers for the in-force block (Base and Rates Up reserve, total outgo, one trace value), to be frozen like SPIA's.
4. Anything in LifeLib's notes we could not match, with the reason.

## 11. Questions for Eko (short)
1. **Licence:** `lifelib-products` has no licence yet. Is "reference and answer key, no copying" acceptable, or does he want us to ask the LifeLib maintainer whether the U.S. library will ship under lifelib's MIT licence?
2. **Product:** Is LifeLib's representative 5-year MYGA (§5) acceptable as our illustrative demo MYGA, until his own MYGA reference case arrives?
3. **Reserve basis:** Q-MYGA-4 / Q-MYGA-6 are still open: GAAP, statutory (CARVM / VM-22) or both? v0.1 shows an illustrative PV of outgo next to the cash surrender value.
4. **Real tables:** Can we load 2012 IAM Basic, Scale G2 and the VM-22 factors from the SOA, or must we stay synthetic? (D3 data and licensing section.)
5. **SPIA:** The engine now matches LifeLib exactly. He still needs to approve the SPIA formulas and assumptions before we remove the "illustrative" label (M3).
