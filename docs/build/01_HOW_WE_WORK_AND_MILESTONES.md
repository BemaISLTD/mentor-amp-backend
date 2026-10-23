# MentorAmp — How We Work and Milestones

**Version:** 1.0 (2026-09-30)
**Owner:** Samuel Ayinla — Technical Lead / Core Backend
**Applies to:** Samuel, Noah Enemali (backend/full-stack), Sam Yung / Elusope Samson (frontend), the Product Owner (Eko) and the Actuarial Product Owner, and the QA Reviewer (to be assigned)
**Read with:** `02_BACKEND_FRONTEND_CONTRACT.md` (what crosses the wire) and `03_QUESTIONS_AND_DOCUMENT_REQUESTS_FOR_PRODUCT_OWNER.md` (what we need from the business)

---

## 1. Why this document exists

Until now the frontend and backend were built separately. Sam Yung built a complete clickable design with fake data; the backend built registries and engine pieces that no screen uses. **No product has run end to end yet.**

From today we work differently:

| Question | Where the answer lives |
|---|---|
| What must MentorAmp do? | The Consolidated Architecture and Delivery Specification (CAS) and the Master Plan (MP). They are the rulebook. |
| What does the user see and click? | Sam Yung's UI (`MentorAmp-frontend`, commit `39913d6`). It is the map. |
| What data crosses between UI and backend? | `02_BACKEND_FRONTEND_CONTRACT.md`. It is the single agreement. |
| What do we still need from the business? | `03_QUESTIONS_AND_DOCUMENT_REQUESTS_FOR_PRODUCT_OWNER.md`. |
| When is something finished? | Section 6 below: it runs, persists, traces, and has evidence. |

**Simple example.** The "Start run" button is not finished because the card on the screen changes colour. It is finished when:
1. the button sends the agreed request;
2. the backend calculates and saves the result;
3. the Results screen shows the real saved number;
4. "Trace this value" shows exactly which formula and which table row produced it.

---

## 2. The rule for every feature: one vertical slice at a time

We never build "all the backend" and then "all the frontend". We build one thin slice through every layer, prove it works, then take the next slice.

```text
User clicks a button (Sam Yung's screen)
   → agreed request in the contract (JSON example)
      → FastAPI endpoint (Noah / Samuel)
         → service / engine (Samuel)
            → PostgreSQL + saved results
         ← agreed response (JSON example)
   ← screen shows real data, with loading / empty / error states
```

Before anyone codes a slice, the three developers agree its row in the contract:

| Question | Example answer (slice: submit a run) |
|---|---|
| Who, and what permission? | Actuary with `runs:execute` (not enforced in demo mode) |
| What triggers it? | Click **Submit to Execution Center** |
| Request | `POST /v1/run-sets` with Projection Set IDs and scenario IDs |
| Immediate response | `202 Accepted` with run IDs, status `queued` |
| What is saved? | A Run Set, the runs, a manifest, run events |
| Long-running behaviour | The UI polls `GET /v1/runs/{id}` every 2 seconds |
| Success | Status `success`; results and trace become available |
| Failure | Status `failed` plus error events; the UI shows the message |
| UI states | queued → running (progress %) → success / partial_success / failed |
| Evidence | API test, engine test, demo recording |

---

## 3. Milestones

Milestones follow the Master Plan weeks (MP §14). Each milestone ends with a demo to the boss.

