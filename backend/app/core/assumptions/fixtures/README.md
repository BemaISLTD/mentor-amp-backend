# Assumption-engine fixtures

Every file in this directory is synthetic, illustrative, and non-production. The
values were created solely to validate table shape, interpolation, mortality
improvement, behavioral schedules, curve semantics, governance, and checksum
handling. They are not SOA, NAIC, US Treasury, regulatory, or insurer data.

## Included development fixtures

| File | Purpose | Dimensions | Source and usage status |
| --- | --- | --- | --- |
| `synthetic_mortality_sample.csv` | Small configurable-terminal-age mortality grid | attained age 40–46; sex M/F | MentorAmp synthetic development data; internal testing only |
| `synthetic_scale_g2_sample.csv` | 1D age/sex improvement-vector structure | attained age 40–46; sex M/F | Synthetic structure inspired by the Scale G2 methodology; not Scale G2 published values |
| `synthetic_mp_sample_matrix.csv` | 2D age/calendar-year improvement structure | attained age 40–43; sex M/F; years 2026–2028 | Synthetic structure inspired by MP-series methodology; not RPEC published values |
| `standard_surrender_tiers.csv` | Illustrative 7-year and 10-year charge schedules | duration 1–15; tier | MentorAmp synthetic development data; requires actuarial calibration before production |
| `illustrative_us_treasury_spot_curve.json` | Spot-curve type and interpolation testing | 1M–30Y tenors | Synthetic spot rates; not recorded or derived US Treasury data |

`manifest.json` records SHA-256 checksums of the raw fixture bytes. The test suite
verifies every checksum before using a fixture.

## Reproducible synthetic equations

- Mortality starts at `qx(40,M)=0.00120` and `qx(40,F)=0.00090`. Each subsequent
  value through age 45 is `round(previous_qx * 1.10, 5)`. Age 46 is the fixture's
  configurable terminal age and is therefore `1.0` for both sexes.
- The G2-shaped sample uses `0.0120 - 0.0002*(age-40)` for M and adds `0.0010`
  for F.
- The MP-shaped sample uses
  `0.0120 - 0.0002*(age-40) - 0.0001*(calendar_year-2026)` for M and adds
  `0.0010` for F.
- Each surrender tier uses
  `max(0, (tier_years - duration + 1) * 0.01)` for its 7-year or 10-year term.

These equations are development controls, not actuarial calibration claims.

## Unresolved dependency: ACT-DEP-TABLE-01

Official 2012 IAM, Scale G2, MP-2021, and 2015 VBT data are intentionally not
redistributed here. Production use requires the Actuarial Product Owner to acquire
authorized source files, document their exact variant and licensing terms, verify
primary-source control values, and approve their checksums. Published actuarial
research is not assumed to grant redistribution rights.

Similarly, a production Treasury curve must identify whether its published inputs are
par yields or zero-coupon spot rates. Par yields require a separately documented and
validated bootstrapping methodology before discount factors may be calculated.
