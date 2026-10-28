"""Safe, deterministic expression language for governed actuarial formulas.

The DSL deliberately interprets a small Python-expression AST.  It never compiles or executes
user-provided source, and every node, operator, function and function argument is explicitly
allowed below.
"""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass
from typing import Any, Mapping


MAX_EXPRESSION_LENGTH = 10_000
MAX_AST_NODES = 500
MAX_EXPONENT = 1_200
MAX_ABS_VALUE = 10 ** 300


class FormulaDslError(ValueError):
    """Base class for errors safe to return through the Formula Studio API."""

    code = "FORMULA_DSL_ERROR"

    def __init__(self, message: str, *, line: int | None = None, column: int | None = None):
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "line": self.line,
            "column": self.column,
        }


class FormulaSyntaxError(FormulaDslError):
    code = "FORMULA_SYNTAX_ERROR"


class FormulaEvaluationError(FormulaDslError):
    code = "FORMULA_EVALUATION_ERROR"


@dataclass(frozen=True)
class DependencyInfo:
    dependencies: list[str]
    prior_dependencies: list[str]
    functions_used: list[str]


@dataclass(frozen=True)
class CompiledDslExpression:
    expression_text: str
    tree: ast.Expression
    dependency_info: DependencyInfo


_BINARY_OPERATORS = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
    ast.Div: lambda left, right: left / right,
    ast.FloorDiv: lambda left, right: left // right,
    ast.Mod: lambda left, right: left % right,
}
_UNARY_OPERATORS = {
    ast.UAdd: lambda value: +value,
    ast.USub: lambda value: -value,
    ast.Not: lambda value: not value,
}
_COMPARISON_OPERATORS = {
    ast.Eq: lambda left, right: left == right,
    ast.NotEq: lambda left, right: left != right,
    ast.Lt: lambda left, right: left < right,
    ast.LtE: lambda left, right: left <= right,
    ast.Gt: lambda left, right: left > right,
    ast.GtE: lambda left, right: left >= right,
}
_FUNCTION_NAMES = {
    "min", "max", "abs", "round", "exp", "log", "sqrt", "pow", "clamp",
    "if_else", "coalesce", "prior",
}


def _location(node: ast.AST) -> dict[str, int | None]:
    return {"line": getattr(node, "lineno", None), "column": getattr(node, "col_offset", None)}


def _syntax(message: str, node: ast.AST) -> FormulaSyntaxError:
    return FormulaSyntaxError(message, **_location(node))


def _parse(expression_text: str) -> ast.Expression:
    if not isinstance(expression_text, str) or not expression_text.strip():
        raise FormulaSyntaxError("Expression text must not be empty.")
    if len(expression_text) > MAX_EXPRESSION_LENGTH:
        raise FormulaSyntaxError(
            f"Expression exceeds the maximum length of {MAX_EXPRESSION_LENGTH} characters."
        )
    try:
        tree = ast.parse(expression_text, mode="eval")
    except (SyntaxError, ValueError, MemoryError) as exc:
        raise FormulaSyntaxError(
            getattr(exc, "msg", None) or "Expression could not be parsed.",
            line=getattr(exc, "lineno", None),
            column=getattr(exc, "offset", None),
        ) from exc
    if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
        raise FormulaSyntaxError(f"Expression exceeds the maximum of {MAX_AST_NODES} syntax nodes.")
    _Validator().visit(tree)
    return tree


