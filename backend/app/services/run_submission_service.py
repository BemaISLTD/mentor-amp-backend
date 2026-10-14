"""Submitting Run Sets: authorize, resolve, freeze one run package per run, persist atomically.

Every run gets its own immutable package before the request returns. If any run of the Run Set
cannot be frozen (wrong project, unvalidated input, unpinned table, invalid scenario...), nothing
is created.
"""

import uuid
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.core import lifecycle
from app.core.execution import run_state
from app.db.models.projection import ProjectionSet, RunSet
from app.db.models.run import Run
from app.db.models.run_package import RunPackage
from app.services import access
from app.services import run_package_service as packages
from app.services.common import ServiceError, conflict, iso, not_found, now_utc
from app.services.run_events import add_event


def _plan(db: Session, user: Any, payload: dict, write: bool):
    """Authorize the project and resolve (Projection Set, scenario IDs) pairs for the request."""
    project = access.require_project_access(db, user, payload["project_id"], write=write)
    projection_set_ids: list[str] = list(payload.get("projection_set_ids") or [])
    if not projection_set_ids:
        raise ServiceError(422, "VALIDATION_ERROR", "Select at least one Projection Set.")
    requested_scenarios: list[str] = list(dict.fromkeys(payload.get("scenario_ids") or []))
    plan: list[tuple[ProjectionSet, list[str], str]] = []
    for projection_set_id in dict.fromkeys(projection_set_ids):
        projection_set = db.get(ProjectionSet, projection_set_id)
        if projection_set is None or projection_set.project_id != project.id:
            raise not_found(f"Projection Set '{projection_set_id}' not found in this project.")
        # A request-level scenario list replaces the Projection Set's own list; each scenario is
        # checked in full when its package is frozen (project, status, fingerprint, overrides).
        if requested_scenarios:
            plan.append((projection_set, requested_scenarios, "request"))
        else:
            plan.append((projection_set, list(dict.fromkeys(projection_set.scenario_ids or [])), "projection_set"))
    return project, plan


def preflight(db: Session, payload: dict, user: Any) -> dict[str, Any]:
    """Dry run of submission: freeze every planned package without saving anything."""
    project, plan = _plan(db, user, payload, write=True)
    planned = []
    problems: list[dict[str, Any]] = []
    for projection_set, scenario_ids, _source in plan:
        if not scenario_ids:
            problems.append({"code": "NO_SCENARIOS", "message": f"'{projection_set.name}' has no scenarios."})
        for scenario_id in scenario_ids:
            configuration, _governance, found = packages.collect(db, project, projection_set, scenario_id)
            item = {
                "projection_set_id": projection_set.id,
                "scenario_id": scenario_id,
                "name": _run_name(projection_set, configuration, scenario_id),
                "ok": not found,
                "problems": [problem.as_dict() for problem in found],
            }
            planned.append(item)
            problems += item["problems"]
    codes = {problem["code"] for problem in problems}
    checks = [
        _check("projection_sets_validated", "Projection compatibility",
               not codes & {"PROJECTION_SET_NOT_VALIDATED", "MODEL_VERSION_NOT_RUNNABLE"},
               f"{len(plan)} Projection Set(s)"),
        _check("inputs_pinned", "Input versions pinned and fingerprinted",
               not codes & {"INPUT_NOT_IN_PROJECT", "INPUT_NOT_RUNNABLE", "INPUT_NOT_FINGERPRINTED",
                            "INFORCE_NOT_SELECTED", "TABLE_NOT_PINNED", "TABLE_AMBIGUOUS", "INPUT_EMPTY"},
               "Every inforce file and table pinned by ID"),
        _check("formulas_ready", "Formula lineage captured",
               not codes & {"FUNCTION_NOT_REGISTERED", "FORMULA_NOT_RUNNABLE", "NO_FORMULAS",
                            "FORMULA_GRAPH_INVALID", "VARIABLE_NOT_DEFINED"},
               "Formulas registered; graph valid"),
        _check("scenarios_valid", "Scenarios valid",
               bool(planned) and not {code for code in codes if code.startswith("SCENARIO") or code == "NO_SCENARIOS"},
               f"{len(planned)} scenario run(s)"),
        # Reports are not implemented yet: "deferred" is neither a pass nor a failure, and it
        # does not affect "ok" (a capability that does not exist is never reported as passing).
        {"code": "reports_attached", "label": "Report attachment", "status": DEFERRED,
         "message": "Report attachment is not available yet (deferred to a later milestone)."},
    ]
    return {
        "ok": bool(planned) and not problems
        and all(check["status"] in ("pass", DEFERRED) for check in checks),
        "checks": checks,
        "planned_runs": planned,
        "problems": problems,
    }


DEFERRED = "deferred"


