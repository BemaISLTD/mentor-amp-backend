# SPIA Reference Model Gap Assessment

## Status

- Assessment date: 2026-10-14
- MentorAmp baseline commit: `fffc396`
- Working branch: `feat/actuarial-reference-models`
- MentorAmp model assessed: `backend/app/products/spia_lite/config.py`
- External reference: LifeLib `uslib` Single Premium Immediate Annuity documentation
- Pinned upstream release: LifeLib `v0.17.2`
- Pinned upstream commit: `b706d8bbab530cc9ef94e06600e9c9d1468d8cb1`

## Baseline verification

- Alembic has one canonical head: `d4f7a9c2e681`.
- Isolated Docker test run: 268 passed, 19 skipped, one known Starlette/httpx deprecation warning.
- Tests were forced to `DATABASE_URL=sqlite://`; the shared Neon database was not contacted.
- PostgreSQL tests were skipped because no disposable PostgreSQL test URL was supplied.

## Scope boundary

LifeLib is an external development benchmark only. It will not be vendored, imported by the MentorAmp API, required in production, or executed when a user runs a model.

MentorAmp will implement its own formulas and data contracts. Small, deterministic expected-result fixtures may be produced independently from a pinned LifeLib release and committed with attribution and provenance.

LifeLib's U.S. SPIA is a draft reference liability cash-flow model. It is not a pricing or reserving model. Its public notes state that discounting and reserves are outside its calculation scope. MentorAmp's illustrative discounting and reserve outputs therefore remain a separate valuation layer and must not be described as LifeLib-reconciled results.

## Current MentorAmp capability

The existing `spia_lite` model supports:

- One annuitant per policy.
- Sex-based annual mortality lookup by attained age.
- Synthetic Gompertz mortality.
- Constant-force conversion from annual mortality to monthly mortality.
- Monthly payments in arrears, conditional on end-of-month survival.
- A flat annual discount-rate override by scenario.
- Present value of expected payments.
- An illustrative prospective reserve.
- Policy-level monthly output, trace rows, deterministic execution, and Parquet run artifacts.

The current in-force sample contains `policy_id`, `product_type`, `issue_date`, `issue_age`, `gender`, `premium`, and `monthly_payment`. Only gender and monthly payment are direct model inputs; premium is not currently used by the calculations.

## Capability comparison

| Area | Baseline MentorAmp SPIA at assessment | LifeLib reference SPIA | Required action |
|---|---|---|---|
| Projection grid | Monthly; first projected month is numbered 1 from the valuation date | Monthly; zero-based policy month from annuity date | Define an explicit translation and test off-by-one behavior |
| In-force starting point | Existing policies can begin after issue using issue date and valuation date | Reference examples project from annuity date | Preserve MentorAmp in-force capability and define opening-state requirements |
| Lives covered | Single life only | Single and joint life | Add primary and optional joint-annuitant state variables |
| Mortality | Synthetic annual qx by attained age and sex | Separate base mortality, improvement scale, A/E factor, and rating factor | Add versioned mortality components without representing synthetic tables as published bases |
| Benefit forms | Life only | Life only, life certain, cash refund, installment refund, and certain only | Add a validated benefit-form field and form-specific mechanics |
| Survivor benefit | Not supported | Survivor percentage with `either` or `primary` reduction trigger | Add survivor percentage and trigger logic |
| Certain period | Not supported | Explicit or refund-derived certain period | Add certain-month state and payment floor |
| Payment frequency | Monthly only | Monthly, quarterly, semiannual, and annual | Add frequency schedule and zero payment in non-payment months |
| Payment timing | Arrears only | Arrears or advance | Add timing and survival-point rules |
| COLA | Not supported | Fixed compound COLA at policy anniversaries | Add annualized income state and anniversary update |
| Cash refund | Not supported | Remaining premium returned on terminal death | Add cumulative scheduled installments and last-death density |
| Installment refund | Not supported | Payments continue until premium is recovered | Add refund-derived certain period |
| Premium tax | Premium is unused | Reduces premium used in refund/income mechanics | Add explicit premium-tax treatment |
| Expenses | Not supported | Monthly maintenance expense while an obligation remains | Add versioned expense assumptions and obligation indicator |
| Commutation | Not supported | Optional commutation of remaining certain payments | Implemented in Increment 7 with strict opt-in and regulatory guardrails |
| Cash-flow outputs | Expected payment and discounted expected payment | Annuity, refund, commutation, expense, liability cash flow, and net cash flow | Add component outputs and a documented sign convention |
| Discounting/reserve | Included as illustrative MentorAmp outputs | Explicitly outside reference cash-flow scope | Keep as a separate MentorAmp-only valuation layer |
| Model identities | Survival and reserve/PV consistency | Life rollforward, payment-factor, and cash-flow identities | Add identities for each new state and component |
| Reference fixtures | Internal independent calculation of the same simplified formulas | Documented worked examples and tests | Add independently generated, pinned expected-result fixtures |
| Provenance | Model labelled illustrative; synthetic mortality identified | Product, technical, implementation, and source notes distinguish sourced facts from standardizations | Add structured reference and assumption provenance |