| Milestone | What the boss will see | Maps to | Depends on |
|---|---|---|---|
| **M0 — Setup** (now) | These three documents; the new Neon database migrated; the backend starting with no login | MP Week 0 | Samuel provides the Neon URL |
| **M1 — First Real Run (SPIA, illustrative)** | A real SPIA projection for 25 synthetic policies, two scenarios (Base 4.5%, Low Interest Rate 3.0%), reserves by year, trace of any value to its formula and mortality-table row, and a comparison of the two runs. Shown through the API page (`/docs`) and a demo script, then through Sam Yung's screens as he wires them. | MP Week 3 target ("first real UI-to-engine run") | Engine fixes (see §5); no business input needed because the formulas are clearly labelled **illustrative** |
| **M2 — MYGA + real data lifecycle** | MYGA illustrative model (account-value rollforward); upload → map → validate → version an inforce file; Formula Studio read views; run cancel; converge Noah's `dev` work (Parquet results, audit, products) | MP Weeks 2–3 | Samuel/Noah convergence; MYGA reference case requested in D3 |
| **M3 — Reconciliation + formula governance** | Load the boss's expected results; compare within tolerance; exceptions and approval; formula draft → approve → activate; the SPIA/MYGA illustrative formulas replaced by approved ones | MP Weeks 3–5 | **Business answers to D3** (reference cases, tolerances) |
| **M4+ — Launch scope** | FIA and RILA (the deepest), frameworks (VM-21, VM-22, LDTI, DAC, MRB, ASC 815), Dagster, PySpark, GPU, Electron packaging, hardening | MP Weeks 4–10 | Business answers; QA reviewer |
| **Post-launch** (CAS §16) | Enterprise scenario generation (the Generator Library screens), AI assistance (the Mentor screens) | CAS §16 / MP §20 | Owner decision to move forward |

> **Why SPIA first?** SPIA code already exists (mortality, survival, payments, present value), and the Master Plan pairs SPIA with MYGA in Week 3. It gives us a real running engine fastest. MYGA follows immediately because Sam Yung's screens are designed around a MYGA model.

> **What "illustrative" means.** The M1 formulas are standard textbook annuity mechanics written by the development team. They are **not** actuarially approved, and they carry the flag `illustrative: true` in every API response. The CAS forbids developers from inventing authoritative actuarial answers (W0R §2, ITD §12). That is why D3 asks the business for the real reference cases. When those arrive, M3 replaces the illustrative formulas and proves them against the independent expected values.

---

## 4. Who does what

| Person | Role in M0–M1 | Role after M1 |
|---|---|---|
| **Samuel** (with AI support) | Writes the contract; builds the M1 engine and API on branch `feat/m1-first-real-run` (from `main`); seeds demo data; runs the boss demo | Owns engine architecture, formula/variable/resolver/projection/valuation/trace contracts, and code review |
| **Noah** | Reviews the M1 PR; continues auth/admin work; **avoids editing the engine files listed in §7 until M1 merges**; plans convergence of `dev` (Parquet artifacts, audit, products) into `main` for M2 | APIs, persistence, imports/mapping, run/results/trace/reconciliation services, exports, integration tests |
| **Sam Yung** | Wires screens to the API from the contract, slice by slice, starting with the M1 screens; adds routing (URLs) and an API client; keeps his design | All seven application areas, UI states, accessibility, Electron packaging |
| **Product Owner (Eko) / Actuarial Product Owner** | Answers D3; supplies reference cases, expected results, tolerances, and the seven confirmations | Actuarial acceptance of each product and framework |
| **QA Reviewer** (not yet assigned) | — | Test plan, independent reproduction, sign-off |

---

## 5. Milestone 1 — exact definition

### 5.1 What M1 builds (backend)

1. **Demo mode without login.** When `AUTH_MODE=disabled` (allowed only with `DEBUG=true`), every request runs as a seeded `demo-admin` user. No token is needed. Login returns in a later milestone without changing any other endpoint.
2. **Engine fixes.** Product formulas are registered at startup; formulas can use context values (month, policy year, attained age); exact table lookups (no silent first-row fallback); the zero-value bug is fixed; prior-period values work; scenario overrides apply to any variable; data loads once per run so Neon stays fast.
3. **New objects.** Models and model versions, formula groups, published outputs, Projection Sets, Run Sets, run progress and events, run manifest.
4. **Endpoints** for Dashboard, Models, Inputs, Scenarios, Formulas, Projections, Runs, Results, Trace and Compare (contract §E).
5. **Seed data (synthetic).** One demo project, one SPIA illustrative model, 25 synthetic policies, a synthetic mortality table, Base and Low Interest Rate scenarios, and a ready Projection Set.
6. **Tests.** Engine self-consistency (engine vs. hand calculation), resolver rules, and the existing API tests.

### 5.2 M1 is done when

Status on 2026-09-30, verified on Samuel's Neon database:

