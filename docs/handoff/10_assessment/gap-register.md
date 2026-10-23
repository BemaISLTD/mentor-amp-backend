# MentorAmp Backend/Core Gap Register

**Version:** 0.1  
**Prepared:** 2026-09-24  
**Owner:** Samuel Ayinla  
**Status:** In progress

| Gap ID | Priority | Gap and evidence | Requirements | Impact | Required action | Owner/dependency | Status |
|---|---|---|---|---|---|---|---|
| GAP-CORE-001 | P0 | Application settings consume generic ambient `DEBUG=release`, causing startup validation failure. | LR1-HND-002/008; LR1-TST-010 | Backend cannot start reproducibly in the current shell. | Namespace settings with a MentorAmp prefix, define allowed values, document environment, and test clean startup. | Samuel + Noah | Open |
| GAP-CORE-002 | P0 | Active environment lacks declared `openpyxl`; installed pytest `7.4.2` is below declared `>=8.3`. | LR1-HND-002; LR1-TST-010 | App import stops and test environment is not reproducible. | Create/verify supported locked environment and clean-install workflow. | Samuel + Noah + QA | Open |
| GAP-VAR-001 | P0 | Variable contract cannot represent unit, dimensions, effective date, owner, approval/lifecycle, validation rules, override permissions, methodology, or immutable versions. | LR1-CORE-008/009 | UI/API/engine cannot share a complete controlled semantic variable definition. | Complete and approve Variable Registry contract before schema implementation. | Samuel + Noah + Actuarial | Open |
| GAP-VAR-002 | P0 | POST requires a client ID that persistence ignores; PUT uses untyped DB-oriented keys; DELETE is physical; no lifecycle/authz/audit. | LR1-API-001; LR1-CORE-008/009; LR1-SEC-003/007 | Clients cannot rely on stable contracts or controlled changes. | Define create/update/lifecycle schemas, structured errors, permissions and audit events. | Noah + Samuel | Open |
| GAP-VAR-003 | P0 | Resolver ignores selected inforce/assumption/factor/scenario set versions and uses latest/name-only queries. | LR1-CORE-010/012; LR1-DATA-001/006 | A rerun can silently use different data and cannot be reproduced. | Resolve exclusively through immutable run-manifest version IDs/fingerprints. | Samuel + Noah; run-manifest contract | Open |
| GAP-VAR-004 | P0 | Resolver ignores declared variable/table lookup keys, matches only a limited fixed set, and falls back to first row. | LR1-CORE-009/012 | Lookup can return an unrelated actuarial value without an error. | Define exact key construction, duplicate/missing behavior, key normalization, and deterministic tests. | Samuel + Actuarial + QA | Open |
| GAP-VAR-005 | P0 | Hardcoded assumption extraction treated valid `mortality_rate=0.0` as missing and returned `lapse_rate=0.2`. | LR1-CORE-012; LR1-TST-002/003 | Direct numerical correctness failure. | Use explicit field/value metadata and `is None`/key-presence checks; add zero-value golden/unit cases. | Samuel + Actuarial + QA | Open |
| GAP-VAR-006 | P0 | Source semantics are incomplete: factor returns a row, scenario operation is ignored, prior-output source variable is ignored, and allowed `lookup`/`output` kinds are not implemented. | LR1-CORE-011/012 | Supported-source claims do not match runtime behavior. | Define typed adapter interface and implement/test every approved source type; reject unsupported types explicitly. | Samuel | Open |
| GAP-VAR-007 | P1 | Resolver performs no type, unit, dimension, range, applicability, override-permission, or fingerprint validation. | LR1-CORE-009/012 | Invalid values may enter calculations without controlled failure. | Add registry-driven validation and structured error codes before formula execution. | Samuel + Actuarial + QA | Open |
| GAP-TRACE-001 | P0 | Main projection passes `trace_logger=None`; formula/input/version/intermediate fields are not captured; trace persistence errors are swallowed. | LR1-CORE-023/024; LR1-DATA-006; LR1-SEC-007 | Results cannot meet source-to-result audit and reproducibility requirements. | Define trace contract, instrument execution, persist efficiently, expose failure, and add authorized query API. | Samuel + Noah | Open |
| GAP-TST-001 | P0 | All 15 discovered test files and `conftest.py` are empty; pytest runs zero tests. | LR1-TST-001/002/003/006/007 | No existing backend capability has automated acceptance evidence. | Establish test/evidence conventions, then add targeted tests after contracts are approved. | QA + Samuel + Noah + Actuarial inputs | Open |

## Sequencing

1. Stabilize environment/startup evidence (`GAP-CORE-001` and `GAP-CORE-002`).
2. Approve variable/resolver/run-manifest/trace contracts (`GAP-VAR-001`, `GAP-VAR-003`, `GAP-TRACE-001`).
3. Correct resolver/API behavior (`GAP-VAR-002`, `GAP-VAR-004` through `GAP-VAR-007`).
4. Add targeted verification and independent actuarial cases (`GAP-TST-001`).

