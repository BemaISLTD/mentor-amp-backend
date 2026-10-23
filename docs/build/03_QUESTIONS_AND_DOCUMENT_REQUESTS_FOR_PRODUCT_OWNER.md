# MentorAmp — Questions and Document Requests for the Product Owner

**To:** Product Owner (Eko) and the Actuarial Product Owner
**From:** Samuel Ayinla, Technical Lead — for the MentorAmp development team (Samuel, Noah, Sam Yung)
**Date:** 2026-09-30
**Why you are receiving this:** The team is building MentorAmp from the Consolidated Architecture and Delivery Specification (CAS). The software can now run a projection end to end, but **only with illustrative formulas and synthetic data** that the developers wrote. The CAS says developers must not invent authoritative actuarial answers (CAS §3.1, §12; Week 0 Readiness Plan §2 and §18). To turn the demonstration into accepted functionality, we need the items below.

---

## How to answer

- Answer in this document, by email, or in a short meeting. Each item has an ID (for example **Q-SPIA-3**); please quote it.
- Where we ask for a file, a **template** is described in §11. Excel is fine.
- If something is not decided yet, say "not decided" and name who will decide and when. We will record it as a blocker rather than guess.
- **Priority:**
  - **P0:** needed in the next 1–2 weeks. Without it, the next milestone stops.
  - **P1:** needed within about a month.
  - **P2:** needed before launch acceptance.

---

## 0. The ten most urgent items (please start here)

| # | ID | What we need | Priority | What it unblocks |
|---|---|---|---|---|
| 1 | Q-SPIA-1…6 | **One SPIA reference case** with inputs, formula intent, assumptions, and **independently prepared expected results including intermediate values**, plus a tolerance | P0 | Replacing the illustrative SPIA formulas; the first real accepted product |
| 2 | Q-MYGA-1…6 | **One MYGA / fixed deferred reference case** (same content) | P0 | M2 MYGA model |
| 3 | Q-TOL-1 | **Tolerance rules**: how close must our numbers be (absolute, relative, per metric)? | P0 | Any automated acceptance test |
| 4 | Q-DATA-1 | **Data dictionary**: field names, meanings and units for policy (inforce) data | P0 | Import mapping and validation |
| 5 | Q-DATA-3 | **Which mortality tables we may use** (and the licence position) | P0 | Replacing the synthetic mortality table |
| 6 | Q-SCOPE-1 | **Product order after SPIA and MYGA** (the CAS says RILA gets the deepest implementation) | P0 | Planning |
| 7 | Q-GOV-1 | **A named QA reviewer** and who signs off actuarial acceptance | P0 | Accepting anything as "done" |
| 8 | Q-ACT-1…7 | **The seven actuarial confirmations** in CAS §17 | P1 | Formula lock for frameworks and VA |
| 9 | Q-SCOPE-3 | **Desktop (Electron) or web?** The CAS requires Electron; the current frontend is a web app | P1 | Packaging work |
| 10 | Q-IP-1 | **Written permission** for any existing formula documents or Excel models you want us to use | P0 | Using your material without IP risk |

---

## 1. What you can already see (context for the questions)

- The backend runs an **illustrative SPIA** projection: 25 synthetic policies, a synthetic mortality table, and two scenarios (discount rate 4.5% and 3.0%).
- It shows the reserve by year, explains any value (formula → inputs → table row), and compares the two scenarios.
- Sam Yung's screens are being connected to these results.
- These numbers are **not** actuarial results. They show that the machinery works. Your reference cases make them real.

---

## 2. Scope and priorities

| ID | Question | Why we ask | Blocks if unanswered | Needed |
|---|---|---|---|---|
| **Q-SCOPE-1** | After SPIA and MYGA, in what order do you want FIA, RILA, IDI, VA and PRT? The CAS gives RILA the deepest implementation (VM-21, MRB, DAC, LDTI needs). Is that still the priority? | We build one vertical product at a time | Product planning beyond M2 | P0 |
| **Q-SCOPE-2** | What is the date and audience of the next demo you need? What must it show? | We plan milestones to your date, not ours | Milestone commitments | P0 |
| **Q-SCOPE-3** | Must Launch 1.0 be a **desktop Electron** application (CAS §1, §10.1), or is the current **web** application acceptable for now? If web, where will it be hosted? | The frontend's Electron shell was removed on 19 September; the web app is on Netlify | Packaging, deployment, security design | P1 |
| **Q-SCOPE-4** | Which screens matter most to you for the next two demos? (Models, Inputs, Projections, Runs, Results, Trace, Reconciliation, Reports, Investigate, Mentor) | We wire screens one at a time | Frontend sequencing | P1 |
| **Q-SCOPE-5** | The UI includes a **scenario generator** and an **AI assistant (Mentor)**. The CAS §16 lists both as after Launch 1.0. Do you agree we defer their backend work? | Avoid spending effort on post-launch items | Scope control | P1 |
| **Q-SCOPE-6** | The CAS status says "Draft for technical review and scope confirmation". Is it now approved, or is there a newer version? | We treat it as the authority | Everything (blocker BLK-012) | P1 |

