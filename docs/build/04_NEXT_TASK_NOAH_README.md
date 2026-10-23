# Next task for Noah: WP2 (M2 Part A) – one branch, real data uploads, run cancel and retry

| | |
|---|---|
| **Assigned to** | Noah Enemali |
| **Assigned by / reviewer** | Samuel Ayinla (Technical Lead) |
| **Milestone** | M2, Part A (`01_HOW_WE_WORK_AND_MILESTONES.md` §3) |
| **Master Plan link** | MP §8.1 (data ingestion) and §13 (Noah owns imports/mapping and the Run Center); the MP Week 1–3 backend tasks |
| **Requirements covered** | LR1-DATA-007, 008, 009, 010, 011; LR1-API-003, 005; LR1-APP-002 (backend side); LR1-SEC-007; LR1-CORE-016 |
| **Blockers closed** | BLK-016 (`main`/`dev` drift, now `backlogs` vs `feat/m1-first-real-run`) |
| **Base commits** | `origin/backlogs` = `516eabd`; `origin/feat/m1-first-real-run` = `cb7201c` |
| **Written** | 2026-10-03 |

---

## 1. The task in one paragraph

First, join our two branches into one, with one database history and one results store, keeping every guarantee from both sides. Then build the **Data Manager lifecycle** the CAS requires: **upload → preview → map → validate → commit a new version → approve**. It covers inforce files, assumption tables, factor tables and scenarios, and every upload is kept and fingerprinted. Then add **run cancel and retry**. At the end, a user can rebuild the SPIA demo from files through the API alone, with no seed script, and get **exactly** the same reserves as today.

## 2. What must be true at the end (acceptance criteria)

Each item must be proved by an automated test or a reproducible command. The tests are listed in §8.

| # | At the end of this task… |
|---|---|
| **AC-1** | There is **one** integration branch, merged to `main` through a reviewed PR, that contains Noah's `backlogs` work **and** the WP1 correction pass. Nothing from either side is lost. |
| **AC-2** | `alembic heads` returns **exactly one** head. Every migration path in §8.1 works on a **disposable** PostgreSQL database. |
| **AC-3** | There is **one** results store: Noah's attempt-scoped Parquet artifacts. All the correction-pass guarantees (§4.3) still hold on top of it. |
| **AC-4** | Inforce, assumption-table, factor-table and scenario files can each be uploaded, previewed, mapped, validated, committed as a **new immutable version**, and approved, all through the API. |
| **AC-5** | Every upload keeps its **raw-file fingerprint**, mapping version, validation result, **rejected records** and an import log (CAS §8.1, LR1-DATA-010). |
| **AC-6** | A file with **different column names** can be mapped to the same canonical fields with **no change to formulas or product code** (LR1-DATA-008). |
| **AC-7** | Validation checks what CAS §8.1 lists: required fields, data types, units, duplicates, referential integrity, ranges, missing values, effective dates and record counts. It runs **before** anything can be committed or run. |
| **AC-8** | Committing creates a new version. **Old versions are never changed.** Projection sets and runs already submitted keep using the exact version they pinned. |
| **AC-9** | Approval is a separate, permissioned step, and every lifecycle step writes an **immutable audit event** with its actor (CAS §10.3, LR1-SEC-007). |
| **AC-10** | `POST /v1/runs/{id}/cancel` and `POST /v1/runs/{id}/retry` work as described in §6. A retried run has the **same configuration fingerprint** as the original. |
| **AC-11** | **"Rebuild from files":** the demo inputs uploaded through the new flow, run against the SPIA model, reproduce the golden numbers in §8.2 **to every digit**. |
| **AC-12** | The known upload gaps are gone (§5.8), the old `/v1/imports/*` endpoints still behave as documented, and `02_BACKEND_FRONTEND_CONTRACT.md` §E.3 and §E.7 describe the new endpoints with real request and response examples. |
| **AC-13** | All tests from both branches pass, plus the new ones. A short report in the format of §9 is handed to Samuel. |

## 3. Read these first