## Implementation decisions

### Preserve the existing model

Do not silently change the meaning of `spia-illustrative-m1`. Existing tests and frozen run identities depend on it. Implement the expanded calculation as a new model/version while retaining the current model as the historical minimal example until an explicit retirement decision is made.

### Reconcile cash flows, not reserves

The LifeLib comparison boundary will include survival states, payment factors, benefit cash-flow components, expenses, liability cash flow, and net cash flow. MentorAmp discount factors, present values, and reserves will have separate native tests because they have no matching LifeLib SPIA output.

### Preserve Data Manager governance

Model points, mortality, improvement, expense assumptions, and any surrender-charge table must enter through versioned datasets and approval controls. Values must not be embedded in formula code when they are business data or assumptions.

### Keep sourced facts separate from standardizations

Every assumption must identify whether it is:

- Contractual or regulatory source material.
- An upstream reference-model standardization.
- A MentorAmp synthetic or illustrative assumption.
- Company data supplied later by an authorized owner.

### Gate commutation explicitly

Commutation relies on withdrawal elections, discounting, surrender charges,
minimums, and residual-payment floors. It is therefore implemented only after
the core benefit forms reconcile, disabled by default, labelled illustrative and
unverified, and excluded from statutory CARVM use.

## Delivery sequence

### Increment 1: metadata and compatible anchor

- Add structured external-reference metadata without a database migration if the existing JSON metadata fields are sufficient.
- Define a new SPIA reference model version.
- Add one single-life, monthly-arrears, life-only anchor case.
- Reconcile undiscounted survival and annuity-payment output.
- Retain MentorAmp discounting and reserve as separately tested outputs.

### Increment 2: payment schedule mechanics

- Add annualized income, frequency, timing, and COLA.
- Test payment dates, anniversary changes, advance/arrears timing, and month-index translation.

### Increment 3: joint-life mechanics

- Add joint-annuitant mortality and survival.
- Add survivor percentage and reduction trigger.
- Test both-death ordering and last-survivor projection length.

### Increment 4: guaranteed and refund forms

- Add life-certain and certain-only forms.
- Add cash-refund and installment-refund forms.
- Test cumulative payments, terminal-death refunds, and the certain-period payment floor.

### Increment 5: expenses and full cash-flow statement

- Add maintenance expense and inflation assumptions.
- Publish component cash flows, liability cash flow, and net cash flow.
- Add rollforward and cash-flow identity tests.

### Increment 6: end-to-end reference workflow

- Import, validate, commit, and approve all required datasets through Data Manager.
- Execute the new model and persist canonical Parquet artifacts.
- Reconcile selected outputs against pinned external fixtures.
- Rebuild from stored files and prove reproducibility.
- Run the full SQLite suite and the PostgreSQL suite against a disposable Docker database.

### Increment 7: commutation

- Add strict opt-in commutation of the remaining certain-payment slice only.
- Govern the surrender-charge schedule as an approved factor-table version.
- Treat utilization, discounting, minimum-withdrawal and minimum-payment parameters
  explicitly as illustrative and unverified.
- Publish the gross withdrawal, net payment, retained charge, commuted fraction and
  remaining commuted value with cash-flow identity coverage.
- Exclude the feature explicitly from statutory CARVM use.

## Initial acceptance criteria

Increment 1 is complete when:

1. The historical `spia-illustrative-m1` behavior remains unchanged.
2. The new model/version carries external source, version, retrieval date, status, and limitation metadata.
3. A single-life monthly-arrears anchor reconciles undiscounted monthly survival and payments to the pinned external fixture within an explicit tolerance.
4. No LifeLib source or runtime package is present in the application image or production requirements.
5. The full existing test suite still passes.
6. Tests confirm the external fixture's provenance and the separation between reference cash flows and MentorAmp-only valuation outputs.

## Increment 1 verification

Implemented on `feat/actuarial-reference-models`:

- Native definition: `backend/app/products/spia_reference/config.py`
- External-boundary metadata: `backend/app/products/spia_reference/metadata.py`
- Pinned expected values: `backend/tests/fixtures/spia_reference_lifelib_v0_17_2.json`
- Reconciliation tests: `backend/tests/unit/test_spia_reference_engine.py`

The anchor is a synthetic model point added only inside the disposable reference environment:

- Male, age 65 at annuity commencement.
- Single life and life only.
- Annualized income of $6,000.
- 12 payments per year, monthly in arrears.
- No COLA and rating factor 1.0.
- Table mortality basis and annuity year 2026.

The first 24 months cover attained ages 65 and 66 and the policy-year/calendar-year transition. MentorAmp reconciles the exact unrounded end-of-month survival probability and expected annuity payment for every month at relative and absolute tolerance `1e-12`.

For diagnostic continuity, the fixture also records LifeLib's full 660-month total annuity payment, ending survival, and its two internal identity-check outcomes. Those full-horizon diagnostics are not yet MentorAmp acceptance assertions because Increment 1 intentionally commits only the two age rows required by the 24-month governed anchor.

The external runner used a separate `mentor_amp_lifelib_ref` container. The running `mentor_amp_api` container was used for all MentorAmp tests. LifeLib and modelx were not added to MentorAmp requirements, the API image, or the runtime workflow.

Verification results after implementation:

- Targeted metadata, reference-model and historical SPIA tests: 12 passed.
- Run-package provenance and existing end-to-end tests: 25 passed.
- Complete isolated backend suite after Increment 1: 277 passed, 19 skipped, one known Starlette/httpx deprecation warning.
- Alembic heads: one (`d4f7a9c2e681`).
- Shared Neon database: untouched.

## Increment 2 verification

Payment scheduling now supports 1, 2, 4 or 12 payments per year, advance or arrears timing, and fixed compound COLA applied at policy anniversaries. Invalid frequencies and timing values fail explicitly.

The shipped LifeLib model point 10 (`IA-000010`) is frozen in the fixture as the independent advance/COLA case. Its first 16 months verify:

- The first advance installment uses opening survival.
- A 3% COLA changes annualized income from $6,000 to $6,180 in month 13.
- The month of death still receives an advance installment when the life was alive at the start of that month.
- No installment is paid after opening survival reaches zero.

Native engine tests additionally verify quarterly payment-month selection for both timing conventions. MentorAmp's separate valuation layer translates advance payments onto the engine's month-end reserve grid, so the first advance payment is undiscounted and reserve-at-valuation equals the sum of timing-correct present values.

Complete isolated backend suite after Increment 2: 285 passed, 19 skipped, one known Starlette/httpx deprecation warning. Shared Neon remained untouched.

## Increment 3 verification

Joint-life support now includes separately governed mortality lookups and survival recursion for the joint annuitant. Under the explicitly illustrative independence assumption, last-survivor probability is `l1 + l2 - l1*l2`.

The life-contingent payment factor implements both upstream reduction triggers:

- `either`: full payment while both lives survive, then the survivor percentage while exactly one survives.
- `primary`: full payment while the primary survives regardless of the joint annuitant's status, then the survivor percentage while only the joint annuitant survives.

The pinned fixture contains:

- LifeLib model point 8 as a full 24-month table-basis joint-life reconciliation across primary survival, joint survival, last-survivor probability, payment factor and annuity payment.
- LifeLib model points 1–4 as scenario checks covering secondary-first and primary-first deaths under both reduction triggers.
- Native edge checks proving payments stop after the last death.

Single-life contracts publish zero joint survival and preserve their previous cash flows. The complete isolated backend suite after Increment 3 passes: 289 passed, 19 skipped, with the same known Starlette/httpx deprecation warning. Shared Neon remained untouched.

## Increment 4 verification