---

## 3. Product reference cases (the most important section)

For **each product** we need one **reference case** (also called a golden policy or test case). This is a small, hand-checkable example that an actuary prepares **independently of our code**.

**What a reference case contains (all products):**
1. **Policy data:** one or more policies with every field needed (see §11.1 template).
2. **Formula intent:** a written description of each calculation step (plain words or formulas). For example: "monthly survival = previous survival × (1 − monthly mortality)".
3. **Assumptions:** the exact tables and parameters (mortality, lapse, expenses, interest, crediting…), with the rows used.
4. **Expected results:** the final outputs **and the intermediate values**, month by month or year by year (see §11.2 template).
5. **Tolerance:** how close our result must be (§8).
6. **Provenance:** who prepared it, when, with which tool, and confirmation that it is synthetic or authorized data.

### 3.1 SPIA (single premium immediate annuity) — **P0**

| ID | Question / request |
|---|---|
| **Q-SPIA-1** | Policy fields: annuitant age and sex, issue date, premium, payment amount and frequency, first payment date, certain period (if any), joint life (if any), refund features (if any). |
| **Q-SPIA-2** | Payment timing: are payments at the start or end of each period? Monthly, quarterly or annual? |
| **Q-SPIA-3** | Mortality: which table and improvement scale? Is age last birthday or nearest birthday? How is annual mortality converted to monthly (constant force, uniform distribution of deaths, other)? |
| **Q-SPIA-4** | Expenses: are maintenance expenses included? Per policy, per payment, or percent of payment, and with what inflation? |
| **Q-SPIA-5** | Discounting: one flat rate or a yield curve? Which basis (GAAP, statutory, pricing)? What valuation date? |
| **Q-SPIA-6** | Expected outputs with intermediates: survival probability, expected payment, discount factor, PV of each payment, reserve at each year end, and the total reserve at the valuation date. |

> Our illustrative SPIA today: payments at month end, synthetic Gompertz mortality converted to monthly by constant force, no expenses, one flat rate (4.5% / 3.0%), 50-year horizon. Please tell us every point where your method differs.

### 3.2 MYGA / fixed deferred annuity — **P0**

| ID | Question / request |
|---|---|
| **Q-MYGA-1** | Policy fields: premium, issue date, account value at valuation, guarantee period, guaranteed rate, current credited rate, surrender charge schedule, free-withdrawal percentage, MVA (if any), death benefit definition, annuitization options. |
| **Q-MYGA-2** | Account-value rollforward: the order of operations each period (interest credit, withdrawals, charges, deaths, lapses). |
| **Q-MYGA-3** | Decrements: lapse (base and dynamic?), partial withdrawals, mortality — tables and formulas. |
| **Q-MYGA-4** | Which reserve or valuation method should the first MYGA reference use (for example CARVM-style, GAAP)? |
| **Q-MYGA-5** | Expected outputs with the intermediate rollforward: beginning AV, interest, withdrawals, surrender charges, death and surrender benefits, ending AV, cash flows, and the reserve. |
| **Q-MYGA-6** | The UI shows a "MYGA Reserve Model" with "GAAP Reserve Base". Should the first MYGA case be GAAP, statutory, or both? |

### 3.3 FIA — **P1**

| ID | Question / request |
|---|---|
| **Q-FIA-1** | Allocations between fixed and indexed accounts; the strategies (point-to-point, monthly average, cap, participation, spread). |
| **Q-FIA-2** | The index scenario or returns to use; option budget or factor inputs; hedge/yield assumptions. |
| **Q-FIA-3** | Expected outputs: index credits per term, AV rollforward, withdrawals, fees, benefits, cash flows. |

### 3.4 RILA (deepest implementation; representative block) — **P1**

| ID | Question / request |
|---|---|
| **Q-RILA-1** | Segment design: index, term, participation, buffer or floor, cap; interim value method; maturity crediting. |
| **Q-RILA-2** | Scenario paths to use (deterministic first, then stochastic?). |
| **Q-RILA-3** | The **representative block**: how many policies and segments, what mix, what horizon (CAS §15 criterion 7). |
| **Q-RILA-4** | Expected outputs at policy and aggregate level, with trace-able intermediates. |