- [x] From an empty Neon database: migrate → seed → submit a Run Set (1 Projection Set × 2 scenarios).
- [~] Both runs reach `success`, 25 policies × 600 months. *Both succeeded, but in 128.7 s rather than under ~60 s.*
  - The calculation takes 0.2 s. The rest is uploading about 18 MB per run from the laptop to Neon us-east-2.
  - An API server hosted next to Neon would meet the target.
  - For the demo, the live progress display covers the wait.
- [x] `GET /v1/runs/{id}/aggregates` returns the reserve by year; the Low Interest Rate reserve ($5,541,437.41) is higher than Base ($4,889,731.24).
- [x] `GET /v1/runs/{id}/trace` for one value shows formula → inputs → mortality-table row (age 72, M) → value.
- [x] `GET /v1/comparisons` shows Base vs Low Interest Rate and names the changed input (discount rate 0.045 → 0.03).
- [x] Model, Projection Set, run, manifest and export responses carry the illustrative flag (checked in the end-to-end test).
- [x] Re-running gives identical numbers: two separate Base runs on Neon gave the same reserve to every digit, and the unit test `test_run_is_deterministic` checks it too.
- [ ] All tests pass (49/49 ✔) and no secrets are committed (nothing committed yet ✔). **Still open:** open a PR and have Noah review it.

### 5.3 What M1 deliberately does not do (and when it comes)

| Not in M1 | Why | When |
|---|---|---|
| Login screen, roles in the UI | Samuel's decision: run first | M2/M3 |
| Approved actuarial formulas | Waiting for business reference cases (D3) | M3 |
| Reconciliation against expected results | Needs the boss's expected results and tolerances | M3 |
| File upload lifecycle (map / version / approve) | M1 seeds data directly | M2 |
| Formula editing, approval, rollback | Needs governance rules | M3 |
| Pause / resume / checkpoint, GPU, PySpark, Dagster | CPU reference path comes first (CAS §9) | M4+ |
| Reports, Investigations, Scratchpad | Build on proven results and trace | M2–M4 |
| Mentor AI, scenario generator | CAS §16: post-launch unless the owner moves them forward | Post-launch |

---

## 6. Definition of done (every feature, every milestone)

From CAS §3.1 and MP §2. A feature counts only when:

1. it can be configured through the application or an approved configuration file;
2. it executes end to end and saves results;
3. a reviewer can trace a result to its formulas, inputs, assumptions, scenarios and source records;
4. automated tests compare results with **independently prepared** expected values within an agreed tolerance (from M3 onward, when the business supplies them; before that, engine self-consistency tests and the `illustrative` label);
5. it is documented well enough for another developer to reproduce it.

A screen, endpoint, schema, mock or roadmap statement alone never counts (CAS §15 criterion 13).

---

## 7. Git and review rules

| Rule | Detail |
|---|---|
| Branches | `feat/<slice>`, `fix/<issue>`, `docs/<topic>`. Never commit directly to `main` or `dev`. |
| Pull requests | Every change goes through a PR with one reviewer who is not the author. **No self-merging.** |
| Checks | Minimum CI: `pytest` and a compile check. Until CI exists, the author pastes the test output in the PR. |
| Base for M1 | `feat/m1-first-real-run` starts from `main` (`6dd5e41`), as Samuel decided. |
| Engine files M1 changes | `backend/app/core/variable_registry/resolver.py` (small fixes), new `backend/app/core/projection_engine/engine.py` and `run_data.py`, new `backend/app/services/*`, new routers under `backend/app/api/`, `backend/app/products/*`, `backend/app/api/dependencies.py`, `backend/app/config.py`, `backend/app/main.py`, and one new migration. **Noah: please avoid parallel edits to these until M1 merges.** |
| `main` vs `dev` divergence | Noah's `dev` has valuable work (Parquet results, audit logs, products and assets, dashboard) that is not in `main`, and it still carries the original PR #1 defects (hard project delete). Because M1 starts from `main`, the two branches will differ. Plan for M2: merge `main` into `dev` (keep delete removed), resolve the two Alembic heads with an `alembic merge` revision, and choose one results store (Parquet per CAS §10.1). See blocker BLK-016. |
| Secrets | `.env` is never committed. The Neon URL, passwords and tokens never go into chat, screenshots or code. |
| Frontend | Sam Yung's repository is his. The backend team does not edit it; changes go through his PRs. He opens PRs rather than pushing to `main`. Build with **pnpm** (his tool); commit a `pnpm-lock.yaml` and remove the stale `package-lock.json` and `bun.lock`. |

