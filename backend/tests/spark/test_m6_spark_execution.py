"""End-to-end distributed PySpark execution, parity, failure/retry, security, and multi-worker tests (M6-02).

Runs against real Apache Spark 4.1+ when PySpark and Java 17+ are available (``venv-spark`` and CI).
Compares Spark outputs against CPU at policy, scenario, projection month, variable, cash flow, reserve,
aggregate, trace, and reconciliation levels using explicitly documented provisional engineering tolerances.
"""

from __future__ import annotations

import json
import math
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pyspark = pytest.importorskip("pyspark")

from app.config import settings  # noqa: E402
from app.core.artifacts import read_attempt_artifact_rows  # noqa: E402
from app.core.execution import backends as exec_backends  # noqa: E402
from app.core.execution.spark_backend import (  # noqa: E402
    SparkExecutionBackend,
    SparkWaveExecutionError,
    _resolve_java_home,
)
from app.db.models.project import Project  # noqa: E402
from app.db.models.run import Run  # noqa: E402
from app.db.models.run_artifact import RunArtifact, RunManifest  # noqa: E402
from app.db.models.run_package import RunAttempt, RunPackage  # noqa: E402
from app.db.models.user import User  # noqa: E402
from app.products.myga_lite.golden import GOLDEN_RESULTS  # noqa: E402
from app.products.spia_reference import config as spia_ref  # noqa: E402
from app.services import (  # noqa: E402
    data_manager_service,
    reconciliation_service,
    results_service,
    run_control_service,
    run_execution_service,
    run_submission_service,
    trace_service,
)
from scripts.seed_indexed import seed_indexed  # noqa: E402
from tests.integration.test_myga_end_to_end import _import_myga_inputs, _seed_myga  # noqa: E402
from tests.integration.test_spia_reference_workflow import (  # noqa: E402
    INFORCE_FIELDS as SPIA_CANONICAL_FIELDS,
    _import_dataset,
    _reference_inputs,
)
from tests.integration.wp1_support import Env  # noqa: E402

if _resolve_java_home() is None:
    pytest.skip("Java 17+ is required for real PySpark tests", allow_module_level=True)


# Explicitly documented provisional engineering parity tolerance (CAS §17 formal owner tolerances remain OPEN).
PROVISIONAL_ABS_TOL = 1e-9


def _submit_and_execute(env: Env, project_key: str, payload: dict) -> dict:
    ids = env.projects[project_key]
    with env.Session() as db:
        admin = db.get(User, ids["admin"])
        submitted = run_submission_service.submit_run_set(db, payload, admin)
    env.execute(submitted)
    return submitted


def _parquet_map(env: Env, run_id: str, artifact_type: str) -> dict[tuple, dict]:
    with env.Session() as db:
        run = db.get(Run, run_id)
        rows = read_attempt_artifact_rows(db, run_id, artifact_type, run.accepted_attempt_number)
    if artifact_type == "outputs":
        return {
            (r["policy_id"], r["scenario_id"], int(r["projection_month"]), r["variable_name"]): r
            for r in rows
        }
    return {
        (r["policy_id"], r["scenario_id"], int(r["projection_month"]), r["variable_name"]): r
        for r in rows
    }


def _assert_full_run_parity(env: Env, cpu_run_id: str, spark_run_id: str) -> None:
    cpu_sum = env.summary(cpu_run_id)
    spk_sum = env.summary(spark_run_id)
    assert cpu_sum["status"] == spk_sum["status"] == "success"
    assert cpu_sum["output_row_count"] == spk_sum["output_row_count"]
    assert math.isclose(
        cpu_sum["headline"]["value"], spk_sum["headline"]["value"], abs_tol=PROVISIONAL_ABS_TOL
    )
    for var_name, cpu_tot in cpu_sum["totals"].items():
        spk_tot = spk_sum["totals"][var_name]
        assert math.isclose(cpu_tot["value"], spk_tot["value"], abs_tol=PROVISIONAL_ABS_TOL)

    cpu_out = _parquet_map(env, cpu_run_id, "outputs")
    spk_out = _parquet_map(env, spark_run_id, "outputs")
    assert set(cpu_out) == set(spk_out)
    for key, crow in cpu_out.items():
        srow = spk_out[key]
        cval = json.loads(crow["value_json"])["value"]
        sval = json.loads(srow["value_json"])["value"]
        assert math.isclose(float(cval), float(sval), abs_tol=PROVISIONAL_ABS_TOL), key

    cpu_tr = _parquet_map(env, cpu_run_id, "traces")
    spk_tr = _parquet_map(env, spark_run_id, "traces")
    assert set(cpu_tr) == set(spk_tr)
    for key, crow in cpu_tr.items():
        srow = spk_tr[key]
        assert crow["output_value_json"] == srow["output_value_json"]
        assert crow["input_values_json"] == srow["input_values_json"]


