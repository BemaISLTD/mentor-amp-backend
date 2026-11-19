"""Unified execution backend abstraction, capability registry, and planner (Phase 6 M6-01).

Pure, storage-free coordination layer:
- Defines the ``ExecutionBackend`` protocol and ``CpuReferenceBackend``.
- Validates requested execution backends, fallback policies, and execution options (fail-closed).
- Probes real host capabilities cleanly without mistaking CUDA simulators for GPU hardware.
- Preserves calculation-neutral ``RunPackage`` inputs: backend choices live outside the package hash.
"""

from __future__ import annotations

import importlib.util
import os
import platform
import shutil
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.config import settings
from app.core.execution.gpu_backend import (
    GPUExecutionBackend,
    inspect_gpu_model_compatibility,
    probe_cuda_hardware,
)
from app.core.execution.spark_backend import (
    SparkExecutionBackend,
    SparkSecurityError,
    _resolve_java_home,
    inspect_spark_model_compatibility,
    validate_spark_master,
)
from app.core.projection_engine.engine import PolicyResult, run_policy
from app.core.projection_engine.run_data import PolicyRecord, RunData

BACKEND_AUTO = "auto"
BACKEND_CPU = "cpu"
BACKEND_SPARK = "spark"
BACKEND_GPU = "gpu"
BACKEND_SPARK_GPU = "spark_gpu"

SUPPORTED_BACKEND_MODES = frozenset({
    BACKEND_AUTO,
    BACKEND_CPU,
    BACKEND_SPARK,
    BACKEND_GPU,
    BACKEND_SPARK_GPU,
})
CONCRETE_BACKENDS = frozenset({
    BACKEND_CPU,
    BACKEND_SPARK,
    BACKEND_GPU,
    BACKEND_SPARK_GPU,
})

FALLBACK_ALLOW_CPU = "allow_cpu_fallback"
FALLBACK_STRICT_FAIL = "strict_fail"
SUPPORTED_FALLBACK_POLICIES = frozenset({
    FALLBACK_ALLOW_CPU,
    FALLBACK_STRICT_FAIL,
})

# Explicit model/product capability boundaries:
# - CPU reference supports every registered actuarial model.
# - Distributed Spark (M6-02) targets policy-parallel models without cross-policy state.
# - GPU acceleration (M6-03) targets only explicitly opted-in dense numerical kernels;
#   arbitrary DSL or unsupported products NEVER silently claim GPU execution.
POLICY_PARALLEL_PRODUCTS = frozenset({
    "SPIA_LITE",
    "SPIA_REFERENCE",
    "MYGA_LITE",
    "FIA_LITE",
    "RILA_LITE",
})
GPU_ELIGIBLE_PRODUCTS = frozenset({
    "PROSPECTIVE_RESERVE_DISCOUNTING",
})

ALLOWED_EXECUTION_OPTION_KEYS = frozenset({
    "spark_partitions",
    "spark_master",
    "gpu_device_id",
    "gpu_precision",
    "prefer_arrow",
    "batch_size",
})
ALLOWED_GPU_PRECISIONS = frozenset({"float64"})

DEFAULT_SPARK_MIN_POLICY_MONTHS = 50_000


