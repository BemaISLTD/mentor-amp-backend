"""M4 integration through frozen submission, shared engine, Parquet and trace."""

import math
import pytest
from app.db.models.run import Run
from app.db.models.run_package import RunPackage
from app.db.models.run_artifact import RunArtifact
from app.services import run_submission_service, results_service, trace_service, reconciliation_service, projection_set_service
from app.services.common import ServiceError
from app.core.artifacts import read_attempt_artifact_rows
from scripts.seed_indexed import seed_indexed
from .wp1_support import Env


@pytest.mark.parametrize("code,count,months", [("FIA", 1, 24), ("RILA", 30, 72)])
def test_indexed_frozen_block_workflow(code, count, months):
    env = Env()
    try:
        with env.Session() as db:
            seeded = seed_indexed(db, code, count=count, months=months)
            submitted = run_submission_service.submit_run_set(db, {
                "project_id": seeded["project"].id,
                "projection_set_ids": [seeded["projection_set"].id], "name": "M4 illustrative block",
            }, seeded["user"])
            project_id, actor_id = seeded["project"].id, seeded["user"].id
        env.execute(submitted)
        finals = {}
        with env.Session() as db:
            for item in submitted["runs"]:
                run = db.get(Run, item["id"])
                assert run.status == "success", run.summary
                assert run.error_count == 0
                package = db.query(RunPackage).filter_by(run_id=run.id).one()
                assert len(package.fingerprint) == 64
                rows = results_service.result_rows(db, run.id, variable="account_value_close", month=months)["rows"]
                assert len(rows) == count
                finals[item["scenario"]["name"]] = math.fsum(row["value"] for row in rows)
                artifacts = db.query(RunArtifact).filter_by(run_id=run.id).all()
                assert {a.artifact_type for a in artifacts} >= {"outputs", "traces"}
                assert all(len(a.checksum_sha256) == 64 for a in artifacts)
                traces = read_attempt_artifact_rows(db, run.id, "traces", run.accepted_attempt_number)
                assert any(t["variable_name"] == "segment_1_index_level" for t in traces)
                sample = trace_service.trace_tree(db, run.id, f"{code}-001", 12, "account_value_close")
                assert sample
                segment_rows = read_attempt_artifact_rows(db, run.id, "outputs", run.accepted_attempt_number,
                                                         filters={"variable_name": "segment_1_interim_val"})
                assert segment_rows and all(r["segment_id"] == "segment_1" for r in segment_rows)
                if item["scenario"]["name"] == "Base":
                    for expected_value, status in ((51750, "PASS"), (50000, "FAIL")):
                        expected = reconciliation_service.create_expected_set(db, project_id, {
                            "name": "Independent illustrative arithmetic", "product_code": code,
                            "timing_convention": "EOM", "rows": [{"policy_id": f"{code}-001", "projection_month": 12,
                            "account_value_close": expected_value}],
                        }, actor_id)
                        report = reconciliation_service.reconcile(db, run.id, {
                            "expected_result_set_id": expected["id"], "policy_id": f"{code}-001",
                            "variables": ["account_value_close"], "abs_tol": 1e-7, "rel_tol": 0,
                        }, actor_id)
                        assert report["status"] == status
            assert finals["Bull"] > finals["Base"] > finals["Bear"]
            assert finals["Base"] != finals["Stress"]
    finally:
        env.close()


def test_indexed_seed_idempotency_and_foreign_bindings_rejected():
    env = Env()
    try:
        with env.Session() as db:
            first = seed_indexed(db, "FIA", count=1, months=12)
            first_id = first["projection_set"].id
            again = seed_indexed(db, "FIA", count=1, months=12)
            assert again["projection_set"].id == first_id
            other = seed_indexed(db, "RILA", count=1, months=12)
            for field, foreign_id in (("factor_table_ids", other["factors"][0].id),
                                      ("inforce_file_ids", other["inforce"].id),
                                      ("scenario_ids", other["scenarios"][0].id)):
                with pytest.raises(ServiceError) as error:
                    projection_set_service.update(db, first_id, {field: [foreign_id]}, first["user"])
                assert error.value.status_code in {404, 422}
            with pytest.raises(ServiceError):
                projection_set_service.update(db, first_id, {"model_version_id": other["version"].id}, first["user"])
    finally:
        env.close()
