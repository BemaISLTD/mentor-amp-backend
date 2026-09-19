# MentorAmp — Phase 0 & 1: Foundation & Database Schema

**Status:** Complete  
**Backend stack:** Python 3.11 · FastAPI · SQLAlchemy (sync) · Alembic · PostgreSQL (Neon)

---

## Overview

These two phases lay the entire foundation of the MentorAmp platform. By the end of Phase 1, the backend is running, connected to PostgreSQL, and every table the projection engine will ever write to or read from already exists in the database — fully migrated, constrained, and indexed.

---

## Phase 0 — Project Scaffolding

### What was built

The app skeleton: server boots, CORS is configured, the database connection is wired up, and the first real resource (`/projects`) is live.

### Project structure

```
backend/
├── pyproject.toml          # project metadata, Python ≥3.11
├── .env / .env.example     # environment variables (see below)
└── app/
    ├── main.py             # FastAPI app, CORS, routers, health check
    ├── config.py           # Pydantic Settings — reads .env
    ├── models/
    │   ├── schemas.py      # Pydantic request/response shapes
    │   └── enums.py        # placeholder, populated in later phases
    ├── api/
    │   └── projects.py     # POST/GET /projects endpoints
    └── db/
        ├── database.py     # SQLAlchemy engine, session factory, Base
        ├── models/
        │   └── project.py  # Project ORM model
        └── migrations/
            ├── env.py
            ├── script.py.mako
            └── versions/
                └── e0c51a917e8e_create_projects_table.py
```

---

### Environment variables

Configured in `.env`, documented in `.env.example`:

| Variable       | Required | Default     | Description                                                       |
|----------------|----------|-------------|-------------------------------------------------------------------|
| `DATABASE_URL` | Yes      | —           | Full PostgreSQL connection string. Use the **direct** (non-pooler) Neon URL for Alembic migrations. |
| `APP_NAME`     | No       | `MentorAmp` | Application name shown in OpenAPI docs.                           |
| `DEBUG`        | No       | `false`     | When `true`: SQL queries are logged, `/docs` and `/redoc` are enabled. |

---

### Application entry point — `app/main.py`

The FastAPI application is created with title and version from `settings`. Two things are wired in at startup:

**CORS middleware** — allows the React frontend to call the API during local development:

| Allowed origin              | Notes                            |
|-----------------------------|----------------------------------|
| `http://localhost:5173`     | Vite default port                |
| `http://localhost:3000`     | Create React App default         |
| `http://127.0.0.1:5173`    | Alternate Vite address           |

All HTTP methods and headers are allowed. Credentials are permitted.

**Health check endpoint:**

```
GET /health
→ 200 { "status": "ok" }
```

No database call is made. This is a lightweight liveness probe.

---

### Database layer — `app/db/database.py`

Uses a **synchronous** SQLAlchemy engine. This is intentional for Phase 0–1; async migration can happen later without touching the models.

| Object          | What it is                                                              |
|-----------------|-------------------------------------------------------------------------|
| `engine`        | SQLAlchemy `create_engine`, `pool_pre_ping=True` for connection health  |
| `SessionLocal`  | `sessionmaker` bound to the engine, `autocommit=False`, `autoflush=False` |
| `Base`          | `DeclarativeBase` — all ORM models inherit from this                   |
| `get_db()`      | FastAPI dependency — yields a `Session`, closes it in `finally`        |

`pool_pre_ping=True` is important for cloud databases like Neon where idle connections may be dropped. It verifies the connection before each use.

---

### Configuration — `app/config.py`

```python
class Settings(BaseSettings):
    database_url: str
    app_name: str = "MentorAmp"
    debug: bool = False
```

Loaded via `pydantic-settings` from `.env`. The `.env` file location is resolved relative to `config.py`, so it always finds `backend/.env` regardless of where the process is started from.

`settings` is a module-level singleton — import it anywhere with `from app.config import settings`.

---

### Projects resource

#### ORM model — `app/db/models/project.py`

| Column        | Type            | Constraints                    | Notes                          |
|---------------|-----------------|--------------------------------|--------------------------------|
| `id`          | `VARCHAR(36)`   | PK                             | UUID v4, generated in Python   |
| `name`        | `VARCHAR(255)`  | NOT NULL                       |                                |
| `description` | `TEXT`          | nullable                       |                                |
| `created_at`  | `TIMESTAMPTZ`   | NOT NULL                       | UTC, set on insert             |
| `updated_at`  | `TIMESTAMPTZ`   | NOT NULL                       | UTC, updated on every save     |

#### Pydantic schemas — `app/models/schemas.py`

**`ProjectCreate`** (request body for POST):
```json
{
  "name": "My Project",           // required, 1–255 chars
  "description": "Optional text"  // optional
}
```

**`ProjectResponse`** (returned by all three endpoints):
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "My Project",
  "description": "Optional text",
  "created_at": "2026-06-25T10:00:00Z",
  "updated_at": "2026-06-25T10:00:00Z"
}
```

**`ProjectListResponse`** (returned by GET /projects):
```json
{
  "projects": [ ...ProjectResponse ],
  "total": 3
}
```

#### API endpoints — `app/api/projects.py`

| Method | Path                  | Status | Request body    | Response body          | Description              |
|--------|-----------------------|--------|-----------------|------------------------|--------------------------|
| POST   | `/projects/`          | 201    | `ProjectCreate` | `ProjectResponse`      | Create a new project     |
| GET    | `/projects/`          | 200    | —               | `ProjectListResponse`  | List all projects, newest first |
| GET    | `/projects/{project_id}` | 200 | —               | `ProjectResponse`      | Fetch one project by ID  |
| GET    | `/projects/{project_id}` | 404 | —               | `{ "detail": "..." }`  | When ID does not exist   |

**Validation errors** (422) are returned by FastAPI automatically when:
- `name` is empty or missing
- `name` exceeds 255 characters

---

### Alembic migration — Migration 1

**Revision:** `e0c51a917e8e`  
**Creates:** `projects` table

Run migrations:
```bash
alembic upgrade head
```

Roll back:
```bash
alembic downgrade -1
```

---

---

## Phase 1 — Full Database Schema

### What was built

Every table the projection engine needs — inforce data, assumptions, factors, scenarios, the variable registry, formula registry, formula dependencies, runs, run outputs, and trace logs — all created in a single Alembic migration in the correct dependency order.

### Migration chain

```
(base)
  └── e0c51a917e8e  →  creates: projects
        └── bfc45372c779  →  creates: all remaining Phase 1 tables
