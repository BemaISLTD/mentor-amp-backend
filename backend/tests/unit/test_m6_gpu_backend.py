"""Unit & contract tests for M6-03 GPU backend, model compatibility, fallback semantics, and Section 7 Spark hardening."""

from __future__ import annotations

from datetime import date
import zipfile
from types import SimpleNamespace

import pytest

from app.config import settings
from app.core.execution import backends as exec_backends
from app.core.execution import spark_backend
from app.core.execution.backends import (
    BACKEND_AUTO,
    BACKEND_CPU,
    BACKEND_GPU,
    BACKEND_SPARK,
    BACKEND_SPARK_GPU,
    BackendCapability,
    BackendCapabilityRegistry,
    ExecutionPlanContext,
    ExecutionPlanner,
    FALLBACK_ALLOW_CPU,
    FALLBACK_STRICT_FAIL,
    probe_hardware_and_runtimes,
    validate_execution_request,
)
from app.core.execution.gpu_backend import (
    GPU_ELIGIBLE_PRODUCT_CODE,
    GPU_KERNEL_NAME,
    GPUExecutionBackend,
    GPUKernelExecutionError,
    GPUSessionStartupError,
    SUPPORTED_GPU_FUNCTION_REFS,
    inspect_gpu_model_compatibility,
)
from app.core.execution.spark_backend import (
    _build_app_zip,
    _compute_wave_row_budget,
    _is_loopback_master,
    _resolve_spark_runtime_config,
)
from app.core.projection_engine.run_data import FormulaSpec, PolicyRecord, RunData, ScenarioSpec, VariableSpec
from app.core.valuation import illustrative_reserve
from app.db.models.user import User
from app.services import run_execution_service, run_submission_service
from tests.integration.wp1_support import Env


def _prospective_reserve_config() -> tuple[list[dict], list[dict]]:
    formulas = [
        {"output_variable": "cash_flow", "function_ref": "prospective_reserve:cash_flow", "dependencies": []},
        {"output_variable": "discount_factor", "function_ref": "prospective_reserve:discount_factor", "dependencies": []},
        {
            "output_variable": "pv_cash_flow",
            "function_ref": "prospective_reserve:pv_cash_flow",
            "dependencies": ["cash_flow", "discount_factor"],
        },
    ]
    variables = [
        {"name": "cash_flow", "source": {"type": "formula"}},
        {"name": "discount_factor", "source": {"type": "formula"}},
        {"name": "pv_cash_flow", "source": {"type": "formula"}},
        {"name": "reserve", "source": {"type": "valuation", "method": illustrative_reserve.METHOD}},
    ]
    return formulas, variables


def _mock_gpu_registry() -> BackendCapabilityRegistry:
    hw = {
        "python_version": "3.14.2",
        "cpu_count": 8,
        "gpu_enabled": True,
        "cuda_gpu_detected": True,
        "cuda_device_count": 1,
        "cuda_device_names": ["NVIDIA Test Mock RTX"],
        "cuda_devices": [
            {
                "device_id": 0,
                "name": "NVIDIA Test Mock RTX",
                "compute_capability": [8, 9],
                "total_memory_bytes": 16 * 1024**3,
                "free_memory_bytes": 14 * 1024**3,
            }
        ],
        "cuda_simulator_env": False,
    }
    caps = {
        BACKEND_CPU: BackendCapability(
            name=BACKEND_CPU,
            description="CPU reference",
            implemented=True,
            environment_available=True,
            accelerated=False,
            implementation_stage="M1-M5",
            supports_all_products=True,
        ),
        BACKEND_SPARK: BackendCapability(
            name=BACKEND_SPARK,
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
            description="Mock physical CUDA GPU",
            implemented=True,
            environment_available=True,
            accelerated=True,
            implementation_stage="M6-03",
            supports_all_products=False,
            supported_products=(GPU_ELIGIBLE_PRODUCT_CODE,),
        ),
        BACKEND_SPARK_GPU: BackendCapability(
            name=BACKEND_SPARK_GPU,
            description="Spark+GPU unavailable",
            implemented=False,
            environment_available=False,
            accelerated=True,
            implementation_stage="M6-03",
            supports_all_products=False,
            unavailable_reason="BACKEND_NOT_IMPLEMENTED",
        ),
    }
    return BackendCapabilityRegistry(hardware=hw, capabilities=caps)


