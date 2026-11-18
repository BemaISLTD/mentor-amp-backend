"""Genuine local PySpark Classic Python 3.14 partition smoke test (M6-00).

Demonstrates and verifies:
1. Java 17 + PySpark 4.1+ Spark Classic session on Python 3.14 (`local[2]`).
2. Broadcasting shared actuarial metadata/tables once via `sc.broadcast(...)`
   rather than resending them with every policy task.
3. Partitioning a policy portfolio across 2 and 4 Spark partitions using
   `RDD.mapPartitionsWithIndex(...)` where each worker initializes
   `register_all_products()` inside the worker process.
4. Exact numerical parity (`max_abs_diff == 0.0`) against single-process CPU execution
   using Mentor AMP's real SPIA reference model and checked-in lifelib fixture.
5. Genuine Spark DataFrame shuffle/aggregation (`groupBy("partition_id")`).
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import time
from dataclasses import replace
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

FIXTURE_PATH = BACKEND / "tests" / "fixtures" / "spia_reference_lifelib_v0_17_2.json"


def _ensure_java_home() -> str:
    """Resolve JAVA_HOME using existing environment, PATH, or local Windows JDK-17 install."""
    if os.environ.get("JAVA_HOME"):
        return os.environ["JAVA_HOME"]
    java_exe = shutil.which("java")
    if java_exe:
        jdk_home = str(Path(java_exe).resolve().parents[1])
        os.environ["JAVA_HOME"] = jdk_home
        return jdk_home
    local_jdk = Path(os.environ.get("LOCALAPPDATA", "")) / "MentorAmp" / "jdk-17"
    if (local_jdk / "bin" / "java.exe").exists():
        os.environ["JAVA_HOME"] = str(local_jdk)
        os.environ["PATH"] = f"{local_jdk / 'bin'};{os.environ.get('PATH', '')}"
        return str(local_jdk)
    raise RuntimeError(
        "Java 17+ was not found. Set JAVA_HOME or install JDK 17 before running Spark smoke."
    )


def _build_spia_template():
    from app.core.projection_engine.engine import order_formulas
    from app.core.projection_engine.run_data import (
        FormulaSpec,
        PolicyRecord,
        RunData,
        ScenarioSpec,
        TableSpec,
        VariableSpec,
    )
    from app.products.spia_reference import config as spia

    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
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

    def build_table(name: str, value_column: str) -> TableSpec:
        table = TableSpec(
            id=f"table-{name.lower()}",
            name=name,
            key_columns=("age", "gender"),
            value_column=value_column,
            rows=[
                {"age": row["age"], "gender": row["gender"], value_column: row[value_column]}
                for row in fixture["assumptions"]
            ],
        )
        assert table.build_index() == []
        return table

    surrender = TableSpec(
        id="table-spia-reference-surrender-charge",
        name=spia.SURRENDER_CHARGE_TABLE_NAME,
        key_columns=("policy_year",),
        value_column="surrender_charge_rate",
        rows=list(fixture["surrender_charge_schedule"]),
        kind="factor",
    )
    assert surrender.build_index() == []

    mortality = build_table(spia.MORTALITY_TABLE_NAME, "base_mortality_rate")
    improvement = build_table(spia.IMPROVEMENT_TABLE_NAME, "mortality_improvement_rate")
    policy_input = fixture["input"]
    horizon_months = len(fixture["rows"])

    template = RunData(
        variables=variables,
        formulas=formulas,
        tables={
            mortality.name: mortality,
            improvement.name: improvement,
            surrender.name: surrender,
        },
        policies=[],
        scenario=ScenarioSpec(
            id="base",
            name="Base",
            overrides=(
                {
                    "target_variable": "commutation_utilization_rate",
                    "operation": "set",
                    "value": 0.0,
                },
            ),
        ),
        valuation_date=date(2026, 12, 31),
        horizon_months=horizon_months,
        output_variables=["annuity_payment", "reserve"],
    )

    policies: list[PolicyRecord] = []
    for idx in range(16):
        is_male = idx % 2 == 0
        age = 65 if is_male else 62
        gender = "M" if is_male else "F"
        income = float(policy_input["annual_income"]) * (1.0 + 0.05 * idx)
        pid = f"SPIA-SMOKE-{idx + 1:04d}"
        data = {
            "policy_id": pid,
            "product_type": "SPIA",
            "issue_date": "2026-12-31",
            "issue_age": age,
            "gender": gender,
            "annual_income": income,
            "benefit_form": "life_only",
            "premium": 0.0,
            "cumulative_scheduled_paid_at_valuation": 0.0,
            "commutation_fraction_at_valuation": 0.0,
            "certain_months": 0,
            "commutation_enabled": 0,
            "annuity_year": int(policy_input["annuity_year"]),
            "payment_frequency": 12,
            "payment_timing": "arrears",
            "cola_rate": 0.01 * (idx % 3),
            "rating_factor": float(policy_input["rating_factor"]),
            "is_joint": 0,
            "joint_issue_age": 65,
            "joint_gender": "M",
            "joint_rating_factor": 1.0,
            "survivor_percentage": 1.0,
            "reduction_trigger": "either",
            "issue_state_excludes_withdrawal": 0,
        }
        policies.append(
            PolicyRecord(
                policy_id=pid,
                data=data,
                dataset_id="smoke-spia-inforce",
                dataset_name=FIXTURE_PATH.name,
            )
        )
    return template, policies


def main() -> None:
    java_home = _ensure_java_home()

    # Make Spark pick up the active venv python executable on both driver and workers.
    os.environ["PYSPARK_PYTHON"] = sys.executable
    os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

    import pandas as pd
    import pyspark
    from pyspark import TaskContext
    from pyspark.sql import SparkSession
    from pyspark.sql import functions as F

    from app.core.formula_engine.formulas import FORMULA_FUNCTIONS
    from app.core.projection_engine.engine import run_policy
    from app.products.registry import register_all_products

    template, policies = _build_spia_template()

    # 1. Pure-CPU reference run
    register_all_products()
    t0 = time.perf_counter()
    cpu_by_id: dict[str, dict[str, float]] = {}
    for pol in policies:
        res = run_policy(template, pol, FORMULA_FUNCTIONS)
        assert res.error is None, res.error
        cpu_by_id[pol.policy_id] = {
            "reserve_0": float(res.reserve_0 or 0.0),
            "annuity_payment_sum": float(res.sums.get("annuity_payment", 0.0)),
            "months_computed": int(res.months_computed),
        }
    cpu_elapsed = time.perf_counter() - t0

    # 2. Local PySpark Classic session
    t_spark_boot = time.perf_counter()
    spark = (
        SparkSession.builder.master("local[2]")
        .appName("mentoramp-m6-00-smoke")
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.execution.arrow.pyspark.enabled", "true")
        .config("spark.python.worker.reuse", "false")
        .config("spark.sql.shuffle.partitions", "4")
        .getOrCreate()
    )
    spark_boot_elapsed = time.perf_counter() - t_spark_boot

    try:
        sc = spark.sparkContext
        broadcast_template = sc.broadcast(template)

        def run_partition(partition_index: int, iterator):
            # Each Spark worker Python process initializes formula registry cleanly.
            if str(BACKEND) not in sys.path:
                sys.path.insert(0, str(BACKEND))
            from app.core.formula_engine.formulas import FORMULA_FUNCTIONS as WORKER_FUNCS
            from app.core.projection_engine.engine import run_policy as worker_run_policy
            from app.products.registry import register_all_products as worker_register

            worker_register()
            shared_template = broadcast_template.value
            ctx = TaskContext.get()
            ctx_pid = ctx.partitionId() if ctx is not None else partition_index
            worker_os_pid = os.getpid()

            for pol in iterator:
                res = worker_run_policy(shared_template, pol, WORKER_FUNCS)
                if res.error is not None:
                    raise RuntimeError(f"Policy {pol.policy_id} failed: {res.error.as_dict()}")
                yield {
                    "policy_id": pol.policy_id,
                    "partition_id": int(ctx_pid),
                    "worker_os_pid": int(worker_os_pid),
                    "reserve_0": float(res.reserve_0 or 0.0),
                    "annuity_payment_sum": float(res.sums.get("annuity_payment", 0.0)),
                    "months_computed": int(res.months_computed),
                }

        def execute_with_partitions(num_partitions: int) -> tuple[list[dict], float]:
            t_start = time.perf_counter()
            rdd = sc.parallelize(policies, numSlices=num_partitions)
            rows = rdd.mapPartitionsWithIndex(run_partition).collect()
            elapsed = time.perf_counter() - t_start
            return rows, elapsed

        p2_rows, p2_elapsed = execute_with_partitions(2)
        p4_rows, p4_elapsed = execute_with_partitions(4)

        def max_diff_vs_cpu(rows: list[dict]) -> float:
            assert len(rows) == len(cpu_by_id)
            worst = 0.0
            for row in rows:
                ref = cpu_by_id[row["policy_id"]]
                worst = max(
                    worst,
                    abs(row["reserve_0"] - ref["reserve_0"]),
                    abs(row["annuity_payment_sum"] - ref["annuity_payment_sum"]),
                )
                assert row["months_computed"] == ref["months_computed"]
            return worst

        p2_diff = max_diff_vs_cpu(p2_rows)
        p4_diff = max_diff_vs_cpu(p4_rows)
        assert p2_diff == 0.0, f"Unexpected Spark(p=2) drift: {p2_diff}"
        assert p4_diff == 0.0, f"Unexpected Spark(p=4) drift: {p4_diff}"

        # 3. Real Arrow-backed Spark DataFrame shuffle & JVM Catalyst aggregation
        df = spark.createDataFrame(pd.DataFrame(p4_rows))
        agg_rows = (
            df.groupBy("partition_id")
            .agg(
                F.count("policy_id").alias("policy_count"),
                F.sum("reserve_0").alias("partition_reserve_sum"),
                F.sum("annuity_payment_sum").alias("partition_payment_sum"),
            )
            .orderBy("partition_id")
            .collect()
        )
        total_spark_reserve = sum(float(r["partition_reserve_sum"]) for r in agg_rows)
        total_cpu_reserve = sum(v["reserve_0"] for v in cpu_by_id.values())
        agg_diff = abs(total_spark_reserve - total_cpu_reserve)
        assert agg_diff < 1e-9, f"Aggregate shuffle mismatch: {agg_diff}"

        report = {
            "gate": "M6-00 Local PySpark Classic Partition Smoke",
            "python_version": platform.python_version(),
            "pyspark_version": pyspark.__version__,
            "spark_version": spark.version,
            "java_home": java_home,
            "java_version": sc._jvm.System.getProperty("java.version"),
            "master": spark.sparkContext.master,
            "default_parallelism": sc.defaultParallelism,
            "driver_os_pid": os.getpid(),
            "spark_session_boot_seconds": round(spark_boot_elapsed, 3),
            "policy_count": len(policies),
            "horizon_months": template.horizon_months,
            "cpu_elapsed_seconds": round(cpu_elapsed, 4),
            "spark_2_partitions": {
                "num_partitions": 2,
                "elapsed_seconds": round(p2_elapsed, 3),
                "worker_os_pids": sorted({r["worker_os_pid"] for r in p2_rows}),
                "partition_ids_seen": sorted({r["partition_id"] for r in p2_rows}),
                "max_abs_diff_vs_cpu": p2_diff,
            },
            "spark_4_partitions": {
                "num_partitions": 4,
                "elapsed_seconds": round(p4_elapsed, 3),
                "worker_os_pids": sorted({r["worker_os_pid"] for r in p4_rows}),
                "partition_ids_seen": sorted({r["partition_id"] for r in p4_rows}),
                "max_abs_diff_vs_cpu": p4_diff,
            },
            "dataframe_shuffle_aggregation": {
                "partitions": [r.asDict() for r in agg_rows],
                "total_reserve_diff_vs_cpu": agg_diff,
            },
            "parity_status": "PASS",
        }
        print(json.dumps(report, indent=2))
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