```

---

### Table inventory

| Table                 | Primary key   | Purpose                                                     |
|-----------------------|---------------|-------------------------------------------------------------|
| `projects`            | UUID string   | Top-level container. All other data belongs to a project.   |
| `inforce_files`       | UUID string   | Metadata for each uploaded policy file.                     |
| `inforce_records`     | BIGSERIAL     | Individual policy rows parsed from an inforce file.         |
| `assumption_sets`     | UUID string   | Named collection of assumption tables per project.          |
| `assumption_tables`   | UUID string   | A single lookup table (mortality, lapse, expense, etc.).    |
| `factor_sets`         | UUID string   | Named collection of factor tables per project.              |
| `factor_tables`       | UUID string   | A single factor lookup table (cap, option budget, etc.).    |
| `scenario_sets`       | UUID string   | Named collection of scenarios per project.                  |
| `scenario_tables`     | UUID string   | Override instructions for a single scenario.                |
| `variable_registry`   | UUID string   | Central catalog of every named variable in the engine.      |
| `formula_registry`    | UUID string   | Catalog of every calculation formula.                       |
| `formula_dependencies`| UUID string   | Maps each formula to the variables it depends on.           |
| `runs`                | UUID string   | Tracks a single projection run execution.                   |
| `run_outputs`         | BIGSERIAL     | Every calculated value from a run.                          |
| `trace_logs`          | BIGSERIAL     | Every variable resolution event — used for debugging.       |

---

### Table details

#### `inforce_files`

Stores metadata about each uploaded file. The actual policy rows are in `inforce_records`.

| Column              | Type           | Notes                                         |
|---------------------|----------------|-----------------------------------------------|
| `id`                | VARCHAR(36)    | UUID PK                                       |
| `project_id`        | VARCHAR(36)    | FK → `projects.id` ON DELETE CASCADE          |
| `filename`          | VARCHAR(500)   | Original filename as uploaded                 |
| `file_type`         | VARCHAR(20)    | `tsv`, `csv`, or `xlsx`                       |
| `row_count`         | INTEGER        | Number of policy rows parsed                  |
| `columns_detected`  | JSON           | Column names and inferred types; nullable      |
| `uploaded_at`       | TIMESTAMPTZ    | UTC upload timestamp                          |

#### `inforce_records`

One row per policy per file. The entire policy record lives in the `data` JSON column.

| Column      | Type         | Notes                                              |
|-------------|--------------|-----------------------------------------------------|
| `id`        | BIGSERIAL    | Auto-increment PK                                   |
| `file_id`   | VARCHAR(36)  | FK → `inforce_files.id` ON DELETE CASCADE           |
| `policy_id` | VARCHAR(100) | Indexed — used in run output lookups                |
| `data`      | JSON         | Full row as key-value object                        |
| `created_at`| TIMESTAMPTZ  | UTC                                                 |

#### `assumption_sets` / `assumption_tables`

`assumption_sets` is the named container (e.g., "Mortality 2024"). `assumption_tables` holds the actual lookup rows.

`assumption_tables` — notable columns:

| Column       | Type        | Notes                                                          |
|--------------|-------------|----------------------------------------------------------------|
| `table_type` | VARCHAR(100)| `mortality`, `lapse`, `expense`, or any custom type string     |
| `lookup_keys`| JSON        | Array of strings — the key fields used to look up a value, e.g. `["age", "gender", "duration"]` |
| `data`       | JSON        | Array of row objects matching the lookup key structure         |

#### `factor_sets` / `factor_tables`

Same structure as assumption tables. `table_type` values: `option_budget`, `cap`, `participation`, `spread`.

#### `scenario_sets` / `scenario_tables`

`scenario_tables.overrides` is a JSON array where each element describes one override:

```json
{
  "target_variable": "lapse_rate",
  "operation": "multiply",
  "value": 1.25,
  "applies_from_period": 1,
  "applies_to_period": 12
}
```

Supported operations are defined by the projection engine in later phases.

---

#### `variable_registry`

The central catalog of every named piece of data the engine can produce or consume.

| Column                  | Type             | Notes                                                          |
|-------------------------|------------------|----------------------------------------------------------------|
| `id`                    | VARCHAR(36)      | UUID PK                                                        |
| `name`                  | VARCHAR(255)     | **Unique, indexed.** This is the key used by formulas and trace logs. |
| `display_name`          | VARCHAR(500)     | Human-readable label for UI display                            |
| `description`           | TEXT             | Full description of what the variable represents               |
| `data_type`             | VARCHAR(50)      | `number`, `string`, `boolean`, `date`, `vector`, `table`       |
| `source_type`           | VARCHAR(50)      | `input`, `assumption`, `factor`, `scenario`, `formula`, `prior_output`, `manual` |
| `source_table`          | VARCHAR(255)     | Which data table to look up (when source_type requires a table lookup) |
| `lookup_keys`           | JSON             | Keys used for table lookup, e.g. `["age", "duration"]`         |
| `default_value`         | JSON             | Fallback value if not found; nullable                          |
| `required`              | BOOLEAN          | Whether a missing value causes the run to fail                 |
| `product_applicability` | `ARRAY(VARCHAR)` | Products this variable applies to. Empty = all products.       |
| `basis_applicability`   | `ARRAY(VARCHAR)` | Bases this variable applies to. Empty = all bases.             |
| `version`               | VARCHAR(20)      | Schema version of this variable definition, default `v1`       |
| `created_at`            | TIMESTAMPTZ      |                                                                |
| `updated_at`            | TIMESTAMPTZ      | Updated on every save                                          |

---

#### `formula_registry`

Every formula the engine can execute.

| Column                  | Type             | Notes                                                           |
|-------------------------|------------------|-----------------------------------------------------------------|
| `id`                    | VARCHAR(36)      | UUID PK                                                         |
| `name`                  | VARCHAR(255)     | Display name for the formula                                    |
| `output_variable`       | VARCHAR(255)     | FK → `variable_registry.name` ON DELETE RESTRICT. The variable this formula produces. |
| `function_ref`          | VARCHAR(255)     | Key into the engine's `FORMULA_FUNCTIONS` registry (Python dict) |
| `category`              | VARCHAR(100)     | Grouping label, e.g. `mortality`, `lapse`, `crediting`; nullable |
| `product_applicability` | `ARRAY(VARCHAR)` | Products this formula applies to                                |
| `basis_applicability`   | `ARRAY(VARCHAR)` | Bases this formula applies to                                   |
| `version`               | VARCHAR(20)      | Default `v1`                                                    |
| `status`                | VARCHAR(20)      | `draft`, `active`, or `deprecated`                              |
| `created_by`            | VARCHAR(255)     | Optional — who registered this formula                          |

`output_variable` uses `ON DELETE RESTRICT` — you cannot delete a variable from the registry while a formula still points to it.

---

#### `formula_dependencies`

A join table. Each row says: "formula X depends on variable Y."

| Column               | Type         | Notes                                                     |
|----------------------|--------------|-----------------------------------------------------------|
| `id`                 | VARCHAR(36)  | UUID PK                                                   |
| `formula_id`         | VARCHAR(36)  | FK → `formula_registry.id` ON DELETE CASCADE              |
| `depends_on_variable`| VARCHAR(255) | FK → `variable_registry.name` ON DELETE CASCADE           |

When a formula is deleted, all its dependency rows are automatically removed. When a variable is deleted, all formulas that depended on it lose those dependency records as well.

---

#### `runs`

Tracks the lifecycle of a single projection execution.

| Column           | Type         | Notes                                                           |
|------------------|--------------|-----------------------------------------------------------------|
| `id`             | VARCHAR(36)  | UUID PK                                                         |
| `project_id`     | VARCHAR(36)  | FK → `projects.id` ON DELETE CASCADE                            |
| `projection_key` | VARCHAR(255) | Identifier for the run configuration (e.g. `"base_2024_q4"`); nullable |
| `status`         | VARCHAR(20)  | `pending` → `running` → `success` / `partial_success` / `failed` |
| `started_at`     | TIMESTAMPTZ  | Set when the engine begins processing; nullable                 |
| `completed_at`   | TIMESTAMPTZ  | Set when the run finishes; nullable                             |
| `created_at`     | TIMESTAMPTZ  | When the run record was created                                 |

---

#### `run_outputs`

Stores every calculated value from every run. This is the primary output table.

| Column             | Type         | Notes                                                      |
|--------------------|--------------|------------------------------------------------------------|
| `id`               | BIGSERIAL    | Auto-increment PK                                          |
| `run_id`           | VARCHAR(36)  | FK → `runs.id` ON DELETE CASCADE; indexed                  |
| `policy_id`        | VARCHAR(100) | The policy this value belongs to                           |
| `scenario_id`      | VARCHAR(100) | The scenario under which this value was calculated         |
| `projection_month` | INTEGER      | Month number in the projection timeline (0-based or 1-based — defined by the engine) |
| `variable_name`    | VARCHAR(255) | The variable this row is a result for                      |
| `value`            | JSON         | The calculated value. Scalar, array, or object depending on the variable's `data_type`. |
| `product`          | VARCHAR(100) | Product type; nullable                                     |
| `created_at`       | TIMESTAMPTZ  |                                                            |

**Composite index:** `idx_run_outputs_lookup` on `(run_id, policy_id, scenario_id, projection_month)` — used when the engine or UI queries all variables for a specific policy/scenario/month combination.

---

#### `trace_logs`

Records every variable resolution event during a run. Used for debugging and auditability. Can be very large — only written when tracing is enabled.

| Column             | Type         | Notes                                                                |
|--------------------|--------------|----------------------------------------------------------------------|
| `id`               | BIGSERIAL    | Auto-increment PK                                                    |
| `run_id`           | VARCHAR(36)  | FK → `runs.id` ON DELETE CASCADE; indexed                            |
| `policy_id`        | VARCHAR(100) |                                                                      |
| `scenario_id`      | VARCHAR(100) |                                                                      |
| `projection_month` | INTEGER      |                                                                      |
| `formula_id`       | VARCHAR(36)  | FK → `formula_registry.id` ON DELETE SET NULL; nullable              |
| `variable_name`    | VARCHAR(255) | Which variable was being resolved                                    |
| `input_values`     | JSON         | The inputs passed into the formula or lookup; nullable               |
| `output_value`     | JSON         | What came back; nullable                                             |
| `source_type`      | VARCHAR(50)  | Where the value came from (`assumption`, `formula`, `factor`, etc.)  |
| `source_table`     | VARCHAR(255) | Which table was queried; nullable                                    |
| `lookup_keys`      | JSON         | The actual key values used in the lookup; nullable                   |
| `error_message`    | TEXT         | Populated if resolution failed; nullable                             |
| `created_at`       | TIMESTAMPTZ  |                                                                      |

**Composite index:** `idx_trace_logs_lookup` on `(run_id, policy_id, projection_month, variable_name)`.

`formula_id` uses `ON DELETE SET NULL` so trace history is preserved even if a formula is later removed from the registry.

---

### Foreign key cascade summary

| Child table             | Parent table         | On parent delete   |
|-------------------------|----------------------|--------------------|
| `inforce_files`         | `projects`           | CASCADE            |
| `inforce_records`       | `inforce_files`      | CASCADE            |
| `assumption_sets`       | `projects`           | CASCADE            |
| `assumption_tables`     | `assumption_sets`    | CASCADE            |
| `factor_sets`           | `projects`           | CASCADE            |
| `factor_tables`         | `factor_sets`        | CASCADE            |
| `scenario_sets`         | `projects`           | CASCADE            |
| `scenario_tables`       | `scenario_sets`      | CASCADE            |
| `runs`                  | `projects`           | CASCADE            |
| `run_outputs`           | `runs`               | CASCADE            |
| `trace_logs`            | `runs`               | CASCADE            |
| `formula_registry`      | `variable_registry`  | RESTRICT (via `output_variable`) |
| `formula_dependencies`  | `formula_registry`   | CASCADE            |
| `formula_dependencies`  | `variable_registry`  | CASCADE            |
| `trace_logs`            | `formula_registry`   | SET NULL           |

Deleting a **project** cascades to all its inforce files, assumption/factor/scenario sets, and runs (and in turn to outputs and traces). Deleting a **variable** from the registry is blocked if any formula still references it as output.

---

### JSON column behavior

Several columns use `JSON` (or `ARRAY` for `product_applicability` / `basis_applicability`). Key things to know:

- JSON columns store any valid JSON value — object, array, number, string, boolean, or null. The column does not enforce internal structure; that is enforced in application code.
- `lookup_keys` on assumption/factor tables is expected to be a JSON **array of strings**.
- `data` on assumption/factor tables is expected to be a JSON **array of row objects**.
- `overrides` on `scenario_tables` is a JSON **array of override objects** (see structure above).
- `value` on `run_outputs` can be a scalar number, an array (for vector variables), or an object (for table variables) — the engine determines the shape based on the variable's `data_type`.
- `input_values`, `output_value`, `lookup_keys` on `trace_logs` are nullable — they are only written when the engine has data to record.

---

## Frontend Integration Guide

### How to start the backend locally

```bash
cd backend
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`.

OpenAPI docs (only when `DEBUG=true`):
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

---

### CORS

The backend accepts requests from `http://localhost:5173` and `http://localhost:3000` out of the box. No proxy configuration needed in your Vite or CRA setup.

---

### Base URL convention

All API paths below are relative to the backend base URL (e.g. `http://localhost:8000`).

---

### Projects API

The only resource with full CRUD endpoints in Phase 0–1. Everything else in the database is accessible by the engine but has no public API yet.

#### Create a project

```
POST /projects/
Content-Type: application/json

{
  "name": "Q4 2024 Projection",
  "description": "Base case + stress scenarios"
}
```

