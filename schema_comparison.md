# MENTORAMP MYGA SCHEMA COMPARISON & HARMONIZATION

## 1. Canonical Schema Version: `mentoramp.myga.inforce/v1`

| Canonical Field Name | Type | Required | Description | Historical Aliases Accepted |
|:---|:---:|:---:|:---|:---|
| `policy_id` | string | Yes | Unique policy contract identifier | - |
| `product_type` | string | Yes | Product family identifier (must equal `MYGA`) | - |
| `issue_date` | string (YYYY-MM-DD) | Yes | Contract effective issue date | - |
| `issue_age` | integer | Yes | Attained age at policy issue (18-85) | - |
| `gender` | string | Yes | Regulatory mortality gender (`M` / `F`) | - |
| `premium` | float | Yes | Single premium paid at issue (USD) | - |
| `guarantee_period_years` | integer | Yes | Initial rate guarantee period in years | `guarantee_term_years` |
| `declared_rate` | float | Yes | Contractual annual credited rate | `credited_rate` |
| `gmir` | float | Yes | Guaranteed Minimum Interest Rate (statutory) | - |
| `gmsv_rate` | float | Yes | Standard Nonforfeiture Law rate (Model #805) | `mgsv_rate` |
| `mva_ref_yield_at_issue` | float | Yes | Benchmark treasury/corporate yield at issue | `locked_mva_reference_yield` |
| `account_value` | float | Yes | Initial account balance (equals premium at issue) | `account_value_open` |
| `mgsv_value` | float | Yes | Initial contractual statutory floor (87.5% premium) | `initial_mgsv` |
| `free_wd_base` | float | Yes | Contractual baseline for annual free withdrawal | - |

## 2. Alias Disagreement & Normalization Governance
1. **Zero Drift Fingerprinting:** Uploading canonical columns or permitted alias columns results in the exact same dataset fingerprint because Data Manager normalizes keys to canonical fields before hashing (`DM-MYGA-02`).
2. **Value Conflict Detection:** If an upload contains both canonical name (`guarantee_period_years`) and alias (`guarantee_term_years`) with conflicting values, Data Manager aborts with `ALIAS_CONFLICT` (`DM-MYGA-04`).
3. **Surrender Schedules:** Harmonized between initial multi-year schedule (`myga_surrender_charge.csv`) and contractual waiver table (`myga_sc_age_cap.csv`).
