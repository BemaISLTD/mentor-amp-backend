# Actuarial Reference Model Plan

## Working branch

`feat/actuarial-reference-models`

## Objective

Use LifeLib and NAIC materials as external references to strengthen MentorAmp's model library, synthetic data, and testing without including LifeLib in the deployed application or committing its source code.

LifeLib will be an independent development benchmark. MentorAmp will execute its own formulas, use approved and versioned datasets, and store results as canonical Parquet artifacts.

## 1. Confirm the baseline

- Run the complete test suite.
- Confirm the Alembic migration history has one head.
- Run the existing SPIA example end to end.
- Confirm that results use canonical Parquet artifacts.
- Record failures before making calculation changes.

## 2. Assess the existing SPIA model

Compare MentorAmp's SPIA implementation with the LifeLib SPIA specification:

- Input fields and assumption structures.
- Mortality assumptions.
- Single-life and joint-life handling.
- Payment timing and frequency.
- Period-certain and refund benefits.
- Survivor percentages and COLA increases.
- Expenses and commissions.
- Monthly processing order.
- Output columns and sign conventions.

Document the gaps before changing calculations.

## 3. Add reference-model metadata

Add MentorAmp-owned metadata for:

- Model name, version, product family, and projection frequency.
- Supported features and known limitations.
- External reference URL and pinned LifeLib release or commit.
- Relevant NAIC references and reference date.
- An explicit `illustrative - not regulatory compliant` classification.

## 4. Expand synthetic SPIA data

Create deterministic synthetic cases for:

- Single-life and joint-life annuities.
- Life-only and period-certain benefits.
- Cash-refund and installment-refund benefits.
- Survivor reductions.
- Level and COLA payments.
- Different payment frequencies.
- Boundary and invalid inputs.

No real policyholder or insurer data will be used.

## 5. Complete native SPIA calculations

Implement missing mechanics in MentorAmp's formula and variable framework. Calculations must:

- Use approved, versioned datasets.
- Follow an explicit monthly processing order.
- Be traceable through registered variables and formulas.
- Produce canonical Parquet results.
- Be reproducible from a frozen run package.

LifeLib will not execute during a MentorAmp run.

## 6. Create independent reference results

Use LifeLib outside the application to calculate expected results for selected synthetic policies.

The repository may contain small expected-result fixtures, the exact upstream version, attribution, and documentation explaining how the results were produced.

The repository will not contain LifeLib source code, copied LifeLib model files, a vendored LifeLib package, or LifeLib as a production dependency.

## 7. Add reconciliation tests

Compare MentorAmp with the independent reference results at:

- Policy and month level.
- Cash-flow component level.
- Portfolio aggregate and final-total level.
- Scenario sensitivity level.

Also test opening in-force reconciliation, policy movements, benefit totals, net cash-flow composition, and repeatability. Numerical tolerances must be explicit and documented.

## 8. Test the complete workflow

```text
Upload
-> Preview
-> Map
-> Validate
-> Commit version
-> Approve
-> Create run
-> Execute
-> Store Parquet
-> View results
-> Reconcile
```

Run unit, integration, end-to-end, migration, Windows, and PostgreSQL tests where applicable. Do not migrate, seed, or modify the shared Neon database.

## 9. Add regulatory traceability

Use NAIC materials as traceability sources, not as a claim of compliance. Record:

- Manual edition and section.
- Requirement interpretation.
- Related model or formula.
- Implementation and actuarial-review status.
- Supporting tests.

MentorAmp assumptions must be distinguished from sourced requirements. Examples and outputs remain illustrative until approved by a qualified actuarial owner.

## 10. Follow with Term Life

After proving the SPIA pattern, implement Term Life as the first new sample model. This will add reusable support for premiums, mortality, lapses, shock lapses, conversions, death claims, expenses, commissions, and monthly policy-state rollforwards.

## Proposed commits

1. `feat: add actuarial reference model metadata`
2. `feat: expand SPIA reference model coverage`
3. `test: add SPIA reference reconciliation coverage`
4. `docs: document actuarial reference model methodology`

## Immediate next step

Increment 1 is implemented on `feat/actuarial-reference-models`:

- The baseline and SPIA gap assessment are complete.
- Structured reference provenance pins LifeLib `v0.17.2` at commit `b706d8bbab530cc9ef94e06600e9c9d1468d8cb1`.
- `spia-reference-v1` is a separate native formula namespace and declarative model version; the historical `spia-illustrative-m1` behavior is unchanged.
- A 24-month, single-life, life-only, monthly-arrears fixture reconciles survival and undiscounted annuity payments at `1e-12` relative and absolute tolerance.
- The fixture was generated in the isolated `mentor_amp_lifelib_ref` development container. LifeLib is absent from application requirements and the MentorAmp API container.
- Model-version configuration is frozen into the fingerprinted run package so the external provenance and comparison boundary travel with every future run of this model.
- Increment 2 adds annual, semiannual, quarterly and monthly schedules; advance/arrears timing; fixed compound anniversary COLA; and timing-correct MentorAmp valuation cash flows.
- Increment 3 adds separately governed joint-annuitant mortality, independent-life last-survivor probability, survivor percentages, and `either`/`primary` reduction triggers.
- Increment 4 adds life-certain, certain-only, cash-refund, and installment-refund forms with explicitly tested guarantee boundaries and death-month ordering.
- Increment 5 adds obligation-weighted maintenance expenses, anniversary inflation, outgo-positive liability cash flow, income-positive net cash flow, and explicit component identities.
- Increment 6 proves upload, preview, mapping, validation, immutable commit, peer approval, package freezing, execution, canonical Parquet persistence, LifeLib reconciliation, and checksummed file-only rebuild on SQLite and disposable PostgreSQL.
- Increment 7 adds strict opt-in commutation of remaining certain payments. The
  surrender-charge schedule is a governed factor-table version; all unsupported
  assumptions are illustrative/unverified, the feature is off by default, and it
  is explicitly excluded from statutory CARVM use.
- The complete isolated backend suite passes after Increment 7: 319 passed, 20
  opt-in tests skipped, and one known Starlette/httpx deprecation warning.
- The opt-in migration/concurrency suite passes 18 tests on a disposable Docker PostgreSQL database, which was removed afterward.
- Alembic remains at one head: `d4f7a9c2e681`.

The SPIA reference-model increments are implemented. The next model-library step
requires product-owner selection and approved acceptance cases; the shared Neon
database remains untouched until an upgrade is explicitly coordinated.