Response `201`:
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Q4 2024 Projection",
  "description": "Base case + stress scenarios",
  "created_at": "2026-06-25T10:00:00Z",
  "updated_at": "2026-06-25T10:00:00Z"
}
```

#### List projects

```
GET /projects/
```

Response `200`:
```json
{
  "projects": [ ...ProjectResponse ],
  "total": 3
}
```

Projects are returned newest-first (`created_at DESC`).

#### Get a single project

```
GET /projects/{project_id}
```

Response `200`: `ProjectResponse`  
Response `404`: `{ "detail": "Project with id '...' not found." }`

---

### Health check

```
GET /health
→ 200 { "status": "ok" }
```

Use this as a readiness check before making other API calls.

---

### Error shapes

FastAPI returns consistent error shapes:

**Validation error (422):**
```json
{
  "detail": [
    {
      "type": "string_too_short",
      "loc": ["body", "name"],
      "msg": "String should have at least 1 character",
      "input": ""
    }
  ]
}
```

**Not found (404):**
```json
{
  "detail": "Project with id 'abc' not found."
}
```

---

### IDs

All resource IDs (projects, runs, variables, formulas, etc.) are **UUID v4 strings** — 36 characters, formatted as `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`. Treat them as opaque strings; do not parse or derive meaning from them.

The two high-volume tables (`run_outputs`, `trace_logs`, `inforce_records`) use **auto-increment integers** as primary keys for performance. These are not exposed in the current API.

---

### What is not yet exposed via API

The following tables exist in the database from Phase 1 but do not have API endpoints yet. They will be added in subsequent phases:

- `inforce_files` / `inforce_records` — file upload endpoints (Phase 3+)
- `assumption_sets` / `assumption_tables` — (Phase 3+)
- `factor_sets` / `factor_tables` — (Phase 3+)
- `scenario_sets` / `scenario_tables` — (Phase 3+)
- `variable_registry` — (Phase 4+)
- `formula_registry` / `formula_dependencies` — (Phase 4+)
- `runs` / `run_outputs` / `trace_logs` — (Phase 5+)

The data contracts (Pydantic shapes) for all of the above are fully defined in Phase 2.


---

---

# Phase 2 — Pydantic Schemas & Enums (Full Data Contracts)

**Status:** Complete  
**Files changed:** `app/models/enums.py`, `app/models/schemas.py`

---

## Overview

Phase 2 defines the complete data contract layer — every Pydantic model and every Literal type the MentorAmp engine uses. Nothing connects to the database here. The purpose is to establish a single source of truth for the shape of every object so that the engine, database layer, API, and frontend all speak the same language.

Every subsequent phase imports from these two files. No layer invents its own shapes.

---

## Enums — `app/models/enums.py`

These are Python `Literal` types (not `Enum` subclasses). They are used directly in Pydantic field annotations, so validation is automatic — passing an unlisted value raises a `ValidationError` immediately.

### `VariableSourceType`
Where a variable's value comes from.

| Value          | Meaning                                                      |
|----------------|--------------------------------------------------------------|
| `input`        | Comes from the inforce / policy dataset                      |
| `assumption`   | Looked up from an assumption table                           |
| `factor`       | Looked up from a factor table (caps, option budgets, etc.)   |
| `scenario`     | Overridden by the active scenario                            |
| `formula`      | Computed by a formula in the registry                        |
| `prior_output` | The value this variable had in a previous projection period  |
| `manual`       | A literal value embedded in the variable definition itself   |

### `DataType`
The type of value a variable holds.

| Value     | Meaning                                          |
|-----------|--------------------------------------------------|
| `number`  | Numeric (int or float)                           |
| `string`  | Text                                             |
| `boolean` | True / False                                     |
| `date`    | ISO date string                                  |
| `vector`  | Array of numbers (e.g. monthly cash flows)       |
| `table`   | Structured object / lookup table                 |

### `VariableKind`
How the variable is classified in the registry.

| Value        | Meaning                                                   |
|--------------|-----------------------------------------------------------|
| `input`      | Sourced directly from the policy dataset                  |
| `assumption` | Sourced from an assumption table                          |
| `factor`     | Sourced from a factor table                               |
| `formula`    | Computed by a formula                                     |
| `output`     | Final output variable written to `run_outputs`            |
| `lookup`     | Intermediate derived value, not a primary output          |

### `FormulaStatus`
Lifecycle state of a formula.

| Value        | Meaning                                    |
|--------------|--------------------------------------------|
| `draft`      | Work in progress, not used in production   |
| `active`     | Live and used by the engine                |
| `deprecated` | Retired, kept for historical reference     |

### `VariableStatus`
Validation state of a variable in the registry.

| Value                  | Meaning                                            |
|------------------------|----------------------------------------------------|
| `valid`                | Variable definition is complete and consistent     |
| `invalid`              | Definition has errors                              |
| `missing_dependency`   | A variable it depends on does not exist            |
| `circular_reference`   | Dependency chain loops back to itself              |

### `RunStatus`
Lifecycle state of a projection run.

| Value             | Meaning                                                       |
|-------------------|---------------------------------------------------------------|
| `pending`         | Created, not yet started                                      |
| `running`         | Currently executing                                           |
| `success`         | Completed with no errors                                      |
| `partial_success` | Completed but some policies or variables had errors           |
| `failed`          | Fatal error, run did not complete                             |

### `ScenarioOperation`
How a scenario override modifies its target variable.

| Value            | Effect                                                         |
|------------------|----------------------------------------------------------------|
| `set`            | Replace the variable's value with the override value           |
| `add`            | Add the override value to the variable's value                 |
| `subtract`       | Subtract the override value from the variable's value          |
| `multiply`       | Multiply the variable's value by the override value            |
| `percent_change` | Apply a percentage change (e.g. `0.10` = +10%)                |

### `CalculationErrorType`
The category of error encountered during a calculation.

| Value                      | When it occurs                                              |
|----------------------------|-------------------------------------------------------------|
| `missing_variable`         | A required variable is not in the registry                  |
| `missing_value`            | A variable is in the registry but resolved to nothing       |
| `invalid_formula`          | `function_ref` does not exist in `FORMULA_FUNCTIONS`        |
| `circular_dependency`      | A dependency cycle was detected at runtime                  |
| `division_by_zero`         | A formula attempted to divide by zero                       |
| `invalid_data_type`        | A value's type does not match the expected `DataType`       |
| `lookup_failed`            | An assumption/factor table lookup returned no match         |
| `scenario_override_failed` | A scenario override could not be applied                    |

---

## Schemas — `app/models/schemas.py`

All models use `from __future__ import annotations` so forward references (e.g. `CalculationTrace` referencing itself) resolve correctly.

---

### Variable Sources (tagged union)

These seven models form a discriminated union on the `type` field. Pydantic picks the right model automatically based on the `type` value at parse time.

#### `InputSource`
```python
type: Literal["input"]  # always "input"
dataset_id: str          # which inforce file/dataset
column_name: str         # which column in that dataset
```

#### `AssumptionSource`
```python
type: Literal["assumption"]
table_id: str            # which assumption table
key: str | None          # optional specific lookup key override
```

#### `FactorSource`
```python
type: Literal["factor"]
table_id: str
lookup_keys: list[str]   # keys used for the lookup, e.g. ["strategy", "duration"]
```

#### `ScenarioSource`
```python
type: Literal["scenario"]
scenario_id: str
override_id: str
```

#### `FormulaSource`
```python
type: Literal["formula"]
formula_id: str
```

#### `PriorOutputSource`
```python
type: Literal["prior_output"]
variable_name: str
offset_periods: int = 1  # how many months back, default 1
```

#### `ManualSource`
```python
type: Literal["manual"]
value: Any               # the literal value embedded in the definition
```

#### `VariableSource` (the union)
```python
VariableSource = Union[
    InputSource, AssumptionSource, FactorSource, ScenarioSource,
    FormulaSource, PriorOutputSource, ManualSource
]
```

When parsing, pass the full object including `type`:
```json
{ "type": "assumption", "table_id": "mortality_2024", "key": null }
```

---

### Core Registry Objects

#### `VariableDefinition`
The full definition of a variable as stored/loaded by the registry.

| Field                    | Type                  | Default     | Notes                                                 |
|--------------------------|-----------------------|-------------|-------------------------------------------------------|
| `id`                     | `str`                 | required    |                                                       |
| `name`                   | `str`                 | required    | The key used everywhere else                          |
| `label`                  | `str \| None`         | `None`      | Human-readable display name                           |
| `kind`                   | `VariableKind`        | required    |                                                       |
| `data_type`              | `DataType`            | required    |                                                       |
| `source`                 | `VariableSource\|None`| `None`      | How to resolve this variable                          |
| `dependencies`           | `list[str]`           | `[]`        | Names of variables this one depends on                |
| `required`               | `bool`                | `True`      |                                                       |
| `default_value`          | `Any \| None`         | `None`      | Used if the variable resolves to nothing              |
| `product_applicability`  | `list[str]`           | `[]`        | Empty = applies to all products                       |
| `basis_applicability`    | `list[str]`           | `[]`        | Empty = applies to all bases                          |
| `description`            | `str \| None`         | `None`      |                                                       |
| `status`                 | `VariableStatus`      | `"valid"`   |                                                       |

#### `FormulaDefinition`
The full definition of a formula.

| Field                    | Type              | Default   | Notes                                                      |
|--------------------------|-------------------|-----------|------------------------------------------------------------|
| `id`                     | `str`             | required  |                                                            |
| `name`                   | `str`             | required  |                                                            |
| `output_variable`        | `str`             | required  | The variable name this formula produces                    |
| `function_ref`           | `str`             | required  | Key into the engine's `FORMULA_FUNCTIONS` Python dict      |
| `dependencies`           | `list[str]`       | `[]`      | Variable names this formula reads                          |
| `category`               | `str`             | `""`      | Grouping label (e.g. `"mortality"`, `"crediting"`)         |
| `product_applicability`  | `list[str]`       | `[]`      |                                                            |
| `basis_applicability`    | `list[str]`       | `[]`      |                                                            |
| `version`                | `str`             | `"v1"`    |                                                            |
| `status`                 | `FormulaStatus`   | `"draft"` |                                                            |
| `test_case_ids`          | `list[str]`       | `[]`      | IDs of test cases that cover this formula                  |

#### `FormulaDatabase`
A complete loadable package — a named collection of variables and formulas that constitutes one "version" of the engine's logic.

| Field       | Type                        | Notes                        |
|-------------|-----------------------------|------------------------------ |
| `id`        | `str`                       |                              |
| `name`      | `str`                       |                              |
| `variables` | `list[VariableDefinition]`  | All variable definitions     |
| `formulas`  | `list[FormulaDefinition]`   | All formula definitions      |
| `metadata`  | `dict[str, Any]`            | Freeform metadata            |

---

### Scenarios

#### `ScenarioOverride`
One instruction within a scenario.

| Field                  | Type                    | Notes                                                        |
|------------------------|-------------------------|--------------------------------------------------------------|
| `id`                   | `str`                   |                                                              |
| `target_variable`      | `str`                   | The variable name to override                                |
| `operation`            | `ScenarioOperation`     | How to apply the override value                              |
| `value`                | `float \| str \| bool`  | The override value                                           |
| `applies_from_period`  | `int \| None`           | Start month (inclusive); `None` = from start of projection   |
| `applies_to_period`    | `int \| None`           | End month (inclusive); `None` = to end of projection         |

#### `ScenarioDefinition`
A named scenario — a collection of overrides applied together.

| Field         | Type                      | Notes |
|---------------|---------------------------|-------|
| `id`          | `str`                     |       |
| `name`        | `str`                     |       |
| `description` | `str \| None`             |       |
| `overrides`   | `list[ScenarioOverride]`  |       |

---

### Projection Run

#### `ProjectionRunDefinition`
The full configuration for a run, passed to the engine before execution starts.

| Field                        | Type          | Notes                                              |
|------------------------------|---------------|----------------------------------------------------|
| `id`                         | `str`         |                                                    |
| `name`                       | `str`         |                                                    |
| `project_id`                 | `str`         |                                                    |
| `formula_database_id`        | `str`         | Which `FormulaDatabase` to use                     |
| `dataset_ids`                | `list[str]`   | Inforce datasets to project                        |
| `scenario_ids`               | `list[str]`   | Scenarios to run; order matters for multi-scenario |
| `projection_length_months`   | `int`         | How many months to project forward                 |
| `selected_output_variables`  | `list[str]`   | Which variables to write to `run_outputs`          |
| `debug_mode`                 | `bool`        | When `True`, trace logs are written                |

---

### Runtime Context

#### `ProjectionContext`
Passed to every variable resolver and formula call during a run. It carries all the information needed to resolve any variable for one specific policy at one specific point in time.

| Field             | Type           | Required | Notes                                                    |
|-------------------|----------------|----------|----------------------------------------------------------|
| `project_id`      | `str`          | Yes      |                                                          |
| `run_id`          | `str`          | Yes      |                                                          |
| `product`         | `str`          | Yes      | Product type of the current policy                       |
| `basis`           | `str \| None`  | No       | Valuation basis (e.g. `"GAAP"`, `"STAT"`)                |
| `methodology`     | `str \| None`  | No       | Calculation methodology if applicable                    |
| `policy_id`       | `str`          | Yes      | Current policy being projected                           |
| `scenario_id`     | `str`          | Yes      | Active scenario                                          |
| `projection_month`| `int`          | Yes      | Current month in the projection timeline                 |
| `duration`        | `int \| None`  | No       | Policy duration in months at projection start            |
| `attained_age`    | `int \| None`  | No       | Insured's attained age                                   |
| `valuation_date`  | `str \| None`  | No       | ISO date string for the valuation date                   |
| `assumption_set`  | `str \| None`  | No       | Which assumption set to use for lookups                  |
| `factor_set`      | `str \| None`  | No       | Which factor set to use for lookups                      |
| `scenario_set`    | `str \| None`  | No       | Which scenario set to use for override lookups           |
| `projection_key`  | `str \| None`  | No       | Arbitrary string key identifying this run configuration  |

---

### Results, Errors & Trace

#### `CalculationError`
Describes a single failure during a run.

| Field             | Type                    | Notes                                         |
|-------------------|-------------------------|-----------------------------------------------|
| `type`            | `CalculationErrorType`  | Category of error                             |
| `message`         | `str`                   | Human-readable description                    |
| `variable_id`     | `str \| None`           |                                               |
| `formula_id`      | `str \| None`           |                                               |
| `policy_id`       | `str \| None`           |                                               |
| `period`          | `int \| None`           | Projection month where the error occurred     |
| `scenario_id`     | `str \| None`           |                                               |
| `dependency_path` | `list[str]`             | Variable name chain leading to the error      |

#### `CalculationResult`
The result of computing one variable for one policy/period/scenario.

| Field         | Type                  | Notes                                               |
|---------------|-----------------------|-----------------------------------------------------|
| `run_id`      | `str`                 |                                                     |
| `variable_id` | `str`                 |                                                     |
| `policy_id`   | `str \| None`         |                                                     |
| `period`      | `int \| None`         |                                                     |
| `scenario_id` | `str \| None`         |                                                     |
| `value`       | `Any`                 | The computed value; `None` on error                 |
| `status`      | `str`                 | `"success"`, `"error"`, or `"skipped"`              |
| `error`       | `CalculationError\|None` | Populated when `status == "error"`               |

#### `VariableResolutionResult`
Describes where a variable's value came from — the output of the variable resolver.

| Field           | Type             | Notes                                                         |
|-----------------|------------------|---------------------------------------------------------------|
| `variable_name` | `str`            |                                                               |
| `value`         | `Any`            | The resolved value                                            |
| `source_type`   | `str`            | Which resolver produced it                                    |
| `source_table`  | `str \| None`    | The table that was queried, if any                            |
| `lookup_keys`   | `dict[str, Any]` | The actual key values used in the lookup                      |
| `was_defaulted` | `bool`           | `True` if the variable's `default_value` was used             |
| `error_message` | `str \| None`    | Populated if resolution failed                                |

#### `CalculationTrace`
A recursive tree showing how an output value was derived. Each node has its own `dependencies` list, which is itself a list of `CalculationTrace` objects. This nesting can go as deep as the dependency chain.

| Field           | Type                       | Notes                                          |
|-----------------|----------------------------|------------------------------------------------|
| `variable_name` | `str`                      | Variable being traced                          |
| `formula_id`    | `str \| None`              | Formula used to compute it, if any             |
| `value`         | `Any`                      | The computed/resolved value                    |
| `source`        | `VariableResolutionResult \| None` | Where the value came from              |
| `dependencies`  | `list[CalculationTrace]`   | The inputs that produced this value (recursive)|
| `period`        | `int \| None`              | Projection month                               |
| `scenario_id`   | `str \| None`              |                                                |

The `from __future__ import annotations` import at the top of `schemas.py` is required for this self-reference to work in Python 3.11.

#### `ProjectionSummary`
High-level stats about a completed run. All fields default to `0`.

| Field                       | Type  |
|-----------------------------|-------|
| `policy_count`              | `int` |
| `period_count`              | `int` |
| `scenario_count`            | `int` |
| `calculated_variable_count` | `int` |
| `error_count`               | `int` |
| `warning_count`             | `int` |

#### `ProjectionResultSet`
The full result of one scenario in a run.

| Field         | Type                      | Notes                                                |
|---------------|---------------------------|------------------------------------------------------|
| `run_id`      | `str`                     |                                                      |
| `scenario_id` | `str`                     |                                                      |
| `started_at`  | `datetime`                |                                                      |
| `completed_at`| `datetime \| None`        | `None` if still running                              |
| `status`      | `RunStatus`               |                                                      |
| `results`     | `list[CalculationResult]` | One entry per variable/policy/period combination     |
| `errors`      | `list[CalculationError]`  | All errors encountered                               |
| `traces`      | `list[CalculationTrace]`  | Only populated when `debug_mode=True`                |
| `summary`     | `ProjectionSummary`       | Aggregate counts                                     |

---

### Dependency Graph (for UI visualization)

These three models are used exclusively to send dependency graph data to the frontend for rendering.

#### `DependencyError`
| Field         | Type       | Notes                                         |
|---------------|------------|-----------------------------------------------|
| `type`        | `str`      | `"circular_dependency"` or `"missing_dependency"` |
| `variable_id` | `str`      |                                               |
| `message`     | `str`      |                                               |
| `path`        | `list[str]`| Variable name chain forming the problem path  |

#### `GraphNode`
| Field    | Type            | Notes                                              |
|----------|-----------------|----------------------------------------------------|
| `id`     | `str`           |                                                    |
| `label`  | `str`           | Display label                                      |
| `kind`   | `VariableKind`  | Drives the node color/icon in the UI               |
| `status` | `str`           | `"valid"`, `"error"`, or `"warning"` — drives border color |

#### `GraphEdge`
| Field    | Type          | Notes                             |
|----------|---------------|-----------------------------------|
| `id`     | `str`         |                                   |
| `source` | `str`         | Source node `id`                  |
| `target` | `str`         | Target node `id`                  |
| `label`  | `str \| None` | Optional label on the edge        |

#### `DependencyGraphView`
| Field   | Type              |
|---------|-------------------|
| `nodes` | `list[GraphNode]` |
| `edges` | `list[GraphEdge]` |

---

### API Schemas

These models are not used by the engine internals — they are the HTTP request/response shapes.

#### `ProjectCreate` (request body)
```json
{
  "name": "Q4 2024 Projection",
  "description": "Optional"
}
```
- `name`: required, 1–255 characters
- `description`: optional

#### `ProjectResponse` (response body)
```json
{
  "id": "550e8400-...",
  "name": "Q4 2024 Projection",
  "description": null,
  "created_at": "2026-06-25T10:00:00Z",
  "updated_at": "2026-06-25T10:00:00Z"
}
```

#### `ProjectUpdate` (PATCH request body)
Both fields are optional — send only what you want to change:
```json
{
  "name": "New Name",
  "description": "Updated description"
}
```

#### `ProjectListResponse`
```json
{
  "projects": [ ...ProjectResponse ],
  "total": 3
}
```

#### `ImportPreviewResponse`
Returned after uploading a file, before confirming the import.
```json
{
  "columns": ["policy_id", "product", "face_amount", "issue_age"],
  "row_count": 4500,
  "sample_rows": [
    { "policy_id": "P001", "product": "FIUL", "face_amount": 500000, "issue_age": 45 }
  ]
}
```

#### `RunStatusResponse`
Returned by the run status polling endpoint (Phase 5+).
```json
{
  "run_id": "abc-123",
  "status": "running",
  "started_at": "2026-06-25T10:00:00Z",
  "completed_at": null,
  "summary": null
}
```

---

## Frontend Integration Guide — Phase 2

### What changed from Phase 1

Phase 1's `ProjectCreate`, `ProjectResponse`, and `ProjectListResponse` are still the same shapes — the endpoints work identically. Phase 2 adds `ProjectUpdate` to allow partial updates (coming in a future phase when the PATCH endpoint is wired up).

### New shapes to know about

These are not yet wired to API endpoints but define exactly what the frontend will receive when those endpoints go live:

**`ImportPreviewResponse`** — what comes back after a file upload before you confirm:
```json
{
  "columns": ["policy_id", "product", ...],
  "row_count": 4500,
  "sample_rows": [{ ...first few rows... }]
}
```

**`RunStatusResponse`** — what comes back when polling run progress:
```json
{
  "run_id": "...",
  "status": "running" | "success" | "failed" | "partial_success" | "pending",
  "started_at": "...",
  "completed_at": null,
  "summary": { "policy_count": 0, "error_count": 0, ... }
}
```

**`DependencyGraphView`** — what comes back for the dependency graph UI:
```json
{
  "nodes": [
    { "id": "lapse_rate", "label": "Lapse Rate", "kind": "assumption", "status": "valid" }
  ],
  "edges": [
    { "id": "e1", "source": "lapse_rate", "target": "net_cash_flow", "label": null }
  ]
}
```

### Enum values reference

These are the only valid string values for each field. Sending anything else will get a `422`.

| Field context               | Valid values                                                                           |
|-----------------------------|----------------------------------------------------------------------------------------|
| Variable `kind`             | `input`, `assumption`, `factor`, `formula`, `output`, `lookup`                         |
| Variable `data_type`        | `number`, `string`, `boolean`, `date`, `vector`, `table`                               |
| Variable `status`           | `valid`, `invalid`, `missing_dependency`, `circular_reference`                         |
| Formula `status`            | `draft`, `active`, `deprecated`                                                        |
| Run `status`                | `pending`, `running`, `success`, `partial_success`, `failed`                           |
| Scenario override `operation`| `set`, `add`, `subtract`, `multiply`, `percent_change`                                |
| Calculation error `type`    | `missing_variable`, `missing_value`, `invalid_formula`, `circular_dependency`, `division_by_zero`, `invalid_data_type`, `lookup_failed`, `scenario_override_failed` |
| Variable source `type`      | `input`, `assumption`, `factor`, `scenario`, `formula`, `prior_output`, `manual`       |


---

---

# Phase 3 — Import Framework (TSV, CSV, Excel → PostgreSQL)

**Status:** Complete  
**Files added:** `app/data/importers/` (4 files), `app/data/validation/` (5 files), `app/api/imports.py`  
**Files updated:** `app/main.py` (imports router registered)

---

## Overview

Phase 3 builds the full data ingestion pipeline. A file is uploaded through the API, the format is auto-detected from the extension, it is parsed into rows, validated against rules specific to its data type, and — if no hard errors are found — stored in PostgreSQL. The frontend can preview parsed rows before or after storing, and all validation errors (with row numbers and column names) come back in the response.

---

## Module Structure

```
app/
├── data/
│   ├── importers/
│   │   ├── base.py           # orchestration: detect, parse, validate, store
│   │   ├── tsv_importer.py   # tab-separated parser
│   │   ├── csv_importer.py   # comma-separated parser
│   │   └── excel_importer.py # .xlsx parser (openpyxl)
│   └── validation/
│       ├── common.py                 # shared validation helpers
│       ├── inforce_validator.py      # rules for policy/inforce files
│       ├── assumption_validator.py   # rules for assumption files
│       ├── factor_validator.py       # rules for factor files
│       └── scenario_validator.py     # rules for scenario files
└── api/
    └── imports.py            # upload + preview endpoints
