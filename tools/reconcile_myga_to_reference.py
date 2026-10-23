"""Reconciliation runner: Executes MentorAmp canonical calculation engine (run_policy)
against the internal development reference fixture.

Uses the strict cross-check harness from tools/myga_reference_crosscheck/myga_crosscheck.py.
Inputs are defined independently; expected fixtures are used solely for output comparison.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.formula_engine.formulas import FORMULA_FUNCTIONS
from app.core.projection_engine.engine import order_formulas, run_policy
from app.core.projection_engine.run_data import (
    FormulaSpec,
    PolicyRecord,
    RunData,
    ScenarioSpec,
    TableSpec,
    VariableSpec,
)
from app.products.myga_lite import config as myga
from app.products.myga_lite.decrements import BenchmarkZeroDecrements
from tools.myga_reference_crosscheck.myga_crosscheck import (
    DEFAULT_ENGINE_TOLERANCE,
    REQUIRED_FIELDS,
    load_fixture,
    reconcile_ledger,
)


def run_canonical_engine_case(
    policy_id: str,
    issue_age: int,
    gender: str,
    withdrawal_month: int,
    withdrawal_amount: float,
    horizon_months: int = 60,
) -> list[dict[str, Any]]:
    """Execute the canonical MentorAmp calculation engine (run_policy) on an independent policy.

    Inputs are declared independently of any fixture:
    - Policy record (100k single premium, 5-year guarantee at 4.50%, 2% GMSV rate)
    - Scenario overrides (prescribed partial withdrawal timing, benchmark $60/yr maintenance expense)
    - Factor tables (standard 5-year surrender charge schedule)
    - Actuarial decrement methodology (BenchmarkZeroDecrements for surviving contract mechanics)
    """
    myga.register_functions()

    variables = {
        row["name"]: VariableSpec(
            name=row["name"],
            kind=row["kind"],
            data_type=row.get("data_type", "number"),
            unit=row.get("unit"),
            source=dict(row.get("source") or {}),
            default_value=row.get("default_value"),
            required=row.get("required", True),
            allow_scenario_override=row.get("allow_scenario_override", False),
        )
        for row in myga.VARIABLES
    }
    formulas = order_formulas([
        FormulaSpec(
            id=f"myga-{index}",
            name=row["name"],
            output_variable=row["output_variable"],
            function_ref=row["function_ref"],
            dependencies=tuple(row["dependencies"]),
            illustrative=True,
        )
        for index, row in enumerate(myga.FORMULAS, start=1)
    ])

    mortality = TableSpec(
        id="mortality",
        name=myga.MORTALITY_TABLE_NAME,
        key_columns=("age", "gender"),
        value_column="qx",
        rows=[{"age": age, "gender": "M", "qx": 0.0} for age in range(40, 100)]
        + [{"age": age, "gender": "F", "qx": 0.0} for age in range(40, 100)],
    )
    mortality.build_index()

    charges = TableSpec(
        id="charges",
        name=myga.SURRENDER_CHARGE_TABLE_NAME,
        key_columns=("schedule", "year_in_gp"),
        value_column="rate",
        kind="factor",
        rows=[
            {"schedule": "initial", "year_in_gp": y, "rate": r}
            for y, r in [(1, 0.07), (2, 0.06), (3, 0.05), (4, 0.04), (5, 0.03), (6, 0.0)]
        ]
        + [{"schedule": "renewal", "year_in_gp": y, "rate": 0.0} for y in range(1, 10)],
    )
    charges.build_index()

    age_caps = TableSpec(
        id="age-caps",
        name=myga.SURRENDER_CHARGE_AGE_CAP_TABLE_NAME,
        key_columns=("attained_age",),
        value_column="cap",
        kind="factor",
        rows=[{"attained_age": age, "cap": 1.0} for age in range(40, 100)],
    )
    age_caps.build_index()

    policy = PolicyRecord(
        policy_id=policy_id,
        data={
            "issue_date": "2022-01-01",
            "issue_age": issue_age,
            "gender": gender,
            "premium": 100_000.0,
            "account_value": 100_000.0,
            "mgsv_value": 87_500.0,
            "free_wd_base": 100_000.0,
            "guarantee_period_years": 5,
            "declared_rate": 0.045,
            "gmir": 0.0025,
            "gmsv_rate": 0.02,
            "mva_ref_yield_at_issue": 0.05,
        },
    )

    overrides = [
        {
            "target_variable": "annual_maintenance_expense",
            "operation": "set",
            "value": 60.0,
            "applies_from_period": 1,
            "applies_to_period": horizon_months,
        },
        {
            "target_variable": "expense_inflation_rate",
            "operation": "set",
            "value": 0.0,
            "applies_from_period": 1,
            "applies_to_period": horizon_months,
        },
        {
            "target_variable": "commission_rate",
            "operation": "set",
            "value": 0.0,
            "applies_from_period": 1,
            "applies_to_period": horizon_months,
        },
    ]
    if withdrawal_amount > 0 and 1 <= withdrawal_month <= horizon_months:
        overrides.append({
            "target_variable": "withdrawal",
            "operation": "set",
            "value": withdrawal_amount,
            "applies_from_period": withdrawal_month,
            "applies_to_period": withdrawal_month,
        })

    scenario = ScenarioSpec(
        id=f"benchmark_{policy_id}",
        name=f"Benchmark {policy_id}",
        overrides=tuple(overrides),
    )

    output_variables = [
        "account_value_open",
        "interest_credited",
        "gross_withdrawal",
        "surrender_charge_on_withdrawal",
        "net_withdrawal_benefit",
        "account_value_close",
        "mgsv_close",
        "surrender_benefit",
        "death_benefit",
        "maintenance_expense",
        "net_cash_flow",
        "surrender_charge_rate",
    ]

    data = RunData(
        variables=variables,
        formulas=formulas,
        tables={
            myga.MORTALITY_TABLE_NAME: mortality,
            myga.SURRENDER_CHARGE_TABLE_NAME: charges,
            myga.SURRENDER_CHARGE_AGE_CAP_TABLE_NAME: age_caps,
        },
        policies=[policy],
        scenario=scenario,
        valuation_date=date(2022, 1, 1),
        horizon_months=horizon_months,
        output_variables=output_variables,
        traced_policy_ids=frozenset({policy_id}),
        decrement_methodology=BenchmarkZeroDecrements(),
    )

    result = run_policy(data, policy, FORMULA_FUNCTIONS)

    if result.error is not None:
        raise RuntimeError(f"Engine execution error on {policy_id}: {result.error}")

    by_month: dict[int, dict[str, float]] = {m: {} for m in range(1, horizon_months + 1)}
    for month, var_name, value in result.outputs:
        if 1 <= month <= horizon_months:
            by_month[month][var_name] = value

    actual_rows: list[dict[str, Any]] = []
    for month in range(1, horizon_months + 1):
        m_data = by_month[month]
        actual_rows.append({
            "policy_id": policy_id,
            "month": month,
            "policy_year": int((month - 1) // 12 + 1),
            "account_value_bom": m_data["account_value_open"],
            "credited_interest": m_data["interest_credited"],
            "gross_withdrawal": m_data["gross_withdrawal"],
            "surrender_charge": m_data["surrender_charge_on_withdrawal"],
            "net_withdrawal_benefit": m_data["net_withdrawal_benefit"],
            "account_value_eom": m_data["account_value_close"],
            "mgsv_eom": m_data["mgsv_close"],
            "surrender_charge_rate": m_data["surrender_charge_rate"],
            "cash_surrender_value_eom": m_data["surrender_benefit"],
            "death_benefit_eom": m_data["death_benefit"],
            "maintenance_expense": m_data["maintenance_expense"],
            "net_insurer_cash_flow": m_data["net_cash_flow"],
        })

    return actual_rows


def main() -> int:
    fixture_path = BACKEND_DIR / "tests" / "fixtures" / "myga_reference_simplified_v1.json"
    fixture = load_fixture(fixture_path)
    schema_version = fixture.get("fixture_schema")

    print("\n" + "=" * 78)
    print("  MENTORAMP CANONICAL MYGA ENGINE VS DEVELOPMENT REFERENCE RECONCILIATION")
    print("=" * 78)

    # 1. Reconcile Anchor Case (Independent execution through canonical run_policy)
    anchor_case = fixture["anchor_case"]
    anchor_actual = run_canonical_engine_case(
        policy_id="MYGA-0001",
        issue_age=62,
        gender="M",
        withdrawal_month=12,
        withdrawal_amount=4000.0,
        horizon_months=60,
    )
    anchor_results = reconcile_ledger(
        actual_rows=anchor_actual,
        expected_rows=anchor_case["rows"],
        tolerance=DEFAULT_ENGINE_TOLERANCE,
        expected_policy_id=anchor_case["policy_id"],
        expected_schema_version=schema_version,
        actual_schema_version=schema_version,
    )

    print(f"\n[Case 1] Anchor Model Point ({anchor_case['policy_id']}): {anchor_results['status']}")
    print(f"  Total Months     : {len(anchor_case['rows'])}")
    print(f"  Tolerance        : <= {DEFAULT_ENGINE_TOLERANCE:.1e}")
    print("-" * 78)
    print(f"  {'Output Field':<30} {'Max Abs Diff':<16} {'Max Rel Diff':<16} {'Status':<8}")
    print("-" * 78)
    for field, stat in anchor_results["field_discrepancies"].items():
        print(f"  {field:<30} {stat['max_abs_diff']:<16.2e} {stat['max_rel_diff']:<16.2e} {stat['status']:<8}")

    # 2. Reconcile Excess Withdrawal Case (Independent execution through canonical run_policy)
    excess_case = fixture["excess_withdrawal_case"]
    excess_actual = run_canonical_engine_case(
        policy_id="MYGA-0002",
        issue_age=65,
        gender="F",
        withdrawal_month=24,
        withdrawal_amount=20000.0,
        horizon_months=60,
    )
    excess_results = reconcile_ledger(
        actual_rows=excess_actual,
        expected_rows=excess_case["rows"],
        tolerance=DEFAULT_ENGINE_TOLERANCE,
        expected_policy_id=excess_case["policy_id"],
        expected_schema_version=schema_version,
        actual_schema_version=schema_version,
    )

    print(f"\n[Case 2] Excess Withdrawal Case ({excess_case['policy_id']}): {excess_results['status']}")
    print(f"  Total Months     : {len(excess_case['rows'])}")
    print(f"  Tolerance        : <= {DEFAULT_ENGINE_TOLERANCE:.1e}")
    print("-" * 78)
    print(f"  {'Output Field':<30} {'Max Abs Diff':<16} {'Max Rel Diff':<16} {'Status':<8}")
    print("-" * 78)
    for field, stat in excess_results["field_discrepancies"].items():
        print(f"  {field:<30} {stat['max_abs_diff']:<16.2e} {stat['max_rel_diff']:<16.2e} {stat['status']:<8}")

    print("=" * 78)

    all_passed = anchor_results["status"] == "PASS" and excess_results["status"] == "PASS"
    overall_status = "PASS" if all_passed else "FAIL"
    print(f"  FINAL OVERALL STATUS: {overall_status}")
    print("=" * 78 + "\n")

    summary = {
        "overall_status": overall_status,
        "anchor_case": anchor_results,
        "excess_withdrawal_case": excess_results,
    }
    report_file = REPO_ROOT / "myga_reference_reconciliation_report.json"
    report_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Report written to: {report_file}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
