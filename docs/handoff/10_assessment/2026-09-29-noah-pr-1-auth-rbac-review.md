# Noah PR #1 Review — Authentication and RBAC

**Review date:** 2026-09-29  
**Repository:** `BemaISLTD/mentor-amp-backend`  
**Target:** `main` at `51d3028`  
**Reviewed head:** `ccab9a6` (`feat/api-auth-rbac`)  
**Decision:** **Request changes — do not merge yet**  
**Update:** Noah pushed fixes; head is now `25b6e54`. See the addendum at the end. Re-review by execution is pending.

## Addendum — 2026-09-29 evening: Noah's fix commits (static read only)

Noah pushed three commits to `feat/api-auth-rbac` around 12:24:
`57e9577` "fix: enforce authorization and validation contracts",
`cd40df7` "fix: wait for database readiness", and
`25b6e54` "chore: align authorization status". PR #1 now reports
mergeable/clean with **0 CI checks** and no GitHub review.

| Finding | Change in `git diff ccab9a6 origin/feat/api-auth-rbac` | Static status |
|---|---|---|
| 1. `projects:write` not enforced | `require_permissions()` added to `backend/app/api/dependencies.py`; `projects:read` on list/get, `projects:write` on create/patch; new test `test_actuary_can_read_projects_but_cannot_mutate_them` | Appears fixed |
| 2. Hard delete cascades governed data | `DELETE /projects/{id}` removed; new test `test_project_delete_is_not_exposed` | Appears fixed |
| 3. Validation errors return 500 | `jsonable_encoder(exc.errors())` re-applied (same content as `d673db3`, not cherry-picked); tests for invalid bootstrap email, invalid user email, empty roles list | Appears fixed |
| 4. Compose races PostgreSQL | `pg_isready` healthcheck and `depends_on: condition: service_healthy` | Appears fixed; clean-volume startup not yet executed |
| Scope/status over-claim | `docs/phase_1_backlog.md` now marks RBAC and projects "In progress"; five-role matrix pending | Fixed |

**Not yet done:** none of the re-review checklist below has been executed
against the new head. Noah reports `12 passed`; that is his claim until
reproduced. Before approving, run the checklist on a clean PostgreSQL volume.

**Related risk (see BLK-016):** `origin/dev` already contains the original
`ccab9a6` but **not** these three fixes. On `dev`, the hard delete endpoint,
the missing permission checks, and the missing DB health check are all still
present. Approving PR #1 does not fix `dev`.

## What the pull request adds

The pull request adds JWT login, first-admin bootstrap, users, roles,
permissions, API versioning, a shared error envelope, project update/delete,
Docker Compose startup, one RBAC migration, and seven integration tests.

The basic structure is useful, the submitted tests pass, and the PostgreSQL
migration can be applied, rolled back, and reapplied. Four defects must be
fixed before merge because they affect authorization, governed-data deletion,
the public error contract, and the documented startup command.

## Merge-blocking findings

### 1. High — the `projects:write` permission is stored but never enforced

`initialize_builtin_roles()` deliberately omits `projects:write` from the
actuary role. The projects router only requires a logged-in user, however, and
the authorization dependency checks role names rather than permissions. This
lets an actuary create, update, and permanently delete projects.

Reproduced against the PR's migrated PostgreSQL stack:

```text
actuary_project_create 201
actuary_project_delete 204
```

Example: Mary is assigned only the `actuary` role. The database says she lacks
`projects:write`, but `DELETE /v1/projects/{id}` still succeeds.

Required fix:

1. Add a `require_permissions(...)` dependency that derives permissions from
   the current user's roles.
2. Require `projects:read` on list/get.
3. Require `projects:write` on create/update.
4. Do not expose project deletion until finding 2 is resolved.
5. Add tests proving an actuary can read projects but receives `403` for every
   disallowed mutation.

Relevant code:

- `backend/app/api/auth.py`, lines 17–23 and 57–62
- `backend/app/api/dependencies.py`, lines 41–53
- `backend/app/main.py`, lines 34–38
- `backend/app/api/projects.py`, lines 26–73

### 2. High — project deletion can cascade governed data without audit or recovery

The new delete endpoint calls `db.delete(project)` and commits. Existing
foreign keys cascade project deletion into in-force files, assumption sets,
factor sets, scenarios, runs, outputs, and traces. The same pull request says
audit logs and soft deletion are still open.

Example: deleting one project can remove its run and trace evidence, leaving no
immutable deletion event and no supported recovery path. This conflicts with
LR1-SEC-007, LR1-SEC-008, and the target Project lifecycle of active/archived.

