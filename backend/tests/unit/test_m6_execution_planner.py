"""Unit tests for M6-01 unified execution backend protocol, capabilities, and planner."""

import pytest

from app.core.execution.backends import (
    BACKEND_AUTO,
    BACKEND_CPU,
    BACKEND_GPU,
    BACKEND_SPARK,
    BACKEND_SPARK_GPU,
    BackendCapability,
    BackendCapabilityRegistry,
    CpuReferenceBackend,
    ExecutionBackend,
    ExecutionPlanContext,
    ExecutionPlanner,
    ExecutionPlanningError,
    FALLBACK_ALLOW_CPU,
    FALLBACK_STRICT_FAIL,
    probe_hardware_and_runtimes,
    validate_execution_request,
)


def test_cpu_backend_satisfies_protocol():
    backend = CpuReferenceBackend()
    assert isinstance(backend, ExecutionBackend)
    cap = backend.capability()
    assert cap.name == BACKEND_CPU
    assert cap.available is True
    assert cap.accelerated is False
    assert cap.supports_all_products is True


def test_unknown_backend_and_fallback_policy_fail_closed():
    with pytest.raises(ExecutionPlanningError) as exc:
        validate_execution_request("tpu")
    assert exc.value.code == "INVALID_EXECUTION_BACKEND"
    assert exc.value.status_code == 422

    with pytest.raises(ExecutionPlanningError) as exc2:
        validate_execution_request("cpu", "ignore_errors")
    assert exc2.value.code == "INVALID_FALLBACK_POLICY"
    assert exc2.value.status_code == 422


@pytest.mark.parametrize(
    ("backend", "options"),
    [
        ("cpu", {"unknown_key": 1}),
        ("spark", {"spark_partitions": 0}),
        ("spark", {"spark_partitions": 9999}),
        ("spark", {"spark_partitions": True}),
        ("spark", {"spark_master": "   "}),
        ("gpu", {"gpu_device_id": -1}),
        ("gpu", {"gpu_precision": "float16"}),
        ("spark", {"prefer_arrow": "yes"}),
        ("auto", {"batch_size": 0}),
        ("cpu", {"spark_partitions": 4}),
        ("cpu", {"gpu_device_id": 0}),
        ("gpu", {"spark_partitions": 4}),
        ("spark", {"gpu_precision": "float64"}),
        ("auto", ["not-a-dict"]),
    ],
)
def test_invalid_execution_options_fail_validation(backend, options):
    with pytest.raises(ExecutionPlanningError) as exc:
        validate_execution_request(backend, FALLBACK_ALLOW_CPU, options)
    assert exc.value.code == "INVALID_EXECUTION_OPTIONS"
    assert exc.value.status_code == 422


def test_cuda_simulator_env_never_claims_real_gpu_hardware(monkeypatch):
    monkeypatch.setenv("NUMBA_ENABLE_CUDASIM", "1")
    hw = probe_hardware_and_runtimes()
    assert hw["cuda_simulator_env"] is True
    assert hw["cuda_gpu_detected"] is False
    registry = BackendCapabilityRegistry(hardware=hw)
    gpu_cap = registry.get_capability(BACKEND_GPU)
    assert gpu_cap.environment_available is False
    assert gpu_cap.available is False


