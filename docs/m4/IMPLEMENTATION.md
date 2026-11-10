# M4 indexed foundation engineering handoff

Status: **IMPLEMENTED, PENDING SENIOR ACTUARIAL RE-REVIEW**.
Controlling specification: `NOAH_M4_FIA_RILA_IMPLEMENTATION_README.md`.
Baseline: `98039a2`. Branch: `feat/m4-fia-rila-foundation`.

## Existing implementation comparison

At the baseline, the FIA/RILA configs, golden cases and shared indexed/accounting
modules were empty. SPIA and MYGA already supplied declarative variables, named
formula dependencies, prior-output state, published-output aggregation metadata,
frozen packages, attempt-scoped Parquet, captured trace, and reconciliation.
The initial uncommitted M4 work added arithmetic and registrations but had
disconnected account-value formulas, missing segment producers, incompatible
function arguments, no executable block harness and empty tests.

The implementation follows those existing contracts. The actual engine entry is
`run_policy(RunData, PolicyRecord, functions)` (the controlling README calls it
`run_projection`). It is retained without product logic or a second engine.
`run_execution_service` remains the production buffered, attempt-scoped writer;
the older `RunArtifactBuffer` is not used to create a parallel execution path.
Generic persistence extensions read optional dimensions from frozen variable
metadata and infer schemas across full partitions so late segment fields survive.
No database migration is needed: existing JSON definitions, tables, packages and
artifact metadata support the additions.

## Implemented behavior

- Shared cap/participation/spread, averaging-return, buffer/floor, fixed
  compounding, signed account-value credit, withdrawal, surrender and exact/range
  factor primitives. Missing and overlapping factor matches fail.
- Complete FIA/RILA formula producers, two segment slots, fixed account,
  prior-month state, maturity observations and published balances/cash flows.
  Core formula dependencies derive from callable signatures.
- Explicit terms, start observations, elapsed months, protection and participation.
  Fixed interest compounds monthly. Interim valuation is configurable; maturity
  credits apply to segment bases. Renewal compounds from prior maturity values;
  sweep transfers once into fixed and leaves the segment inactive.
- EOM order: value accounts, allocate withdrawals and fees in proportion to their
  pre-deduction values, adjust bases with the configured pro-rata/dollar-for-dollar
  rule, then transfer maturity sweeps. Overdraws fail.
- Death benefit can use AV or greater-of-AV-and-guarantee. Minimum guarantee and
  MVA are explicit external inputs; no statutory formula or rate is invented.
- Product-specific Data Manager schemas check allocation conservation, balances,
  terms, strategies, start observations and configurable rules.
- Scenario lookup binds `(path_id, month, index_code)` to a pinned factor table.
  Four distinct paths: Base, Bull, Bear, Stress.
- Representative RILA block: 30 policies, SP500/RUSSELL2000, 12/36-month terms,
  buffer/floor profiles, fixed allocations, and 72 projection months.
- Integration tests exercise submission, package SHA-256, engine execution,
  accepted Parquet, segment dimensions, cell trace, scenario preservation,
  independent engineering reconciliation and foreign-project rejection.

## Engineering anchor

Policy 001 starts with 25,000 fixed and 25,000 indexed. At month 12 under Base:
`25,000 * 1.03 + 25,000 * 1.04 = 51,750`.
This is independently written arithmetic, never copied from engine output.
Other illustrative tests supply hand-calculated loss, anniversary and withdrawal
examples. Separate owner-approved golden tests are explicitly skipped pending
independent oracles. Numeric reconciliation PASS for an engineering anchor does
not confer actuarial VERIFIED or ACCEPTED status.

## Running and evidence

Local validation on 2026-11-01: full suite **545 passed, 28 skipped**, with one
upstream Starlette/httpx deprecation warning. M4-specific run: **34 passed,
2 skipped** (independent owner oracles). Explicit security/reconciliation matrix:
**25 passed**. Final metadata checks: **11 passed, 2 skipped**. Canonical Alembic
head: `a6c1f4e8d203`. Disposable PostgreSQL execution was not available.

From `backend`, with the development environment configured:

```powershell
python -m scripts.seed_indexed
python -m scripts.review_indexed --output ../.m4-review/evidence
python -m pytest -q -p no:cacheprovider --basetemp=../.m4-review/pytest-temp
python -m scripts.package_m4_review
```

The seeder creates isolated FIA/RILA projects using existing catalog helpers,
validators and submission services. It never invokes the MYGA seeder and refuses
to edit approved/published indexed models. No actuarial approvals are fabricated;
packages are explicitly sandbox until review, retaining all source fingerprints.
The review runner uses fresh in-memory SQLite, never the configured application
database. It exports actual policy/trace Parquet, derived aggregate Parquet,
schemas, checksums, cell trace, frozen manifests and engineering reconciliation.

PostgreSQL migration tests require `MENTORAMP_TEST_POSTGRES_URL` referencing an
explicitly disposable test database. Single-head history is checked without a
database. Skipping PostgreSQL tests is not migration execution evidence.

## Outstanding owner decisions and limitations

ACT-M4-DEC-01 through ACT-M4-DEC-15 all remain pending confirmation. Sample caps,
rates, scenarios and tolerances are engineering fixtures, not locked contract terms.

- `prorated_intrinsic` is an illustrative approximation. `external` requires
  supplied pre-withdrawal interim values. AG54 replication is not claimed.
- The sample declares two slots. The scalar transition primitive and shared
  engine impose no two-segment limit; more slots require more declarations.
- Averaging and range lookup are infrastructure. The sample FIA uses PTP/spread;
  option budget is a pinned, traceable factor, not calibrated option pricing.
- Final Model 805, surrender/MVA, benefit/claim timing, fees and independent
  expected results remain owner decisions. Guarantee/MVA inputs are explicit.
- Fees and withdrawals are supported. Mortality-weighted claims, reserve, hedge,
  regulatory compliance and independent actuarial acceptance are not claimed.
- Aggregate Parquet is a derived review artifact of accepted results. Canonical
  artifacts remain the existing attempt-scoped policy outputs and traces.
- Segment Parquet rows retain `segment_id`, `strategy_type`, and `index_code`.
- Governed reference runs require actual approval of model/data/oracles. The
  runner produces sandbox evidence with fingerprints.

MYGA sample/reference CSVs and its reconciliation baseline are unchanged.
The archive records exact tests and unavailable checks and must not be represented
as final actuarial acceptance.