def test_gpu_capability_is_implemented_and_simulator_never_fakes_hardware(monkeypatch):
    monkeypatch.setenv("NUMBA_ENABLE_CUDASIM", "1")
    hw = probe_hardware_and_runtimes()
    assert hw["cuda_simulator_env"] is True
    assert hw["cuda_gpu_detected"] is False
    reg = BackendCapabilityRegistry(hardware=hw)
    gpu_cap = reg.get_capability(BACKEND_GPU)
    assert gpu_cap.implemented is True
    assert gpu_cap.environment_available is False
    assert gpu_cap.available is False
    assert gpu_cap.unavailable_reason == "GPU_HARDWARE_UNAVAILABLE"


def test_gpu_model_compatibility_checks():
    formulas, variables = _prospective_reserve_config()
    ok, issues = inspect_gpu_model_compatibility(GPU_ELIGIBLE_PRODUCT_CODE, formulas, variables)
    assert ok is True
    assert issues == []

    # Wrong product fails cleanly
    ok2, issues2 = inspect_gpu_model_compatibility("SPIA_LITE", formulas, variables)
    assert ok2 is False
    assert any("PRODUCT_NOT_GPU_ELIGIBLE" in i or "UNSUPPORTED_GPU_PRODUCT" in i for i in issues2)

    # Unsupported formula ref fails cleanly
    bad_formulas = list(formulas) + [
        {"output_variable": "other", "function_ref": "spia:benefit", "dependencies": []}
    ]
    ok3, issues3 = inspect_gpu_model_compatibility(GPU_ELIGIBLE_PRODUCT_CODE, bad_formulas, variables)
    assert ok3 is False
    assert any("UNSUPPORTED_GPU_FUNCTION_REF" in i for i in issues3)

    # Cross-policy or custom valuation method fails cleanly
    cross_vars = list(variables) + [
        {"name": "pool_agg", "scope": "cross_policy", "source": {"type": "formula"}}
    ]
    ok4, issues4 = inspect_gpu_model_compatibility(GPU_ELIGIBLE_PRODUCT_CODE, formulas, cross_vars)
    assert ok4 is False
    assert any("CROSS_POLICY_VARIABLE" in i for i in issues4)

    bad_val_vars = variables[:-1] + [
        {"name": "reserve", "source": {"type": "valuation", "method": "custom_stochastic"}}
    ]
    ok5, issues5 = inspect_gpu_model_compatibility(GPU_ELIGIBLE_PRODUCT_CODE, formulas, bad_val_vars)
    assert ok5 is False
    assert any("UNSUPPORTED_VALUATION_METHOD" in i for i in issues5)


def test_planner_routes_eligible_gpu_model_and_rejects_spia_with_mock_gpu():
    planner = ExecutionPlanner(_mock_gpu_registry())
    formulas, variables = _prospective_reserve_config()

    eligible_ctx = ExecutionPlanContext(
        product_code=GPU_ELIGIBLE_PRODUCT_CODE,
        policy_count=100,
        horizon_months=120,
        formulas=formulas,
        variables=variables,
    )
    explicit_plan = planner.plan(validate_execution_request(BACKEND_GPU, FALLBACK_STRICT_FAIL), eligible_ctx)
    assert explicit_plan.runnable is True
    assert explicit_plan.planned_backend == BACKEND_GPU
    assert explicit_plan.actual_backend == BACKEND_GPU
    assert explicit_plan.accelerated is True

    auto_plan = planner.plan(validate_execution_request(BACKEND_AUTO, FALLBACK_ALLOW_CPU), eligible_ctx)
    assert auto_plan.runnable is True
    assert auto_plan.planned_backend == BACKEND_GPU
    assert auto_plan.actual_backend == BACKEND_GPU

    spia_ctx = ExecutionPlanContext(product_code="SPIA_LITE", policy_count=100, horizon_months=120)
    spia_strict = planner.plan(validate_execution_request(BACKEND_GPU, FALLBACK_STRICT_FAIL), spia_ctx)
    assert spia_strict.runnable is False
    assert spia_strict.error_code == "MODEL_NOT_GPU_SUPPORTED"

    spia_fb = planner.plan(validate_execution_request(BACKEND_GPU, FALLBACK_ALLOW_CPU), spia_ctx)
    assert spia_fb.runnable is True
    assert spia_fb.actual_backend == BACKEND_CPU
    assert spia_fb.fallback_reason == "MODEL_NOT_GPU_SUPPORTED"