def test_unavailable_gpu_produces_explicit_fallback_or_strict_rejection():
    planner = ExecutionPlanner(
        BackendCapabilityRegistry(
            hardware={
                "python_version": "3.14.2",
                "platform": "Windows-11",
                "cpu_count": 4,
                "java_detected": False,
                "pyspark_installed": False,
                "pyarrow_installed": False,
                "numba_installed": False,
                "numba_cuda_installed": False,
                "cuda_simulator_env": False,
                "cuda_gpu_detected": False,
                "cuda_device_count": 0,
                "cuda_device_names": [],
            }
        )
    )
    ctx = ExecutionPlanContext(product_code="MYGA_LITE", policy_count=25, horizon_months=480)

    fallback_plan = planner.plan(validate_execution_request(BACKEND_GPU, FALLBACK_ALLOW_CPU), ctx)
    assert fallback_plan.runnable is True
    assert fallback_plan.requested_backend == BACKEND_GPU
    assert fallback_plan.planned_backend == BACKEND_CPU
    assert fallback_plan.actual_backend == BACKEND_CPU
    assert fallback_plan.fallback_used is True
    assert fallback_plan.fallback_reason == "GPU_HARDWARE_UNAVAILABLE"
    assert fallback_plan.accelerated is False

    strict_plan = planner.plan(validate_execution_request(BACKEND_GPU, FALLBACK_STRICT_FAIL), ctx)
    assert strict_plan.runnable is False
    assert strict_plan.accelerated is False
    assert strict_plan.error_code == "GPU_HARDWARE_UNAVAILABLE"


def test_unsupported_models_never_silently_claim_gpu_even_when_gpu_available():
    gpu_ready_registry = BackendCapabilityRegistry(
        hardware={"python_version": "3.14.2", "cpu_count": 8, "cuda_gpu_detected": True},
        capabilities={
            BACKEND_CPU: BackendCapability(
                name=BACKEND_CPU,
                description="CPU",
                implemented=True,
                environment_available=True,
                accelerated=False,
                implementation_stage="M1-M5",
                supports_all_products=True,
            ),
            BACKEND_SPARK: BackendCapability(
                name=BACKEND_SPARK,
                description="Spark",
                implemented=False,
                environment_available=False,
                accelerated=True,
                implementation_stage="M6-02",
                supported_products=("MYGA_LITE",),
            ),
            BACKEND_GPU: BackendCapability(
                name=BACKEND_GPU,
                description="GPU",
                implemented=True,
                environment_available=True,
                accelerated=True,
                implementation_stage="M6-03",
                supports_all_products=False,
                supported_products=("PROSPECTIVE_RESERVE_DISCOUNTING",),
            ),
            BACKEND_SPARK_GPU: BackendCapability(
                name=BACKEND_SPARK_GPU,
                description="Spark+GPU",
                implemented=False,
                environment_available=False,
                accelerated=True,
                implementation_stage="M6-03",
                supported_products=("PROSPECTIVE_RESERVE_DISCOUNTING",),
            ),
        },
    )
    planner = ExecutionPlanner(gpu_ready_registry)
    unsupported_ctx = ExecutionPlanContext(product_code="SPIA_LITE", policy_count=5000, horizon_months=240)

    auto_plan = planner.plan(validate_execution_request(BACKEND_AUTO), unsupported_ctx)
    assert auto_plan.planned_backend == BACKEND_CPU
    assert auto_plan.actual_backend == BACKEND_CPU
    assert auto_plan.accelerated is False

    fb_plan = planner.plan(validate_execution_request(BACKEND_GPU, FALLBACK_ALLOW_CPU), unsupported_ctx)
    assert fb_plan.runnable is True
    assert fb_plan.planned_backend == BACKEND_CPU
    assert fb_plan.actual_backend == BACKEND_CPU
    assert fb_plan.fallback_used is True
    assert fb_plan.fallback_reason == "MODEL_NOT_GPU_SUPPORTED"
    assert fb_plan.accelerated is False

    strict_plan = planner.plan(validate_execution_request(BACKEND_GPU, FALLBACK_STRICT_FAIL), unsupported_ctx)
    assert strict_plan.runnable is False
    assert strict_plan.error_code == "MODEL_NOT_GPU_SUPPORTED"


def test_non_cpu_backend_execution_is_not_faked_in_m6_01():
    registry = BackendCapabilityRegistry()
    with pytest.raises(ExecutionPlanningError) as exc:
        registry.get_backend(BACKEND_SPARK)
    assert exc.value.code in {"BACKEND_NOT_IMPLEMENTED", "SPARK_RUNTIME_UNAVAILABLE"}
    assert exc.value.status_code == 409