```

---

## Import Pipeline

Every upload flows through the same four steps:

```
Upload → detect_format() → parse_file() → validate_func() → store_func()
                                                ↓
                                    hard errors? → return errors, skip store
                                    warnings only? → store anyway, return warnings
```

The pipeline is implemented in `base.py` as `import_file()`. It is format-agnostic — the caller passes in the validator and storage function.

---

## Parsers

### Format Detection — `base.detect_format(filename)`

Detects format from the file extension. Case-insensitive.

| Extension(s)       | Detected format |
|--------------------|-----------------|
| `.tsv`, `.tab`     | `tsv`           |
| `.csv`             | `csv`           |
| `.xlsx`, `.xls`    | `xlsx`          |
| anything else      | raises `ValueError` — rejected with HTTP 400 |

### TSV Parser — `tsv_importer.parse_tsv(file_path)`

- Opens with `encoding="utf-8-sig"` — BOM is stripped automatically
- Uses Python's `csv.DictReader` with `delimiter="\t"`
- Handles quoted fields (embedded tabs inside quotes are not split)
- First row is always the header row

### CSV Parser — `csv_importer.parse_csv(file_path)`

- Opens with `encoding="utf-8-sig"` — BOM stripped
- Uses `csv.DictReader` with default (comma) delimiter
- Handles commas inside quoted fields correctly (Python's csv module RFC 4180 compliance)
- First row is always the header row

### Excel Parser — `excel_importer.parse_excel(file_path)`

- Uses `openpyxl` in `read_only=True, data_only=True` mode
- Reads only the **first (active) worksheet**
- First row = column headers. If a header cell is `None`, it gets a synthetic name (`col_0`, `col_1`, …)
- Completely empty rows (all cells `None`) are skipped
- Cell values are returned as Python native types (int, float, str, datetime, None)

---

## Validation Layer

### Common Helpers — `validation/common.py`

These functions are shared across all per-type validators.

#### `check_required_columns(columns, required) → list[str]`
Returns a list of column names that are in `required` but not present in the parsed data.  
Used by every validator as the first check — if columns are missing, later checks are skipped.

#### `check_duplicate_ids(rows, id_column) → list[dict]`
Finds values in `id_column` that appear more than once. Returns one error per duplicate value with the 1-based row numbers where it appears.

```json
{
  "type": "duplicate_id",
  "message": "Duplicate policy_id 'P001' found on rows [1, 45].",
  "column": "policy_id",
  "value": "P001",
  "rows": [1, 45]
}
```

#### `check_data_types(rows, column_types) → list[dict]`
Checks that columns match their expected type. Currently supports `"number"` and `"integer"` (both checked with `float()` cast). `"string"` always passes. Empty/null values are skipped — they are handled by a separate required check.

```json
{
  "type": "type_mismatch",
  "message": "Expected number for column 'premium', got 'N/A' on row 12.",
  "column": "premium",
  "row": 12,
  "value": "N/A"
}
```

#### `check_plausible_ranges(rows, range_rules) → list[dict]`
Checks numeric columns against `min` / `max` bounds. Each rule can be configured as a hard `error` or a `warning` via `allow_warnings`.

- `allow_warnings: True` → severity is `"warning"` (stored, surfaced to UI)
- `allow_warnings: False` → severity is `"error"` (blocks storage)

```json
{
  "type": "error_range",
  "message": "Value -0.05 in column 'mortality_rate' is below minimum 0 on row 7.",
  "column": "mortality_rate",
  "row": 7,
  "value": -0.05,
  "severity": "error"
}
```

---

### Error vs Warning Distinction

The pipeline in `base.import_file()` separates errors from warnings:
- **Hard errors** (`severity != "warning"`) — block storage entirely. The response includes errors but `stored_count` is `0`.
- **Warnings** — storage proceeds. The response includes the warning list alongside the stored count.

---

### Per-Type Validators

#### `validate_inforce(rows)` — `inforce_validator.py`

**Required columns:** `policy_id`

**Type checks** (for columns that exist):

| Column          | Expected type |
|-----------------|---------------|
| `policy_id`     | string        |
| `issue_age`     | integer       |
| `premium`       | number        |
| `account_value` | number        |
| `gender`        | string        |
| `product_type`  | string        |

**Range checks:**

| Column          | Min | Max | Severity |
|-----------------|-----|-----|----------|
| `issue_age`     | 0   | 120 | warning  |
| `premium`       | 0   | —   | warning  |
| `account_value` | 0   | —   | warning  |

**Additional check:** Duplicate `policy_id` values → hard error.

#### `validate_assumptions(rows)` — `assumption_validator.py`

**Required columns:** `table_name`, `table_type`

**Range checks:**

| Column           | Min | Max | Severity |
|------------------|-----|-----|----------|
| `age`            | 0   | 120 | warning  |
| `duration`       | 0   | 100 | warning  |
| `mortality_rate` | 0   | 1   | **error** |
| `lapse_rate`     | 0   | 1   | **error** |

Negative mortality or lapse rates are hard errors and block storage.

#### `validate_factors(rows)` — `factor_validator.py`

**Required columns:** `table_name`, `table_type`

**Range checks:**

| Column               | Min | Max | Severity |
|----------------------|-----|-----|----------|
| `cap`                | 0   | 1   | warning  |
| `participation_rate` | 0   | 2   | warning  |
| `spread`             | 0   | 0.5 | warning  |
| `option_budget`      | 0   | —   | warning  |
| `duration`           | 0   | 50  | warning  |

#### `validate_scenarios(rows)` — `scenario_validator.py`

**Required columns:** `scenario_name`, `target_variable`, `operation`, `value`

**Additional check:** `operation` must be one of: `set`, `add`, `subtract`, `multiply`, `percent_change`. Any other value is a hard error.

```json
{
  "type": "invalid_operation",
  "message": "Invalid operation 'override' on row 3. Must be one of: add, multiply, percent_change, set, subtract.",
  "column": "operation",
  "row": 3,
  "value": "override"
}
```

---

## API Endpoints — `app/api/imports.py`

All endpoints are under `/imports` prefix, tagged `imports`.

### File Upload Behavior

Every upload endpoint:
1. Validates the file extension — rejects unsupported formats with `400`
2. Saves the file to `backend/uploads/` with a UUID prefix to avoid name collisions
3. Runs the appropriate validator
4. Stores data (or not, if hard errors) and returns the result

### `POST /imports/inforce?project_id={id}`

Uploads an inforce/policy file. Validates and stores rows to `inforce_files` and `inforce_records`.

**Request:** `multipart/form-data`, field name `file`, query param `project_id` required.

**Response `201`:**
```json
{
  "row_count": 1000,
  "stored_count": 1000,
  "columns": ["policy_id", "product_type", "issue_age", "account_value"],
  "errors": []
}
```

If hard errors exist:
```json
{
  "row_count": 1000,
  "stored_count": 0,
  "columns": ["policy_id", ...],
  "errors": [
    {
      "type": "duplicate_id",
      "message": "Duplicate policy_id 'P001' found on rows [1, 45].",
      "column": "policy_id",
      "value": "P001",
      "rows": [1, 45]
    }
  ]
}
```

**What gets stored:**
- One row in `inforce_files` with file metadata (`filename`, `file_type`, `row_count`, `columns_detected`)
- One row per policy in `inforce_records` with `policy_id` and the full row as `data` JSON

If a row has no `policy_id` column, a synthetic key `row_{index}` is used.

---

### `POST /imports/assumptions?project_id={id}`

Uploads an assumption file. Validates against assumption rules.

> **Note:** Full storage into `assumption_sets` / `assumption_tables` is pending (product-specific logic comes in a later phase). This endpoint currently validates and returns a preview — rows are not yet persisted to the assumption tables.

**Response `201`:**
```json
{
  "row_count": 50,
  "columns": ["table_name", "table_type", "age", "duration", "mortality_rate"],
  "errors": [],
  "preview": [ ...first 20 rows... ]
}
```

---

### `POST /imports/factors?project_id={id}`

Uploads a factor file. Validates against factor rules. Same response shape as assumptions.

> **Note:** Full storage pending (same as assumptions).

---

### `POST /imports/scenarios?project_id={id}`

Uploads a scenario override file. Validates against scenario rules. Same response shape.

> **Note:** Full storage pending.

---

### `GET /imports/preview?file_path={path}`

Returns a preview of a previously uploaded file (first 20 rows) without storing anything. `file_path` must be the server-side path returned from a previous upload, or a known path on the server.

**Response `200` — `ImportPreviewResponse`:**
```json
{
  "columns": ["policy_id", "product_type", "issue_age"],
  "row_count": 1000,
  "sample_rows": [ ...first 20 rows... ]
}
```

**Response `404`:** File not found at the given path.

---

## Uploaded File Storage

Files are written to `backend/uploads/` with the naming pattern:

```
{uuid_hex}_{original_filename}
```

Example: `a3f8c1d2e4b5...._policies_q4.tsv`

The UUID prefix prevents filename collisions from concurrent uploads. Files are not automatically cleaned up — this is a known gap to address in a later phase.

---

## Frontend Integration Guide — Phase 3

### Uploading a file

All upload endpoints use `multipart/form-data`. Send the file in a field named `file` and pass `project_id` as a query parameter.

```js
const formData = new FormData();
formData.append("file", selectedFile);

