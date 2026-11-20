"""End-to-end CUDA kernel execution, numerical parity, error isolation, cancellation, and retry tests (M6-03).

Runs either on:
- Physical NVIDIA CUDA hardware (``cuda.is_available() == True`` without ``NUMBA_ENABLE_CUDASIM=1``), OR
- Explicit CUDA simulator verification (``NUMBA_ENABLE_CUDASIM=1``) where every provenance assertion
  explicitly checks ``hardware_verified is False`` and ``simulator_non_hardware is True``.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
from datetime import date
from typing import Any

import pytest

numba_cuda = pytest.importorskip("numba.cuda")

from app.core import lifecycle  # noqa: E402
from app.core.artifacts import read_attempt_artifact_rows  # noqa: E402
from app.core.execution import backends as exec_backends  # noqa: E402
from app.core.execution.backends import (  # noqa: E402
    BACKEND_CPU,
    BACKEND_GPU,
    BackendCapability,
    BackendCapabilityRegistry,
    FALLBACK_STRICT_FAIL,
    probe_hardware_and_runtimes,
)
from app.core.execution.fingerprints import INFORCE_FINGERPRINT_SCHEME, fingerprint, inforce_fingerprint  # noqa: E402
from app.core.execution.gpu_backend import (  # noqa: E402
    GPU_ELIGIBLE_PRODUCT_CODE,
    GPUExecutionBackend,
    probe_cuda_hardware,
)
from app.db.models.inforce import InforceFile, InforceRecord  # noqa: E402
from app.db.models.project import Project  # noqa: E402
from app.db.models.projection import ProjectionSet  # noqa: E402
from app.db.models.run import Run  # noqa: E402
from app.db.models.run_package import RunPackage  # noqa: E402
from app.db.models.scenario import ScenarioSet, ScenarioTable  # noqa: E402
from app.db.models.user import User  # noqa: E402
from app.products.prospective_reserve_discounting import config as pr_cfg  # noqa: E402
from app.services import (  # noqa: E402
    projection_set_service,
    reconciliation_service,
    run_control_service,
    run_execution_service,
    run_submission_service,
)
from scripts import seed_demo  # noqa: E402
from tests.integration.wp1_support import Env  # noqa: E402

PROVISIONAL_ABS_TOL = 1e-9

_REAL_CUDA_AVAILABLE = bool(probe_cuda_hardware().get("cuda_gpu_detected"))
_CUDASIM_ACTIVE = os.environ.get("NUMBA_ENABLE_CUDASIM") == "1"
if not _REAL_CUDA_AVAILABLE and not _CUDASIM_ACTIVE:
    pytest.skip(
        "Requires physical NVIDIA CUDA hardware or explicit NUMBA_ENABLE_CUDASIM=1 simulator env",
        allow_module_level=True,
    )


def _gpu_test_registry() -> BackendCapabilityRegistry:
    """Use physical GPU capability if present; otherwise explicitly mark simulator non-hardware test registry."""
    if _REAL_CUDA_AVAILABLE:
        return BackendCapabilityRegistry()
    hw = probe_hardware_and_runtimes()
    caps = {
        BACKEND_CPU: BackendCapability(
            name=BACKEND_CPU,
            description="CPU reference path",
            implemented=True,
            environment_available=True,
            accelerated=False,
            implementation_stage="M1-M5",
            supports_all_products=True,
        ),
        "spark": BackendCapability(
            name="spark",
            description="Spark unavailable",
            implemented=True,
            environment_available=False,
            accelerated=True,
            implementation_stage="M6-02",
            supports_all_products=True,
            unavailable_reason="SPARK_RUNTIME_UNAVAILABLE",
        ),
        BACKEND_GPU: BackendCapability(
            name=BACKEND_GPU,
            description="CUDA simulator non-hardware test target",
            implemented=True,
            environment_available=True,
            accelerated=True,
            implementation_stage="M6-03",
            supports_all_products=False,
            supported_products=(GPU_ELIGIBLE_PRODUCT_CODE,),
            runtime_details={"simulator_non_hardware": True},
        ),
        "spark_gpu": BackendCapability(
            name="spark_gpu",
            description="Hybrid Spark+GPU unimplemented",
            implemented=False,
            environment_available=False,
            accelerated=True,
            implementation_stage="M6-03",
            supports_all_products=False,
            unavailable_reason="BACKEND_NOT_IMPLEMENTED",
        ),
    }
    return BackendCapabilityRegistry(hardware=hw, capabilities=caps)


def _patch_gpu_test_environment(monkeypatch):
    monkeypatch.setattr(exec_backends, "current_capability_registry", _gpu_test_registry)
    monkeypatch.setattr(run_submission_service, "current_capability_registry", _gpu_test_registry)
    monkeypatch.setattr(run_execution_service, "current_capability_registry", _gpu_test_registry)
    if not _REAL_CUDA_AVAILABLE:
        orig_init = GPUExecutionBackend.__init__

        def _sim_init(self, *, execution_options=None, allow_simulator=False, hardware_probe=None):
            orig_init(
                self,
                execution_options=execution_options,
                allow_simulator=True,
                hardware_probe=hardware_probe,
            )

        monkeypatch.setattr(GPUExecutionBackend, "__init__", _sim_init)


def _seed_prospective_reserve_fixture(
    env: Env,
    *,
    key: str = "GPU",
    horizon_months: int = 24,
    csv_text: str | None = None,
    with_override: bool = True,
) -> dict[str, Any]:
    ids = env.seed(key, horizon=horizon_months)
    if csv_text is None:
        csv_text = (
            "policy_id,monthly_cash_flow,cash_flow_growth_rate\n"
            "POL_POS,1250.0,0.03\n"
            "POL_ZERO,0.0,0.0\n"
            "POL_NEG,-450.25,0.015\n"
            "POL_DECLINE,800.0,-0.04\n"
        )
    rows = list(csv.DictReader(io.StringIO(csv_text)))

    with env.Session() as db:
        project = db.get(Project, ids["project"])
        user = db.get(User, ids["admin"])
        model, version, group = seed_demo.upsert_model(db, project, user, pr_cfg)
        seed_demo.upsert_variables(db, pr_cfg)
        seed_demo.upsert_definitions(db, version, pr_cfg)
        seed_demo.upsert_formulas(db, version, group, pr_cfg)
        seed_demo.upsert_published_outputs(db, version, pr_cfg)
        checks = seed_demo.validate_model(db, model, version)
        assert checks["status"] == "validated", checks

        inforce = InforceFile(
            project_id=project.id,
            filename=f"prospective_{key}_{horizon_months}.csv",
            file_type="csv",
            row_count=len(rows),
            columns_detected=list(rows[0].keys()),
            status=lifecycle.VALIDATED,
            version_label="v1",
            fingerprint=inforce_fingerprint((r["policy_id"], r) for r in rows),
            fingerprint_scheme=INFORCE_FINGERPRINT_SCHEME,
        )
        db.add(inforce)
        db.flush()
        for r in rows:
            db.add(InforceRecord(file_id=inforce.id, policy_id=r["policy_id"], data=r))

        sc_set = ScenarioSet(project_id=project.id, name=f"GPU Scenarios {key}")
        db.add(sc_set)
        db.flush()
        sc_base = ScenarioTable(
            set_id=sc_set.id,
            scenario_name="Base",
            overrides=[],
            status=lifecycle.VALIDATED,
            version_label="v1",
            fingerprint=fingerprint([]),
        )
        db.add(sc_base)
        scenarios = [sc_base]
        if with_override:
            stress_overrides = [
                {
                    "target_variable": "discount_rate_annual",
                    "operation": "set",
                    "value": -0.01,
                    "applies_from_period": 1,
                    "applies_to_period": horizon_months,
                }
            ]
            sc_stress = ScenarioTable(
                set_id=sc_set.id,
                scenario_name="Stress Negative Rate",
                overrides=stress_overrides,
                status=lifecycle.VALIDATED,
                version_label="v1",
                fingerprint=fingerprint(stress_overrides),
            )
            db.add(sc_stress)
            scenarios.append(sc_stress)
        db.flush()

        ps = ProjectionSet(
            project_id=project.id,
            name=f"GPU Projection Set {key} {horizon_months}m",
            version_label="v1",
            created_by=user.id,
            model_version_id=version.id,
            inforce_file_ids=[inforce.id],
            assumption_table_ids=[],
            factor_table_ids=[],
            scenario_ids=[s.id for s in scenarios],
            valuation_date=date(2026, 12, 31),
            horizon_months=horizon_months,
            time_step="monthly",
            output_variables=["cash_flow", "discount_factor", "pv_cash_flow", "reserve"],
            trace_scope={"mode": "selected_policies", "policy_ids": [r["policy_id"] for r in rows]},
            parameters={},
            status=lifecycle.DRAFT,
        )
        db.add(ps)
        db.commit()
        validated = projection_set_service.validate(db, ps.id)
        assert validated["status"] == "validated", validated["validation"]
        db.commit()
        return {
            "project_id": project.id,
            "admin_id": user.id,
            "projection_set_id": ps.id,
            "scenario_ids": [s.id for s in scenarios],
        }


def _assert_json_semantically_equal(left: Any, right: Any, *, context: str) -> None:
    """Recursively compare parsed JSON payloads using exact equality for metadata and 1e-9 for floats."""
    if isinstance(left, bool) or isinstance(right, bool):
        assert left is right, f"Boolean mismatch at {context}: {left!r} vs {right!r}"
        return
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        assert math.isclose(float(left), float(right), abs_tol=PROVISIONAL_ABS_TOL), (
            f"Numeric mismatch at {context}: {left!r} vs {right!r}"
        )
        return
    if isinstance(left, dict) and isinstance(right, dict):
        assert set(left) == set(right), f"Dict keys mismatch at {context}: {set(left)} vs {set(right)}"
        for key in left:
            _assert_json_semantically_equal(left[key], right[key], context=f"{context}.{key}")
        return
    if isinstance(left, list) and isinstance(right, list):
        assert len(left) == len(right), f"List length mismatch at {context}: {len(left)} vs {len(right)}"
        for idx, (lv, rv) in enumerate(zip(left, right)):
            _assert_json_semantically_equal(lv, rv, context=f"{context}[{idx}]")
        return
    assert left == right, f"Value mismatch at {context}: {left!r} vs {right!r}"


def _assert_trace_tree_equal(cpu_node: dict[str, Any], gpu_node: dict[str, Any], *, path: str) -> None:
    """Compare recursive /v1/runs/{id}/trace trees node-by-node against CPU reference."""
    for field in ("variable", "month", "source_type", "source_table", "formula_id", "error"):
        assert cpu_node.get(field) == gpu_node.get(field), (
            f"Trace tree field '{field}' mismatch at {path}: {cpu_node.get(field)!r} vs {gpu_node.get(field)!r}"
        )
    _assert_json_semantically_equal(cpu_node.get("value"), gpu_node.get("value"), context=f"{path}.value")
    _assert_json_semantically_equal(cpu_node.get("inputs"), gpu_node.get("inputs"), context=f"{path}.inputs")
    cpu_children = sorted(
        cpu_node.get("children") or [],
        key=lambda c: (str(c.get("variable")), int(c.get("month") or 0)),
    )
    gpu_children = sorted(
        gpu_node.get("children") or [],
        key=lambda c: (str(c.get("variable")), int(c.get("month") or 0)),
    )
    assert len(cpu_children) == len(gpu_children), (
        f"Trace tree children count mismatch at {path}: {len(cpu_children)} vs {len(gpu_children)}"
    )
    for c_child, g_child in zip(cpu_children, gpu_children):
        _assert_trace_tree_equal(
            c_child,
            g_child,
            path=f"{path}->{c_child.get('variable')}[m={c_child.get('month')}]",
        )


def test_gpu_vs_cpu_parity_traces_reconciliation_determinism_and_fingerprint(monkeypatch):
    _patch_gpu_test_environment(monkeypatch)
    env = Env()
    try:
        fix = _seed_prospective_reserve_fixture(env, key="P1", horizon_months=24, with_override=True)

        with env.Session() as db:
            admin = db.get(User, fix["admin_id"])
            cpu_sub = run_submission_service.submit_run_set(
                db,
                {
                    "project_id": fix["project_id"],
                    "name": "CPU Ref",
                    "projection_set_ids": [fix["projection_set_id"]],
                    "scenario_ids": fix["scenario_ids"],
                    "execution_backend": BACKEND_CPU,
                },
                admin,
            )
            gpu_sub_1 = run_submission_service.submit_run_set(
                db,
                {
                    "project_id": fix["project_id"],
                    "name": "GPU Run 1",
                    "projection_set_ids": [fix["projection_set_id"]],
                    "scenario_ids": fix["scenario_ids"],
                    "execution_backend": BACKEND_GPU,
                    "fallback_policy": FALLBACK_STRICT_FAIL,
                    "execution_options": {"gpu_device_id": 0, "gpu_precision": "float64", "batch_size": 2},
                },
                admin,
            )
            gpu_sub_2 = run_submission_service.submit_run_set(
                db,
                {
                    "project_id": fix["project_id"],
                    "name": "GPU Run 2",
                    "projection_set_ids": [fix["projection_set_id"]],
                    "scenario_ids": fix["scenario_ids"],
                    "execution_backend": BACKEND_GPU,
                    "fallback_policy": FALLBACK_STRICT_FAIL,
                    "execution_options": {"gpu_device_id": 0, "gpu_precision": "float64", "batch_size": 2},
                },
                admin,
            )

        env.execute(cpu_sub)
        env.execute(gpu_sub_1)
        env.execute(gpu_sub_2)

        cpu_run_ids = [r["id"] for r in cpu_sub["runs"]]
        gpu_run_ids_1 = [r["id"] for r in gpu_sub_1["runs"]]
        gpu_run_ids_2 = [r["id"] for r in gpu_sub_2["runs"]]
        assert len(cpu_run_ids) == len(gpu_run_ids_1) == 2

        for idx, (cpu_run_id, gpu_run_1, gpu_run_2) in enumerate(zip(cpu_run_ids, gpu_run_ids_1, gpu_run_ids_2)):
            assert cpu_sub["runs"][idx]["run_package_fingerprint"] == gpu_sub_1["runs"][idx]["run_package_fingerprint"]
            c_sum = env.summary(cpu_run_id)
            g_sum1 = env.summary(gpu_run_1)
            g_sum2 = env.summary(gpu_run_2)
            assert c_sum["status"] == g_sum1["status"] == g_sum2["status"] == "success"
            assert c_sum["output_row_count"] == g_sum1["output_row_count"] == g_sum2["output_row_count"]
            assert math.isclose(c_sum["headline"]["value"], g_sum1["headline"]["value"], abs_tol=PROVISIONAL_ABS_TOL)
            assert g_sum1["headline"]["value"] == g_sum2["headline"]["value"]

            gpu_view = env.get(f"/runs/{gpu_run_1}").json()
            gpu_meta = gpu_view["execution"]["gpu"]
            assert gpu_meta["hardware_verified"] is _REAL_CUDA_AVAILABLE
            assert gpu_meta["simulator_non_hardware"] is (not _REAL_CUDA_AVAILABLE)
            assert gpu_meta["timing_source"] == (
                "cuda_hardware_events" if _REAL_CUDA_AVAILABLE else "simulator_wall_clock"
            )
            assert gpu_meta["precision"] == "float64"
            assert gpu_meta["batches_completed"] == 2
            for timing_key in (
                "h2d_transfer_seconds",
                "kernel_launch_seconds",
                "kernel_execution_seconds",
                "kernel_sync_wait_seconds",
                "d2h_transfer_seconds",
                "end_to_end_batch_seconds",
            ):
                assert timing_key in gpu_meta and gpu_meta[timing_key] >= 0.0
            assert "vram_free_after_bytes" in gpu_meta

            with env.Session() as db:
                cpu_run = db.get(Run, cpu_run_id)
                gpu_run = db.get(Run, gpu_run_1)
                cpu_pkg = db.query(RunPackage).filter(RunPackage.run_id == cpu_run_id).one()
                gpu_pkg = db.query(RunPackage).filter(RunPackage.run_id == gpu_run_1).one()
                assert cpu_pkg.fingerprint == gpu_pkg.fingerprint
                assert fingerprint(cpu_pkg.package["configuration"]) == fingerprint(
                    gpu_pkg.package["configuration"]
                )

                c_rows = read_attempt_artifact_rows(db, cpu_run_id, "outputs", cpu_run.accepted_attempt_number)
                g_rows1 = read_attempt_artifact_rows(db, gpu_run_1, "outputs", gpu_run.accepted_attempt_number)
                g_rows2 = read_attempt_artifact_rows(
                    db, gpu_run_2, "outputs", db.get(Run, gpu_run_2).accepted_attempt_number
                )
                c_map = {
                    (r["policy_id"], int(r["projection_month"]), r["variable_name"]): float(
                        json.loads(r["value_json"])["value"]
                    )
                    for r in c_rows
                }
                g_map1 = {
                    (r["policy_id"], int(r["projection_month"]), r["variable_name"]): float(
                        json.loads(r["value_json"])["value"]
                    )
                    for r in g_rows1
                }
                g_map2 = {
                    (r["policy_id"], int(r["projection_month"]), r["variable_name"]): float(
                        json.loads(r["value_json"])["value"]
                    )
                    for r in g_rows2
                }
                assert set(c_map) == set(g_map1) == set(g_map2)
                for k, cv in c_map.items():
                    assert math.isclose(cv, g_map1[k], abs_tol=PROVISIONAL_ABS_TOL), f"Mismatch at {k}: {cv} vs {g_map1[k]}"
                    assert g_map1[k] == g_map2[k]

                c_trace = read_attempt_artifact_rows(db, cpu_run_id, "traces", cpu_run.accepted_attempt_number)
                g_trace = read_attempt_artifact_rows(db, gpu_run_1, "traces", gpu_run.accepted_attempt_number)
                assert len(c_trace) == len(g_trace) > 0

                # Deep semantic trace parity (formula_id, source metadata, scenario-adjusted inputs, outputs)
                c_trace_map = {
                    (r["policy_id"], int(r["projection_month"]), r["variable_name"]): r
                    for r in c_trace
                }
                g_trace_map = {
                    (r["policy_id"], int(r["projection_month"]), r["variable_name"]): r
                    for r in g_trace
                }
                assert set(c_trace_map) == set(g_trace_map)
                for tkey, crow in c_trace_map.items():
                    grow = g_trace_map[tkey]
                    assert crow["formula_id"] == grow["formula_id"], f"formula_id mismatch at {tkey}"
                    assert crow["source_type"] == grow["source_type"], f"source_type mismatch at {tkey}"
                    assert crow["source_table"] == grow["source_table"], f"source_table mismatch at {tkey}"
                    assert crow["error_message"] == grow["error_message"], f"error_message mismatch at {tkey}"
                    _assert_json_semantically_equal(
                        json.loads(crow["lookup_keys_json"]),
                        json.loads(grow["lookup_keys_json"]),
                        context=f"trace{tkey}.lookup_keys",
                    )
                    _assert_json_semantically_equal(
                        json.loads(crow["input_values_json"]),
                        json.loads(grow["input_values_json"]),
                        context=f"trace{tkey}.input_values",
                    )
                    _assert_json_semantically_equal(
                        json.loads(crow["output_value_json"]),
                        json.loads(grow["output_value_json"]),
                        context=f"trace{tkey}.output_value",
                    )

                # Confirm scenario-adjusted discount rate (-0.01 in stress scenario idx==1) appears in traces
                expected_rate = 0.045 if idx == 0 else -0.01
                rate_row = g_trace_map[("POL_POS", 1, "discount_rate_annual")]
                assert math.isclose(
                    float(json.loads(rate_row["output_value_json"])["value"]),
                    expected_rate,
                    abs_tol=PROVISIONAL_ABS_TOL,
                )

                expected = reconciliation_service.create_expected_set(
                    db,
                    fix["project_id"],
                    {
                        "name": f"GPU Parity Recon {idx}",
                        "product_code": GPU_ELIGIBLE_PRODUCT_CODE,
                        "timing_convention": "EOM",
                        "rows": [
                            {
                                "policy_id": "POL_POS",
                                "projection_month": 0,
                                "reserve": c_map[("POL_POS", 0, "reserve")],
                            },
                            {
                                "policy_id": "POL_POS",
                                "projection_month": 1,
                                "cash_flow": c_map[("POL_POS", 1, "cash_flow")],
                                "pv_cash_flow": c_map[("POL_POS", 1, "pv_cash_flow")],
                            },
                        ],
                    },
                    fix["admin_id"],
                )
                recon = reconciliation_service.reconcile(
                    db,
                    gpu_run_1,
                    {
                        "expected_result_set_id": expected["id"],
                        "policy_id": "POL_POS",
                        "variables": ["reserve", "cash_flow", "pv_cash_flow"],
                        "abs_tol": PROVISIONAL_ABS_TOL,
                        "rel_tol": 0,
                    },
                    fix["admin_id"],
                )
                assert recon["status"] == "PASS"

            # Compare full recursive trace trees between CPU and GPU across reserve and PV cash flow
            for var_name, month_num in (("reserve", 0), ("reserve", 1), ("pv_cash_flow", 1), ("pv_cash_flow", 24)):
                cpu_tree_resp = env.get(
                    f"/runs/{cpu_run_id}/trace",
                    policy_id="POL_POS",
                    month=month_num,
                    variable=var_name,
                    depth=6,
                )
                gpu_tree_resp = env.get(
                    f"/runs/{gpu_run_1}/trace",
                    policy_id="POL_POS",
                    month=month_num,
                    variable=var_name,
                    depth=6,
                )
                assert cpu_tree_resp.status_code == 200, cpu_tree_resp.text
                assert gpu_tree_resp.status_code == 200, gpu_tree_resp.text
                _assert_trace_tree_equal(
                    cpu_tree_resp.json()["root"],
                    gpu_tree_resp.json()["root"],
                    path=f"scenario[{idx}].{var_name}[m={month_num}]",
                )

            manifest = env.get(f"/runs/{gpu_run_1}/manifest").json()["final_manifest"]["manifest"]
            assert manifest["runtime"]["execution"]["requested_backend"] == BACKEND_GPU
            assert manifest["runtime"]["execution"]["actual_backend"] == BACKEND_GPU
    finally:
        env.close()


def test_gpu_long_and_single_month_horizons_and_per_policy_error_isolation(monkeypatch):
    _patch_gpu_test_environment(monkeypatch)
    env = Env()
    try:
        csv_mixed = (
            "policy_id,monthly_cash_flow,cash_flow_growth_rate\n"
            "POL_OK_1,500.0,0.0\n"
            "POL_MISSING_CF,,0.0\n"
            "POL_BAD_GROWTH,300.0,-1.0\n"
            "POL_OK_2,-150.0,0.02\n"
        )
        fix_1m = _seed_prospective_reserve_fixture(
            env, key="E1", horizon_months=1, csv_text=csv_mixed, with_override=False
        )
        with env.Session() as db:
            admin = db.get(User, fix_1m["admin_id"])
            cpu_sub = run_submission_service.submit_run_set(
                db,
                {
                    "project_id": fix_1m["project_id"],
                    "name": "CPU Mixed",
                    "projection_set_ids": [fix_1m["projection_set_id"]],
                    "scenario_ids": fix_1m["scenario_ids"],
                    "execution_backend": BACKEND_CPU,
                },
                admin,
            )
            gpu_sub = run_submission_service.submit_run_set(
                db,
                {
                    "project_id": fix_1m["project_id"],
                    "name": "GPU Mixed",
                    "projection_set_ids": [fix_1m["projection_set_id"]],
                    "scenario_ids": fix_1m["scenario_ids"],
                    "execution_backend": BACKEND_GPU,
                    "fallback_policy": FALLBACK_STRICT_FAIL,
                },
                admin,
            )
        env.execute(cpu_sub)
        env.execute(gpu_sub)

        cs = env.summary(cpu_sub["runs"][0]["id"])
        gs = env.summary(gpu_sub["runs"][0]["id"])
        assert cs["status"] == gs["status"] == "partial_success"
        assert cs["complete"] is gs["complete"] is False
        assert cs["policy_count"] == gs["policy_count"] == 4
        assert cs["failed_policy_count"] == gs["failed_policy_count"] == 2
        assert math.isclose(cs["headline"]["value"], gs["headline"]["value"], abs_tol=PROVISIONAL_ABS_TOL)

        fix_480 = _seed_prospective_reserve_fixture(
            env, key="E480", horizon_months=480, with_override=False
        )
        with env.Session() as db:
            admin = db.get(User, fix_480["admin_id"])
            cpu_480 = run_submission_service.submit_run_set(
                db,
                {
                    "project_id": fix_480["project_id"],
                    "name": "CPU 480m",
                    "projection_set_ids": [fix_480["projection_set_id"]],
                    "scenario_ids": fix_480["scenario_ids"],
                    "execution_backend": BACKEND_CPU,
                },
                admin,
            )
            gpu_480 = run_submission_service.submit_run_set(
                db,
                {
                    "project_id": fix_480["project_id"],
                    "name": "GPU 480m",
                    "projection_set_ids": [fix_480["projection_set_id"]],
                    "scenario_ids": fix_480["scenario_ids"],
                    "execution_backend": BACKEND_GPU,
                    "fallback_policy": FALLBACK_STRICT_FAIL,
                },
                admin,
            )
        env.execute(cpu_480)
        env.execute(gpu_480)
        assert math.isclose(
            env.summary(cpu_480["runs"][0]["id"])["headline"]["value"],
            env.summary(gpu_480["runs"][0]["id"])["headline"]["value"],
            abs_tol=PROVISIONAL_ABS_TOL,
        )
    finally:
        env.close()


def test_gpu_batch_cancellation_and_retry_without_duplicates(monkeypatch):
    _patch_gpu_test_environment(monkeypatch)
    env = Env()
    try:
        csv_6 = (
            "policy_id,monthly_cash_flow,cash_flow_growth_rate\n"
            + "\n".join(f"POL_{i},{100.0 * (i + 1)},0.01" for i in range(6))
            + "\n"
        )
        fix = _seed_prospective_reserve_fixture(
            env, key="CANCEL", horizon_months=12, csv_text=csv_6, with_override=False
        )

        with env.Session() as db:
            admin = db.get(User, fix["admin_id"])
            sub = run_submission_service.submit_run_set(
                db,
                {
                    "project_id": fix["project_id"],
                    "name": "GPU Cancel Test",
                    "projection_set_ids": [fix["projection_set_id"]],
                    "scenario_ids": fix["scenario_ids"],
                    "execution_backend": BACKEND_GPU,
                    "fallback_policy": FALLBACK_STRICT_FAIL,
                    "execution_options": {"batch_size": 2},
                },
                admin,
            )
        run_id = sub["runs"][0]["id"]

        orig_exec_batches = GPUExecutionBackend.execute_batches

        def _cancel_after_first_batch(self, data, policies, *, should_cancel=None):
            for b_idx, batch_res in enumerate(orig_exec_batches(self, data, policies, should_cancel=should_cancel)):
                yield batch_res
                if b_idx == 0:
                    with env.Session() as cdb:
                        admin = cdb.get(User, fix["admin_id"])
                        run_control_service.cancel_run(cdb, run_id, admin)

        monkeypatch.setattr(GPUExecutionBackend, "execute_batches", _cancel_after_first_batch)
        env.execute(sub)

        cancelled_sum = env.summary(run_id)
        assert cancelled_sum["status"] == "cancelled"
        assert cancelled_sum["results_available"] is False

        monkeypatch.setattr(GPUExecutionBackend, "execute_batches", orig_exec_batches)
        retried_resp = env.client.post(f"/v1/runs/{run_id}/retry", headers=env.admin_headers())
        assert retried_resp.status_code == 202, retried_resp.text
        retried_id = retried_resp.json()["id"]
        assert retried_id != run_id

        retried_sum = env.summary(retried_id)
        retried_view = env.get(f"/runs/{retried_id}").json()
        assert retried_sum["status"] == "success"
        assert retried_sum["results_available"] is True
        assert retried_view["accepted_attempt_number"] == 1
        with env.Session() as db:
            rows = read_attempt_artifact_rows(db, retried_id, "outputs", 1)
            assert len(rows) > 0
            assert len({(r["policy_id"], int(r["projection_month"]), r["variable_name"]) for r in rows}) == len(rows)
    finally:
        env.close()
