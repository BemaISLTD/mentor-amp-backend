"""Native SPIA reference model definition.

ILLUSTRATIVE - NOT ACTUARIALLY APPROVED OR REGULATORY COMPLIANT.

The cash-flow mechanics in this module are MentorAmp-owned implementations reconciled against
the pinned external benchmark declared in :mod:`app.products.spia_reference.metadata`. LifeLib
is never imported or executed by MentorAmp. Discounting and reserves are a separate MentorAmp
valuation layer and are deliberately excluded from the external reconciliation boundary.

The current increment supports single and joint lives, all five core payout forms, payment
schedules, fixed annual COLA, refund claims, maintenance expenses, optional certain-payment
commutation, and a component cash-flow statement without changing the historical
``spia-illustrative-m1`` model.
"""

from app.core.formula_engine.formulas import register_function
from app.products.spia_reference.metadata import LIFELIB_SPIA_REFERENCE

PRODUCT_CODE = "SPIA"
PRODUCT_NAME = "Single Premium Immediate Annuity"
FUNCTION_PREFIX = "spia_reference"
IMPLEMENTATION_VERSION = "spia-reference-v1"

MORTALITY_TABLE_NAME = "SPIA_REFERENCE_MORTALITY_STANDARDIZED_2012"
IMPROVEMENT_TABLE_NAME = "SPIA_REFERENCE_IMPROVEMENT_STANDARDIZED"
SURRENDER_CHARGE_TABLE_NAME = "SPIA_REFERENCE_SURRENDER_CHARGE"
MORTALITY_TABLE_YEAR = 2012
MORTALITY_AE_FACTOR = 1.084
DEFAULT_DISCOUNT_RATE = 0.045
DEFAULT_EXPENSE_MAINTENANCE_ANNUAL = 60.0
DEFAULT_EXPENSE_INFLATION_RATE = 0.025
DEFAULT_COMMUTATION_UTILIZATION_RATE = 0.0
DEFAULT_COMMUTATION_DISCOUNT_RATE = 0.04
DEFAULT_COMMUTATION_MINIMUM_AMOUNT = 5000.0
DEFAULT_COMMUTATION_MINIMUM_PAYMENT = 100.0


def annual_mortality_rate(
    base_mortality_rate: float,
    mortality_improvement_rate: float,
    policy_month: float,
    annuity_year: float,
    mortality_table_year: float,
    mortality_ae_factor: float,
    rating_factor: float,
) -> float:
    """Apply generational improvement, A/E and rating factors in that order."""
    duration_years = (int(policy_month) - 1) // 12
    calendar_year = int(annuity_year) + duration_years
    improvement_years = calendar_year - int(mortality_table_year)
    projected_base = base_mortality_rate * (
        1.0 - mortality_improvement_rate
    ) ** improvement_years
    best_estimate = min(1.0, mortality_ae_factor * projected_base)
    return min(1.0, rating_factor * best_estimate)


def monthly_mortality_rate(annual_mortality_rate: float) -> float:
    """Convert annual mortality to monthly mortality using constant force."""
    return 1.0 - (1.0 - annual_mortality_rate) ** (1.0 / 12.0)


def survival_probability(
    survival_prev: float, monthly_mortality_rate: float
) -> float:
    """Probability of survival to the end of the projection month."""
    return survival_prev * (1.0 - monthly_mortality_rate)


def joint_attained_age(joint_issue_age: float, policy_month: float) -> float:
    """Joint annuitant attained age during the projection policy year."""
    return joint_issue_age + (int(policy_month) - 1) // 12


def joint_annual_mortality_rate(
    joint_attained_age: float,
    joint_base_mortality_rate: float,
    joint_mortality_improvement_rate: float,
    policy_month: float,
    annuity_year: float,
    mortality_table_year: float,
    mortality_ae_factor: float,
    joint_rating_factor: float,
) -> float:
    """Joint-annuitant generational mortality using the same governed basis."""
    if joint_attained_age < 0:
        raise ValueError("joint_attained_age cannot be negative")
    return annual_mortality_rate(
        joint_base_mortality_rate,
        joint_mortality_improvement_rate,
        policy_month,
        annuity_year,
        mortality_table_year,
        mortality_ae_factor,
        joint_rating_factor,
    )


def joint_monthly_mortality_rate(joint_annual_mortality_rate: float) -> float:
    """Convert the joint annuitant's annual mortality to monthly mortality."""
    return monthly_mortality_rate(joint_annual_mortality_rate)


def joint_survival_probability(
    is_joint: float,
    joint_survival_prev: float,
    joint_monthly_mortality_rate: float,
) -> float:
    """Joint annuitant survival probability at month end."""
    if int(is_joint) not in (0, 1) or int(is_joint) != is_joint:
        raise ValueError("is_joint must be 0 or 1")
    if not int(is_joint):
        return 0.0
    return joint_survival_prev * (1.0 - joint_monthly_mortality_rate)


def last_survivor_probability(
    is_joint: float,
    survival_probability: float,
    joint_survival_probability: float,
) -> float:
    """Probability at least one covered life survives, assuming independence."""
    if int(is_joint) not in (0, 1) or int(is_joint) != is_joint:
        raise ValueError("is_joint must be 0 or 1")
    if not int(is_joint):
        return survival_probability
    return (
        survival_probability
        + joint_survival_probability
        - survival_probability * joint_survival_probability
    )


def payment_factor_life(
    benefit_form: str,
    is_joint: float,
    survivor_percentage: float,
    reduction_trigger: str,
    payment_timing: str,
    survival_prev: float,
    survival_probability: float,
    joint_survival_prev: float,
    joint_survival_probability: float,
) -> float:
    """Life-contingent factor at the advance or arrears payment point."""
    form = str(benefit_form).strip().lower()
    if form not in (
        "life_only",
        "life_certain",
        "cash_refund",
        "installment_refund",
        "certain_only",
    ):
        raise ValueError("unsupported benefit_form")
    if form == "certain_only":
        return 0.0
    if not 0.0 <= survivor_percentage <= 1.0:
        raise ValueError("survivor_percentage must be between 0 and 1")
    timing = str(payment_timing).strip().lower()
    if timing not in ("advance", "arrears"):
        raise ValueError("payment_timing must be 'advance' or 'arrears'")
    primary = survival_prev if timing == "advance" else survival_probability
    if int(is_joint) not in (0, 1) or int(is_joint) != is_joint:
        raise ValueError("is_joint must be 0 or 1")
    if not int(is_joint):
        return primary
    if form == "cash_refund" and survivor_percentage != 1.0:
        raise ValueError("joint cash_refund requires survivor_percentage of 1")
    joint = joint_survival_prev if timing == "advance" else joint_survival_probability
    trigger = str(reduction_trigger).strip().lower()
    if trigger == "either":
        both_alive = primary * joint
        exactly_one_alive = primary + joint - 2.0 * both_alive
        return both_alive + survivor_percentage * exactly_one_alive
    if trigger == "primary":
        return primary + survivor_percentage * (1.0 - primary) * joint
    raise ValueError("reduction_trigger must be 'either' or 'primary'")


def annual_income_current(
    annual_income: float,
    cola_rate: float,
    policy_month: float,
) -> float:
    """Unreduced annualized income after anniversary COLA increases."""
    policy_duration = (int(policy_month) - 1) // 12
    return annual_income * (1.0 + cola_rate) ** policy_duration


def scheduled_installment_untrimmed(
    annual_income_current: float,
    payment_frequency: float,
    payment_timing: str,
    policy_month: float,
) -> float:
    """Deterministic contractual installment before refund trim and survival weighting."""
    frequency = int(payment_frequency)
    if frequency not in (1, 2, 4, 12) or frequency != payment_frequency:
        raise ValueError("payment_frequency must be one of 1, 2, 4 or 12")
    timing = str(payment_timing).strip().lower()
    if timing not in ("advance", "arrears"):
        raise ValueError("payment_timing must be 'advance' or 'arrears'")
    month = int(policy_month)
    step = 12 // frequency
    is_payment_month = month % step == 0 if timing == "arrears" else (month - 1) % step == 0
    if not is_payment_month:
        return 0.0
    return annual_income_current / frequency


def scheduled_installment(
    benefit_form: str,
    premium: float,
    scheduled_installment_untrimmed: float,
    cumulative_scheduled_before_current: float,
) -> float:
    """Trim only the final installment-refund payment to land exactly on premium."""
    if str(benefit_form).strip().lower() != "installment_refund":
        return scheduled_installment_untrimmed
    if premium <= 0.0:
        raise ValueError("installment_refund requires positive premium")
    if cumulative_scheduled_before_current >= premium:
        return scheduled_installment_untrimmed
    remaining = max(0.0, premium - cumulative_scheduled_before_current)
    return min(scheduled_installment_untrimmed, remaining)


def cumulative_scheduled_before_current(
    cumulative_scheduled_prev: float,
    cumulative_scheduled_paid_at_valuation: float,
    projection_month: float,
) -> float:
    """Scheduled installments paid before this month, including opening in-force state."""
    if cumulative_scheduled_paid_at_valuation < 0.0:
        raise ValueError("cumulative_scheduled_paid_at_valuation cannot be negative")
    if int(projection_month) == 1:
        return cumulative_scheduled_paid_at_valuation
    return cumulative_scheduled_prev


def cumulative_scheduled_payment(
    cumulative_scheduled_before_current: float,
    scheduled_installment: float,
) -> float:
    """Scheduled installments paid by the end of the current projection month."""
    return cumulative_scheduled_before_current + scheduled_installment


def certain_floor(
    benefit_form: str,
    certain_months: float,
    premium: float,
    cumulative_scheduled_before_current: float,
    policy_month: float,
) -> float:
    """Unreduced-payment guarantee floor for certain and installment-refund forms."""
    form = str(benefit_form).strip().lower()
    if form not in (
        "life_only",
        "life_certain",
        "cash_refund",
        "installment_refund",
        "certain_only",
    ):
        raise ValueError("unsupported benefit_form")
    if certain_months < 0 or int(certain_months) != certain_months:
        raise ValueError("certain_months must be a non-negative integer")
    if form in ("life_certain", "certain_only"):
        return 1.0 if policy_month <= certain_months else 0.0
    if form == "installment_refund":
        if premium <= 0.0:
            raise ValueError("installment_refund requires positive premium")
        return 1.0 if cumulative_scheduled_before_current < premium else 0.0
    return 0.0