const response = await fetch(
  `/imports/inforce?project_id=${projectId}`,
  { method: "POST", body: formData }
);
const result = await response.json();
```

### Response fields

| Field           | Type            | Meaning                                             |
|-----------------|-----------------|-----------------------------------------------------|
| `row_count`     | `number`        | Total rows parsed from the file                     |
| `stored_count`  | `number`        | Rows actually written to the database               |
| `columns`       | `string[]`      | Column names detected in the file                   |
| `errors`        | `ErrorObject[]` | Validation errors and warnings (see below)          |
| `preview`       | `object[]`      | First 20 rows (assumptions/factors/scenarios only)  |

When `stored_count === 0` and `errors` is non-empty, the file was rejected due to hard errors. When `stored_count > 0` and `errors` is non-empty, warnings were found but the file was stored.

### Error object shape

All validation errors follow this structure:

```ts
interface ValidationError {
  type: string;           // "missing_column" | "duplicate_id" | "type_mismatch" | "error_range" | "warning_range" | "invalid_operation"
  message: string;        // Human-readable description — safe to show directly
  column?: string;        // Which column the error is about
  row?: number;           // 1-based row number (omitted for column-level errors)
  value?: any;            // The offending value
  rows?: number[];        // For duplicate_id: all row numbers with that duplicate
  severity?: "error" | "warning";  // Present on range checks only
}
```

### Supported file formats

| Extension    | Accepted |
|--------------|----------|
| `.tsv`       | ✅       |
| `.tab`       | ✅       |
| `.csv`       | ✅       |
| `.xlsx`      | ✅       |
| `.xls`       | ✅       |
| anything else| ❌ 400   |

### Required columns by file type

Show these to users before or during upload so they know what's expected.

**Inforce files** — required: `policy_id`

**Assumption files** — required: `table_name`, `table_type`

**Factor files** — required: `table_name`, `table_type`

**Scenario files** — required: `scenario_name`, `target_variable`, `operation`, `value`

### Known limitations in this phase

- Assumption, factor, and scenario uploads are **validated but not persisted** to their respective tables. They return a `preview` array instead. Full storage comes in a later phase.
- The `GET /imports/preview` endpoint requires a server-side file path — it is not designed for end-user use yet. A proper flow where the frontend uploads first and gets back a preview in the same response is the recommended pattern.
- Uploaded files in `backend/uploads/` are not cleaned up automatically.
- Non-UTF-8 encoded files will produce a read error — encoding detection is not implemented. Files must be UTF-8 or UTF-8-with-BOM.


---

---

# Phase 4 — Variable Registry & Resolver

**Status:** Complete  
**Files added:** `app/core/variable_registry/` (2 files), `app/core/trace_engine/tracer.py`, `app/core/entry_points.py`, `app/api/variables.py`  
**Files updated:** `app/main.py` (variables router registered)

---

## Overview

Phase 4 builds the Variable Registry and Resolver — the core of the engine's data layer. A variable is a named piece of data the projection engine can produce or consume. The registry is the catalog of what variables exist and where they come from. The resolver is what actually goes and fetches a value at runtime given a context (policy, scenario, month). Every resolution is automatically logged to `trace_logs`.

---

## Module Structure

```
app/
├── core/
│   ├── variable_registry/
│   │   ├── registry.py     # CRUD against variable_registry table
│   │   └── resolver.py     # runtime value resolution
│   ├── trace_engine/
│   │   └── tracer.py       # log_resolution() — writes to trace_logs
│   └── entry_points.py     # non-HTTP callable wrappers
└── api/
    └── variables.py        # REST endpoints for the registry
```

---

## Variable Registry — `core/variable_registry/registry.py`

Provides all CRUD operations against the `variable_registry` PostgreSQL table. All functions take a SQLAlchemy `Session` as the first argument. They return Pydantic `VariableDefinition` objects, not ORM models.

### Internal: `_model_to_definition(model)`

Converts a `VariableRegistry` ORM row into a `VariableDefinition`. This is the only place where the ORM ↔ Pydantic mapping lives. Key mapping decisions:

- `model.source_type` maps to `VariableDefinition.kind`
- `model.default_value` is stored in the DB as `{"value": <val>}` JSON — the wrapper is stripped when reading back
- A `source` object is reconstructed from `source_type` + `source_table` + `lookup_keys`:
  - `manual` → `ManualSource`
  - `input` → `InputSource` with `column_name = model.name`
  - anything with a `source_table` → appropriate typed source with `table_id`

### `register(db, variable: VariableDefinition) → VariableDefinition`

Creates a new row in `variable_registry`. If a variable with the same `name` already exists, PostgreSQL raises a unique constraint violation — the API layer catches this and returns `409 Conflict`.

Storage notes:
- `default_value` is stored wrapped: `{"value": <actual_value>}`
- `lookup_keys` is taken from `source.lookup_keys` if present
- `source_table` is taken from `source.table_id` if present

### `get_by_name(db, name) → VariableDefinition | None`

Queries by `name` (unique, indexed). Returns `None` if not found.

### `get_by_id(db, variable_id) → VariableDefinition | None`

Queries by UUID primary key. Returns `None` if not found.

### `list_all(db, product=None, kind=None) → list[VariableDefinition]`

Returns all variables, ordered by name. Optional filters:
- `kind` — filters by `source_type` column (e.g. `"assumption"`, `"formula"`)
- `product` — filters by `product_applicability` array using PostgreSQL `ANY()` operator

### `update(db, name, updates: dict) → VariableDefinition | None`

Applies a partial update. Only these fields can be updated:

| Allowed field             |
|---------------------------|
| `display_name`            |
| `description`             |
| `data_type`               |
| `source_type`             |
| `source_table`            |
| `lookup_keys`             |
| `required`                |
| `default_value`           |
| `product_applicability`   |
| `basis_applicability`     |

Any keys in `updates` not in this allowlist are silently ignored.

### `delete(db, name) → bool`

Deletes by name. Returns `True` if deleted, `False` if not found.

**Important:** If a formula in `formula_registry` references this variable as `output_variable`, PostgreSQL will raise an FK violation (`ON DELETE RESTRICT`). If any `formula_dependencies` row references this variable's name, those rows are cascade-deleted. The API layer should handle the FK violation and return a meaningful error.

---

## Variable Resolver — `core/variable_registry/resolver.py`

The resolver is what the projection engine calls every time it needs the value of a variable. It takes a variable name and a `ProjectionContext`, looks up the variable definition, determines the source type, fetches the value from the right place, applies defaults, and returns a `VariableResolutionResult`.

### `resolve(variable_name, context, db, trace_logger=None) → VariableResolutionResult`

The main entry point. Never raises an exception — all errors are returned inside `VariableResolutionResult.error_message`.

**Resolution flow:**

```
1. Look up variable definition by name
   → not found: return error result immediately

