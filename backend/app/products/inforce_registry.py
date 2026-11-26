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


def _validate_indexed_row(number, item, issues):
    """Validate generic state and conservation without selecting contract semantics."""
    def error(column, message):
        issues.append(_issue("error", "INVALID_INDEXED_STATE", message, number, column, item.get(column)))
    amounts = [item.get("fixed_av"), item.get("segment_1_initial_value"), item.get("segment_2_initial_value")]
    av = item.get("account_value")
    if all(isinstance(v, (int, float)) for v in [av, *amounts]):
        if not math.isclose(sum(amounts), av, rel_tol=1e-10, abs_tol=1e-8):
            error("account_value", "Initial segment values and fixed account must sum to account value.")
    for slot in ("segment_1", "segment_2"):
        term, elapsed = item.get(f"{slot}_term_months"), item.get(f"{slot}_elapsed", 0)
        if isinstance(term, (int, float)) and (not math.isfinite(term) or term <= 0 or int(term) != term):
            error(f"{slot}_term_months", "Term must be a positive integer.")
        if isinstance(elapsed, (int, float)) and isinstance(term, (int, float)):
            if (not math.isfinite(elapsed) or not math.isfinite(term)
                    or elapsed < 0 or elapsed >= term or int(elapsed) != elapsed):
                error(f"{slot}_elapsed", "Elapsed months must be an integer below term.")
        strategy = item.get(f"{slot}_strategy")
        allowed = {"ptp", "spread"} if str(item.get("product_type", "")).upper() == "FIA" else {"buffer", "floor"}
        if strategy not in allowed:
            error(f"{slot}_strategy", f"Strategy must be one of {sorted(allowed)}.")
        level = item.get(f"{slot}_start_index")
        if isinstance(level, (int, float)) and level <= 0:
            error(f"{slot}_start_index", "Starting index must be positive.")
        downside = item.get(f"{slot}_downside_rate")
        if isinstance(downside, (int, float)) and not 0 <= downside <= 1:
            error(f"{slot}_downside_rate", "Downside protection must lie in [0, 1].")
        for suffix in ("cap", "participation_rate", "spread"):
            value = item.get(f"{slot}_{suffix}")
            if isinstance(value, (int, float)) and (not math.isfinite(value) or value < 0):
                error(f"{slot}_{suffix}", "Crediting parameters must be finite and nonnegative.")
    for column, allowed in (("interim_method", {"prorated_intrinsic", "external"}),
                            ("withdrawal_mode", {"pro_rata", "dollar_for_dollar"}),
                            ("rollover_rule", {"auto_renew", "sweep_to_fixed"}),
                            ("death_benefit_rule", {"account_value", "greater_of_av_and_guarantee"})):
        if column in item and item[column] not in allowed:
            error(column, f"Unsupported {column}.")


def _product_error(
    issues: list[dict[str, Any]],
    number: int,
    code: str,
    column: str,
    message: str,
    item: dict[str, Any],
) -> None:
    issues.append(_issue("error", code, message, number, column, item.get(column)))


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _optional_number(
    number: int,
    item: dict[str, Any],
    issues: list[dict[str, Any]],
    column: str,
    *,
    integer: bool = False,
) -> float | int | None:
    value = item.get(column)
    if value in (None, ""):
        return None
    try:
        parsed = float(str(value))
        if not math.isfinite(parsed) or (integer and parsed != int(parsed)):
            raise ValueError
        result: float | int = int(parsed) if integer else parsed
        item[column] = result
        return result
    except (TypeError, ValueError, OverflowError):
        _product_error(
            issues,
            number,
            "TYPE_MISMATCH",
            column,
            f"'{column}' must be {'integer' if integer else 'number'}.",
            item,
        )
        return None