def payment_factor(certain_floor: float, payment_factor_life: float) -> float:
    """Master payment factor: guarantee is a floor, never an additional stream."""
    return max(certain_floor, payment_factor_life)


def annuity_payment(
    scheduled_installment: float,
    payment_factor: float,
    certain_floor: float,
    commutation_fraction: float,
) -> float:
    """Expected annuity installment after guarantee and survival weighting."""
    if not 0.0 <= commutation_fraction <= 1.0:
        raise ValueError("commutation_fraction must be between 0 and 1")
    return (
        scheduled_installment
        * payment_factor
        * (1.0 - commutation_fraction * certain_floor)
    )


def last_death_probability(
    last_survivor_prev: float,
    last_survivor_probability: float,
) -> float:
    """Probability that the income-terminating death occurs in this month."""
    return last_survivor_prev - last_survivor_probability


def refund_claim(
    benefit_form: str,
    premium: float,
    payment_timing: str,
    cumulative_scheduled_before_current: float,
    cumulative_scheduled_payment: float,
    last_death_probability: float,
) -> float:
    """Expected cash-refund claim using death-month payment ordering."""
    if str(benefit_form).strip().lower() != "cash_refund":
        return 0.0
    if premium <= 0.0:
        raise ValueError("cash_refund requires positive premium")
    timing = str(payment_timing).strip().lower()
    if timing not in ("advance", "arrears"):
        raise ValueError("payment_timing must be 'advance' or 'arrears'")
    paid = (
        cumulative_scheduled_payment
        if timing == "advance"
        else cumulative_scheduled_before_current
    )
    return last_death_probability * max(0.0, premium - paid)


def discount_factor(
    discount_rate_annual: float,
    projection_month: float,
    payment_timing: str,
) -> float:
    """MentorAmp-only discount factor; not a LifeLib-reconciled output."""
    timing = str(payment_timing).strip().lower()
    if timing not in ("advance", "arrears"):
        raise ValueError("payment_timing must be 'advance' or 'arrears'")
    payment_month = projection_month - 1.0 if timing == "advance" else projection_month
    return (1.0 + discount_rate_annual) ** (-payment_month / 12.0)


def pv_annuity_payment(annuity_payment: float, discount_factor: float) -> float:
    """MentorAmp-only present value of the expected annuity payment."""
    return annuity_payment * discount_factor


def refund_discount_factor(
    discount_rate_annual: float,
    projection_month: float,
) -> float:
    """Refunds are paid at month end under both annuity-payment timing conventions."""
    return (1.0 + discount_rate_annual) ** (-projection_month / 12.0)


def pv_refund_claim(refund_claim: float, refund_discount_factor: float) -> float:
    return refund_claim * refund_discount_factor


def surrender_charge_lookup_year(policy_year: float) -> float:
    """Clamp the published surrender-charge schedule to contract years 2 through 10."""
    year = int(policy_year)
    return float(min(max(year, 2), 10))


def commutation_fraction(
    commutation_fraction_prev: float,
    commutation_amount_prev: float,
    commuted_value_prev: float,
    commutation_fraction_at_valuation: float,
    projection_month: float,
) -> float:
    """Cumulative certain-payment reduction before the current month's withdrawal."""
    if not 0.0 <= commutation_fraction_at_valuation <= 1.0:
        raise ValueError("commutation_fraction_at_valuation must be between 0 and 1")
    if int(projection_month) == 1:
        return commutation_fraction_at_valuation
    if commuted_value_prev <= 0.0 or commutation_amount_prev <= 0.0:
        return commutation_fraction_prev
    return commutation_fraction_prev + (
        (1.0 - commutation_fraction_prev)
        * commutation_amount_prev
        / commuted_value_prev
    )


def commutation_eligible(
    commutation_enabled: float,
    issue_state_excludes_withdrawal: float,
    benefit_form: str,
    certain_floor: float,
    policy_year: float,
    policy_month: float,
) -> float:
    """One when the contract may commute certain payments in the current month."""
    for name, value in (
        ("commutation_enabled", commutation_enabled),
        ("issue_state_excludes_withdrawal", issue_state_excludes_withdrawal),
    ):
        if int(value) not in (0, 1) or int(value) != value:
            raise ValueError(f"{name} must be 0 or 1")
    if not int(commutation_enabled) or int(issue_state_excludes_withdrawal):
        return 0.0
    if str(benefit_form).strip().lower() not in (
        "life_certain", "certain_only", "installment_refund"
    ):
        return 0.0
    year = int(policy_year)
    if year < 2 or certain_floor <= 0.0:
        return 0.0
    return 1.0 if int(policy_month) == 12 * (year - 1) + 1 else 0.0


def _future_guaranteed_installments(
    benefit_form: str,
    certain_months: float,
    premium: float,
    cumulative_scheduled_payment: float,
    annual_income: float,
    cola_rate: float,
    payment_frequency: float,
    payment_timing: str,
    policy_month: float,
) -> list[tuple[int, float]]:
    """Future scheduled installments inside the remaining guarantee."""
    form = str(benefit_form).strip().lower()
    current = int(policy_month)
    if form in ("life_certain", "certain_only"):
        final_month = int(certain_months)
    elif form == "installment_refund":
        if cumulative_scheduled_payment >= premium:
            return []
        final_month = current
        cumulative = cumulative_scheduled_payment
        while cumulative < premium:
            final_month += 1
            income = annual_income_current(annual_income, cola_rate, final_month)
            cumulative += scheduled_installment_untrimmed(
                income, payment_frequency, payment_timing, final_month
            )
            if final_month - current > 10000:
                raise ValueError("installment-refund guarantee does not recover premium")
    else:
        return []
    installments: list[tuple[int, float]] = []
    for month in range(current + 1, final_month + 1):
        income = annual_income_current(annual_income, cola_rate, month)
        amount = scheduled_installment_untrimmed(
            income, payment_frequency, payment_timing, month
        )
        if amount > 0.0:
            installments.append((month - current, amount))
    return installments


def commuted_value(
    benefit_form: str,
    certain_months: float,
    premium: float,
    cumulative_scheduled_payment: float,
    annual_income: float,
    cola_rate: float,
    payment_frequency: float,
    payment_timing: str,
    policy_month: float,
    commutation_fraction: float,
    commutation_discount_rate: float,
    commutation_discount_convention: str,
) -> float:
    """Present value of remaining guaranteed scheduled installments."""
    installments = _future_guaranteed_installments(
        benefit_form, certain_months, premium, cumulative_scheduled_payment,
        annual_income, cola_rate, payment_frequency, payment_timing, policy_month,
    )
    convention = str(commutation_discount_convention).strip().lower()
    total = 0.0
    for months_ahead, amount in installments:
        years = months_ahead / 12.0
        if convention == "compound":
            if commutation_discount_rate <= -1.0:
                raise ValueError("commutation_discount_rate must be greater than -1")
            discount = (1.0 + commutation_discount_rate) ** (-years)
        elif convention == "simple":
            discount = max(0.0, 1.0 - commutation_discount_rate * years)
        else:
            raise ValueError("invalid commutation_discount_convention")
        total += amount * discount
    return total * (1.0 - commutation_fraction)


def minimum_remaining_guaranteed_installment(
    benefit_form: str,
    certain_months: float,
    premium: float,
    cumulative_scheduled_payment: float,
    annual_income: float,
    cola_rate: float,
    payment_frequency: float,
    payment_timing: str,
    policy_month: float,
) -> float:
    installments = _future_guaranteed_installments(
        benefit_form, certain_months, premium, cumulative_scheduled_payment,
        annual_income, cola_rate, payment_frequency, payment_timing, policy_month,
    )
    return min((amount for _month, amount in installments), default=0.0)


def commutation_amount(
    commutation_eligible: float,
    commuted_value: float,
    commutation_utilization_rate: float,
    commutation_fraction: float,
    minimum_remaining_guaranteed_installment: float,
    commutation_minimum_amount: float,
    commutation_minimum_payment: float,
) -> float:
    """Gross withdrawal after utilization, residual-payment, and minimum-amount limits."""
    if not 0.0 <= commutation_utilization_rate <= 1.0:
        raise ValueError("commutation_utilization_rate must be between 0 and 1")
    if not 0.0 <= commutation_fraction <= 1.0:
        raise ValueError("commutation_fraction must be between 0 and 1")
    if commutation_minimum_amount < 0.0:
        raise ValueError("commutation_minimum_amount cannot be negative")
    if commutation_minimum_payment < 0.0:
        raise ValueError("commutation_minimum_payment cannot be negative")
    if commutation_eligible <= 0.0 or commuted_value <= 0.0:
        return 0.0
    if minimum_remaining_guaranteed_installment <= 0.0:
        return 0.0
    if commutation_fraction == 1.0:
        return 0.0
    maximum_fraction = 1.0 - (
        commutation_minimum_payment / minimum_remaining_guaranteed_installment
    )
    if maximum_fraction <= commutation_fraction:
        return 0.0
    maximum_withdrawal = (
        commuted_value
        * (maximum_fraction - commutation_fraction)
        / (1.0 - commutation_fraction)
    )
    withdrawal = min(
        commutation_utilization_rate * commuted_value,
        commuted_value,
        maximum_withdrawal,
    )
    return withdrawal if withdrawal >= commutation_minimum_amount else 0.0


def commutation_payment(
    commutation_amount: float,
    surrender_charge_lookup_year: float,
    surrender_charge_rate: float,
) -> float:
    """Net commutation amount paid to the owner; liability outgo positive."""
    if not 0.0 <= surrender_charge_rate <= 1.0:
        raise ValueError("surrender_charge_rate must be between 0 and 1")
    return commutation_amount * (1.0 - surrender_charge_rate)


