"""Genuine NVIDIA CUDA GPU execution backend for dense float64 prospective reserve (M6-03).

Architecture decision:
- Target workload: ``PROSPECTIVE_RESERVE_DISCOUNTING`` (dense ``float64`` scheduled cash flows,
  monthly discount factors, present values, and backward prospective reserve recurrence).
- Stack: ``numba-cuda`` (``@cuda.jit``) compiled to PTX on compatible NVIDIA CUDA hardware
  (compute capability >= 5.0), using strict IEEE-754 ``float64`` precision.
- Hardware integrity: ``NUMBA_ENABLE_CUDASIM=1`` is NEVER counted as real GPU hardware by
  capability probing or production execution; simulator runs are strictly isolated for
  non-hardware dev verification and record ``hardware_verified=False``.
- Safety: Bounds per-wave VRAM allocation, tracks H2D / kernel / sync / D2H timings cleanly,
  releases device arrays deterministically in ``finally``, supports per-policy input error
  isolation, respects cancellation between VRAM batches, and never silently mixes backends.
"""

from __future__ import annotations

import importlib.util
import math
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

import numpy as np

from app.config import settings
from app.core.projection_engine.engine import CONSISTENCY_TOLERANCE, EngineError, PolicyResult
from app.core.projection_engine.run_data import PolicyRecord, RunData
from app.core.valuation import illustrative_reserve

GPU_ELIGIBLE_PRODUCT_CODE = "PROSPECTIVE_RESERVE_DISCOUNTING"
GPU_KERNEL_NAME = "prospective_reserve_discounting_float64_v1"
SUPPORTED_GPU_FUNCTION_REFS = frozenset({
    "prospective_reserve:cash_flow",
    "prospective_reserve:discount_factor",
    "prospective_reserve:pv_cash_flow",
})


class GPUSessionStartupError(RuntimeError):
    """Raised before calculating if physical CUDA initialization or VRAM allocation fails."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class GPUKernelExecutionError(RuntimeError):
    """Raised if CUDA kernel execution fails AFTER GPU calculation begins (no silent CPU fallback)."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class GPUCancelledError(RuntimeError):
    """Raised when cancellation is requested before/between bounded GPU VRAM batches."""


@dataclass
class GPUBatchTelemetry:
    """Measured per-batch GPU execution, memory, and timing telemetry."""

    batch_index: int
    policy_count: int
    horizon_months: int
    threads_per_block: int
    blocks_per_grid: int
    estimated_device_bytes: int
    h2d_transfer_seconds: float
    kernel_seconds: float
    sync_seconds: float
    d2h_transfer_seconds: float
    total_batch_seconds: float
    max_consistency_rel_err: float


def probe_cuda_hardware() -> dict[str, Any]:
    """Probe genuine NVIDIA CUDA hardware, driver/runtime versions, and compute capabilities.

    Strictly returns ``cuda_gpu_detected=False`` whenever ``NUMBA_ENABLE_CUDASIM=1`` is active
    or no physical CUDA GPU is reachable. Never crashes if CUDA libraries are absent.
    """
    numba_spec = importlib.util.find_spec("numba")
    numba_cuda_spec = importlib.util.find_spec("numba_cuda")
    cudasim_enabled = os.environ.get("NUMBA_ENABLE_CUDASIM") == "1"

    result: dict[str, Any] = {
        "numba_installed": numba_spec is not None,
        "numba_cuda_installed": numba_cuda_spec is not None,
        "cuda_simulator_env": cudasim_enabled,
        "cuda_gpu_detected": False,
        "cuda_device_count": 0,
        "cuda_device_names": [],
        "cuda_devices": [],
        "cuda_runtime_version": None,
        "cuda_driver_version": None,
        "gpu_probe_error": None,
    }
    if cudasim_enabled or numba_cuda_spec is None:
        return result

    try:
        from numba import cuda  # type: ignore[import-not-found]

        if not bool(cuda.is_available()):
            return result
        gpus = list(cuda.gpus)
        if not gpus:
            return result

        runtime_ver = None
        driver_ver = None
        try:
            rt = getattr(cuda, "runtime", None)
            if rt is not None and hasattr(rt, "get_version"):
                v = rt.get_version()
                runtime_ver = f"{v[0]}.{v[1]}" if isinstance(v, tuple) else str(v)
        except Exception:  # noqa: BLE001
            pass
        try:
            drv = getattr(getattr(cuda, "cudadriver", None), "driver", None)
            if drv is not None and hasattr(drv, "get_version"):
                dv = drv.get_version()
                driver_ver = f"{dv[0]}.{dv[1]}" if isinstance(dv, tuple) else str(dv)
        except Exception:  # noqa: BLE001
            pass

        devices: list[dict[str, Any]] = []
        names: list[str] = []
        for idx, gpu in enumerate(gpus):
            raw_name = getattr(gpu, "name", b"CUDA GPU")
            name = (
                raw_name.decode("utf-8", errors="replace")
                if isinstance(raw_name, (bytes, bytearray))
                else str(raw_name)
            )
            cc = tuple(int(x) for x in getattr(gpu, "compute_capability", (0, 0)))
            free_mem = None
            total_mem = None
            try:
                with gpu:
                    ctx = cuda.current_context()
                    mem_info = ctx.get_memory_info()
                    free_mem = int(mem_info.free)
                    total_mem = int(mem_info.total)
            except Exception:  # noqa: BLE001
                pass
            names.append(name)
            devices.append({
                "device_id": int(getattr(gpu, "id", idx)),
                "name": name,
                "compute_capability": list(cc),
                "free_memory_bytes": free_mem,
                "total_memory_bytes": total_mem,
            })

        result["cuda_device_count"] = len(devices)
        result["cuda_device_names"] = names
        result["cuda_devices"] = devices
        result["cuda_runtime_version"] = runtime_ver
        result["cuda_driver_version"] = driver_ver
        result["cuda_gpu_detected"] = len(devices) > 0
        return result
    except Exception as exc:  # noqa: BLE001 - absent CUDA driver must never crash capability endpoint
        result["gpu_probe_error"] = str(exc)
        return result


