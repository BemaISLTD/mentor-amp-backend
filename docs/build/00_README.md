# docs/build — the build documents (start here)

| File | For | What it is |
|---|---|---|
| `01_HOW_WE_WORK_AND_MILESTONES.md` | Whole team, the boss | How we work, milestones M0–M4, who does what, git rules, weekly rhythm, the M1 boss-demo script |
| `02_BACKEND_FRONTEND_CONTRACT.md` | Sam Yung, Noah, Samuel | **The single agreement** on tables, endpoints, JSON, statuses and every button on every screen. Includes the engine contract, seed data, TypeScript types and wiring order. |
| `03_QUESTIONS_AND_DOCUMENT_REQUESTS_FOR_PRODUCT_OWNER.md` | The Product Owner (Eko), Actuarial Product Owner | What the team needs from the business: reference cases, expected results, tolerances, data, the seven confirmations |
| `04_NEXT_TASK_NOAH_README.md` | Noah (reviewer: Samuel) | WP2 / M2 Part A: join `backlogs` with the correction pass, build the Data Manager lifecycle (upload → map → validate → version → approve), run cancel and retry. Includes acceptance criteria, tests and expected results |
| `05_M2B_MYGA_PLAN.md` | Samuel (Noah: Data Manager schema piece) | M2 Part B: the 5-year MYGA model built from the LifeLib U.S. technical notes (spec and answer key, no copying), plus the SPIA engine cross-check against LifeLib (exact match). Includes acceptance criteria, worked-example numbers and tests |
| `06_FRONTEND_SYNC_TASKS_FOR_SAM.md` | Sam (reviewer: Samuel) | Everything the frontend must fix or connect to match the backend (`f12d211` vs `fffc396`): wrong statuses, partial results, trace capture, roles; Data Manager, cancel/retry, run evidence, admin; clean-up; what not to build |
| `source-text/mentor_amp_frontend_update.md` | Reference | Sam Yung's 9-page PDF converted to text |

The authoritative DOCX sources are already converted to text with tables in `authoritative documents/extracted/` (verified exact; see `EXTRACTION_MANIFEST.md`).

Authority order: CAS → requirement tracker → these build documents → the UI → sample data.