def surrender_charge(
    commutation_amount: float,
    surrender_charge_lookup_year: float,
    surrender_charge_rate: float,
) -> float:
    """Amount retained by the insurer from a gross commutation withdrawal."""
    return commutation_amount * surrender_charge_rate


def expense_inflation_factor(
    expense_inflation_rate: float,
    policy_month: float,
) -> float:
    """Fixed compound expense inflation applied on policy anniversaries."""
    if expense_inflation_rate <= -1.0:
        raise ValueError("expense_inflation_rate must be greater than -1")
    policy_duration = (int(policy_month) - 1) // 12
    return (1.0 + expense_inflation_rate) ** policy_duration


def obligation_factor(
    benefit_form: str,
    certain_floor: float,
    last_survivor_probability: float,
) -> float:
    """Closing probability that any contractual payment obligation remains."""
    form = str(benefit_form).strip().lower()
    if form not in (
        "life_only",
        "life_certain",
        "cash_refund",
        "installment_refund",
        "certain_only",
    ):
        raise ValueError("unsupported benefit_form")
    if form == "certain_only":
        return certain_floor
    return max(certain_floor, last_survivor_probability)


def maintenance_expense(
    expense_maintenance_annual: float,
    expense_inflation_factor: float,
    obligation_factor: float,
) -> float:
    """Expected monthly maintenance expense while an obligation remains."""
    if expense_maintenance_annual < 0.0:
        raise ValueError("expense_maintenance_annual cannot be negative")
    return (
        expense_maintenance_annual
        / 12.0
        * expense_inflation_factor
        * obligation_factor
    )


def pv_maintenance_expense(
    maintenance_expense: float,
    refund_discount_factor: float,
) -> float:
    """Month-end present value of expected maintenance expense."""
    return maintenance_expense * refund_discount_factor


def pv_commutation_payment(
    commutation_payment: float,
    refund_discount_factor: float,
) -> float:
    """Month-end present value of net commutation outgo."""
    return commutation_payment * refund_discount_factor


def liability_cash_flow(
    annuity_payment: float,
    refund_claim: float,
    commutation_payment: float,
    maintenance_expense: float,
) -> float:
    """Current supported gross liability outgo components."""
    return annuity_payment + refund_claim + commutation_payment + maintenance_expense


def net_cash_flow(liability_cash_flow: float) -> float:
    """Income-positive insurer cash flow; SPIA projected income is zero."""
    return -liability_cash_flow


def cash_flow_identity_residual(
    liability_cash_flow: float,
    annuity_payment: float,
    refund_claim: float,
    commutation_payment: float,
    maintenance_expense: float,
) -> float:
    """Zero when the component statement reconciles to gross liability outgo."""
    return liability_cash_flow - (
        annuity_payment + refund_claim + commutation_payment + maintenance_expense
    )


def pv_liability_cash_flow(
    pv_annuity_payment: float,
    pv_refund_claim: float,
    pv_commutation_payment: float,
    pv_maintenance_expense: float,
) -> float:
    return (
        pv_annuity_payment
        + pv_refund_claim
        + pv_commutation_payment
        + pv_maintenance_expense
    )


def valuation_annuity_payment(
    annuity_payment: float,
    discount_rate_annual: float,
    payment_timing: str,
) -> float:
    """Translate start-of-month payments to the engine's month-end valuation grid."""
    timing = str(payment_timing).strip().lower()
    if timing not in ("advance", "arrears"):
        raise ValueError("payment_timing must be 'advance' or 'arrears'")
    if timing == "arrears":
        return annuity_payment
    return annuity_payment * (1.0 + discount_rate_annual) ** (1.0 / 12.0)


def valuation_liability_cash_flow(
    valuation_annuity_payment: float,
    refund_claim: float,
    commutation_payment: float,
    maintenance_expense: float,
) -> float:
    """Month-end-grid equivalent used by the prospective reserve engine."""
    return (
        valuation_annuity_payment
        + refund_claim
        + commutation_payment
        + maintenance_expense
    )


_FUNCTIONS = (
    (
        "annual_mortality_rate",
        annual_mortality_rate,
        "q = min(1, rating_factor * min(1, AE * q_base * (1 - G2)^k))",
        "Generational mortality: project the standardized base rate, then apply the "
        "illustrative A/E and contract rating factors.",
    ),
    (
        "monthly_mortality_rate",
        monthly_mortality_rate,
        "q_month = 1 - (1 - q_annual)^(1/12)",
        "Constant-force conversion of annual mortality to monthly mortality.",
    ),
    (
        "survival_probability",
        survival_probability,
        "survival = survival_prev * (1 - q_month)",
        "End-of-month single-life survival probability.",
    ),
    (
        "joint_attained_age",
        joint_attained_age,
        "joint attained age = joint issue age + completed policy years",
        "Joint annuitant age nearest birthday in the projection policy year.",
    ),
    (
        "joint_annual_mortality_rate",
        joint_annual_mortality_rate,
        "joint q = min(1, joint rating * min(1, AE * q_base * (1 - G2)^k))",
        "Joint-annuitant generational mortality on the governed standardized basis.",
    ),
    (
        "joint_monthly_mortality_rate",
        joint_monthly_mortality_rate,
        "joint q_month = 1 - (1 - joint q_annual)^(1/12)",
        "Constant-force conversion for the joint annuitant.",
    ),
    (
        "joint_survival_probability",
        joint_survival_probability,
        "joint survival = joint survival prev * (1 - joint q_month)",
        "End-of-month joint-annuitant survival probability.",
    ),
    (
        "last_survivor_probability",
        last_survivor_probability,
        "last survivor = l1 + l2 - l1*l2 on joint contracts; l1 otherwise",
        "At-least-one-alive probability under the illustrative independence assumption.",
    ),
    (
        "payment_factor_life",
        payment_factor_life,
        "either: l1*l2 + delta*(l1+l2-2*l1*l2); primary: l1 + delta*(1-l1)*l2",
        "Life-contingent payment factor at the configured advance or arrears point.",
    ),
    (
        "annual_income_current",
        annual_income_current,
        "B(y) = B(1) * (1 + COLA)^(policy_year - 1)",
        "Unreduced annualized income after fixed compound anniversary increases.",
    ),
    (
        "scheduled_installment_untrimmed",
        scheduled_installment_untrimmed,
        "scheduled installment = annual income / frequency on payment months, else zero",
        "Contractual installment before survival weighting or installment-refund trim.",
    ),
    (
        "scheduled_installment",
        scheduled_installment,
        "installment-refund final payment = min(schedule, premium - cumulative paid)",
        "Deterministic installment after the exact-premium-recovery trim.",
    ),
    (
        "cumulative_scheduled_before_current",
        cumulative_scheduled_before_current,
        "opening cumulative at projection month 1; prior cumulative output thereafter",
        "Scheduled installments paid before the current month, including opening in-force state.",
    ),
    (
        "cumulative_scheduled_payment",
        cumulative_scheduled_payment,
        "cumulative schedule = prior cumulative schedule + current installment",
        "Installments paid by the end of the current month, without survival weighting.",
    ),
    (
        "certain_floor",
        certain_floor,
        "certain floor = 1 while the elected or refund-derived guarantee remains",
        "Unreduced-payment guarantee floor; never added to the life factor.",
    ),
    (
        "payment_factor",
        payment_factor,
        "payment factor = max(certain floor, life-contingent factor)",
        "Master payment factor preventing guarantee double counting.",
    ),
    (
        "annuity_payment",
        annuity_payment,
        "expected payment = scheduled installment * payment factor * (1 - commuted fraction * certain floor)",
        "Expected annuity outgo after the optional commutation reduction to the certain slice.",
    ),
    (
        "surrender_charge_lookup_year",
        surrender_charge_lookup_year,
        "lookup year = min(10, max(2, policy year))",
        "Lookup key for the illustrative contract-year surrender-charge schedule.",
    ),
    (
        "commutation_fraction",
        commutation_fraction,
        "opening fraction at projection month 1; otherwise prior fraction plus the prior withdrawal share",
        "Cumulative reduction to future guaranteed installments, including opening in-force state.",
    ),
    (
        "commutation_eligible",
        commutation_eligible,
        "eligible on enabled, non-excluded contract anniversaries inside a supported guarantee",
        "Strict optional eligibility gate for the illustrative certain-payment commutation feature.",
    ),
    (
        "commuted_value",
        commuted_value,
        "commuted value = PV of remaining guaranteed installments * (1 - commuted fraction)",
        "Illustrative value of the remaining certain-payment stream.",
    ),
    (
        "minimum_remaining_guaranteed_installment",
        minimum_remaining_guaranteed_installment,
        "minimum installment = min(remaining guaranteed scheduled installments)",
        "Minimum remaining installment used to preserve the configured payment floor.",
    ),
    (
        "commutation_amount",
        commutation_amount,
        "gross withdrawal = limited utilization * commuted value, subject to payment and amount floors",
        "Gross illustrative commutation withdrawal before surrender charge.",
    ),
    (
        "commutation_payment",
        commutation_payment,
        "commutation payment = gross withdrawal * (1 - surrender charge rate)",
        "Net owner payment included in liability outgo.",
    ),
    (
        "surrender_charge",
        surrender_charge,
        "surrender charge = gross withdrawal * surrender charge rate",
        "Charge retained by the insurer and disclosed separately from liability outgo.",
    ),
    (
        "pv_commutation_payment",
        pv_commutation_payment,
        "PV commutation payment = commutation payment * month-end discount factor",
        "MentorAmp illustrative valuation of net commutation outgo.",
    ),
    (
        "last_death_probability",
        last_death_probability,
        "last-death probability = prior last-survivor probability - current",
        "Probability of the death that terminates the income stream.",
    ),
    (
        "refund_claim",
        refund_claim,
        "cash refund = last-death probability * max(0, premium - scheduled payments paid)",
        "Expected cash-refund outgo with advance/arrears death-month ordering.",
    ),
    (
        "discount_factor",
        discount_factor,
        "discount factor = (1 + annual rate)^(-payment_time/12)",
        "MentorAmp illustrative valuation layer; outside the LifeLib comparison scope.",
    ),
    (
        "pv_annuity_payment",
        pv_annuity_payment,
        "PV annuity payment = annuity payment * discount factor",
        "MentorAmp illustrative valuation layer; outside the LifeLib comparison scope.",
    ),
    (
        "refund_discount_factor",
        refund_discount_factor,
        "refund discount factor = (1 + annual rate)^(-projection_month/12)",
        "Month-end discount factor for expected cash-refund claims.",
    ),
    (
        "pv_refund_claim",
        pv_refund_claim,
        "PV refund claim = refund claim * refund discount factor",
        "MentorAmp illustrative valuation of the expected cash-refund claim.",
    ),
    (
        "expense_inflation_factor",
        expense_inflation_factor,
        "expense inflation = (1 + inflation rate)^(policy year - 1)",
        "Fixed compound maintenance-expense inflation at policy anniversaries.",
    ),
    (
        "obligation_factor",
        obligation_factor,
        "obligation = certain floor for certain-only; max(floor, last survivor) otherwise",
        "Closing probability that any contractual payment obligation remains.",
    ),
    (
        "maintenance_expense",
        maintenance_expense,
        "expense = annual maintenance / 12 * inflation * obligation",
        "Expected maintenance expense while a payment obligation remains.",
    ),
    (
        "pv_maintenance_expense",
        pv_maintenance_expense,
        "PV expense = maintenance expense * month-end discount factor",
        "MentorAmp illustrative valuation of expected maintenance expense.",
    ),
    (
        "liability_cash_flow",
        liability_cash_flow,
        "liability cash flow = annuity payment + refund claim + commutation payment + maintenance expense",
        "Current supported gross liability outgo, positive for outgo.",
    ),
    (
        "net_cash_flow",
        net_cash_flow,
        "net cash flow = -liability cash flow",
        "Income-positive insurer sign convention; projected SPIA income is zero.",
    ),
    (
        "cash_flow_identity_residual",
        cash_flow_identity_residual,
        "residual = liability cash flow - sum(component outgo)",
        "Component-statement reconciliation residual, expected to be zero.",
    ),
    (
        "pv_liability_cash_flow",
        pv_liability_cash_flow,
        "PV liability cash flow = PV annuity + PV refund + PV commutation + PV maintenance expense",
        "Timing-correct present value of current liability components.",
    ),
    (
        "valuation_annuity_payment",
        valuation_annuity_payment,
        "valuation cash flow = advance payment * (1 + annual rate)^(1/12), else payment",
        "Timing bridge for the month-end reserve engine; not a published cash flow.",
    ),
    (
        "valuation_liability_cash_flow",
        valuation_liability_cash_flow,
        "valuation liability cash flow = timing-adjusted annuity + refund + commutation + expense",
        "Month-end-grid equivalent of the supported liability cash flow.",
    ),
)


