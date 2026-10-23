"""Automated trace assertion test for MYGA partial and excess withdrawal months.

Verifies that execution traces capture all 15 required intermediate and output variables
for the excess-withdrawal month (Month 12):
- account_value_open
- gross_withdrawal
- free_withdrawal_allowance
- free_withdrawal_available
- chargeable_withdrawal
- surrender_charge_rate
- surrender_charge_on_withdrawal
- withdrawal_mva
- net_withdrawal_benefit
- account_value_before_credit
- interest_credited
- account_value_close
- mgsv_close (GMSV)
- surrender_benefit (cash surrender value)
- liability_outgo (net liability outgo)
"""

from datetime import date
import json
import pytest

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


def test_excess_withdrawal_month_contains_all_required_trace_nodes():
    """Assert that trace for the excess-withdrawal month contains all 15 specified variables with valid values."""
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

    formulas = [
        FormulaSpec(
            id=f"myga-{index}",
            name=row["name"],
            output_variable=row["output_variable"],
            function_ref=row["function_ref"],
            dependencies=tuple(row["dependencies"]),
            illustrative=True,
        )
        for index, row in enumerate(myga.FORMULAS, start=1)
    ]
    ordered_formulas = order_formulas(formulas)

    # Tables with built indices
    mortality = TableSpec(
        id="mort", name=myga.MORTALITY_TABLE_NAME, key_columns=("age", "gender"), value_column="qx", kind="assumption",
        rows=[{"age": age, "gender": g, "qx": 0.005} for age in range(50, 75) for g in ("M", "F")],
    )
    mortality.build_index()

    sc_rows = []
    for sched in ("INITIAL", "initial"):
        for y, r in enumerate([0.07, 0.06, 0.05, 0.04, 0.03], start=1):
            sc_rows.append({"schedule": sched, "year_in_gp": y, "rate": r})
    for sched in ("RENEWAL", "renewal"):
        for y, r in enumerate([0.05, 0.04, 0.03, 0.02, 0.01], start=1):
            sc_rows.append({"schedule": sched, "year_in_gp": y, "rate": r})

    charges = TableSpec(
        id="sc", name=myga.SURRENDER_CHARGE_TABLE_NAME, key_columns=("schedule", "year_in_gp"), value_column="rate", kind="factor",
        rows=sc_rows,
    )
    charges.build_index()

    age_caps = TableSpec(
        id="caps", name=myga.SURRENDER_CHARGE_AGE_CAP_TABLE_NAME, key_columns=("attained_age",), value_column="cap", kind="factor",
        rows=[{"attained_age": a, "cap": 1.0} for a in range(50, 75)],
    )
    age_caps.build_index()

    # Policy with 100,000 premium
    policy = PolicyRecord(
        policy_id="MYGA-TRACE-01",
        data={
            "issue_date": "2026-12-31",
            "issue_age": 60,
            "gender": "M",
            "premium": 100000.0,
            "account_value": 100000.0,
            "mgsv_value": 87500.0,
            "free_wd_base": 100000.0,
            "guarantee_period_years": 5,
            "declared_rate": 0.045,
            "gmir": 0.0025,
            "gmsv_rate": 0.02,
            "mva_ref_yield_at_issue": 0.05,
        },
    )

    # Scenario injecting an excess withdrawal of $20,000 at month 12
    scenario = ScenarioSpec(
        id="excess_wd",
        name="Excess Withdrawal",
        overrides=(
            {
                "target_variable": "withdrawal",
                "operation": "set",
                "value": 20000.0,
                "applies_from_period": 12,
                "applies_to_period": 12,
            },
        ),
    )

    data = RunData(
        variables=variables,
        formulas=ordered_formulas,
        tables={
            myga.MORTALITY_TABLE_NAME: mortality,
            myga.SURRENDER_CHARGE_TABLE_NAME: charges,
            myga.SURRENDER_CHARGE_AGE_CAP_TABLE_NAME: age_caps,
        },
        policies=[policy],
        scenario=scenario,
        valuation_date=date(2026, 12, 31),
        horizon_months=24,
        output_variables=[
            "account_value_close",
            "surrender_benefit",
            "liability_outgo",
            "gross_withdrawal",
            "chargeable_withdrawal",
            "net_withdrawal_benefit",
        ],
        traced_policy_ids=frozenset({"MYGA-TRACE-01"}),
    )

    result = run_policy(data, policy, FORMULA_FUNCTIONS)
    assert result.error is None, result.error

    # Filter trace rows for Month 12
    m12_traces = {
        row["variable_name"]: row
        for row in result.trace_rows
        if row.get("projection_month") == 12
    }

    required_trace_vars = [
        "account_value_open",
        "gross_withdrawal",
        "free_withdrawal_allowance",
        "free_withdrawal_available",
        "chargeable_withdrawal",
        "surrender_charge_rate",
        "surrender_charge_on_withdrawal",
        "withdrawal_mva",
        "net_withdrawal_benefit",
        "account_value_before_credit",
        "interest_credited",
        "account_value_close",
        "mgsv_close",
        "surrender_benefit",
        "liability_outgo",
    ]

    for var in required_trace_vars:
        assert var in m12_traces, f"Missing required trace variable in Month 12: {var}"
        node = m12_traces[var]
        raw = node["output_value"]
        val = raw["value"] if isinstance(raw, dict) else raw
        assert isinstance(val, (int, float)), f"Variable {var} output must be numeric, got {val}"
        assert val is not None

    def _get_val(var_name: str) -> float:
        raw = m12_traces[var_name]["output_value"]
        return float(raw["value"] if isinstance(raw, dict) else raw)

    # Verify arithmetic specifics for the $20,000 excess withdrawal
    # Gross withdrawal requested = 20,000
    assert _get_val("gross_withdrawal") == 20000.0

    # Chargeable withdrawal = 20,000 - free allowance (> 0)
    chargeable = _get_val("chargeable_withdrawal")
    assert chargeable > 0.0

    # Surrender charge on withdrawal = chargeable * rate (> 0)
    sc = _get_val("surrender_charge_on_withdrawal")
    assert sc > 0.0

    # Net withdrawal benefit = gross - sc + mva
    net_benefit = _get_val("net_withdrawal_benefit")
    assert net_benefit < 20000.0
    assert net_benefit > 0.0