---

## 8. Weekly rhythm (MP §21.1)

| When | Who | Output |
|---|---|---|
| Daily, 10–15 min | All | Done / today / blocked |
| 2–3 × per week | Samuel + Noah | Engine, schema and API decisions |
| 2 × per week | Samuel + Noah + Sam Yung | Contract check: payloads, states, next screen to wire |
| As needed | Samuel + Actuarial owner | Formula intent, expected values, tolerances |
| Friday | All + the boss | **Demo of running code** (not slides), plus the next week's slice |

### Weekly report to the boss (MP §24)

| Field | Content |
|---|---|
| Status | Green / Amber / Red, with the reason |
| Demonstrated | What ran this week (run IDs, screenshots) |
| Evidence | Tests, manifests, comparisons |
| Business inputs | What we received from D3; what is still missing and what it blocks |
| Next | The next slice and its date |

---

## 9. Boss demo runbook — M1

Until Sam Yung's screens are wired, the demo uses the API documentation page and a script. Both show **real** calculations.

**Before the demo (5 minutes)**
1. Start the backend: `cd backend && .venv\Scripts\python -m uvicorn app.main:app --port 8000`.
2. Check `GET http://localhost:8000/v1/system/status` → `database.status = ok`, `auth_mode = disabled`, `engine.registered_functions ≥ 5`.
3. If the database is empty: `python scripts/seed_demo.py`.

**Live (10 minutes)**
1. **"This is the project and model."** `GET /v1/projects` → `GET /v1/projects/{id}/models` → the SPIA Illustrative Model, its version, basis and status.
2. **"These are the inputs."** `GET /v1/projects/{id}/inputs` → 25 synthetic policies, the synthetic mortality table, and the two scenarios.
3. **"This is how the calculation is configured."** `GET /v1/projects/{id}/projection-sets` → valuation date 2026-12-31, 50-year monthly horizon, outputs, trace scope.
4. **"Run it."** `python scripts/demo_run.py` → submits one Run Set with Base and Low Interest Rate, shows live progress, then prints reserves by year for both.
5. **"Why this number?"** The script prints the trace tree of one value: `pv_expected_payment` → `expected_payment × discount_factor` → `survival_probability` → mortality-table row (age 72, F) → `qx`.
6. **"What changed between scenarios?"** The comparison shows the discount rate (4.5% → 3.0%) and the reserve increase by year.
7. **"What we need from you."** Hand over `03_QUESTIONS_AND_DOCUMENT_REQUESTS_FOR_PRODUCT_OWNER.md`. The formulas are illustrative until the business supplies reference cases.

---

## 10. Risks and how we handle them

| Risk | Sign | What we do |
|---|---|---|
| Business inputs arrive late | D3 questions unanswered after one week | Escalate in the Friday report; continue with illustrative formulas, clearly labelled; do not mark products accepted |
| Neon is slow over the internet | Runs take minutes | The engine loads data once and writes in large batches using table-level inserts, not ORM bulk inserts. The ORM path made 1,200 round trips for 1,200 trace rows. Trace is captured only for selected policies. What remains (about 1 minute per run) is upload bandwidth from a laptop; host the API in Neon's region for real use. |
| `main` and `dev` drift further apart | Merge conflicts in engine files | Noah avoids the M1 engine files; the convergence plan is in M2 |
| Frontend outruns backend again | Screens built on new mock data | Every new screen starts from a contract row |
| Nobody independent checks results | No QA reviewer | Requested in D3; until then, the tests and manifests are the evidence |
| Electron not decided | Web app deployed on Netlify; CAS requires Electron | Decision requested in D3 |
| Treating the demo as launch | Pressure to call things "done" | The definition of done (§6) and the `illustrative` label |
