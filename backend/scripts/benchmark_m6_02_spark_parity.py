"""Empirical CPU vs PySpark benchmark and numerical parity report (Phase 6 M6-02).

Measures on the same real Mentor AMP pure engine (``run_policy`` + ``SparkExecutionBackend``):
- CPU calculation time and end-to-end runtime
- Spark JVM startup time, broadcast serialization, partition exec, Catalyst shuffle agg, collect time
- Peak driver RSS memory (Windows psapi / POSIX resource)
- Task/partition distribution and worker OS PIDs across local[2] and local[4]
- Exact numerical parity across all policies and output variables

Evaluates empirically whether the provisional 50,000 policy-month Spark crossover holds on this host.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.execution.spark_backend import SparkExecutionBackend, _resolve_java_home  # noqa: E402
from app.core.formula_engine.formulas import FORMULA_FUNCTIONS  # noqa: E402
from app.core.projection_engine.engine import order_formulas, run_policy  # noqa: E402
from app.core.projection_engine.run_data import (  # noqa: E402
    FormulaSpec,
    PolicyRecord,
    RunData,
    ScenarioSpec,
    TableSpec,
    VariableSpec,
)
from app.products.registry import register_all_products  # noqa: E402
from app.products.spia_reference import config as spia  # noqa: E402

FIXTURE_PATH = BACKEND / "tests" / "fixtures" / "spia_reference_lifelib_v0_17_2.json"


def _peak_rss_mb() -> float:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        pmc = PROCESS_MEMORY_COUNTERS()
        pmc.cb = ctypes.sizeof(pmc)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
            return round(pmc.PeakWorkingSetSize / (1024 * 1024), 2)
        return 0.0
    import resource

    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(rss_kb / 1024.0, 2)


def _build_benchmark_workload(policy_count: int, horizon_months: int = 240) -> tuple[RunData, list[PolicyRecord]]:
    """Build deterministic SPIA policies scaled safely on this laptop's RAM."""
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    # Extend mortality/improvement tables synthetically up to age 120 cleanly for long-horizon scaling.
    existing_assump = {(r["age"], r["gender"]): r for r in fixture["assumptions"]}
    extended_mort = []
    extended_imp = []
    for gender in ("M", "F"):
        seed_row = existing_assump[(65, "M")] if gender == "M" else existing_assump[(62, "F")]
        for age in range(55, 121):
            base_q = float(seed_row["base_mortality_rate"]) * (1.03 ** max(age - 65, 0))
            base_q = min(base_q, 0.99)
            imp_r = float(seed_row["mortality_improvement_rate"])
            extended_mort.append({"age": age, "gender": gender, "base_mortality_rate": base_q})
            extended_imp.append({"age": age, "gender": gender, "mortality_improvement_rate": imp_r})

    variables = {
        item["name"]: VariableSpec(
            name=item["name"],
            kind=item["kind"],
            data_type=item["data_type"],
            unit=item.get("unit"),
            source=dict(item["source"]),
            default_value=item.get("default_value"),
            required=item.get("required", True),
            display_name=item.get("display_name"),
            allow_scenario_override=item.get("allow_scenario_override", False),
        )
        for item in spia.VARIABLES
    }
    formulas = order_formulas(
        [
            FormulaSpec(
                id=f"formula-{item['output_variable']}",
                name=item["name"],
                output_variable=item["output_variable"],
                function_ref=item["function_ref"],
                dependencies=tuple(item["dependencies"]),
                illustrative=True,
            )
            for item in spia.FORMULAS
        ]
    )

    mort_table = TableSpec(
        id="bench-mort",
        name=spia.MORTALITY_TABLE_NAME,
        key_columns=("age", "gender"),
        value_column="base_mortality_rate",
        rows=extended_mort,
    )
    assert mort_table.build_index() == []
    imp_table = TableSpec(
        id="bench-imp",
        name=spia.IMPROVEMENT_TABLE_NAME,
        key_columns=("age", "gender"),
        value_column="mortality_improvement_rate",
        rows=extended_imp,
    )
    assert imp_table.build_index() == []
    sc_table = TableSpec(
        id="bench-sc",
        name=spia.SURRENDER_CHARGE_TABLE_NAME,
        key_columns=("policy_year",),
        value_column="surrender_charge_rate",
        rows=[{"policy_year": y, "surrender_charge_rate": 0.0} for y in range(1, 60)],
        kind="factor",
    )
    assert sc_table.build_index() == []

    data = RunData(
        variables=variables,
        formulas=formulas,
        tables={
            mort_table.name: mort_table,
            imp_table.name: imp_table,
            sc_table.name: sc_table,
        },
        policies=[],
        scenario=ScenarioSpec(
            id="base",
            name="Base",
            overrides=(
                {"target_variable": "commutation_utilization_rate", "operation": "set", "value": 0.0},
            ),
        ),
        valuation_date=date(2026, 12, 31),
        horizon_months=horizon_months,
        output_variables=["expected_payment", "pv_expected_payment", "reserve"],
    )

    policies: list[PolicyRecord] = []
    for idx in range(policy_count):
        age = 60 + (idx % 12)
        gender = "M" if idx % 2 == 0 else "F"
        pid = f"BENCH-{idx + 1:05d}"
        policies.append(
            PolicyRecord(
                policy_id=pid,
                data={
                    "policy_id": pid,
                    "product_type": "SPIA",
                    "issue_date": "2026-12-31",
                    "issue_age": age,
                    "gender": gender,
                    "annual_income": 6000.0 + 100.0 * (idx % 20),
                    "benefit_form": "life_only",
                    "premium": 0.0,
                    "cumulative_scheduled_paid_at_valuation": 0.0,
                    "commutation_fraction_at_valuation": 0.0,
                    "certain_months": 0,
                    "commutation_enabled": 0,
                    "annuity_year": 2026,
                    "payment_frequency": 12,
                    "payment_timing": "arrears",
                    "cola_rate": 0.01 * (idx % 3),
                    "rating_factor": 1.0,
                    "is_joint": 0,
                    "joint_issue_age": 65,
                    "joint_gender": "M",
                    "joint_rating_factor": 1.0,
                    "survivor_percentage": 1.0,
                    "reduction_trigger": "either",
                    "issue_state_excludes_withdrawal": 0,
                },
                dataset_id="bench-inforce",
                dataset_name="synthetic_spia_bench",
            )
        )
    return data, policies


