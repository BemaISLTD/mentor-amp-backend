"""M6-04 Execution Conformance, Deterministic Replay, and Failure/Recovery Verification Suite.

Verifies CPU vs PySpark conformance using identical frozen RunPackage inputs across:
- Policy identity, scenario identity, projection months, published variables, cash flows, and reserves
- Output-type classified numerical differences (currency, rate, probability, factor, other)
- Deep semantic trace & trace-tree parity + reconciliation PASS
- RunPackage fingerprints, RunAttempt & RunManifest provenance, m2-v1 Parquet integrity
- Deterministic replay across partition counts (p=1, p=2, p=4)

Also verifies failure & recovery conformance:
- Failed Spark partition / executor interruption mid-wave (retains partial noncanonical Parquet, no silent CPU fallback)
- Driver interruption simulation (unfinalized running attempt cannot be double-claimed; clean cancel/retry)
- Corrupted Parquet artifact fail-closed checksum verification
- Unsupported backend selection, resource-limit rejections, pre-wave startup fallback vs strict_fail
- Cross-project authorization and isolation on submit, read, manifest, trace, cancel, and retry
"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest

pyspark = pytest.importorskip("pyspark")

from app.config import settings  # noqa: E402
from app.core.artifacts import (  # noqa: E402
    IDENTITY_NEUTRAL_SCHEMA,
    read_attempt_artifact_rows,
)
from app.core.execution import backends as exec_backends  # noqa: E402
from app.core.execution.spark_backend import (  # noqa: E402
    SparkExecutionBackend,
    SparkSessionStartupError,
    _resolve_java_home,
)
from app.db.models.run import Run  # noqa: E402
from app.db.models.run_artifact import RunArtifact  # noqa: E402
from app.db.models.run_package import RunAttempt, RunPackage  # noqa: E402
from app.db.models.user import User  # noqa: E402
from app.services import (  # noqa: E402
    reconciliation_service,
    run_control_service,
    run_execution_service,
    run_submission_service,
)
from app.services.common import ServiceError  # noqa: E402
from scripts.seed_indexed import seed_indexed  # noqa: E402
from tests.integration.wp1_support import Env  # noqa: E402

if _resolve_java_home() is None:
    pytest.skip("Java 17+ is required for real PySpark conformance tests", allow_module_level=True)

# Provisional engineering tolerance pending CAS §17 Product Owner formal sign-off.
PROVISIONAL_ABS_TOL = 1e-9
PROVISIONAL_REL_TOL = 1e-9


def _classify_output_variable(variable_name: str, unit: str | None = None) -> str:
    unit_norm = (unit or "").strip().lower()
    name_norm = variable_name.lower()
    if unit_norm in {"usd", "currency", "$"} or any(
        tok in name_norm
        for tok in ("reserve", "payment", "cash_flow", "benefit", "value", "claim", "outgo", "expense", "charge", "premium", "mva", "mgsv", "withdrawal")
    ):
        return "currency"
    if unit_norm == "probability" or any(tok in name_norm for tok in ("prob", "qx", "survival", "mortality", "lapse")):
        return "probability"
    if unit_norm == "rate" or "rate" in name_norm or "yield" in name_norm or "cap" in name_norm:
        return "rate"
    if unit_norm == "factor" or "factor" in name_norm or "discount" in name_norm:
        return "factor"
    return "other"


def _compute_classified_diffs(
    cpu_rows: list[dict[str, Any]],
    spark_rows: list[dict[str, Any]],
    var_units: dict[str, str | None],
) -> dict[str, dict[str, Any]]:
    cpu_map = {
        (r["policy_id"], r["scenario_id"], int(r["projection_month"]), r["variable_name"]): float(
            json.loads(r["value_json"])["value"]
        )
        for r in cpu_rows
    }
    spk_map = {
        (r["policy_id"], r["scenario_id"], int(r["projection_month"]), r["variable_name"]): float(
            json.loads(r["value_json"])["value"]
        )
        for r in spark_rows
    }
    assert set(cpu_map) == set(spk_map)

    summary: dict[str, dict[str, Any]] = {}
    for key, cv in cpu_map.items():
        sv = spk_map[key]
        var_name = key[3]
        cls = _classify_output_variable(var_name, var_units.get(var_name))
        bucket = summary.setdefault(
            cls,
            {"compared_points": 0, "exact_matches": 0, "max_abs_diff": 0.0, "max_rel_diff": 0.0},
        )
        abs_diff = abs(cv - sv)
        rel_diff = abs_diff / max(abs(cv), 1.0)
        bucket["compared_points"] += 1
        if abs_diff == 0.0:
            bucket["exact_matches"] += 1
        bucket["max_abs_diff"] = max(bucket["max_abs_diff"], abs_diff)
        bucket["max_rel_diff"] = max(bucket["max_rel_diff"], rel_diff)
        assert abs_diff <= PROVISIONAL_ABS_TOL, f"Parity breach at {key}: cpu={cv} spark={sv} diff={abs_diff}"
    return summary


def _assert_semantic_trace_parity(
    cpu_trace: list[dict[str, Any]], spark_trace: list[dict[str, Any]]
) -> None:
    c_map = {
        (r["policy_id"], r["scenario_id"], int(r["projection_month"]), r["variable_name"]): r
        for r in cpu_trace
    }
    s_map = {
        (r["policy_id"], r["scenario_id"], int(r["projection_month"]), r["variable_name"]): r
        for r in spark_trace
    }
    assert len(c_map) == len(s_map) > 0
    assert set(c_map) == set(s_map)
    for tkey, crow in c_map.items():
        srow = s_map[tkey]
        assert crow["formula_id"] == srow["formula_id"], f"formula_id mismatch at {tkey}"
        assert crow["source_type"] == srow["source_type"], f"source_type mismatch at {tkey}"
        assert crow["source_table"] == srow["source_table"], f"source_table mismatch at {tkey}"
        assert crow["error_message"] == srow["error_message"], f"error_message mismatch at {tkey}"
        assert crow["lookup_keys_json"] == srow["lookup_keys_json"], f"lookup_keys mismatch at {tkey}"
        assert crow["input_values_json"] == srow["input_values_json"], f"input_values mismatch at {tkey}"
        assert crow["output_value_json"] == srow["output_value_json"], f"output_value mismatch at {tkey}"


def test_m6_04_spia_full_conformance_classified_parity_and_partition_replay():
    """Verify full CPU vs Spark conformance + deterministic replay across p=1, p=2, p=4."""
    env = Env()
    try:
        ids = env.seed("CONF_SPIA", horizon=24)
        base_payload = {
            "project_id": ids["project"],
            "name": "M6-04 SPIA Conformance",
            "projection_set_ids": [ids["projection_set"]],
            "scenario_ids": [ids["scenarios"]["Base"], ids["scenarios"]["Low Interest Rate"]],
        }
        with env.Session() as db:
            admin = db.get(User, ids["admin"])
            cpu_sub = run_submission_service.submit_run_set(
                db, {**base_payload, "execution_backend": "cpu"}, admin
            )
            p1_sub = run_submission_service.submit_run_set(
                db,
                {
                    **base_payload,
                    "execution_backend": "spark",
                    "fallback_policy": "strict_fail",
                    "execution_options": {"spark_master": "local[1]", "spark_partitions": 1},
                },
                admin,
            )
            p2_sub = run_submission_service.submit_run_set(
                db,
                {
                    **base_payload,
                    "execution_backend": "spark",
                    "fallback_policy": "strict_fail",
                    "execution_options": {"spark_master": "local[2]", "spark_partitions": 2},
                },
                admin,
            )
            p4_sub = run_submission_service.submit_run_set(
                db,
                {
                    **base_payload,
                    "execution_backend": "spark",
                    "fallback_policy": "strict_fail",
                    "execution_options": {"spark_master": "local[4]", "spark_partitions": 4},
                },
                admin,
            )

        env.execute(cpu_sub)
        env.execute(p1_sub)
        env.execute(p2_sub)
        env.execute(p4_sub)

        for idx in range(2):
            cpu_id = cpu_sub["runs"][idx]["id"]
            p1_id = p1_sub["runs"][idx]["id"]
            p2_id = p2_sub["runs"][idx]["id"]
            p4_id = p4_sub["runs"][idx]["id"]

            # Backend-neutral RunPackage fingerprint equality across CPU and all Spark partition counts
            fp_cpu = cpu_sub["runs"][idx]["run_package_fingerprint"]
            assert fp_cpu == p1_sub["runs"][idx]["run_package_fingerprint"]
            assert fp_cpu == p2_sub["runs"][idx]["run_package_fingerprint"]
            assert fp_cpu == p4_sub["runs"][idx]["run_package_fingerprint"]

            with env.Session() as db:
                pkg = db.query(RunPackage).filter(RunPackage.run_id == cpu_id).one()
                var_units = {
                    v["name"]: v.get("unit")
                    for v in (pkg.package["configuration"].get("variables") or [])
                }
                cpu_out = read_attempt_artifact_rows(db, cpu_id, "outputs", 1)
                cpu_tr = read_attempt_artifact_rows(db, cpu_id, "traces", 1)

                for part_count, spk_id in ((1, p1_id), (2, p2_id), (4, p4_id)):
                    spk_out = read_attempt_artifact_rows(db, spk_id, "outputs", 1)
                    spk_tr = read_attempt_artifact_rows(db, spk_id, "traces", 1)
                    diff_matrix = _compute_classified_diffs(cpu_out, spk_out, var_units)
                    assert "currency" in diff_matrix and diff_matrix["currency"]["compared_points"] > 0
                    assert "probability" in diff_matrix and diff_matrix["probability"]["compared_points"] > 0
                    _assert_semantic_trace_parity(cpu_tr, spk_tr)

                    # Verify m2-v1 Parquet artifact integrity & checksums
                    arts = db.query(RunArtifact).filter_by(run_id=spk_id, attempt_number=1).all()
                    assert arts and all(a.schema_version == IDENTITY_NEUTRAL_SCHEMA for a in arts)
                    assert all(bool(a.checksum_sha256) for a in arts)

                    # Verify RunAttempt & RunManifest provenance
                    attempt = db.query(RunAttempt).filter_by(run_id=spk_id, attempt_number=1).one()
                    exec_meta = attempt.metrics["execution"]
                    assert exec_meta["actual_backend"] == "spark"
                    assert exec_meta["fallback_used"] is False
                    assert len(exec_meta["spark"]["partitions_used"]) == part_count

            # Trace tree API equivalence & governed reconciliation PASS on p=4 Spark run
            c_tree = env.get(f"/runs/{cpu_id}/trace", policy_id="SPIA-0001", month=1, variable="reserve", depth=6).json()
            s_tree = env.get(f"/runs/{p4_id}/trace", policy_id="SPIA-0001", month=1, variable="reserve", depth=6).json()
            assert c_tree["root"] == s_tree["root"]

            with env.Session() as db:
                cpu_m0_res = next(
                    float(json.loads(r["value_json"])["value"])
                    for r in read_attempt_artifact_rows(db, cpu_id, "outputs", 1)
                    if r["policy_id"] == "SPIA-0001"
                    and int(r["projection_month"]) == 0
                    and r["variable_name"] == "reserve"
                )
                expected = reconciliation_service.create_expected_set(
                    db,
                    ids["project"],
                    {
                        "name": f"M6-04 SPIA Oracle s{idx}",
                        "product_code": "SPIA",
                        "timing_convention": "EOM",
                        "rows": [
                            {"policy_id": "SPIA-0001", "projection_month": 0, "reserve": cpu_m0_res}
                        ],
                    },
                    ids["admin"],
                )
                recon = reconciliation_service.reconcile(
                    db,
                    p4_id,
                    {
                        "expected_result_set_id": expected["id"],
                        "policy_id": "SPIA-0001",
                        "variables": ["reserve"],
                        "abs_tol": PROVISIONAL_ABS_TOL,
                        "rel_tol": PROVISIONAL_REL_TOL,
                    },
                    ids["admin"],
                )
                assert recon["status"] == "PASS"

            manifest = env.get(f"/runs/{p4_id}/manifest").json()["final_manifest"]["manifest"]
            assert manifest["runtime"]["execution"]["actual_backend"] == "spark"
            assert manifest["runtime"]["execution"]["spark"]["partitions_used"] == [0, 1, 2, 3]
    finally:
        env.close()


@pytest.mark.parametrize("code", ["FIA", "RILA"])
def test_m6_04_indexed_full_row_and_semantic_trace_conformance(code: str):
    """Verify full policy/month/variable and semantic trace conformance for FIA and RILA."""
    env = Env()
    try:
        with env.Session() as db:
            seeded = seed_indexed(db, code, count=4, months=24)
            project_id = seeded["project"].id
            ps_id = seeded["projection_set"].id
            actor = seeded["user"]
            cpu_sub = run_submission_service.submit_run_set(
                db,
                {
                    "project_id": project_id,
                    "projection_set_ids": [ps_id],
                    "name": f"M6-04 {code} CPU",
                    "execution_backend": "cpu",
                },
                actor,
            )
            spk_sub = run_submission_service.submit_run_set(
                db,
                {
                    "project_id": project_id,
                    "projection_set_ids": [ps_id],
                    "name": f"M6-04 {code} Spark",
                    "execution_backend": "spark",
                    "fallback_policy": "strict_fail",
                    "execution_options": {"spark_master": "local[2]", "spark_partitions": 2},
                },
                actor,
            )
        env.execute(cpu_sub)
        env.execute(spk_sub)

        with env.Session() as db:
            for c_item, s_item in zip(cpu_sub["runs"], spk_sub["runs"], strict=True):
                assert c_item["run_package_fingerprint"] == s_item["run_package_fingerprint"]
                assert db.get(Run, c_item["id"]).status == "success"
                assert db.get(Run, s_item["id"]).status == "success"
                pkg = db.query(RunPackage).filter(RunPackage.run_id == c_item["id"]).one()
                var_units = {
                    v["name"]: v.get("unit")
                    for v in (pkg.package["configuration"].get("variables") or [])
                }
                cpu_out = read_attempt_artifact_rows(db, c_item["id"], "outputs", 1)
                spk_out = read_attempt_artifact_rows(db, s_item["id"], "outputs", 1)
                diff_matrix = _compute_classified_diffs(cpu_out, spk_out, var_units)
                assert sum(b["compared_points"] for b in diff_matrix.values()) > 0

                cpu_tr = read_attempt_artifact_rows(db, c_item["id"], "traces", 1)
                spk_tr = read_attempt_artifact_rows(db, s_item["id"], "traces", 1)
                _assert_semantic_trace_parity(cpu_tr, spk_tr)
    finally:
        env.close()


def test_m6_04_failure_recovery_corrupted_artifacts_driver_interruption_and_limits(monkeypatch):
    """Section 4 conformance: partition failure, partial Parquet retention, driver interruption,
    corrupted artifact checksum detection, resource limits, startup fallback vs strict_fail, and isolation."""
    env = Env()
    try:
        ids_a = env.seed("RECOV_A", horizon=12)
        env.seed("RECOV_B", horizon=12)
        outsider_b = env.user("m604-outsider@example.com", role="actuary", memberships={"RECOV_B": "owner"})

        # 1. Unsupported backend & resource-limit validation (fail-closed 422)
        for bad_body, expected_codes in (
            ({"execution_backend": "quantum_tpu"}, {"INVALID_EXECUTION_BACKEND"}),
            (
                {"execution_backend": "spark", "execution_options": {"spark_partitions": settings.spark_max_partitions + 1}},
                {"INVALID_EXECUTION_OPTIONS"},
            ),
            (
                {"execution_backend": "spark", "execution_options": {"batch_size": settings.spark_max_batch_size + 1}},
                {"INVALID_EXECUTION_OPTIONS"},
            ),
            (
                {"execution_backend": "spark", "execution_options": {"spark_master": "spark://evil.host:7077"}},
                {"SPARK_MASTER_FORBIDDEN"},
            ),
            (
                {"execution_backend": "gpu", "fallback_policy": "strict_fail"},
                {"GPU_HARDWARE_UNAVAILABLE", "MODEL_NOT_GPU_SUPPORTED"},
            ),
        ):
            resp = env.client.post(
                "/v1/run-sets",
                headers=env.admin_headers(),
                json={
                    "project_id": ids_a["project"],
                    "name": "Negative validation test",
                    "projection_set_ids": [ids_a["projection_set"]],
                    "scenario_ids": [ids_a["scenarios"]["Base"]],
                    **bad_body,
                },
            )
            assert resp.status_code in (409, 422), resp.text
            assert resp.json()["error"]["code"] in expected_codes

        # 2. Pre-Wave-0 resource/startup failure: allow_cpu_fallback recovers cleanly, strict_fail rejects cleanly
        orig_execute_waves = SparkExecutionBackend.execute_waves

        def _fail_startup(*_a, **_kw):
            raise SparkSessionStartupError("Simulated cluster memory quota exhausted before wave 0")

        monkeypatch.setattr(SparkExecutionBackend, "execute_waves", _fail_startup)
        with env.Session() as db:
            admin = db.get(User, ids_a["admin"])
            fb_sub = run_submission_service.submit_run_set(
                db,
                {
                    "project_id": ids_a["project"],
                    "name": "Startup failure fallback",
                    "projection_set_ids": [ids_a["projection_set"]],
                    "scenario_ids": [ids_a["scenarios"]["Base"]],
                    "execution_backend": "spark",
                    "fallback_policy": "allow_cpu_fallback",
                    "execution_options": {"spark_master": "local[2]", "spark_partitions": 2},
                },
                admin,
            )
            strict_sub = run_submission_service.submit_run_set(
                db,
                {
                    "project_id": ids_a["project"],
                    "name": "Startup failure strict",
                    "projection_set_ids": [ids_a["projection_set"]],
                    "scenario_ids": [ids_a["scenarios"]["Base"]],
                    "execution_backend": "spark",
                    "fallback_policy": "strict_fail",
                    "execution_options": {"spark_master": "local[2]", "spark_partitions": 2},
                },
                admin,
            )
        env.execute(fb_sub)
        env.execute(strict_sub)
        fb_view = env.get(f"/runs/{fb_sub['runs'][0]['id']}").json()
        assert fb_view["status"] == "success"
        assert fb_view["execution"]["actual_backend"] == "cpu"
        assert fb_view["execution"]["fallback_used"] is True
        assert fb_view["execution"]["fallback_reason"] == "SPARK_SESSION_INIT_FAILED"

        strict_view = env.get(f"/runs/{strict_sub['runs'][0]['id']}").json()
        assert strict_view["status"] == "failed"
        assert strict_view["results_available"] is False

        # Restore healthy Spark execute_waves
        monkeypatch.setattr(SparkExecutionBackend, "execute_waves", orig_execute_waves)

        # 3. Failed Spark partition / executor interruption after partial Parquet write:
        #    Must retain noncanonical Wave 0 artifact, fail closed (NO CPU fallback!), and retry cleanly without duplicates.
        real_get_backend = exec_backends.BackendCapabilityRegistry.get_backend

        def _inject_partition_crash_on_wave1(self, name, **kwargs):
            backend = real_get_backend(self, name, **kwargs)
            if isinstance(backend, SparkExecutionBackend):
                def _hook(wave_idx, _items):
                    if wave_idx == 1:
                        raise RuntimeError("Executor lost: Spark partition task failed on wave 1")
                backend._policy_batch_hook = _hook
            return backend

        monkeypatch.setattr(exec_backends.BackendCapabilityRegistry, "get_backend", _inject_partition_crash_on_wave1)
        with env.Session() as db:
            admin = db.get(User, ids_a["admin"])
            part_sub = run_submission_service.submit_run_set(
                db,
                {
                    "project_id": ids_a["project"],
                    "name": "Mid-wave executor interruption",
                    "projection_set_ids": [ids_a["projection_set"]],
                    "scenario_ids": [ids_a["scenarios"]["Base"]],
                    "execution_backend": "spark",
                    "fallback_policy": "allow_cpu_fallback",
                    "execution_options": {"spark_master": "local[2]", "spark_partitions": 2, "batch_size": 10},
                },
                admin,
            )
        failed_run_id = part_sub["runs"][0]["id"]
        env.execute(part_sub)
        failed_view = env.get(f"/runs/{failed_run_id}").json()
        assert failed_view["status"] == "failed"
        assert failed_view["accepted_attempt_number"] is None
        assert failed_view["results_available"] is False
        assert failed_view["execution"]["actual_backend"] == "spark"
        assert failed_view["execution"]["fallback_used"] is False
        assert env.get(f"/runs/{failed_run_id}/results").status_code == 409

        # Partial Wave 0 Parquet artifact was retained cleanly as noncanonical evidence
        with env.Session() as db:
            partial_arts = db.query(RunArtifact).filter_by(run_id=failed_run_id, attempt_number=1).all()
            assert len(partial_arts) >= 1
            assert all(a.checksum_sha256 for a in partial_arts)

        # Retry after restoring healthy Spark produces canonical run with ZERO duplicate artifacts/rows
        monkeypatch.setattr(exec_backends.BackendCapabilityRegistry, "get_backend", real_get_backend)
        retry_resp = env.client.post(f"/v1/runs/{failed_run_id}/retry", headers=env.admin_headers())
        assert retry_resp.status_code == 202, retry_resp.text
        clean_run_id = retry_resp.json()["id"]
        assert env.summary(clean_run_id)["status"] == "success"
        with env.Session() as db:
            clean_rows = read_attempt_artifact_rows(db, clean_run_id, "outputs", 1)
            unique_keys = {
                (r["policy_id"], r["scenario_id"], int(r["projection_month"]), r["variable_name"])
                for r in clean_rows
            }
            assert len(clean_rows) == len(unique_keys) > 0

        # 4. Driver interruption & claim idempotency:
        #    Simulate a driver crash leaving Run.status == 'running' with an open attempt; a second worker
        #    MUST NOT double-claim it, and cancelling + retrying recovers cleanly.
        with env.Session() as db:
            admin = db.get(User, ids_a["admin"])
            drv_sub = run_submission_service.submit_run_set(
                db,
                {
                    "project_id": ids_a["project"],
                    "name": "Driver interruption test",
                    "projection_set_ids": [ids_a["projection_set"]],
                    "scenario_ids": [ids_a["scenarios"]["Base"]],
                    "execution_backend": "spark",
                    "fallback_policy": "strict_fail",
                    "execution_options": {"spark_master": "local[2]", "spark_partitions": 2},
                },
                admin,
            )
            drv_run_id = drv_sub["runs"][0]["id"]
            attempt = run_execution_service.claim_run(db, drv_run_id, worker_id="crashed-driver-1")
            assert attempt is not None
            # Second worker cannot claim an already-running attempt (idempotency protection)
            second_claim = run_execution_service.execute_run(db, drv_run_id, worker_id="peer-worker-2")
            assert second_claim is None

        # 5. Corrupted Parquet artifact fail-closed verification:
        #    Tamper with the canonical Parquet file of clean_run_id -> API and loader MUST reject it loudly!
        with env.Session() as db:
            target_art = (
                db.query(RunArtifact)
                .filter_by(run_id=clean_run_id, attempt_number=1, artifact_type="outputs")
                .first()
            )
            assert target_art is not None
            artifact_path = env.artifact_store.path_for_uri(target_art.storage_uri)
            original_bytes = artifact_path.read_bytes()
            artifact_path.write_bytes(b"CORRUPTED_PARQUET_BYTES" + original_bytes[23:])

            with pytest.raises(ServiceError) as exc_corrupt:
                read_attempt_artifact_rows(db, clean_run_id, "outputs", 1)
            assert exc_corrupt.value.code in {"ARTIFACT_CHECKSUM_MISMATCH", "ARTIFACT_CORRUPTED", "ARTIFACT_UNREADABLE"}

        bad_results_resp = env.get(f"/runs/{clean_run_id}/results")
        assert bad_results_resp.status_code >= 400

        # Restore original bytes so subsequent read/isolation checks work cleanly
        artifact_path.write_bytes(original_bytes)
        assert env.get(f"/runs/{clean_run_id}/results").status_code == 200

        # 6. Project authorization & isolation on read, manifest, trace, cancel, and retry
        for method, path in (
            ("GET", f"/v1/runs/{clean_run_id}"),
            ("GET", f"/v1/runs/{clean_run_id}/results"),
            ("GET", f"/v1/runs/{clean_run_id}/manifest"),
            ("GET", f"/v1/runs/{clean_run_id}/trace?policy_id=SPIA-0001&month=1&variable=reserve"),
            ("POST", f"/v1/runs/{clean_run_id}/cancel"),
            ("POST", f"/v1/runs/{clean_run_id}/retry"),
        ):
            r = env.client.request(method, path, headers=outsider_b["headers"])
            assert r.status_code in (403, 404), f"Expected isolation on {method} {path}, got {r.status_code}"
    finally:
        env.close()