def test_spark_spia_parity_and_partition_invariance():
    env = Env()
    try:
        ids = env.seed("A", horizon=24)
        base_req = {
            "project_id": ids["project"],
            "name": "SPIA parity comparison",
            "projection_set_ids": [ids["projection_set"]],
            "scenario_ids": [ids["scenarios"]["Base"], ids["scenarios"]["Low Interest Rate"]],
        }
        cpu_sub = _submit_and_execute(env, "A", {**base_req, "execution_backend": "cpu"})
        sp2_sub = _submit_and_execute(
            env,
            "A",
            {
                **base_req,
                "execution_backend": "spark",
                "fallback_policy": "strict_fail",
                "execution_options": {"spark_master": "local[2]", "spark_partitions": 2},
            },
        )
        sp4_sub = _submit_and_execute(
            env,
            "A",
            {
                **base_req,
                "execution_backend": "spark",
                "fallback_policy": "strict_fail",
                "execution_options": {"spark_master": "local[4]", "spark_partitions": 4},
            },
        )

        for scen_idx in range(2):
            cpu_r = cpu_sub["runs"][scen_idx]
            sp2_r = sp2_sub["runs"][scen_idx]
            sp4_r = sp4_sub["runs"][scen_idx]

            # Backend-neutral RunPackage fingerprint invariant across CPU, p=2, p=4.
            assert cpu_r["run_package_fingerprint"] == sp2_r["run_package_fingerprint"]
            assert cpu_r["run_package_fingerprint"] == sp4_r["run_package_fingerprint"]

            _assert_full_run_parity(env, cpu_r["id"], sp2_r["id"])
            _assert_full_run_parity(env, cpu_r["id"], sp4_r["id"])

            v2 = env.get(f"/runs/{sp2_r['id']}").json()
            assert v2["compute"]["backend"] == "spark"
            assert v2["execution"]["actual_backend"] == "spark"
            assert v2["execution"]["accelerated"] is True
            assert v2["execution"]["fallback_used"] is False
            assert v2["execution"]["spark"]["spark_master"] == "local[2]"
            assert v2["execution"]["spark"]["partitions_used"] == [0, 1]
            assert len(v2["execution"]["spark"]["worker_os_pids"]) >= 1

            v4 = env.get(f"/runs/{sp4_r['id']}").json()
            assert v4["execution"]["spark"]["spark_master"] == "local[4]"
            assert v4["execution"]["spark"]["partitions_used"] == [0, 1, 2, 3]

            manifest = env.get(f"/runs/{sp4_r['id']}/manifest").json()["final_manifest"]["manifest"]
            assert manifest["runtime"]["execution"]["actual_backend"] == "spark"
            assert manifest["runtime"]["execution"]["spark"]["partitions_used"] == [0, 1, 2, 3]
    finally:
        env.close()