class ExecutionPlanningError(Exception):
    """Structured fail-closed validation or planning error."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        status_code: int = 422,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = status_code


@dataclass(frozen=True)
class ExecutionRequest:
    requested_backend: str = BACKEND_CPU
    fallback_policy: str = FALLBACK_ALLOW_CPU
    execution_options: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "execution_backend": self.requested_backend,
            "fallback_policy": self.fallback_policy,
            "execution_options": dict(self.execution_options),
        }


@dataclass(frozen=True)
class ExecutionPlanContext:
    product_code: str | None = None
    policy_count: int = 0
    horizon_months: int = 0
    scenario_count: int = 1
    formulas: Sequence[Any] | None = None
    variables: Any = None

    @property
    def policy_months(self) -> int:
        return max(self.policy_count, 0) * max(self.horizon_months, 0) * max(self.scenario_count, 1)


@dataclass(frozen=True)
class BackendCapability:
    name: str
    description: str
    implemented: bool
    environment_available: bool
    accelerated: bool
    implementation_stage: str
    supports_all_products: bool = False
    supported_products: tuple[str, ...] = ()
    unavailable_reason: str | None = None
    runtime_details: dict[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.implemented and self.environment_available

    def supports_product(self, product_code: str | None) -> bool:
        if self.supports_all_products:
            return True
        if not product_code:
            return False
        return product_code in self.supported_products

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "available": self.available,
            "implemented": self.implemented,
            "environment_available": self.environment_available,
            "accelerated": self.accelerated,
            "implementation_stage": self.implementation_stage,
            "supports_all_products": self.supports_all_products,
            "supported_products": list(self.supported_products),
            "unavailable_reason": self.unavailable_reason,
            "runtime_details": dict(self.runtime_details),
        }


@dataclass(frozen=True)
class ExecutionPlan:
    requested_backend: str
    planned_backend: str
    actual_backend: str
    fallback_policy: str
    fallback_used: bool
    fallback_reason: str | None
    fallback_message: str | None
    accelerated: bool
    runnable: bool
    planner_reason: str
    reasons: tuple[str, ...] = ()
    execution_options: dict[str, Any] = field(default_factory=dict)
    hardware: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def as_provenance(self, **extra: Any) -> dict[str, Any]:
        payload = {
            "requested_backend": self.requested_backend,
            "planned_backend": self.planned_backend,
            "actual_backend": self.actual_backend,
            "fallback_policy": self.fallback_policy,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "fallback_message": self.fallback_message,
            "accelerated": self.accelerated,
            "runnable": self.runnable,
            "planner_reason": self.planner_reason,
            "reasons": list(self.reasons),
            "execution_options": dict(self.execution_options),
            "hardware": dict(self.hardware),
        }
        payload.update(extra)
        return payload


@runtime_checkable
class ExecutionBackend(Protocol):
    """Minimal contract every M6 execution backend implements."""

    name: str
    accelerated: bool

    def capability(self) -> BackendCapability: ...

    def execute_policies(
        self,
        data: RunData,
        policies: Iterable[PolicyRecord],
        functions: Mapping[str, Callable[..., Any]],
    ) -> Iterable[tuple[PolicyRecord, PolicyResult]]: ...


class CpuReferenceBackend:
    """In-process pure-Python reference engine delegating directly to ``run_policy``."""

    name = BACKEND_CPU
    accelerated = False

    def __init__(self, policy_runner: Callable[..., PolicyResult] = run_policy):
        self._policy_runner = policy_runner

    def capability(self) -> BackendCapability:
        return BackendCapability(
            name=BACKEND_CPU,
            description="Local CPU reference path",
            implemented=True,
            environment_available=True,
            accelerated=False,
            implementation_stage="M1-M5",
            supports_all_products=True,
            runtime_details={"python_version": platform.python_version()},
        )

    def execute_policies(
        self,
        data: RunData,
        policies: Iterable[PolicyRecord],
        functions: Mapping[str, Callable[..., Any]],
    ) -> Iterable[tuple[PolicyRecord, PolicyResult]]:
        for policy in policies:
            yield policy, self._policy_runner(data, policy, functions)


def probe_hardware_and_runtimes() -> dict[str, Any]:
    """Inspect real CPU, Java/PySpark, and physical CUDA GPU readiness cleanly."""
    java_home_resolved = _resolve_java_home()
    java_detected = java_home_resolved is not None

    pyspark_spec = importlib.util.find_spec("pyspark")
    pyarrow_spec = importlib.util.find_spec("pyarrow")
    cuda_hw = probe_cuda_hardware()

    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(terse=True),
        "cpu_count": os.cpu_count() or 1,
        "spark_enabled": bool(settings.spark_enabled),
        "gpu_enabled": bool(settings.gpu_enabled),
        "java_detected": java_detected,
        "java_home_set": bool(os.environ.get("JAVA_HOME")),
        "pyspark_installed": pyspark_spec is not None,
        "pyarrow_installed": pyarrow_spec is not None,
        "numba_installed": bool(cuda_hw.get("numba_installed")),
        "numba_cuda_installed": bool(cuda_hw.get("numba_cuda_installed")),
        "cuda_simulator_env": bool(cuda_hw.get("cuda_simulator_env")),
        "cuda_gpu_detected": bool(cuda_hw.get("cuda_gpu_detected")),
        "cuda_device_count": int(cuda_hw.get("cuda_device_count") or 0),
        "cuda_device_names": list(cuda_hw.get("cuda_device_names") or ()),
        "cuda_devices": list(cuda_hw.get("cuda_devices") or ()),
        "cuda_driver_version": cuda_hw.get("cuda_driver_version"),
        "cuda_runtime_version": cuda_hw.get("cuda_runtime_version"),
        "gpu_probe_error": cuda_hw.get("probe_error"),
    }


def _validate_int_option(
    key: str, value: Any, *, minimum: int, maximum: int
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExecutionPlanningError(
            "INVALID_EXECUTION_OPTIONS",
            f"Execution option '{key}' must be an integer between {minimum} and {maximum}.",
            {"option": key, "value": value},
        )
    if value < minimum or value > maximum:
        raise ExecutionPlanningError(
            "INVALID_EXECUTION_OPTIONS",
            f"Execution option '{key}' must be between {minimum} and {maximum} (got {value}).",
            {"option": key, "value": value},
        )
    return value


def validate_execution_request(
    requested_backend: Any = None,
    fallback_policy: Any = None,
    execution_options: Any = None,
) -> ExecutionRequest:
    """Validate and normalize execution backend inputs, failing closed on unknown/invalid values."""
    backend = BACKEND_CPU if requested_backend is None else requested_backend
    if not isinstance(backend, str) or backend not in SUPPORTED_BACKEND_MODES:
        raise ExecutionPlanningError(
            "INVALID_EXECUTION_BACKEND",
            f"Unsupported execution_backend '{requested_backend}'. "
            f"Allowed values: {sorted(SUPPORTED_BACKEND_MODES)}.",
            {
                "execution_backend": requested_backend,
                "allowed_backends": sorted(SUPPORTED_BACKEND_MODES),
            },
        )

    policy = FALLBACK_ALLOW_CPU if fallback_policy is None else fallback_policy
    if not isinstance(policy, str) or policy not in SUPPORTED_FALLBACK_POLICIES:
        raise ExecutionPlanningError(
            "INVALID_FALLBACK_POLICY",
            f"Unsupported fallback_policy '{fallback_policy}'. "
            f"Allowed values: {sorted(SUPPORTED_FALLBACK_POLICIES)}.",
            {
                "fallback_policy": fallback_policy,
                "allowed_fallback_policies": sorted(SUPPORTED_FALLBACK_POLICIES),
            },
        )

    if execution_options is None:
        raw_options: dict[str, Any] = {}
    elif not isinstance(execution_options, dict):
        raise ExecutionPlanningError(
            "INVALID_EXECUTION_OPTIONS",
            "Field 'execution_options' must be a JSON object.",
            {"execution_options": execution_options},
        )
    else:
        raw_options = dict(execution_options)

    unknown_keys = sorted(set(raw_options) - ALLOWED_EXECUTION_OPTION_KEYS)
    if unknown_keys:
        raise ExecutionPlanningError(
            "INVALID_EXECUTION_OPTIONS",
            f"Unsupported execution option(s): {unknown_keys}.",
            {
                "unknown_options": unknown_keys,
                "allowed_options": sorted(ALLOWED_EXECUTION_OPTION_KEYS),
            },
        )

    normalized: dict[str, Any] = {}
    if "spark_partitions" in raw_options and raw_options["spark_partitions"] is not None:
        normalized["spark_partitions"] = _validate_int_option(
            "spark_partitions",
            raw_options["spark_partitions"],
            minimum=1,
            maximum=settings.spark_max_partitions,
        )
    if "spark_master" in raw_options and raw_options["spark_master"] is not None:
        try:
            normalized["spark_master"] = validate_spark_master(raw_options["spark_master"])
        except SparkSecurityError as err:
            raise ExecutionPlanningError(err.code, err.message, err.details) from err
    if "gpu_device_id" in raw_options and raw_options["gpu_device_id"] is not None:
        normalized["gpu_device_id"] = _validate_int_option(
            "gpu_device_id", raw_options["gpu_device_id"], minimum=0, maximum=15
        )
    if "gpu_precision" in raw_options and raw_options["gpu_precision"] is not None:
        precision = raw_options["gpu_precision"]
        if not isinstance(precision, str) or precision not in ALLOWED_GPU_PRECISIONS:
            raise ExecutionPlanningError(
                "INVALID_EXECUTION_OPTIONS",
                f"Execution option 'gpu_precision' must be one of {sorted(ALLOWED_GPU_PRECISIONS)}.",
                {"option": "gpu_precision", "value": precision},
            )
        normalized["gpu_precision"] = precision
    if "prefer_arrow" in raw_options and raw_options["prefer_arrow"] is not None:
        prefer_arrow = raw_options["prefer_arrow"]
        if not isinstance(prefer_arrow, bool):
            raise ExecutionPlanningError(
                "INVALID_EXECUTION_OPTIONS",
                "Execution option 'prefer_arrow' must be a boolean.",
                {"option": "prefer_arrow", "value": prefer_arrow},
            )
        normalized["prefer_arrow"] = prefer_arrow
    if "batch_size" in raw_options and raw_options["batch_size"] is not None:
        max_batch = settings.spark_max_batch_size if backend in {BACKEND_SPARK, BACKEND_SPARK_GPU} else 1_000_000
        normalized["batch_size"] = _validate_int_option(
            "batch_size", raw_options["batch_size"], minimum=1, maximum=max_batch
        )

    # Cross-validate backend-specific options so mismatched flags fail loudly.
    spark_only = {"spark_partitions", "spark_master", "prefer_arrow"}
    gpu_only = {"gpu_device_id", "gpu_precision"}
    if backend == BACKEND_CPU and (set(normalized) & (spark_only | gpu_only)):
        raise ExecutionPlanningError(
            "INVALID_EXECUTION_OPTIONS",
            f"Spark/GPU execution options {sorted(set(normalized) & (spark_only | gpu_only))} "
            "cannot be combined with explicit execution_backend='cpu'.",
            {"execution_backend": backend, "conflicting_options": sorted(set(normalized))},
        )
    if backend == BACKEND_GPU and (set(normalized) & spark_only):
        raise ExecutionPlanningError(
            "INVALID_EXECUTION_OPTIONS",
            f"Spark options {sorted(set(normalized) & spark_only)} cannot be passed with execution_backend='gpu'.",
            {"execution_backend": backend, "conflicting_options": sorted(set(normalized) & spark_only)},
        )
    if backend == BACKEND_SPARK and (set(normalized) & gpu_only):
        raise ExecutionPlanningError(
            "INVALID_EXECUTION_OPTIONS",
            f"GPU options {sorted(set(normalized) & gpu_only)} cannot be passed with execution_backend='spark'.",
            {"execution_backend": backend, "conflicting_options": sorted(set(normalized) & gpu_only)},
        )

    return ExecutionRequest(
        requested_backend=backend,
        fallback_policy=policy,
        execution_options=normalized,
    )


class BackendCapabilityRegistry:
    """Authoritative registry of concrete execution backends and live capabilities."""

    def __init__(
        self,
        *,
        hardware: dict[str, Any] | None = None,
        capabilities: Mapping[str, BackendCapability] | None = None,
    ):
        self.hardware = dict(hardware) if hardware is not None else probe_hardware_and_runtimes()
        if capabilities is not None:
            self._capabilities = dict(capabilities)
        else:
            self._capabilities = self._build_default_capabilities(self.hardware)
        self._cpu_backend = CpuReferenceBackend()

    @staticmethod
    def _build_default_capabilities(hw: dict[str, Any]) -> dict[str, BackendCapability]:
        spark_env_ready = bool(
            hw.get("spark_enabled", settings.spark_enabled)
            and hw.get("pyspark_installed")
            and hw.get("java_detected")
        )
        gpu_hw_ready = bool(hw.get("cuda_gpu_detected")) and bool(
            hw.get("gpu_enabled", settings.gpu_enabled)
        )
        return {
            BACKEND_CPU: BackendCapability(
                name=BACKEND_CPU,
                description="Local CPU reference path",
                implemented=True,
                environment_available=True,
                accelerated=False,
                implementation_stage="M1-M5",
                supports_all_products=True,
                runtime_details={"python_version": hw.get("python_version")},
            ),
            BACKEND_SPARK: BackendCapability(
                name=BACKEND_SPARK,
                description="Distributed Apache Spark Classic executor (shared run_policy engine)",
                implemented=True,
                environment_available=spark_env_ready,
                accelerated=True,
                implementation_stage="M6-02",
                supports_all_products=True,
                supported_products=tuple(sorted(POLICY_PARALLEL_PRODUCTS)),
                unavailable_reason=None if spark_env_ready else "SPARK_RUNTIME_UNAVAILABLE",
                runtime_details={
                    "spark_enabled": bool(hw.get("spark_enabled", settings.spark_enabled)),
                    "pyspark_installed": bool(hw.get("pyspark_installed")),
                    "java_detected": bool(hw.get("java_detected")),
                    "pyarrow_installed": bool(hw.get("pyarrow_installed")),
                    "default_master": settings.spark_default_master,
                    "max_partitions": settings.spark_max_partitions,
                },
            ),
            BACKEND_GPU: BackendCapability(
                name=BACKEND_GPU,
                description="NVIDIA CUDA GPU float64 prospective-reserve kernel",
                implemented=True,
                environment_available=gpu_hw_ready,
                accelerated=True,
                implementation_stage="M6-03",
                supports_all_products=False,
                supported_products=tuple(sorted(GPU_ELIGIBLE_PRODUCTS)),
                unavailable_reason=None if gpu_hw_ready else "GPU_HARDWARE_UNAVAILABLE",
                runtime_details={
                    "gpu_enabled": bool(hw.get("gpu_enabled", settings.gpu_enabled)),
                    "cuda_gpu_detected": gpu_hw_ready,
                    "cuda_device_count": hw.get("cuda_device_count", 0),
                    "cuda_device_names": list(hw.get("cuda_device_names") or ()),
                    "cuda_devices": list(hw.get("cuda_devices") or ()),
                    "cuda_driver_version": hw.get("cuda_driver_version"),
                    "cuda_runtime_version": hw.get("cuda_runtime_version"),
                    "cuda_simulator_env": bool(hw.get("cuda_simulator_env")),
                    "simulator_never_counted_as_hardware": True,
                },
            ),
            BACKEND_SPARK_GPU: BackendCapability(
                name=BACKEND_SPARK_GPU,
                description="Hybrid Spark + GPU executor (scheduled for M6-03)",
                implemented=False,
                environment_available=spark_env_ready and gpu_hw_ready,
                accelerated=True,
                implementation_stage="M6-03",
                supports_all_products=False,
                supported_products=tuple(sorted(GPU_ELIGIBLE_PRODUCTS)),
                unavailable_reason=(
                    "BACKEND_NOT_IMPLEMENTED"
                    if (spark_env_ready and gpu_hw_ready)
                    else ("GPU_HARDWARE_UNAVAILABLE" if not gpu_hw_ready else "SPARK_RUNTIME_UNAVAILABLE")
                ),
                runtime_details={
                    "spark_ready": spark_env_ready,
                    "gpu_ready": gpu_hw_ready,
                },
            ),
        }

    def get_capability(self, name: str) -> BackendCapability:
        if name not in self._capabilities:
            raise ExecutionPlanningError(
                "INVALID_EXECUTION_BACKEND",
                f"Unknown concrete backend '{name}'.",
                {"backend": name},
            )
        return self._capabilities[name]

    def get_backend(
        self,
        name: str,
        *,
        policy_runner: Callable[..., PolicyResult] | None = None,
        execution_options: Mapping[str, Any] | None = None,
    ) -> ExecutionBackend:
        if name == BACKEND_CPU:
            if policy_runner is not None:
                return CpuReferenceBackend(policy_runner=policy_runner)
            return self._cpu_backend
        if name == BACKEND_SPARK:
            cap = self.get_capability(name)
            if not cap.available:
                raise ExecutionPlanningError(
                    cap.unavailable_reason or "SPARK_RUNTIME_UNAVAILABLE",
                    f"Backend 'spark' cannot execute runs on this worker "
                    f"(implemented={cap.implemented}, environment_available={cap.environment_available}).",
                    cap.as_dict(),
                    status_code=409,
                )
            return SparkExecutionBackend(execution_options=execution_options)
        if name == BACKEND_GPU:
            cap = self.get_capability(name)
            if not cap.available:
                raise ExecutionPlanningError(
                    cap.unavailable_reason or "GPU_HARDWARE_UNAVAILABLE",
                    f"Backend 'gpu' cannot execute runs on this worker "
                    f"(implemented={cap.implemented}, environment_available={cap.environment_available}).",
                    cap.as_dict(),
                    status_code=409,
                )
            return GPUExecutionBackend(execution_options=execution_options, hardware_probe=self.hardware)
        cap = self.get_capability(name)
        raise ExecutionPlanningError(
            cap.unavailable_reason or "BACKEND_NOT_IMPLEMENTED",
            f"Backend '{name}' cannot execute runs yet (implemented={cap.implemented}, "
            f"environment_available={cap.environment_available}).",
            cap.as_dict(),
            status_code=409,
        )

    def discovery_payload(self) -> dict[str, Any]:
        return {
            "default_backend": BACKEND_CPU,
            "requested_modes": sorted(SUPPORTED_BACKEND_MODES),
            "fallback_policies": sorted(SUPPORTED_FALLBACK_POLICIES),
            "execution_options_schema": {
                "spark_partitions": {"type": "integer", "min": 1, "max": 1024},
                "spark_master": {"type": "string"},
                "gpu_device_id": {"type": "integer", "min": 0, "max": 15},
                "gpu_precision": {"type": "string", "enum": sorted(ALLOWED_GPU_PRECISIONS)},
                "prefer_arrow": {"type": "boolean"},
                "batch_size": {"type": "integer", "min": 1, "max": 1_000_000},
            },
            "backends": {
                name: self._capabilities[name].as_dict()
                for name in (BACKEND_CPU, BACKEND_SPARK, BACKEND_GPU, BACKEND_SPARK_GPU)
            },
            "hardware": dict(self.hardware),
        }


class ExecutionPlanner:
    """Selects and validates the concrete execution backend for a run or preflight plan."""

    def __init__(self, registry: BackendCapabilityRegistry | None = None):
        self.registry = registry or BackendCapabilityRegistry()

    def plan(
        self,
        request: ExecutionRequest,
        context: ExecutionPlanContext,
    ) -> ExecutionPlan:
        hardware = dict(self.registry.hardware)

        if request.requested_backend == BACKEND_CPU:
            return ExecutionPlan(
                requested_backend=BACKEND_CPU,
                planned_backend=BACKEND_CPU,
                actual_backend=BACKEND_CPU,
                fallback_policy=request.fallback_policy,
                fallback_used=False,
                fallback_reason=None,
                fallback_message=None,
                accelerated=False,
                runnable=True,
                planner_reason="EXPLICIT_CPU_SELECTED",
                reasons=("EXPLICIT_CPU_SELECTED",),
                execution_options=dict(request.execution_options),
                hardware=hardware,
            )

        if request.requested_backend == BACKEND_AUTO:
            # Check accelerated backends in preference order ONLY if fully implemented,
            # physically available on the host, AND explicitly supporting this model's dependency graph.
            for candidate in (BACKEND_SPARK_GPU, BACKEND_SPARK, BACKEND_GPU):
                cap = self.registry.get_capability(candidate)
                if not cap.available:
                    continue
                if candidate == BACKEND_SPARK:
                    graph_ok, _issues = inspect_spark_model_compatibility(
                        context.formulas, context.variables
                    )
                    if not (
                        graph_ok
                        and cap.supports_product(context.product_code)
                        and context.policy_months >= settings.spark_min_policy_months
                    ):
                        continue
                elif candidate == BACKEND_GPU:
                    if not cap.supports_product(context.product_code):
                        continue
                    if context.formulas is not None or context.variables is not None:
                        gpu_ok, _issues = inspect_gpu_model_compatibility(
                            context.product_code, context.formulas, context.variables
                        )
                        if not gpu_ok:
                            continue
                elif not cap.supports_product(context.product_code):
                    continue
                return ExecutionPlan(
                    requested_backend=BACKEND_AUTO,
                    planned_backend=candidate,
                    actual_backend=candidate,
                    fallback_policy=request.fallback_policy,
                    fallback_used=False,
                    fallback_reason=None,
                    fallback_message=None,
                    accelerated=cap.accelerated,
                    runnable=True,
                    planner_reason=f"AUTO_SELECTED_{candidate.upper()}",
                    reasons=(f"AUTO_SELECTED_{candidate.upper()}",),
                    execution_options=dict(request.execution_options),
                    hardware=hardware,
                )

            auto_reason = (
                "AUTO_BELOW_SPARK_THRESHOLD"
                if context.policy_months and context.policy_months < settings.spark_min_policy_months
                else "AUTO_CPU_REFERENCE_DEFAULT"
            )
            return ExecutionPlan(
                requested_backend=BACKEND_AUTO,
                planned_backend=BACKEND_CPU,
                actual_backend=BACKEND_CPU,
                fallback_policy=request.fallback_policy,
                fallback_used=False,
                fallback_reason=None,
                fallback_message=None,
                accelerated=False,
                runnable=True,
                planner_reason=auto_reason,
                reasons=(auto_reason,),
                execution_options=dict(request.execution_options),
                hardware=hardware,
            )

        # Explicit accelerated backend selection: spark, gpu, spark_gpu
        cap = self.registry.get_capability(request.requested_backend)
        product_supported = cap.supports_product(context.product_code)
        graph_issues: list[str] = []
        if request.requested_backend == BACKEND_SPARK and product_supported:
            graph_ok, graph_issues = inspect_spark_model_compatibility(
                context.formulas, context.variables
            )
            product_supported = graph_ok
        elif request.requested_backend == BACKEND_GPU and product_supported:
            if context.formulas is not None or context.variables is not None:
                gpu_ok, graph_issues = inspect_gpu_model_compatibility(
                    context.product_code, context.formulas, context.variables
                )
                product_supported = gpu_ok
        reasons: list[str] = []
        if not cap.environment_available:
            reasons.append(
                cap.unavailable_reason
                or (
                    "GPU_HARDWARE_UNAVAILABLE"
                    if request.requested_backend in {BACKEND_GPU, BACKEND_SPARK_GPU}
                    else "SPARK_RUNTIME_UNAVAILABLE"
                )
            )
        if not product_supported:
            reasons.append(
                "MODEL_NOT_GPU_SUPPORTED"
                if request.requested_backend in {BACKEND_GPU, BACKEND_SPARK_GPU}
                else "MODEL_NOT_SPARK_COMPATIBLE"
            )
        if not cap.implemented:
            reasons.append("BACKEND_NOT_IMPLEMENTED")

        if not reasons:
            return ExecutionPlan(
                requested_backend=request.requested_backend,
                planned_backend=request.requested_backend,
                actual_backend=request.requested_backend,
                fallback_policy=request.fallback_policy,
                fallback_used=False,
                fallback_reason=None,
                fallback_message=None,
                accelerated=cap.accelerated,
                runnable=True,
                planner_reason=f"EXPLICIT_{request.requested_backend.upper()}_SELECTED",
                reasons=(f"EXPLICIT_{request.requested_backend.upper()}_SELECTED",),
                execution_options=dict(request.execution_options),
                hardware=hardware,
            )

        # Primary reason precedence:
        # 1. Explicit host hardware/runtime unavailability (when hardware/runtime is absent)
        # 2. Unsupported product/model (never silently claims GPU even on GPU host!)
        # 3. Planned future milestone implementation (M6-02 / M6-03)
        primary_reason = reasons[0]
        human_message = (
            f"Requested backend '{request.requested_backend}' cannot run"
            + (f" model '{context.product_code}'" if context.product_code else "")
            + f": {', '.join(reasons)}."
        )

        if request.fallback_policy == FALLBACK_ALLOW_CPU:
            return ExecutionPlan(
                requested_backend=request.requested_backend,
                planned_backend=BACKEND_CPU,
                actual_backend=BACKEND_CPU,
                fallback_policy=request.fallback_policy,
                fallback_used=True,
                fallback_reason=primary_reason,
                fallback_message=human_message,
                accelerated=False,
                runnable=True,
                planner_reason="CPU_FALLBACK_APPLIED",
                reasons=tuple(reasons),
                execution_options=dict(request.execution_options),
                hardware=hardware,
            )

        return ExecutionPlan(
            requested_backend=request.requested_backend,
            planned_backend=request.requested_backend,
            actual_backend=request.requested_backend,
            fallback_policy=request.fallback_policy,
            fallback_used=False,
            fallback_reason=primary_reason,
            fallback_message=human_message,
            accelerated=False,
            runnable=False,
            planner_reason="STRICT_REJECTED",
            reasons=tuple(reasons),
            execution_options=dict(request.execution_options),
            hardware=hardware,
            error_code=primary_reason,
            error_message=human_message,
        )


def current_capability_registry() -> BackendCapabilityRegistry:
    """Seam for API/service callers and tests to obtain the active capability registry."""
    return BackendCapabilityRegistry()
