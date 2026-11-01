"""Generic Rollforward & Attribution Framework (Work Package M5C).

Per CAS §7.2, Milestone 5 Architecture Invariants, and ACT-DEC-08:
- The authoritative launch requirement is a configurable rollforward/attribution
  framework supporting applicable methodology-defined attribution components.
- Do NOT hardcode a universal six-column accounting waterfall.
- Mathematical Continuity Invariant:
    Beginning Balance + Sum(Attributed Movements) = Ending Balance (± tolerance)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import math
from typing import Any, Final
import uuid

from sqlalchemy.orm import Session

from app.core.valuation.taxonomy import (
    APPROVED_METHODOLOGIES,
    validate_basis,
    validate_methodology,
)
from app.db.models.rollforward import RollforwardSummary


@dataclass(frozen=True)
class RollforwardComponentSpec:
    """Specification of an expected rollforward component."""
    name: str
    display_label: str
    order: int
    is_movement: bool  # True for movements, False for beginning/ending balance markers
    description: str = ""


@dataclass(frozen=True)
class RollforwardTemplateSpec:
    """Template defining the ordered components for a specific methodology and basis."""
    methodology: str
    basis: str
    components: tuple[RollforwardComponentSpec, ...]
    default_tolerance: float = 0.01  # Cent-level tolerance default


# Configurable templates per methodology
LDTI_LFPB_TEMPLATE: Final = RollforwardTemplateSpec(
    methodology="LDTI",
    basis="GAAP",
    components=(
        RollforwardComponentSpec("beginning_balance", "Beginning Balance", 1, False, "Opening LFPB liability"),
        RollforwardComponentSpec("new_business", "New Business", 2, True, "Inception reserve for newly issued contracts"),
        RollforwardComponentSpec("expected_change", "Expected Change", 3, True, "Net premium accretion and interest accrual at locked rate"),
        RollforwardComponentSpec("experience_variance", "Experience Variance", 4, True, "Actual vs expected benefit payments and charges"),
        RollforwardComponentSpec("assumption_unlock", "Assumption Unlock", 5, True, "Impact of annual assumption update on future cash flows"),
        RollforwardComponentSpec("discount_rate_movement", "Discount Rate Movement (OCI)", 6, True, "Remeasurement at current A-grade yield recognized in OCI"),
        RollforwardComponentSpec("ending_balance", "Ending Balance", 7, False, "Closing LFPB liability"),
    ),
)

DAC_TEMPLATE: Final = RollforwardTemplateSpec(
    methodology="DAC",
    basis="GAAP",
    components=(
        RollforwardComponentSpec("beginning_balance", "Beginning Balance", 1, False, "Opening unamortized DAC asset"),
        RollforwardComponentSpec("capitalized_costs", "Capitalized Acquisition Costs", 2, True, "New policy issue capitalized costs"),
        RollforwardComponentSpec("amortization_expense", "Amortization Expense", 3, True, "Constant-level run-off over expected term"),
        RollforwardComponentSpec("terminations_adjustments", "Terminations & Adjustments", 4, True, "Derecognition on contract extinguishment"),
        RollforwardComponentSpec("ending_balance", "Ending Balance", 5, False, "Closing unamortized DAC asset"),
    ),
)

MRB_TEMPLATE: Final = RollforwardTemplateSpec(
    methodology="MRB",
    basis="GAAP",
    components=(
        RollforwardComponentSpec("beginning_fair_value", "Beginning Fair Value", 1, False, "Opening MRB fair value liability"),
        RollforwardComponentSpec("attributed_fees", "Attributed Fees", 2, True, "Guarantee fees credited to MRB component"),
        RollforwardComponentSpec("benefit_payments", "Benefit Payments", 3, True, "Actual living/death guarantee claims paid"),
        RollforwardComponentSpec("change_in_fair_value_ni", "Fair Value Change (Net Income)", 4, True, "Market movements and risk-neutral path revaluation"),
        RollforwardComponentSpec("own_credit_risk_oci", "Own Credit Risk (OCI)", 5, True, "Instrument-specific credit risk remeasurement in OCI"),
        RollforwardComponentSpec("ending_fair_value", "Ending Fair Value", 6, False, "Closing MRB fair value liability"),
    ),
)

VM21_ATTRIBUTION_TEMPLATE: Final = RollforwardTemplateSpec(
    methodology="VM21",
    basis="STATUTORY",
    components=(
        RollforwardComponentSpec("beginning_reserve", "Beginning Reserve", 1, False, "Opening VM-21 principle-based reserve"),
        RollforwardComponentSpec("new_issues", "New Issues", 2, True, "Reserves on new variable/RILA contracts"),
        RollforwardComponentSpec("inforce_cash_flows", "Inforce Net Cash Flows", 3, True, "Expected mortality, surrender, and fee cash flows"),
        RollforwardComponentSpec("market_movement_impact", "Economic & Market Movement", 4, True, "Change in scenario paths and equity tail risk"),
        RollforwardComponentSpec("hedging_cdhs_impact", "Hedging & CDHS", 5, True, "Clearly Defined Hedging Strategy net P&L offset"),
        RollforwardComponentSpec("assumption_changes", "Prudent Assumption Updates", 6, True, "Revisions to prudent best-estimate assumptions"),
        RollforwardComponentSpec("tail_metric_adjustments", "Tail & Standard Projection Adjustment", 7, True, "Additional standard projection amount / tail adjustments"),
        RollforwardComponentSpec("ending_reserve", "Ending Reserve", 8, False, "Closing VM-21 principle-based reserve"),
    ),
)

VM22_ATTRIBUTION_TEMPLATE: Final = RollforwardTemplateSpec(
    methodology="VM22",
    basis="STATUTORY",
    components=(
        RollforwardComponentSpec("beginning_reserve", "Beginning Reserve", 1, False, "Opening VM-22 statutory reserve"),
        RollforwardComponentSpec("new_business", "New Business", 2, True, "Initial statutory reserve on newly issued fixed contracts"),
        RollforwardComponentSpec("contractual_cash_flows", "Contractual Cash Flows", 3, True, "Interest credited, annuity payments, surrender benefits"),
        RollforwardComponentSpec("economic_revaluation", "Prescribed Scenario Revaluation", 4, True, "Deterministic / stochastic scenario interest rate updates"),
        RollforwardComponentSpec("csv_floor_adjustment", "Cash Surrender Value Floor Adjustment", 5, True, "Clamp delta to enforce contractual aggregate CSV floor"),
        RollforwardComponentSpec("ending_reserve", "Ending Reserve", 6, False, "Closing VM-22 statutory reserve"),
    ),
)

ASC815_BIFURCATION_TEMPLATE: Final = RollforwardTemplateSpec(
    methodology="ASC815",
    basis="GAAP",
    components=(
        RollforwardComponentSpec("total_hybrid_liability", "Total Hybrid Contract Liability", 1, False, "Gross contract carrying value"),
        RollforwardComponentSpec("host_debt_carrying_value", "Host Debt Contract Value", 2, True, "Accreting host debt component"),
        RollforwardComponentSpec("embedded_derivative_fair_value", "Embedded Derivative Fair Value", 3, True, "Mark-to-market bifurcated option feature"),
        RollforwardComponentSpec("bifurcation_reconciliation_total", "Reconciled Total", 4, False, "Sum of Host + Embedded Derivative"),
    ),
)

METHODOLOGY_TEMPLATES: Final[dict[str, RollforwardTemplateSpec]] = {
    "LDTI": LDTI_LFPB_TEMPLATE,
    "DAC": DAC_TEMPLATE,
    "MRB": MRB_TEMPLATE,
    "VM21": VM21_ATTRIBUTION_TEMPLATE,
    "VM22": VM22_ATTRIBUTION_TEMPLATE,
    "ASC815": ASC815_BIFURCATION_TEMPLATE,
}


@dataclass
class RollforwardContinuityResult:
    """Result of mathematical rollforward continuity validation."""
    is_continuous: bool
    opening_balance: float
    sum_of_movements: float
    expected_ending_balance: float
    actual_ending_balance: float
    difference: float
    tolerance: float
    components_evaluated: list[dict[str, Any]] = field(default_factory=list)
    error_message: str | None = None


def get_methodology_template(methodology: str) -> RollforwardTemplateSpec:
    """Retrieve the standard rollforward template for a given methodology."""
    clean_m = validate_methodology(methodology)
    if clean_m not in METHODOLOGY_TEMPLATES:
        raise ValueError(f"No rollforward template registered for methodology '{methodology}'.")
    return METHODOLOGY_TEMPLATES[clean_m]


def validate_continuity(
    opening_balance: float,
    movements: dict[str, float] | list[tuple[str, float]],
    ending_balance: float,
    tolerance: float = 0.01,
) -> RollforwardContinuityResult:
    """Validate the core mathematical continuity invariant:
    
        Opening Balance + Sum(Movements) = Ending Balance (± tolerance)
    """
    if isinstance(movements, dict):
        movement_items = list(movements.items())
    else:
        movement_items = movements

    total_movements = sum(amount for _, amount in movement_items)
    calculated_ending = opening_balance + total_movements
    diff = calculated_ending - ending_balance
    abs_diff = abs(diff)

    is_continuous = abs_diff <= (tolerance + 1e-9)

    evaluated = []
    evaluated.append({"component": "opening_balance", "amount": opening_balance, "is_movement": False})
    for name, amt in movement_items:
        evaluated.append({"component": name, "amount": amt, "is_movement": True})
    evaluated.append({"component": "ending_balance", "amount": ending_balance, "is_movement": False})

    err_msg = None
    if not is_continuous:
        err_msg = (
            f"Rollforward continuity breach: Opening ({opening_balance:,.2f}) + "
            f"Movements ({total_movements:,.2f}) = Calculated Ending ({calculated_ending:,.2f}), "
            f"which differs from Actual Ending ({ending_balance:,.2f}) by {abs_diff:,.4f} "
            f"(tolerance: {tolerance})."
        )

    return RollforwardContinuityResult(
        is_continuous=is_continuous,
        opening_balance=opening_balance,
        sum_of_movements=total_movements,
        expected_ending_balance=calculated_ending,
        actual_ending_balance=ending_balance,
        difference=diff,
        tolerance=tolerance,
        components_evaluated=evaluated,
        error_message=err_msg,
    )


def record_rollforward_summary(
    db: Session,
    run_id: str,
    project_id: str,
    cohort_id: str,
    methodology: str,
    basis: str,
    continuity_result: RollforwardContinuityResult,
    currency: str = "USD",
    trace_id: str | None = None,
) -> list[RollforwardSummary]:
    """Persist verified rollforward attribution components to rollforward_summaries table."""
    valid_b = validate_basis(basis)
    valid_m = validate_methodology(methodology)
    trace_identifier = trace_id or str(uuid.uuid4())[:32]

    records = []
    order = 1
    for item in continuity_result.components_evaluated:
        comp_name = item["component"]
        amt = float(item["amount"])
        is_mov = item["is_movement"]

        record = RollforwardSummary(
            id=str(uuid.uuid4()),
            run_id=run_id,
            project_id=project_id,
            cohort_id=cohort_id,
            methodology=valid_m,
            basis=valid_b,
            currency=currency,
            component_name=comp_name,
            component_order=order,
            is_movement=is_mov,
            opening_balance=continuity_result.opening_balance,
            movement_amount=amt if is_mov else 0.0,
            closing_balance=continuity_result.actual_ending_balance,
            is_continuity_verified=continuity_result.is_continuous,
            continuity_difference=round(continuity_result.difference, 4),
            trace_identifier=trace_identifier,
            created_at=datetime.now(timezone.utc),
        )
        records.append(record)
        order += 1

    db.add_all(records)
    db.commit()
    return records
