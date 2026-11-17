"""Test adapter from declarative indexed definitions to the shared RunData contract."""

from datetime import date
from app.core.formula_engine.formulas import FORMULA_FUNCTIONS
from app.core.projection_engine.engine import order_formulas, run_policy
from app.core.projection_engine.run_data import FormulaSpec, PolicyRecord, RunData, ScenarioSpec, TableSpec, VariableSpec
from app.products.shared_modules.synthetic import block_rows, index_paths


def project(product, policy=None, months=24, path=0, levels=None):
    product.register_functions()
    code = product.PRODUCT_CODE
    policy = policy or block_rows(code, 1)[0]
    tables = {}
    paths = index_paths(months)
    if levels is not None:
        paths = [{"path_id": 0, "month": month, "index_code": index, "level": level}
                 for month, level in enumerate(levels, 1) for index in ("SP500", "RUSSELL2000")]
    for name, keys, column, rows in (
        (f"{code}_INDEX_PATHS", ("path_id", "month", "index_code"), "level", paths),
        (f"{code}_SURRENDER", ("policy_year",), "rate", [{"policy_year": y, "rate": .05} for y in range(1, 12)]),
        (f"{code}_OPTION_BUDGET", ("policy_year",), "budget", [{"policy_year": y, "budget": .02} for y in range(1, 12)]),
    ):
        table = TableSpec(name, name, keys, column, rows)
        assert not table.build_index()
        tables[name] = table
    variables = {v["name"]: VariableSpec(**{k: val for k, val in v.items() if k != "description"}) for v in product.VARIABLES}
    formulas = order_formulas([FormulaSpec(id=f["output_variable"], **{k: val for k, val in f.items() if k != "unit"}) for f in product.FORMULAS])
    record = PolicyRecord(policy["policy_id"], policy)
    data = RunData(variables, formulas, tables, [record], ScenarioSpec(str(path), str(path),
                   ({"target_variable": "path_id", "operation": "set", "value": path},)),
                   date(2025, 12, 31), months, [v["variable_name"] for v in product.PUBLISHED_OUTPUTS],
                   frozenset({record.policy_id}))
    result = run_policy(data, record, FORMULA_FUNCTIONS)
    return result


def value(result, name, month):
    return next(v for m, n, v in result.outputs if n == name and m == month)