The native SPIA reference model now supports all four remaining core benefit forms:

- Life certain uses the elected certain period as an unreduced-payment floor and then reverts to the applicable life or survivor factor.
- Certain only pays through the elected period and stops even when a covered life survives beyond it.
- Cash refund pays the positive remainder of premium over scheduled installments on the income-terminating death. Arrears excludes the death-month installment; advance includes it.
- Installment refund guarantees scheduled installments until premium is recovered, trims only the final guaranteed installment when necessary, and then resumes ordinary life-contingent installments.

The guarantee is implemented as `max(certain_floor, payment_factor_life)`, not as an additional payment stream. This prevents double counting while a covered life survives during the guarantee period. Gross liability cash flow is annuity payment plus expected refund claim; its discounting and reserve remain clearly identified as MentorAmp-only valuation outputs.

Pinned LifeLib cases cover life-certain boundaries with one or both lives dead, cash-refund death-month ordering for advance and arrears payments, the installment-refund recovery boundary, and certain-only termination. Native edge tests additionally cover a non-integral final refund installment, post-guarantee payment resumption, invalid forms, non-positive refund premiums, and the upstream joint cash-refund survivor-percentage restriction.

Verification after Increment 4:

- Targeted SPIA metadata and calculation tests: 32 passed.
- Product API and frozen run-package compatibility tests: 26 passed.
- Complete isolated backend suite: 301 passed, 19 PostgreSQL-only tests skipped, and one known Starlette/httpx deprecation warning.
- Shared Neon database: untouched; the full suite explicitly used `DATABASE_URL=sqlite://`.

## Increment 5 verification

The reference model now publishes the remaining base-run operating cash-flow components and both sign conventions:

- `obligation_factor` is the closing probability that a payment obligation remains. It is `max(certain_floor, last_survivor_probability)` for life-contingent forms and only `certain_floor` for `certain_only`.
- `maintenance_expense` is the annual maintenance assumption divided by 12, compounded by the fixed annual expense-inflation assumption, and weighted by the obligation factor.
- `liability_cash_flow` is outgo-positive and equals annuity payment plus refund claim plus maintenance expense.
- `net_cash_flow` is income-positive from the insurer perspective and equals the negative of liability cash flow because projected SPIA premium income is zero.
- `cash_flow_identity_residual` explicitly proves that the component statement closes to zero each month.

The $60 annual maintenance expense and 2.5% annual inflation defaults are labelled upstream standardizations, not insurer-published assumptions. Both are frozen in the model-version configuration through the declarative variable definition and may be overridden by a governed scenario. Maintenance expenses and refund claims are included in the MentorAmp-only present-value and prospective-reserve layer using month-end timing.

Pinned unrounded LifeLib fixtures cover the first month, the first inflation anniversary, survivor reduction, a life-certain obligation ending after both deaths, and the `certain_only` boundary where survival remains positive but the contractual obligation ends. Commutation remains intentionally zero/out of scope until Increment 7.

Verification after Increment 5:

- Focused SPIA, metadata, product API, and frozen run-package tests: 63 passed.
- Complete isolated backend suite: 306 passed, 19 PostgreSQL-only tests skipped, and one known Starlette/httpx deprecation warning.
- Disposable Docker PostgreSQL suite: 18 passed. The uniquely named test database was deleted after verification.
- Shared Neon database: untouched; the complete suite explicitly used `DATABASE_URL=sqlite://`.

## Increment 6 verification

The SPIA reference model now has an executable governed workflow rather than only formula-level reconciliation:

1. A synthetic reference policy is uploaded, previewed, mapped, validated, committed as an immutable version, and independently approved through Data Manager.
2. The standardized mortality and improvement tables follow the same lifecycle and are pinned by exact dataset ID and fingerprint.
3. A governed scenario is validated against the reference model version and independently approved.
4. A validated Projection Set freezes the model definition, external-reference metadata, dataset identities, scenario, formula implementations, build identity, outputs, and trace scope into the run package.
5. Execution writes the accepted output and trace rows to checksummed canonical Parquet artifacts.
6. The first 24 months of survival and annuity payments reconcile to the pinned LifeLib fixture at `1e-12` relative and absolute tolerance.
7. A file-only reconstruction reads the checksummed canonical Data Manager Parquet files, rebuilds engine inputs from the frozen package, recalculates, and matches every rebuilt output row to the accepted run Parquet.