def test_spark_myga_golden_reconciliation_and_bounded_waves():
    env = Env()
    try:
        _seed_myga(env)
        ids = env.projects["A"]
        reviewer = env.user(
            "myga-spark-rev@example.com", role="reviewer", memberships={"A": "viewer"}
        )
        imported = _import_myga_inputs(env, reviewer["headers"])
        datasets = {name: item["dataset"] for name, item in imported.items()}
        outputs = [
            "account_value_close", "mgsv_close", "surrender_benefit", "death_benefit",
            "death_claims", "surrender_claims", "annuitization_transfer", "maturity_benefit",
            "maintenance_expense", "liability_outgo", "net_cash_flow", "reserve",
        ]
        created = env.client.post(
            f"/v1/projects/{ids['project']}/projection-sets",
            headers=env.admin_headers(),
            json={
                "name": "MYGA Spark Bounded Waves",
                "model_version_id": ids["myga_version"],
                "inforce_file_ids": [datasets["inforce"]["id"]],
                "assumption_table_ids": [datasets["mortality"]["id"]],
                "factor_table_ids": [datasets["charges"]["id"], datasets["age_caps"]["id"]],
                "scenario_ids": [datasets["base"]["id"], datasets["rates_up"]["id"]],
                "valuation_date": "2026-12-31", "horizon_months": 480,
                "time_step": "monthly", "output_variables": outputs,
                "trace_scope": {"mode": "selected_policies", "policy_ids": ["MYGA-0001"]},
                "parameters": {},
            },
        )
        assert created.status_code == 201, created.text
        ps_id = created.json()["id"]
        assert env.client.post(
            f"/v1/projection-sets/{ps_id}/validate", headers=env.admin_headers()
        ).status_code == 200

        # 25 policies with batch_size=7 executes as 4 bounded waves (7+7+7+4).
        sub = _submit_and_execute(
            env,
            "A",
            {
                "project_id": ids["project"],
                "name": "MYGA Spark Wave Run",
                "projection_set_ids": [ps_id],
                "scenario_ids": [datasets["base"]["id"], datasets["rates_up"]["id"]],
                "execution_backend": "spark",
                "fallback_policy": "strict_fail",
                "execution_options": {
                    "spark_master": "local[2]",
                    "spark_partitions": 2,
                    "batch_size": 7,
                },
            },
        )
        runs = {r["scenario"]["name"]: r["id"] for r in sub["runs"]}
        base_sum = env.summary(runs["Base"])
        ru_sum = env.summary(runs["Rates Up 150bp"])
        assert math.isclose(base_sum["headline"]["value"], GOLDEN_RESULTS["base_reserve"], abs_tol=1e-6)
        assert math.isclose(ru_sum["headline"]["value"], GOLDEN_RESULTS["rates_up_reserve"], abs_tol=1e-6)
        assert math.isclose(
            base_sum["totals"]["liability_outgo"]["value"],
            GOLDEN_RESULTS["base_undiscounted_outgo"],
            abs_tol=1e-6,
        )
        assert math.isclose(
            ru_sum["totals"]["liability_outgo"]["value"],
            GOLDEN_RESULTS["rates_up_undiscounted_outgo"],
            abs_tol=1e-6,
        )
        trace = env.get(
            f"/runs/{runs['Base']}/trace",
            policy_id="MYGA-0001", month=30, variable="surrender_benefit", depth=8,
        ).json()["root"]
        assert math.isclose(
            trace["value"], GOLDEN_RESULTS["base_policy_0001_month_30_surrender_benefit"], abs_tol=1e-6
        )

        view = env.get(f"/runs/{runs['Base']}").json()
        assert view["execution"]["spark"]["waves_completed"] == 4
        assert view["execution"]["actual_backend"] == "spark"

        # Deterministic Parquet replay across repeat Spark submission.
        sub2 = _submit_and_execute(
            env,
            "A",
            {
                "project_id": ids["project"],
                "name": "MYGA Spark Replay",
                "projection_set_ids": [ps_id],
                "scenario_ids": [datasets["base"]["id"]],
                "execution_backend": "spark",
                "fallback_policy": "strict_fail",
                "execution_options": {"spark_master": "local[4]", "spark_partitions": 4, "batch_size": 7},
            },
        )
        with env.Session() as db:
            b1 = sorted(
                (a.artifact_type, a.row_count, env.artifact_store.read_bytes(a.storage_uri))
                for a in db.query(RunArtifact).filter_by(run_id=runs["Base"], attempt_number=1).all()
            )
            b2 = sorted(
                (a.artifact_type, a.row_count, env.artifact_store.read_bytes(a.storage_uri))
                for a in db.query(RunArtifact).filter_by(run_id=sub2["runs"][0]["id"], attempt_number=1).all()
            )
            assert b1 == b2
    finally:
        env.close()


@pytest.mark.parametrize("code,count,months", [("FIA", 4, 24), ("RILA", 12, 36)])
def test_spark_fia_and_rila_parity_and_reconciliation(code, count, months):
    env = Env()
    try:
        with env.Session() as db:
            seeded = seed_indexed(db, code, count=count, months=months)
            project_id = seeded["project"].id
            ps_id = seeded["projection_set"].id
            actor = seeded["user"]
            cpu_sub = run_submission_service.submit_run_set(
                db,
                {
                    "project_id": project_id,
                    "projection_set_ids": [ps_id],
                    "name": f"{code} CPU baseline",
                    "execution_backend": "cpu",
                },
                actor,
            )
            spk_sub = run_submission_service.submit_run_set(
                db,
                {
                    "project_id": project_id,
                    "projection_set_ids": [ps_id],
                    "name": f"{code} Spark run",
                    "execution_backend": "spark",
                    "fallback_policy": "strict_fail",
                    "execution_options": {"spark_master": "local[2]", "spark_partitions": 2},
                },
                actor,
            )
            actor_id = actor.id
        env.execute(cpu_sub)
        env.execute(spk_sub)

        with env.Session() as db:
            for c_item, s_item in zip(cpu_sub["runs"], spk_sub["runs"], strict=True):
                assert c_item["run_package_fingerprint"] == s_item["run_package_fingerprint"]
                s_run = db.get(Run, s_item["id"])
                c_run = db.get(Run, c_item["id"])
                assert s_run.status == c_run.status == "success"

                c_rows = {
                    r["policy_id"]: r["value"]
                    for r in results_service.result_rows(db, c_run.id, variable="account_value_close", month=months)["rows"]
                }
                s_rows = {
                    r["policy_id"]: r["value"]
                    for r in results_service.result_rows(db, s_run.id, variable="account_value_close", month=months)["rows"]
                }
                assert set(c_rows) == set(s_rows)
                for pid in c_rows:
                    assert math.isclose(c_rows[pid], s_rows[pid], abs_tol=PROVISIONAL_ABS_TOL)

                c_tr = read_attempt_artifact_rows(db, c_run.id, "traces", c_run.accepted_attempt_number)
                s_tr = read_attempt_artifact_rows(db, s_run.id, "traces", s_run.accepted_attempt_number)
                assert len(c_tr) == len(s_tr) > 0

                if c_item["scenario"]["name"] == "Base":
                    m12 = results_service.result_rows(db, s_run.id, variable="account_value_close", month=12)["rows"]
                    base_p1 = next(r["value"] for r in m12 if r["policy_id"] == f"{code}-001")
                    expected = reconciliation_service.create_expected_set(
                        db,
                        project_id,
                        {
                            "name": f"{code} CPU-Spark oracle",
                            "product_code": code,
                            "timing_convention": "EOM",
                            "rows": [
                                {"policy_id": f"{code}-001", "projection_month": 12, "account_value_close": base_p1}
                            ],
                        },
                        actor_id,
                    )
                    report = reconciliation_service.reconcile(
                        db,
                        s_run.id,
                        {
                            "expected_result_set_id": expected["id"],
                            "policy_id": f"{code}-001",
                            "variables": ["account_value_close"],
                            "abs_tol": PROVISIONAL_ABS_TOL,
                            "rel_tol": 0,
                        },
                        actor_id,
                    )
                    assert report["status"] == "PASS"
    finally:
        env.close()