def function_ref(name: str) -> str:
    return f"{FUNCTION_PREFIX}.{name}"


def register_functions() -> None:
    """Register the native SPIA reference formulas idempotently."""
    for name, function, expression, explanation in _FUNCTIONS:
        register_function(
            function_ref(name),
            function,
            expression_text=expression,
            explanation=explanation,
            illustrative=True,
            implementation_version=IMPLEMENTATION_VERSION,
        )


VARIABLES: list[dict] = [
    {
        "name": "gender",
        "display_name": "Primary annuitant gender",
        "kind": "input",
        "data_type": "string",
        "unit": None,
        "required": True,
        "source": {"type": "input", "column": "gender", "transform": "upper"},
        "description": "Primary annuitant gender used for mortality lookup.",
    },
    {
        "name": "annual_income",
        "display_name": "Annualized annuity income",
        "kind": "input",
        "data_type": "number",
        "unit": "USD/year",
        "required": True,
        "source": {"type": "input", "column": "annual_income"},
        "description": "Unreduced annualized contractual income; not derived from premium.",
    },
    {
        "name": "premium",
        "display_name": "Single premium",
        "kind": "input",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "default_value": 0.0,
        "source": {"type": "input", "column": "premium"},
        "description": "Premium used only by cash- and installment-refund benefits.",
    },
    {
        "name": "cumulative_scheduled_paid_at_valuation",
        "display_name": "Scheduled installments paid before valuation",
        "kind": "input",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "default_value": 0.0,
        "source": {
            "type": "input",
            "column": "cumulative_scheduled_paid_at_valuation",
        },
        "description": "Opening cumulative contractual installments for an existing in-force policy.",
    },
    {
        "name": "commutation_fraction_at_valuation",
        "display_name": "Commuted fraction at valuation",
        "kind": "input",
        "data_type": "number",
        "unit": "rate",
        "required": False,
        "default_value": 0.0,
        "source": {"type": "input", "column": "commutation_fraction_at_valuation"},
        "description": "Opening reduction to remaining certain payments from prior commutations.",
    },
    {
        "name": "benefit_form",
        "display_name": "Benefit form",
        "kind": "input",
        "data_type": "string",
        "unit": None,
        "required": False,
        "default_value": "life_only",
        "source": {"type": "input", "column": "benefit_form"},
        "description": "Life only, life certain, cash refund, installment refund or certain only.",
    },
    {
        "name": "certain_months",
        "display_name": "Elected certain period",
        "kind": "input",
        "data_type": "number",
        "unit": "months",
        "required": False,
        "default_value": 0,
        "source": {"type": "input", "column": "certain_months"},
        "description": "Guarantee-period month count for life-certain and certain-only forms.",
    },
    {
        "name": "commutation_enabled",
        "display_name": "Commutation enabled indicator",
        "kind": "input",
        "data_type": "number",
        "unit": "indicator",
        "required": False,
        "default_value": 0,
        "source": {"type": "input", "column": "commutation_enabled"},
        "description": "Strict opt-in for illustrative certain-payment commutation; disabled by default.",
    },
    {
        "name": "issue_state_excludes_withdrawal",
        "display_name": "Issue-state commutation exclusion",
        "kind": "input",
        "data_type": "number",
        "unit": "indicator",
        "required": False,
        "default_value": 1,
        "source": {"type": "input", "column": "issue_state_excludes_withdrawal"},
        "description": "One when jurisdiction rules exclude commutation; omission fails closed.",
    },
    {
        "name": "annuity_year",
        "display_name": "Annuity commencement year",
        "kind": "input",
        "data_type": "number",
        "unit": "year",
        "required": True,
        "source": {"type": "input", "column": "annuity_year"},
        "description": "Calendar year containing the first policy projection year.",
    },
    {
        "name": "payment_frequency",
        "display_name": "Payments per year",
        "kind": "input",
        "data_type": "number",
        "unit": "payments/year",
        "required": False,
        "default_value": 12,
        "source": {"type": "input", "column": "payment_frequency"},
        "description": "Supported values are 1, 2, 4 and 12 payments per year.",
    },
    {
        "name": "payment_timing",
        "display_name": "Payment timing",
        "kind": "input",
        "data_type": "string",
        "unit": None,
        "required": False,
        "default_value": "arrears",
        "source": {"type": "input", "column": "payment_timing"},
        "description": "Whether scheduled installments are paid in advance or arrears.",
    },
    {
        "name": "cola_rate",
        "display_name": "Annual COLA rate",
        "kind": "input",
        "data_type": "number",
        "unit": "rate",
        "required": False,
        "default_value": 0.0,
        "source": {"type": "input", "column": "cola_rate"},
        "description": "Fixed compound increase applied on policy anniversaries.",
    },
    {
        "name": "is_joint",
        "display_name": "Joint-life indicator",
        "kind": "input",
        "data_type": "number",
        "unit": "indicator",
        "required": False,
        "default_value": 0,
        "source": {"type": "input", "column": "is_joint"},
        "description": "1 for a joint-life contract and 0 for a single-life contract.",
    },
    {
        "name": "joint_issue_age",
        "display_name": "Joint annuitant issue age",
        "kind": "input",
        "data_type": "number",
        "unit": "years",
        "required": False,
        "default_value": 65,
        "source": {"type": "input", "column": "joint_issue_age"},
        "description": "Age nearest birthday at annuity commencement; ignored for single life.",
    },
    {
        "name": "joint_gender",
        "display_name": "Joint annuitant gender",
        "kind": "input",
        "data_type": "string",
        "unit": None,
        "required": False,
        "default_value": "M",
        "source": {"type": "input", "column": "joint_gender", "transform": "upper"},
        "description": "Joint annuitant gender; ignored for single life.",
    },
    {
        "name": "survivor_percentage",
        "display_name": "Survivor payment percentage",
        "kind": "input",
        "data_type": "number",
        "unit": "probability",
        "required": False,
        "default_value": 1.0,
        "source": {"type": "input", "column": "survivor_percentage"},
        "description": "Share of unreduced income payable after the triggering death.",
    },
    {
        "name": "reduction_trigger",
        "display_name": "Survivor reduction trigger",
        "kind": "input",
        "data_type": "string",
        "unit": None,
        "required": False,
        "default_value": "either",
        "source": {"type": "input", "column": "reduction_trigger"},
        "description": "Reduce after either first death or only after primary death.",
    },
    {
        "name": "rating_factor",
        "display_name": "Mortality rating factor",
        "kind": "input",
        "data_type": "number",
        "unit": "factor",
        "required": False,
        "default_value": 1.0,
        "source": {"type": "input", "column": "rating_factor"},
        "description": "Contract-specific mortality multiplier; 1.0 for the anchor.",
    },
    {
        "name": "joint_rating_factor",
        "display_name": "Joint mortality rating factor",
        "kind": "input",
        "data_type": "number",
        "unit": "factor",
        "required": False,
        "default_value": 1.0,
        "source": {"type": "input", "column": "joint_rating_factor"},
        "description": "Joint-annuitant mortality multiplier; ignored for single life.",
    },
    {
        "name": "attained_age",
        "display_name": "Attained age",
        "kind": "context",
        "data_type": "number",
        "unit": "years",
        "required": True,
        "source": {"type": "context", "field": "attained_age"},
        "description": "Age nearest birthday during the policy projection year.",
    },
    {
        "name": "projection_month",
        "display_name": "Projection month",
        "kind": "context",
        "data_type": "number",
        "unit": "month",
        "required": True,
        "source": {"type": "context", "field": "projection_month"},
        "description": "One-based MentorAmp projection month.",
    },
    {
        "name": "policy_month",
        "display_name": "Policy month",
        "kind": "context",
        "data_type": "number",
        "unit": "month",
        "required": True,
        "source": {"type": "context", "field": "policy_month"},
        "description": "One-based contract duration month, including time in force before valuation.",
    },
    {
        "name": "policy_year",
        "display_name": "Policy year",
        "kind": "context",
        "data_type": "number",
        "unit": "year",
        "required": True,
        "source": {"type": "context", "field": "policy_year"},
        "description": "One-based policy year derived from projection month.",
    },
    {
        "name": "base_mortality_rate",
        "display_name": "Standardized base annual mortality",
        "kind": "assumption",
        "data_type": "number",
        "unit": "probability",
        "required": True,
        "source": {
            "type": "assumption",
            "table": MORTALITY_TABLE_NAME,
            "value_column": "mortality_rate",
            "key_map": {"age": "attained_age", "gender": "gender"},
        },
        "description": "Illustrative standardized base rate; not a licensed IAM table.",
    },
    {
        "name": "mortality_improvement_rate",
        "display_name": "Standardized mortality improvement",
        "kind": "assumption",
        "data_type": "number",
        "unit": "rate",
        "required": True,
        "source": {
            "type": "assumption",
            "table": IMPROVEMENT_TABLE_NAME,
            "value_column": "improvement_rate",
            "key_map": {"age": "attained_age", "gender": "gender"},
        },
        "description": "Illustrative standardized improvement scale; not published G2.",
    },
    {
        "name": "mortality_table_year",
        "display_name": "Mortality table base year",
        "kind": "manual",
        "data_type": "number",
        "unit": "year",
        "required": True,
        "default_value": MORTALITY_TABLE_YEAR,
        "source": {"type": "manual", "value": MORTALITY_TABLE_YEAR},
        "description": "Base year used by the standardized generational projection.",
    },
    {
        "name": "mortality_ae_factor",
        "display_name": "Mortality A/E factor",
        "kind": "manual",
        "data_type": "number",
        "unit": "factor",
        "required": True,
        "default_value": MORTALITY_AE_FACTOR,
        "source": {"type": "manual", "value": MORTALITY_AE_FACTOR},
        "description": "Illustrative upstream standardization, not a company assumption.",
    },
    {
        "name": "annual_mortality_rate",
        "display_name": "Projected annual mortality",
        "kind": "formula",
        "data_type": "number",
        "unit": "probability",
        "required": False,
        "source": {"type": "formula"},
        "description": "Annual mortality after improvement, A/E and rating factors.",
    },
    {
        "name": "monthly_mortality_rate",
        "display_name": "Monthly mortality",
        "kind": "formula",
        "data_type": "number",
        "unit": "probability",
        "required": False,
        "source": {"type": "formula"},
        "description": "Constant-force monthly mortality probability.",
    },
    {
        "name": "survival_prev",
        "display_name": "Prior month survival",
        "kind": "prior_output",
        "data_type": "number",
        "unit": "probability",
        "required": True,
        "source": {
            "type": "prior_output",
            "variable": "survival_probability",
            "offset": 1,
            "initial_value": 1.0,
        },
        "description": "Survival at the preceding month end; 1.0 at commencement.",
    },
    {
        "name": "survival_probability",
        "display_name": "Primary survival probability",
        "kind": "formula",
        "data_type": "number",
        "unit": "probability",
        "required": False,
        "source": {"type": "formula", "non_increasing": True},
        "description": "Probability the primary annuitant survives to month end.",
    },
    {
        "name": "joint_attained_age",
        "display_name": "Joint annuitant attained age",
        "kind": "formula",
        "data_type": "number",
        "unit": "years",
        "required": False,
        "source": {"type": "formula"},
        "description": "Joint annuitant age nearest birthday in the policy year.",
    },
    {
        "name": "joint_base_mortality_rate",
        "display_name": "Joint standardized base annual mortality",
        "kind": "assumption",
        "data_type": "number",
        "unit": "probability",
        "required": True,
        "source": {
            "type": "assumption",
            "table": MORTALITY_TABLE_NAME,
            "value_column": "mortality_rate",
            "key_map": {"age": "joint_attained_age", "gender": "joint_gender"},
        },
        "description": "Joint-annuitant standardized base rate.",
    },
    {
        "name": "joint_mortality_improvement_rate",
        "display_name": "Joint standardized mortality improvement",
        "kind": "assumption",
        "data_type": "number",
        "unit": "rate",
        "required": True,
        "source": {
            "type": "assumption",
            "table": IMPROVEMENT_TABLE_NAME,
            "value_column": "improvement_rate",
            "key_map": {"age": "joint_attained_age", "gender": "joint_gender"},
        },
        "description": "Joint-annuitant standardized improvement scale.",
    },
    {
        "name": "joint_annual_mortality_rate",
        "display_name": "Joint projected annual mortality",
        "kind": "formula",
        "data_type": "number",
        "unit": "probability",
        "required": False,
        "source": {"type": "formula"},
        "description": "Joint annual mortality after improvement, A/E and rating.",
    },
    {
        "name": "joint_monthly_mortality_rate",
        "display_name": "Joint monthly mortality",
        "kind": "formula",
        "data_type": "number",
        "unit": "probability",
        "required": False,
        "source": {"type": "formula"},
        "description": "Joint constant-force monthly mortality probability.",
    },
    {
        "name": "joint_survival_prev",
        "display_name": "Prior month joint survival",
        "kind": "prior_output",
        "data_type": "number",
        "unit": "probability",
        "required": True,
        "source": {
            "type": "prior_output",
            "variable": "joint_survival_probability",
            "offset": 1,
            "initial_value": 1.0,
        },
        "description": "Joint survival at the preceding month end.",
    },
    {
        "name": "joint_survival_probability",
        "display_name": "Joint annuitant survival probability",
        "kind": "formula",
        "data_type": "number",
        "unit": "probability",
        "required": False,
        "source": {"type": "formula", "non_increasing": True},
        "description": "Probability the joint annuitant survives to month end.",
    },
    {
        "name": "last_survivor_probability",
        "display_name": "Last-survivor probability",
        "kind": "formula",
        "data_type": "number",
        "unit": "probability",
        "required": False,
        "source": {"type": "formula", "non_increasing": True},
        "description": "Probability at least one covered life survives to month end.",
    },
    {
        "name": "payment_factor_life",
        "display_name": "Life-contingent payment factor",
        "kind": "formula",
        "data_type": "number",
        "unit": "factor",
        "required": False,
        "source": {"type": "formula"},
        "description": "Survival and survivor-reduction factor at the payment point.",
    },
    {
        "name": "annual_income_current",
        "display_name": "Current annualized annuity income",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD/year",
        "required": False,
        "source": {"type": "formula"},
        "description": "Annualized income after anniversary COLA increases.",
    },
    {
        "name": "scheduled_installment_untrimmed",
        "display_name": "Untrimmed scheduled installment",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula"},
        "description": "Contractual installment on payment months before refund trim.",
    },
    {
        "name": "cumulative_scheduled_prev",
        "display_name": "Prior cumulative scheduled installments",
        "kind": "prior_output",
        "data_type": "number",
        "unit": "USD",
        "required": True,
        "source": {
            "type": "prior_output",
            "variable": "cumulative_scheduled_payment",
            "offset": 1,
            "initial_value": 0.0,
        },
        "description": "Deterministic scheduled installments paid before this month.",
    },
    {
        "name": "cumulative_scheduled_before_current",
        "display_name": "Cumulative installments before current month",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula", "non_decreasing": True},
        "description": "Opening cumulative paid amount in month 1, then prior projected cumulative.",
    },
    {
        "name": "scheduled_installment",
        "display_name": "Scheduled installment",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula"},
        "description": "Installment after exact-premium-recovery trim where applicable.",
    },
    {
        "name": "cumulative_scheduled_payment",
        "display_name": "Cumulative scheduled installments",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula", "non_decreasing": True},
        "description": "Deterministic scheduled installments paid through this month.",
    },
    {
        "name": "certain_floor",
        "display_name": "Certain-period payment floor",
        "kind": "formula",
        "data_type": "number",
        "unit": "factor",
        "required": False,
        "source": {"type": "formula"},
        "description": "One while the unreduced-payment guarantee remains, otherwise zero.",
    },
    {
        "name": "payment_factor",
        "display_name": "Master payment factor",
        "kind": "formula",
        "data_type": "number",
        "unit": "factor",
        "required": False,
        "source": {"type": "formula"},
        "description": "Maximum of the certain floor and life-contingent factor.",
    },
    {
        "name": "commutation_utilization_rate",
        "display_name": "Commutation utilization rate",
        "kind": "manual",
        "data_type": "number",
        "unit": "rate",
        "required": True,
        "default_value": DEFAULT_COMMUTATION_UTILIZATION_RATE,
        "source": {
            "type": "manual",
            "value": DEFAULT_COMMUTATION_UTILIZATION_RATE,
            "classification": "upstream_standardization",
        },
        "allow_scenario_override": True,
        "description": "Illustrative and unverified utilization assumption; zero by default.",
    },
    {
        "name": "commutation_discount_rate",
        "display_name": "Commutation discount rate",
        "kind": "manual",
        "data_type": "number",
        "unit": "rate",
        "required": True,
        "default_value": DEFAULT_COMMUTATION_DISCOUNT_RATE,
        "source": {
            "type": "manual",
            "value": DEFAULT_COMMUTATION_DISCOUNT_RATE,
            "classification": "upstream_standardization",
        },
        "allow_scenario_override": True,
        "description": "Illustrative and unverified discount rate for remaining certain payments.",
    },
    {
        "name": "commutation_discount_convention",
        "display_name": "Commutation discount convention",
        "kind": "manual",
        "data_type": "string",
        "unit": None,
        "required": True,
        "default_value": "compound",
        "source": {
            "type": "manual",
            "value": "compound",
            "classification": "upstream_standardization",
        },
        "description": "Illustrative compound or simple discount convention.",
    },
    {
        "name": "commutation_minimum_amount",
        "display_name": "Minimum commutation amount",
        "kind": "manual",
        "data_type": "number",
        "unit": "USD",
        "required": True,
        "default_value": DEFAULT_COMMUTATION_MINIMUM_AMOUNT,
        "source": {
            "type": "manual",
            "value": DEFAULT_COMMUTATION_MINIMUM_AMOUNT,
            "classification": "upstream_standardization",
        },
        "allow_scenario_override": True,
        "description": "Illustrative minimum gross withdrawal; not an approved product rule.",
    },
    {
        "name": "commutation_minimum_payment",
        "display_name": "Minimum remaining guaranteed installment",
        "kind": "manual",
        "data_type": "number",
        "unit": "USD",
        "required": True,
        "default_value": DEFAULT_COMMUTATION_MINIMUM_PAYMENT,
        "source": {
            "type": "manual",
            "value": DEFAULT_COMMUTATION_MINIMUM_PAYMENT,
            "classification": "upstream_standardization",
        },
        "allow_scenario_override": True,
        "description": "Illustrative floor preserved after commutation; not an approved product rule.",
    },
    {
        "name": "surrender_charge_lookup_year",
        "display_name": "Surrender-charge lookup year",
        "kind": "formula",
        "data_type": "number",
        "unit": "year",
        "required": False,
        "source": {"type": "formula"},
        "description": "Contract year clamped to the illustrative schedule range.",
    },
    {
        "name": "surrender_charge_rate",
        "display_name": "Surrender-charge rate",
        "kind": "factor",
        "data_type": "number",
        "unit": "rate",
        "required": True,
        "source": {
            "type": "factor",
            "table": SURRENDER_CHARGE_TABLE_NAME,
            "value_column": "surrender_charge_rate",
            "key_map": {"policy_year": "surrender_charge_lookup_year"},
        },
        "description": "Illustrative contract-year charge schedule imported through Data Manager.",
    },
    {
        "name": "commutation_fraction_prev",
        "display_name": "Prior commuted fraction",
        "kind": "prior_output",
        "data_type": "number",
        "unit": "rate",
        "required": True,
        "source": {
            "type": "prior_output",
            "variable": "commutation_fraction",
            "offset": 1,
            "initial_value": 0.0,
        },
        "description": "Cumulative guaranteed-payment reduction before the current month.",
    },
    {
        "name": "commutation_amount_prev",
        "display_name": "Prior gross commutation amount",
        "kind": "prior_output",
        "data_type": "number",
        "unit": "USD",
        "required": True,
        "source": {
            "type": "prior_output",
            "variable": "commutation_amount",
            "offset": 1,
            "initial_value": 0.0,
        },
        "description": "Gross commutation withdrawal in the preceding projection month.",
    },
    {
        "name": "commuted_value_prev",
        "display_name": "Prior commuted value",
        "kind": "prior_output",
        "data_type": "number",
        "unit": "USD",
        "required": True,
        "source": {
            "type": "prior_output",
            "variable": "commuted_value",
            "offset": 1,
            "initial_value": 0.0,
        },
        "description": "Value of remaining certain payments in the preceding month.",
    },
    {
        "name": "commutation_fraction",
        "display_name": "Cumulative commuted fraction",
        "kind": "formula",
        "data_type": "number",
        "unit": "rate",
        "required": False,
        "source": {"type": "formula", "non_decreasing": True},
        "description": "Cumulative reduction applied only to the certain-payment slice.",
    },
    {
        "name": "commutation_eligible",
        "display_name": "Commutation eligibility indicator",
        "kind": "formula",
        "data_type": "number",
        "unit": "indicator",
        "required": False,
        "source": {"type": "formula"},
        "description": "One only at an eligible contract anniversary under the strict opt-in gate.",
    },
    {
        "name": "commuted_value",
        "display_name": "Value of commutable certain payments",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula"},
        "description": "Illustrative value of remaining guaranteed scheduled installments.",
    },
    {
        "name": "minimum_remaining_guaranteed_installment",
        "display_name": "Minimum future guaranteed installment",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula"},
        "description": "Minimum remaining installment before the current withdrawal.",
    },
    {
        "name": "commutation_amount",
        "display_name": "Gross commutation amount",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula"},
        "description": "Gross illustrative withdrawal before surrender charge.",
    },
    {
        "name": "commutation_payment",
        "display_name": "Net commutation payment",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula"},
        "description": "Net owner payment after the illustrative surrender charge.",
    },
    {
        "name": "surrender_charge",
        "display_name": "Surrender charge retained",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula"},
        "description": "Charge retained by the insurer and excluded from liability outgo.",
    },
    {
        "name": "annuity_payment",
        "display_name": "Expected annuity payment",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula"},
        "description": "Expected scheduled annuity outgo after guarantee and survival weighting.",
    },
    {
        "name": "last_survivor_prev",
        "display_name": "Prior last-survivor probability",
        "kind": "prior_output",
        "data_type": "number",
        "unit": "probability",
        "required": True,
        "source": {
            "type": "prior_output",
            "variable": "last_survivor_probability",
            "offset": 1,
            "initial_value": 1.0,
        },
        "description": "Last-survivor probability at the preceding month end.",
    },
    {
        "name": "last_death_probability",
        "display_name": "Last-death probability",
        "kind": "formula",
        "data_type": "number",
        "unit": "probability",
        "required": False,
        "source": {"type": "formula"},
        "description": "Probability the income-terminating death occurs during this month.",
    },
    {
        "name": "refund_claim",
        "display_name": "Expected cash-refund claim",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula"},
        "description": "Expected remaining-premium cash refund paid on terminal death.",
    },
    {
        "name": "expense_maintenance_annual",
        "display_name": "Annual maintenance expense",
        "kind": "manual",
        "data_type": "number",
        "unit": "USD/year",
        "required": True,
        "default_value": DEFAULT_EXPENSE_MAINTENANCE_ANNUAL,
        "source": {
            "type": "manual",
            "value": DEFAULT_EXPENSE_MAINTENANCE_ANNUAL,
            "classification": "upstream_standardization",
        },
        "allow_scenario_override": True,
        "description": "Illustrative maintenance cost; not an insurer-published assumption.",
    },
    {
        "name": "expense_inflation_rate",
        "display_name": "Annual expense inflation",
        "kind": "manual",
        "data_type": "number",
        "unit": "rate",
        "required": True,
        "default_value": DEFAULT_EXPENSE_INFLATION_RATE,
        "source": {
            "type": "manual",
            "value": DEFAULT_EXPENSE_INFLATION_RATE,
            "classification": "upstream_standardization",
        },
        "allow_scenario_override": True,
        "description": "Illustrative fixed expense inflation; not an insurer assumption.",
    },
    {
        "name": "expense_inflation_factor",
        "display_name": "Expense inflation factor",
        "kind": "formula",
        "data_type": "number",
        "unit": "factor",
        "required": False,
        "source": {"type": "formula"},
        "description": "Compound expense inflation applied at policy anniversaries.",
    },
    {
        "name": "obligation_factor",
        "display_name": "Payment obligation factor",
        "kind": "formula",
        "data_type": "number",
        "unit": "factor",
        "required": False,
        "source": {"type": "formula"},
        "description": "Closing probability that a contractual payment obligation remains.",
    },
    {
        "name": "maintenance_expense",
        "display_name": "Expected maintenance expense",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula"},
        "description": "Expected monthly maintenance expense while an obligation remains.",
    },
    {
        "name": "discount_rate_annual",
        "display_name": "Annual discount rate",
        "kind": "manual",
        "data_type": "number",
        "unit": "rate",
        "required": True,
        "default_value": DEFAULT_DISCOUNT_RATE,
        "source": {"type": "manual", "value": DEFAULT_DISCOUNT_RATE},
        "allow_scenario_override": True,
        "description": "MentorAmp-only illustrative valuation assumption.",
    },
    {
        "name": "discount_factor",
        "display_name": "Discount factor",
        "kind": "formula",
        "data_type": "number",
        "unit": "factor",
        "required": False,
        "source": {"type": "formula"},
        "description": "MentorAmp-only discount factor to the valuation date.",
    },
    {
        "name": "pv_annuity_payment",
        "display_name": "PV of expected annuity payment",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula"},
        "description": "MentorAmp-only discounted expected payment.",
    },
    {
        "name": "refund_discount_factor",
        "display_name": "Refund discount factor",
        "kind": "formula",
        "data_type": "number",
        "unit": "factor",
        "required": False,
        "source": {"type": "formula"},
        "description": "MentorAmp-only month-end discount factor for refund claims.",
    },
    {
        "name": "pv_refund_claim",
        "display_name": "PV of expected refund claim",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula"},
        "description": "MentorAmp-only discounted expected cash-refund claim.",
    },
    {
        "name": "pv_commutation_payment",
        "display_name": "PV of net commutation payment",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula"},
        "description": "MentorAmp-only discounted illustrative commutation outgo.",
    },
    {
        "name": "pv_maintenance_expense",
        "display_name": "PV of expected maintenance expense",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula"},
        "description": "MentorAmp-only discounted expected maintenance expense.",
    },
    {
        "name": "liability_cash_flow",
        "display_name": "Gross liability cash flow",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula"},
        "description": "Annuity payment plus cash-refund claim; outgo positive.",
    },
    {
        "name": "pv_liability_cash_flow",
        "display_name": "PV of gross liability cash flow",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula"},
        "description": "Timing-correct present value of supported liability components.",
    },
    {
        "name": "net_cash_flow",
        "display_name": "Net cash flow",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula"},
        "description": "Income-positive insurer cash flow; negative when liability outgo occurs.",
    },
    {
        "name": "cash_flow_identity_residual",
        "display_name": "Cash-flow identity residual",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula"},
        "description": "Gross liability less the sum of its supported components; expected zero.",
    },
    {
        "name": "valuation_annuity_payment",
        "display_name": "Timing-adjusted valuation cash flow",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula"},
        "description": "Internal month-end-grid equivalent used only by valuation.",
    },
    {
        "name": "valuation_liability_cash_flow",
        "display_name": "Timing-adjusted valuation liability cash flow",
        "kind": "formula",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {"type": "formula"},
        "description": "Month-end-grid equivalent used by the prospective reserve engine.",
    },
    {
        "name": "reserve",
        "display_name": "Illustrative prospective reserve",
        "kind": "output",
        "data_type": "number",
        "unit": "USD",
        "required": False,
        "source": {
            "type": "valuation",
            "method": "illustrative_prospective_reserve",
            "cash_flow": "valuation_liability_cash_flow",
            "rate": "discount_rate_annual",
            "consistency_sum_of": "pv_liability_cash_flow",
        },
        "description": "MentorAmp-only valuation output; not externally reconciled.",
    },
]