The same workflow passes on SQLite and on a uniquely named disposable Docker PostgreSQL database. PostgreSQL exposed an overlong formula-group lineage label that SQLite did not enforce; the model metadata now uses the valid `external_reference` label without changing schema or migrations.

Verification after Increment 6:

- Data Manager, existing rebuild, frozen-package, and demo compatibility tests: 54 passed.
- Complete SQLite-backed suite: 307 passed, 20 opt-in/skipped, and one known Starlette/httpx deprecation warning.
- Complete SPIA reference workflow on disposable Docker PostgreSQL: passed.
- Disposable PostgreSQL database: verified by exact name and deleted after the run.
- Shared Neon database: untouched.

## Increment 7 verification

The reference model now supports an optional commutation withdrawal from the
remaining certain-payment slice. It is disabled by default and requires an explicit
policy opt-in. Eligibility is limited to supported certain-bearing benefit forms,
contract anniversaries from policy year 2 while a guarantee remains, and contracts
without a jurisdiction exclusion.

The calculation values future guaranteed installments using an illustrative 4%
compound rate, applies a zero-by-default governed utilization assumption, preserves
the configured minimum remaining installment, enforces the minimum gross withdrawal,
and deducts the governed contract-year surrender charge. A current-month withdrawal
reduces only future certain payments; the life-contingent tail is not reduced after
the elected guarantee expires. The charge retained by the insurer is disclosed but
is not liability outgo.

All non-contract parameters are labelled `illustrative_unverified`. Model-version
metadata freezes `enabled_by_default: false`, `valuation_use: best_estimate_only`,
and `carvm_eligible: false`. This feature must not be used for statutory CARVM
reserves or an approved product illustration.

The pinned sensitivity uses LifeLib SPIA model point 9 with an isolated development
override of 10% utilization. It verifies the exact policy-year-2 commuted value,
gross withdrawal, surrender charge, net payment, next-month payment reduction, the
policy-year-3 minimum-amount boundary, and the unreduced life tail after the certain
period. The shipped base assumption remains zero.

Contract anniversaries, COLA, payment schedules, guarantee boundaries, expense
inflation and commutation use the engine's contract-duration `policy_month`.
Discounting continues to use `projection_month`, which measures time from the
valuation date. A regression beginning at contract month 25 proves that an existing
in-force policy receives its year-3 anniversary treatment rather than restarting at
duration one.

Existing policies also carry explicit opening state:

- `cumulative_scheduled_paid_at_valuation` seeds cash- and installment-refund
  accumulated payments before the first projected month.
- `commutation_fraction_at_valuation` seeds the reduction created by commutations
  before the valuation date.

Both default to zero for new business. A missing jurisdiction-exclusion indicator
fails closed, so enabling commutation cannot silently treat an unknown jurisdiction
as eligible.

The surrender-charge schedule is uploaded, mapped, validated, committed, independently
approved, pinned by ID and fingerprint in the run package, used by execution, and
reloaded from its canonical Parquet file during the file-only rebuild. No LifeLib
package or source is present in the application runtime.

Verification after Increment 7:

- Focused SPIA reference and metadata calculation tests: 45 passed.
- Complete SQLite-backed suite: 319 passed, 20 skipped, and one known
  Starlette/httpx deprecation warning.
- Complete SPIA reference workflow on disposable Docker PostgreSQL: passed.
- Disposable PostgreSQL database: verified by exact name and deleted after the run.
- Destructive PostgreSQL setup accepts only names matching
  `mentoramp_<purpose>_test`; shared or ambiguous names are rejected before reset.
- Shared Neon database: untouched.

## References

- LifeLib uslib overview: https://lifelib.io/libraries/uslib/index.html
- LifeLib SPIA product specification: https://lifelib.io/libraries/uslib/products/immediate_annuity/product-spec.html
- LifeLib SPIA technical notes: https://lifelib.io/libraries/uslib/products/immediate_annuity/technical-notes.html
- LifeLib SPIA implementation notes: https://lifelib.io/libraries/uslib/products/immediate_annuity/model.html
- NAIC Valuation Manual: https://content.naic.org/sites/default/files/pbr_data_valuation_manual_current_edition.pdf