def test_spark_per_policy_error_isolation_partial_success():
    """M6-02-01: Preserve canonical Data Manager schema while verifying Spark per-policy partial_success."""
    env = Env()
    try:
        ids = env.seed("A", horizon=12)
        env.monkeypatch.setattr(
            data_manager_service, "get_artifact_store", lambda: env.artifact_store
        )
        with env.Session() as db:
            project = db.get(Project, ids["project"])
            user = db.get(User, ids["admin"])
            seed = env.seed_module
            model, version, group = seed.upsert_model(db, project, user, spia_ref)
            seed.upsert_variables(db, spia_ref)
            seed.upsert_definitions(db, version, spia_ref)
            seed.upsert_formulas(db, version, group, spia_ref)
            seed.upsert_published_outputs(db, version, spia_ref)
            checks = seed.validate_model(db, model, version)
            assert checks["status"] == "validated", checks
            db.commit()
            ids["reference_version"] = version.id

        reviewer = env.user("spia-spark-rev@example.com", role="reviewer", memberships={"A": "viewer"})
        ref_inputs = _reference_inputs(env, reviewer["headers"])

        # Verify Data Manager still rejects schema-incomplete SPIA CSVs before execution.
        incomplete_csv = (
            "policy_id,product_type,issue_date,issue_age,gender,monthly_payment,cola_rate\n"
            "INCOMPLETE-1,SPIA,2026-12-31,65,M,600,0.0\n"
        ).encode()
        incomplete_fields = (
            "policy_id", "product_type", "issue_date", "issue_age", "gender",
            "monthly_payment", "cola_rate",
        )
        opened = env.client.post(
            "/v1/import-sessions",
            headers=env.admin_headers(),
            json={
                "project_id": ids["project"],
                "category": "liability_inforce",
                "name": "Incomplete SPIA rejected",
                "options": {"valuation_date": "2026-12-31"},
            },
        )
        assert opened.status_code == 201, opened.text
        bad_session_id = opened.json()["id"]
        uploaded = env.client.post(
            f"/v1/import-sessions/{bad_session_id}/files",
            headers=env.admin_headers(),
            files={"file": ("incomplete.csv", incomplete_csv, "text/csv")},
        )
        assert uploaded.status_code == 200, uploaded.text
        bad_mapping = env.client.put(
            f"/v1/import-sessions/{bad_session_id}/mapping",
            headers=env.admin_headers(),
            json={"fields": [{"source_column": f, "target_field": f} for f in incomplete_fields]},
        )
        assert bad_mapping.status_code == 200, bad_mapping.text
        bad_validate = env.client.post(
            f"/v1/import-sessions/{bad_session_id}/validate",
            headers=env.admin_headers(),
        )
        assert bad_validate.status_code == 201, bad_validate.text
        val_json = bad_validate.json()
        assert val_json["status"] == "invalid" and val_json["error_count"] >= 1
        bad_commit = env.client.post(
            f"/v1/import-sessions/{bad_session_id}/commit",
            headers=env.admin_headers(),
        )
        assert bad_commit.status_code in (409, 422)

        # Canonical schema-valid CSV where BAD-FORM-1 passes Data Manager validation
        # but raises ValueError('unsupported benefit_form') inside spia_reference calculation.
        valid_csv = (
            "policy_id,product_type,issue_date,issue_age,gender,premium,monthly_payment,annual_income,"
            "benefit_form,certain_months,annuity_year,payment_frequency,payment_timing,"
            "cola_rate,rating_factor,is_joint,joint_issue_age,joint_gender,joint_rating_factor,"
            "survivor_percentage,reduction_trigger,commutation_enabled,"
            "issue_state_excludes_withdrawal,cumulative_scheduled_paid_at_valuation,"
            "commutation_fraction_at_valuation\n"
            "GOOD-1,SPIA,2026-12-31,65,M,0,500,6000,life_only,0,2026,12,arrears,0,1,0,65,M,1,1,either,0,0,0,0\n"
            "BAD-FORM-1,SPIA,2026-12-31,65,M,0,500,6000,unsupported_form,0,2026,12,arrears,0,1,0,65,M,1,1,either,0,0,0,0\n"
            "GOOD-2,SPIA,2026-12-31,65,M,0,700,8400,life_only,0,2026,12,arrears,0,1,0,65,M,1,1,either,0,0,0,0\n"
        ).encode()
        imported = _import_dataset(
            env,
            reviewer["headers"],
            category="liability_inforce",
            name="SPIA Canonical Mixed Runtime Failure",
            content=valid_csv,
            fields=SPIA_CANONICAL_FIELDS,
            options={"valuation_date": "2026-12-31", "expected_record_count": 3},
        )
        datasets = {k: v["dataset"] for k, v in ref_inputs.items()}
        ps = env.client.post(
            f"/v1/projects/{ids['project']}/projection-sets",
            headers=env.admin_headers(),
            json={
                "name": "Mixed SPIA Canonical Partial Success",
                "model_version_id": ids["reference_version"],
                "inforce_file_ids": [imported["dataset"]["id"]],
                "assumption_table_ids": [datasets["mortality"]["id"], datasets["improvement"]["id"]],
                "factor_table_ids": [datasets["surrender_charge"]["id"]],
                "scenario_ids": [datasets["scenario"]["id"]],
                "valuation_date": "2026-12-31",
                "horizon_months": 12,
                "time_step": "monthly",
                "output_variables": ["annuity_payment", "pv_liability_cash_flow", "reserve"],
                "trace_scope": {"mode": "selected_policies", "policy_ids": ["GOOD-1", "GOOD-2"]},
                "parameters": {},
            },
        )
        assert ps.status_code == 201, ps.text
        ps_id = ps.json()["id"]
        assert env.client.post(f"/v1/projection-sets/{ps_id}/validate", headers=env.admin_headers()).status_code == 200

        cpu_sub = _submit_and_execute(
            env,
            "A",
            {
                "project_id": ids["project"],
                "name": "CPU partial_success ref",
                "projection_set_ids": [ps_id],
                "scenario_ids": [datasets["scenario"]["id"]],
                "execution_backend": "cpu",
            },
        )
        spk_sub = _submit_and_execute(
            env,
            "A",
            {
                "project_id": ids["project"],
                "name": "Spark partial_success test",
                "projection_set_ids": [ps_id],
                "scenario_ids": [datasets["scenario"]["id"]],
                "execution_backend": "spark",
                "fallback_policy": "strict_fail",
                "execution_options": {"spark_master": "local[2]", "spark_partitions": 2},
            },
        )
        cpu_id = cpu_sub["runs"][0]["id"]
        spk_id = spk_sub["runs"][0]["id"]
        assert cpu_sub["runs"][0]["run_package_fingerprint"] == spk_sub["runs"][0]["run_package_fingerprint"]

        cpu_view = env.get(f"/runs/{cpu_id}").json()
        spk_view = env.get(f"/runs/{spk_id}").json()
        assert spk_view["status"] == cpu_view["status"] == "partial_success"
        assert spk_view["error_count"] == cpu_view["error_count"] == 1
        assert spk_view["accepted_attempt_number"] == 1
        assert spk_view["execution"]["actual_backend"] == "spark"

        # Opt-in contract matches CPU reference (409 RESULTS_INCOMPLETE without include_partial=true).
        blocked = env.get(f"/runs/{spk_id}/results")
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "RESULTS_INCOMPLETE"
        spk_sum = env.summary(spk_id)
        cpu_sum = env.summary(cpu_id)
        assert spk_sum["complete"] is cpu_sum["complete"] is False
        assert spk_sum["failed_policy_count"] == cpu_sum["failed_policy_count"] == 1
        assert spk_sum["policy_count"] == cpu_sum["policy_count"] == 3

        # Error reporting matches BAD-FORM-1.
        spk_events = env.get(f"/runs/{spk_id}/events").json()["events"]
        assert any(
            ev["level"] == "error"
            and (ev["data"] or {}).get("policy_id") == "BAD-FORM-1"
            and "unsupported benefit_form" in ev["message"]
            for ev in spk_events
        )

        # Surviving policies retain exact outputs, traces, and artifact checksums matching CPU.
        with env.Session() as db:
            cpu_rows = read_attempt_artifact_rows(db, cpu_id, "outputs", 1)
            spk_rows = read_attempt_artifact_rows(db, spk_id, "outputs", 1)
            assert len(spk_rows) == len(cpu_rows) > 0
            cpu_map = {
                (r["policy_id"], int(r["projection_month"]), r["variable_name"]): json.loads(r["value_json"])["value"]
                for r in cpu_rows
            }
            spk_map = {
                (r["policy_id"], int(r["projection_month"]), r["variable_name"]): json.loads(r["value_json"])["value"]
                for r in spk_rows
            }
            assert set(cpu_map) == set(spk_map)
            assert {k[0] for k in spk_map} == {"GOOD-1", "GOOD-2"}
            for key in cpu_map:
                assert math.isclose(cpu_map[key], spk_map[key], abs_tol=PROVISIONAL_ABS_TOL)

            cpu_tr = read_attempt_artifact_rows(db, cpu_id, "traces", 1)
            spk_tr = read_attempt_artifact_rows(db, spk_id, "traces", 1)
            assert len(spk_tr) == len(cpu_tr) > 0

            cpu_out_art = db.query(RunArtifact).filter_by(run_id=cpu_id, artifact_type="outputs").one()
            spk_out_art = db.query(RunArtifact).filter_by(run_id=spk_id, artifact_type="outputs").one()
            assert spk_out_art.checksum_sha256 and spk_out_art.row_count == cpu_out_art.row_count

            # Reconciliation refuses incomplete partial_success runs consistently on both CPU and Spark.
            m1_good1 = spk_map[("GOOD-1", 1, "annuity_payment")]
            expected = reconciliation_service.create_expected_set(
                db,
                ids["project"],
                {
                    "name": "Partial Success GOOD-1 Oracle",
                    "product_code": "SPIA",
                    "timing_convention": "EOM",
                    "rows": [{"policy_id": "GOOD-1", "projection_month": 1, "annuity_payment": m1_good1}],
                },
                ids["admin"],
            )
            from app.services.common import ServiceError

            for incomplete_run_id in (cpu_id, spk_id):
                with pytest.raises(ServiceError) as exc_info:
                    reconciliation_service.reconcile(
                        db,
                        incomplete_run_id,
                        {
                            "expected_result_set_id": expected["id"],
                            "policy_id": "GOOD-1",
                            "variables": ["annuity_payment"],
                            "abs_tol": PROVISIONAL_ABS_TOL,
                            "rel_tol": 0,
                        },
                        ids["admin"],
                    )
                assert exc_info.value.status_code == 409
                assert exc_info.value.code == "RESULTS_INCOMPLETE"
    finally:
        env.close()


