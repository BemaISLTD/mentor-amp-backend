"""Generate isolated Phase 7 execution and governance evidence.

This runner always creates a temporary SQLite database and a caller-selected
evidence directory. It never reads or migrates the configured application DB.
Run from ``backend`` with::

    python -m scripts.review_phase7 --output ../.phase7-review
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import math
from pathlib import Path
import tempfile
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db.models  # noqa: F401 - register the complete schema
from app.core import artifacts
from app.core.artifacts import LocalParquetArtifactStore, verified_artifact_bytes
from app.db.database import Base
from app.db.models.audit_log import AuditLog
from app.db.models.governance import Report
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact
from app.db.models.run_package import RunPackage
from app.services import (
    governance_service,
    reconciliation_service,
    reporting_service,
    results_service,
    run_execution_service,
    run_submission_service,
    trace_service,
)
from scripts.seed_phase7 import seed_phase7


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


def _run_evidence(db, store, run: Run, code: str, policy_id: str, variable: str) -> dict:
    if run.status != "success" or run.error_count:
        raise RuntimeError(f"{code} run {run.id} failed: {run.status} {run.summary}")
    package = db.query(RunPackage).filter_by(run_id=run.id).one()
    if len(package.fingerprint) != 64:
        raise RuntimeError("RunPackage fingerprint is not SHA-256.")
    artifact_rows = db.query(RunArtifact).filter_by(
        run_id=run.id, attempt_number=run.accepted_attempt_number
    ).all()
    if {row.artifact_type for row in artifact_rows} < {"outputs", "traces"}:
        raise RuntimeError("Canonical outputs and traces were not both persisted.")
    artifacts_out = []
    for row in artifact_rows:
        content = verified_artifact_bytes(store, row.storage_uri, row.checksum_sha256)
        artifacts_out.append({
            "type": row.artifact_type,
            "schema_version": row.schema_version,
            "row_count": row.row_count,
            "sha256": hashlib.sha256(content).hexdigest(),
        })
    trace = trace_service.trace_tree(db, run.id, policy_id, 1, variable, depth=6)
    return {
        "run_id": run.id,
        "status": run.status,
        "run_package_fingerprint": package.fingerprint,
        "run_package_schema": package.schema_version,
        "engine_version": (
            (package.package.get("configuration") or {}).get("build") or {}
        ).get("engine_version"),
        "artifacts": artifacts_out,
        "trace_root": trace["root"],
    }


def generate(output: str | Path) -> dict:
    destination = Path(output).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    store = LocalParquetArtifactStore(destination / "artifacts")
    with tempfile.TemporaryDirectory(prefix="mentoramp-phase7-") as temporary:
        engine = create_engine(f"sqlite:///{Path(temporary) / 'phase7-review.db'}")
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        Base.metadata.create_all(engine)
        try:
            with ExitStack() as stack:
                stack.enter_context(patch.object(artifacts, "get_artifact_store", return_value=store))
                stack.enter_context(patch.object(run_execution_service, "get_artifact_store", return_value=store))
                stack.enter_context(patch.object(reporting_service, "get_artifact_store", return_value=store))
                stack.enter_context(patch.object(run_execution_service, "SessionLocal", Session))
                with Session() as db:
                    seeded = seed_phase7(db)
                    submissions = {}
                    for code in ("IDI", "VA", "PRT"):
                        item = seeded[code]
                        submissions[code] = run_submission_service.submit_run_set(db, {
                            "project_id": item["project"].id,
                            "projection_set_ids": [item["projection_set"].id],
                            "name": f"{code} Phase 7 isolated review",
                        }, item["user"])
                for submitted in submissions.values():
                    run_execution_service.execute_run_set(submitted["run_set"]["id"])

                evidence: dict[str, object] = {"classification": "SYNTHETIC_ENGINEERING_EVIDENCE"}
                trace_targets = {
                    "IDI": ("IDI-A-10001", "reserve"),
                    "VA": ("VA-GMDB-10001", "account_value_close"),
                    "PRT": ("PRT-LIFE-10001", "expected_benefit_payment"),
                }
                with Session() as db:
                    for code, submitted in submissions.items():
                        policy_id, variable = trace_targets[code]
                        code_runs = []
                        for submitted_run in submitted["runs"]:
                            run = db.get(Run, submitted_run["id"])
                            code_runs.append(_run_evidence(db, store, run, code, policy_id, variable))
                        evidence[code] = code_runs

                    # Independent PRT month-1 arithmetic: annual mortality is 2%,
                    # so EOM survival is 0.98**(1/12) and the life-only expected
                    # benefit is that probability times the $1,000 schedule.
                    prt_base = next(
                        db.get(Run, item["id"])
                        for item in submissions["PRT"]["runs"]
                        if item["scenario"]["name"] == "Base"
                    )
                    prt_aggregate = results_service.aggregates(
                        db,
                        prt_base.id,
                        group_by="projection_year",
                        variables="expected_benefit_payment,total_prt_outflow,pv_benefit_payment",
                    )
                    if not prt_aggregate["rows"]:
                        raise RuntimeError("PRT block aggregation produced no projection-year rows.")
                    independent = 1000.0 * math.pow(0.98, 1.0 / 12.0)
                    project = seeded["PRT"]["project"]
                    actor = seeded["PRT"]["user"]
                    statuses = []
                    for expected_value in (independent, independent + 1.0):
                        expected = reconciliation_service.create_expected_set(db, project.id, {
                            "name": f"PRT independent month-1 anchor {expected_value:.6f}",
                            "product_code": "PRT",
                            "source_reference": "Phase 7 hand calculation: 1000 * 0.98 ** (1/12)",
                            "timing_convention": "EOM",
                            "rows": [{
                                "policy_id": "PRT-LIFE-10001", "projection_month": 1,
                                "expected_benefit_payment": expected_value,
                            }],
                        }, actor.id)
                        result = reconciliation_service.reconcile(db, prt_base.id, {
                            "expected_result_set_id": expected["id"],
                            "policy_id": "PRT-LIFE-10001",
                            "variables": ["expected_benefit_payment"],
                            "abs_tol": 1e-6, "rel_tol": 0.0,
                        }, actor.id)
                        statuses.append(result["status"])
                    if statuses != ["PASS", "FAIL"]:
                        raise RuntimeError(f"Unexpected reconciliation outcomes: {statuses}")
                    evidence["reconciliation"] = {
                        "formula": "1000 * 0.98 ** (1/12)",
                        "independent_expected": independent,
                        "outcomes": statuses,
                    }
                    evidence["prt_block_aggregation"] = {
                        "participant_count": 4,
                        "projection_year_rows": prt_aggregate["rows"],
                    }

                    # Generate, read, export, publish and version an immutable
                    # report strictly from the accepted PRT run artifacts.
                    report_data = governance_service.create_report(db, project.id, {
                        "name": "Phase 7 disclosure-supporting evidence",
                        "report_type": "disclosure_supporting",
                        "definition": {"source_run_id": prt_base.id},
                    }, actor)
                    report_id = report_data["id"]
                    generated = reporting_service.generate(db, project.id, report_id, actor)
                    reread = reporting_service.payload(db, report_id)
                    csv_bytes, _ = reporting_service.export_csv(db, report_id, actor)
                    if generated != reread or not csv_bytes.startswith(b"section,line_item"):
                        raise RuntimeError("Report payload or export verification failed.")
                    governance_service.publish_versioned(db, Report, report_id, actor)
                    version = governance_service.version_versioned(db, Report, report_id, actor)
                    actions = [row.action for row in db.query(AuditLog).filter(
                        AuditLog.entity_id == report_id
                    ).order_by(AuditLog.created_at).all()]
                    evidence["report"] = {
                        "report_id": report_id,
                        "new_version_id": version["id"],
                        "schema_version": generated["schema_version"],
                        "classification": generated["metadata"]["classification"],
                        "is_production_approved": generated["metadata"]["is_production_approved"],
                        "audit_actions": actions,
                    }
                    evidence["life_readiness"] = {
                        "version_ids": [row.id for row in seeded["LIFE"]["versions"]],
                        "version_numbers": [row.version_number for row in seeded["LIFE"]["versions"]],
                        "four_eyes_approved": all(
                            row.approved_by and row.approved_by != row.committed_by
                            for row in seeded["LIFE"]["versions"]
                        ),
                        "calculation_status": "MODEL_NOT_CONFIGURED",
                    }
                _write(destination / "phase7-evidence.json", evidence)
                return evidence
        finally:
            engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    print(json.dumps(generate(arguments.output), indent=2, default=str))
