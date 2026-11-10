# Central Assumption and Factor Table Engine - Review Evidence

## Delivery identity

- Branch: `feat/assumption-factor-engine`
- Approved baseline: `98039a28e281df617b90da461f58bdf2c8140263`
- Implementation boundary: `backend/app/core/assumptions/`
- Acceptance tests: `backend/tests/unit/test_assumption_table_engine.py`
- Dependency addition: NumPy is explicitly declared in `backend/requirements.txt`.

The implementation does not depend on the pending FIA/RILA or M5 branches. It does not
modify product engines, execution runners, orchestration, valuation-pipeline services,
database models, or Alembic migrations.

## Lead clarifications applied

1. Mortality improvement exposes both `FIXED_ATTAINED_AGE` and `GENERATIONAL` modes.
   Generational projections advance attained age with each calendar-year interval.
2. `MortalityTable` requires a configurable terminal age represented by the selected
   mortality table's integer age grid. It does not assume age 120 universally.
3. `YieldCurveType.PAR` and `YieldCurveType.SPOT_ZERO` are distinct. Direct discounting
   from par yields fails closed. Par-to-spot conversion requires an explicit converter,
   named methodology, and `validated=True` before the resulting spot curve can discount.
4. Scalar/vector equivalence is asserted with absolute numerical tolerance `1e-12`, not
   bitwise equality. Test counts below come from the actual approved baseline.

## Test execution log

All commands ran in the dedicated image `mentoramp-assumption-engine-test` built from
this branch. No shared or remote database was used.

| Gate | Result |
| --- | --- |
| New assumption-engine acceptance suite | `35 passed in 0.51s` |
| All unit tests on approved baseline plus new tests | `327 passed, 1 skipped in 5.79s` |
| Complete repository suite | `546 passed, 26 skipped in 167.98s` |
| PostgreSQL-specific suite | `23 passed in 678.48s` against disposable local database `mentoramp_assumption_engine_test` |

The final regression pass also verifies fail-closed handling for malformed governance
dates, unknown semantic categories, nonscalar scalar-lookups, fractional mortality and
grading coordinates, and unsupported yield-curve type or compounding values.

The disposable PostgreSQL database was dropped immediately after the successful run.
The restored `mentor_amp` database was not migrated, seeded, or modified.

Observed warnings are pre-existing and outside this assignment:

- Starlette `httpx` TestClient deprecation.
- SQLAlchemy `Result.tuples()` deprecation in migration
  `d1a4c7e9b205_expand_builtin_rbac_roles.py`.

## Repeatable performance evidence

The acceptance benchmark performs 100,000 vectorized bilinear lookups. It records facts
without defining a performance pass threshold or substituting timing for correctness.

```json
{
  "cpu": "Intel(R) Core(TM) i7-7500U CPU @ 2.70GHz",
  "elapsed_seconds": 0.018487199999981385,
  "logical_cpus": 2,
  "memory_total": "2033096 kB",
  "os": "Linux-5.10.76-linuxkit-x86_64-with-glibc2.41",
  "peak_tracemalloc_bytes": 9707374,
  "points": 100000
}
```

## Fixture provenance and licensing control

All bundled values are labeled `SYNTHETIC_NON_PRODUCTION`. The fixture README documents
reproducible development equations, and `manifest.json` records SHA-256 checksums of raw
fixture bytes. A directory-local `.gitattributes` prevents Windows line-ending conversion
from changing the checked fixture bytes.

No fixture is represented as official SOA, RPEC, NAIC, Treasury, regulatory, or insurer
data. `ACT-DEP-TABLE-01` remains open for authorized source acquisition, licensing review,
primary-source control values, and actuarial approval.

## Integration-readiness assessment

- Runtime tables expose `table_id`, `version_label`, and `fingerprint_sha256`, matching
  future RunPackage binding needs without changing the current database schema.
- The registry admits only independently approved, fingerprint-valid tables and rejects
  duplicate identifiers, preserving immutable version semantics.
- CSV grid loading and typed curve loading are infrastructure-neutral and do not import
  SQLAlchemy, FastAPI, product modules, runners, or orchestration.
- Product formulas are not wired to this module in this branch. Integration should occur
  only after senior architecture review through the controlled resolver/model-definition
  boundary.
- No claim of zero future merge conflicts is made. The main integration dependency is the
  explicit NumPy runtime requirement and later resolver/RunPackage adapter work.
