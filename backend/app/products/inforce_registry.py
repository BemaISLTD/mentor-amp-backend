"""Generic product inforce schema and validation registry.

Allows products (SPIA, MYGA, future FIA/RILA/PRT/Life) to register their own
inforce field requirements, column aliases, and product-specific validation rules
without hardcoding product types in Data Manager.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

COMMON_INFORCE_FIELDS: dict[str, str] = {
    "policy_id": "string",
    "product_type": "string",
    "issue_date": "date",
    "issue_age": "integer",
    "gender": "string",
    "premium": "number",
}


def _normalize_version(version: str | None) -> str:
    if not version:
        return "v1"
    v = str(version).strip().lower()
    if "/" in v:
        v = v.split("/")[-1]
    if not v.startswith("v") and v.isdigit():
        v = f"v{v}"
    return v


@dataclass(frozen=True)
class ProductInforceDefinition:
    """Registered inforce configuration for a specific product code and schema version."""

    product_code: str
    schema_version: str = "v1"
    fields: dict[str, str] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    amount_fields: tuple[str, ...] = ("premium",)
    negative_allowed: bool = False
    validate_row: Callable[[int, dict[str, Any], list[dict[str, Any]]], None] | None = None

    def all_required_fields(self) -> dict[str, str]:
        """Combine common and product-specific required fields."""
        return {**COMMON_INFORCE_FIELDS, **self.fields}


_PRODUCT_INFORCE_REGISTRY: dict[tuple[str, str], ProductInforceDefinition] = {}


def register_inforce_product(definition: ProductInforceDefinition) -> None:
    """Register or replace an inforce product definition."""
    code = definition.product_code.strip().upper()
    ver = _normalize_version(definition.schema_version)
    _PRODUCT_INFORCE_REGISTRY[(code, ver)] = definition
    if ver == "v1":
        _PRODUCT_INFORCE_REGISTRY[(code, "")] = definition


def get_product_inforce_definition(
    product_code: str, schema_version: str | None = None
) -> ProductInforceDefinition | None:
    """Look up an inforce product definition by case-insensitive code and schema version.

    If an explicit schema_version is requested, only that exact registered version is
    returned; it must NEVER fall back to v1 or default.
    Only when schema_version is None or omitted does it fall back to the default v1 schema.
    """
    code = product_code.strip().upper()
    if schema_version:
        ver = _normalize_version(schema_version)
        return _PRODUCT_INFORCE_REGISTRY.get((code, ver))
    # Fallback to (code, "v1") or (code, "") only when no explicit version was supplied
    defn = _PRODUCT_INFORCE_REGISTRY.get((code, "v1"))
    if defn is not None:
        return defn
    return _PRODUCT_INFORCE_REGISTRY.get((code, ""))


def get_registered_inforce_products() -> dict[tuple[str, str], ProductInforceDefinition]:
    """Return an immutable snapshot of all registered inforce products."""
    return dict(_PRODUCT_INFORCE_REGISTRY)


def clear_product_inforce_registry() -> None:
    """Reset registry to initial state (useful in test teardown)."""
    _PRODUCT_INFORCE_REGISTRY.clear()
    _register_builtin_products()


def _issue(
    severity: str,
    code: str,
    message: str,
    row: int | None = None,
    column: str | None = None,
    value: Any = None,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "row_number": row,
        "column_name": column,
        "value": None if value is None else str(value),
    }


def _validate_spia_row(
    number: int,
    item: dict[str, Any],
    issues: list[dict[str, Any]],
) -> None:
    pass


def _validate_myga_row(
    number: int,
    item: dict[str, Any],
    issues: list[dict[str, Any]],
) -> None:
    period = item.get("guarantee_period_years")
    if isinstance(period, int) and period != 5:
        issues.append(
            _issue(
                "error",
                "INVALID_GUARANTEE_PERIOD",
                "This illustrative MYGA product requires a five-year guarantee period.",
                number,
                "guarantee_period_years",
                period,
            )
        )
    rate = item.get("gmsv_rate")
    if isinstance(rate, (int, float)) and math.isfinite(rate) and rate < 0.0015:
        issues.append(
            _issue(
                "error",
                "GMSV_RATE_BELOW_MINIMUM",
                "MYGA gmsv_rate must be at least 0.15%.",
                number,
                "gmsv_rate",
                rate,
            )
        )


def _register_builtin_products() -> None:
    # 1. SPIA
    register_inforce_product(
        ProductInforceDefinition(
            product_code="SPIA",
            schema_version="v1",
            fields={"monthly_payment": "number"},
            aliases={},
            amount_fields=("premium", "monthly_payment"),
            negative_allowed=True,
            validate_row=_validate_spia_row,
        )
    )

    # 2. MYGA (mentoramp.myga.inforce/v1)
    register_inforce_product(
        ProductInforceDefinition(
            product_code="MYGA",
            schema_version="v1",
            fields={
                "guarantee_period_years": "integer",
                "declared_rate": "number",
                "gmir": "number",
                "gmsv_rate": "number",
                "mva_ref_yield_at_issue": "number",
                "account_value": "number",
                "mgsv_value": "number",
                "free_wd_base": "number",
            },
            aliases={
                "guarantee_term_years": "guarantee_period_years",
                "credited_rate": "declared_rate",
                "mgsv_rate": "gmsv_rate",
            },
            amount_fields=("premium", "account_value", "mgsv_value", "free_wd_base"),
            negative_allowed=False,
            validate_row=_validate_myga_row,
        )
    )


# Automatically register builtins on import
_register_builtin_products()