### 3.5 IDI, variable annuity, PRT, life readiness — **P2**

| ID | Question / request |
|---|---|
| **Q-IDI-1** | Claim-state case: states, incidence and termination tables, premiums, benefits, expenses, expected cash flows and liability. |
| **Q-VA-1** | The initial VA design: separate and fixed accounts, funds or scenario returns, charges, withdrawals, and which rider/benefit (see Q-ACT-3). |
| **Q-PRT-1** | Participant/block case: participant data, decrements, benefit schedules, payments, discounting, PV and aggregate expected outputs. |
| **Q-LIFE-1** | The minimum life-policy fields we must accept at launch (see Q-ACT-7). |

---

## 4. Frameworks (valuation and accounting) — **P1/P2**

For each framework we need one agreed reference case and expected results (CAS §7, §15 criterion 6).

| ID | Framework | What we need |
|---|---|---|
| **Q-FW-1** | VM-21 | Product (RILA/VA), scenario set, behaviour assumptions, the aggregation and tail measure (CTE level), expected results |
| **Q-FW-2** | VM-22 | See Q-ACT-4 |
| **Q-FW-3** | LDTI | Cohorts, discount-rate handling, assumption update and remeasurement example, liability rollforward, disclosure fields |
| **Q-FW-4** | DAC | Capitalized cost inputs, grouping, amortization method, rollforward example |
| **Q-FW-5** | MRB | Which benefits, risk-neutral scenarios, non-performance risk inputs, fair value and rollforward example |
| **Q-FW-6** | ASC 815 | See Q-ACT-2; host and embedded derivative split example |
| **Q-FW-7** | GAAP reporting | Which balance-sheet, income-statement, rollforward and attribution outputs, in what layout |

---

## 5. The seven actuarial confirmations (CAS §17) — **P1**

The CAS says these must be decided **before formulas are locked**.

| ID | Confirmation | Question |
|---|---|---|
| **Q-ACT-1** | Fixed-account attribution | What calculation and reporting split do you intend for fixed-account or account-value attribution, and for which products? |
| **Q-ACT-2** | ASC 815 terminology | Do you confirm the outputs are the host contract and embedded derivative components formerly described under FAS 133? Which rollforward and attribution fields are required? |
| **Q-ACT-3** | Variable annuity scope | What is the initial VA reference design: separate/fixed-account features, rider or benefit type, fees, withdrawals, scenario assumptions? |
| **Q-ACT-4** | VM-22 reference case | Which product, method, valuation-manual version, scenario or prescribed-input approach, and expected results? |
| **Q-ACT-5** | Disclosure package | Which ASU 2018-12 disclosure tables and rollforwards must we populate first, and at what level of disaggregation? |
| **Q-ACT-6** | Performance benchmark | Target policy count, scenario count, horizon, output granularity, hardware, and acceptable runtime for CPU, PySpark and GPU. |
| **Q-ACT-7** | Life readiness | The minimum life policy, coverage, rider, premium, benefit, assumption and valuation fields we must accept at launch. |

---

## 6. Data

| ID | Request | Why | Priority |
|---|---|---|---|
| **Q-DATA-1** | A **data dictionary** for policy (inforce) data: field name, meaning, type, unit, allowed values, required or optional | Import mapping and validation rules (CAS §8.1) | P0 |
| **Q-DATA-2** | **Sample inforce files** per product: synthetic or anonymized, with written authorization to use them | Realistic testing; the CAS forbids unauthorized policyholder data | P0 |
| **Q-DATA-3** | **Mortality tables** we may use (for example SOA/IAM tables) and any improvement scales; confirm licensing | Replace the synthetic table | P0 |
| **Q-DATA-4** | Other assumption tables: lapse, withdrawal, expense, incidence/termination (IDI) | Product slices | P1 |
| **Q-DATA-5** | Factor tables: caps, participation rates, spreads, option budgets, yields (FIA/RILA) | FIA/RILA | P1 |
| **Q-DATA-6** | Scenario sets: deterministic rate scenarios; the stochastic scenario source for VM-21/MRB | Scenario inputs | P1 |
| **Q-DATA-7** | Asset data (for "Asset Inforce"): which fields, and is it needed for launch? | The UI shows asset inforce | P2 |
| **Q-DATA-8** | Actual experience data for rollforwards (if needed at launch) | Rollforward/attribution | P2 |

---

## 7. Formula documents and intellectual property

| ID | Question | Priority |
|---|---|---|
| **Q-IP-1** | Do you have formula specifications, actuarial memos or Excel models you want us to follow? If yes, please confirm **in writing** that MentorAmp may use them (CAS §3.3: no employer or client proprietary material without written authorization). | P0 |
| **Q-IP-2** | May any real (anonymized) data leave your environment? Where may it be stored (local only, Neon, other)? | P0 |