2. Build lookup keys from context (policy_id, age, duration, etc.)

3. Dispatch to the correct source handler based on variable.kind:
   input        → _inforce_lookup()
   assumption   → _assumption_lookup()
   factor       → _factor_lookup()
   scenario     → _scenario_lookup()
   prior_output → _prior_period_lookup()
   manual       → read value from source definition
   formula      → returns None (formula output handled by formula engine)

4. If value is still None and default_value exists → use default, set was_defaulted=True

5. If value is still None and required=True → set error_message

6. Call trace_logger(context, result) if provided

7. Return VariableResolutionResult
```

### `build_lookup_keys(definition, context) → dict`

Builds the lookup key dictionary from the current `ProjectionContext`. Maps:

| Context field       | Key in dict         |
|---------------------|---------------------|
| `policy_id`         | `"policy_id"`       |
| `projection_month`  | `"projection_month"`|
| `attained_age`      | `"age"`             |
| `duration`          | `"duration"`        |
| `valuation_date`    | `"valuation_date"`  |
| `scenario_id`       | `"scenario_id"`     |
| `product`           | `"product_type"`    |

Only non-None context fields are included. These keys are also written to `trace_logs.lookup_keys`.

---

### Source Handlers

#### `_inforce_lookup(db, policy_id) → dict | None`

Queries `inforce_records` for the most recent record matching `policy_id` (ordered by `created_at DESC`). Returns the full `data` JSON dict, or `None` if not found.

The resolver then extracts the specific column by `column_name` from the record dict.

**Multiple records for same policy_id:** The most recent upload wins (latest `created_at`).

#### `_assumption_lookup(db, table_name, keys) → Any`

Queries `assumption_tables` by `table_name`. Iterates through `data` rows and matches on `age` and `duration` keys (both optional — if the context doesn't provide a key or the row doesn't have it, that dimension is skipped).

**Multiple matching rows:** Returns the first match. If no match, returns `data[0]` as a fallback. This is a deliberate design decision — assumption tables are expected to be well-structured and the first row is a reasonable default when an exact match doesn't exist.

#### `_factor_lookup(db, table_name, keys) → Any`

Same pattern as assumption lookup, but matches on `duration` only. Falls back to `data[0]` if no match.

#### `_scenario_lookup(db, scenario_id, variable_name, context) → Any`

Queries `scenario_tables` by `scenario_name`. Iterates through `overrides` and finds the first override where:
- `target_variable` matches `variable_name`
- `applies_from_period` is `None` or ≤ current `projection_month`
- `applies_to_period` is `None` or ≥ current `projection_month`

Returns the full override dict (including `operation` and `value`). Returns `None` if no matching override exists for this period.

The formula engine is responsible for applying the `operation` to the base value — the resolver just surfaces the override.

#### `_prior_period_lookup(db, variable_name, context, offset) → Any`

Looks up `run_outputs` for the same `run_id` / `policy_id` / `scenario_id`, at `projection_month - offset`. Returns the stored value, or `None` if month 0 or not yet computed.

Values in `run_outputs.value` are stored as `{"value": <val>}` JSON — the wrapper is unwrapped on read.

---

## Trace Logger — `core/trace_engine/tracer.py`

### `log_resolution(context, result, db) → None`

Writes one row to `trace_logs` after every variable resolution. Silently swallows exceptions (rolls back on failure) to avoid breaking a projection run due to a logging error.

**Skips logging when `run_id` is in:** `("r1", "test", "placeholder")` — these are test/development contexts that should not pollute the trace table.

**What is logged:**

| `trace_logs` column  | Value                                              |
|----------------------|----------------------------------------------------|
| `run_id`             | `context.run_id`                                   |
| `policy_id`          | `context.policy_id`                                |
| `scenario_id`        | `context.scenario_id`                              |
| `projection_month`   | `context.projection_month`                         |
| `variable_name`      | `result.variable_name`                             |
| `output_value`       | `{"value": result.value}` (wrapped) or `null`      |
| `source_type`        | `result.source_type`                               |
| `source_table`       | `result.source_table`                              |
| `lookup_keys`        | `result.lookup_keys` dict                          |
| `error_message`      | `result.error_message` (null on success)           |

`formula_id` is not written at this stage — it is populated by the formula engine in Phase 5.

---

## Non-HTTP Entry Points — `core/entry_points.py`

These functions wrap the registry and resolver so they can be called from scripts, tests, or background jobs without needing a FastAPI request context. They manage their own database session.

### `register_variable(variable: VariableDefinition) → VariableDefinition`

Opens a session, calls `registry.register()`, closes the session.

### `resolve_variable(variable_name, context) → VariableResolutionResult`

Opens a session, calls `resolver.resolve()` with trace logging wired in, closes the session.

Usage from a plain Python script:

```python
from app.core.entry_points import register_variable, resolve_variable
from app.models.schemas import VariableDefinition, ProjectionContext

var = register_variable(VariableDefinition(
    id="v1",
    name="issue_age",
    kind="input",
    data_type="number",
    source={"type": "input", "dataset_id": "d1", "column_name": "issue_age"},
))

result = resolve_variable("issue_age", ProjectionContext(
    project_id="p1", run_id="r1", product="FIUL",
    policy_id="P001", scenario_id="base", projection_month=0,
))
print(result.value)
```

---

## API Endpoints — `app/api/variables.py`

All endpoints under `/variables`, tagged `variables`. Registered in `main.py`.

### `GET /variables`

List all registered variables. Supports optional query filters.

| Query param | Type   | Effect                                      |
|-------------|--------|---------------------------------------------|
| `product`   | string | Filter to variables applicable to a product |
| `kind`      | string | Filter by variable kind (`input`, `assumption`, `formula`, etc.) |

**Response `200`:** `list[VariableDefinition]` ordered by name.

### `POST /variables`

Register a new variable.

**Request body:** `VariableDefinition` (see Phase 2 schema reference)

**Response `201`:** Created `VariableDefinition`

**Response `409`:** Variable name already exists:
```json
{ "detail": "Variable 'issue_age' already exists." }
```

### `GET /variables/{name}`

Get a single variable by its unique name.

**Response `200`:** `VariableDefinition`  
**Response `404`:** Variable not found.

### `PUT /variables/{name}`

Update fields on an existing variable. Body is a plain JSON object with only the fields to change.

```json
{
  "description": "Updated description",
  "required": false
}
```

**Response `200`:** Updated `VariableDefinition`  
**Response `404`:** Variable not found.

### `DELETE /variables/{name}`

Delete a variable. Returns `204 No Content` on success.

**Response `204`:** Deleted  
**Response `404`:** Not found

**Note:** If a formula references this variable as its `output_variable`, the delete will fail with a `500` (FK constraint violation from PostgreSQL). A future phase will handle this with a proper `409` response.

---

## Frontend Integration Guide — Phase 4

### Variable registry API

#### List variables with filters

```
GET /variables?kind=assumption
GET /variables?product=FIUL
GET /variables?kind=formula&product=FIUL
```

Returns an array of `VariableDefinition` objects.

#### Create a variable

```
POST /variables
Content-Type: application/json

{
  "id": "v-001",
  "name": "issue_age",
  "label": "Issue Age",
  "kind": "input",
  "data_type": "number",
  "source": {
    "type": "input",
    "dataset_id": "d1",
    "column_name": "issue_age"
  },
  "required": true,
  "description": "Age of the insured at policy issue",
  "product_applicability": ["FIUL", "RILA"],
  "basis_applicability": []
}
```

#### Update a variable (partial)

```
PUT /variables/issue_age

{
  "description": "New description",
  "required": false
}
```

Only send the fields you want to change. Unrecognised fields are ignored.

#### Delete a variable

```
DELETE /variables/issue_age
→ 204 No Content
```

Will fail if any formula uses this variable as its output. Check the formula registry before deleting.

---

### `VariableDefinition` shape reminder

```ts
interface VariableDefinition {
  id: string;
  name: string;                    // unique key — used everywhere
  label?: string;                  // display name
  kind: "input" | "assumption" | "factor" | "formula" | "output" | "lookup";
  data_type: "number" | "string" | "boolean" | "date" | "vector" | "table";
  source?: VariableSource;         // how to resolve the value
  dependencies: string[];          // other variable names this one depends on
  required: boolean;
  default_value?: any;
  product_applicability: string[]; // empty = all products
  basis_applicability: string[];   // empty = all bases
  description?: string;
  status: "valid" | "invalid" | "missing_dependency" | "circular_reference";
}
```

### Resolution result shape

When the engine resolves a variable (not exposed as a public API endpoint yet, but used in run outputs and trace logs), it returns:

```ts
interface VariableResolutionResult {
  variable_name: string;
  value: any;                 // the resolved value; null on error
  source_type: string;        // where it came from
  source_table?: string;      // which table was queried
  lookup_keys: Record<string, any>;  // the key values used in the lookup
  was_defaulted: boolean;     // true if the default_value was used
  error_message?: string;     // set if resolution failed
}
```

A result with `error_message` set and `value: null` means the variable could not be resolved. The frontend will encounter this shape in trace log data and run output error lists.

### Scenario overrides and the resolver

The resolver respects period ranges on scenario overrides. An override only applies when `projection_month` is within `[applies_from_period, applies_to_period]`. Both bounds are inclusive and both are optional (null = no limit). The frontend should surface this when building the scenario editor UI.

### Trace logs

Every variable resolution during a run writes to `trace_logs`. A dedicated trace API endpoint comes in a later phase. For now, trace data can be inspected directly in the database or via the run output records.


---

---

# Phase 5 — Formula Registry & Dependency Engine

**Status:** Complete  
**Files added:** `app/core/formula_engine/` (3 files), `app/core/dependency_engine/` (2 files), `app/api/formulas.py`  
**Files updated:** `app/core/entry_points.py` (formula + dependency entries added)

---

## Overview

Phase 5 introduces two tightly coupled systems. The Formula Registry is the catalog of every calculation the engine can perform — each formula declares what variable it produces and what variables it depends on. The Dependency Engine takes that catalog and determines the safe execution order, detects problems (cycles, missing variables, duplicate outputs), and produces a graph structure the frontend can visualize.

Together with the Variable Registry from Phase 4, these two registries form the complete definition layer for the projection engine.

---

## Module Structure

```
app/
├── core/
│   ├── formula_engine/
│   │   ├── registry.py    # CRUD against formula_registry + formula_dependencies
│   │   ├── formulas.py    # FORMULA_FUNCTIONS dict — populated by product modules
│   │   └── validator.py   # validates formula declarations
│   ├── dependency_engine/
│   │   ├── graph.py       # DAG construction, topo sort, cycle/missing detection
│   │   └── visualizer.py  # converts graph to GraphNode/GraphEdge for UI
│   └── entry_points.py    # updated: build_dependency_graph, validate_formula_database, get_execution_order
└── api/
    └── formulas.py        # REST endpoints for the formula registry
