"""Registry of pure formula functions — populated by product modules."""

from typing import Any, Callable

FORMULA_FUNCTIONS: dict[str, Callable[..., Any]] = {}


def register_function(key: str, func: Callable[..., Any]) -> None:
    """Register a formula function under a unique key."""
    FORMULA_FUNCTIONS[key] = func


def get_function(key: str) -> Callable[..., Any] | None:
    """Get a registered formula function by key."""
    return FORMULA_FUNCTIONS.get(key)


def list_functions() -> list[str]:
    """Return all registered function keys."""
    return list(FORMULA_FUNCTIONS.keys())
