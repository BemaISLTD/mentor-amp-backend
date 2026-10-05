"""Registry of executable formula implementations — populated by product modules at startup.

A formula function is a pure Python callable ``f(**inputs) -> float``. The keyword names are
exactly the formula's dependency variable names. Functions must not read the database, files,
the clock or random state (contract §F.4). Product modules register them through
``app.products.registry.register_all_products()``.

Each registration records the implementation's *identity*: module, qualified name, an optional
implementation version and an ``implementation_fingerprint`` — a hash of the function's compiled
code (bytecode, constants, names, defaults and closure values, recursively for nested code),
keyed to the Python version. A run package freezes that identity; before a run executes, the
fingerprint of the callable currently registered under the same key is recomputed and must
match, so a submitted run can never silently execute different code.

The fingerprint covers the function's own code. Code it calls (helpers, library functions) is
covered by the build identity (``app.services.build_info``), which a run package also freezes.

Registering the same key again is a no-op only if the implementation identity is identical; a
different implementation under an existing key raises ``FormulaRegistrationConflict``.
"""

import inspect
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from types import CodeType, FunctionType
from typing import Any

from app.core.execution.fingerprints import fingerprint


class FormulaRegistrationConflict(Exception):
    """A different implementation was registered under a key that already has one."""


def _code_identity(code: CodeType) -> dict[str, Any]:
    return {
        "bytecode": code.co_code.hex(),
        "exception_table": getattr(code, "co_exceptiontable", b"").hex(),
        "constants": [
            _code_identity(const) if isinstance(const, CodeType) else repr(const)
            for const in code.co_consts
        ],
        "names": list(code.co_names),
        "varnames": list(code.co_varnames),
        "freevars": list(code.co_freevars),
        "cellvars": list(code.co_cellvars),
        "argcount": code.co_argcount,
        "posonlyargcount": code.co_posonlyargcount,
        "kwonlyargcount": code.co_kwonlyargcount,
        "flags": code.co_flags,
    }


def implementation_fingerprint(func: Callable[..., Any]) -> str:
    """Deterministic identity of a plain Python function's executable code."""
    if not isinstance(func, FunctionType):
        raise TypeError(
            f"Formula implementations must be plain Python functions, got {type(func).__name__}."
        )
    closure = [repr(cell.cell_contents) for cell in func.__closure__ or ()]
    return fingerprint({
        "python": sys.implementation.cache_tag,
        "module": func.__module__,
        "qualname": func.__qualname__,
        "code": _code_identity(func.__code__),
        "defaults": repr(func.__defaults__),
        "kwdefaults": repr(sorted((func.__kwdefaults__ or {}).items())),
        "closure": closure,
    })


@dataclass(frozen=True)
class RegisteredFormulaFunction:
    key: str
    func: Callable[..., Any]
    module: str
    qualname: str
    implementation_version: str | None
    implementation_fingerprint: str
    expression_text: str | None = None
    explanation: str | None = None
    illustrative: bool = False

    def identity(self) -> dict[str, Any]:
        """What a run package freezes for this implementation."""
        return {
            "module": self.module,
            "qualname": self.qualname,
            "implementation_version": self.implementation_version,
            "implementation_fingerprint": self.implementation_fingerprint,
        }


FORMULA_REGISTRY: dict[str, RegisteredFormulaFunction] = {}


class _CallableView(Mapping):
    """Read-only ``key -> callable`` view of the registry (what the pure engine receives)."""

    def __getitem__(self, key: str) -> Callable[..., Any]:
        return FORMULA_REGISTRY[key].func

    def __iter__(self) -> Iterator[str]:
        return iter(FORMULA_REGISTRY)

    def __len__(self) -> int:
        return len(FORMULA_REGISTRY)


FORMULA_FUNCTIONS: Mapping[str, Callable[..., Any]] = _CallableView()
# Registration records carry expression_text / explanation (kept under the old name).
FORMULA_METADATA: Mapping[str, RegisteredFormulaFunction] = FORMULA_REGISTRY


def register_function(
    key: str,
    func: Callable[..., Any],
    *,
    expression_text: str | None = None,
    explanation: str | None = None,
    illustrative: bool = False,
    implementation_version: str | None = None,
) -> RegisteredFormulaFunction:
    """Register an implementation under ``key``; identical re-registration is a no-op."""
    registration = RegisteredFormulaFunction(
        key=key,
        func=func,
        module=func.__module__,
        qualname=func.__qualname__,
        implementation_version=implementation_version,
        implementation_fingerprint=implementation_fingerprint(func),
        expression_text=expression_text,
        explanation=explanation,
        illustrative=illustrative,
    )
    existing = FORMULA_REGISTRY.get(key)
    if existing is not None:
        if existing.identity() != registration.identity():
            raise FormulaRegistrationConflict(
                f"Formula function '{key}' is already registered with a different implementation "
                f"({existing.module}.{existing.qualname} "
                f"{existing.implementation_fingerprint[:12]}… vs {registration.module}."
                f"{registration.qualname} {registration.implementation_fingerprint[:12]}…)."
            )
        return existing
    FORMULA_REGISTRY[key] = registration
    return registration


def get_registration(key: str) -> RegisteredFormulaFunction | None:
    return FORMULA_REGISTRY.get(key)


def get_function(key: str) -> Callable[..., Any] | None:
    """Get a registered formula function by key."""
    registration = FORMULA_REGISTRY.get(key)
    return registration.func if registration else None


def list_functions() -> list[str]:
    """Return all registered function keys."""
    return list(FORMULA_REGISTRY.keys())


def source_file(func: Callable[..., Any]) -> str | None:
    """Where an implementation is defined (for error messages)."""
    try:
        return inspect.getsourcefile(func)
    except TypeError:
        return None