**The authoritative sources** (in `authoritative documents/extracted/`):
- CAS §3.1 – what "implemented" means (5 rules);
- CAS §3.3 – data rules: synthetic or authorized data only;
- CAS §8.1 – data ingestion (this task's core);
- CAS §10.1 – storage;
- CAS §10.3 – minimum controls: role separation, immutable audit, retention;
- CAS §11 – the Data manager and Run center rows;
- CAS §12 and §12.1 – testing, governance and the run manifest;
- CAS §15 – criterion 2 (create project → import → validate → map → run → …) and criterion 13 (an endpoint alone never counts);
- MP §2 (definition of done), §8.1, §13 (ownership) and §14.1–14.3 (Weeks 1–3 backend tasks).

**The build documents** (`docs/build/`):
- `01_HOW_WE_WORK_AND_MILESTONES.md` – §3 (M2 scope), §6 (definition of done), §7 (Git rules: no direct commits to `main`, no self-merge, `.env` never committed);
- `02_BACKEND_FRONTEND_CONTRACT.md`:
  - Part C – the error envelope, statuses and IDs;
  - Part D.3 – planned M2 tables: `import_sessions`, `mapping_profiles`, `validation_issues`;
  - §E.3 – Inputs, including the sketched **M2 import lifecycle** you are now building;
  - §E.7 – the Runs cancel and retry buttons.
- `03_QUESTIONS_AND_DOCUMENT_REQUESTS_FOR_PRODUCT_OWNER.md` – the business data dictionary is still pending, so canonical field lists are **provisional**.

**The project records:**
- `docs/handoff/01_requirements/master-requirement-tracker.md` – the requirement IDs above;
- `docs/handoff/11_project-management/blockers.md` – BLK-016.

**The frontend documents:**
- `frontend_integration_guide.md` and `frontend_api_reference.md` (repository root). The "Known backend issues" table in the reference lists the gaps this task closes.

**Your own plan:** `docs/phase_1_backlog.md` on `backlogs`. Update it when you finish.

**The correction-pass report:** `reviews/WP1C_REPORT.md`. This file is not in Git; Samuel will send it to you. It explains every guarantee you must keep in Part 1.

## 4. Part 1 – Join the branches (do this first)

### 4.1 Why

Both branches start from WP1 (`36b1cce`) and went separate ways. Measured on 2026-10-03:
- **The same 18 files changed on both sides**, including `run_execution_service.py`, `results_service.py`, `run_package_service.py`, `imports.py`, `variables.py`, `run_artifact.py`, `main.py` and `schemas.py`.
- **The database history splits in two.** `backlogs` ends at `c6e1a4b9d203` (registry actor fields); `feat/m1-first-real-run` ends at `c9e2a4b6d8f1` (the correction pass). Both come after `b7e4d2a9c613`.
- **`b7e4d2a9c613` (WP1) was edited on `backlogs` after it was pushed.** The shared `neondb` is still at `4d8e2f6a1c90` (M1), so no shared database has applied either version yet. Accept the current edit, then **freeze** that file: from now on, never edit a migration once it has been pushed.
- **Two results stores exist.** `backlogs` writes results and trace to Parquet artifacts and removed the `run_outputs` and `trace_logs` tables. The correction pass still reads and writes attempt-scoped database rows. `01_HOW_WE_WORK_AND_MILESTONES.md` §7 and CAS §10.1 settle this: **Parquet is the one store.**

### 4.2 Steps

1. Create a branch `feat/m2-convergence` from `origin/backlogs`, then merge `origin/feat/m1-first-real-run` into it.
2. Resolve every conflict. Where both sides changed behaviour, keep **both** behaviours. For example, keep the registry audit **and** the admin-only writes on `/v1/variables/`, and keep the `X-MentorAmp-Complete` CORS header **and** the `context`/`valuation` variable kinds.
3. Move the correction-pass code that touches result rows onto the artifact store:
   - run finalization;
   - deleting a failed attempt's rows;
   - the evidence-consistency gate on results;
   - the frozen scenario ID on every result and trace row.
4. Add an Alembic **merge revision** whose `down_revision` is `("c6e1a4b9d203", "c9e2a4b6d8f1")`. Update `tests/unit/test_wp1_alembic_history.py` so it expects the new single head.
5. Run every test (§8.1), then open a PR from `feat/m2-convergence` to `main`. Samuel reviews it; no self-merge. After the merge, `main` is the only integration branch, and all later work branches from `main`.

### 4.3 Guarantees that must survive the merge

These come from the correction pass. Each is proved by tests in `tests/integration/test_wp1c_corrections.py`, `tests/unit/test_wp1c_rules.py` and `tests/unit/test_wp1c_formula_and_build_identity.py`. Those tests must pass on the artifact store, rewritten only where they read the removed tables directly.

- **Frozen code.** A run executes the formula code and build that were frozen when it was submitted. Otherwise it fails with `FORMULA_IMPLEMENTATION_MISMATCH` or `BUILD_IDENTITY_MISMATCH`.
- **Safe ending.** Finishing a run is atomic and can be recovered. A run whose records disagree withholds its results (`FINALIZATION_INCONSISTENT`).
- **Variables per model version.** Variables are defined per model version (`model_variable_definitions`), and one project can never change another project's numbers.
- **Package identity.** It is checked (`PACKAGE_IDENTITY_MISMATCH`, `RUN_INDEX_MISMATCH`).
- **Evidence is kept.** Run packages, manifests and attempts use RESTRICT foreign keys, plus PostgreSQL triggers that refuse deletion.
- **Run sets.** They record their resolved `scenario_ids` and `resolution`.
- **Scenario rules.** A scenario may only target variables with `allow_scenario_override = true`, using numeric values, periods 0–1200 and no overlapping ranges.
- **Inforce fingerprint v2.** Records are sorted by unique `policy_id`, and duplicate IDs are refused.
- **Honest preflight.** Unbuilt capabilities report `deferred`, not `pass`.
- **Exact totals.** Aggregates are bit-for-bit reproducible.

## 5. Part 2 – The Data Manager lifecycle (the main build)

Start from the lifecycle sketched in `02_BACKEND_FRONTEND_CONTRACT.md` §E.3. You may refine names and shapes, but record every change in the contract. Keep the existing error envelope, the `/v1` prefix, project-membership checks and the `404`-for-other-projects rule.

### 5.1 The flow

| Step | Endpoint (starting design) | What it does |
|---|---|---|
| Open | `POST /v1/import-sessions` with `{project_id, category, name, version_label?, replaces_dataset_id?}` | Starts an import. `category` is `liability_inforce`, `assumption_table`, `factor_table` or `scenario`. `replaces_dataset_id` makes it a new version of an existing dataset. |
| Upload | `POST /v1/import-sessions/{id}/files` (multipart, field `file`) | Accepts CSV, TSV, XLSX and Parquet. Keeps the **raw file** (artifact storage) and its **SHA-256 fingerprint**. Returns the row count, columns and detected format. |
| Preview | `GET /v1/import-sessions/{id}/preview?rows=20` | The first rows and the detected column types **of the uploaded file**. This replaces today's unusable server-path preview. |
| Map | `PUT /v1/import-sessions/{id}/mapping` with `{profile_id}` or `{fields: [{source_column, target_field, transform?}]}` | Maps source columns to the category's canonical fields. Mappings can be saved and reused as **mapping profiles** (`GET/POST /v1/mapping-profiles?project_id=&category=`), and each profile is versioned. |
| Validate | `POST /v1/import-sessions/{id}/validate` → `GET /v1/validation-runs/{id}/issues` | Runs every check in §5.3. Issues are stored with severity, row, column, value and code. Rejected records are kept and listable. |
| Commit | `POST /v1/import-sessions/{id}/commit` | Creates a **new immutable dataset version** with its canonical-records fingerprint, status `validated` or `needs_review`, and a link to the version it replaces. **Refused** if blocking errors exist or validation has not run. Returns the new dataset ID. |
| Approve | `POST /v1/datasets/{kind}/{id}/approve` and `/reject` with `{reason}` | A separate step that needs an approval permission (§5.6). |
| Versions | `GET /v1/datasets/{kind}/{id}/versions` | Lists the version chain, with who, when, fingerprint and status. |
| Compare | `GET /v1/datasets/{kind}/{id}/compare?other_id=` | A record-level difference between two versions, matched by key: counts of added, removed and changed records, plus the first N changes. This is the "compare" function in CAS §11. |

New tables, following contract §D.3: `import_sessions`, `mapping_profiles`, `validation_runs` / `validation_issues` and `rejected_records`, plus the version links on the dataset tables. Large raw files and rejected rows belong in artifact storage; PostgreSQL keeps the metadata (CAS §10.1, LR1-DATA-011). Each table needs a forward and a backward migration.

### 5.2 Canonical fields (provisional, until the business data dictionary arrives through D3)

| Category | Canonical fields | Notes |
|---|---|---|
| `liability_inforce` (SPIA) | `policy_id` (required, unique), `product_type`, `issue_date` (date), `issue_age` (integer), `gender` (`M`/`F`), `premium` (number ≥ 0), `monthly_payment` (number ≥ 0) | Exactly what the SPIA model's input variables need today. `GET /v1/projects/{id}/input-mappings` shows the current mapping. |
| `assumption_table` | Lookup-key columns (for mortality: `age`, `gender`) plus one value column (`qx`) | The table **name and type come from the session**, not from file columns. |
| `factor_table` | Lookup-key columns plus one value column | Same as above. |
| `scenario` | `scenario_name`, `target_variable`, `operation` (`set`, `add`, `subtract`, `multiply`, `percent_change`), `value`, `applies_from_period`, `applies_to_period` | Checked against the chosen model version's variable definitions (§4.3 scenario rules). |

> **Defect to fix:** today's `POST /v1/imports/assumptions` requires `table_name` and `table_type` columns **inside the file**. That means the project's own sample `samples/spia/synthetic_mortality_gompertz.csv` (columns `age,gender,qx`) cannot be imported. Take the name and type from the session instead.

### 5.3 Validation checks (CAS §8.1, LR1-DATA-009)

| Check | Example | Severity |
|---|---|---|
| Required fields | `policy_id` not mapped or empty | error |
| Data types | `issue_age = "abc"` | error |
| Units | a rate given as `4.5` where `0.045` is expected (when the canonical field has a unit) | warning |
| Duplicates | the same `policy_id` twice; the same lookup key twice in a table | error |
| Referential integrity | a scenario targets a variable the model version does not define, or one that is not overridable; a table is missing a lookup key the model needs | error |
| Ranges | `issue_age = 130`; `qx` outside 0–1; `premium < 0` | warning (age, premium) / error (probabilities) |
| Missing values | an empty cell in a required field | error |
| Effective dates | `issue_date` after the valuation date; invalid dates | error |
| Record counts | 0 rows; or the row count differs from an expected count given in the session | error |

### 5.4 Immutability and versions

- A committed version is **never** changed. A correction is a new version.
- Projection sets pin dataset **IDs**, and run packages pin IDs **and fingerprints**, as today. A new version changes nothing that was already pinned or submitted.
- Only `validated` or `approved` versions can be selected for a run (`lifecycle.RUNNABLE_INPUT_STATUSES`).
- The canonical-records fingerprint must not depend on the file format: CSV, TSV, XLSX and Parquet copies of the same data give the same fingerprint (test D-11).
- If storing canonical records changes how values are written (for example `"67"` becomes `67`), introduce a **new fingerprint scheme** (`inforce-v3`) rather than changing `inforce-v2`. Existing files keep their scheme, as the correction pass did for v1 → v2.

### 5.5 Rejected records and import log (LR1-DATA-010)

- Keep every rejected row with its reasons, and list them through the API.
- The import session keeps a log of who did each step, when, and with what counts and fingerprints.

### 5.6 Permissions and audit

- **Uploading, mapping, validating and committing** need `imports:write` and owner or editor membership of the project. Viewers get `403`, and other projects return `404`.
- **Approving and rejecting** need a separate approval permission held by `actuary`, `reviewer` and `admin`, but **not** by `model_developer` (role separation, CAS §10.3).
- **No self-approval.** The user who committed a version cannot approve it (peer review, CAS §12 Governance).
- **Audit events.** Every open, upload, map, validate, commit, approve and reject writes an immutable audit event in the same transaction, using the `audit_logs` table and actor fields you already built.
- **Archived projects** reject every step with `409`, as today.

### 5.7 Keep the old endpoints working

`POST /v1/imports/inforce|assumptions|factors|scenarios` stay available, marked deprecated in OpenAPI. They become thin wrappers over a one-shot session: open → upload → default mapping → validate → commit when there are no errors. They must also:
- return the **new dataset ID**;
- return `422` when nothing was stored (not `201` with `stored_count: 0`);
- actually store assumption, factor and scenario files.

### 5.8 Upload gaps this part must close

These are listed in `frontend_api_reference.md` under "Known backend issues".
- The upload response has no file ID.
- The upload returns `201` even when nothing was stored.
- Assumption, factor and scenario uploads are never stored.
- `GET /v1/imports/preview` only reads server paths.
- Inforce range warnings do not appear in `GET /v1/projects/{id}/validation-issues`.

## 6. Part 3 – Run cancel and retry (LR1-API-005, contract §E.7)

| Endpoint | Behaviour |
|---|---|
| `POST /v1/runs/{id}/cancel` | **Pending run:** becomes `cancelled` immediately and never starts.<br>**Running run:** records a cancel request. The worker checks it between policies and ends the run as `cancelled` with a final manifest and a `cancelled` event.<br>A cancelled run exposes **no results** (`409 RESULTS_NOT_AVAILABLE`). Its partial artifacts stay as non-canonical evidence.<br>Cancelling a finished run returns `409`.<br>Needs `runs:execute` and owner or editor membership. |
| `POST /v1/runs/{id}/retry` | Allowed for `failed`, `cancelled` and `partial_success` runs. Creates a **new run** in the same run set from the **same frozen configuration**: same configuration fingerprint, not re-read from live data. It links back to the original (`retried_from_run_id`).<br>Retrying a `success` run returns `409`.<br>If the server's code build has changed since the original was submitted, the frozen build no longer matches and the retry must be refused (`BUILD_IDENTITY_MISMATCH`). That is correct: the user must submit a fresh run on the new build. |

The run set's status must update correctly in both cases. Add both endpoints to the run and attempt views (`can_cancel`, `can_retry` flags are useful for the UI).

## 7. Not in this task

- **The MYGA model and formulas** (M2 Part B). That is engine work owned by Samuel, and it waits for the MYGA reference case requested in D3.
- **Formula Studio lifecycle and reconciliation** against business expected results (M3).
- **Expected Results import** (M3), and **Asset Inforce mapping** (optional stretch; Asset Inforce uses your `asset_positions`).
- **S3 object storage, a durable worker queue, Dagster, PySpark, GPU** (M4+ / MP Weeks 5–6).
- **Changing the illustrative SPIA mathematics**, inventing actuarial assumptions, or using real policyholder data (CAS §3.3).
- **Editing Sam Yung's frontend repository.** He wires the screens from the contract.
- **Migrating the shared `neondb`.** Use disposable databases only. The shared database is upgraded once, after the PR is merged and Samuel approves.

## 8. What to test and the expected results

Run the SQLite suite with `DATABASE_URL="sqlite://" pytest tests` (or `docker compose exec -T api pytest -q`). Run the PostgreSQL suite with `MENTORAMP_TEST_POSTGRES_URL=<disposable database whose name contains "test"> pytest tests/postgres`. **Never** point it at `neondb`.

### 8.1 Part 1 – Joining the branches

| Test | Expected result |
|---|---|
| Every test file from **both** branches | All pass. The count is at least the union: `feat/m1-first-real-run` has 195 passed + 9 PostgreSQL; `backlogs` has its own suite. Nothing is skipped except the PostgreSQL tests when no test URL is set. |
| `alembic heads` | Exactly **one** head (the new merge revision). |
| Migration: empty database → head | Succeeds. |
| Migration: M1 head `4d8e2f6a1c90` **with M1 demo data** → head (this is the path the shared `neondb` will take) | Succeeds. Legacy runs remain readable. Inforce files are re-fingerprinted v2 or flagged `needs_review`, as in the correction pass. Nothing is marked validated by mistake. |
| Migration: `backlogs` head `c6e1a4b9d203` → head, and correction-pass head `c9e2a4b6d8f1` → head | Both succeed with their data kept. |
| Downgrade one step, then upgrade again | Succeeds, with no data loss in the tables the migration keeps. |
| `tests/integration/test_wp1c_corrections.py`, `tests/unit/test_wp1c_rules.py`, `tests/unit/test_wp1c_formula_and_build_identity.py` | All pass on the Parquet artifact store. |
| Your `test_artifact_storage.py`, `test_registry_audit.py`, `test_inforce_record_viewer.py`, `test_products_api.py`, `test_dashboard_api.py`, `test_api_contract.py` | All pass. |
| `GET /v1/variables/` on the seeded demo | `200`, and it includes the `context` variables (`projection_month`, `attained_age`). |
| The CSV export with an `Origin` header | `Access-Control-Expose-Headers` includes `X-MentorAmp-Complete`. |

### 8.2 Golden numbers (must not change by a single digit)

These come from the synthetic SPIA demo: 25 policies, a valuation date of 2026-12-31 and the illustrative formulas. They were verified on M1, WP1 and the correction pass.

| Run | Value |
|---|---|
| Base, 600 months: reserve at valuation | **4,889,731.244841259** |
| Low Interest Rate, 600 months: reserve at valuation | **5,541,437.408305269** |
| Total undiscounted expected payments (both scenarios) | **7,419,301.206836598** |
| Base, 24 months: reserve | **893,043.9157255866** |
| Low Interest Rate, 24 months: reserve | **906,298.662195956** |
| Trace: `pv_expected_payment`, policy `SPIA-0001`, month 12, Base | **1,533.2093195295497** |
| Two identical submissions | the **same** run-package fingerprint and bit-identical aggregates |

### 8.3 Part 2 – Data Manager

| # | Test | Expected result |
|---|---|---|
| D-1 | **Rebuild from files.** In the seeded demo project, upload `samples/spia/synthetic_spia_inforce.csv` (inforce) and `samples/spia/synthetic_mortality_gompertz.csv` (assumption table `SYNTH_MORT_GOMPERTZ_2026`, keys `age` and `gender`, value `qx`). Also upload a scenario file with one row: `Low Interest Rate (file)`, `discount_rate_annual`, `set`, `0.03`. Commit, approve, create a projection set that pins **only** these new versions, validate, and run Base plus the file scenario. | Base reserve **4,889,731.244841259**; file-scenario reserve **5,541,437.408305269**, to every digit. The mortality table and inforce versions in the run package are the uploaded ones, not the seeded ones. |
| D-2 | **New layout, same data.** Rename the inforce columns (for example `PolicyNo`, `Age`, `Sex`, `MonthlyAnnuity`), map them with a saved profile, and commit. | Same canonical fingerprint as D-1, the same reserves, and **no formula or product file changed** (LR1-DATA-008). |
| D-3 | Reuse a saved mapping profile on a second upload | The mapping is applied with no manual steps, and the profile version is recorded on the session. |
| D-4 | Missing `policy_id` column | Validation error; commit refused (`409`/`422`); nothing stored; rejected records listed. |
| D-5 | Duplicate `policy_id` | Error naming both rows; commit refused. |
| D-6 | `issue_age = 130` and `premium = -5` | Warnings. The commit succeeds with status `needs_review`, and the issues appear in `GET /v1/projects/{id}/validation-issues`. |
| D-7 | `issue_age = "abc"`, an empty required cell, or `issue_date` after the valuation date | Errors; commit refused. |
| D-8 | Mortality table with `qx = 1.4` or a duplicate `(age, gender)` | Errors; commit refused. |
| D-9 | Scenario targeting `mortality_rate_annual` (not overridable), an unknown variable, or overlapping periods | Error codes `SCENARIO_TARGET_NOT_OVERRIDABLE`, `SCENARIO_TARGET_UNKNOWN`, `SCENARIO_OVERRIDE_OVERLAP`; commit refused. |
| D-10 | Empty file; unsupported extension (`.pdf`) | Error / `400`; nothing stored. |
| D-11 | CSV, TSV, XLSX and Parquet copies of the same inforce data | The same canonical fingerprint for all four. |
| D-12 | Commit v2 of an inforce file while a projection set pins v1 and a run was submitted on v1 | v1 is unchanged (same fingerprint). The run executes v1. The projection set still pins v1. `versions` lists v1 and v2. |
| D-13 | `compare` v1 and v2 (one policy changed, one added) | `changed: 1`, `added: 1`, `removed: 0`, with the changed fields shown. |
| D-14 | A viewer uploads; a user from another project reads a session; anyone uploads to an archived project | `403` / `404` / `409`. |
| D-15 | A `model_developer` approves; the user who committed approves their own version | Both refused (`403`). An `actuary` who is not the committer can approve. |
| D-16 | Audit | Each lifecycle step has exactly one audit event, with the right actor, entity, and before/after state. |
| D-17 | Raw file kept | The stored raw-file fingerprint equals the SHA-256 of the uploaded bytes, and the raw file can be retrieved by an authorized user. |
| D-18 | Old endpoints | `POST /v1/imports/inforce` returns the new dataset ID; it returns `422` (not `201`) when nothing is stored; assumption, factor and scenario uploads are stored. |

### 8.4 Part 3 – Cancel and retry

| # | Test | Expected result |
|---|---|---|
| R-1 | Cancel a **pending** run | `cancelled` at once. It never starts: no attempt is claimed and there is no `claimed` event. Results return `409`. |
| R-2 | Cancel a **running** run (use a test hook that pauses between policies) | It ends `cancelled`, with a final manifest and a `cancelled` event. Results return `409`. Partial artifacts are kept but not exposed. |
| R-3 | Cancel a `success` run | `409`; the run is unchanged. |
| R-4 | A run set whose runs are all cancelled | Run-set status `cancelled`. With one success and one cancelled run, the status follows the documented rule. |
| R-5 | Retry a failed run (force a failure with a test hook), then let it succeed | A new run with the **same configuration fingerprint**, linked through `retried_from_run_id`. Base reserve **4,889,731.244841259**. |
| R-6 | Retry a `success` run | `409`. |
| R-7 | Cancel or retry as a `viewer`, or on another project's run | `403` / `404`. |

## 9. What to hand back

Open the PR (or PRs: Part 1 first, then Parts 2 and 3) with a report in this order:

1. **Summary.** What was built, in five lines.
2. **Branch and commits.** The PR links and the head commit.
3. **Migration graph** before and after, and the output of `alembic heads`.
4. **Merge decisions.** Every conflict and which behaviour was kept.
5. **New endpoints and tables**, with links to the updated contract sections (§E.3 and §E.7).
6. **Test results.** The SQLite count, the PostgreSQL count, and any test skipped or failed, with the real output.
7. **The golden-numbers table** (§8.2), filled with the values you measured.
8. **The D-1 to D-18 and R-1 to R-7 results**, each pass or fail, with evidence.
9. **The acceptance checklist** AC-1 to AC-13, each ✅ or ❌, with a reason.
10. **Open issues and decisions** you need from Samuel.
11. **Updated files:** `docs/phase_1_backlog.md` and `02_BACKEND_FRONTEND_CONTRACT.md`. Samuel will regenerate `frontend_api_reference.md` from the merged code.

## 10. Rules that apply throughout

- Branch from the right base. Open PRs; there is **no self-merge** (`01_HOW_WE_WORK_AND_MILESTONES.md` §7).
- **Do not migrate or seed the shared `neondb`.** Use disposable databases only.
- **Never edit a migration after it has been pushed.** Add a new revision instead.
- Never commit `.env`, passwords, tokens or database URLs.
- Synthetic or authorized data only (CAS §3.3). Do not change the illustrative SPIA formulas.
- Every claim in the report must have evidence: a test, a command output or a file path. A screen, endpoint or schema alone never counts as done (CAS §15, criterion 13).