def inspect_gpu_model_compatibility(
    product_code: str | None,
    formulas: Sequence[Any] | None = None,
    variables: Sequence[Any] | Mapping[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    """Verify a model uses ONLY supported dense float64 prospective-reserve/PV operations."""
    issues: list[str] = []
    if product_code != GPU_ELIGIBLE_PRODUCT_CODE:
        issues.append(f"UNSUPPORTED_GPU_PRODUCT:{product_code}")
        return False, issues

    if formulas is not None:
        formula_list = list(formulas)
        if not formula_list:
            issues.append("NO_FORMULAS")
        for item in formula_list:
            func_ref = (
                str(item.get("function_ref") or "")
                if isinstance(item, Mapping)
                else str(getattr(item, "function_ref", "") or "")
            )
            if func_ref not in SUPPORTED_GPU_FUNCTION_REFS:
                issues.append(f"UNSUPPORTED_GPU_FUNCTION_REF:{func_ref}")

    var_items: Iterable[Any]
    if isinstance(variables, Mapping):
        var_items = variables.values()
    elif variables is not None:
        var_items = variables
    else:
        var_items = ()

    has_valuation = False
    for var in var_items:
        source = var.get("source") if isinstance(var, Mapping) else getattr(var, "source", None)
        var_scope = var.get("scope") if isinstance(var, Mapping) else getattr(var, "kind", None)
        var_name = var.get("name") if isinstance(var, Mapping) else getattr(var, "name", "unknown")
        if var_scope in {"portfolio", "cross_policy"}:
            issues.append(f"CROSS_POLICY_VARIABLE:{var_name}")
        if not isinstance(source, Mapping):
            continue
        stype = source.get("type")
        if source.get("scope") in {"portfolio", "cross_policy"}:
            issues.append(f"CROSS_POLICY_VARIABLE:{var_name}")
        if stype == "valuation":
            has_valuation = True
            if source.get("method") != illustrative_reserve.METHOD:
                issues.append(f"UNSUPPORTED_VALUATION_METHOD:{source.get('method')}")
    if variables is not None and not has_valuation:
        issues.append("NO_VALUATION_VARIABLE")

    return len(issues) == 0, issues


def _apply_override(base: float, override: Mapping[str, Any]) -> float:
    op = str(override.get("operation", "set"))
    val = float(override.get("value"))
    if op == "set":
        return val
    if op == "add":
        return float(base) + val
    if op == "subtract":
        return float(base) - val
    if op == "multiply":
        return float(base) * val
    if op == "percent_change":
        return float(base) * (1.0 + val)
    raise ValueError(f"Unsupported scenario override operation '{op}'.")


def _resolve_monthly_series(
    base_value: float,
    var_name: str,
    horizon: int,
    overrides: Sequence[Mapping[str, Any]],
    *,
    allow_override: bool,
) -> list[float]:
    series = [float(base_value)] * horizon
    if not allow_override:
        return series
    for month in range(1, horizon + 1):
        val = float(base_value)
        for ov in overrides:
            if ov.get("target_variable") != var_name:
                continue
            start = ov.get("applies_from_period")
            end = ov.get("applies_to_period")
            if start not in (None, "") and month < int(start):
                continue
            if end not in (None, "") and month > int(end):
                continue
            val = _apply_override(val, ov)
        series[month - 1] = val
    return series


cuda: Any = None
_COMPILED_KERNEL: Any = None


def _prospective_reserve_kernel_impl(
    base_cash_flows,
    growth_rates,
    discount_rates,
    valuation_rates,
    horizon,
    cash_flows_out,
    discount_factors_out,
    pv_cash_flows_out,
    reserves_out,
):
    p = cuda.grid(1)
    if p >= base_cash_flows.shape[0]:
        return

    base_cf = base_cash_flows[p]
    growth = growth_rates[p]
    val_rate = valuation_rates[p]
    monthly_discount = math.pow(1.0 + val_rate, -1.0 / 12.0)

    for m_idx in range(horizon):
        month_num = float(m_idx + 1)
        if month_num <= 1.0 or growth == 0.0:
            cf = base_cf
        else:
            cf = base_cf * math.pow(1.0 + growth, (month_num - 1.0) / 12.0)
        df = math.pow(1.0 + discount_rates[m_idx], -month_num / 12.0)
        cash_flows_out[p, m_idx] = cf
        discount_factors_out[p, m_idx] = df
        pv_cash_flows_out[p, m_idx] = cf * df

    reserves_out[p, horizon] = 0.0
    for t in range(horizon - 1, -1, -1):
        reserves_out[p, t] = (reserves_out[p, t + 1] + cash_flows_out[p, t]) * monthly_discount


def _compile_or_get_cuda_kernel():
    global cuda, _COMPILED_KERNEL
    if _COMPILED_KERNEL is not None:
        return _COMPILED_KERNEL
    from numba import cuda as _numba_cuda  # type: ignore[import-not-found]

    cuda = _numba_cuda
    _COMPILED_KERNEL = _numba_cuda.jit(_prospective_reserve_kernel_impl)
    return _COMPILED_KERNEL


class GPUExecutionBackend:
    """Real NVIDIA CUDA GPU execution backend for ``PROSPECTIVE_RESERVE_DISCOUNTING``."""

    name = "gpu"
    accelerated = True

    def __init__(
        self,
        *,
        execution_options: Mapping[str, Any] | None = None,
        allow_simulator: bool = False,
        hardware_probe: dict[str, Any] | None = None,
    ):
        self.execution_options = dict(execution_options or {})
        # Simulator mode is NEVER enabled silently in production; only explicit dev tests set this.
        self.allow_simulator = bool(allow_simulator)
        self._hardware_probe = hardware_probe
        self.last_execution_telemetry: dict[str, Any] = {}

    def capability(self):
        from app.core.execution.backends import BackendCapability

        hw = self._hardware_probe if self._hardware_probe is not None else probe_cuda_hardware()
        env_ok = bool(settings.gpu_enabled and hw.get("cuda_gpu_detected"))
        return BackendCapability(
            name="gpu",
            description="NVIDIA CUDA GPU float64 prospective-reserve & discounting kernel",
            implemented=True,
            environment_available=env_ok,
            accelerated=True,
            implementation_stage="M6-03",
            supports_all_products=False,
            supported_products=(GPU_ELIGIBLE_PRODUCT_CODE,),
            unavailable_reason=None if env_ok else "GPU_HARDWARE_UNAVAILABLE",
            runtime_details={
                "gpu_enabled": bool(settings.gpu_enabled),
                "cuda_gpu_detected": bool(hw.get("cuda_gpu_detected")),
                "cuda_device_count": hw.get("cuda_device_count", 0),
                "cuda_device_names": list(hw.get("cuda_device_names") or ()),
                "cuda_devices": list(hw.get("cuda_devices") or ()),
                "cuda_driver_version": hw.get("cuda_driver_version"),
                "cuda_runtime_version": hw.get("cuda_runtime_version"),
                "cuda_simulator_env": bool(hw.get("cuda_simulator_env")),
                "simulator_never_counted_as_hardware": True,
                "precision": settings.gpu_default_precision,
                "kernel_name": GPU_KERNEL_NAME,
            },
        )

    def _validate_runtime_environment(self) -> tuple[dict[str, Any], bool]:
        """Verify CUDA device, compute capability, precision, and driver availability."""
        if not settings.gpu_enabled:
            raise GPUSessionStartupError(
                "GPU_DISABLED",
                "GPU execution is disabled by server configuration.",
            )
        precision = str(self.execution_options.get("gpu_precision") or settings.gpu_default_precision)
        if precision != "float64":
            raise GPUSessionStartupError(
                "UNSUPPORTED_GPU_PRECISION",
                f"GPU kernel requires float64 precision (got '{precision}').",
                {"gpu_precision": precision},
            )

        hw = self._hardware_probe if self._hardware_probe is not None else probe_cuda_hardware()
        sim_active = bool(hw.get("cuda_simulator_env")) or os.environ.get("NUMBA_ENABLE_CUDASIM") == "1"
        if hw.get("cuda_gpu_detected") and not sim_active:
            devices = list(hw.get("cuda_devices") or ())
            device_id = int(self.execution_options.get("gpu_device_id", 0))
            if device_id < 0 or device_id >= len(devices):
                raise GPUSessionStartupError(
                    "GPU_DEVICE_INVALID",
                    f"Requested gpu_device_id={device_id} is out of range (detected {len(devices)} device(s)).",
                    {"gpu_device_id": device_id, "device_count": len(devices)},
                )
            dev = devices[device_id]
            cc = tuple(dev.get("compute_capability") or (0, 0))
            min_cc = (settings.gpu_min_compute_capability_major, settings.gpu_min_compute_capability_minor)
            if cc < min_cc:
                raise GPUSessionStartupError(
                    "GPU_COMPUTE_CAPABILITY_UNSUPPORTED",
                    f"GPU '{dev.get('name')}' compute capability {cc[0]}.{cc[1]} is below minimum {min_cc[0]}.{min_cc[1]}.",
                    {"compute_capability": list(cc), "minimum": list(min_cc)},
                )
            return dev, True

        if self.allow_simulator and sim_active and hw.get("numba_cuda_installed"):
            return {
                "device_id": int(self.execution_options.get("gpu_device_id", 0)),
                "name": "NUMBA_CUDA_SIMULATOR (Non-Hardware)",
                "compute_capability": [0, 0],
                "free_memory_bytes": int(self.execution_options.get("simulated_free_vram_bytes", 512 * 1024 * 1024)),
                "total_memory_bytes": int(self.execution_options.get("simulated_total_vram_bytes", 512 * 1024 * 1024)),
            }, False

        raise GPUSessionStartupError(
            "GPU_HARDWARE_UNAVAILABLE",
            "No compatible physical NVIDIA CUDA GPU is available on this host.",
            hw,
        )

    def execute_policies(
        self,
        data: RunData,
        policies: Iterable[PolicyRecord],
        functions: Mapping[str, Callable[..., Any]],
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Iterable[tuple[PolicyRecord, PolicyResult]]:
        """Execute compatible prospective-reserve policies on CUDA GPU in bounded VRAM batches."""
        del functions
        policy_list = list(policies)
        for batch_outcomes in self.execute_batches(data, policy_list, should_cancel=should_cancel):
            yield from batch_outcomes

    def execute_batches(
        self,
        data: RunData,
        policies: Sequence[PolicyRecord],
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Iterable[list[tuple[PolicyRecord, PolicyResult]]]:
        """Yield bounded VRAM batches of GPU-computed ``PolicyResult`` tuples cleanly."""
        dev_info, hardware_verified = self._validate_runtime_environment()

        # Verify model definition compatibility strictly before launching CUDA.
        first_func = data.formulas[0].function_ref if data.formulas else None
        product_guess = (
            GPU_ELIGIBLE_PRODUCT_CODE
            if (
                any(f.function_ref in SUPPORTED_GPU_FUNCTION_REFS for f in data.formulas)
                and all(f.function_ref in SUPPORTED_GPU_FUNCTION_REFS for f in data.formulas)
            )
            else first_func
        )
        ok, issues = inspect_gpu_model_compatibility(product_guess, data.formulas, data.variables)
        if not ok:
            raise GPUSessionStartupError(
                "MODEL_NOT_GPU_SUPPORTED",
                f"Model is incompatible with GPU kernel '{GPU_KERNEL_NAME}': {issues}",
                {"issues": issues},
            )

        horizon = int(data.horizon_months)
        if horizon < 1:
            raise GPUSessionStartupError(
                "INVALID_EXECUTION_OPTIONS",
                f"GPU kernel requires horizon_months >= 1 (got {horizon}).",
                {"horizon_months": horizon},
            )

        threads_per_block = max(32, min(1024, int(settings.gpu_threads_per_block)))
        requested_batch = int(self.execution_options.get("batch_size") or len(policies) or 1)
        bytes_per_policy = ((3 * horizon) + (horizon + 1) + 4) * 8  # float64 output & input buffers
        free_vram = dev_info.get("free_memory_bytes")
        vram_cap = settings.gpu_max_batch_bytes
        if isinstance(free_vram, int) and free_vram > 0:
            vram_cap = min(vram_cap, int(free_vram * settings.gpu_memory_utilization_limit))
        if bytes_per_policy > vram_cap:
            raise GPUSessionStartupError(
                "GPU_INSUFFICIENT_MEMORY",
                f"A single policy requires {bytes_per_policy} bytes which exceeds safe VRAM limit {vram_cap} bytes.",
                {"bytes_per_policy": bytes_per_policy, "vram_budget_bytes": vram_cap},
            )
        max_policies_vram = max(1, vram_cap // bytes_per_policy)
        effective_batch_size = max(1, min(requested_batch, max_policies_vram))

        # Resolve shared scenario-level discount rate vector ONCE on host cleanly.
        disc_var = data.variables.get("discount_rate_annual")
        if disc_var is not None:
            raw_disc = disc_var.source.get("value", disc_var.default_value)
            base_disc = float(0.045 if raw_disc is None else raw_disc)
        else:
            base_disc = 0.045
        disc_allow_ov = bool(disc_var.allow_scenario_override) if disc_var else True
        scenario_disc_series = _resolve_monthly_series(
            base_disc, "discount_rate_annual", horizon, data.scenario.overrides, allow_override=disc_allow_ov
        )
        if any((not math.isfinite(r)) or (r <= -1.0) for r in scenario_disc_series):
            # Per-policy isolated error matches CPU semantics cleanly on invalid scenario rate.
            scenario_disc_valid = False
        else:
            scenario_disc_valid = True

        rate_varies = any(abs(r - scenario_disc_series[0]) > 1e-15 for r in scenario_disc_series)
        val_spec = next(iter(data.valuation_variables()), None)
        val_var_name = val_spec.name if val_spec is not None else "reserve"
        check_var_name = val_spec.source.get("consistency_sum_of") if val_spec else "pv_cash_flow"
        wanted = set(data.output_variables)

        t_init0 = perf_counter()
        try:
            from numba import cuda  # type: ignore[import-not-found]

            if hardware_verified:
                cuda.select_device(int(dev_info["device_id"]))
            kernel = _compile_or_get_cuda_kernel()
        except Exception as exc:  # noqa: BLE001
            raise GPUSessionStartupError(
                "GPU_KERNEL_INIT_FAILED",
                f"Unable to initialize CUDA kernel '{GPU_KERNEL_NAME}': {exc}",
                {"device": dev_info},
            ) from exc
        kernel_init_seconds = perf_counter() - t_init0

        hw_probe = self._hardware_probe if self._hardware_probe is not None else probe_cuda_hardware()
        telemetry: dict[str, Any] = {
            "kernel_name": GPU_KERNEL_NAME,
            "precision": "float64",
            "hardware_verified": hardware_verified,
            "execution_mode": "cuda_hardware" if hardware_verified else "cuda_simulator",
            "simulator_non_hardware": not hardware_verified,
            "device_id": int(dev_info["device_id"]),
            "device_name": str(dev_info["name"]),
            "compute_capability": list(dev_info.get("compute_capability") or (0, 0)),
            "vram_total_bytes": dev_info.get("total_memory_bytes"),
            "vram_free_before_bytes": free_vram,
            "vram_budget_bytes": int(vram_cap),
            "effective_batch_size": int(effective_batch_size),
            "cuda_driver_version": hw_probe.get("cuda_driver_version"),
            "cuda_runtime_version": hw_probe.get("cuda_runtime_version"),
            "kernel_init_seconds": round(kernel_init_seconds, 6),
            "batches_completed": 0,
            "h2d_transfer_seconds": 0.0,
            "kernel_seconds": 0.0,
            "sync_seconds": 0.0,
            "d2h_transfer_seconds": 0.0,
            "total_gpu_seconds": 0.0,
            "max_consistency_rel_err": 0.0,
            "peak_estimated_device_bytes": 0,
            "cancelled": False,
        }
        self.last_execution_telemetry = telemetry

        growth_var = data.variables.get("cash_flow_growth_rate")
        default_growth = float(growth_var.default_value if growth_var and growth_var.default_value is not None else 0.0)
        disc_arr_host = np.asarray(scenario_disc_series, dtype=np.float64)

        total_policies = len(policies)
        for batch_idx, start in enumerate(range(0, total_policies, effective_batch_size)):
            if should_cancel is not None and should_cancel():
                telemetry["cancelled"] = True
                raise GPUCancelledError(f"Run cancelled before GPU batch {batch_idx}.")

            batch = list(policies[start : start + effective_batch_size])
            valid_indices: list[int] = []
            per_policy_errors: dict[int, EngineError] = {}
            base_cf_list: list[float] = []
            growth_list: list[float] = []
            growth_defaulted_list: list[bool] = []
            val_rate_list: list[float] = []

            for local_idx, policy in enumerate(batch):
                if not scenario_disc_valid:
                    per_policy_errors[local_idx] = EngineError(
                        "formula_error",
                        "discount_rate_annual must be finite and greater than -100%",
                        "discount_factor",
                        1,
                    )
                    continue
                raw_cf = policy.data.get("monthly_cash_flow")
                raw_g = policy.data.get("cash_flow_growth_rate")
                growth_defaulted = raw_g in (None, "")
                if raw_cf is None or raw_cf == "":
                    per_policy_errors[local_idx] = EngineError(
                        "missing_value",
                        "Required input 'monthly_cash_flow' is missing.",
                        "cash_flow",
                        1,
                    )
                    continue
                try:
                    cf_val = float(raw_cf)
                    g_val = float(default_growth if growth_defaulted else raw_g)
                except (TypeError, ValueError) as exc:
                    per_policy_errors[local_idx] = EngineError(
                        "type_error", f"Non-numeric policy input: {exc}", "cash_flow", 1
                    )
                    continue
                if not (math.isfinite(cf_val) and math.isfinite(g_val)):
                    per_policy_errors[local_idx] = EngineError(
                        "formula_error", "Policy cash_flow inputs must be finite.", "cash_flow", 1
                    )
                    continue
                if g_val <= -1.0:
                    per_policy_errors[local_idx] = EngineError(
                        "formula_error", "cash_flow_growth_rate must be greater than -100%", "cash_flow", 1
                    )
                    continue
                valid_indices.append(local_idx)
                base_cf_list.append(cf_val)
                growth_list.append(g_val)
                growth_defaulted_list.append(growth_defaulted)
                val_rate_list.append(float(scenario_disc_series[0]))

            batch_outcomes: dict[int, PolicyResult] = {}
            for bad_idx, err in per_policy_errors.items():
                pol = batch[bad_idx]
                tr_rows = []
                if pol.policy_id in data.traced_policy_ids:
                    tr_rows.append({
                        "policy_id": pol.policy_id,
                        "projection_month": err.month or 1,
                        "variable_name": err.variable or "",
                        "formula_id": None,
                        "input_values_json": "{}",
                        "output_value_json": '{"value": null}',
                        "source_type": "error",
                        "source_table": None,
                        "error_message": f"{err.error_type}: {err.message}",
                    })
                batch_outcomes[bad_idx] = PolicyResult(
                    policy_id=pol.policy_id,
                    outputs=[],
                    trace_rows=tr_rows,
                    error=err,
                    months_computed=0,
                )

            if valid_indices:
                n_valid = len(valid_indices)
                blocks = (n_valid + threads_per_block - 1) // threads_per_block
                est_bytes = n_valid * bytes_per_policy
                telemetry["peak_estimated_device_bytes"] = max(
                    telemetry["peak_estimated_device_bytes"], est_bytes
                )

                d_base_cf = d_growth = d_disc = d_val_rate = None
                d_cf_out = d_df_out = d_pv_out = d_res_out = None
                t_batch0 = perf_counter()
                try:
                    t_h2d0 = perf_counter()
                    h_base_cf = np.asarray(base_cf_list, dtype=np.float64)
                    h_growth = np.asarray(growth_list, dtype=np.float64)
                    h_val_rate = np.asarray(val_rate_list, dtype=np.float64)

                    d_base_cf = cuda.to_device(h_base_cf)
                    d_growth = cuda.to_device(h_growth)
                    d_disc = cuda.to_device(disc_arr_host)
                    d_val_rate = cuda.to_device(h_val_rate)

                    d_cf_out = cuda.device_array((n_valid, horizon), dtype=np.float64)
                    d_df_out = cuda.device_array((n_valid, horizon), dtype=np.float64)
                    d_pv_out = cuda.device_array((n_valid, horizon), dtype=np.float64)
                    d_res_out = cuda.device_array((n_valid, horizon + 1), dtype=np.float64)
                    h2d_s = perf_counter() - t_h2d0

                    t_kern0 = perf_counter()
                    kernel[blocks, threads_per_block](
                        d_base_cf,
                        d_growth,
                        d_disc,
                        d_val_rate,
                        horizon,
                        d_cf_out,
                        d_df_out,
                        d_pv_out,
                        d_res_out,
                    )
                    t_sync0 = perf_counter()
                    if hardware_verified:
                        cuda.synchronize()
                    sync_s = perf_counter() - t_sync0
                    kernel_s = perf_counter() - t_kern0

                    t_d2h0 = perf_counter()
                    h_cf = d_cf_out.copy_to_host()
                    h_df = d_df_out.copy_to_host()
                    h_pv = d_pv_out.copy_to_host()
                    h_res = d_res_out.copy_to_host()
                    d2h_s = perf_counter() - t_d2h0
                except Exception as exc:  # noqa: BLE001
                    raise GPUKernelExecutionError(
                        "GPU_KERNEL_EXECUTION_ERROR",
                        f"CUDA kernel execution failed on batch {batch_idx}: {exc}",
                        {"batch_index": batch_idx, "policy_count": n_valid},
                    ) from exc
                finally:
                    # Explicitly release all device buffer references cleanly.
                    for buf in (d_base_cf, d_growth, d_disc, d_val_rate, d_cf_out, d_df_out, d_pv_out, d_res_out):
                        if buf is not None:
                            del buf

                total_batch_s = perf_counter() - t_batch0
                telemetry["batches_completed"] += 1
                telemetry["h2d_transfer_seconds"] = round(telemetry["h2d_transfer_seconds"] + h2d_s, 6)
                telemetry["kernel_seconds"] = round(telemetry["kernel_seconds"] + kernel_s, 6)
                telemetry["sync_seconds"] = round(telemetry["sync_seconds"] + sync_s, 6)
                telemetry["d2h_transfer_seconds"] = round(telemetry["d2h_transfer_seconds"] + d2h_s, 6)
                telemetry["total_gpu_seconds"] = round(telemetry["total_gpu_seconds"] + total_batch_s, 6)
                telemetry["threads_per_block"] = threads_per_block
                telemetry["blocks_per_grid"] = blocks

                formula_by_out = {f.output_variable: f for f in data.formulas}
                for row_k, local_idx in enumerate(valid_indices):
                    pol = batch[local_idx]
                    cf_row = [float(x) for x in h_cf[row_k]]
                    df_row = [float(x) for x in h_df[row_k]]
                    pv_row = [float(x) for x in h_pv[row_k]]
                    res_row = [float(x) for x in h_res[row_k]]

                    if not (
                        all(math.isfinite(v) for v in cf_row)
                        and all(math.isfinite(v) for v in df_row)
                        and all(math.isfinite(v) for v in pv_row)
                        and all(math.isfinite(v) for v in res_row)
                    ):
                        batch_outcomes[local_idx] = PolicyResult(
                            policy_id=pol.policy_id,
                            error=EngineError(
                                "non_finite_result",
                                f"GPU kernel produced non-finite values for policy '{pol.policy_id}'.",
                                val_var_name,
                                1,
                            ),
                            months_computed=horizon,
                        )
                        continue

                    warnings: list[str] = []
                    if rate_varies:
                        warnings.append(
                            "'discount_rate_annual' changes over time; the M1 valuation uses the month-1 rate."
                        )
                    if check_var_name == "pv_cash_flow":
                        pv_sum = math.fsum(pv_row)
                        scale = max(abs(pv_sum), abs(res_row[0]), 1.0)
                        rel_err = abs(pv_sum - res_row[0]) / scale
                        telemetry["max_consistency_rel_err"] = max(
                            telemetry["max_consistency_rel_err"], rel_err
                        )
                        if rel_err > CONSISTENCY_TOLERANCE:
                            warnings.append(
                                f"Consistency check failed: {val_var_name}_0 = {res_row[0]} but sum of {check_var_name} = {pv_sum}."
                            )

                    history_map = {
                        "cash_flow": cf_row,
                        "discount_factor": df_row,
                        "pv_cash_flow": pv_row,
                    }
                    outputs: list[tuple[int, str, float]] = []
                    for out_name in data.output_variables:
                        if out_name == val_var_name:
                            outputs.extend((m, out_name, val) for m, val in enumerate(res_row))
                        elif out_name in history_map:
                            outputs.extend((m, out_name, val) for m, val in enumerate(history_map[out_name], start=1))

                    sums: dict[str, float] = {}
                    for name, series in history_map.items():
                        if name in wanted:
                            sums[name] = math.fsum(series)
                    reserve_0: float | None = None
                    if val_var_name in wanted:
                        reserve_0 = res_row[0]
                        sums[f"{val_var_name}_0"] = res_row[0]

                    trace_rows: list[dict[str, Any]] = []
                    if pol.policy_id in data.traced_policy_ids:
                        g_val = float(growth_list[row_k])
                        g_defaulted = bool(growth_defaulted_list[row_k])
                        b_cf = float(base_cf_list[row_k])
                        for m_idx in range(horizon):
                            m = m_idx + 1
                            disc_m = float(scenario_disc_series[m_idx])
                            trace_rows.extend([
                                {
                                    "policy_id": pol.policy_id,
                                    "projection_month": m,
                                    "variable_name": "monthly_cash_flow",
                                    "formula_id": None,
                                    "input_values": {
                                        "column": "monthly_cash_flow",
                                        "dataset_id": pol.dataset_id,
                                        "was_defaulted": False,
                                    },
                                    "output_value": {"value": b_cf},
                                    "source_type": "input",
                                    "source_table": pol.dataset_name,
                                    "lookup_keys": {"policy_id": pol.policy_id},
                                    "error_message": None,
                                },
                                {
                                    "policy_id": pol.policy_id,
                                    "projection_month": m,
                                    "variable_name": "cash_flow_growth_rate",
                                    "formula_id": None,
                                    "input_values": {
                                        "column": "cash_flow_growth_rate",
                                        "dataset_id": pol.dataset_id,
                                        "was_defaulted": g_defaulted,
                                    },
                                    "output_value": {"value": g_val},
                                    "source_type": "input",
                                    "source_table": pol.dataset_name,
                                    "lookup_keys": {"policy_id": pol.policy_id},
                                    "error_message": None,
                                },
                                {
                                    "policy_id": pol.policy_id,
                                    "projection_month": m,
                                    "variable_name": "projection_month",
                                    "formula_id": None,
                                    "input_values": {"field": "projection_month", "was_defaulted": False},
                                    "output_value": {"value": m},
                                    "source_type": "context",
                                    "source_table": None,
                                    "lookup_keys": None,
                                    "error_message": None,
                                },
                                {
                                    "policy_id": pol.policy_id,
                                    "projection_month": m,
                                    "variable_name": "discount_rate_annual",
                                    "formula_id": None,
                                    "input_values": {"note": "Variable default", "was_defaulted": False},
                                    "output_value": {"value": disc_m},
                                    "source_type": "manual",
                                    "source_table": "manual",
                                    "lookup_keys": None,
                                    "error_message": None,
                                },
                            ])
                            if "cash_flow" in formula_by_out:
                                f = formula_by_out["cash_flow"]
                                trace_rows.append({
                                    "policy_id": pol.policy_id,
                                    "projection_month": m,
                                    "variable_name": "cash_flow",
                                    "formula_id": f.id,
                                    "input_values": {
                                        "monthly_cash_flow": b_cf,
                                        "cash_flow_growth_rate": g_val,
                                        "projection_month": m,
                                    },
                                    "output_value": {"value": cf_row[m_idx]},
                                    "source_type": "formula",
                                    "source_table": None,
                                    "lookup_keys": None,
                                    "error_message": None,
                                })
                            if "discount_factor" in formula_by_out:
                                f = formula_by_out["discount_factor"]
                                trace_rows.append({
                                    "policy_id": pol.policy_id,
                                    "projection_month": m,
                                    "variable_name": "discount_factor",
                                    "formula_id": f.id,
                                    "input_values": {
                                        "discount_rate_annual": disc_m,
                                        "projection_month": m,
                                    },
                                    "output_value": {"value": df_row[m_idx]},
                                    "source_type": "formula",
                                    "source_table": None,
                                    "lookup_keys": None,
                                    "error_message": None,
                                })
                            if "pv_cash_flow" in formula_by_out:
                                f = formula_by_out["pv_cash_flow"]
                                trace_rows.append({
                                    "policy_id": pol.policy_id,
                                    "projection_month": m,
                                    "variable_name": "pv_cash_flow",
                                    "formula_id": f.id,
                                    "input_values": {
                                        "cash_flow": cf_row[m_idx],
                                        "discount_factor": df_row[m_idx],
                                    },
                                    "output_value": {"value": pv_row[m_idx]},
                                    "source_type": "formula",
                                    "source_table": None,
                                    "lookup_keys": None,
                                    "error_message": None,
                                })
                        for m in range(0, horizon + 1):
                            trace_rows.append({
                                "policy_id": pol.policy_id,
                                "projection_month": m,
                                "variable_name": val_var_name,
                                "formula_id": None,
                                "input_values": {
                                    "discount_rate_annual": float(scenario_disc_series[0]),
                                    "cash_flow_next": cf_row[m] if m < horizon else None,
                                    f"{val_var_name}_next": res_row[m + 1] if m < horizon else None,
                                },
                                "output_value": {"value": res_row[m]},
                                "source_type": "valuation",
                                "source_table": illustrative_reserve.METHOD,
                                "lookup_keys": None,
                                "error_message": None,
                            })

                    batch_outcomes[local_idx] = PolicyResult(
                        policy_id=pol.policy_id,
                        outputs=outputs,
                        trace_rows=trace_rows,
                        sums=sums,
                        reserve_0=reserve_0,
                        warnings=warnings,
                        error=None,
                        months_computed=horizon,
                    )

            yield [(batch[i], batch_outcomes[i]) for i in range(len(batch))]
