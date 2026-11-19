"""M6-03 CPU vs CUDA GPU / Simulator benchmark for dense float64 prospective reserve.

Usage (from ``backend`` directory):
  Physical NVIDIA CUDA host:
    python scripts/benchmark_m6_03_gpu.py
  Explicit simulator parity/profiling check (NEVER reported as physical hardware timing):
    NUMBA_ENABLE_CUDASIM=1 python scripts/benchmark_m6_03_gpu.py --allow-simulator
"""

from __future__ import annotations

import argparse
from datetime import date
import json
import os
import sys
from pathlib import Path
from time import perf_counter

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.execution.backends import CpuReferenceBackend  # noqa: E402
from app.core.execution.gpu_backend import GPUExecutionBackend, probe_cuda_hardware  # noqa: E402
from app.core.formula_engine.formulas import FORMULA_FUNCTIONS  # noqa: E402
from app.core.projection_engine.run_data import FormulaSpec, PolicyRecord, RunData, ScenarioSpec, VariableSpec  # noqa: E402
from app.core.valuation import illustrative_reserve  # noqa: E402
from app.products.registry import register_all_products  # noqa: E402


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
            "projection_month": VariableSpec("projection_month", "input", "integer", source={"type": "context", "field": "projection_month"}),
            "monthly_cash_flow": VariableSpec("monthly_cash_flow", "input", "number", source={"type": "input", "column": "monthly_cash_flow"}),
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
            FormulaSpec("f1", "Cash Flow", "cash_flow", "prospective_reserve:cash_flow", ("monthly_cash_flow", "cash_flow_growth_rate", "projection_month")),
            FormulaSpec("f2", "Discount Factor", "discount_factor", "prospective_reserve:discount_factor", ("discount_rate_annual", "projection_month")),
            FormulaSpec("f3", "PV Cash Flow", "pv_cash_flow", "prospective_reserve:pv_cash_flow", ("cash_flow", "discount_factor")),
        ],
        tables={},
        policies=policies,
        scenario=ScenarioSpec("1", "Benchmark Base", ()),
        valuation_date=date(2026, 1, 1),
        horizon_months=horizon_months,
        output_variables=["cash_flow", "discount_factor", "pv_cash_flow", "reserve"],
        traced_policy_ids=frozenset(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark CPU vs GPU prospective reserve calculation")
    parser.add_argument(
        "--allow-simulator",
        action="store_true",
        help="Allow NUMBA_ENABLE_CUDASIM=1 simulator mode (results strictly flagged simulator_non_hardware=True).",
    )
    args = parser.parse_args()

    register_all_products()
    hw = probe_cuda_hardware()
    use_sim = bool(args.allow_simulator and os.environ.get("NUMBA_ENABLE_CUDASIM") == "1" and not hw["cuda_gpu_detected"])
    if not hw["cuda_gpu_detected"] and not use_sim:
        print(
            json.dumps(
                {
                    "status": "SKIPPED_NO_PHYSICAL_CUDA",
                    "hardware_probe": hw,
                    "note": "No physical NVIDIA CUDA hardware detected. Pass --allow-simulator with NUMBA_ENABLE_CUDASIM=1 only for non-hardware parity profiling.",
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
    for n_policies, horizon in workloads:
        rd = _make_run_data(n_policies, horizon)
        t_cpu0 = perf_counter()
        cpu_res = [res for _, res in cpu_backend.execute_policies(rd, rd.policies, FORMULA_FUNCTIONS)]
        cpu_s = perf_counter() - t_cpu0

        gpu_backend = GPUExecutionBackend(allow_simulator=use_sim)
        t_gpu0 = perf_counter()
        gpu_res = [res for _, res in gpu_backend.execute_policies(rd, rd.policies, FORMULA_FUNCTIONS)]
        gpu_s = perf_counter() - t_gpu0

        for c, g in zip(cpu_res, gpu_res):
            if c.error is not None or g.error is not None:
                raise RuntimeError(f"Parity benchmark error: cpu={c.error} gpu={g.error}")
        max_reserve_diff = max(
            abs(float(c.reserve_0) - float(g.reserve_0)) for c, g in zip(cpu_res, gpu_res)
        )
        if max_reserve_diff > 1e-9:
            raise RuntimeError(f"CPU vs GPU reserve_0 parity drift exceeded 1e-9: {max_reserve_diff}")
        rows.append(
            {
                "policies": n_policies,
                "horizon_months": horizon,
                "policy_months": n_policies * horizon,
                "cpu_seconds": round(cpu_s, 4),
                "gpu_seconds": round(gpu_s, 4),
                "telemetry": gpu_backend.last_execution_telemetry,
                "max_reserve_0_abs_diff": max_reserve_diff,
            }
        )

    print(
        json.dumps(
            {
                "mode": "cuda_hardware" if not use_sim else "cuda_simulator_non_hardware",
                "hardware_probe": hw,
                "results": rows,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