FORMULAS: list[dict] = [
    {
        "name": "Projected annual mortality",
        "output_variable": "annual_mortality_rate",
        "dependencies": [
            "base_mortality_rate",
            "mortality_improvement_rate",
            "policy_month",
            "annuity_year",
            "mortality_table_year",
            "mortality_ae_factor",
            "rating_factor",
        ],
        "unit": "probability",
    },
    {
        "name": "Monthly mortality",
        "output_variable": "monthly_mortality_rate",
        "dependencies": ["annual_mortality_rate"],
        "unit": "probability",
    },
    {
        "name": "Survival probability",
        "output_variable": "survival_probability",
        "dependencies": ["survival_prev", "monthly_mortality_rate"],
        "unit": "probability",
    },
    {
        "name": "Joint attained age",
        "output_variable": "joint_attained_age",
        "dependencies": ["joint_issue_age", "policy_month"],
        "unit": "years",
    },
    {
        "name": "Joint projected annual mortality",
        "output_variable": "joint_annual_mortality_rate",
        "dependencies": [
            "joint_attained_age",
            "joint_base_mortality_rate",
            "joint_mortality_improvement_rate",
            "policy_month",
            "annuity_year",
            "mortality_table_year",
            "mortality_ae_factor",
            "joint_rating_factor",
        ],
        "unit": "probability",
    },
    {
        "name": "Joint monthly mortality",
        "output_variable": "joint_monthly_mortality_rate",
        "dependencies": ["joint_annual_mortality_rate"],
        "unit": "probability",
    },
    {
        "name": "Joint survival probability",
        "output_variable": "joint_survival_probability",
        "dependencies": [
            "is_joint",
            "joint_survival_prev",
            "joint_monthly_mortality_rate",
        ],
        "unit": "probability",
    },
    {
        "name": "Last-survivor probability",
        "output_variable": "last_survivor_probability",
        "dependencies": [
            "is_joint",
            "survival_probability",
            "joint_survival_probability",
        ],
        "unit": "probability",
    },
    {
        "name": "Life-contingent payment factor",
        "output_variable": "payment_factor_life",
        "dependencies": [
            "benefit_form",
            "is_joint",
            "survivor_percentage",
            "reduction_trigger",
            "payment_timing",
            "survival_prev",
            "survival_probability",
            "joint_survival_prev",
            "joint_survival_probability",
        ],
        "unit": "factor",
    },
    {
        "name": "Annualized income",
        "output_variable": "annual_income_current",
        "dependencies": ["annual_income", "cola_rate", "policy_month"],
        "unit": "USD/year",
    },
    {
        "name": "Untrimmed scheduled installment",
        "output_variable": "scheduled_installment_untrimmed",
        "dependencies": [
            "annual_income_current",
            "payment_frequency",
            "payment_timing",
            "policy_month",
        ],
        "unit": "USD",
    },
    {
        "name": "Cumulative installments before current month",
        "output_variable": "cumulative_scheduled_before_current",
        "dependencies": [
            "cumulative_scheduled_prev",
            "cumulative_scheduled_paid_at_valuation",
            "projection_month",
        ],
        "unit": "USD",
    },
    {
        "name": "Scheduled installment",
        "output_variable": "scheduled_installment",
        "dependencies": [
            "benefit_form",
            "premium",
            "scheduled_installment_untrimmed",
            "cumulative_scheduled_before_current",
        ],
        "unit": "USD",
    },
    {
        "name": "Cumulative scheduled installments",
        "output_variable": "cumulative_scheduled_payment",
        "dependencies": [
            "cumulative_scheduled_before_current",
            "scheduled_installment",
        ],
        "unit": "USD",
    },
    {
        "name": "Certain-period payment floor",
        "output_variable": "certain_floor",
        "dependencies": [
            "benefit_form",
            "certain_months",
            "premium",
            "cumulative_scheduled_before_current",
            "policy_month",
        ],
        "unit": "factor",
    },
    {
        "name": "Master payment factor",
        "output_variable": "payment_factor",
        "dependencies": ["certain_floor", "payment_factor_life"],
        "unit": "factor",
    },
    {
        "name": "Surrender-charge lookup year",
        "output_variable": "surrender_charge_lookup_year",
        "dependencies": ["policy_year"],
        "unit": "year",
    },
    {
        "name": "Cumulative commuted fraction",
        "output_variable": "commutation_fraction",
        "dependencies": [
            "commutation_fraction_prev",
            "commutation_amount_prev",
            "commuted_value_prev",
            "commutation_fraction_at_valuation",
            "projection_month",
        ],
        "unit": "rate",
    },
    {
        "name": "Commutation eligibility",
        "output_variable": "commutation_eligible",
        "dependencies": [
            "commutation_enabled",
            "issue_state_excludes_withdrawal",
            "benefit_form",
            "certain_floor",
            "policy_year",
            "policy_month",
        ],
        "unit": "indicator",
    },
    {
        "name": "Value of commutable certain payments",
        "output_variable": "commuted_value",
        "dependencies": [
            "benefit_form",
            "certain_months",
            "premium",
            "cumulative_scheduled_payment",
            "annual_income",
            "cola_rate",
            "payment_frequency",
            "payment_timing",
            "policy_month",
            "commutation_fraction",
            "commutation_discount_rate",
            "commutation_discount_convention",
        ],
        "unit": "USD",
    },
    {
        "name": "Minimum future guaranteed installment",
        "output_variable": "minimum_remaining_guaranteed_installment",
        "dependencies": [
            "benefit_form",
            "certain_months",
            "premium",
            "cumulative_scheduled_payment",
            "annual_income",
            "cola_rate",
            "payment_frequency",
            "payment_timing",
            "policy_month",
        ],
        "unit": "USD",
    },
    {
        "name": "Gross commutation amount",
        "output_variable": "commutation_amount",
        "dependencies": [
            "commutation_eligible",
            "commuted_value",
            "commutation_utilization_rate",
            "commutation_fraction",
            "minimum_remaining_guaranteed_installment",
            "commutation_minimum_amount",
            "commutation_minimum_payment",
        ],
        "unit": "USD",
    },
    {
        "name": "Net commutation payment",
        "output_variable": "commutation_payment",
        "dependencies": [
            "commutation_amount",
            "surrender_charge_lookup_year",
            "surrender_charge_rate",
        ],
        "unit": "USD",
    },
    {
        "name": "Surrender charge retained",
        "output_variable": "surrender_charge",
        "dependencies": [
            "commutation_amount",
            "surrender_charge_lookup_year",
            "surrender_charge_rate",
        ],
        "unit": "USD",
    },
    {
        "name": "Expected annuity payment",
        "output_variable": "annuity_payment",
        "dependencies": [
            "scheduled_installment",
            "payment_factor",
            "certain_floor",
            "commutation_fraction",
        ],
        "unit": "USD",
    },
    {
        "name": "Last-death probability",
        "output_variable": "last_death_probability",
        "dependencies": ["last_survivor_prev", "last_survivor_probability"],
        "unit": "probability",
    },
    {
        "name": "Expected cash-refund claim",
        "output_variable": "refund_claim",
        "dependencies": [
            "benefit_form",
            "premium",
            "payment_timing",
            "cumulative_scheduled_before_current",
            "cumulative_scheduled_payment",
            "last_death_probability",
        ],
        "unit": "USD",
    },
    {
        "name": "Discount factor",
        "output_variable": "discount_factor",
        "dependencies": ["discount_rate_annual", "projection_month", "payment_timing"],
        "unit": "factor",
    },
    {
        "name": "PV of expected annuity payment",
        "output_variable": "pv_annuity_payment",
        "dependencies": ["annuity_payment", "discount_factor"],
        "unit": "USD",
    },
    {
        "name": "Refund discount factor",
        "output_variable": "refund_discount_factor",
        "dependencies": ["discount_rate_annual", "projection_month"],
        "unit": "factor",
    },
    {
        "name": "PV of expected refund claim",
        "output_variable": "pv_refund_claim",
        "dependencies": ["refund_claim", "refund_discount_factor"],
        "unit": "USD",
    },
    {
        "name": "PV of net commutation payment",
        "output_variable": "pv_commutation_payment",
        "dependencies": ["commutation_payment", "refund_discount_factor"],
        "unit": "USD",
    },
    {
        "name": "Expense inflation factor",
        "output_variable": "expense_inflation_factor",
        "dependencies": ["expense_inflation_rate", "policy_month"],
        "unit": "factor",
    },
    {
        "name": "Payment obligation factor",
        "output_variable": "obligation_factor",
        "dependencies": [
            "benefit_form",
            "certain_floor",
            "last_survivor_probability",
        ],
        "unit": "factor",
    },
    {
        "name": "Expected maintenance expense",
        "output_variable": "maintenance_expense",
        "dependencies": [
            "expense_maintenance_annual",
            "expense_inflation_factor",
            "obligation_factor",
        ],
        "unit": "USD",
    },
    {
        "name": "PV of expected maintenance expense",
        "output_variable": "pv_maintenance_expense",
        "dependencies": ["maintenance_expense", "refund_discount_factor"],
        "unit": "USD",
    },
    {
        "name": "Gross liability cash flow",
        "output_variable": "liability_cash_flow",
        "dependencies": [
            "annuity_payment",
            "refund_claim",
            "commutation_payment",
            "maintenance_expense",
        ],
        "unit": "USD",
    },
    {
        "name": "Net cash flow",
        "output_variable": "net_cash_flow",
        "dependencies": ["liability_cash_flow"],
        "unit": "USD",
    },
    {
        "name": "Cash-flow identity residual",
        "output_variable": "cash_flow_identity_residual",
        "dependencies": [
            "liability_cash_flow",
            "annuity_payment",
            "refund_claim",
            "commutation_payment",
            "maintenance_expense",
        ],
        "unit": "USD",
    },
    {
        "name": "PV of gross liability cash flow",
        "output_variable": "pv_liability_cash_flow",
        "dependencies": [
            "pv_annuity_payment",
            "pv_refund_claim",
            "pv_commutation_payment",
            "pv_maintenance_expense",
        ],
        "unit": "USD",
    },
    {
        "name": "Timing-adjusted valuation cash flow",
        "output_variable": "valuation_annuity_payment",
        "dependencies": [
            "annuity_payment",
            "discount_rate_annual",
            "payment_timing",
        ],
        "unit": "USD",
    },
    {
        "name": "Timing-adjusted valuation liability cash flow",
        "output_variable": "valuation_liability_cash_flow",
        "dependencies": [
            "valuation_annuity_payment",
            "refund_claim",
            "commutation_payment",
            "maintenance_expense",
        ],
        "unit": "USD",
    },
]
for _formula in FORMULAS:
    _formula["function_ref"] = function_ref(_formula["output_variable"])

