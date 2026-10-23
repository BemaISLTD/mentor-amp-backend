"""Decrement methodology isolation tests (DEC-01 through DEC-04).

Verifies that:
- DEC-01: Run A (Best Estimate) and Run B (Zero Decrements) execute independently and produce distinct results.
- DEC-02: Run A and Run B execute concurrently without cross-thread contamination or mutable global leakage.
- DEC-03: Repeating Run A after Run B reproduces the exact bit-for-bit results of initial Run A.
- DEC-04: Frozen run configuration identifies the decrement methodology identity sufficiently for deterministic replay.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import pytest

from app.core.execution import run_package
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
from app.products.myga_lite.decrements import (
    BenchmarkZeroDecrements,
    IllustrativeBestEstimateDecrements,
    get_current_decrement_methodology,
)


def _build_test_run_data(
    decrement_methodology,
    horizon_months: int = 12,
    policy_id: str = "POL-DEC-001",
) -> tuple[RunData, PolicyRecord, dict]:
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
        rows=[{"age": age, "gender": "M", "qx": 0.012} for age in range(40, 100)]
        + [{"age": age, "gender": "F", "qx": 0.010} for age in range(40, 100)],
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
            "issue_age": 60,
            "gender": "M",
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

    scenario = ScenarioSpec(id="Base", name="Base Scenario", overrides=())
    output_variables = [
        "account_value_close",
        "inforce_open",
        "monthly_mortality",
        "monthly_lapse_rate",
        "annuitization_rate",
        "liability_outgo",
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
        decrement_methodology=decrement_methodology,
    )

    func_map = {myga.function_ref(name): func for name, func, *_ in myga._FUNCTIONS}
    func_map.update({myga.function_ref(name): func for name, func in myga._SUPPORT_FUNCTIONS})
    return data, policy, func_map


def test_dec_01_independent_methodology_execution():
    """DEC-01: Run A (Best Estimate) and Run B (Zero Decrements) execute independently with distinct outcomes."""
    data_a, policy_a, funcs_a = _build_test_run_data(IllustrativeBestEstimateDecrements())
    result_a = run_policy(data_a, policy_a, funcs_a)
    assert result_a.error is None

    data_b, policy_b, funcs_b = _build_test_run_data(BenchmarkZeroDecrements())
    result_b = run_policy(data_b, policy_b, funcs_b)
    assert result_b.error is None

    outputs_a = {f"{var}_{m}": val for m, var, val in result_a.outputs}
    outputs_b = {f"{var}_{m}": val for m, var, val in result_b.outputs}

    # Best estimate should have non-zero decrements (mortality, baseline lapse)
    assert outputs_a["monthly_mortality_1"] > 0.0
    assert outputs_a["monthly_lapse_rate_1"] > 0.0
    assert outputs_a["liability_outgo_1"] > outputs_b["liability_outgo_1"]

    # Benchmark zero decrements must have zero mortality and zero lapse rates
    assert outputs_b["monthly_mortality_1"] == 0.0
    assert outputs_b["monthly_lapse_rate_1"] == 0.0

    # Account value mechanics under zero decrements preserves 1.0 inforce persistence
    assert outputs_a["inforce_open_12"] < 1.0
    assert outputs_b["inforce_open_12"] == 1.0


def test_dec_02_concurrent_execution_thread_safety():
    """DEC-02: Run A and Run B execute concurrently without cross-contaminating assumptions."""
    data_a, policy_a, funcs_a = _build_test_run_data(IllustrativeBestEstimateDecrements())
    data_b, policy_b, funcs_b = _build_test_run_data(BenchmarkZeroDecrements())

    expected_mortality_a = myga.monthly_mortality(0.012)
    expected_mortality_b = 0.0

    def run_worker_a(_):
        res = run_policy(data_a, policy_a, funcs_a)
        outputs = {f"{var}_{m}": val for m, var, val in res.outputs}
        assert outputs["monthly_mortality_1"] == pytest.approx(expected_mortality_a, rel=1e-9)
        assert outputs["monthly_lapse_rate_1"] > 0.0
        return outputs["monthly_mortality_1"]

    def run_worker_b(_):
        res = run_policy(data_b, policy_b, funcs_b)
        outputs = {f"{var}_{m}": val for m, var, val in res.outputs}
        assert outputs["monthly_mortality_1"] == expected_mortality_b
        assert outputs["monthly_lapse_rate_1"] == 0.0
        return outputs["monthly_mortality_1"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures_a = [executor.submit(run_worker_a, i) for i in range(20)]
        futures_b = [executor.submit(run_worker_b, i) for i in range(20)]

        for f in futures_a:
            assert f.result() == pytest.approx(expected_mortality_a, rel=1e-9)
        for f in futures_b:
            assert f.result() == expected_mortality_b


def test_dec_03_sequential_reproducibility():
    """DEC-03: Repeat Run A after Run B; Run A must bit-identically reproduce original Run A."""
    data_a, policy_a, funcs_a = _build_test_run_data(IllustrativeBestEstimateDecrements())
    data_b, policy_b, funcs_b = _build_test_run_data(BenchmarkZeroDecrements())

    first_a = run_policy(data_a, policy_a, funcs_a)
    assert first_a.error is None

    mid_b = run_policy(data_b, policy_b, funcs_b)
    assert mid_b.error is None

    second_a = run_policy(data_a, policy_a, funcs_a)
    assert second_a.error is None

    assert first_a.outputs == second_a.outputs
    assert first_a.sums == second_a.sums


def test_dec_04_frozen_configuration_identifies_decrement_methodology():
    """DEC-04: Frozen run configuration identifies the decrement methodology identity for reproducibility."""
    base_config = {
        "formulas": [],
        "variables": [],
        "valuation_date": "2026-01-01",
        "horizon_months": 12,
        "parameters": {"decrement_methodology": "illustrative_best_estimate"},
    }

    run_data_1 = run_package.build_run_data(base_config, [], {})
    assert run_data_1.decrement_methodology == "illustrative_best_estimate"

    alt_config = {
        "formulas": [],
        "variables": [],
        "valuation_date": "2026-01-01",
        "horizon_months": 12,
        "parameters": {"decrement_methodology": "benchmark_zero_decrements"},
    }

    run_data_2 = run_package.build_run_data(alt_config, [], {})
    assert run_data_2.decrement_methodology == "benchmark_zero_decrements"

    # Confirms that methodology identity is carried into RunData from frozen configuration
    assert run_data_1.decrement_methodology != run_data_2.decrement_methodology
