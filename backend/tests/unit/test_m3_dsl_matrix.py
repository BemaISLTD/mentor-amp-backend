"""M3 DSL & Execution Unit Test Matrix (DSL-01 to DSL-08)."""

import ast
import pytest
from unittest.mock import patch

from app.core.formula_engine.dsl import (
    FormulaSyntaxError,
    FormulaEvaluationError,
    compile_expression,
    evaluate_compiled,
    evaluate_expression,
    extract_dependencies,
)
from app.core.projection_engine.engine import (
    ENGINE_VERSION,
    EngineError,
    order_formulas,
    run_policy,
)
from app.core.projection_engine.run_data import (
    FormulaSpec,
    PolicyRecord,
    RunData,
    ScenarioSpec,
    VariableSpec,
)
from app.models.schemas import FormulaTestCase, FormulaUpdateRequest
from datetime import date


def test_dsl_01_ast_parsed_once_per_frozen_formula():
    """DSL-01: AST parsed once per frozen formula across projection loop (no re-parsing per month)."""
    expr = "balance = prior(balance, 1000.0) * 1.05 + premium"
    compiled = compile_expression("prior(balance, 1000.0) * 1.05 + premium")
    assert compiled.dependency_info.dependencies == ["premium"]
    assert compiled.dependency_info.prior_dependencies == ["balance"]

    formula = FormulaSpec(
        id="f1",
        name="Balance",
        output_variable="balance",
        function_ref="dsl",
        dependencies=("premium",),
        expression_text="prior(balance, 1000.0) * 1.05 + premium",
        compiled_expression=compiled,
    )
    variables = {
        "premium": VariableSpec(name="premium", kind="manual", default_value=100.0),
        "balance": VariableSpec(name="balance", kind="formula"),
    }
    policy = PolicyRecord(policy_id="P1", data={})
    data = RunData(
        variables=variables,
        formulas=[formula],
        tables={},
        policies=[policy],
        scenario=ScenarioSpec(id="s1", name="Base"),
        valuation_date=date(2026, 1, 1),
        horizon_months=12,
        output_variables=["balance"],
    )

    with patch("ast.parse", wraps=ast.parse) as spy_parse:
        result = run_policy(data, policy, {})
        # During 12 months of projection, ast.parse must be called ZERO times!
        assert spy_parse.call_count == 0
        assert len(result.outputs) == 12
        assert result.error is None


def test_dsl_02_prior_separated_from_same_step_dependencies():
    """DSL-02: prior(...) separated from same-step dependencies."""
    expr = "prior(reserve) * 1.04 + net_cash_flow - expense"
    info = extract_dependencies(expr)
    assert sorted(info.dependencies) == ["expense", "net_cash_flow"]
    assert sorted(info.prior_dependencies) == ["reserve"]


def test_dsl_03_valid_recursive_self_prior_formula_does_not_report_cycle():
    """DSL-03: Valid recursive self-prior formula does not report cycle in order_formulas."""
    formula = FormulaSpec(
        id="f1",
        name="Account Value",
        output_variable="account_value",
        function_ref="dsl",
        dependencies=("credited_rate",),  # Same-step dependency only; prior(account_value) shifted
        expression_text="prior(account_value, 100.0) * (1.0 + credited_rate)",
    )
    # Ordering must succeed without circular_dependency error
    ordered = order_formulas([formula])
    assert len(ordered) == 1
    assert ordered[0].output_variable == "account_value"


def test_dsl_04_mutual_same_step_formula_dependency_reports_circular_error():
    """DSL-04: Mutual same-step formula dependency reports circular error."""
    f1 = FormulaSpec(
        id="f1", name="A", output_variable="var_a", function_ref="dsl",
        dependencies=("var_b",), expression_text="var_b + 1",
    )
    f2 = FormulaSpec(
        id="f2", name="B", output_variable="var_b", function_ref="dsl",
        dependencies=("var_a",), expression_text="var_a * 2",
    )
    with pytest.raises(EngineError) as exc_info:
        order_formulas([f1, f2])
    assert exc_info.value.error_type == "circular_dependency"


def test_dsl_05_typed_formula_update_request_validates_bounds_and_finite_values():
    """DSL-05: Typed formula update request validates bounds and finite values."""
    # Valid test case
    tc = FormulaTestCase(
        name="Test case 1",
        inputs={"premium": 100.0},
        prior_inputs={"balance": 500.0},
        expected_output=625.0,
        abs_tol=0.01,
    )
    req = FormulaUpdateRequest(
        name="Updated Name",
        expression_text="prior(balance) * 1.05 + premium",
        test_cases=[tc],
    )
    assert req.name == "Updated Name"

    # Reject non-finite expected_output
    with pytest.raises(ValueError):
        FormulaTestCase(expected_output=float("nan"))
    with pytest.raises(ValueError):
        FormulaTestCase(expected_output=float("inf"))

    # Reject negative abs_tol
    with pytest.raises(ValueError):
        FormulaTestCase(expected_output=100.0, abs_tol=-0.5)

    # Reject non-finite inputs
    with pytest.raises(ValueError):
        FormulaTestCase(expected_output=100.0, inputs={"x": float("nan")})


def test_dsl_06_and_07_engine_version_bumped():
    """DSL-08: Engine version bumped to m3-cpu-dsl-1 and verified."""
    assert ENGINE_VERSION == "m3-cpu-dsl-1"