def test_gpu_runtime_validation_blocks_simulator_low_cc_invalid_device_and_vram(monkeypatch):
    # Simulator forbidden unless explicit allow_simulator=True
    monkeypatch.setenv("NUMBA_ENABLE_CUDASIM", "1")
    sim_backend = GPUExecutionBackend(allow_simulator=False)
    with pytest.raises(GPUSessionStartupError) as exc_sim:
        sim_backend._validate_runtime_environment()
    assert exc_sim.value.code == "GPU_HARDWARE_UNAVAILABLE"
    monkeypatch.delenv("NUMBA_ENABLE_CUDASIM", raising=False)

    # Low compute capability
    low_cc_probe = {
        "cuda_simulator_env": False,
        "cuda_gpu_detected": True,
        "cuda_device_count": 1,
        "cuda_devices": [{"device_id": 0, "name": "Legacy", "compute_capability": [3, 5]}],
    }
    b_cc = GPUExecutionBackend(hardware_probe=low_cc_probe)
    with pytest.raises(GPUSessionStartupError) as exc_cc:
        b_cc._validate_runtime_environment()
    assert exc_cc.value.code == "GPU_COMPUTE_CAPABILITY_UNSUPPORTED"

    # Invalid device index
    ok_probe = {
        "cuda_simulator_env": False,
        "cuda_gpu_detected": True,
        "cuda_device_count": 1,
        "cuda_devices": [
            {
                "device_id": 0,
                "name": "Valid",
                "compute_capability": [8, 0],
                "free_memory_bytes": 1024 * 1024,
            }
        ],
    }
    b_dev = GPUExecutionBackend(execution_options={"gpu_device_id": 2}, hardware_probe=ok_probe)
    with pytest.raises(GPUSessionStartupError) as exc_dev:
        b_dev._validate_runtime_environment()
    assert exc_dev.value.code == "GPU_DEVICE_INVALID"

    # Insufficient VRAM for huge horizon
    tiny_vram_probe = {
        "cuda_simulator_env": False,
        "cuda_gpu_detected": True,
        "cuda_device_count": 1,
        "cuda_devices": [
            {
                "device_id": 0,
                "name": "TinyVRAM",
                "compute_capability": [8, 0],
                "free_memory_bytes": 1024,
                "total_memory_bytes": 1024,
            }
        ],
    }
    b_vram = GPUExecutionBackend(hardware_probe=tiny_vram_probe)
    run_data = RunData(
        variables={
            "cash_flow": VariableSpec("cash_flow", "output", "number", source={"type": "formula"}),
            "discount_factor": VariableSpec("discount_factor", "output", "number", source={"type": "formula"}),
            "pv_cash_flow": VariableSpec("pv_cash_flow", "output", "number", source={"type": "formula"}),
            "reserve": VariableSpec(
                "reserve",
                "output",
                "number",
                source={"type": "valuation", "method": illustrative_reserve.METHOD},
            ),
        },
        formulas=[
            FormulaSpec("f1", "Cash Flow", "cash_flow", "prospective_reserve:cash_flow", ()),
            FormulaSpec("f2", "Discount Factor", "discount_factor", "prospective_reserve:discount_factor", ()),
            FormulaSpec("f3", "PV Cash Flow", "pv_cash_flow", "prospective_reserve:pv_cash_flow", ("cash_flow", "discount_factor")),
        ],
        tables={},
        policies=[PolicyRecord("P1", {"policy_id": "P1", "monthly_cash_flow": 100.0})],
        scenario=ScenarioSpec("1", "Base", ()),
        valuation_date=date(2026, 1, 1),
        horizon_months=120,
        output_variables=["cash_flow", "discount_factor", "pv_cash_flow", "reserve"],
        traced_policy_ids=frozenset(),
    )
    with pytest.raises(GPUSessionStartupError) as exc_vram:
        list(b_vram.execute_batches(run_data, run_data.policies))
    assert exc_vram.value.code == "GPU_INSUFFICIENT_MEMORY"