```

---

## Formula Functions Registry — `core/formula_engine/formulas.py`

The lowest layer: a Python dictionary mapping string keys to callable functions.

```python
FORMULA_FUNCTIONS: dict[str, Callable] = {}
```

This dict is intentionally empty at this phase. Product-specific formula implementations are added by product modules in later phases using `register_function()`.

### `register_function(key, func)`
Adds a callable to the registry under a string key. The key must match the `function_ref` field on a `FormulaDefinition`.

### `get_function(key) → Callable | None`
Retrieves a callable by key. Returns `None` if not registered.

### `list_functions() → list[str]`
Returns all registered function keys. Used by the validator to report what is available.

**Important:** `FORMULA_FUNCTIONS` is a module-level singleton. It is populated at import time by product modules. The formula validator checks against this dict, so a formula with a `function_ref` that hasn't been registered yet will fail validation — this is expected and correct behavior during phased development.

---

## Formula Registry — `core/formula_engine/registry.py`

CRUD against `formula_registry` and `formula_dependencies` tables. All functions take a `Session`.

### `register(db, formula: FormulaDefinition) → FormulaDefinition`

Creates a `formula_registry` row then inserts one `formula_dependencies` row per entry in `formula.dependencies`. Uses `db.flush()` before inserting dependencies so the FK constraint is satisfied within the same transaction.

Dependencies are stored as `depends_on_variable` → `variable_registry.name` FK. If a dependency variable name does not exist in `variable_registry`, PostgreSQL raises an FK violation on commit.

### `get_by_id(db, formula_id) → FormulaDefinition | None`

Looks up by UUID primary key. The `_model_to_definition()` helper reads the related `FormulaDependency` rows and populates `dependencies` as a list of variable name strings.

### `get_by_output(db, output_variable) → FormulaDefinition | None`

Finds the formula that produces a specific variable. Used by the API to enforce the one-formula-per-output constraint.

### `list_all(db, category=None, product=None) → list[FormulaDefinition]`

Returns all formulas ordered by name. Filters:
- `category` — exact string match on `FormulaRegistry.category`
- `product` — PostgreSQL `ANY()` filter on `product_applicability` array

### `update(db, formula_id, updates: dict) → FormulaDefinition | None`

Applies partial updates. Allowed fields:

| Field                   |
|-------------------------|
| `name`                  |
| `output_variable`       |
| `function_ref`          |
| `category`              |
| `product_applicability` |
| `basis_applicability`   |
| `status`                |

**Note:** Updating `dependencies` is not in the allowlist — to change dependencies, delete and re-register the formula. This keeps the dependency table consistent.

### `delete(db, formula_id) → bool`

Deletes the formula row. `formula_dependencies` rows cascade-delete automatically (FK `ON DELETE CASCADE`). Returns `False` if not found.

---

## Formula Validator — `core/formula_engine/validator.py`

### `validate_formula_declaration(db, formula) → list[str]`

Runs three checks on a single formula:

1. **`function_ref` exists** in `FORMULA_FUNCTIONS` — if not, error lists available keys
2. **`output_variable` is registered** in `variable_registry` — if not, error
3. **Each dependency is registered** in `variable_registry` — one error per missing dep

Returns a list of error message strings. Empty list = valid.

### `validate_all(db, formulas) → dict[str, list[str]]`

Runs `validate_formula_declaration` on every formula in the list. Returns a dict of `formula_id → [errors]`. Only formulas with errors appear in the result — formulas with no errors are omitted.

---

## Dependency Engine — `core/dependency_engine/graph.py`

All functions operate purely on `list[FormulaDefinition]` and a set of registered variable names. No database calls.

### `build_adjacency(formulas) → dict[str, set[str]]`

Produces an adjacency list:

```python
{ "output_variable": {"dependency_1", "dependency_2", ...} }
```

One key per formula, value is the set of variables it depends on.

### `topological_sort(formulas) → list[str]`

Returns all variable names in execution order — dependencies come before the formulas that use them. Uses iterative DFS with `visited` and `in_progress` sets to detect cycles.

**On cycle:** raises `ValueError` with message `"Circular dependency: A → B → C → A"`. The path includes the repeated node at both start and end of the cycle string.

**Orphan dependencies** (variables that are inputs to formulas but not outputs of any formula) are appended at the end of the order after all formula outputs are placed. They appear last because they need no computation — they are sourced from inputs.

### `detect_circular_dependencies(formulas) → list[DependencyError]`

Calls `topological_sort` and catches its `ValueError`. Parses the cycle path from the error message and returns a `DependencyError` with:

| Field        | Value                                    |
|--------------|------------------------------------------|
| `type`       | `"circular_dependency"`                  |
| `variable_id`| First variable in the cycle              |
| `message`    | Full error string from topo sort         |
| `path`       | List of variable names forming the cycle |

Returns empty list if no cycles. Only detects one cycle per call (the first one found by DFS).

### `detect_missing_dependencies(formulas, registered_variables) → list[DependencyError]`

`registered_variables` is a `set[str]` of all names currently in `variable_registry`. Returns one `DependencyError` per dependency that appears in a formula's `dependencies` list but is absent from the set.

```python
DependencyError(
    type="missing_dependency",
    variable_id="unregistered_var",
    message="Formula 'calc_nav' depends on 'unregistered_var', which is not registered.",
    path=["calc_nav_output", "unregistered_var"],
)
```

### `detect_duplicate_outputs(formulas) → list[DependencyError]`

Finds any `output_variable` that appears in more than one formula. Returns one error per duplicate, listing all formula names that claim that output.

### `validate_dependencies(formulas, registered_variables) → list[DependencyError]`

Convenience wrapper that runs all three checks and returns the combined list:
1. `detect_circular_dependencies`
2. `detect_missing_dependencies`
3. `detect_duplicate_outputs`

---

## Dependency Graph Visualizer — `core/dependency_engine/visualizer.py`

### `to_graph_view(formulas) → DependencyGraphView`

Converts a list of `FormulaDefinition` objects into a `DependencyGraphView` ready for the frontend to render.

**Node classification:**
- Variables that are the `output_variable` of any formula → `kind = "formula"`
- Variables that only appear as dependencies (no formula produces them) → `kind = "input"`

All nodes start with `status = "valid"`. A future phase will mark nodes `"error"` or `"warning"` based on validation results.

**Edges:** One `GraphEdge` per dependency relationship. Direction is dependency → output (the arrow points from what is needed to what it produces). Each edge gets a UUID as its `id`.

**Example** — formula `net_cash_flow` depends on `premium` and `death_benefit`:

```json
{
  "nodes": [
    { "id": "net_cash_flow", "label": "net_cash_flow", "kind": "formula", "status": "valid" },
    { "id": "premium",       "label": "premium",       "kind": "input",   "status": "valid" },
    { "id": "death_benefit", "label": "death_benefit", "kind": "input",   "status": "valid" }
  ],
  "edges": [
    { "id": "uuid-1", "source": "premium",       "target": "net_cash_flow", "label": null },
    { "id": "uuid-2", "source": "death_benefit", "target": "net_cash_flow", "label": null }
  ]
}
```

---

## Non-HTTP Entry Points — `core/entry_points.py` (updated)

Three new functions added to the existing entry points module:

### `build_dependency_graph(formula_ids=None) → DependencyGraphView`

Loads all formulas from the DB (or a filtered subset by ID list) and calls `to_graph_view()`. Returns the graph structure directly. Used by scripts and tests.

### `validate_formula_database() → list[DependencyError]`

Loads all formulas and all variable names from the DB, then calls `validate_dependencies()`. Returns the full list of dependency errors across the entire formula database.

### `get_execution_order() → list[str]`

Loads all formulas and returns the topological sort result — the list of variable names in the order they must be computed. Raises `ValueError` if any circular dependency exists.

---

## API Endpoints — `app/api/formulas.py`

All endpoints under `/formulas`, tagged `formulas`. Registered in `main.py`.

### `GET /formulas`

List all formulas. Optional query filters:

| Query param | Effect                                                  |
|-------------|---------------------------------------------------------|
| `category`  | Filter by category string (e.g. `"mortality"`)          |
| `product`   | Filter by product applicability (e.g. `"FIUL"`)         |

**Response `200`:** `list[FormulaDefinition]` ordered by name.

### `POST /formulas`

Register a new formula.

**Request body:** `FormulaDefinition`

**One-formula-per-output rule:** If a formula already exists that produces the same `output_variable`, the request is rejected:

```json
{ "detail": "Formula for output 'lapse_rate' already exists." }
```

**Response `201`:** Created `FormulaDefinition` with dependencies populated.

**Response `409`:** Duplicate output variable.

### `GET /formulas/{formula_id}`

Get a single formula by UUID.

**Response `200`:** `FormulaDefinition` (includes `dependencies` list)  
**Response `404`:** Not found.

### `GET /formulas/{formula_id}/dependencies`

Returns the dependency graph for a single formula — the formula node and all its direct dependency nodes.

**Response `200`:** `DependencyGraphView`

```json
{
  "nodes": [ ...GraphNode ],
  "edges": [ ...GraphEdge ]
}
```

### `PUT /formulas/{formula_id}`

Partial update. Body is a plain JSON object with only the fields to change.

**Response `200`:** Updated `FormulaDefinition`  
**Response `404`:** Not found.

### `DELETE /formulas/{formula_id}`

Delete a formula. Its `formula_dependencies` rows cascade-delete automatically.

**Response `204`:** Deleted  
**Response `404`:** Not found.

**Note:** Deleting a formula does not delete its `output_variable` from `variable_registry`. Other formulas that depend on this formula's output continue to declare that dependency — they will show up as missing dependencies when `validate_formula_database()` is next run.

---

## Frontend Integration Guide — Phase 5

### Formula registry API

#### Create a formula

```
POST /formulas
Content-Type: application/json

