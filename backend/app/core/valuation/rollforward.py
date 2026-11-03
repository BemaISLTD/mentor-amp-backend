"""Generic Rollforward & Attribution Framework (Work Package M5C).

Per CAS §7.2, Milestone 5 Architecture Invariants, ACT-DEC-08, and Senior Review Directives 6, 10, 11:
- The authoritative launch requirement is a configurable rollforward/attribution
  framework supporting applicable methodology-defined attribution components.
- Mathematical Continuity Invariant:
    Beginning Balance + Sum(Attributed Movements) = Ending Balance (± configured tolerance)
- Governed numerical policy: Strict Decimal arithmetic across all balances and movements.
- Directive 6: Movement templates are non-production reference examples / test fixtures,
  NOT owner-approved accounting truth (actual frameworks remain gated).
- Directive 10: Parent Run identity validation (project, basis, methodology) with 404 on cross-tenant.
- Directive 11: Configurable tolerance; no permanent $0.01 universal assumption.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Final
import uuid

from sqlalchemy.orm import Session

from app.core.valuation.taxonomy import (
    SUPPORTED_LAUNCH_METHODOLOGIES,
    validate_basis,
    validate_basis_methodology_pair,
    validate_methodology,
)
from app.db.models.rollforward import RollforwardSummary
from app.services.common import bad_request, not_found


def to_decimal(val: Any) -> Decimal:
    """Coerce any scalar numeric or string representation into a deterministic Decimal."""
    if isinstance(val, Decimal):
        return val
    return Decimal(str(val))


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
    """Template defining the ordered components for a methodology (Reference / Test Fixture)."""
    methodology: str
    basis: str
    components: tuple[RollforwardComponentSpec, ...]
    default_tolerance: Decimal = Decimal("0.01")


# =============================================================================
# REFERENCE / TEST FIXTURE TEMPLATES (Directive 6: NOT production truth)
# =============================================================================

REFERENCE_LDTI_LFPB_TEMPLATE: Final = RollforwardTemplateSpec(
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
    default_tolerance=Decimal("0.01"),
)

REFERENCE_DAC_TEMPLATE: Final = RollforwardTemplateSpec(
    methodology="DAC",
    basis="GAAP",
    components=(
        RollforwardComponentSpec("beginning_balance", "Beginning Balance", 1, False, "Opening unamortized DAC asset"),
        RollforwardComponentSpec("capitalized_costs", "Capitalized Acquisition Costs", 2, True, "New policy issue capitalized costs"),
        RollforwardComponentSpec("amortization_expense", "Amortization Expense", 3, True, "Constant-level run-off over expected term"),
        RollforwardComponentSpec("terminations_adjustments", "Terminations & Adjustments", 4, True, "Derecognition on contract extinguishment"),
        RollforwardComponentSpec("ending_balance", "Ending Balance", 5, False, "Closing unamortized DAC asset"),
    ),
    default_tolerance=Decimal("0.01"),
)

REFERENCE_MRB_TEMPLATE: Final = RollforwardTemplateSpec(
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
    default_tolerance=Decimal("0.01"),
)

REFERENCE_VM21_TEMPLATE: Final = RollforwardTemplateSpec(
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
    default_tolerance=Decimal("0.01"),
)

REFERENCE_VM22_TEMPLATE: Final = RollforwardTemplateSpec(
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
    default_tolerance=Decimal("0.01"),
)

REFERENCE_ASC815_TEMPLATE: Final = RollforwardTemplateSpec(
    methodology="ASC815",
    basis="GAAP",
    components=(
        RollforwardComponentSpec("total_hybrid_liability", "Total Hybrid Contract Liability", 1, False, "Gross contract carrying value"),
        RollforwardComponentSpec("host_debt_carrying_value", "Host Debt Contract Value", 2, True, "Accreting host debt component"),
        RollforwardComponentSpec("embedded_derivative_fair_value", "Embedded Derivative Fair Value", 3, True, "Mark-to-market bifurcated option feature"),
        RollforwardComponentSpec("bifurcation_reconciliation_total", "Reconciled Total", 4, False, "Sum of Host + Embedded Derivative"),
    ),
    default_tolerance=Decimal("0.01"),
)

REFERENCE_TEMPLATES: Final[dict[str, RollforwardTemplateSpec]] = {
    "LDTI": REFERENCE_LDTI_LFPB_TEMPLATE,
    "DAC": REFERENCE_DAC_TEMPLATE,
    "MRB": REFERENCE_MRB_TEMPLATE,
    "VM21": REFERENCE_VM21_TEMPLATE,
    "VM22": REFERENCE_VM22_TEMPLATE,
    "ASC815": REFERENCE_ASC815_TEMPLATE,
}

# Backwards compatibility alias
METHODOLOGY_TEMPLATES = REFERENCE_TEMPLATES
# Aliases for backwards compatibility in existing tests
DAC_TEMPLATE = REFERENCE_DAC_TEMPLATE
LDTI_LFPB_TEMPLATE = REFERENCE_LDTI_LFPB_TEMPLATE
MRB_TEMPLATE = REFERENCE_MRB_TEMPLATE
VM21_ATTRIBUTION_TEMPLATE = REFERENCE_VM21_TEMPLATE
VM22_ATTRIBUTION_TEMPLATE = REFERENCE_VM22_TEMPLATE
ASC815_BIFURCATION_TEMPLATE = REFERENCE_ASC815_TEMPLATE



@dataclass
class RollforwardContinuityResult:
    """Result of mathematical rollforward continuity validation with Decimal precision."""
    is_continuous: bool
    opening_balance: Decimal
    sum_of_movements: Decimal
    expected_ending_balance: Decimal
    actual_ending_balance: Decimal
    difference: Decimal
    tolerance: Decimal
    components_evaluated: list[dict[str, Any]] = field(default_factory=list)
    error_message: str | None = None


def get_methodology_template(methodology: str) -> RollforwardTemplateSpec:
    """Retrieve reference rollforward template (Directive 6: reference fixture)."""
    clean_m = validate_methodology(methodology)
    if clean_m not in REFERENCE_TEMPLATES:
        raise ValueError(f"No reference rollforward template registered for methodology '{methodology}'.")
    return REFERENCE_TEMPLATES[clean_m]


def validate_continuity(
    opening_balance: Decimal | float | int | str,
    movements: dict[str, Any] | list[tuple[str, Any]],
    ending_balance: Decimal | float | int | str,
    tolerance: Decimal | float | int | str = Decimal("0.01"),
) -> RollforwardContinuityResult:
    """Validate rollforward continuity using deterministic Decimal arithmetic (Directive 11).

    Beginning + Σ Movements = Ending (± tolerance)
    """
    dec_open = to_decimal(opening_balance)
    dec_end = to_decimal(ending_balance)
    dec_tol = to_decimal(tolerance)

    if isinstance(movements, dict):
        movement_items = [(k, to_decimal(v)) for k, v in movements.items()]
    else:
        movement_items = [(k, to_decimal(v)) for k, v in movements]

    total_movements = sum((amt for _, amt in movement_items), Decimal("0"))
    calculated_ending = dec_open + total_movements
    diff = calculated_ending - dec_end
    abs_diff = abs(diff)

    is_continuous = abs_diff <= dec_tol

    evaluated = []
    evaluated.append({"component": "opening_balance", "amount": dec_open, "is_movement": False})
    for name, amt in movement_items:
        evaluated.append({"component": name, "amount": amt, "is_movement": True})
    evaluated.append({"component": "ending_balance", "amount": dec_end, "is_movement": False})

    err_msg = None
    if not is_continuous:
        err_msg = (
            f"Rollforward continuity breach: Opening ({dec_open:,.2f}) + "
            f"Movements ({total_movements:,.2f}) = Calculated Ending ({calculated_ending:,.2f}), "
            f"which differs from Actual Ending ({dec_end:,.2f}) by {abs_diff} "
            f"(tolerance: {dec_tol})."
        )

    return RollforwardContinuityResult(
        is_continuous=is_continuous,
        opening_balance=dec_open,
        sum_of_movements=total_movements,
        expected_ending_balance=calculated_ending,
        actual_ending_balance=dec_end,
        difference=diff,
        tolerance=dec_tol,
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
    """Persist verified rollforward attribution components to rollforward_summaries table.

    Enforces Directive 10: Validates against canonical parent Run:
    - Parent Run exists and belongs to project_id (404 on cross-project IDOR).
    - Rollforward basis and methodology strictly match parent Run identity.
    """
    from app.db.models.run import Run

    # Validate parent Run exists in project_id (Directive 10)
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise not_found(f"Run '{run_id}' not found.")
    if run.project_id != project_id:
        raise not_found(f"Run '{run_id}' not found in project '{project_id}'.")

    valid_b = validate_basis(basis)
    valid_m = validate_methodology(methodology)

    # Invariants against canonical parent Run
    if run.basis and run.basis != valid_b:
        raise bad_request(
            f"Rollforward basis '{valid_b}' contradicts parent run basis '{run.basis}'."
        )
    if run.methodology and run.methodology != valid_m:
        raise bad_request(
            f"Rollforward methodology '{valid_m}' contradicts parent run methodology '{run.methodology}'."
        )
    validate_basis_methodology_pair(valid_b, valid_m)

    trace_identifier = trace_id or str(uuid.uuid4())[:32]
    records = []
    order = 1

    for item in continuity_result.components_evaluated:
        comp_name = item["component"]
        amt = item["amount"]
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
            movement_amount=amt if is_mov else Decimal("0.0"),
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