def _seed_gpu_projection_set(env: Env) -> dict:
    from app.core import lifecycle
    from app.core.execution.fingerprints import inforce_fingerprint, INFORCE_FINGERPRINT_SCHEME
    from app.db.models.inforce import InforceFile, InforceRecord
    from app.db.models.project import Project
    from app.db.models.projection import ProjectionSet
    from app.products.prospective_reserve_discounting import config as pr_cfg
    from app.services import projection_set_service
    from scripts import seed_demo

    ids = env.seed("GPU", horizon=12)
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

        rows = [
            {"policy_id": "POL_1", "monthly_cash_flow": 100.0, "cash_flow_growth_rate": 0.0},
            {"policy_id": "POL_2", "monthly_cash_flow": 200.0, "cash_flow_growth_rate": 0.02},
        ]
        inforce = InforceFile(
            project_id=project.id,
            filename="gpu_fallback_inforce.csv",
            file_type="csv",
            row_count=len(rows),
            columns_detected=["policy_id", "monthly_cash_flow", "cash_flow_growth_rate"],
            status=lifecycle.VALIDATED,
            version_label="v1",
            fingerprint=inforce_fingerprint((r["policy_id"], r) for r in rows),
            fingerprint_scheme=INFORCE_FINGERPRINT_SCHEME,
        )
        db.add(inforce)
        db.flush()
        for r in rows:
            db.add(InforceRecord(file_id=inforce.id, policy_id=r["policy_id"], data=r))

        ps = ProjectionSet(
            project_id=project.id,
            name="GPU Fallback Test Set",
            version_label="v1",
            created_by=user.id,
            model_version_id=version.id,
            inforce_file_ids=[inforce.id],
            assumption_table_ids=[],
            factor_table_ids=[],
            scenario_ids=list(ids["scenarios"].values())[:1],
            valuation_date=date(2026, 12, 31),
            horizon_months=12,
            time_step="monthly",
            output_variables=["cash_flow", "discount_factor", "pv_cash_flow", "reserve"],
            trace_scope={"mode": "selected_policies", "policy_ids": ["POL_1"]},
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
            "scenario_ids": list(ids["scenarios"].values())[:1],
        }


def test_worker_startup_fallback_and_mid_execution_strict_failure(tmp_path, monkeypatch):
    env = Env()
    try:
        fix = _seed_gpu_projection_set(env)

        # Scenario 1: Pre-batch GPUSessionStartupError falls back cleanly to CPU when allow_cpu_fallback
        monkeypatch.setattr(exec_backends, "current_capability_registry", _mock_gpu_registry)
        monkeypatch.setattr(run_submission_service, "current_capability_registry", _mock_gpu_registry)
        monkeypatch.setattr(run_execution_service, "current_capability_registry", _mock_gpu_registry)

        def _fail_startup(*_a, **_kw):
            raise GPUSessionStartupError("GPU_DRIVER_INIT_FAILED", "CUDA driver mismatch at startup")

        monkeypatch.setattr(GPUExecutionBackend, "execute_batches", _fail_startup)

        payload = {
            "project_id": fix["project_id"],
            "name": "GPU Startup Fallback Test",
            "projection_set_ids": [fix["projection_set_id"]],
            "scenario_ids": fix["scenario_ids"],
            "execution_backend": BACKEND_GPU,
            "fallback_policy": FALLBACK_ALLOW_CPU,
        }
        with env.Session() as db:
            admin = db.get(User, fix["admin_id"])
            sub1 = run_submission_service.submit_run_set(db, payload, admin)
        env.execute(sub1)
        run_id_1 = sub1["runs"][0]["id"]
        s1 = env.summary(run_id_1)
        assert s1["status"] == "success", s1
        assert s1["execution"]["requested_backend"] == "gpu"
        assert s1["execution"]["actual_backend"] == "cpu"
        assert s1["execution"]["fallback_used"] is True
        assert s1["execution"]["fallback_reason"] == "GPU_DRIVER_INIT_FAILED"

        # Scenario 2: Mid-execution GPUKernelExecutionError NEVER silently falls back to CPU
        def _fail_mid_kernel(*_a, **_kw):
            raise GPUKernelExecutionError("GPU_KERNEL_LAUNCH_FAILED", "PTX illegal instruction after launch")

        monkeypatch.setattr(GPUExecutionBackend, "execute_batches", _fail_mid_kernel)
        with env.Session() as db:
            admin = db.get(User, fix["admin_id"])
            sub2 = run_submission_service.submit_run_set(db, payload, admin)
        env.execute(sub2)
        run_id_2 = sub2["runs"][0]["id"]
        s2 = env.summary(run_id_2)
        assert s2["status"] == "failed"
    finally:
        env.close()


