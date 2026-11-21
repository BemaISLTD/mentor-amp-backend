"""M6-04 Comprehensive CPU vs PySpark Performance, Cold/Warm, Serialization, Artifact & Parity Benchmark.

Measures real Mentor AMP workloads using identical frozen inputs across policy counts, scenario counts,
projection horizons, and partition counts:
- CPU end-to-end runtime & throughput
- Spark cold vs warm runtime (JVM boot, broadcast/serialization, partition execution, shuffle agg, collect)
- Partition counts & worker OS PID utilization
- Peak driver RSS memory (MB)
- Written m2-v1 zstd Parquet output artifact sizes (bytes & rows)
- Output-type classified numerical differences (exact, max_abs_diff, max_rel_diff) vs CPU reference

Outputs structured JSON to stdout and optionally to ``--output <path>``.
Uses actual observed measurements only; never fabricates speedups or production thresholds.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from time import perf_counter
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.artifacts import IDENTITY_NEUTRAL_SCHEMA, LocalParquetArtifactStore  # noqa: E402
from app.core.execution.spark_backend import SparkExecutionBackend, _resolve_java_home  # noqa: E402
from app.core.formula_engine.formulas import FORMULA_FUNCTIONS  # noqa: E402
from app.core.projection_engine.engine import run_policy  # noqa: E402
from app.core.projection_engine.run_data import RunData, ScenarioSpec  # noqa: E402
from app.products.registry import register_all_products  # noqa: E402
from scripts.benchmark_m6_02_spark_parity import (  # noqa: E402
    _build_benchmark_workload,
    _peak_rss_mb,
)

PROVISIONAL_ABS_TOL = 1e-9


def _git_commit() -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(BACKEND.parent), text=True, timeout=5
        )
        return out.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _classify_var(name: str, unit: str | None = None) -> str:
    u = (unit or "").strip().lower()
    n = name.lower()
    if u in {"usd", "currency", "$"} or any(
        tok in n for tok in ("reserve", "payment", "cash_flow", "benefit", "pv_")
    ):
        return "currency"
    if u == "probability" or any(tok in n for tok in ("prob", "qx", "survival", "mortality")):
        return "probability"
    if u == "rate" or "rate" in n:
        return "rate"
    if u == "factor" or "factor" in n or "discount" in n:
        return "factor"
    return "other"


def _write_parquet_artifact_size(outcomes: list[tuple[Any, Any]], scenario_id: str) -> tuple[int, int]:
    rows = []
    for pol, res in outcomes:
        if res.error is not None:
            continue
        for month, var_name, val in res.outputs:
            rows.append(
                {
                    "policy_id": pol.policy_id,
                    "scenario_id": scenario_id,
                    "projection_month": month,
                    "variable_name": var_name,
                    "value_json": json.dumps({"value": val}, sort_keys=True),
                    "product": "SPIA_REFERENCE",
                }
            )
    if not rows:
        return 0, 0
    with tempfile.TemporaryDirectory() as tmpdir:
        store = LocalParquetArtifactStore(tmpdir)
        stored = store.write_rows(
            "bench-run", "outputs", rows, attempt_number=1, schema_version=IDENTITY_NEUTRAL_SCHEMA
        )
        size_bytes = store.path_for_uri(stored.uri).stat().st_size
        return len(rows), int(size_bytes)


def _compare_classified(
    cpu_outcomes: list[tuple[Any, Any]],
    spk_outcomes: list[tuple[Any, Any]],
    var_units: dict[str, str | None],
) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for (cp, cres), (sp, sres) in zip(cpu_outcomes, spk_outcomes, strict=True):
        assert cp.policy_id == sp.policy_id
        assert cres.error is None and sres.error is None
        c_map = {(m, v): float(val) for m, v, val in cres.outputs}
        s_map = {(m, v): float(val) for m, v, val in sres.outputs}
        assert set(c_map) == set(s_map)
        for (m, v), cv in c_map.items():
            sv = s_map[(m, v)]
            cls = _classify_var(v, var_units.get(v))
            b = stats.setdefault(
                cls,
                {"compared_points": 0, "exact_matches": 0, "max_abs_diff": 0.0, "max_rel_diff": 0.0},
            )
            diff = abs(cv - sv)
            rel = diff / max(abs(cv), 1.0)
            b["compared_points"] += 1
            if diff == 0.0:
                b["exact_matches"] += 1
            b["max_abs_diff"] = max(b["max_abs_diff"], diff)
            b["max_rel_diff"] = max(b["max_rel_diff"], rel)
    return stats


def _run_scenario_pair(
    base_data: RunData,
    policies: list[Any],
    scenarios: list[ScenarioSpec],
    *,
    spark_master: str,
    spark_partitions: int,
    batch_size: int,
) -> dict[str, Any]:
    var_units = {name: spec.unit for name, spec in base_data.variables.items()}
    cpu_total_s = 0.0
    spark_total_s = 0.0
    spark_boot_s = 0.0
    broadcast_s = 0.0
    partition_exec_s = 0.0
    collect_s = 0.0
    shuffle_agg_s = 0.0
    partitions_used: set[int] = set()
    worker_pids: set[int] = set()
    total_output_rows = 0
    total_parquet_bytes = 0
    merged_parity: dict[str, dict[str, Any]] = {}

    for sc_spec in scenarios:
        rd = RunData(
            variables=base_data.variables,
            formulas=base_data.formulas,
            tables=base_data.tables,
            policies=policies,
            scenario=sc_spec,
            valuation_date=base_data.valuation_date,
            horizon_months=base_data.horizon_months,
            output_variables=base_data.output_variables,
            traced_policy_ids=base_data.traced_policy_ids,
            parameters=base_data.parameters,
            decrement_methodology=base_data.decrement_methodology,
        )
        t_cpu0 = perf_counter()
        cpu_outcomes = [(p, run_policy(rd, p, FORMULA_FUNCTIONS)) for p in policies]
        cpu_total_s += perf_counter() - t_cpu0

        session_info: dict[str, Any] = {}
        backend = SparkExecutionBackend(
            execution_options={
                "spark_master": spark_master,
                "spark_partitions": spark_partitions,
                "batch_size": batch_size,
            }
        )
        t_spk0 = perf_counter()
        spk_outcomes: list[tuple[Any, Any]] = []
        for wave in backend.execute_waves(
            rd,
            policies,
            on_session_ready=session_info.update,
            watch_poll_seconds=0.0,
        ):
            spk_outcomes.extend(wave.outcomes)
            broadcast_s += wave.broadcast_seconds
            partition_exec_s += wave.partition_exec_seconds
            collect_s += wave.collect_seconds
            shuffle_agg_s += wave.shuffle_agg_seconds
            partitions_used.update(wave.partitions_seen)
            worker_pids.update(wave.worker_os_pids)
        spark_total_s += perf_counter() - t_spk0
        spark_boot_s += float(session_info.get("spark_session_boot_seconds", 0.0))

        row_cnt, pq_bytes = _write_parquet_artifact_size(spk_outcomes, sc_spec.id or sc_spec.name)
        total_output_rows += row_cnt
        total_parquet_bytes += pq_bytes

        sc_parity = _compare_classified(cpu_outcomes, spk_outcomes, var_units)
        for cls, bucket in sc_parity.items():
            target = merged_parity.setdefault(
                cls,
                {"compared_points": 0, "exact_matches": 0, "max_abs_diff": 0.0, "max_rel_diff": 0.0},
            )
            target["compared_points"] += bucket["compared_points"]
            target["exact_matches"] += bucket["exact_matches"]
            target["max_abs_diff"] = max(target["max_abs_diff"], bucket["max_abs_diff"])
            target["max_rel_diff"] = max(target["max_rel_diff"], bucket["max_rel_diff"])

    policy_months = len(policies) * base_data.horizon_months * len(scenarios)
    all_within_tol = all(
        b["max_abs_diff"] <= PROVISIONAL_ABS_TOL for b in merged_parity.values()
    )
    return {
        "policies": len(policies),
        "horizon_months": base_data.horizon_months,
        "scenarios": len(scenarios),
        "policy_months": policy_months,
        "spark_master": spark_master,
        "spark_partitions_requested": spark_partitions,
        "partitions_used": sorted(partitions_used),
        "worker_os_pid_count": len(worker_pids),
        "cpu_end_to_end_seconds": round(cpu_total_s, 4),
        "spark_end_to_end_seconds": round(spark_total_s, 4),
        "spark_session_boot_seconds": round(spark_boot_s, 4),
        "spark_broadcast_seconds": round(broadcast_s, 4),
        "spark_partition_exec_seconds": round(partition_exec_s, 4),
        "spark_collect_seconds": round(collect_s, 4),
        "spark_shuffle_agg_seconds": round(shuffle_agg_s, 4),
        "cpu_policy_months_per_sec": round(policy_months / max(cpu_total_s, 1e-6), 1),
        "spark_policy_months_per_sec": round(policy_months / max(spark_total_s, 1e-6), 1),
        "spark_vs_cpu_speed_ratio": round(cpu_total_s / max(spark_total_s, 1e-6), 3),
        "output_row_count": total_output_rows,
        "parquet_artifact_bytes": total_parquet_bytes,
        "peak_driver_rss_mb": _peak_rss_mb(),
        "parity_by_output_type": merged_parity,
        "within_provisional_tol": all_within_tol,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M6-04 CPU vs PySpark benchmark and conformance report")
    parser.add_argument("--output", type=str, default=None, help="Optional JSON output file path.")
    args = parser.parse_args(argv)

    register_all_products()
    java_home = _resolve_java_home()
    if java_home is None:
        raise RuntimeError("Java 17+ is required for M6-04 PySpark benchmark.")

    base_sc = ScenarioSpec("base", "Base", ())
    stress_sc = ScenarioSpec(
        "stress",
        "Low Rate Stress",
        (
            {
                "target_variable": "valuation_discount_rate",
                "operation": "set",
                "value": 0.03,
                "applies_from_period": 1,
                "applies_to_period": 600,
            },
        ),
    )

    # Representative workloads across policy count, scenario count, horizon, and partition count:
    # Workload 0 runs twice (cold first JVM start, warm second run) to show cold vs warm overhead explicitly.
    matrix_specs = [
        ("small_25p_24m_2sc_cold", 25, 24, [base_sc, stress_sc], "local[2]", 2, 25),
        ("small_25p_24m_2sc_warm", 25, 24, [base_sc, stress_sc], "local[2]", 2, 25),
        ("medium_100p_120m_2sc_p2", 100, 120, [base_sc, stress_sc], "local[2]", 2, 50),
        ("med_long_200p_240m_1sc_p4", 200, 240, [base_sc], "local[4]", 4, 100),
        ("large_400p_240m_1sc_p2", 400, 240, [base_sc], "local[2]", 2, 100),
        ("large_400p_240m_1sc_p4", 400, 240, [base_sc], "local[4]", 4, 100),
    ]

    results = []
    for label, n_pol, horizon, sc_list, master, parts, batch_sz in matrix_specs:
        rd, pols = _build_benchmark_workload(policy_count=n_pol, horizon_months=horizon)
        desired_outputs = [
            v
            for v in (
                "survival_probability",
                "payment_factor_life",
                "annuity_payment",
                "pv_liability_cash_flow",
                "reserve",
            )
            if v in rd.variables
        ]
        if desired_outputs:
            rd.output_variables = desired_outputs
        entry = _run_scenario_pair(
            rd,
            pols,
            sc_list,
            spark_master=master,
            spark_partitions=parts,
            batch_size=batch_sz,
        )
        entry["label"] = label
        results.append(entry)

    report = {
        "benchmark": "M6-04 CPU vs PySpark Conformance & Performance",
        "source_commit": _git_commit(),
        "python_version": platform.python_version(),
        "platform": platform.platform(terse=True),
        "cpu_count": os.cpu_count(),
        "provisional_abs_tol": PROVISIONAL_ABS_TOL,
        "cas_17_owner_tolerance_status": "OPEN_PENDING_PRODUCT_OWNER_CONFIRMATION",
        "physical_gpu_acceptance": "NOT_RUN_OPEN",
        "results": results,
    }
    text = json.dumps(report, indent=2)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
