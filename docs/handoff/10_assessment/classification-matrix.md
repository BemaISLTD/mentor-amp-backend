# MentorAmp Existing-Implementation Classification Matrix

**Version:** 0.1  
**Prepared:** 2026-09-24  
**Owner:** Samuel Ayinla  
**Status:** In progress

## Classification rules

| Classification | Meaning |
|---|---|
| `REUSE` | Works, meets target boundaries, and has credible evidence. |
| `IMPROVE` | Correct foundation with limited missing behavior or quality work. |
| `REFACTOR/REBUILD` | Useful logic exists, but the structure or contract conflicts with the target. |
| `REPLACE` | Existing approach should be removed for the approved design. |
| `BUILD` | Required capability does not exist. |
| `DO NOT COUNT` | Empty, mock, placeholder, demo-only, or unsupported claim. |

## Assessed components

| ID | Component/files | Requirements | Verified behavior/evidence | Target comparison | Classification | Required action | Owner/dependency |
|---|---|---|---|---|---|---|---|
| ASMT-001 | Application settings/bootstrap — `app/config.py`, `app/main.py`, requirements | LR1-GOV-002; LR1-HND-002/008; LR1-TST-010 | Default import fails because ambient `DEBUG=release` cannot populate Boolean `debug`; temporary correction reaches missing declared dependency `openpyxl`. Installed pytest is below declared version. | Settings/dependency files exist, but clean startup is not reproducible and generic environment names collide. | `IMPROVE` | Add MentorAmp-specific environment prefix, startup validation, locked supported environment, clean-install instructions/test, and dependency verification. | Samuel + Noah; deployment decisions |
| ASMT-002 | Variable contract/model — `schemas.py`, `db/models/variable.py`, migration | LR1-CORE-008/009; LR1-DATA-006 | Stores basic name/type/source/default/applicability and one version string. Unit, dimensions, effective date, owner, approval, validations, overrides, methodology and auditable version relationships are absent; model version is lost in API mapping. | Useful prototype fields exist, but target semantic/version/governance contract cannot be represented. | `REFACTOR/REBUILD` | Define variable contract first, then revise schema/model/migration with immutable version/lifecycle rules. | Samuel + Noah; formula/resolver contracts |
| ASMT-003 | Variable Registry persistence — `core/variable_registry/registry.py` | LR1-CORE-008/009; LR1-API-001 | CRUD works conceptually; mapping centralised. Create ignores required request ID, dependencies are always empty, version is not returned, source metadata is lossy, updates silently filter raw keys, delete is physical. | Does not preserve target metadata or controlled lifecycle. | `REFACTOR/REBUILD` | Introduce create/read/update lifecycle DTOs, lossless mapping, optimistic/version rules, approved retirement, validation and audit events. | Samuel + Noah |
| ASMT-004 | Variable API — `api/variables.py` | LR1-CORE-008/009; LR1-API-001; LR1-SEC-003/007 | List/create/get/update/delete routes exist. OpenAPI create requires `id`; update is untyped; no auth/security is declared; no version/approval operations exist. | Endpoint presence is useful, but contract and governance boundaries are incompatible with target. | `REFACTOR/REBUILD` | Define versioned API shapes, structured errors, authz, lifecycle operations, pagination/filtering and contract tests. | Noah + Samuel; auth decision |
| ASMT-005 | Variable Resolver — `core/variable_registry/resolver.py` | LR1-CORE-010/011/012; LR1-DATA-001/003/006 | Supports several source branches and returns source/keys/error metadata. Targeted check proved `mortality_rate=0.0` incorrectly resolves to `lapse_rate=0.2`. Selected sets/versions and declared lookup keys are ignored; mismatches can return first row. | The boundary idea is valuable, but lookup correctness and reproducibility are unsafe. | `REFACTOR/REBUILD` | Implement typed source adapters, exact version-aware keys, explicit missing/duplicate behavior, validations, override rules, structured errors and source-record trace. | Samuel + Actuarial + QA; resolver contract/reference cases |
| ASMT-006 | Resolver trace integration — `trace_engine/tracer.py`, `TraceLog`, projection runner | LR1-CORE-023/024; LR1-DATA-006; LR1-SEC-007 | Trace model/hook exist and one non-HTTP entry point enables them. Main projection explicitly passes `trace_logger=None`; logger omits formula inputs/versions and swallows persistence failures. | Trace is not reliably captured during the actual calculation path. | `REFACTOR/REBUILD` | Define trace event contract and buffered persistence; instrument projection/formula evaluation; make required-trace failure visible; expose authorized query API. | Samuel + Noah; trace/retention decisions |
| ASMT-007 | Variable/resolver tests and broader test skeleton | LR1-TST-001/002/006/007 | `test_variable_registry.py`, `test_resolver.py`, `conftest.py`, and all 15 discovered test files are zero bytes. Pytest reports no tests. | Names provide no verification or regression evidence. | `DO NOT COUNT` | After contracts are fixed, add targeted registry/resolver/API/trace tests with positive, zero, missing, duplicate, version, override, failure and reproducibility cases. | Samuel + Noah + QA + Actuarial inputs |

## Current classification summary

| Classification | Count |
|---|---:|
| `REUSE` | 0 |
| `IMPROVE` | 1 |
| `REFACTOR/REBUILD` | 5 |
| `REPLACE` | 0 |
| `BUILD` | 0 |
| `DO NOT COUNT` | 1 |

These counts cover only assessed rows. Unassessed components must not be interpreted as reusable or complete.

