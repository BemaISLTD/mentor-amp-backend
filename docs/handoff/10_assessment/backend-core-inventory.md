# MentorAmp Backend/Core Inventory

**Version:** 0.1  
**Prepared:** 2026-09-24  
**Owner:** Samuel Ayinla  
**Status:** In progress — first vertical slice assessed  
**Authority:** CAS, MP, W0R, ITD, master requirement tracker, and target architecture

## 1. Purpose

This inventory records what the existing backend actually contains and what has been executed or inspected. It does not grant completion credit merely because a file, class, route, or schema exists.

Assessment sequence:

`VERIFY → UNDERSTAND → COMPARE → TEST → CLASSIFY → ACT → RE-VERIFY → RECORD`

The first vertical slice is the Variable Registry and Variable Resolver because variables connect imported data, assumptions, scenarios, formulas, projection, output, and trace.

## 2. Repository and environment baseline

| Item | Verified evidence | Assessment implication |
|---|---|---|
| Repository | Workspace root is backend repo, branch `main`, commit `51d3028`. Backend package is under `backend/`. | Baseline identity captured; assessment artifacts remain uncommitted. |
| Python | Active interpreter is Python `3.14.2`; `pyproject.toml` requires `>=3.11` without a tested upper bound. | Interpreter is nominally allowed, but no compatibility evidence exists. |
| Dependency declaration | `requirements.txt` declares FastAPI, SQLAlchemy, PostgreSQL driver, Alembic, Polars, OpenPyXL, upload support, pytest, and httpx. | A dependency list exists but is not a locked/reproducible environment. |
| Installed environment | Installed pytest is `7.4.2`, below declared `>=8.3`; `openpyxl` is not installed. | Current environment does not match declared requirements. |
| Configuration | Ambient environment has `DEBUG=release`; case-insensitive `Settings.debug: bool` reads it and raises a validation error. | Application import fails without an environment override. Settings require a MentorAmp-specific prefix and startup validation. |
| Application import | With temporary `DEBUG=false`, import proceeds until `ModuleNotFoundError: openpyxl`. | Full FastAPI/OpenAPI startup is not reproducible in the current environment. |
| Syntax compilation | `python -m compileall -q app` passed. | Python source is syntactically compilable; this does not prove runtime correctness. |
| Test execution | `python -m pytest -q` returned `no tests ran in 0.38s`. | There is no automated behavioral evidence. |
| Test inventory | 15 `test_*.py` files exist and all 15 are zero bytes; `tests/conftest.py` is also zero bytes. | Test filenames are placeholders and receive `DO NOT COUNT`. |

## 3. Current backend package areas

| Area | Existing paths | Assessment status |
|---|---|---|
| API | `app/api/projects.py`, `imports.py`, `variables.py`, `formulas.py`, plus empty/incomplete run/result/trace areas | Variable API inspected; remaining APIs pending. |
| Configuration/database | `app/config.py`, `app/db/database.py`, models and Alembic migrations | Startup baseline inspected; full DB/migration assessment pending. |
| Formula | `app/core/formula_engine/` | Dependency contact points inspected; full assessment pending. |
| Variable | `app/core/variable_registry/`, variable schema/model/API | First vertical-slice assessment complete. |
| Dependency | `app/core/dependency_engine/` | Contact points inspected; full assessment pending. |
| Projection | `app/core/projection_engine/` | Resolver/trace integration inspected; full assessment pending. |
| Output | `app/core/output/` | Pending. |
| Trace | `app/core/trace_engine/`, `TraceLog` model | Resolver trace path inspected; full assessment pending. |
| Imports/validation | `app/data/importers/`, `app/data/validation/` | Pending. |
| Products | SPIA Lite, FIA Lite, RILA Lite and shared modules | Pending; names and files do not imply executable accepted products. |
| Tests | 15 named unit/integration/golden files | Verified empty; `DO NOT COUNT`. |
| Orchestration/scale | No verified Dagster, PySpark, or GPU implementation in the initial file inventory | Full confirmation pending. |

## 4. Variable vertical-slice inventory

### 4.1 API layer

**Path:** `backend/app/api/variables.py`

Present behavior:

- list variables with optional product/kind filtering;
- create after name-only duplicate check;
- retrieve by name;
- update using an untyped `dict`;
- hard-delete by name;
- return `VariableDefinition` responses.

Findings:

- POST uses `VariableDefinition` as both create request and response.
- OpenAPI therefore requires client-supplied `id`, but persistence ignores that ID and generates another UUID.
- PUT accepts an unvalidated dictionary rather than a controlled update contract.
- Update field names are database-oriented (`display_name`, `source_type`) while the API schema uses `label`, `kind`, and `source`.
- There is no API authentication/authorization boundary on these operations.
- Hard delete conflicts with controlled version/lifecycle expectations and can be blocked by formula foreign keys.
- No submit/approve/activate/retire/version lifecycle exists.

### 4.2 Pydantic contract

**Path:** `backend/app/models/schemas.py`

Present metadata:

- ID, name, label, kind, data type, source dictionary, dependencies;
- required/default flags;
- product/basis applicability;
- description and a limited validation-status enum.

Missing or conflicting target metadata:

- unit and dimensions;
- version and effective date;
- owner and approval/lifecycle state;
- validation rules and range/domain constraints;
- override permission and priority rules;
- methodology applicability;
- typed source contract is declared as source classes but `VariableDefinition.source` is only `dict`, so source-specific validation is bypassed;
- `output` and `lookup` kinds are allowed but have no corresponding resolver branches.

### 4.3 Database model

**Path:** `backend/app/db/models/variable.py`

Present persistence:

- UUID identity and unique name;
- display name, description, type, source type/table, lookup keys;
- default and required flags;
- product/basis applicability;
- one `version` string;
- created/updated timestamps.

