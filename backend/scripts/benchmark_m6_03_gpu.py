"""M6-03 CPU vs CUDA GPU benchmark and physical-hardware acceptance harness.

Usage (from ``backend`` directory):
  Mandatory physical NVIDIA CUDA hardware acceptance (exits non-zero if physical GPU absent or simulator set):
    python scripts/benchmark_m6_03_gpu.py --require-physical-gpu

  Optional dev check (exits 0 with acceptance_status=NOT_RUN when no physical GPU exists):
    python scripts/benchmark_m6_03_gpu.py

  Explicit non-hardware CUDA simulator parity check (NEVER accepted as physical GPU evidence):
    NUMBA_ENABLE_CUDASIM=1 python scripts/benchmark_m6_03_gpu.py --allow-simulator
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.execution.backends import CpuReferenceBackend  # noqa: E402
from app.core.execution.gpu_backend import GPUExecutionBackend, probe_cuda_hardware  # noqa: E402
from app.core.formula_engine.formulas import FORMULA_FUNCTIONS  # noqa: E402
from app.core.projection_engine.run_data import (  # noqa: E402
    FormulaSpec,
    PolicyRecord,
    RunData,
    ScenarioSpec,
    VariableSpec,
)
from app.core.valuation import illustrative_reserve  # noqa: E402
from app.products.registry import register_all_products  # noqa: E402

PROVISIONAL_ABS_TOL = 1e-9


def _pkg_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _git_commit() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(BACKEND_ROOT.parent),
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return proc.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _make_run_data(policy_count: int, horizon_months: int) -> RunData:
    policies = [
        PolicyRecord(
            policy_id=f"BENCH_{i:06d}",
            data={
                "policy_id": f"BENCH_{i:06d}",
                "monthly_cash_flow": 500.0 + ((i % 19) - 9) * 45.0,
                "cash_flow_growth_rate": 0.015 * ((i % 5) - 2),
            },
        )
        for i in range(policy_count)
    ]
    return RunData(
        variables={
            "projection_month": VariableSpec(
                "projection_month",
                "input",
                "integer",
                source={"type": "context", "field": "projection_month"},
            ),
            "monthly_cash_flow": VariableSpec(
                "monthly_cash_flow",
                "input",
                "number",
                source={"type": "input", "column": "monthly_cash_flow"},
            ),
            "cash_flow_growth_rate": VariableSpec(
                "cash_flow_growth_rate",
                "input",
                "number",
                source={"type": "input", "column": "cash_flow_growth_rate"},
                default_value=0.0,
                required=False,
            ),
            "discount_rate_annual": VariableSpec(
                "discount_rate_annual",
                "input",
                "number",
                source={"type": "manual", "value": 0.045},
                default_value=0.045,
                allow_scenario_override=True,
            ),
            "cash_flow": VariableSpec("cash_flow", "output", "number", source={"type": "formula"}),
            "discount_factor": VariableSpec("discount_factor", "output", "number", source={"type": "formula"}),
            "pv_cash_flow": VariableSpec("pv_cash_flow", "output", "number", source={"type": "formula"}),
            "reserve": VariableSpec(
                "reserve",
                "output",
                "number",
                source={
                    "type": "valuation",
                    "method": illustrative_reserve.METHOD,
                    "cash_flow": "cash_flow",
                    "rate": "discount_rate_annual",
                    "consistency_sum_of": "pv_cash_flow",
                },
            ),
        },
        formulas=[
            FormulaSpec(
                "f1",
                "Cash Flow",
                "cash_flow",
                "prospective_reserve:cash_flow",
                ("monthly_cash_flow", "cash_flow_growth_rate", "projection_month"),
            ),
            FormulaSpec(
                "f2",
                "Discount Factor",
                "discount_factor",
                "prospective_reserve:discount_factor",
                ("discount_rate_annual", "projection_month"),
            ),
            FormulaSpec(
                "f3",
                "PV Cash Flow",
                "pv_cash_flow",
                "prospective_reserve:pv_cash_flow",
                ("cash_flow", "discount_factor"),
            ),
        ],
        tables={},
        policies=policies,
        scenario=ScenarioSpec("1", "Benchmark Base", ()),
        valuation_date=date(2026, 1, 1),
        horizon_months=horizon_months,
        output_variables=["cash_flow", "discount_factor", "pv_cash_flow", "reserve"],
        traced_policy_ids=frozenset(),
    )


def _compare_full_outputs(cpu_res: list[Any], gpu_res: list[Any]) -> dict[str, Any]:
    max_output_abs_diff = 0.0
    max_reserve_0_abs_diff = 0.0
    total_points = 0
    for c, g in zip(cpu_res, gpu_res):
        if c.error is not None or g.error is not None:
            raise RuntimeError(f"Parity benchmark error: cpu={c.error} gpu={g.error}")
        r_diff = abs(float(c.reserve_0) - float(g.reserve_0))
        max_reserve_0_abs_diff = max(max_reserve_0_abs_diff, r_diff)
        c_map = {(m, v): float(val) for m, v, val in c.outputs}
        g_map = {(m, v): float(val) for m, v, val in g.outputs}
        if set(c_map) != set(g_map):
            raise RuntimeError(f"Output key mismatch for policy {c.policy_id}")
        for key, cv in c_map.items():
            diff = abs(cv - g_map[key])
            max_output_abs_diff = max(max_output_abs_diff, diff)
            total_points += 1
    return {
        "compared_output_points": total_points,
        "max_reserve_0_abs_diff": max_reserve_0_abs_diff,
        "max_output_abs_diff": max_output_abs_diff,
        "within_provisional_tol": max_output_abs_diff <= PROVISIONAL_ABS_TOL,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark CPU vs CUDA GPU prospective reserve calculation and verify hardware acceptance."
    )
    parser.add_argument(
        "--require-physical-gpu",
        action="store_true",
        help="Mandatory physical NVIDIA hardware acceptance gate: exits non-zero if physical CUDA GPU is absent or simulator is active.",
    )
    parser.add_argument(
        "--allow-simulator",
        action="store_true",
        help="Allow NUMBA_ENABLE_CUDASIM=1 simulator mode (forbidden with --require-physical-gpu; never marks hardware acceptance PASS).",
    )
    args = parser.parse_args(argv)

    register_all_products()
    hw = probe_cuda_hardware()
    sim_env = os.environ.get("NUMBA_ENABLE_CUDASIM") == "1"
    env_meta = {
        "source_commit": _git_commit(),
        "python_version": platform.python_version(),
        "platform": platform.platform(terse=True),
        "numba_version": _pkg_version("numba"),
        "numba_cuda_version": _pkg_version("numba-cuda"),
        "cuda_runtime_package_version": _pkg_version("nvidia-cuda-runtime"),
    }

    if args.require_physical_gpu:
        if args.allow_simulator or sim_env:
            print(
                json.dumps(
                    {
                        "acceptance_status": "FAIL",
                        "failure_reason": "SIMULATOR_FORBIDDEN_FOR_PHYSICAL_ACCEPTANCE",
                        "physical_gpu_acceptance": "FAIL",
                        "environment": env_meta,
                        "hardware_probe": hw,
                    },
                    indent=2,
                )
            )
            return 2
        if not hw.get("cuda_gpu_detected"):
            print(
                json.dumps(
                    {
                        "acceptance_status": "FAIL",
                        "failure_reason": "PHYSICAL_GPU_UNAVAILABLE",
                        "physical_gpu_acceptance": "FAIL",
                        "environment": env_meta,
                        "hardware_probe": hw,
                    },
                    indent=2,
                )
            )
            return 2

    use_sim = bool(args.allow_simulator and sim_env and not hw.get("cuda_gpu_detected"))
    if not hw.get("cuda_gpu_detected") and not use_sim:
        print(
            json.dumps(
                {
                    "acceptance_status": "NOT_RUN",
                    "physical_gpu_acceptance": "NOT_RUN",
                    "status": "SKIPPED_NO_PHYSICAL_CUDA",
                    "environment": env_meta,
                    "hardware_probe": hw,
                    "note": "No physical NVIDIA CUDA hardware detected. Use --require-physical-gpu on a CUDA host for mandatory hardware acceptance, or --allow-simulator with NUMBA_ENABLE_CUDASIM=1 for non-hardware parity verification.",
                },
                indent=2,
            )
        )
        return 0

    workloads = (
        [(25, 120), (100, 360)]
        if use_sim
        else [(100, 120), (1000, 360), (5000, 480)]
    )
    cpu_backend = CpuReferenceBackend()
    rows = []
    all_passed = True
    for idx, (n_policies, horizon) in enumerate(workloads):
        rd = _make_run_data(n_policies, horizon)
        t_cpu0 = perf_counter()
        cpu_res = [res for _, res in cpu_backend.execute_policies(rd, rd.policies, FORMULA_FUNCTIONS)]
        cpu_s = perf_counter() - t_cpu0

        # Cold invocation (includes first-time kernel JIT/PTX compilation on idx == 0)
        cold_backend = GPUExecutionBackend(allow_simulator=use_sim)
        t_cold0 = perf_counter()
        cold_res = [res for _, res in cold_backend.execute_policies(rd, rd.policies, FORMULA_FUNCTIONS)]
        cold_e2e_s = perf_counter() - t_cold0

        # Warm invocation (reuses compiled kernel cleanly)
        warm_backend = GPUExecutionBackend(allow_simulator=use_sim)
        t_warm0 = perf_counter()
        warm_res = [res for _, res in warm_backend.execute_policies(rd, rd.policies, FORMULA_FUNCTIONS)]
        warm_e2e_s = perf_counter() - t_warm0

        cold_parity = _compare_full_outputs(cpu_res, cold_res)
        warm_parity = _compare_full_outputs(cpu_res, warm_res)
        if not (cold_parity["within_provisional_tol"] and warm_parity["within_provisional_tol"]):
            all_passed = False

        if args.require_physical_gpu and not (
            cold_backend.last_execution_telemetry.get("hardware_verified")
            and warm_backend.last_execution_telemetry.get("hardware_verified")
        ):
            all_passed = False

        rows.append(
            {
                "workload_index": idx,
                "policies": n_policies,
                "horizon_months": horizon,
                "policy_months": n_policies * horizon,
                "cpu_end_to_end_seconds": round(cpu_s, 6),
                "cold_gpu_end_to_end_seconds": round(cold_e2e_s, 6),
                "warm_gpu_end_to_end_seconds": round(warm_e2e_s, 6),
                "cold_telemetry": cold_backend.last_execution_telemetry,
                "warm_telemetry": warm_backend.last_execution_telemetry,
                "cold_parity": cold_parity,
                "warm_parity": warm_parity,
            }
        )

    if use_sim:
        acceptance = "SIMULATOR_ONLY_NOT_HARDWARE_ACCEPTED"
        phys_status = "NOT_RUN"
        exit_code = 0 if all_passed else 1
    else:
        acceptance = "PASS" if all_passed else "FAIL"
        phys_status = acceptance
        exit_code = 0 if all_passed else 1

    print(
        json.dumps(
            {
                "mode": "cuda_hardware" if not use_sim else "cuda_simulator_non_hardware",
                "acceptance_status": acceptance,
                "physical_gpu_acceptance": phys_status,
                "provisional_abs_tol": PROVISIONAL_ABS_TOL,
                "environment": env_meta,
                "hardware_probe": hw,
                "results": rows,
            },
            indent=2,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
