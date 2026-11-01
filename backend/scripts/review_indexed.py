"""Generate reproducible M4 review evidence through production services.

Runs only against a fresh disposable SQLite database. Never connects to the
configured application database or touches MYGA sample/reference assets.
Run from backend: python -m scripts.review_indexed --output ../.m4-review/evidence
"""

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
from unittest.mock import patch

import polars as pl
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.models  # register schema
from app.core import artifacts
from app.core.artifacts import LocalParquetArtifactStore
from app.db.database import Base
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact
from app.db.models.run_package import RunPackage
from app.services import run_submission_service, run_execution_service, results_service, trace_service, reconciliation_service, run_query_service
from scripts.seed_indexed import seed_indexed


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


def generate(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    store = LocalParquetArtifactStore(output / "parquet")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(artifacts, "get_artifact_store", return_value=store))
            stack.enter_context(patch.object(run_execution_service, "get_artifact_store", return_value=store))
            stack.enter_context(patch.object(run_execution_service, "SessionLocal", Session))
            evidence = {}
            for code, count, months in (("FIA", 1, 24), ("RILA", 30, 72)):
                with Session() as db:
                    seeded = seed_indexed(db, code, count=count, months=months)
                    submitted = run_submission_service.submit_run_set(db, {
                        "project_id": seeded["project"].id,
                        "projection_set_ids": [seeded["projection_set"].id], "name": f"{code} M4 review",
                    }, seeded["user"])
                    project_id, actor_id = seeded["project"].id, seeded["user"].id
                run_execution_service.execute_run_set(submitted["run_set"]["id"])
                product_evidence = {}
                with Session() as db:
                    for item in submitted["runs"]:
                        run = db.get(Run, item["id"])
                        if run.status != "success":
                            raise RuntimeError(f"{code} {item['scenario']['name']}: {run.status}, {run.summary}")
                        name = item["scenario"]["name"]
                        destination = output / f"{code}_{name}"
                        destination.mkdir(exist_ok=True)
                        write_json(destination / "manifest.json", run_query_service.get_manifest(db, run.id))
                        package = db.query(RunPackage).filter_by(run_id=run.id).one()
                        write_json(destination / "run_package.json", package.package)
                        write_json(destination / "trace.json", trace_service.trace_tree(db, run.id, f"{code}-001", 12, "account_value_close", depth=6))
                        aggregate = results_service.aggregates(db, run.id, group_by="projection_month", variables="account_value_close")
                        write_json(destination / "aggregate.json", aggregate)
                        aggregate_rows = [{"scenario_id": run.scenario_id, "projection_month": row["period"],
                                           "account_value_close": row["values"]["account_value_close"]}
                                          for row in aggregate["rows"]]
                        # Derived reporting artifact; values come from accepted engine outputs.
                        pl.DataFrame(aggregate_rows).write_parquet(destination / "aggregate.parquet")
                        schemas = {}
                        for artifact in db.query(RunArtifact).filter_by(run_id=run.id):
                            path = store.path_for_uri(artifact.storage_uri)
                            schemas[artifact.artifact_type] = {"columns": {k: str(v) for k, v in pl.read_parquet_schema(path).items()},
                                                              "row_count": artifact.row_count, "sha256": artifact.checksum_sha256}
                        write_json(destination / "schemas.json", schemas)
                        product_evidence[name] = {"run_id": run.id, "policies": count, "months": months,
                                                  "final_account_value": aggregate_rows[-1]["account_value_close"]}
                        if name == "Base":
                            # Independent hand calculation for policy 001 at month 12:
                            # 25,000 * 1.03 fixed + 25,000 * 1.04 indexed = 51,750.
                            expected = reconciliation_service.create_expected_set(db, project_id, {
                                "name": "Illustrative hand-calculated engineering anchor",
                                "product_code": code, "source_reference": "docs/m4/IMPLEMENTATION.md#engineering-anchor",
                                "timing_convention": "EOM", "rows": [{"policy_id": f"{code}-001", "projection_month": 12,
                                                                          "account_value_close": 51750.0}],
                            }, actor_id)
                            report = reconciliation_service.reconcile(db, run.id, {
                                "expected_result_set_id": expected["id"], "policy_id": f"{code}-001",
                                "variables": ["account_value_close"], "abs_tol": 1e-7, "rel_tol": 0,
                            }, actor_id)
                            if report["status"] != "PASS":
                                raise RuntimeError(report)
                            write_json(destination / "reconciliation.json", {"classification": "ENGINEERING_ANCHOR_ONLY",
                                       "actuarial_acceptance": "PENDING_OWNER_APPROVAL", "report": report})
                            db.commit()
                assert product_evidence["Bull"]["final_account_value"] > product_evidence["Base"]["final_account_value"] > product_evidence["Bear"]["final_account_value"]
                assert product_evidence["Base"]["final_account_value"] != product_evidence["Stress"]["final_account_value"]
                evidence[code] = product_evidence
            write_json(output / "scenario_preservation.json", evidence)
            return evidence
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(generate(args.output), indent=2))