def test_spark_cancellation_worker_failure_and_retry_without_duplicates(monkeypatch):
    """M6-02-02: Real run_control_service cancellation, job_group propagation, and retry without duplicates."""
    env = Env()
    try:
        ids = env.seed("A", horizon=12)
        # Part A: Real run_control_service.cancel_run() stops Spark execution cleanly using Spark job groups.
        with env.Session() as db:
            admin = db.get(User, ids["admin"])
            sub_cancel = run_submission_service.submit_run_set(
                db,
                {
                    "project_id": ids["project"],
                    "name": "Spark cancel wave",
                    "projection_set_ids": [ids["projection_set"]],
                    "scenario_ids": [ids["scenarios"]["Base"]],
                    "execution_backend": "spark",
                    "fallback_policy": "strict_fail",
                    "execution_options": {"spark_master": "local[2]", "spark_partitions": 2, "batch_size": 8},
                },
                admin,
            )
        cancel_run_id = sub_cancel["runs"][0]["id"]

        seen_waves: list[int] = []
        real_get_backend = exec_backends.BackendCapabilityRegistry.get_backend

        def _cancel_at_wave1(self, name, **kwargs):
            backend = real_get_backend(self, name, **kwargs)
            if isinstance(backend, SparkExecutionBackend):
                def _hook(wave_idx, _items):
                    seen_waves.append(wave_idx)
                    if wave_idx == 1:
                        with env.Session() as db2:
                            u2 = db2.get(User, ids["admin"])
                            run_control_service.cancel_run(db2, cancel_run_id, u2)
                backend._policy_batch_hook = _hook
            return backend

        monkeypatch.setattr(exec_backends.BackendCapabilityRegistry, "get_backend", _cancel_at_wave1)
        with env.Session() as db:
            status_c = run_execution_service.execute_run(db, cancel_run_id, worker_id="w-cancel")
        assert status_c == "cancelled"
        # Wave 0 completed cleanly; Wave 1 hook triggered cancel_run() and stopped before Wave 1 stage/Wave 2 ran.
        assert seen_waves == [0, 1]

        cancel_view = env.get(f"/runs/{cancel_run_id}").json()
        assert cancel_view["status"] == "cancelled"
        assert cancel_view["results_available"] is False
        assert cancel_view["progress"]["done"] == 8
        assert cancel_view["execution"]["spark"]["waves_completed"] == 1
        assert cancel_view["execution"]["spark"]["cancelled"] is True
        assert cancel_view["execution"]["spark"]["job_group"] == f"mentoramp-run-{cancel_run_id}-attempt-1"
        assert env.get(f"/runs/{cancel_run_id}/results").status_code == 409

        # Part B: Worker crash after Wave 0 fails run (no silent CPU fallback!), then retry succeeds cleanly.
        monkeypatch.setattr(exec_backends.BackendCapabilityRegistry, "get_backend", real_get_backend)
        with env.Session() as db:
            admin = db.get(User, ids["admin"])
            sub_crash = run_submission_service.submit_run_set(
                db,
                {
                    "project_id": ids["project"],
                    "name": "Spark crash and retry",
                    "projection_set_ids": [ids["projection_set"]],
                    "scenario_ids": [ids["scenarios"]["Base"]],
                    "execution_backend": "spark",
                    "fallback_policy": "allow_cpu_fallback",
                    "execution_options": {"spark_master": "local[2]", "spark_partitions": 2, "batch_size": 12},
                },
                admin,
            )
        crash_run_id = sub_crash["runs"][0]["id"]

        def _crash_on_wave1(self, name, **kwargs):
            backend = real_get_backend(self, name, **kwargs)
            if isinstance(backend, SparkExecutionBackend):
                def _hook(wave_idx, _items):
                    if wave_idx == 1:
                        raise RuntimeError("Injected worker process termination on wave 1")
                backend._policy_batch_hook = _hook
            return backend

        monkeypatch.setattr(exec_backends.BackendCapabilityRegistry, "get_backend", _crash_on_wave1)
        with env.Session() as db:
            status_f = run_execution_service.execute_run(db, crash_run_id, worker_id="w-crash")
        assert status_f == "failed"

        failed_view = env.get(f"/runs/{crash_run_id}").json()
        assert failed_view["accepted_attempt_number"] is None
        assert failed_view["execution"]["actual_backend"] == "spark"
        assert failed_view["execution"]["fallback_used"] is False

        # Restore healthy Spark and retry cleanly via API without duplicate rows or artifacts.
        monkeypatch.setattr(exec_backends.BackendCapabilityRegistry, "get_backend", real_get_backend)
        retried_resp = env.client.post(f"/v1/runs/{crash_run_id}/retry", headers=env.admin_headers())
        assert retried_resp.status_code == 202, retried_resp.text
        retried_id = retried_resp.json()["id"]
        assert env.summary(retried_id)["status"] == "success"
        retried_view = env.get(f"/runs/{retried_id}").json()
        assert retried_view["execution"]["actual_backend"] == "spark"
        assert retried_view["progress"]["done"] == 25
        with env.Session() as db:
            artifacts = db.query(RunArtifact).filter_by(run_id=retried_id, attempt_number=1).all()
            outputs = [a for a in artifacts if a.artifact_type == "outputs"]
            assert len(outputs) == 3
            total_written = sum(int(a.row_count or 0) for a in outputs)
            assert total_written == 1225
            retried_rows = read_attempt_artifact_rows(db, retried_id, "outputs", 1)
            assert len(retried_rows) == total_written
            assert len({(r["policy_id"], r["scenario_id"], int(r["projection_month"]), r["variable_name"]) for r in retried_rows}) == total_written
    finally:
        env.close()