def _validate_idi_row(number: int, item: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    code = "INVALID_IDI_STATE"
    if item.get("claim_status") not in {"active", "disabled"}:
        _product_error(issues, number, code, "claim_status", "claim_status must be active or disabled.", item)
    for column, strictly_positive in (
        ("monthly_benefit", True),
        ("annual_premium", False),
        ("elimination_months", False),
        ("max_benefit_months", True),
        ("claim_months_elapsed", False),
        ("expense_per_policy_monthly", False),
    ):
        value = item.get(column)
        invalid = not _finite_number(value) or value < 0 or (strictly_positive and value <= 0)
        if invalid:
            qualifier = "positive" if strictly_positive else "non-negative"
            _product_error(issues, number, code, column, f"{column} must be finite and {qualifier}.", item)
    if item.get("waive_premium_on_disability") not in {0, 1}:
        _product_error(
            issues, number, code, "waive_premium_on_disability",
            "waive_premium_on_disability must be 0 or 1.", item,
        )
    discount_rate = item.get("discount_rate")
    if not _finite_number(discount_rate) or discount_rate <= -1.0:
        _product_error(issues, number, code, "discount_rate", "discount_rate must be finite and greater than -1.", item)
    if item.get("claim_status") == "active" and item.get("claim_months_elapsed") != 0:
        _product_error(
            issues, number, code, "claim_months_elapsed",
            "Active policies require claim_months_elapsed equal to 0.", item,
        )


def _validate_va_row(number: int, item: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    code = "INVALID_VA_STATE"
    amount_columns = (
        "account_value", "eq_account_value", "bond_account_value", "fixed_account_value",
        "benefit_base", "gmdb_base", "withdrawal_amount_monthly",
    )
    rate_columns = (
        "fixed_credited_rate", "me_fee_rate", "admin_fee_rate", "rider_fee_rate",
        "rollup_rate", "gwb_pct",
    )
    for column in (*amount_columns, *rate_columns):
        value = item.get(column)
        if not _finite_number(value) or value < 0.0:
            _product_error(issues, number, code, column, f"{column} must be finite and non-negative.", item)
    discount_rate = item.get("discount_rate")
    if not _finite_number(discount_rate) or discount_rate <= -1.0:
        _product_error(issues, number, code, "discount_rate", "discount_rate must be finite and greater than -1.", item)
    surrender = item.get("surrender_charge_rate")
    if not _finite_number(surrender) or not 0.0 <= surrender <= 1.0:
        _product_error(issues, number, code, "surrender_charge_rate", "surrender_charge_rate must be in [0, 1].", item)
    for column, allowed in (
        ("rider_type", {"NONE", "GMDB_ROP", "GMDB_RATCHET", "GMWB", "GLWB"}),
        ("withdrawal_mode", {"dollar_for_dollar", "pro_rata"}),
        ("fee_conversion_method", {"simple_12th", "effective_monthly_rate", "constant_force_deduction"}),
    ):
        if item.get(column) not in allowed:
            _product_error(issues, number, code, column, f"Unsupported {column}.", item)
    rider_fee_basis = str(item.get("rider_fee_basis") or "account_value").strip()
    item["rider_fee_basis"] = rider_fee_basis
    if rider_fee_basis not in {"account_value", "benefit_base"}:
        _product_error(issues, number, code, "rider_fee_basis", "Unsupported rider_fee_basis.", item)
    gmwb_remaining = _optional_number(number, item, issues, "gmwb_remaining_balance")
    if item.get("rider_type") == "GMWB" and (gmwb_remaining is None or gmwb_remaining < 0.0):
        _product_error(
            issues, number, code, "gmwb_remaining_balance",
            "GMWB requires a non-negative gmwb_remaining_balance.", item,
        )
    components = [item.get(name) for name in ("eq_account_value", "bond_account_value", "fixed_account_value")]
    account_value = item.get("account_value")
    if _finite_number(account_value) and all(_finite_number(value) for value in components):
        if not math.isclose(sum(components), account_value, rel_tol=0.0, abs_tol=1e-6):
            _product_error(
                issues, number, code, "account_value",
                "Subaccount values must sum to account_value within 1e-6.", item,
            )


def _validate_prt_row(number: int, item: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    code = "INVALID_PRT_STATE"
    status = item.get("participant_status")
    form = item.get("form_of_annuity")
    deferral = item.get("deferral_months")
    certain = item.get("certain_months")
    joint_pct = item.get("joint_survivor_pct")
    if status not in {"in_pay", "deferred"}:
        _product_error(issues, number, code, "participant_status", "Unsupported participant_status.", item)
    elif (status == "in_pay" and deferral != 0) or (status == "deferred" and (not _finite_number(deferral) or deferral <= 0)):
        _product_error(issues, number, code, "deferral_months", "in_pay requires 0; deferred requires positive deferral_months.", item)
    if form not in {"life_only", "certain_and_life", "joint_survivor"}:
        _product_error(issues, number, code, "form_of_annuity", "Unsupported form_of_annuity.", item)
    if form == "certain_and_life" and (not _finite_number(certain) or certain <= 0):
        _product_error(issues, number, code, "certain_months", "certain_and_life requires positive certain_months.", item)
    elif form != "certain_and_life" and certain != 0:
        _product_error(issues, number, code, "certain_months", "certain_months must be 0 for this annuity form.", item)
    if not _finite_number(joint_pct) or not 0.0 <= joint_pct <= 1.0:
        _product_error(issues, number, code, "joint_survivor_pct", "joint_survivor_pct must be in [0, 1].", item)
    elif form == "joint_survivor" and joint_pct <= 0.0:
        _product_error(issues, number, code, "joint_survivor_pct", "joint_survivor requires positive joint_survivor_pct.", item)
    for column, strictly_positive in (
        ("monthly_benefit", True), ("cola_rate", False), ("cola_cap", False),
        ("expense_pct_of_benefit", False),
    ):
        value = item.get(column)
        if not _finite_number(value) or value < 0.0 or (strictly_positive and value <= 0.0):
            _product_error(issues, number, code, column, f"{column} has an invalid value.", item)
    discount_rate = item.get("discount_rate")
    if not _finite_number(discount_rate) or discount_rate <= -1.0:
        _product_error(issues, number, code, "discount_rate", "discount_rate must be greater than -1.", item)
    if form == "joint_survivor":
        spouse_age = _optional_number(number, item, issues, "spouse_age", integer=True)
        spouse_gender = str(item.get("spouse_gender") or "").strip().upper()
        item["spouse_gender"] = spouse_gender
        if spouse_age is None or not 0 <= spouse_age <= 120:
            _product_error(issues, number, code, "spouse_age", "joint_survivor requires spouse_age in [0, 120].", item)
        if spouse_gender not in {"M", "F"}:
            _product_error(issues, number, code, "spouse_gender", "joint_survivor requires spouse_gender M or F.", item)


def _validate_life_row(number: int, item: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    code = "INVALID_LIFE_STATE"
    plan = str(item.get("life_plan_type") or "").upper()
    item["life_plan_type"] = plan
    if plan not in {"TERM", "WHOLE_LIFE", "UL", "IUL"}:
        _product_error(issues, number, code, "life_plan_type", "Unsupported life_plan_type.", item)
    if item.get("death_benefit_option") not in {"LEVEL", "INCREASING", "ROP"}:
        _product_error(issues, number, code, "death_benefit_option", "Unsupported death_benefit_option.", item)
    if item.get("valuation_basis_hint") not in {"STATUTORY", "GAAP"}:
        _product_error(issues, number, code, "valuation_basis_hint", "valuation_basis_hint must be STATUTORY or GAAP.", item)
    if item.get("expected_cash_flow_currency") != "USD":
        _product_error(issues, number, code, "expected_cash_flow_currency", "Only USD is supported for this readiness proof.", item)
    for column, strictly_positive in (
        ("face_amount", True), ("annualized_premium", False), ("modal_premium", False),
        ("account_value", False),
    ):
        value = item.get(column)
        if not _finite_number(value) or value < 0.0 or (strictly_positive and value <= 0.0):
            _product_error(issues, number, code, column, f"{column} has an invalid value.", item)

    mode_factors = {"ANNUAL": 1, "SEMI_ANNUAL": 2, "QUARTERLY": 4, "MONTHLY": 12}
    mode = str(item.get("premium_mode") or "").upper()
    item["premium_mode"] = mode
    factor = mode_factors.get(mode)
    modal = item.get("modal_premium")
    annualized = item.get("annualized_premium")
    if factor is None:
        _product_error(issues, number, "INVALID_LIFE_PREMIUM_FREQUENCY", "premium_mode", "Unsupported premium_mode.", item)
    elif _finite_number(modal) and _finite_number(annualized):
        if abs(modal * factor - annualized) > 0.05:
            _product_error(
                issues, number, "INVALID_LIFE_PREMIUM_FREQUENCY", "modal_premium",
                "modal_premium multiplied by premium frequency must equal annualized_premium within 0.05.", item,
            )

    term = _optional_number(number, item, issues, "coverage_term_years", integer=True)
    if plan == "TERM" and (term is None or term <= 0):
        _product_error(issues, number, code, "coverage_term_years", "TERM requires positive coverage_term_years.", item)
    csv = _optional_number(number, item, issues, "cash_surrender_value")
    if csv is not None and csv < 0.0:
        _product_error(issues, number, code, "cash_surrender_value", "cash_surrender_value must be non-negative.", item)
    if plan in {"UL", "IUL"} and csv is not None and _finite_number(item.get("account_value")) and csv > item["account_value"]:
        _product_error(issues, number, code, "cash_surrender_value", "cash_surrender_value cannot exceed account_value.", item)

    coverage_2_id = str(item.get("coverage_2_id") or "").strip()
    if coverage_2_id:
        if coverage_2_id == str(item.get("coverage_id") or "").strip():
            _product_error(issues, number, code, "coverage_2_id", "Coverage IDs must be distinct.", item)
        coverage_2_face = _optional_number(number, item, issues, "coverage_2_face_amount")
        if coverage_2_face is None or coverage_2_face <= 0.0:
            _product_error(issues, number, code, "coverage_2_face_amount", "Secondary coverage requires positive face amount.", item)
    for slot in ("rider_1", "rider_2"):
        rider_code = str(item.get(f"{slot}_code") or "").strip()
        if rider_code:
            rider_face = _optional_number(number, item, issues, f"{slot}_face_amount")
            rider_premium = _optional_number(number, item, issues, f"{slot}_premium")
            if rider_face is None or rider_face <= 0.0:
                _product_error(issues, number, code, f"{slot}_face_amount", "Rider benefit limit must be positive.", item)
            if rider_premium is None or rider_premium < 0.0:
                _product_error(issues, number, code, f"{slot}_premium", "Rider premium must be non-negative.", item)

    seen_coverage_ids = {str(item.get("coverage_id") or "").strip()}
    if coverage_2_id:
        seen_coverage_ids.add(coverage_2_id)
    coverages = item.get("coverages")
    if coverages not in (None, ""):
        if not isinstance(coverages, list):
            _product_error(issues, number, code, "coverages", "coverages must be a structured list.", item)
        else:
            for idx, entry in enumerate(coverages):
                col = f"coverages[{idx}]"
                if not isinstance(entry, dict):
                    issues.append(_issue("error", code, "Each coverage entry must be a mapping object.", number, col, entry))
                    continue
                cov_id = str(entry.get("coverage_id") or "").strip()
                if not cov_id:
                    issues.append(_issue("error", code, "Nested coverage requires non-empty coverage_id.", number, f"{col}.coverage_id", entry.get("coverage_id")))
                elif cov_id in seen_coverage_ids:
                    issues.append(_issue("error", code, "Nested coverage_id must be distinct from existing coverages.", number, f"{col}.coverage_id", cov_id))
                else:
                    seen_coverage_ids.add(cov_id)
                cov_face = entry.get("face_amount")
                if not _finite_number(cov_face) or cov_face <= 0.0:
                    issues.append(_issue("error", code, "Nested coverage requires finite positive face_amount.", number, f"{col}.face_amount", cov_face))
                cov_plan_raw = entry.get("life_plan_type", entry.get("plan_type"))
                cov_plan = None
                if cov_plan_raw not in (None, ""):
                    cov_plan = str(cov_plan_raw).strip().upper()
                    if cov_plan not in {"TERM", "WHOLE_LIFE", "UL", "IUL"}:
                        issues.append(_issue("error", code, "Unsupported nested coverage plan_type.", number, f"{col}.plan_type", cov_plan_raw))
                cov_term = entry.get("coverage_term_years", entry.get("term_years"))
                if cov_term not in (None, ""):
                    if not _finite_number(cov_term) or cov_term <= 0 or int(cov_term) != cov_term:
                        issues.append(_issue("error", code, "Nested coverage term years must be a positive integer.", number, f"{col}.coverage_term_years", cov_term))
                elif cov_plan == "TERM":
                    issues.append(_issue("error", code, "Nested TERM coverage requires positive coverage_term_years.", number, f"{col}.coverage_term_years", cov_term))

    seen_rider_codes = {
        str(item.get(f"{slot}_code") or "").strip().upper()
        for slot in ("rider_1", "rider_2")
        if str(item.get(f"{slot}_code") or "").strip()
    }
    riders = item.get("riders")
    if riders not in (None, ""):
        if not isinstance(riders, list):
            _product_error(issues, number, code, "riders", "riders must be a structured list.", item)
        else:
            for idx, entry in enumerate(riders):
                col = f"riders[{idx}]"
                if not isinstance(entry, dict):
                    issues.append(_issue("error", code, "Each rider entry must be a mapping object.", number, col, entry))
                    continue
                r_code = str(entry.get("rider_code", entry.get("code")) or "").strip().upper()
                if not r_code:
                    issues.append(_issue("error", code, "Nested rider requires non-empty rider_code.", number, f"{col}.rider_code", entry.get("rider_code", entry.get("code"))))
                elif r_code in seen_rider_codes:
                    issues.append(_issue("error", code, "Nested rider_code must be distinct.", number, f"{col}.rider_code", r_code))
                else:
                    seen_rider_codes.add(r_code)
                r_face = entry.get("face_amount", entry.get("benefit_limit"))
                if not _finite_number(r_face) or r_face <= 0.0:
                    issues.append(_issue("error", code, "Nested rider requires finite positive face_amount or benefit_limit.", number, f"{col}.face_amount", r_face))
                if "premium" in entry and entry["premium"] not in (None, ""):
                    r_prem = entry["premium"]
                    if not _finite_number(r_prem) or r_prem < 0.0:
                        issues.append(_issue("error", code, "Nested rider premium must be finite and non-negative.", number, f"{col}.premium", r_prem))


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

    indexed_fields = {"account_value": "number", "fixed_av": "number"}
    for slot in ("segment_1", "segment_2"):
        indexed_fields.update({f"{slot}_{field}": kind for field, kind in {
            "base": "number", "initial_value": "number", "strategy": "string",
            "index_code": "string", "term_months": "integer", "cap": "number",
            "downside_rate": "number", "start_index": "number",
        }.items()})
    for code in ("FIA", "RILA"):
        register_inforce_product(ProductInforceDefinition(
            product_code=code, fields=dict(indexed_fields), aliases={"av": "account_value"},
            amount_fields=("premium", "account_value", "fixed_av", "segment_1_base",
                           "segment_2_base", "segment_1_initial_value", "segment_2_initial_value"),
            validate_row=_validate_indexed_row,
        ))

    register_inforce_product(ProductInforceDefinition(
        product_code="IDI",
        fields={
            "claim_status": "string", "occupation_class": "string",
            "monthly_benefit": "number", "annual_premium": "number",
            "elimination_months": "integer", "max_benefit_months": "integer",
            "claim_months_elapsed": "integer", "expense_per_policy_monthly": "number",
            "waive_premium_on_disability": "integer", "discount_rate": "number",
        },
        amount_fields=("premium", "monthly_benefit", "annual_premium", "expense_per_policy_monthly"),
        validate_row=_validate_idi_row,
    ))

    register_inforce_product(ProductInforceDefinition(
        product_code="VA",
        fields={
            "account_value": "number", "eq_account_value": "number",
            "bond_account_value": "number", "fixed_account_value": "number",
            "eq_fund_code": "string", "bond_fund_code": "string",
            "fixed_credited_rate": "number", "me_fee_rate": "number",
            "admin_fee_rate": "number", "rider_fee_rate": "number",
            "fee_conversion_method": "string", "rider_type": "string",
            "benefit_base": "number", "gmdb_base": "number", "rollup_rate": "number",
            "gwb_pct": "number", "withdrawal_amount_monthly": "number",
            "withdrawal_mode": "string", "surrender_charge_rate": "number",
            "discount_rate": "number",
        },
        aliases={"av": "account_value"},
        amount_fields=(
            "premium", "account_value", "eq_account_value", "bond_account_value",
            "fixed_account_value", "benefit_base", "gmdb_base", "withdrawal_amount_monthly",
        ),
        validate_row=_validate_va_row,
    ))

    register_inforce_product(ProductInforceDefinition(
        product_code="PRT",
        fields={
            "participant_id": "string", "plan_group_id": "string",
            "participant_status": "string", "monthly_benefit": "number",
            "deferral_months": "integer", "form_of_annuity": "string",
            "certain_months": "integer", "joint_survivor_pct": "number",
            "cola_rate": "number", "cola_cap": "number",
            "expense_pct_of_benefit": "number", "discount_rate": "number",
        },
        amount_fields=("premium", "monthly_benefit"),
        validate_row=_validate_prt_row,
    ))

    register_inforce_product(ProductInforceDefinition(
        product_code="LIFE",
        fields={
            "life_plan_type": "string", "underwriting_class": "string",
            "smoker_status": "string", "policy_status": "string",
            "coverage_id": "string", "face_amount": "number",
            "death_benefit_option": "string", "annualized_premium": "number",
            "modal_premium": "number", "premium_mode": "string",
            "account_value": "number", "mortality_table_ref": "string",
            "lapse_table_ref": "string", "valuation_basis_hint": "string",
            "expected_cash_flow_currency": "string",
        },
        aliases={
            "sex": "gender", "smoking_status": "smoker_status",
            "risk_class": "underwriting_class", "sum_assured": "face_amount",
            "annual_premium": "annualized_premium",
        },
        amount_fields=("premium", "face_amount", "annualized_premium", "modal_premium", "account_value"),
        validate_row=_validate_life_row,
    ))

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
