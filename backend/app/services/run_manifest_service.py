"""Final run manifests: configuration identity plus execution outcome, written exactly once.

Two fingerprints, two meanings:

- ``run_package_fingerprint`` (runs, run_packages): hash of the frozen *configuration* only.
  Identical configurations on the same build have identical package fingerprints.
- ``final_manifest_fingerprint`` (runs, run_manifests.fingerprint): hash of the complete final
  manifest below — configuration identity *and* runtime outcome (run ID, attempt, timestamps,
  user, status, warnings, errors). It is computed once, from the finished document, when the
  run reaches a terminal status; the document is never modified afterwards (write-once table).
"""

from typing import Any

from sqlalchemy.orm import Session

from app.core.execution.fingerprints import fingerprint
from app.db.models.run import Run
from app.db.models.run_artifact import RunManifest
from app.db.models.run_package import RunAttempt, RunPackage
from app.services.build_info import build_identity, runtime_environment
from app.services.common import iso, user_ref

MANIFEST_SCHEMA_VERSION = "mentoramp.run_manifest/v1"
MAX_MESSAGES = 50


def _configuration_identity(package: RunPackage | None) -> dict[str, Any]:
    if package is None:
        return {"run_package_id": None, "run_package_fingerprint": None}
    document = package.package
    config = document["configuration"]
    datasets = config["datasets"]
    return {
        "run_package_id": package.id,
        "run_package_fingerprint": package.fingerprint,
        "fingerprint_algorithm": document["fingerprint_algorithm"],
        "package_schema_version": document["schema_version"],
        "project_id": config["project"]["id"],
        "model": config["model"],
        "model_version": config["model_version"],
        "projection_set": config["projection_set"],
        "valuation_date": config["valuation_date"],
        "horizon_months": config["horizon_months"],
        "time_step": config["time_step"],
        "execution_backend": config["execution_backend"],
        "parameters": config["parameters"],
        "formulas": [
            {"id": f["id"], "output_variable": f["output_variable"], "version": f["version"],
             "function_ref": f["function_ref"], "content_fingerprint": f["content_fingerprint"]}
            for f in config["formulas"]
        ],
        "variables": [
            {"name": v["name"], "version": v["version"], "content_fingerprint": v["content_fingerprint"]}
            for v in config["variables"]
        ],
        "datasets": {
            section: [
                {"id": d["id"], "name": d["name"], "version_label": d.get("version_label"),
                 "fingerprint": d["fingerprint"]}
                for d in datasets.get(section) or []
            ]
            for section in ("inforce", "assumption_tables", "factor_tables")
        },
        "scenario": {
            key: config["scenario"].get(key)
            for key in ("id", "name", "version_label", "scenario_type", "fingerprint")
        },
        "outputs": config["outputs"],
        "submitted_build": config["build"],
        "illustrative": bool(config["model_version"]["illustrative"]),
    }


def build_final_manifest(
    db: Session,
    run: Run,
    attempt: RunAttempt | None,
    package: RunPackage | None,
    warnings: list[str],
    errors: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "run": {
            "id": run.id,
            "name": run.name,
            "project_id": run.project_id,
            "run_set_id": run.run_set_id,
        },
        "configuration": _configuration_identity(package),
        "runtime": {
            "status": run.status,
            "submitted_at": iso(run.created_at),
            "started_at": iso(run.started_at),
            "completed_at": iso(run.completed_at),
            "triggered_by": user_ref(db, run.triggered_by),
            "attempt_count": run.attempt_count,
            "attempt": (
                {
                    "number": attempt.attempt_number,
                    "status": attempt.status,
                    "worker_id": attempt.worker_id,
                    "started_at": iso(attempt.started_at),
                    "completed_at": iso(attempt.completed_at),
                    "error_type": attempt.error_type,
                    "output_row_count": attempt.output_row_count,
                    "trace_row_count": attempt.trace_row_count,
                }
                if attempt is not None else None
            ),
            "accepted_attempt_number": run.accepted_attempt_number,
            "executed_build": build_identity(),
            "environment": runtime_environment(),
            "warning_count": run.warning_count or 0,
            "error_count": run.error_count or 0,
            "warnings": warnings[:MAX_MESSAGES],
            "errors": errors[:MAX_MESSAGES],
            "reconciliation_status": "not_available",  # expected-vs-actual arrives in M3
            "approval_state": "not_available",        # run approval arrives with governance
        },
    }


def write_final_manifest(
    db: Session,
    run: Run,
    attempt: RunAttempt | None,
    package: RunPackage | None,
    warnings: list[str],
    errors: list[str],
) -> RunManifest:
    """Create the run's only final manifest (caller commits in the same transaction as the status)."""
    document = build_final_manifest(db, run, attempt, package, warnings, errors)
    final_fingerprint = fingerprint(document)
    record = RunManifest(
        run_id=run.id,
        fingerprint=final_fingerprint,
        manifest=document,
        schema_version=MANIFEST_SCHEMA_VERSION,
        run_package_id=package.id if package else None,
        run_package_fingerprint=package.fingerprint if package else None,
        attempt_number=attempt.attempt_number if attempt else None,
    )
    db.add(record)
    run.final_manifest_fingerprint = final_fingerprint
    return record
