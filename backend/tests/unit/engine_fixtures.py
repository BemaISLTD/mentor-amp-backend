"""Helpers that build in-memory RunData from the SPIA illustrative model definition."""

from datetime import date

from app.core.formula_engine.formulas import FORMULA_FUNCTIONS
from app.core.projection_engine.engine import order_formulas
from app.core.projection_engine.run_data import (
    FormulaSpec,
    PolicyRecord,
    RunData,
    ScenarioSpec,
    TableSpec,
    VariableSpec,
)
from app.products.spia_lite import config as spia

MAX_TABLE_AGE = spia.MORTALITY_TABLE_MAX_AGE


def spia_variables() -> dict[str, VariableSpec]:
    return {
        item["name"]: VariableSpec(
            name=item["name"],
            kind=item["kind"],
            data_type=item["data_type"],
            unit=item.get("unit"),
            source=dict(item["source"]),
            default_value=item.get("default_value"),
            required=item.get("required", True),
            display_name=item.get("display_name"),
        )
        for item in spia.VARIABLES
    }


def spia_formulas() -> list[FormulaSpec]:
    return order_formulas(
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


def mortality_table(overrides: dict[tuple[int, str], float] | None = None) -> TableSpec:
    rows = []
    for age in range(40, MAX_TABLE_AGE + 1):
        for gender in ("F", "M"):
            qx = spia.gompertz_qx(age, gender)
            if overrides and (age, gender) in overrides:
                qx = overrides[(age, gender)]
            rows.append({"age": age, "gender": gender, "qx": qx})
    table = TableSpec(
        id="table-mortality",
        name=spia.MORTALITY_TABLE_NAME,
        key_columns=("age", "gender"),
        value_column="qx",
        rows=rows,
    )
    assert table.build_index() == []
    return table


def make_run_data(
    policies: list[dict],
    horizon: int = 24,
    overrides: tuple[dict, ...] = (),
    traced: frozenset[str] = frozenset(),
    table: TableSpec | None = None,
    output_variables: list[str] | None = None,
) -> RunData:
    spia.register_functions()
    mortality = table or mortality_table()
    return RunData(
        variables=spia_variables(),
        formulas=spia_formulas(),
        tables={mortality.name: mortality},
        policies=[
            PolicyRecord(policy_id=p["policy_id"], data=p, dataset_id="ds-1", dataset_name="inforce.csv")
            for p in policies
        ],
        scenario=ScenarioSpec(id="scn-1", name="Test scenario", overrides=overrides),
        valuation_date=date(2026, 12, 31),
        horizon_months=horizon,
        output_variables=output_variables
        or ["survival_probability", "expected_payment", "pv_expected_payment", "reserve"],
        traced_policy_ids=traced,
    )


POLICY = {
    "policy_id": "SPIA-0001",
    "product_type": "SPIA",
    "issue_date": "2022-03-01",
    "issue_age": "67",
    "gender": "m",
    "premium": "250000",
    "monthly_payment": "1650",
}

FUNCTIONS = FORMULA_FUNCTIONS
