"""Registry of pure formula functions — populated by product modules at startup.

A formula function is a pure Python callable ``f(**inputs) -> float``. The keyword names are
exactly the formula's dependency variable names. Functions must not read the database, files,
the clock or random state (contract §F.4). Product modules register them through
``app.products.registry.register_all_products()``.
"""

from dataclasses import dataclass
from typing import Any, Callable

FORMULA_FUNCTIONS: dict[str, Callable[..., Any]] = {}


@dataclass(frozen=True)
class FormulaFunctionInfo:
    key: str
    expression_text: str | None = None
    explanation: str | None = None
    illustrative: bool = False


FORMULA_METADATA: dict[str, FormulaFunctionInfo] = {}


def register_function(
    key: str,
    func: Callable[..., Any],
    *,
    expression_text: str | None = None,
    explanation: str | None = None,
    illustrative: bool = False,
) -> None:
    """Register a formula function under a unique key (idempotent)."""
    FORMULA_FUNCTIONS[key] = func
    FORMULA_METADATA[key] = FormulaFunctionInfo(
        key=key,
        expression_text=expression_text,
        explanation=explanation,
        illustrative=illustrative,
    )


def get_function(key: str) -> Callable[..., Any] | None:
    """Get a registered formula function by key."""
    return FORMULA_FUNCTIONS.get(key)


def list_functions() -> list[str]:
    """Return all registered function keys."""
    return list(FORMULA_FUNCTIONS.keys())
