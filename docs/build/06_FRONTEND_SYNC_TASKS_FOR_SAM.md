# Frontend tasks: bring the UI in line with the backend

| | |
|---|---|
| **For** | Sam Yung (frontend) |
| **From / reviewer** | Samuel Ayinla (Technical Lead) |
| **Frontend checked** | `BemaISLTD/mentor-amp` `main` = `f12d211` (14 Oct) |
| **Backend checked** | `BemaISLTD/mentor-amp-backend` `main` = `fffc396` (WP2 merged) |
| **How it was checked** | Every backend endpoint (115) compared with every call in `src/`; every screen opened in a browser against the backend; a full SPIA run driven through the UI; every "not available" message compared with what the backend really offers |
| **Written** | 2026-10-16 |

## 0. What already works (keep it working)

All 12 screens open with no errors. A full SPIA run works from the UI: preflight, submit, live progress, results (Base $4,889,731 and Low $5,541,437, matching the backend exactly), compare (+$651,706), CSV export and the Trace Explorer (SPIA-0001, month 12 = 1,533.20932).

**Main references (attached, 16 October edition, checked against backend `fffc396`):**
- **`frontend_api_reference.md`**: every endpoint (all 115), with its access rule, parameters, request body, **response type in TypeScript** and a **real example response**. All 140 type blocks compile in strict mode, and every new example was checked against its type. Start with its section "What changed since the 2 October edition". The new sections are **Data Manager**, **Run control** and **Model editing**.
- **`frontend_integration_guide.md`**: the overall flow, the rules, the roles and what is not built yet.
- Noah's WP2 contract (short summary of the same Data Manager and cancel/retry rules): https://github.com/BemaISLTD/mentor-amp-backend/blob/main/docs/wp2_frontend_api_contract.md

---

## Part A: Fix first (the screens disagree with the backend)

These are small, but today they show wrong information.

| # | Problem | What to do | Done when |
|---|---|---|---|
| **A1** | **Run status names.** The backend calls a waiting run `pending`, not `queued`. So the "Queued" counters on the Run Set screens always show 0, and waiting runs show the raw word "pending". Results and Reports also don't recognise `cancelled` or `pending` | Use the backend lists. **Runs:** `pending, running, success, partial_success, failed, cancelled`. **Run Sets:** `queued, running, success, partial_success, failed, cancelled` (`runs_by_status` counts the *runs*, so read `pending` there) | A cancelled run and a waiting run show the right label and colour on Runs, Results, Reports and Investigate, and the counters are right |
| **A2** | **Data and model statuses.** Inputs and Models show anything that isn't `validated` or `needs_review` as **"Draft"**, so **approved, rejected and superseded files all look like drafts** | **Inputs:** handle `uploaded, draft, needs_review, validated, approved, rejected, superseded, legacy_unvalidated`. **Model versions:** handle `draft, needs_review, validated, approved, published`. Rejected and superseded must look clearly different (and not usable) | A rejected file never shows as "Draft" |
| **A3** | **Partial results.** For a `partial_success` run, the backend refuses results unless you send `include_partial=true` (it returns `409 RESULTS_INCOMPLETE`). The UI never sends it, and Investigate treats partial runs as complete, so it hits that error | Show a clear "This run is incomplete" warning with a **"Show incomplete results"** choice. Only then add `include_partial=true` to summary, aggregates, results, export.csv, trace and comparisons. On CSV, read the `X-MentorAmp-Complete` header | A partial run can be opened only after the user agrees, and it is always labelled incomplete |
| **A4** | **Trace capture is always off for projection sets made in the UI.** The Create dialog always sends `trace_scope: {mode: 'none'}` and says "Trace capture is unavailable", but the backend supports it. So runs from UI-made projection sets can **never** be investigated | Enable the trace settings: `mode` = `none`, `selected_policies` (with `policy_ids`) or `all`. Suggested default: `selected_policies` with a few policies | A projection set created in the UI produces a run you can trace in Investigate |
| **A5** | **Permission errors.** The UI shows every button to every role. A reviewer or read-only user who clicks Submit, Upload, etc. gets a `403` | Use `user.roles` from `/v1/auth/me` to hide or disable actions the role can't do, and show a friendly message on any `403` | A `reviewer` account sees no Submit or Upload buttons, and nothing breaks |

Roles (backend `app/api/auth.py`):

| Role | What it can do |
|---|---|
| `admin` | Everything |
| `actuary` | Everything except editing project settings |
| `model_developer` | Read; import data; run |
| `reviewer` | Read, plus **approve/reject data** |
| `read_only` | Read only |

---

## Part B: Connect backend features that have no screen yet

In priority order. B1 and B2 are the most important.