PUBLISHED_OUTPUTS: list[dict] = [
    {
        "variable_name": "survival_probability",
        "display_name": "Primary survival probability",
        "unit": "probability",
        "dimension": "Policy x scenario x projection month",
        "aggregation": "sum",
        "description": "LifeLib-reconciled end-of-month survival probability.",
    },
    {
        "variable_name": "joint_survival_probability",
        "display_name": "Joint annuitant survival probability",
        "unit": "probability",
        "dimension": "Policy x scenario x projection month",
        "aggregation": "sum",
        "description": "LifeLib-reconciled joint-annuitant end-of-month survival.",
    },
    {
        "variable_name": "last_survivor_probability",
        "display_name": "Last-survivor probability",
        "unit": "probability",
        "dimension": "Policy x scenario x projection month",
        "aggregation": "sum",
        "description": "LifeLib-reconciled probability at least one covered life survives.",
    },
    {
        "variable_name": "payment_factor_life",
        "display_name": "Life-contingent payment factor",
        "unit": "factor",
        "dimension": "Policy x scenario x projection month",
        "aggregation": "sum",
        "description": "LifeLib-reconciled survival and survivor-reduction factor.",
    },
    {
        "variable_name": "certain_floor",
        "display_name": "Certain-period payment floor",
        "unit": "factor",
        "dimension": "Policy x scenario x projection month",
        "aggregation": "sum",
        "description": "LifeLib-reconciled elected or refund-derived guarantee floor.",
    },
    {
        "variable_name": "payment_factor",
        "display_name": "Master payment factor",
        "unit": "factor",
        "dimension": "Policy x scenario x projection month",
        "aggregation": "sum",
        "description": "LifeLib-reconciled maximum of guarantee and life factors.",
    },
    {
        "variable_name": "annuity_payment",
        "display_name": "Expected annuity payment",
        "unit": "USD",
        "dimension": "Policy x scenario x projection month",
        "aggregation": "sum",
        "description": "LifeLib-reconciled expected monthly annuity outgo.",
    },
    {
        "variable_name": "commutation_fraction",
        "display_name": "Cumulative commuted fraction",
        "unit": "rate",
        "dimension": "Policy x scenario x projection month",
        "aggregation": "sum",
        "description": "Illustrative reduction to the remaining certain-payment slice.",
    },
    {
        "variable_name": "commuted_value",
        "display_name": "Value of commutable certain payments",
        "unit": "USD",
        "dimension": "Policy x scenario x projection month",
        "aggregation": "sum",
        "description": "Illustrative value of remaining guaranteed installments.",
    },
    {
        "variable_name": "commutation_payment",
        "display_name": "Net commutation payment",
        "unit": "USD",
        "dimension": "Policy x scenario x projection month",
        "aggregation": "sum",
        "description": "Optional, illustrative net withdrawal outgo; not approved for CARVM.",
    },
    {
        "variable_name": "surrender_charge",
        "display_name": "Surrender charge retained",
        "unit": "USD",
        "dimension": "Policy x scenario x projection month",
        "aggregation": "sum",
        "description": "Illustrative charge disclosure excluded from liability outgo.",
    },
    {
        "variable_name": "pv_annuity_payment",
        "display_name": "PV of expected annuity payment",
        "unit": "USD",
        "dimension": "Policy x scenario x projection month",
        "aggregation": "sum",
        "description": "MentorAmp-only illustrative present value output.",
    },
    {
        "variable_name": "refund_claim",
        "display_name": "Expected cash-refund claim",
        "unit": "USD",
        "dimension": "Policy x scenario x projection month",
        "aggregation": "sum",
        "description": "LifeLib-reconciled expected remaining-premium refund.",
    },
    {
        "variable_name": "obligation_factor",
        "display_name": "Payment obligation factor",
        "unit": "factor",
        "dimension": "Policy x scenario x projection month",
        "aggregation": "sum",
        "description": "LifeLib-reconciled closing payment-obligation probability.",
    },
    {
        "variable_name": "maintenance_expense",
        "display_name": "Expected maintenance expense",
        "unit": "USD",
        "dimension": "Policy x scenario x projection month",
        "aggregation": "sum",
        "description": "LifeLib-reconciled expected maintenance expense.",
    },
    {
        "variable_name": "liability_cash_flow",
        "display_name": "Gross liability cash flow",
        "unit": "USD",
        "dimension": "Policy x scenario x projection month",
        "aggregation": "sum",
        "description": "LifeLib-reconciled annuity, refund, and maintenance outgo.",
    },
    {
        "variable_name": "net_cash_flow",
        "display_name": "Net cash flow",
        "unit": "USD",
        "dimension": "Policy x scenario x projection month",
        "aggregation": "sum",
        "description": "LifeLib-reconciled income-positive insurer cash flow.",
    },
    {
        "variable_name": "cash_flow_identity_residual",
        "display_name": "Cash-flow identity residual",
        "unit": "USD",
        "dimension": "Policy x scenario x projection month",
        "aggregation": "sum",
        "description": "Expected to be zero when component cash flows reconcile.",
    },
    {
        "variable_name": "pv_liability_cash_flow",
        "display_name": "PV of gross liability cash flow",
        "unit": "USD",
        "dimension": "Policy x scenario x projection month",
        "aggregation": "sum",
        "description": "MentorAmp-only timing-correct present value output.",
    },
    {
        "variable_name": "reserve",
        "display_name": "Illustrative prospective reserve",
        "unit": "USD",
        "dimension": "Policy x scenario x projection month",
        "aggregation": "end_of_period",
        "description": "MentorAmp-only reserve; not a LifeLib-reconciled output.",
    },
]