{
  "id": "f-001",
  "name": "Calculate Net Cash Flow",
  "output_variable": "net_cash_flow",
  "function_ref": "calc_net_cash_flow",
  "dependencies": ["premium", "death_benefit", "lapse_rate"],
  "category": "cashflow",
  "product_applicability": ["FIUL"],
  "basis_applicability": [],
  "version": "v1",
  "status": "draft"
}
```

#### List formulas with filters

```
GET /formulas?category=mortality
GET /formulas?product=FIUL
```

#### Get dependency graph for one formula

```
GET /formulas/f-001/dependencies
```

Returns `DependencyGraphView` — use this to power a per-formula dependency diagram.

### `FormulaDefinition` shape

```ts
interface FormulaDefinition {
  id: string;
  name: string;
  output_variable: string;     // the variable this formula produces
  function_ref: string;        // key into the engine's function registry
  dependencies: string[];      // variable names this formula reads
  category: string;            // grouping label
  product_applicability: string[];
  basis_applicability: string[];
  version: string;
  status: "draft" | "active" | "deprecated";
  test_case_ids: string[];
}
```

### Dependency graph shapes

```ts
interface DependencyGraphView {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

interface GraphNode {
  id: string;       // variable name — use as node key in your graph renderer
  label: string;    // display text
  kind: "input" | "assumption" | "factor" | "formula" | "output";
  status: "valid" | "error" | "warning";  // drives node color
}

interface GraphEdge {
  id: string;        // UUID
  source: string;    // source node id (dependency)
  target: string;    // target node id (formula output)
  label?: string;
}
```

**Edge direction:** source → target means "source is needed by target". An arrow from `premium` to `net_cash_flow` means `net_cash_flow` depends on `premium`. Render arrows pointing from dependency to consumer.

### Dependency error shape

```ts
interface DependencyError {
  type: "circular_dependency" | "missing_dependency";
  variable_id: string;    // the variable at the centre of the problem
  message: string;        // human-readable — safe to display
  path: string[];         // variable name chain showing the problem
}
```

For a circular dependency, `path` is the full cycle: `["A", "B", "C", "A"]` — the first and last elements are the same variable.

For a missing dependency, `path` is `["formula_output", "missing_variable"]`.

### Constraints to enforce in the UI

- **One formula per output variable** — the backend enforces this with `409`, but the UI should check before submitting
- **`function_ref` must exist** in the engine's function registry — the validator reports this, but until the function is registered by a product module, the formula will fail validation. Show a warning on formulas in `draft` status
- **Updating dependencies** is not supported via `PUT` — if dependencies need to change, the formula must be deleted and re-created
- **`status: "active"` formulas** are the only ones the projection engine will execute — keep `draft` formulas out of production runs

### Execution order

The engine uses `get_execution_order()` internally before every run to determine the safe calculation sequence. The frontend does not need to call this, but knowing it exists is useful for debugging why a formula's value appears before another's.


---

---

# Phase 6 — Projection Engine (Core Loop)

**Status:** Complete  
**Files added:** `app/core/projection_engine/` (3 files), `app/core/output/` (2 files)  
**Files updated:** `app/core/entry_points.py` (`run_projection` added)

---

## Overview

Phase 6 is the execution layer — the part of the system that actually computes things. Given a `ProjectionRunDefinition`, the engine loads policies and formulas from the database, determines the safe formula execution order, and runs the full nested loop: policy × scenario × month × formula. Results are persisted to `run_outputs` after each policy. Errors are captured per-formula without stopping the rest of the run. The final run status (`success`, `partial_success`, `failed`) is written back to the `runs` table when the loop completes.

The engine has no HTTP dependency. It can be called from a script, a background task, or an API endpoint identically.

---

## Module Structure

```
app/
└── core/
    ├── projection_engine/
    │   ├── context.py    # builds ProjectionContext for each loop step
    │   ├── loop.py       # data loading helpers (policies, formulas)
    │   └── runner.py     # main execution loop
    └── output/
        ├── storage.py    # save/get run_outputs, update run status
        └── aggregate.py  # compute_summary, get_aggregates
```

---

## Context Builder — `core/projection_engine/context.py`

### `build_context(run_def, policy_data, policy_id, scenario_id, month) → ProjectionContext`

Constructs the `ProjectionContext` for a single step of the loop. Called once per (policy, scenario, month) combination before executing that step's formulas.

**Derived fields computed here:**

| Field            | Source                                                                   |
|------------------|--------------------------------------------------------------------------|
| `product`        | `policy_data["product_type"]`, empty string if absent                    |
| `duration`       | Set equal to `month` (policy duration = current projection month)        |
| `attained_age`   | `issue_age + floor((month - 1) / 12)` — issue age aged up by full years elapsed |
| `projection_month`| The `month` argument (1-based)                                          |
| `run_id`         | `run_def.id`                                                             |
| `project_id`     | `run_def.project_id`                                                     |
| `scenario_id`    | The `scenario_id` argument                                               |

`issue_age` is read from `policy_data` under the keys `"issue_age"` or `"age"` (whichever exists). If neither is present or the value is non-numeric, `attained_age` is left as `None`.

`assumption_set`, `factor_set`, `scenario_set`, and `projection_key` are set to `None` at this stage — these are populated in product-specific phases.

---

## Data Loaders — `core/projection_engine/loop.py`

### `load_policies(db, dataset_ids) → list[dict]`

Loads inforce records from `inforce_records`. Each element is:

```python
{"policy_id": "P001", "data": { ...full row dict... }}
```

If `dataset_ids` is an empty list, **all** inforce records are loaded. If `dataset_ids` is populated, only records from those `file_id` values are loaded.

### `load_formulas(db) → list[FormulaDefinition]`

Loads all formulas from `formula_registry` (no status filter applied — all formulas regardless of `draft`/`active`/`deprecated` are loaded). Filtering to `active` only is a planned improvement.

---

## Projection Runner — `core/projection_engine/runner.py`

### `run_projection(run_def, db, trace_logger=None) → ProjectionResultSet`

The main entry point. Executes the full projection and returns a `ProjectionResultSet`.

#### Startup sequence

1. If a `Run` row for `run_def.id` does not yet exist, one is created with `status="pending"`
2. Status is immediately updated to `"running"` and committed — visible to any status polling
3. Policies and formulas are loaded from DB
4. If no formulas exist, the run completes immediately with `status="success"` and empty results

#### Pre-loop: topological sort

Before the main loop starts, `topological_sort(formulas)` is called to get the execution order. If a circular dependency is detected here, the run fails immediately:

```python
status = "failed"
errors = [CalculationError(type="circular_dependency", message="Circular dependency: A → B → A")]
```

No loop iterations are executed.

#### Main loop structure

```
for policy in policies:
    for scenario_id in run_def.scenario_ids:
        for month in range(1, projection_length_months + 1):
            context = build_context(...)
            for var_name in formula_order:
                formula = formula_map.get(var_name)
                if formula is None:
                    continue            # var_name is an input, not a formula output — skip
                try:
                    resolve all dependencies
                    call formula function
                    save_output(...)
                    append CalculationResult(status="success")
                except Exception:
                    classify error
                    append CalculationResult(status="error")
    db.commit()   ← committed once per policy, not per formula
```

**Commit strategy:** `db.commit()` is called after finishing all scenarios and months for one policy. This means partial results are durable per-policy — if the run crashes halfway, completed policies' outputs are preserved.

`save_output` uses `db.flush()` (not commit) inside the loop — rows are staged in the session and committed together at the policy boundary.

#### Formula execution

For each formula in topological order:

1. All `formula.dependencies` are resolved by calling `resolver.resolve()` for each. If any dependency resolves with an `error_message`, a `ValueError` is raised immediately — the formula is not called.
2. The callable is retrieved from `FORMULA_FUNCTIONS` by `formula.function_ref`. If it doesn't exist, `ValueError` is raised.
3. The function is called with the resolved inputs as keyword arguments: `func(**resolved_inputs)`
4. The return value is passed to `save_output()`

#### Error handling

Every formula call is wrapped in a `try/except Exception`. On any exception:
- `_classify_error(exc)` determines the `CalculationErrorType` from the exception message
- A `CalculationError` is appended to `all_errors`
- A `CalculationResult(status="error")` is appended to `all_results`
- **Execution continues** to the next formula/policy/period

The run never halts due to a single formula failure.

#### Error classification — `_classify_error(exc)`

| Exception message contains  | `CalculationErrorType`  |
|-----------------------------|-------------------------|
| `"zero division"` / `"division by zero"` | `"division_by_zero"` |
| `"cannot resolve"` / `"could not be resolved"` | `"missing_value"` |
| `"not found"` | `"invalid_formula"` |
| `"circular"` | `"circular_dependency"` |
| anything else | `"invalid_formula"` |

#### Run status determination

After the loop completes:

| Condition                               | Final status       |
|-----------------------------------------|--------------------|
| `error_count == 0`                      | `"success"`        |
| All results have `status="error"`       | `"failed"`         |
| Some errors, some successes             | `"partial_success"`|

`update_run_status()` is called with the final status and `completed_at` is stamped.

---

## Output Storage — `core/output/storage.py`

### `save_output(db, run_id, policy_id, scenario_id, month, variable_name, value, product)`

Creates a `RunOutput` row. Values are stored wrapped:

```python
value={"value": value}   # if value is not already a dict
value=value              # if value is already a dict (e.g. table-type variables)
```

Uses `db.flush()` — does not commit. The caller commits at the policy boundary.

### `get_output(db, run_id, policy_id, scenario_id, month, variable_name) → Any`

Point lookup for a single stored value. Unwraps the `{"value": ...}` envelope on read. Returns `None` if not found.

### `get_policy_outputs(db, run_id, policy_id) → list[dict]`

Returns all outputs for one policy across all scenarios and months, ordered by `projection_month` then `variable_name`. Each element:

```python
{
    "scenario_id": "base",
    "month": 3,
    "variable": "net_cash_flow",
    "value": 1234.56
}
```

### `get_run_results(db, run_id) → list[dict]`

Returns all outputs for the entire run, ordered by `policy_id`, `scenario_id`, `projection_month`. Same element shape as above but also includes `policy_id`.

### `update_run_status(db, run_id, status)`

Updates `runs.status`. If the status is terminal (`success`, `partial_success`, `failed`), also stamps `completed_at` with the current UTC time. Uses `db.flush()`.

---

## Aggregation — `core/output/aggregate.py`

### `compute_summary(db, run_id, error_count=0) → ProjectionSummary`

Scans all `RunOutput` rows for the run and computes:

| Summary field                | How computed                                       |
|------------------------------|----------------------------------------------------|
| `policy_count`               | Distinct `policy_id` values                        |
| `period_count`               | Maximum `projection_month` value                   |
| `scenario_count`             | Distinct `scenario_id` values                      |
| `calculated_variable_count`  | Total row count (all outputs across all dimensions)|
| `error_count`                | Passed in from the runner (not re-derived)         |
| `warning_count`              | Always `0` at this phase                           |

### `get_aggregates(db, run_id, variable_name=None, period=None) → dict`

Simple numeric aggregation over `run_outputs`. Filters by `variable_name` and/or `period` if provided. Non-numeric values are skipped silently.

Returns:

```python
{"count": 120, "sum": 45678.90, "avg": 380.66}
```

This is Phase 1 of the aggregation framework. Cross-scenario aggregation, percentile bands, and time-series rollups are planned for a later phase.

---

## Non-HTTP Entry Point — `core/entry_points.py` (updated)

### `run_projection(run_def: ProjectionRunDefinition) → ProjectionResultSet`

Opens a session, calls `runner.run_projection()`, closes the session. Fully self-contained — no FastAPI required.

Usage from a script:

```python
from app.core.entry_points import run_projection
from app.models.schemas import ProjectionRunDefinition

result = run_projection(ProjectionRunDefinition(
    id="run-001",
    name="Base Case Q4",
    project_id="proj-001",
    formula_database_id="db-001",
    dataset_ids=["file-001"],
    scenario_ids=["base", "stress"],
    projection_length_months=12,
    selected_output_variables=["net_cash_flow", "account_value"],
    debug_mode=False,
))

print(result.status)
print(f"{result.summary.policy_count} policies, {result.summary.error_count} errors")
```

---

## Frontend Integration Guide — Phase 6

### Triggering a run

There is no dedicated `POST /runs` endpoint yet — runs are triggered via the non-HTTP entry point or through a run management endpoint added in a later phase. The run status can be observed by querying the `runs` table or through the run status polling endpoint when it is available.

### `ProjectionRunDefinition` — what to send

```ts
interface ProjectionRunDefinition {
  id: string;                         // UUID you generate
  name: string;
  project_id: string;
  formula_database_id: string;        // not yet fully wired — use any string
  dataset_ids: string[];              // inforce_files.id values to include
  scenario_ids: string[];             // scenario names to run (e.g. ["base", "stress_1"])
  projection_length_months: number;   // how many months to project
  selected_output_variables: string[];// variable names to include in output
  debug_mode: boolean;                // true = trace logs written
}
```

### `ProjectionResultSet` — what comes back

```ts
interface ProjectionResultSet {
  run_id: string;
  scenario_id: string;          // comma-joined list of all scenario IDs
  started_at: string;           // ISO datetime
  completed_at: string | null;
  status: "success" | "partial_success" | "failed";
  results: CalculationResult[];
  errors: CalculationError[];
  summary: ProjectionSummary;
}
```

### `ProjectionSummary`

```ts
interface ProjectionSummary {
  policy_count: number;
  period_count: number;               // highest month reached
  scenario_count: number;
  calculated_variable_count: number;  // total output rows written
  error_count: number;
  warning_count: number;
}
```

### Run status flow

```
created → "pending" → "running" → "success" | "partial_success" | "failed"
```

A run stays `"running"` until `run_projection()` returns. If the process is killed mid-run, the status remains `"running"` indefinitely — there is no automatic timeout or recovery at this phase.

### Reading results after a run

Use the storage helpers (exposed via API endpoints in a later phase):

- `GET /runs/{run_id}/outputs` — all outputs for the run
- `GET /runs/{run_id}/outputs?policy_id=P001` — one policy's outputs
- `GET /runs/{run_id}/summary` — `ProjectionSummary`

These endpoints are not yet live. For now, results are queryable directly from `run_outputs` or via `get_run_results()` / `get_policy_outputs()` from Python.

### Error handling on the frontend

When `status === "partial_success"` or `status === "failed"`, the `errors` array contains `CalculationError` objects:

```ts
interface CalculationError {
  type: "division_by_zero" | "missing_value" | "invalid_formula" | "circular_dependency" | ...;
  message: string;       // human-readable — safe to display
  variable_id: string;   // which variable failed
  formula_id: string;    // which formula was executing
  policy_id: string;     // which policy was being processed
  period: number;        // which month failed
  scenario_id: string;
  dependency_path: string[];
}
```

Each error is scoped to a specific (policy, scenario, month, variable) combination — a failure for one policy does not affect other policies.

### Determinism

The engine is deterministic: given the same `ProjectionRunDefinition`, the same policies in the database, and the same formula functions, it produces identical output on every run. Formula execution order is fixed by topological sort, and the policy loop order follows the DB query order (by insertion order / `id`).