def _check(code: str, label: str, passed: bool, message: str) -> dict[str, str]:
    return {"code": code, "label": label, "status": "pass" if passed else "fail", "message": message}


def _run_name(projection_set: ProjectionSet, configuration: dict | None, scenario_id: str) -> str:
    scenario_name = (configuration or {}).get("scenario", {}).get("name") or scenario_id
    return f"{projection_set.name} · {scenario_name}"


def submit_run_set(db: Session, payload: dict, user: Any) -> dict[str, Any]:
    project, plan = _plan(db, user, payload, write=True)
    for projection_set, scenario_ids, _source in plan:
        if projection_set.status not in lifecycle.RUNNABLE_PROJECTION_SET_STATUSES:
            raise conflict(
                f"Projection Set '{projection_set.name}' is not validated "
                f"(status: {projection_set.status}). Validate it first.",
                reason="PROJECTION_SET_NOT_VALIDATED",
                projection_set_id=projection_set.id,
            )
        if not scenario_ids:
            raise ServiceError(400, "BAD_REQUEST", f"Projection Set '{projection_set.name}' has no scenarios to run.")

    submitted_at = now_utc()
    run_set_id = str(uuid.uuid4())
    frozen: list[tuple[ProjectionSet, str, packages.FrozenPackage]] = []
    for projection_set, scenario_ids, _source in plan:
        for scenario_id in scenario_ids:
            run_id = str(uuid.uuid4())
            package = packages.freeze(
                db, project, projection_set, scenario_id, user, run_id=run_id, run_set_id=run_set_id,
            )
            frozen.append((projection_set, run_id, package))

    run_set = RunSet(
        id=run_set_id,
        project_id=project.id,
        name=payload.get("name") or "Run Set",
        notes=payload.get("notes") or None,
        projection_set_ids=[projection_set.id for projection_set, _ids, _source in plan],
        # The scenarios actually submitted, first-appearance order across Projection Sets.
        scenario_ids=list(dict.fromkeys(scenario_id for _ps, ids, _source in plan for scenario_id in ids)),
        resolution={
            "requested_scenario_ids": list(dict.fromkeys(payload.get("scenario_ids") or [])),
            "projection_sets": [
                {
                    "projection_set_id": projection_set.id,
                    "scenario_source": source,
                    "scenario_ids": list(ids),
                    "runs": [
                        {"run_id": run_id, "scenario_id": package.configuration["scenario"]["id"]}
                        for ps, run_id, package in frozen if ps.id == projection_set.id
                    ],
                }
                for projection_set, ids, source in plan
            ],
        },
        status=run_state.RUN_SET_QUEUED,
        created_by=user.id,
        submitted_at=submitted_at,
    )
    db.add(run_set)
    db.flush()

    created: list[tuple[Run, packages.FrozenPackage]] = []
    for projection_set, run_id, package in frozen:
        configuration = package.configuration
        run = Run(
            id=run_id,
            project_id=project.id,
            name=_run_name(projection_set, configuration, configuration["scenario"]["id"]),
            projection_key=f"{projection_set.name}@{projection_set.version_label}",
            status=run_state.PENDING,
            run_set_id=run_set.id,
            projection_set_id=projection_set.id,
            model_version_id=configuration["model_version"]["id"],
            scenario_id=configuration["scenario"]["id"],
            valuation_date=date.fromisoformat(configuration["valuation_date"]),
            horizon_months=configuration["horizon_months"],
            illustrative=bool(configuration["model_version"]["illustrative"]),
            triggered_by=user.id,
            run_package_fingerprint=package.fingerprint,
            attempt_count=0,
        )
        db.add(run)
        db.flush()
        document = package.document
        db.add(RunPackage(
            id=document["identity"]["package_id"],
            run_id=run.id,
            project_id=project.id,
            schema_version=document["schema_version"],
            fingerprint_algorithm=document["fingerprint_algorithm"],
            fingerprint=package.fingerprint,
            package=document,
            created_by=user.id,
        ))
        add_event(db, run.id, "queued", f"Run queued with run package {package.fingerprint[:12]}….",
                  data={"run_package_fingerprint": package.fingerprint})
        created.append((run, package))
    db.commit()

    return {
        "run_set": {
            "id": run_set.id,
            "name": run_set.name,
            "status": run_set.status,
            "run_count": len(created),
            "submitted_at": iso(submitted_at),
        },
        "runs": [
            {
                "id": run.id,
                "name": run.name,
                "status": run.status,
                "scenario": {"id": run.scenario_id, "name": package.configuration["scenario"]["name"]},
                "projection_set": {
                    "id": run.projection_set_id,
                    "name": package.configuration["projection_set"]["name"],
                },
                "run_package_id": package.document["identity"]["package_id"],
                "run_package_fingerprint": package.fingerprint,
            }
            for run, package in created
        ],
    }