MODEL = {
    "name": "SPIA Reference Model",
    "product_code": PRODUCT_CODE,
    "description": "Native MentorAmp SPIA model reconciled to a pinned external benchmark. "
    "Illustrative and not actuarially approved.",
    "version": {
        "version_label": "v1.0-reference",
        "block_name": "SPIA Reference Block",
        "profile_name": "Reference reconciliation",
        "basis": "Illustrative standardized cash flow",
        "methodology": "Monthly generational mortality and life-contingent cash flow",
        "notes": "Increment 7: optional illustrative certain-payment commutation, disabled by default.",
        "configuration": {
            "implementation_version": IMPLEMENTATION_VERSION,
            "reference_model": LIFELIB_SPIA_REFERENCE.as_configuration(),
            "reconciliation_scope": [
                "survival_probability",
                "joint_survival_probability",
                "last_survivor_probability",
                "payment_factor_life",
                "certain_floor",
                "payment_factor",
                "annuity_payment",
                "commutation_fraction",
                "commuted_value",
                "commutation_payment",
                "surrender_charge",
                "refund_claim",
                "obligation_factor",
                "maintenance_expense",
                "liability_cash_flow",
                "net_cash_flow",
                "cash_flow_identity_residual",
            ],
            "mentoramp_only_outputs": [
                "discount_factor",
                "pv_annuity_payment",
                "pv_refund_claim",
                "pv_commutation_payment",
                "pv_maintenance_expense",
                "pv_liability_cash_flow",
                "reserve",
            ],
            "commutation": {
                "enabled_by_default": False,
                "assumption_status": "illustrative_unverified",
                "valuation_use": "best_estimate_only",
                "carvm_eligible": False,
                "limitations": [
                    "Applies only to the remaining certain-payment slice.",
                    "Requires explicit policy opt-in and an imported surrender-charge table.",
                    "Existing policies must supply opening cumulative payments and prior commuted fraction.",
                    "Missing jurisdiction eligibility data is treated as excluded.",
                    "Must not be used for statutory CARVM reserves or approved product illustration.",
                ],
            },
        },
    },
    "formula_group": {
        "name": "SPIA reference core",
        "description": "Joint-life mortality, survivor reductions, scheduling, cash flow and valuation.",
        "lineage_state": "external_reference",
        "version_label": "v1",
    },
}

MODEL_DEFINITION = {
    "model": MODEL,
    "variables": VARIABLES,
    "formulas": FORMULAS,
    "published_outputs": PUBLISHED_OUTPUTS,
}