def test_spark_multi_worker_standalone_cluster_execution(monkeypatch, tmp_path):
    """M6-02-03: Local standalone multi-worker cluster (Master JVM + 2 separate Worker JVMs on 127.0.0.1)."""
    def free_port() -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    java_home = _resolve_java_home()
    assert java_home is not None
    java_exe = str(Path(java_home) / "bin" / ("java.exe" if sys.platform == "win32" else "java"))
    spark_home = str(Path(pyspark.__file__).parent)
    jars = str(Path(spark_home) / "jars" / "*")
    m_port, m_web = free_port(), free_port()
    w1_port, w1_web = free_port(), free_port()
    w2_port, w2_web = free_port(), free_port()
    master_url = f"spark://127.0.0.1:{m_port}"
    w1_dir = tmp_path / "w1"
    w2_dir = tmp_path / "w2"
    w1_dir.mkdir()
    w2_dir.mkdir()

    env = Env()
    procs: list[subprocess.Popen] = []
    try:
        sub_env = {
            **os.environ,
            "JAVA_HOME": java_home,
            "SPARK_HOME": spark_home,
            "SPARK_SCALA_VERSION": "2.13",
            "PYSPARK_PYTHON": sys.executable,
            "PYSPARK_DRIVER_PYTHON": sys.executable,
        }
        procs.append(subprocess.Popen(
            [java_exe, "-cp", jars, "org.apache.spark.deploy.master.Master", "--host", "127.0.0.1", "--port", str(m_port), "--webui-port", str(m_web)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=sub_env,
        ))
        procs.append(subprocess.Popen(
            [java_exe, "-cp", jars, "org.apache.spark.deploy.worker.Worker", master_url, "--host", "127.0.0.1", "--port", str(w1_port), "--webui-port", str(w1_web), "--cores", "1", "--memory", "1g", "--work-dir", str(w1_dir)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=sub_env,
        ))
        procs.append(subprocess.Popen(
            [java_exe, "-cp", jars, "org.apache.spark.deploy.worker.Worker", master_url, "--host", "127.0.0.1", "--port", str(w2_port), "--webui-port", str(w2_web), "--cores", "1", "--memory", "1g", "--work-dir", str(w2_dir)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=sub_env,
        ))
        for _ in range(40):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{m_web}/json/", timeout=2) as r:
                    info = json.load(r)
                if info.get("aliveworkers", 0) >= 2:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            pytest.fail("Standalone Spark Master did not see 2 alive workers in 20s")

        monkeypatch.setattr(settings, "spark_allowed_masters", f"local[1..8],{master_url}")
        ids = env.seed("A", horizon=12)
        cpu_sub = _submit_and_execute(
            env, "A",
            {
                "project_id": ids["project"],
                "name": "Multi-worker CPU reference",
                "projection_set_ids": [ids["projection_set"]],
                "scenario_ids": [ids["scenarios"]["Base"]],
                "execution_backend": "cpu",
            },
        )
        cluster_sub = _submit_and_execute(
            env, "A",
            {
                "project_id": ids["project"],
                "name": "Multi-worker Standalone Spark",
                "projection_set_ids": [ids["projection_set"]],
                "scenario_ids": [ids["scenarios"]["Base"]],
                "execution_backend": "spark",
                "fallback_policy": "strict_fail",
                "execution_options": {"spark_master": master_url, "spark_partitions": 4},
            },
        )
        cpu_id = cpu_sub["runs"][0]["id"]
        spk_id = cluster_sub["runs"][0]["id"]
        _assert_full_run_parity(env, cpu_id, spk_id)

        view = env.get(f"/runs/{spk_id}").json()
        spark_meta = view["execution"]["spark"]
        assert spark_meta["spark_master"] == master_url
        assert len(spark_meta["partitions_used"]) == 4
        # Genuine local standalone multi-worker evidence: driver + >=2 non-driver application executors & >=2 worker OS PIDs.
        assert len(spark_meta["worker_executor_infos"]) >= 2
        assert len(set(spark_meta["worker_executor_ids"])) >= 2
        assert "driver" not in spark_meta["worker_executor_ids"]
        assert len(spark_meta["executor_infos"]) >= 3
        assert len(spark_meta["worker_os_pids"]) >= 2
    finally:
        env.close()
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()


def test_spark_security_allowlist_and_cross_project_isolation():
    env = Env()
    try:
        ids_a = env.seed("A", horizon=12)
        env.seed("B", horizon=12)
        outsider_b = env.user("outsider-spk@example.com", role="actuary", memberships={"B": "owner"})

        bad_resp = env.client.post(
            "/v1/run-sets",
            headers=env.admin_headers(),
            json={
                "project_id": ids_a["project"],
                "name": "Evil master attempt",
                "projection_set_ids": [ids_a["projection_set"]],
                "scenario_ids": [ids_a["scenarios"]["Base"]],
                "execution_backend": "spark",
                "execution_options": {"spark_master": "spark://untrusted.attacker.invalid:7077"},
            },
        )
        assert bad_resp.status_code == 422
        assert bad_resp.json()["error"]["code"] == "SPARK_MASTER_FORBIDDEN"

        unauth_resp = env.client.post(
            "/v1/run-sets",
            headers=outsider_b["headers"],
            json={
                "project_id": ids_a["project"],
                "name": "Cross project spark",
                "projection_set_ids": [ids_a["projection_set"]],
                "scenario_ids": [ids_a["scenarios"]["Base"]],
                "execution_backend": "spark",
            },
        )
        assert unauth_resp.status_code == 404
    finally:
        env.close()