Gaps:

- unique name prevents multiple controlled versions of the same semantic variable;
- no effective date, status, owner, approval, validation, unit, dimensions, override permissions, methodology, or audit/change reason;
- source-table fields do not hold complete selected dataset/set/version identity;
- no immutable version relationship or replacement/supersession model.

### 4.4 Registry persistence mapping

**Path:** `backend/app/core/variable_registry/registry.py`

Positive foundation:

- registry operations are separated from FastAPI routes;
- model-to-contract conversion is centralized;
- database query operations are direct and understandable.

Gaps and loss of information:

- model `version` is neither returned in `VariableDefinition` nor accepted/updated through the contract;
- source-specific metadata is only partly reconstructed;
- dependencies are always returned as an empty list;
- typed source objects are not used;
- arbitrary update dictionaries are silently filtered, making client mistakes difficult to detect;
- create ignores client `id` even though the request requires it;
- delete is physical rather than lifecycle-controlled.

### 4.5 Resolver context

**Path:** `backend/app/models/schemas.py::ProjectionContext`

Present context includes project, run, product, basis, methodology, policy, scenario, month, duration, attained age, valuation date, assumption set, factor set, scenario set, and projection key.

This is a useful foundation. However, the resolver does not consistently use the version/set selectors. It also lacks explicit model/formula/data version identity, cohort/segment/coverage/rider/account dimensions, and execution identity where applicable.

### 4.6 Resolver behavior

**Path:** `backend/app/core/variable_registry/resolver.py`

Implemented branches:

- inforce input;
- assumption;
- factor;
- scenario;
- prior output;
- manual;
- formula output;
- fallback/default.

Positive foundation:

- returns a structured `VariableResolutionResult`;
- records source type/table, lookup keys, default use, and errors;
- accepts an optional trace logger;
- does not place resolution logic in the UI.

Correctness and reproducibility gaps:

1. `build_lookup_keys()` ignores the variable definition's declared lookup keys and constructs one fixed key dictionary.
2. Assumption matching uses only age/duration and accepts rows with missing keys as matches.
3. When no assumption/factor row matches, it silently returns the first row.
4. Assumption/factor queries select by table name and ignore `assumption_set`/`factor_set` and version/effective date.
5. Inforce lookup chooses the latest created record for a policy and ignores the run's selected dataset IDs/version.
6. Scenario lookup selects by scenario name, ignores scenario-set/version identity, and returns `value` without applying the declared operation (`set`, `add`, `multiply`, and so on).
7. Prior-output resolution ignores `PriorOutputSource.variable_name` and always searches using the variable currently being resolved.
8. Factor resolution returns an entire row rather than a declared value/column.
9. Assumption value extraction hardcodes `mortality_rate` then `lapse_rate`, instead of using controlled metadata.
10. It uses Python truthiness to choose between those fields. A valid `mortality_rate=0.0` is treated as missing and can be replaced by a different field.
11. No type, unit, dimension, range, applicability, override-permission, or source-fingerprint validation occurs.
12. Allowed `lookup` and `output` kinds fall through to default behavior rather than producing an explicit unsupported-source error.
13. Broad exception handling converts implementation errors into strings without structured error identity.

### 4.7 Targeted correctness check

A controlled mock returned this assumption row:

```text
{"mortality_rate": 0.0, "lapse_rate": 0.2}
```

Resolving `mortality_rate` returned `0.2` with no error.

Expected behavior is `0.0`. Zero is a valid value and must be distinguished from `None` or a missing key.

### 4.8 Projection and trace integration

Paths:

- `backend/app/core/projection_engine/runner.py`
- `backend/app/core/trace_engine/tracer.py`
- `backend/app/db/models/trace_log.py`
- `backend/app/core/entry_points.py`

Present foundation:

- non-HTTP `resolve_variable()` supplies a trace logger;
- trace model can store run, policy, scenario, month, variable, source, lookup keys, output, and error;
- projection calls the resolver for formula dependencies.

Blocking gaps:

- the main projection runner explicitly calls `resolve(..., trace_logger=None)`, so normal projection resolution does not create the trace records required by the architecture;
- formula ID and input values exist in the database model but `log_resolution()` does not populate them;
- model/formula/assumption/scenario/dataset versions, intermediate formula values, overrides, source-record identity, and fingerprints are absent;
- trace persistence commits once per resolution, which will not scale to policy/scenario/time workloads;
- every trace exception is swallowed after rollback, so a run can appear successful even when required audit evidence was not stored;
- `app/api/trace.py` is not mounted by `app/main.py` and was previously verified empty, so trace is not queryable through the application API.

## 5. Current conclusion

The Variable Registry and Resolver are a meaningful prototype foundation. They demonstrate the intended separation of API, persistence, resolution, projection, and trace concerns. They cannot be reused unchanged because the target requires controlled metadata, deterministic version-aware lookup, strict validation, trace during execution, lifecycle governance, and executable tests.

Overall vertical-slice classification: **`REFACTOR/REBUILD`**.

The existing design concepts can inform the target contracts. Production acceptance remains unavailable until the contracts are locked, the implementation is corrected, migrations are updated, and focused tests prove deterministic behavior.

## 6. Evidence limitations

- No live PostgreSQL-backed variable CRUD workflow was executed.
- Application startup is currently blocked by environment/dependency mismatches.
- No existing automated test can be run because every test file is empty.
- Actuarial lookup rules, reference data, independently prepared expected values, and tolerances remain blocked on the Actuarial Product Owner.

## 7. Next assessment slice

Continue with configuration/database and Formula Registry/Formula Engine because they define how variable versions, formula dependencies, and execution identity should be persisted and validated.

