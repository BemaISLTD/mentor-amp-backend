"""Unit tests for the governed Formula Expression DSL."""

import math
from dataclasses import replace

import pytest

from app.core.formula_engine.dsl import (
    FormulaEvaluationError,
    FormulaSyntaxError,
    evaluate_expression,
    extract_dependencies,
)
from app.core.projection_engine.engine import run_policy
from tests.unit.engine_fixtures import FUNCTIONS, POLICY, make_run_data


@pytest.mark.parametrize(
    ("expression", "inputs", "prior_inputs", "expected"),
    [
        ("min(a, b) + max(a, b)", {"a": 2.0, "b": 5.0}, {}, 7.0),
        ("abs(x) + round(y, ndigits=2)", {"x": -3.0, "y": 1.236}, {}, 4.24),
        ("exp(log(x))", {"x": 2.5}, {}, 2.5),
        ("sqrt(x) + pow(y, 2)", {"x": 9.0, "y": 4.0}, {}, 19.0),
        ("clamp(x, 0, 10)", {"x": 12.0}, {}, 10),
        ("if_else(x > 0 and x <= 5, x, 0)", {"x": 3.0}, {}, 3.0),
        ("x if x != 0 else fallback", {"x": 0, "fallback": 7}, {}, 7),
        ("coalesce(x, fallback)", {"x": None, "fallback": 8.0}, {}, 8.0),
        ("prior(balance) + premium", {"premium": 10.0}, {"balance": 90.0}, 100.0),
        ("prior(balance, opening)", {"opening": 75.0}, {}, 75.0),
        ("7 // 2 + 7 % 2", {}, {}, 4),
        ("not flag or other", {"flag": True, "other": True}, {}, True),
    ],
)
def test_dsl_01_evaluates_allowed_expressions(expression, inputs, prior_inputs, expected):
    actual = evaluate_expression(expression, inputs, prior_inputs)
    assert actual == pytest.approx(expected) if isinstance(expected, float) else actual == expected


def test_dsl_01_extracts_current_prior_and_function_dependencies():
    info = extract_dependencies(
        "round(max(0.0, account_value - withdrawal) + prior(account_value, opening), 6)"
    )
    assert info.dependencies == ["account_value", "opening", "withdrawal"]
    assert info.prior_dependencies == ["account_value"]
    assert info.functions_used == ["max", "prior", "round"]


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os')",
        "eval('1 + 1')",
        "exec('pass')",
        "x.__class__",
        "x[0]",
        "lambda x: x",
        "[x for x in values]",
        "sum(values)",
        "(f)(x)",
        "prior('balance')",
    ],
)
def test_dsl_02_rejects_unsafe_or_unlisted_syntax(expression):
    with pytest.raises(FormulaSyntaxError) as caught:
        extract_dependencies(expression)
    assert caught.value.as_dict()["code"] == "FORMULA_SYNTAX_ERROR"


@pytest.mark.parametrize(
    "expression",
    ["sqrt(1, 2)", "log(10, 2)", "abs(x, y)", "min()", "clamp(x, 0)", "round(x, 2, 3)"],
)
def test_dsl_rejects_invalid_function_signatures_during_validation(expression):
    with pytest.raises(FormulaSyntaxError):
        extract_dependencies(expression)


@pytest.mark.parametrize(
    "expression",
    ["1 / 0", "log(0)", "log(-1)", "sqrt(-1)", "10 ** 10000", "pow(10, 10000)"],
)
def test_dsl_03_reports_domain_errors_cleanly(expression):
    with pytest.raises(FormulaEvaluationError) as caught:
        evaluate_expression(expression, {})
    assert caught.value.as_dict()["code"] == "FORMULA_EVALUATION_ERROR"
    assert caught.value.message


def test_dsl_03_rejects_missing_input_cleanly():
    with pytest.raises(FormulaEvaluationError, match="Missing input variable 'x'"):
        evaluate_expression("x + 1", {})


def test_dsl_conditionals_and_defaults_are_lazy():
    assert evaluate_expression("if_else(flag, value, 1 / 0)", {"flag": True, "value": 7}) == 7
    assert evaluate_expression("coalesce(value, 1 / 0)", {"value": 8}) == 8
    assert evaluate_expression("prior(balance, 1 / 0)", {}, {"balance": 9}) == 9


def test_dsl_rejects_non_finite_and_excessive_results():
    with pytest.raises(FormulaEvaluationError, match="numeric magnitude"):
        evaluate_expression("10 ** 301", {})
    with pytest.raises(FormulaEvaluationError, match="numeric or boolean"):
        evaluate_expression("coalesce(value, 0)", {"value": [1, 2]})


def test_dsl_math_result_is_exact_within_machine_precision():
    assert evaluate_expression("exp(log(x))", {"x": 2.5}) == pytest.approx(
        2.5, abs=math.ulp(2.5)
    )


def test_dsl_formula_executes_in_projection_engine_and_is_traced():
    data = make_run_data(
        [POLICY], horizon=2, traced=frozenset({POLICY["policy_id"]}),
        output_variables=["expected_payment"],
    )
    data.formulas = [
        replace(
            formula,
            function_ref="dsl",
            expression_text="monthly_payment * survival_probability",
        ) if formula.output_variable == "expected_payment" else formula
        for formula in data.formulas
    ]
    result = run_policy(data, data.policies[0], FUNCTIONS)
    assert result.error is None, result.error and result.error.as_dict()
    expected_rows = [row for row in result.trace_rows if row["variable_name"] == "expected_payment"]
    assert len(expected_rows) == 2
    assert expected_rows[0]["input_values"].keys() == {"monthly_payment", "survival_probability"}
    assert result.outputs[0][2] == pytest.approx(
        float(POLICY["monthly_payment"]) * expected_rows[0]["input_values"]["survival_probability"]
    )


def test_dsl_prior_retains_a_raw_input_not_in_the_current_dependency_graph():
    data = make_run_data(
        [POLICY], horizon=2, traced=frozenset({POLICY["policy_id"]}),
        output_variables=["expected_payment"],
    )
    data.formulas = [
        replace(
            formula,
            function_ref="dsl",
            dependencies=(),
            expression_text="prior(monthly_payment, 0.0)",
        ) if formula.output_variable == "expected_payment" else formula
        for formula in data.formulas
    ]
    result = run_policy(data, data.policies[0], FUNCTIONS)
    assert result.error is None, result.error and result.error.as_dict()
    values = [row[2] for row in result.outputs if row[1] == "expected_payment"]
    assert values == pytest.approx([0.0, float(POLICY["monthly_payment"])])