| # | Feature | Backend endpoints | Notes |
|---|---|---|---|
| **B1** | **Data Manager** (replaces the one-step upload): open → upload → preview → map → validate → commit → approve/reject. **All four kinds:** inforce, assumption tables, factor tables, scenarios | `/v1/import-sessions…`, `/v1/mapping-profiles`, `/v1/validation-runs/{id}/issues` and `/rejected-records`, `/v1/datasets/{kind}/{id}/approve`, `/reject`, `/versions`, `/compare`, `/v1/projects/{id}/import-sessions` | Follow Noah's contract. It removes the "saving to the library is not available" and "Scenario import is not available" messages. `POST /v1/imports/inforce` is **deprecated**; stop using it. The uploader cannot approve their own file (`409 SELF_APPROVAL_FORBIDDEN`), so approval needs a second user (see B4) |
| **B2** | **Cancel and Retry** on each run in the Execution Center | `POST /v1/runs/{id}/cancel`, `POST /v1/runs/{id}/retry` | Show them only when `can_cancel` / `can_retry` is true, and still handle `409`. A retry creates a new run linked by `retried_from_run_id` |
| **B3** | **Run evidence** ("View package details" today only shows a message) | `GET /v1/runs/{id}/package`, `/manifest`, `/attempts`, `/steps`, plus `GET /v1/runs/{id}` for one run | Show what was frozen for the run (fingerprint, inputs, formulas) and each attempt |
| **B4** | **Admin: users, members, audit** | `GET/POST /v1/users/` (admin only; body includes `roles`), `POST /v1/projects/{id}/members` (`owner`/`editor`/`viewer`), `GET /v1/audit-logs/` | The screen says "no audit-list API" and "Member actions are not available", but both exist. This is also how you create the second (reviewer) account needed for B1 |
| **B5** | **Projection sets: edit, duplicate, add a scenario, view one** | `PATCH /v1/projection-sets/{id}`, `POST …/duplicate`, `POST …/scenarios`, `GET /v1/projection-sets/{id}` | Today the UI can only create |
| **B6** | **See the data inside an input** (the rows) | `GET /v1/inputs/inforce/{id}` and `/records`, `GET /v1/inputs/assumption-tables/{id}`, `GET /v1/inputs/factor-tables/{id}` | For "view input" / lineage |
| **B7** | **Results:** policy-level rows, and run history filters | `GET /v1/runs/{id}/results` (paged); `GET /v1/runs` accepts `status`, `projection_set_id`, `model_version_id`, `run_set_id` | The screen says "Run-history filtering is not available", but it is |
| **B8** | **Models:** detail, versions, structure, variables; create a model or a new draft version; edit version notes | `GET /v1/models/{id}`, `/versions`, `GET /v1/model-versions/{id}/structure`, `/variables`; `POST /v1/projects/{id}/models`, `POST /v1/models/{id}/versions`, `PATCH /v1/model-versions/{id}` | Formulas themselves are code and are **not** editable from the UI; that "unavailable" message is correct. **New versions stay `draft` and cannot run yet** (the backend has no approval endpoint), so label drafts "not runnable yet" and offer edits and archiving on drafts only. See "Known backend issues" in the reference |
| **B9** | Smaller: one scenario's detail; "what depends on this value" in Investigate | `GET /v1/scenarios/{id}`; `GET /v1/runs/{id}/trace/dependents` | Nice to have |

---

## Part C: Clean-up

1. **Mentor screen:** it still shows mock chats ("GAAP Reserve Model v3.2", dated 2025). Hide them or mark the screen "Coming later", so nobody takes them for real data.
2. **Trace Explorer heading:** "Why did net reserve change?" → something like "Trace a value".
3. **Delete the 20 unused duplicate files** at the top of `src/pages/`: 10 `.jsx` files (`ExecutionCenter`, `FormulaLibrary`, `InputLibrary`, `ModelLibrary`, `ProjectionSetsLibrary`, `ReportsCompleted`, `ReserveInvestigation`, `ResultsExplorer`, `ScenarioAnalysis`, `dashboard`) and their 10 `.css` files. Nothing imports them. The app uses the copies in the sub-folders, which import their own CSS.
4. **Lint:** 53 warnings (it was 47). Please bring it down.
5. **Process:** push to a branch and open a PR. Please don't push straight to `main`.

---

## Part D: Don't build these now

**The backend really doesn't have these, so the "unavailable" message is correct:**
- PDF/XLSX reports and report sharing
- scenario generation and scenario export
- creating or editing formulas
- saving investigations
- Scratchpad
- Mentor AI
- integrations
- saving workspace defaults

**The backend has these, but they aren't for the UI yet:**
- the old `/v1/variables` and `/v1/formulas` registry editing (admin-only, legacy)
- `/v1/dashboard/stats` (use the project dashboard)
- `/v1/products` and `/v1/asset-positions` (ask Noah before using them for the "Asset Inforce" tab)
- project archive
- anything under `/internal/experimental`

---

## How to test

- Use the backend `main` locally, as you do now.
- For B1, you need **two accounts**: an `admin` (or `actuary`) who uploads, and a second user with the `reviewer` role who is a member of the project and approves. Create the second account with B4, or ask Samuel.
- **Check before handing back:**
  - Submit a SPIA run from the UI and the numbers still match: Base **$4,889,731**, Low Interest Rate **$5,541,437**.
  - Cancel a running run.
  - Retry it.
  - Upload, validate, commit and approve one file.
