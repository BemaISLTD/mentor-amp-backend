# MYGA Backend-to-Frontend Integration Contract

This document records the MYGA-specific additions to the versioned `/v1` API contract.
All MYGA values below are illustrative; they are not approved statutory figures.

## F.9 Five-Year MYGA Illustrative Results

MYGA uses the existing governed flow, not product-specific run routes. Discover it through
`GET /v1/system/status`, upload and approve datasets with the Data Manager APIs, submit
through `POST /v1/run-sets`, and read the canonical run, summary, results, aggregate,
comparison, and trace APIs. Runs and result responses retain `illustrative: true`; the UI
must display that label.

### Required in-force fields

| Field | Type | Unit / format |
| --- | --- | --- |
| `policy_id` | string | Stable policy identifier |
| `product_type` | string | `MYGA` |
| `issue_date` | date | ISO `YYYY-MM-DD` |
| `issue_age` | integer | Years |
| `gender` | string | `M` or `F`; mortality-table key |
| `premium` | number | USD |
| `guarantee_period_years` | integer | `5` for this product |
| `declared_rate` | number | Decimal annual rate |
| `gmir` | number | Decimal annual rate |
| `gmsv_rate` | number | Decimal annual floor-crediting rate; at least `0.0015` |
| `mva_ref_yield_at_issue` | number | Decimal annual yield |
| `account_value` | number | USD |
| `mgsv_value` | number | USD |
| `free_wd_base` | number | USD |

MYGA does not require SPIA's `monthly_payment`. An unknown `product_type` fails validation.
Negative MYGA monetary inputs and floor rates below 0.15% fail validation. The imported
`gmsv_rate` is a governed contract input; the backend does not derive it from a five-year
CMT series. An actuarial owner must verify that it also satisfies the applicable issue-date
statutory rate. This illustrative model makes no compliance claim.

### Published outputs

All outputs have dimension `Policy x scenario x projection month` and unit `USD`.

| Variable | Meaning | Aggregation |
| --- | --- | --- |
| `account_value_close` | End-of-month account value | `end_of_period` |
| `mgsv_close` | End-of-month minimum guaranteed surrender value | `end_of_period` |
| `surrender_benefit` | Benefit after MVA, charge, and floor | `end_of_period` |
| `death_benefit` | Per-policy death benefit | `end_of_period` |
| `death_claims` | Monthly weighted death claims | `sum` |
| `surrender_claims` | Monthly weighted surrender claims | `sum` |
| `annuitization_transfer` | Monthly transfer on annuitization | `sum` |
| `maturity_benefit` | Monthly maturity payout | `sum` |
| `maintenance_expense` | Monthly maintenance expense | `sum` |
| `liability_outgo` | Total policyholder and expense outgo | `sum` |
| `net_cash_flow` | Premium income less liability outgo | `sum` |
| `reserve` | Illustrative prospective reserve | `end_of_period` |

The frontend must use returned output metadata rather than infer aggregation from names.
Account credits, surrender charges, MVA, and MGSV changes are internal contract mechanics,
not separate cash-flow rows. Premium and acquisition commission are recognized only for
new business issued in the projected first month.

### Scenarios, traces, and artifacts

- `market_rate`, `mva_ref_yield`, `discount_rate_annual`, and `withdrawal` accept governed
  scenario overrides. Standard valuation scenarios leave `withdrawal` at zero. A withdrawal
  outside `[0, opening account value]` fails the affected policy; it is not silently capped
  or paid in excess of account value.
- `GET /v1/runs/{run_id}/trace` returns a nested `root.children` tree, not a flat
  `dependencies` array. Surrender-benefit lineage connects account value, free amount,
  excess, charge, capped MVA, surrender value, MGSV floor, and final benefit.
- Current analytical artifacts use the `m2-v1` Parquet schema: raw bytes are reproducible
  across identical runs, while the immutable artifact record and run-scoped path hold run
  and attempt identity. Verified API reads restore those identity fields. Older artifacts
  remain readable under their original schemas.

### Frozen synthetic integration values

For the 25-policy, 480-month synthetic portfolio dated 2026-12-31:

| Scenario / point | Value (USD) |
| --- | ---: |
| Base reserve | 4,163,674.901809879 |
| Rates Up 150bp reserve | 4,201,201.546339422 |
| Base total liability outgo | 5,298,179.058449140 |
| Rates Up 150bp total liability outgo | 5,358,149.664428924 |
| Base `MYGA-0001`, month 30 surrender benefit | 51,979.599258014285 |

These are regression anchors for synthetic tests, not pricing or valuation guidance.