def test_section7_spark_operational_hardening_helpers_and_contention(monkeypatch):
    # 7B: Remote cluster config omits fixed 127.0.0.1 and enables app zip shipping
    assert _is_loopback_master("local[2]") is True
    assert _is_loopback_master("spark://127.0.0.1:7077") is True
    assert _is_loopback_master("spark://spark-master.internal:7077") is False

    monkeypatch.setattr(settings, "spark_driver_host", None)
    monkeypatch.setattr(settings, "spark_driver_bind_address", None)
    monkeypatch.setattr(settings, "spark_worker_python", "/opt/spark-venv/bin/python")
    monkeypatch.setattr(settings, "spark_ship_app_zip", False)

    remote_cfg = _resolve_spark_runtime_config("spark://spark-master.internal:7077")
    assert remote_cfg["loopback_master"] is False
    assert remote_cfg["driver_host"] is None
    assert remote_cfg["driver_bind_address"] is None
    assert remote_cfg["worker_python"] == "/opt/spark-venv/bin/python"
    assert remote_cfg["ship_app_zip"] is True

    # App zip packages app/*.py cleanly
    zip_path = _build_app_zip()
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = set(zf.namelist())
        assert "app/products/registry.py" in names
        assert "app/core/execution/spark_backend.py" in names

    # 7C: Wave row budget caps effective batch size for huge horizons/traces
    monkeypatch.setattr(settings, "spark_max_wave_result_rows", 10_000)
    rd = RunData(
        variables={},
        formulas=[],
        tables={},
        policies=[],
        scenario=ScenarioSpec("1", "B", ()),
        valuation_date=date(2026, 1, 1),
        horizon_months=480,
        output_variables=["a", "b", "c", "d"],
        traced_policy_ids=frozenset({"P1"}),
    )
    eff_batch, est_rows, budget = _compute_wave_row_budget(rd, requested_batch_size=500)
    assert budget == 10_000
    assert est_rows == 480 * (4 + 15)
    assert eff_batch == 10_000 // est_rows
    assert eff_batch < 500

    # 7A: Active Spark session contention raises SparkSessionStartupError instead of stopping active peer master
    with spark_backend._SPARK_SESSION_LOCK:
        prev_runs = spark_backend._SPARK_ACTIVE_RUNS
        prev_master = spark_backend._SPARK_ACTIVE_MASTER
        spark_backend._SPARK_ACTIVE_RUNS = 1
        spark_backend._SPARK_ACTIVE_MASTER = "local[2]"
    try:
        b = spark_backend.SparkExecutionBackend(execution_options={"spark_master": "local[4]"})
        with pytest.raises(spark_backend.SparkSessionStartupError) as exc_c:
            list(b.execute_waves(rd, []))
        assert "SPARK_MASTER_CONTENTION" in str(exc_c.value)
    finally:
        with spark_backend._SPARK_SESSION_LOCK:
            spark_backend._SPARK_ACTIVE_RUNS = prev_runs
            spark_backend._SPARK_ACTIVE_MASTER = prev_master