---

## 8. Tolerances and acceptance

| ID | Question | Priority |
|---|---|---|
| **Q-TOL-1** | For each output type, what tolerance is acceptable? For example, reserve within 0.01% relative or $1 absolute; cash flows within $0.01 per policy per month. | P0 |
| **Q-TOL-2** | At which level must results match: policy × month, policy total, block total? | P0 |
| **Q-TOL-3** | How are differences outside tolerance handled: who can accept an explained difference, and how is it recorded? | P1 |
| **Q-TOL-4** | For CPU vs PySpark vs GPU parity, what numerical tolerance is acceptable? | P2 |

---

## 9. People and governance

| ID | Question | Priority |
|---|---|---|
| **Q-GOV-1** | Who is the **QA reviewer** (independent testing and sign-off)? | P0 |
| **Q-GOV-2** | Who gives **actuarial acceptance** for each product and framework? | P0 |
| **Q-GOV-3** | Which user roles must the application support at launch: model developer, actuary, reviewer, administrator, read-only (CAS §10.3)? Anything else? | P1 |
| **Q-GOV-4** | Who can give the team **GitHub admin** rights to protect the `main` branches (blocker BLK-004)? | P1 |
| **Q-GOV-5** | What approval steps do formulas, assumptions and runs need (for example submit → review → approve → activate)? | P1 |

---

## 10. Infrastructure and data sensitivity

| ID | Question | Priority |
|---|---|---|
| **Q-INF-1** | Who owns the Coolify, Vercel, Netlify and Neon accounts? Which are for demos only? (CAS §10.2) | P1 |
| **Q-INF-2** | What data classification applies (synthetic only, anonymized, confidential)? What must never leave the local machine? | P0 |
| **Q-INF-3** | Where should the backend run for demos: a developer laptop, a shared server, or a cloud service? | P1 |

---

## 11. Templates we propose (Excel or CSV)

### 11.1 Reference-case policy data (one row per policy)

| Column | Example | Notes |
|---|---|---|
| `policy_id` | SPIA-REF-001 | Unique |
| `product_type` | SPIA | |
| `issue_date` | 2024-01-15 | ISO date |
| `valuation_date` | 2026-12-31 | |
| `issue_age` or `date_of_birth` | 67 | State the age basis |
| `gender` | M | |
| `premium` | 250000 | Currency stated in the header |
| product-specific fields | `monthly_payment`, `certain_period_months`, `guarantee_rate`… | Add columns as needed, with a short description row |

### 11.2 Expected results (one row per policy × period × output)

| Column | Example | Notes |
|---|---|---|
| `case_id` | SPIA-REF-001 | Links to 11.1 |
| `scenario` | Base | |
| `period` | 12 | Month or year number; state which |
| `period_end_date` | 2027-12-31 | |
| `output` | `survival_probability` / `expected_payment` / `discount_factor` / `pv_expected_payment` / `reserve` | Intermediate and final values |
| `expected_value` | 0.977212 | Full precision |
| `unit` | probability / USD | |
| `tolerance_abs` / `tolerance_rel` | 0.000001 / 0.0001 | Optional per row |
| `prepared_by` / `tool` / `date` | A. Actuary / Excel / 2026-10-05 | Provenance |

### 11.3 Assumption tables (one sheet per table)

- Header: name, version, source, effective date, key columns, value column, unit, licence note.
- Then the rows: the key columns (for example `age`, `gender`, `duration`) and the value column (for example `qx`).

---

## 12. Tracking (the team keeps this updated)

| ID | Owner | Asked | Answered | Status |
|---|---|---|---|---|
| Q-SPIA-1…6 | Actuarial Product Owner | 2026-09-30 | | Open |
| Q-MYGA-1…6 | Actuarial Product Owner | 2026-09-30 | | Open |
| Q-TOL-1…2 | Actuarial Product Owner | 2026-09-30 | | Open |
| Q-DATA-1…3 | Product Owner | 2026-09-30 | | Open |
| Q-SCOPE-1…6 | Product Owner | 2026-09-30 | | Open |
| Q-ACT-1…7 | Actuarial Product Owner (+ accounting review) | 2026-09-30 | | Open |
| Q-IP-1…2 | Product Owner | 2026-09-30 | | Open |
| Q-GOV-1…5 | Product Owner / Sponsor | 2026-09-30 | | Open |
| Q-INF-1…3 | Product Owner / Sponsor | 2026-09-30 | | Open |

Thank you. Every answer turns part of the demonstration into accepted, traceable functionality.
