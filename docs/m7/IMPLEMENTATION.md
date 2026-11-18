# Milestone 7 Implementation Evidence

## Scope and classification

This branch implements the Phase 7 IDI, VA, PRT, LIFE-readiness, and governed
reporting work package from the approved `origin/main` baseline
`efb9657b084dff980d65326c5c244bfd043bd695`.

- Implementation commit: `7943f059c64036fb5fe262c9a23a173c286aa922`
- Implementation diff from baseline: `38 files changed, 4025 insertions(+), 10 deletions(-)`

All model inputs, examples, and numerical anchors delivered here are synthetic
engineering fixtures. They are not production-approved actuarial assumptions,
accounting statements, or owner-signed reference models.

- Production approval: `false`
- Reporting classification: `DISCLOSURE_SUPPORTING_INFRASTRUCTURE_NON_FINAL`
- Actuarial decision status: `ACT-DEC-01..09` and `ACT-P7-DEC-01..07` remain open
- Owner-signed IDI/VA/PRT oracle tests remain explicitly skipped

## Delivered components

- Pure shared primitives for disability, variable-annuity, and PRT benefits.
- Shared-engine vertical slices for IDI, VA, and PRT; all are registered through
  the existing product and inforce registries.
- Canonical Data Manager schemas for IDI, VA, PRT, and LIFE, including LIFE
  premium-frequency fidelity and multi-coverage/rider validation.
- LIFE v1/v2 immutable retention and four-eyes approval evidence without a DB
  model or Alembic change. Full LIFE actuarial calculations remain excluded.
- Governed report materialization from checksum-verified accepted Parquet
  artifacts with honest unavailable states, non-final flags, immutable
  publication, CSV export, and audit events.
- Synthetic samples and isolated `seed_phase7.py` / `review_phase7.py` scripts.
- Unit, engineering-anchor, and isolated end-to-end tests.

## Numerical engineering anchors

- IDI elimination, maximum-benefit, waiver-of-premium, and decrement behavior is
  tested independently in the shared-module and golden-policy suites.
- VA explicitly distinguishes finite GMWB from lifetime GLWB and validates all
  three supported fee-conversion methods.
- PRT life-only month-one expected benefit is independently reconciled as:

  `1000 * 0.98 ** (1 / 12)`

  The isolated review runner requires the exact value to return `PASS` at
  `abs_tol=1e-6`, and the same value plus `$1.00` to return `FAIL`.

## Reproducible evidence runner

From `backend/`:

```powershell
python -m scripts.review_phase7 --output ../.phase7-review
```

The runner creates a temporary SQLite database, executes all IDI/VA/PRT
scenario runs, verifies frozen RunPackages and physical Parquet checksums,
builds trace trees, performs PASS/FAIL reconciliation, confirms LIFE v1/v2
four-eyes evidence, and generates, reads, exports, publishes, and versions a
governed report. It writes `phase7-evidence.json` under the selected output
directory and never connects to the configured application database.

## Database and protected-area proof

- Canonical Alembic head remains `c9f4e2d8a615`.
- No migration or ORM schema files are modified.
- No Phase 6 orchestration, PySpark/GPU, CI workflow, frontend, or existing
  SPIA/MYGA/FIA/RILA product baseline file is modified.
- Shared-file changes are additive registry, normalization, CORS/header, and
  Data Manager validation hooks required by the approved work package.
- Dagster is pinned in `backend/requirements.txt` because the integrated M5
  graph is imported by the normal application test/runtime path.

## Verification record

Final verification commands:

```powershell
python -m pytest -q tests
python -m pytest -v tests/postgres tests/integration/test_phase7_e2e_workflow.py
```

Focused verification completed during implementation:

- Phase 7 seed/schema/PRT golden checks: `22 passed, 2 skipped`.
- Isolated Phase 7 E2E workflow: `1 passed`.
- Phase 7 focused product, Data Manager, report, seed, and golden suites:
  `51 passed, 3 skipped`.
- Post-review reporting, shared-module, LIFE-gate, and E2E regression suite:
  `21 passed`.
- Exact final full SQLite suite: `796 passed, 33 skipped, 1 warning` in
  `2364.50s`. The warning is the known Starlette `TestClient`/`httpx`
  deprecation warning and is not a calculation or API failure.
- Exact final disposable PostgreSQL 15 suite plus Phase 7 E2E:
  `26 passed, 1 warning` in `966.07s`. The database name was
  `mentoramp_phase7_test`; it was removed after verification. Canonical
  Alembic head: `c9f4e2d8a615`.

The skips include the owner-signed actuarial oracle placeholders and other
explicitly gated or environment-specific cases; none is replaced by an
illustrative approval claim.