def run_benchmark_point(policy_count: int, horizon_months: int = 240) -> dict[str, Any]:
    register_all_products()
    data, policies = _build_benchmark_workload(policy_count, horizon_months)
    policy_months = policy_count * horizon_months

    t_cpu0 = perf_counter()
    cpu_map: dict[str, dict[str, float]] = {}
    for pol in policies:
        res = run_policy(data, pol, FORMULA_FUNCTIONS)
        assert res.error is None, res.error
        cpu_map[pol.policy_id] = {
            "reserve_0": float(res.reserve_0 or 0.0),
            "annuity_payment": float(res.sums.get("expected_payment", 0.0)),
        }
    cpu_total_s = perf_counter() - t_cpu0

    configs = [("local[2]", 2), ("local[4]", 4)]
    spark_runs = []
    for master, partitions in configs:
        backend = SparkExecutionBackend(
            execution_options={
                "spark_master": master,
                "spark_partitions": partitions,
                "batch_size": 250,
            }
        )
        session_info: dict[str, Any] = {}
        t_s0 = perf_counter()
        waves = list(
            backend.execute_waves(
                data,
                policies,
                on_session_ready=lambda info: session_info.update(info),
            )
        )
        spark_total_s = perf_counter() - t_s0

        worst_diff = 0.0
        seen_pids: set[int] = set()
        seen_parts: set[int] = set()
        bcast_s = sum(w.broadcast_seconds for w in waves)
        max_calc_s = sum(w.partition_exec_seconds for w in waves)
        collect_s = sum(w.collect_seconds for w in waves)
        agg_s = sum(w.shuffle_agg_seconds for w in waves)
        for w in waves:
            seen_pids.update(w.worker_os_pids)
            seen_parts.update(w.partitions_seen)
            for pol, res in w.outcomes:
                assert res.error is None
                ref = cpu_map[pol.policy_id]
                worst_diff = max(
                    worst_diff,
                    abs(float(res.reserve_0 or 0.0) - ref["reserve_0"]),
                    abs(float(res.sums.get("expected_payment", 0.0)) - ref["annuity_payment"]),
                )
        spark_runs.append({
            "master": master,
            "partitions": len(seen_parts),
            "waves": len(waves),
            "total_seconds": round(spark_total_s, 3),
            "session_boot_seconds": session_info.get("spark_session_boot_seconds"),
            "broadcast_seconds": round(bcast_s, 4),
            "partition_exec_seconds": round(max_calc_s, 4),
            "collect_seconds": round(collect_s, 4),
            "shuffle_agg_seconds": round(agg_s, 4),
            "worker_os_pids_count": len(seen_pids),
            "max_abs_diff_vs_cpu": worst_diff,
            "speedup_vs_cpu": round(cpu_total_s / spark_total_s, 3) if spark_total_s > 0 else 0.0,
        })

    return {
        "policy_count": policy_count,
        "horizon_months": horizon_months,
        "policy_months": policy_months,
        "cpu_seconds": round(cpu_total_s, 3),
        "peak_rss_mb": _peak_rss_mb(),
        "spark_runs": spark_runs,
    }


def main() -> None:
    assert _resolve_java_home(), "Java 17+ required"
    results = [run_benchmark_point(n, horizon_months=240) for n in (16, 100, 300)]
    payload = {
        "benchmark": "M6-02 Empirical CPU vs PySpark Scale & Parity",
        "platform": platform.platform(terse=True),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "provisional_threshold_policy_months": 50_000,
        "results": results,
    }
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