class _Validator(ast.NodeVisitor):
    """Reject everything except the documented expression grammar."""

    def generic_visit(self, node: ast.AST) -> None:
        allowed = (
            ast.Expression, ast.Constant, ast.Name, ast.Load, ast.BinOp, ast.UnaryOp,
            ast.BoolOp, ast.Compare, ast.IfExp, ast.Call, ast.keyword,
            ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
            ast.UAdd, ast.USub, ast.Not, ast.And, ast.Or,
            ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
        )
        if not isinstance(node, allowed):
            raise _syntax(f"Syntax '{type(node).__name__}' is not allowed.", node)
        super().generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if type(node.value) not in (int, float, bool):
            raise _syntax("Only numeric and boolean constants are allowed.", node)
        if isinstance(node.value, float) and not math.isfinite(node.value):
            raise _syntax("Non-finite numeric constants are not allowed.", node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id.startswith("_") or "__" in node.id:
            raise _syntax("Private and dunder names are not allowed.", node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if type(node.op) not in (*_BINARY_OPERATORS, ast.Pow):
            raise _syntax(f"Operator '{type(node.op).__name__}' is not allowed.", node)
        self.visit(node.left)
        self.visit(node.right)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
        if type(node.op) not in _UNARY_OPERATORS:
            raise _syntax(f"Unary operator '{type(node.op).__name__}' is not allowed.", node)
        self.visit(node.operand)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        if type(node.op) not in (ast.And, ast.Or):
            raise _syntax(f"Boolean operator '{type(node.op).__name__}' is not allowed.", node)
        for value in node.values:
            self.visit(value)

    def visit_Compare(self, node: ast.Compare) -> None:
        if any(type(op) not in _COMPARISON_OPERATORS for op in node.ops):
            raise _syntax("The comparison operator is not allowed.", node)
        self.visit(node.left)
        for comparator in node.comparators:
            self.visit(comparator)

    def visit_Call(self, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTION_NAMES:
            raise _syntax("Only whitelisted direct function calls are allowed.", node)
        if any(keyword.arg is None for keyword in node.keywords):
            raise _syntax("Expanded keyword arguments are not allowed.", node)
        if node.func.id == "prior":
            if not 1 <= len(node.args) <= 2 or node.keywords:
                raise _syntax("prior() accepts a variable name and an optional positional default.", node)
            if not isinstance(node.args[0], ast.Name):
                raise _syntax("The first argument to prior() must be a variable name.", node.args[0])
            self.visit(node.args[0])
            if len(node.args) == 2:
                self.visit(node.args[1])
            return
        name = node.func.id
        positional_count = len(node.args)
        keyword_names = [keyword.arg for keyword in node.keywords]
        if name in {"min", "max"} and (positional_count < 1 or node.keywords):
            raise _syntax(f"{name}() requires one or more positional arguments.", node)
        if name in {"abs", "exp", "log", "sqrt"} and (
            positional_count != 1 or node.keywords
        ):
            raise _syntax(f"{name}() requires exactly one positional argument.", node)
        if name == "round" and (
            positional_count not in {1, 2}
            or any(keyword != "ndigits" for keyword in keyword_names)
            or len(keyword_names) > 1
            or (positional_count == 2 and keyword_names)
        ):
            raise _syntax("round() accepts a value and one optional ndigits argument.", node)
        required_arguments = {"pow": 2, "clamp": 3, "if_else": 3, "coalesce": 2}
        if name in required_arguments and (
            positional_count != required_arguments[name] or node.keywords
        ):
            raise _syntax(
                f"{name}() requires exactly {required_arguments[name]} positional arguments.", node,
            )
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)


class _DependencyExtractor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.dependencies: set[str] = set()
        self.prior_dependencies: set[str] = set()
        self.functions_used: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if node.id not in _FUNCTION_NAMES:
            self.dependencies.add(node.id)

    def visit_Call(self, node: ast.Call) -> None:
        name = node.func.id  # validated by _parse
        self.functions_used.add(name)
        if name == "prior":
            self.prior_dependencies.add(node.args[0].id)
            for arg in node.args[1:]:
                self.visit(arg)
            return
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)


def compile_expression(expression_text: str) -> CompiledDslExpression:
    """Validate syntax once, extract dependencies once, and return frozen compiled expression."""
    tree = _parse(expression_text)
    extractor = _DependencyExtractor()
    extractor.visit(tree)
    info = DependencyInfo(
        dependencies=sorted(extractor.dependencies),
        prior_dependencies=sorted(extractor.prior_dependencies),
        functions_used=sorted(extractor.functions_used),
    )
    return CompiledDslExpression(
        expression_text=expression_text,
        tree=tree,
        dependency_info=info,
    )


def extract_dependencies(expression_text: str) -> DependencyInfo:
    """Validate an expression and return its deterministic dependency metadata."""
    return compile_expression(expression_text).dependency_info


def validate_expression(expression_text: str) -> DependencyInfo:
    """Validate syntax and return dependency metadata when successful."""
    return extract_dependencies(expression_text)


def _checked_power(base: Any, exponent: Any, node: ast.AST) -> Any:
    if isinstance(exponent, bool) or not isinstance(exponent, (int, float)):
        raise FormulaEvaluationError("Exponent must be numeric.", **_location(node))
    if abs(exponent) > MAX_EXPONENT:
        raise FormulaEvaluationError(
            f"Exponent magnitude must not exceed {MAX_EXPONENT}.", **_location(node)
        )
    try:
        result = base ** exponent
    except (ArithmeticError, TypeError, ValueError, OverflowError) as exc:
        raise FormulaEvaluationError(f"Power calculation failed: {exc}", **_location(node)) from exc
    if isinstance(result, complex):
        raise FormulaEvaluationError("Complex results are not supported.", **_location(node))
    return result


class _Evaluator:
    def __init__(self, inputs: Mapping[str, Any], prior_inputs: Mapping[str, Any]):
        self.inputs = inputs
        self.prior_inputs = prior_inputs

    def evaluate(self, node: ast.AST) -> Any:
        try:
            result = self._evaluate(node)
            if isinstance(result, (int, float)) and not isinstance(result, bool):
                if isinstance(result, float) and not math.isfinite(result):
                    raise FormulaEvaluationError(
                        "Formula produced a non-finite numeric value.", **_location(node)
                    )
                if abs(result) > MAX_ABS_VALUE:
                    raise FormulaEvaluationError(
                        "Formula result exceeds the supported numeric magnitude.", **_location(node)
                    )
            return result
        except FormulaDslError:
            raise
        except ZeroDivisionError as exc:
            raise FormulaEvaluationError("Division by zero.", **_location(node)) from exc
        except (ArithmeticError, TypeError, ValueError, OverflowError) as exc:
            raise FormulaEvaluationError(str(exc) or type(exc).__name__, **_location(node)) from exc

    def _evaluate(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return self.evaluate(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in self.inputs:
                raise FormulaEvaluationError(
                    f"Missing input variable '{node.id}'.", **_location(node)
                )
            return self.inputs[node.id]
        if isinstance(node, ast.BinOp):
            left, right = self.evaluate(node.left), self.evaluate(node.right)
            if isinstance(node.op, ast.Pow):
                return _checked_power(left, right, node)
            return _BINARY_OPERATORS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp):
            return _UNARY_OPERATORS[type(node.op)](self.evaluate(node.operand))
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                result = self.evaluate(node.values[0])
                for value in node.values[1:]:
                    if not result:
                        return result
                    result = self.evaluate(value)
                return result
            result = self.evaluate(node.values[0])
            for value in node.values[1:]:
                if result:
                    return result
                result = self.evaluate(value)
            return result
        if isinstance(node, ast.Compare):
            left = self.evaluate(node.left)
            for operator, comparator in zip(node.ops, node.comparators):
                right = self.evaluate(comparator)
                if not _COMPARISON_OPERATORS[type(operator)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            branch = node.body if self.evaluate(node.test) else node.orelse
            return self.evaluate(branch)
        if isinstance(node, ast.Call):
            return self._call(node)
        raise FormulaEvaluationError(
            f"Unsupported syntax '{type(node).__name__}'.", **_location(node)
        )

    def _call(self, node: ast.Call) -> Any:
        name = node.func.id
        if name == "prior":
            variable_name = node.args[0].id
            if variable_name in self.prior_inputs:
                return self.prior_inputs[variable_name]
            return self.evaluate(node.args[1]) if len(node.args) == 2 else 0.0
        if name == "if_else":
            if node.keywords or len(node.args) != 3:
                raise FormulaEvaluationError(
                    "if_else() requires exactly three positional arguments.", **_location(node)
                )
            return self.evaluate(node.args[1] if self.evaluate(node.args[0]) else node.args[2])
        if name == "coalesce":
            if node.keywords or len(node.args) != 2:
                raise FormulaEvaluationError(
                    "coalesce() requires exactly two positional arguments.", **_location(node)
                )
            first = self.evaluate(node.args[0])
            return self.evaluate(node.args[1]) if first is None else first
        args = [self.evaluate(arg) for arg in node.args]
        kwargs = {keyword.arg: self.evaluate(keyword.value) for keyword in node.keywords}
        if name == "min":
            return min(*args, **kwargs)
        if name == "max":
            return max(*args, **kwargs)
        if name == "abs":
            return abs(*args, **kwargs)
        if name == "round":
            return round(*args, **kwargs)
        if name == "exp":
            return math.exp(*args, **kwargs)
        if name == "log":
            if not args or args[0] <= 0:
                raise FormulaEvaluationError("log() requires a value greater than zero.", **_location(node))
            return math.log(*args, **kwargs)
        if name == "sqrt":
            if not args or args[0] < 0:
                raise FormulaEvaluationError("sqrt() requires a non-negative value.", **_location(node))
            return math.sqrt(*args, **kwargs)
        if name == "pow":
            if kwargs or len(args) != 2:
                raise FormulaEvaluationError("pow() requires exactly two positional arguments.", **_location(node))
            return _checked_power(args[0], args[1], node)
        if name == "clamp":
            if kwargs or len(args) != 3:
                raise FormulaEvaluationError("clamp() requires exactly three positional arguments.", **_location(node))
            value, low, high = args
            if low > high:
                raise FormulaEvaluationError("clamp() low bound must not exceed high bound.", **_location(node))
            return max(low, min(high, value))
        raise FormulaEvaluationError(f"Function '{name}' is not supported.", **_location(node))


def evaluate_compiled(
    compiled: CompiledDslExpression,
    inputs: Mapping[str, Any],
    prior_inputs: Mapping[str, Any] | None = None,
) -> Any:
    """Evaluate a pre-compiled AST expression against explicit current/prior input mappings."""
    result = _Evaluator(inputs, prior_inputs or {}).evaluate(compiled.tree)
    if not isinstance(result, (int, float, bool)):
        raise FormulaEvaluationError("Formula result must be numeric or boolean.")
    return result


def evaluate_expression(
    expression_text: str,
    inputs: Mapping[str, Any],
    prior_inputs: Mapping[str, Any] | None = None,
) -> Any:
    """Validate, compile, and evaluate an expression against explicit current/prior input mappings."""
    compiled = compile_expression(expression_text)
    return evaluate_compiled(compiled, inputs, prior_inputs)