Required fix: remove the endpoint for now, or replace it with an authorized
archive/soft-delete workflow that records an audit event and follows an agreed
retention rule. Define a separate admin permission if permanent deletion is
eventually required.

Relevant code:

- `backend/app/api/projects.py`, lines 66–73
- `backend/app/db/migrations/versions/bfc45372c779_create_all_phase1_tables.py`
- `docs/handoff/01_requirements/master-requirement-tracker.md`, LR1-SEC-007 and LR1-SEC-008

### 3. High — custom validation errors become HTTP 500 responses

The validation handler passes `exc.errors()` directly to `JSONResponse`.
Pydantic includes the original `ValueError` object in `ctx` for custom field
validators, and that exception object is not JSON serializable.

Reproduced against both SQLite and the migrated PostgreSQL/API container:

```text
POST /v1/auth/bootstrap with email "not-an-email"
expected: 422 VALIDATION_ERROR
actual:   500 INTERNAL_SERVER_ERROR
```

Noah has already authored the focused fix in later commit `d673db3`
(`jsonable_encoder(exc.errors())`), but that commit is not part of PR #1.
Bring that fix into the PR and add regression tests for invalid bootstrap email,
invalid user email, and an empty roles list.

Relevant code:

- `backend/app/api/errors.py`, lines 48–60
- `backend/app/models/auth.py`, lines 14–20, 22–28, and 36–42

### 4. High — a clean `docker compose up --build -d` can leave the API stopped

Compose starts the API after starting the database container, but it does not
wait until PostgreSQL is ready. On a clean test volume, Alembic immediately
received `connection refused`; the API exited with status 1 and remained down.
Manually starting the API after PostgreSQL became ready succeeded, which proves
the migration SQL is not the cause.

Required fix: add a PostgreSQL `pg_isready` health check and make the API depend
on `service_healthy`. A bounded connection/migration retry is also useful.
Add a clean-volume startup check that verifies `/health` returns 200.

Relevant code:

- `docker-compose.yml`, lines 16–29, especially the short-form `depends_on` at
  lines 23–24
- `README.md`, lines 18–29

## Scope/status correction

The PR backlog marks authentication/RBAC and the error contract as complete.
They are not complete while findings 1 and 3 remain. The consolidated
requirement LR1-SEC-003 also requires role separation for model developer,
actuary, reviewer, administrator, and read-only users; this PR defines only
`admin` and `actuary`.

Keep the work **In progress** until the five-role authorization model is agreed,
implemented, and covered by allowed/denied-operation tests. If this PR is meant
only as the first increment, change its backlog wording to say that clearly.

## Verification performed

- Reviewed the exact Git pull-request head and merge ref without changing the
  working branch.
- Installed the PR's declared dependencies in an isolated review copy.
- Ran the submitted test suite locally: **7 passed, 1 deprecation warning**.
- Ran Python compilation: passed.
- Confirmed one Alembic head: `2f6d51e920a4`.
- Built the Docker image and tested a clean PostgreSQL volume.
- Confirmed the initial Compose startup failure described in finding 4.
- After manually restarting the API, confirmed all five RBAC tables and the
  migration head in PostgreSQL.
- Ran the migration downgrade/re-upgrade cycle successfully.
- Ran the submitted suite inside Docker after manual restart: **7 passed,
  1 deprecation warning**.
- Reproduced findings 1 and 3 through the running PostgreSQL-backed API.
- Removed all temporary containers, network, and review database volume.

PostgreSQL migration result after manual restart:

```text
alembic_version = 2f6d51e920a4
tables = permissions, role_permissions, roles, user_roles, users
```

## Minimum re-review checklist

- [ ] Actuary receives `403` for project create/update/delete.
- [ ] Project delete is removed or replaced by approved audited lifecycle behavior.
- [ ] Invalid custom-validator input returns the documented 422 envelope.
- [ ] A clean `docker compose up --build -d` reaches a healthy API without manual restart.
- [ ] New negative tests cover the three behaviors above.
- [ ] Backlog completion claims match the implemented role scope.
- [ ] Existing seven tests still pass.
- [ ] PostgreSQL migration upgrade and rollback still pass.

## Suggested review message to Noah

> Please update PR #1 before merge. An actuary currently has project write and
> hard-delete access even though the role lacks `projects:write`; invalid email
> validation returns 500 instead of 422; and clean Docker startup races
> PostgreSQL and leaves the API stopped. Please enforce permissions at the
> endpoint level, remove or redesign hard project deletion with audit/retention,
> bring validation fix `d673db3` into this branch with negative tests, and add a
> database health check/startup gate. The RBAC migration itself passed clean
> PostgreSQL upgrade, rollback, and re-upgrade testing.
