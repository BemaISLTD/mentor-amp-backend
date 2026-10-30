"""Native illustrative five-year MYGA definition.

The implementation is MentorAmp-owned. External models are used only as independent
development oracles and are not imported or executed by the application.
"""

import math

from app.core.formula_engine.formulas import register_function
from app.products.myga_lite.decrements import (
    DecrementMethodology,
    IllustrativeBestEstimateDecrements,
    get_current_decrement_methodology,
    reset_decrement_methodology,
    set_decrement_methodology,
)

PRODUCT_CODE = "MYGA"
PRODUCT_NAME = "Five-Year Multi-Year Guaranteed Annuity"
FUNCTION_PREFIX = "myga_illustrative"
IMPLEMENTATION_VERSION = "myga-illustrative-v0.1"
MORTALITY_TABLE_NAME = "SYNTH_MORT_GOMPERTZ_2026"
SURRENDER_CHARGE_TABLE_NAME = "MYGA_SURRENDER_CHARGE"
SURRENDER_CHARGE_AGE_CAP_TABLE_NAME = "MYGA_SC_AGE_CAP"
DEFAULT_GUARANTEE_PERIOD_YEARS = 5
DEFAULT_DECLARED_RATE = 0.0445
DEFAULT_GMIR = 0.0025
DEFAULT_GMSV_RATE = 0.028
DEFAULT_MVA_REFERENCE_YIELD = 0.05
# Validation lower bound for illustrative contractual nonforfeiture assumption in MentorAmp development models
MIN_GMSV_RATE = 0.0015


def _valid_withdrawal(account_value_open: float, withdrawal: float) -> float:
    """Reject a withdrawal that exceeds the available account value."""
    opening = float(account_value_open)
    requested = float(withdrawal)
    if not math.isfinite(opening) or not math.isfinite(requested):
        raise ValueError("account value and withdrawal must be finite")
    if requested < 0.0 or requested > opening:
        raise ValueError("withdrawal must be between zero and opening account value")
    return requested


def guarantee_period_index(policy_year: float, guarantee_period_years: float) -> float:
    """One-based guarantee-period number containing the policy year."""
    year = int(policy_year)
    period = int(guarantee_period_years)
    if year < 1 or period < 1:
        raise ValueError("policy year and guarantee period must be positive")
    return float((year - 1) // period + 1)


def year_in_guarantee_period(
    policy_year: float, guarantee_period_years: float, guarantee_period_index: float,
) -> float:
    """One-based contract year within the current guarantee period."""
    return float(int(policy_year) - int(guarantee_period_years) * (int(guarantee_period_index) - 1))


def month_in_policy_year(policy_month: float) -> float:
    """Zero-based completed months within the current policy year."""
    month = int(policy_month)
    if month < 1:
        raise ValueError("policy month must be positive")
    return float((month - 1) % 12)


def renewal_window(
    guarantee_period_index: float,
    year_in_guarantee_period: float,
    month_in_policy_year: float,
) -> float:
    """Return one during the first month of a renewal guarantee period."""
    return float(
        int(guarantee_period_index) > 1
        and int(year_in_guarantee_period) == 1
        and int(month_in_policy_year) == 0
    )


def credited_rate(
    guarantee_period_index: float,
    renewal_window: float,
    declared_rate: float,
    gmir: float,
    market_rate: float,
    renewal_spread: float,
    credited_rate_previous: float,
) -> float:
    """Annual account crediting rate locked for each guarantee period."""
    if int(guarantee_period_index) == 1:
        return float(declared_rate)
    if renewal_window >= 0.5:
        return max(float(gmir), float(market_rate) - float(renewal_spread))
    return float(credited_rate_previous)


def locked_mva_reference_yield(
    guarantee_period_index: float,
    renewal_window: float,
    mva_ref_yield_at_issue: float,
    mva_ref_yield: float,
    locked_mva_reference_yield_previous: float,
) -> float:
    """Reference yield fixed at issue and reset only in a renewal window."""
    if int(guarantee_period_index) == 1:
        return float(mva_ref_yield_at_issue)
    if renewal_window >= 0.5:
        return float(mva_ref_yield)
    return float(locked_mva_reference_yield_previous)


def account_value_open(
    projection_month: float, account_value: float, account_value_close_previous: float,
) -> float:
    """Opening account value from input state or the prior projected month."""
    return float(account_value if int(projection_month) == 1 else account_value_close_previous)


def mgsv_open(projection_month: float, mgsv_value: float, mgsv_close_previous: float) -> float:
    """Opening minimum-guaranteed surrender value from input or prior month."""
    return float(mgsv_value if int(projection_month) == 1 else mgsv_close_previous)


def free_withdrawal_available(
    policy_year: float,
    month_in_policy_year: float,
    free_wd_base: float,
    account_value_open: float,
    free_withdrawal_available_previous: float,
    withdrawal: float,
) -> float:
    """Remaining annual amount exempt from both surrender charge and MVA."""
    if int(month_in_policy_year) == 0:
        allowance = 0.10 * (float(free_wd_base) if int(policy_year) == 1 else float(account_value_open))
    else:
        allowance = float(free_withdrawal_available_previous)
    return max(0.0, allowance - max(float(withdrawal), 0.0))


def account_value_before_credit(account_value_open: float, withdrawal: float) -> float:
    """Account value after the contractual withdrawal and before interest crediting."""
    return max(0.0, float(account_value_open) - _valid_withdrawal(account_value_open, withdrawal))


def account_value_close(account_value_before_credit: float, credited_rate: float) -> float:
    """Month-end account value after effective annual interest is credited."""
    if credited_rate <= -1.0:
        raise ValueError("credited rate must be greater than -100%")
    return float(account_value_before_credit) * (1.0 + float(credited_rate)) ** (1.0 / 12.0)


def interest_credited(account_value_close: float, account_value_before_credit: float) -> float:
    """Monthly dollar interest credited to account value."""
    return float(account_value_close) - float(account_value_before_credit)


def mgsv_close(
    mgsv_open: float, withdrawal: float, contract_charge_annual: float, gmsv_rate: float,
) -> float:
    """Month-end contractual nonforfeiture floor (MGSV) after gross withdrawal and monthly contract charge."""
    if not math.isfinite(float(gmsv_rate)) or gmsv_rate < MIN_GMSV_RATE:
        raise ValueError("GMSV rate must be at least 0.15%")
    base = max(0.0, float(mgsv_open) - float(withdrawal) - float(contract_charge_annual) / 12.0)
    return base * (1.0 + float(gmsv_rate)) ** (1.0 / 12.0)


def surrender_charge_rate(
    renewal_window: float, schedule_rate: float, attained_age_charge_cap: float,
    charge_schedule: str,
) -> float:
    """Applicable charge rate after renewal-window waiver and attained-age cap."""
    del charge_schedule  # explicit dependency establishes lookup/formula execution order
    if renewal_window >= 0.5:
        return 0.0
    return max(0.0, min(float(schedule_rate), float(attained_age_charge_cap)))


def surrender_excess(account_value_close: float, free_withdrawal_available: float) -> float:
    """Account value exposed to surrender charge and MVA."""
    return max(0.0, float(account_value_close) - float(free_withdrawal_available))


def surrender_charge(surrender_excess: float, surrender_charge_rate: float) -> float:
    """Dollar surrender charge applied only to value above the free allowance."""
    return float(surrender_excess) * float(surrender_charge_rate)


def mva_term(
    policy_year: float,
    month_in_policy_year: float,
    guarantee_period_years: float,
    guarantee_period_index: float,
) -> float:
    """Remaining MVA duration in years to the end of the current guarantee period."""
    elapsed_months = int(month_in_policy_year) + 1
    fraction_left = max(0, 12 - elapsed_months) / 12.0
    full_years_left = int(guarantee_period_years) * int(guarantee_period_index) - int(policy_year)
    return max(0.0, fraction_left + full_years_left)


def market_value_adjustment(
    renewal_window: float,
    locked_mva_reference_yield: float,
    mva_ref_yield: float,
    mva_term: float,
    surrender_excess: float,
    surrender_charge: float,
) -> float:
    """Linear MVA on excess value, symmetrically capped by the surrender charge."""
    if renewal_window >= 0.5:
        return 0.0
    raw = (
        (float(locked_mva_reference_yield) - float(mva_ref_yield))
        * float(mva_term)
        * float(surrender_excess)
    )
    cap = max(0.0, float(surrender_charge))
    return max(-cap, min(raw, cap))


def surrender_value(
    account_value_close: float, market_value_adjustment: float, surrender_charge: float,
) -> float:
    """Contractual surrender amount before applying the contractual nonforfeiture floor."""
    return float(account_value_close) + float(market_value_adjustment) - float(surrender_charge)


def surrender_benefit(surrender_value: float, mgsv_close: float) -> float:
    """Cash surrender benefit after applying the contractual nonforfeiture floor as a lower bound."""
    return max(float(surrender_value), float(mgsv_close))


def death_benefit(account_value_close: float, surrender_benefit: float) -> float:
    """Death benefit calculation.

    STATUS: NEEDS ACTUARIAL PRODUCT OWNER DECISION.

    Current development baseline uses Option B: max(account_value_close, surrender_benefit).
    Options under review for Actuarial Product Owner decision:
      - Option A: death_benefit = account_value_close
      - Option B: death_benefit = max(account_value_close, surrender_benefit)
      - Option C: explicit APO-specified contractual rule

    This formula must not be represented as an approved or final actuarial rule until
    formally approved by the Actuarial Product Owner.
    """
    return max(float(account_value_close), float(surrender_benefit))


# Supporting decrement, expense, and ledger functions. The twenty entries in ``_FUNCTIONS``
# are the ordered contract-value stages requested by the product specification. These scalar
# helpers expand the multi-output decrement and ledger stages for MentorAmp's one-output-per-
# formula execution contract.
def statutory_floor_rate(five_year_cmt: float) -> float:
    """Illustrative contractual nonforfeiture corridor: rounded CMT less 1.25%, bounded by 0.15% and 3%."""
    rounded = round(float(five_year_cmt) / 0.0005) * 0.0005
    return min(0.03, max(0.0015, rounded - 0.0125))


def monthly_mortality(mortality_rate_annual: float) -> float:
    """Convert annual mortality to an equivalent monthly probability via methodology provider."""
    return get_current_decrement_methodology().get_monthly_mortality(mortality_rate_annual)


def gmir_lapse_factor(gmir: float) -> float:
    """Dynamic lapse multiplier from GMIR via methodology provider."""
    return get_current_decrement_methodology().get_gmir_lapse_factor(gmir)


def base_lapse_rate(policy_year: float, guarantee_period_years: float) -> float:
    """Baseline duration lapse rate via methodology provider."""
    return get_current_decrement_methodology().get_base_lapse_rate(policy_year, guarantee_period_years)


def charge_schedule(guarantee_period_index: float) -> str:
    """Select the initial or renewal surrender-charge schedule."""
    return "initial" if int(guarantee_period_index) == 1 else "renewal"


def dynamic_lapse_exponent(policy_year: float, guarantee_period_years: float) -> float:
    """Use exponent 2.0 in the initial charge period and 2.5 thereafter."""
    return 2.0 if int(policy_year) <= int(guarantee_period_years) else 2.5


def dynamic_lapse_market(
    credited_rate: float, market_rate: float, dynamic_lapse_exponent: float,
) -> float:
    """Interest-sensitive lapse response with a 50 bp no-response buffer."""
    credited = float(credited_rate)
    market = float(market_rate)
    power = float(dynamic_lapse_exponent)
    if credited >= market:
        return -1.25 * (credited - market) ** power
    if credited >= market - 0.005:
        return 0.0
    return 1.25 * (market - 0.005 - credited) ** power


def dynamic_lapse_rate(
    dynamic_lapse_market: float,
    surrender_benefit: float,
    account_value_close: float,
) -> float:
    """Gate dynamic lapse as surrender-value haircut approaches twenty percent."""
    if account_value_close <= 0.0:
        return 0.0
    haircut = 1.0 - float(surrender_benefit) / float(account_value_close)
    return float(dynamic_lapse_market) * max(0.0, 1.0 - 5.0 * haircut)


def mva_lapse_factor_at(
    mva_term: float, renewal_window: float, mva_lapse_factor: float,
) -> float:
    """Apply the dampener only while an MVA is contractually in force."""
    return float(mva_lapse_factor) if mva_term > 0.0 and renewal_window < 0.5 else 1.0


def annual_lapse_rate(
    base_lapse_rate: float,
    gmir_lapse_factor: float,
    dynamic_lapse_rate: float,
    mva_lapse_factor_at: float,
) -> float:
    """Total annual lapse via methodology provider."""
    return get_current_decrement_methodology().get_annual_lapse_rate(
        base_lapse_rate, gmir_lapse_factor, dynamic_lapse_rate, mva_lapse_factor_at
    )


def monthly_lapse_rate(annual_lapse_rate: float) -> float:
    """Convert annual lapse to an equivalent monthly probability via methodology provider."""
    return get_current_decrement_methodology().get_monthly_lapse_rate(annual_lapse_rate)


def annuitization_rate(renewal_window: float) -> float:
    """Annuitization election rate via methodology provider."""
    return get_current_decrement_methodology().get_annuitization_rate(renewal_window)


def inforce_open(
    projection_month: float, inforce_close_previous: float,
) -> float:
    """Opening policy count, initialized to one at the valuation date."""
    return 1.0 if int(projection_month) == 1 else float(inforce_close_previous)


def maturity_rate(attained_age: float, month_in_policy_year: float) -> float:
    """Terminate in the month whose end advances the policyholder to age 100."""
    month_end_age = float(attained_age) + (float(month_in_policy_year) + 1.0) / 12.0
    return 1.0 if month_end_age >= 100.0 else 0.0


def inforce_close(
    inforce_open: float,
    annuitization_rate: float,
    monthly_mortality: float,
    monthly_lapse_rate: float,
    maturity_rate: float,
) -> float:
    """Sequential monthly policy-count rollforward after all decrements."""
    return (
        float(inforce_open)
        * (1.0 - float(annuitization_rate))
        * (1.0 - float(monthly_mortality))
        * (1.0 - float(monthly_lapse_rate))
        * (1.0 - float(maturity_rate))
    )


def death_claims(
    inforce_open: float, annuitization_rate: float, monthly_mortality: float,
    death_benefit: float,
) -> float:
    """Pay death benefits on policies surviving annuitization elections."""
    return (
        float(inforce_open) * (1.0 - float(annuitization_rate))
        * float(monthly_mortality) * float(death_benefit)
    )


def surrender_claims(
    inforce_open: float, annuitization_rate: float, monthly_mortality: float,
    monthly_lapse_rate: float, surrender_benefit: float,
) -> float:
    """Pay full surrender benefits after annuitization and mortality decrements."""
    return (
        float(inforce_open) * (1.0 - float(annuitization_rate))
        * (1.0 - float(monthly_mortality)) * float(monthly_lapse_rate)
        * float(surrender_benefit)
    )


def annuitization_transfer(
    inforce_open: float, annuitization_rate: float, account_value_before_credit: float,
) -> float:
    """Transfer the account value before monthly interest to elected annuitants."""
    return float(inforce_open) * float(annuitization_rate) * float(account_value_before_credit)


def maturity_benefit(
    inforce_open: float, annuitization_rate: float, monthly_mortality: float,
    monthly_lapse_rate: float, maturity_rate: float, account_value_close: float,
) -> float:
    """Pay surviving account values in the month the contract reaches maturity."""
    return (
        float(inforce_open) * (1.0 - float(annuitization_rate))
        * (1.0 - float(monthly_mortality)) * (1.0 - float(monthly_lapse_rate))
        * float(maturity_rate) * float(account_value_close)
    )


def maintenance_expense(
    inforce_open: float, policy_year: float, annual_maintenance_expense: float,
    expense_inflation_rate: float,
) -> float:
    """Inflate the monthly per-policy expense by completed policy years."""
    return (
        float(inforce_open) * float(annual_maintenance_expense) / 12.0
        * (1.0 + float(expense_inflation_rate)) ** (int(policy_year) - 1)
    )


def premium_cash_flow(projection_month: float, policy_month: float, premium: float) -> float:
    """Recognize the purchase premium only when issue falls in this projection."""
    return float(premium) if int(projection_month) == int(policy_month) == 1 else 0.0


def acquisition_commission(
    projection_month: float, policy_month: float, premium: float, commission_rate: float,
) -> float:
    """Pay acquisition commission once, on new business at issue."""
    return (
        float(premium) * float(commission_rate)
        if int(projection_month) == int(policy_month) == 1 else 0.0
    )


def gross_withdrawal(account_value_open: float, withdrawal: float) -> float:
    """Gross withdrawal requested and debited from opening account value."""
    return _valid_withdrawal(account_value_open, withdrawal)


def free_withdrawal_allowance(
    policy_year: float,
    month_in_policy_year: float,
    free_wd_base: float,
    account_value_open: float,
    free_withdrawal_available_previous: float,
) -> float:
    """Annual free withdrawal allowance available at the beginning of the month before current withdrawal."""
    if int(month_in_policy_year) == 0:
        return 0.10 * (float(free_wd_base) if int(policy_year) == 1 else float(account_value_open))
    return float(free_withdrawal_available_previous)


def chargeable_withdrawal(gross_withdrawal: float, free_withdrawal_allowance: float) -> float:
    """Portion of gross withdrawal exceeding the available free withdrawal allowance."""
    return max(0.0, float(gross_withdrawal) - float(free_withdrawal_allowance))


def surrender_charge_on_withdrawal(
    chargeable_withdrawal: float, surrender_charge_rate: float,
) -> float:
    """Contractual surrender charge assessed on the chargeable portion of partial withdrawal."""
    return float(chargeable_withdrawal) * float(surrender_charge_rate)


def withdrawal_mva(
    renewal_window: float,
    locked_mva_reference_yield: float,
    mva_ref_yield: float,
    mva_term: float,
    chargeable_withdrawal: float,
    surrender_charge_on_withdrawal: float,
) -> float:
    """Linear MVA applied to chargeable partial withdrawal, capped symmetrically by the withdrawal charge."""
    if renewal_window >= 0.5 or float(chargeable_withdrawal) <= 0.0:
        return 0.0
    raw = (
        (float(locked_mva_reference_yield) - float(mva_ref_yield))
        * float(mva_term)
        * float(chargeable_withdrawal)
    )
    cap = max(0.0, float(surrender_charge_on_withdrawal))
    return max(-cap, min(raw, cap))


def net_withdrawal_benefit(
    gross_withdrawal: float,
    surrender_charge_on_withdrawal: float,
    withdrawal_mva: float,
) -> float:
    """Net benefit amount paid to policyholder for partial withdrawal after charges and MVA."""
    return max(0.0, float(gross_withdrawal) - float(surrender_charge_on_withdrawal) + float(withdrawal_mva))


def withdrawal_payment(
    inforce_open: float,
    net_withdrawal_benefit: float,
    account_value_open: float | None = None,
) -> float:
    """Pay net elective withdrawal benefits to policies active at the month's opening."""
    if account_value_open is not None:
        gross = _valid_withdrawal(account_value_open, net_withdrawal_benefit)
        return float(inforce_open) * gross
    return float(inforce_open) * float(net_withdrawal_benefit)


def liability_outgo(
    withdrawal_payment: float,
    death_claims: float,
    surrender_claims: float,
    annuitization_transfer: float,
    maturity_benefit: float,
    maintenance_expense: float,
    acquisition_commission: float,
) -> float:
    """External policyholder and expense outgo; internal account movements are excluded."""
    return math.fsum((
        withdrawal_payment, death_claims, surrender_claims, annuitization_transfer,
        maturity_benefit, maintenance_expense, acquisition_commission,
    ))


def net_cash_flow(premium_cash_flow: float, liability_outgo: float) -> float:
    """Insurer cash flow using income-positive and outgo-negative signs."""
    return float(premium_cash_flow) - float(liability_outgo)


def discount_factor(discount_rate_annual: float, projection_month: float) -> float:
    """Discount month-end cash flows at the effective annual scenario rate."""
    if discount_rate_annual <= -1.0:
        raise ValueError("discount rate must be greater than -100%")
    return (1.0 + float(discount_rate_annual)) ** (-float(projection_month) / 12.0)


def pv_liability_outgo(liability_outgo: float, discount_factor: float) -> float:
    """Discount external monthly outgo to the valuation date."""
    return float(liability_outgo) * float(discount_factor)


_SUPPORT_FUNCTIONS = [
    ("statutory_floor_rate", statutory_floor_rate),
    ("monthly_mortality", monthly_mortality),
    ("gmir_lapse_factor", gmir_lapse_factor),
    ("base_lapse_rate", base_lapse_rate),
    ("charge_schedule", charge_schedule),
    ("dynamic_lapse_exponent", dynamic_lapse_exponent),
    ("dynamic_lapse_market", dynamic_lapse_market),
    ("dynamic_lapse_rate", dynamic_lapse_rate),
    ("mva_lapse_factor_at", mva_lapse_factor_at),
    ("annual_lapse_rate", annual_lapse_rate),
    ("monthly_lapse_rate", monthly_lapse_rate),
    ("annuitization_rate", annuitization_rate),
    ("inforce_open", inforce_open),
    ("maturity_rate", maturity_rate),
    ("inforce_close", inforce_close),
    ("death_claims", death_claims),
    ("surrender_claims", surrender_claims),
    ("annuitization_transfer", annuitization_transfer),
    ("maturity_benefit", maturity_benefit),
    ("maintenance_expense", maintenance_expense),
    ("premium_cash_flow", premium_cash_flow),
    ("acquisition_commission", acquisition_commission),
    ("gross_withdrawal", gross_withdrawal),
    ("free_withdrawal_allowance", free_withdrawal_allowance),
    ("chargeable_withdrawal", chargeable_withdrawal),
    ("surrender_charge_on_withdrawal", surrender_charge_on_withdrawal),
    ("withdrawal_mva", withdrawal_mva),
    ("net_withdrawal_benefit", net_withdrawal_benefit),
    ("withdrawal_payment", withdrawal_payment),
    ("liability_outgo", liability_outgo),
    ("net_cash_flow", net_cash_flow),
    ("discount_factor", discount_factor),
    ("pv_liability_outgo", pv_liability_outgo),
    ("interest_credited", interest_credited),
]

_SUPPORT_EXPRESSIONS = {
    "statutory_floor_rate": "clamp(round_to_5bp(cmt5) - 0.0125, 0.0015, 0.03)",
    "monthly_mortality": "1 - (1 - annual_mortality)^(1/12)",
    "gmir_lapse_factor": "1.25 if GMIR <= 1%; 1.0 if <= 2.5%; otherwise 0.70",
    "base_lapse_rate": "75% in each first post-guarantee year; otherwise 1%",
    "charge_schedule": "initial in guarantee period 1; renewal thereafter",
    "dynamic_lapse_exponent": "2.0 in initial period; 2.5 thereafter",
    "dynamic_lapse_market": "1.25 * signed market-credit spread beyond 50bp buffer ^ exponent",
    "dynamic_lapse_rate": "market response * max(0, 1 - 5 * (1 - surrender_benefit / AV))",
    "mva_lapse_factor_at": "configured factor while MVA active; otherwise 1",
    "annual_lapse_rate": "clamp(base * GMIR factor + dynamic * MVA factor, 0.005, 0.90)",
    "monthly_lapse_rate": "1 - (1 - annual_lapse_rate)^(1/12)",
    "annuitization_rate": "0.01 in renewal window; otherwise 0",
    "inforce_open": "1 at valuation; otherwise previous inforce_close",
    "maturity_rate": "1 when attained age reaches 100 at month end; otherwise 0",
    "inforce_close": "inforce_open * (1-a) * (1-q) * (1-w) * (1-maturity)",
    "death_claims": "inforce_open * (1-a) * q * death_benefit",
    "surrender_claims": "inforce_open * (1-a) * (1-q) * w * surrender_benefit",
    "annuitization_transfer": "inforce_open * a * account_value_before_credit",
    "maturity_benefit": "inforce_open * (1-a) * (1-q) * (1-w) * maturity * AV",
    "maintenance_expense": "inforce_open * annual_expense / 12 * (1 + inflation)^(year-1)",
    "premium_cash_flow": "premium at issue in projection month 1; otherwise 0",
    "acquisition_commission": "premium * commission_rate at issue in projection month 1; otherwise 0",
    "gross_withdrawal": "validated withdrawal (0 <= withdrawal <= account_value_open)",
    "free_withdrawal_allowance": "10% of anniversary AV (or free_wd_base in Y1) in month 0; else prior available",
    "chargeable_withdrawal": "max(0, gross_withdrawal - free_withdrawal_allowance)",
    "surrender_charge_on_withdrawal": "chargeable_withdrawal * surrender_charge_rate",
    "withdrawal_mva": "clamp((locked_yield - current_yield) * term * chargeable_withdrawal, -charge, charge)",
    "net_withdrawal_benefit": "max(0, gross_withdrawal - surrender_charge_on_withdrawal + withdrawal_mva)",
    "withdrawal_payment": "inforce_open * net_withdrawal_benefit",
    "liability_outgo": "withdrawals + claims + annuitization + maturity + expenses + commission",
    "net_cash_flow": "premium_cash_flow - liability_outgo",
    "discount_factor": "(1 + annual_discount_rate)^(-projection_month/12)",
    "pv_liability_outgo": "liability_outgo * discount_factor",
    "interest_credited": "av_close - av_before_credit",
}


_FUNCTIONS = [
    ("guarantee_period_index", guarantee_period_index,
     "gp_index = floor((policy_year - 1) / guarantee_period_years) + 1",
     "Locates the policy year in its one-based guarantee period."),
    ("year_in_guarantee_period", year_in_guarantee_period,
     "year_in_gp = policy_year - guarantee_period_years * (gp_index - 1)",
     "Restarts contract-year duration when a new guarantee period begins."),
    ("month_in_policy_year", month_in_policy_year,
     "month_in_year = (policy_month - 1) mod 12",
     "Returns completed months within the current contract year."),
    ("renewal_window", renewal_window,
     "in_window = gp_index > 1 and year_in_gp = 1 and month_in_year = 0",
     "Identifies the one-month fee-free renewal window."),
    ("credited_rate", credited_rate,
     "rate = declared_rate in GP1; max(gmir, market_rate - spread) at renewal; otherwise prior rate",
     "Locks an annual crediting rate for each guarantee period."),
    ("locked_mva_reference_yield", locked_mva_reference_yield,
     "locked_yield = issue_yield in GP1; current reference yield at renewal; otherwise prior yield",
     "Locks the MVA comparison yield for the guarantee period."),
    ("account_value_open", account_value_open,
     "av_open = input account value in month 1; otherwise prior av_close",
     "Carries the policy account balance forward month by month."),
    ("mgsv_open", mgsv_open,
     "mgsv_open = input floor in month 1; otherwise prior mgsv_close",
     "Carries the contractual nonforfeiture floor forward independently from account value."),
    ("free_withdrawal_available", free_withdrawal_available,
     "free_available = annual allowance reset less withdrawals used",
     "Tracks the annual value exempt from both charge and MVA."),
    ("account_value_before_credit", account_value_before_credit,
     "av_before_credit = max(0, av_open - withdrawal)",
     "Deducts withdrawals before monthly interest is credited."),
    ("account_value_close", account_value_close,
     "av_close = av_before_credit * (1 + credited_rate)^(1/12)",
     "Credits one month of effective annual interest."),
    ("mgsv_close", mgsv_close,
     "mgsv_close = max(0, mgsv_open - withdrawal - annual_charge/12) * (1 + gmsv_rate)^(1/12)",
     "Accumulates the contractual nonforfeiture floor after gross withdrawal deduction."),
    ("surrender_charge_rate", surrender_charge_rate,
     "charge_rate = 0 in renewal window; otherwise min(schedule_rate, age_cap)",
     "Applies the duration schedule and attained-age cap."),
    ("surrender_excess", surrender_excess,
     "excess = max(0, av_close - free_available)",
     "Limits surrender adjustments to value above the free allowance."),
    ("surrender_charge", surrender_charge,
     "charge = charge_rate * excess",
     "Calculates the dollar surrender charge."),
    ("mva_term", mva_term,
     "term = fractional contract year remaining + whole guarantee years remaining",
     "Measures time remaining until the current guarantee period ends."),
    ("market_value_adjustment", market_value_adjustment,
     "mva = clamp((locked_yield - current_yield) * term * excess, -charge, charge)",
     "Applies a linear, symmetric, charge-capped market value adjustment."),
    ("surrender_value", surrender_value,
     "surrender_value = av_close + mva - charge",
     "Combines the account value, MVA, and charge in contractual order."),
    ("surrender_benefit", surrender_benefit,
     "surrender_benefit = max(surrender_value, mgsv_close)",
     "Applies the contractual nonforfeiture floor after MVA and charge."),
    ("death_benefit", death_benefit,
     "death_benefit = max(av_close, surrender_benefit)",
     "Prevents the death benefit from falling below account or surrender value."),
]


def function_ref(name: str) -> str:
    return f"{FUNCTION_PREFIX}.{name}"


def register_functions() -> None:
    """Register the twenty ordered MYGA account-value functions idempotently."""
    for name, func, expression, explanation in _FUNCTIONS:
        register_function(
            function_ref(name), func, expression_text=expression, explanation=explanation,
            illustrative=True, implementation_version=IMPLEMENTATION_VERSION,
        )
    for name, func in _SUPPORT_FUNCTIONS:
        register_function(
            function_ref(name), func,
            expression_text=_SUPPORT_EXPRESSIONS[name],
            explanation=(func.__doc__ or "").strip(),
            illustrative=True, implementation_version=IMPLEMENTATION_VERSION,
        )


def _variable(name: str, kind: str, *, unit: str | None = None, source: dict | None = None,
              required: bool = True, default_value=None,
              allow_scenario_override: bool = False) -> dict:
    row = {
        "name": name, "display_name": name.replace("_", " ").title(), "kind": kind,
        "data_type": "number", "unit": unit, "required": required,
        "source": source or {"type": kind},
        "description": f"Illustrative MYGA {name.replace('_', ' ')}.",
        "allow_scenario_override": allow_scenario_override,
    }
    if default_value is not None:
        row["default_value"] = default_value
    return row


VARIABLES = [
    {
        "name": "gender", "display_name": "Gender", "kind": "input",
        "data_type": "string", "unit": None, "required": True,
        "source": {"type": "input", "column": "gender", "transform": "upper"},
        "description": "Policyholder gender used by the synthetic mortality lookup.",
    },
    _variable("premium", "input", unit="USD", source={"type": "input", "column": "premium"}),
    _variable("account_value", "input", unit="USD", source={"type": "input", "column": "account_value"}),
    _variable("mgsv_value", "input", unit="USD", source={"type": "input", "column": "mgsv_value"}),
    _variable("free_wd_base", "input", unit="USD", source={"type": "input", "column": "free_wd_base"}),
    _variable("guarantee_period_years", "input", unit="years", source={"type": "input", "column": "guarantee_period_years"}),
    _variable("declared_rate", "input", unit="rate", source={"type": "input", "column": "declared_rate"}),
    _variable("gmir", "input", unit="rate", source={"type": "input", "column": "gmir"}),
    _variable("gmsv_rate", "input", unit="rate", source={"type": "input", "column": "gmsv_rate"}),
    _variable("mva_ref_yield_at_issue", "input", unit="rate", source={"type": "input", "column": "mva_ref_yield_at_issue"}),
    _variable("projection_month", "context", unit="month", source={"type": "context", "field": "projection_month"}),
    _variable("policy_month", "context", unit="month", source={"type": "context", "field": "policy_month"}),
    _variable("policy_year", "context", unit="year", source={"type": "context", "field": "policy_year"}),
    _variable("attained_age", "context", unit="years", source={"type": "context", "field": "attained_age"}),
    _variable("withdrawal", "manual", unit="USD", source={"type": "manual", "value": 0.0}, default_value=0.0,
              allow_scenario_override=True),
    _variable("market_rate", "manual", unit="rate", source={"type": "manual", "value": DEFAULT_DECLARED_RATE}, default_value=DEFAULT_DECLARED_RATE),
    _variable("renewal_spread", "manual", unit="rate", source={"type": "manual", "value": 0.0}, default_value=0.0),
    _variable("mva_ref_yield", "manual", unit="rate", source={"type": "manual", "value": DEFAULT_MVA_REFERENCE_YIELD}, default_value=DEFAULT_MVA_REFERENCE_YIELD),
    _variable("contract_charge_annual", "manual", unit="USD", source={"type": "manual", "value": 0.0}, default_value=0.0),
    _variable("schedule_rate", "factor", unit="rate", source={
        "type": "factor", "table": SURRENDER_CHARGE_TABLE_NAME, "value_column": "rate",
        "key_map": {"schedule": "charge_schedule", "year_in_gp": "year_in_guarantee_period"},
    }),
    _variable("attained_age_charge_cap", "factor", unit="rate", source={
        "type": "factor", "table": SURRENDER_CHARGE_AGE_CAP_TABLE_NAME,
        "value_column": "cap", "key_map": {"attained_age": "attained_age"},
    }),
    _variable("mortality_rate_annual", "assumption", unit="probability", source={
        "type": "assumption", "table": MORTALITY_TABLE_NAME, "value_column": "qx",
        "key_map": {"age": "attained_age", "gender": "gender"},
    }),
    _variable("mva_lapse_factor", "manual", unit="factor", source={"type": "manual", "value": 0.35}, default_value=0.35),
    _variable("annual_maintenance_expense", "manual", unit="USD/year", source={"type": "manual", "value": 50.0}, default_value=50.0),
    _variable("expense_inflation_rate", "manual", unit="rate", source={"type": "manual", "value": 0.025}, default_value=0.025),
    _variable("commission_rate", "manual", unit="rate", source={"type": "manual", "value": 0.02}, default_value=0.02),
    _variable("discount_rate_annual", "manual", unit="rate", source={"type": "manual", "value": 0.045}, default_value=0.045),
    _variable("credited_rate_previous", "prior_output", unit="rate", source={"type": "prior_output", "variable": "credited_rate", "offset": 1, "initial_value": DEFAULT_DECLARED_RATE}),
    _variable("locked_mva_reference_yield_previous", "prior_output", unit="rate", source={"type": "prior_output", "variable": "locked_mva_reference_yield", "offset": 1, "initial_value": DEFAULT_MVA_REFERENCE_YIELD}),
    _variable("account_value_close_previous", "prior_output", unit="USD", source={"type": "prior_output", "variable": "account_value_close", "offset": 1, "initial_value": 0.0}),
    _variable("mgsv_close_previous", "prior_output", unit="USD", source={"type": "prior_output", "variable": "mgsv_close", "offset": 1, "initial_value": 0.0}),
    _variable("free_withdrawal_available_previous", "prior_output", unit="USD", source={"type": "prior_output", "variable": "free_withdrawal_available", "offset": 1, "initial_value": 0.0}),
    _variable("inforce_close_previous", "prior_output", unit="count", source={"type": "prior_output", "variable": "inforce_close", "offset": 1, "initial_value": 1.0}),
]

_FORMULA_DEPS = [
    ("guarantee_period_index", ["policy_year", "guarantee_period_years"]),
    ("year_in_guarantee_period", ["policy_year", "guarantee_period_years", "guarantee_period_index"]),
    ("month_in_policy_year", ["policy_month"]),
    ("renewal_window", ["guarantee_period_index", "year_in_guarantee_period", "month_in_policy_year"]),
    ("credited_rate", ["guarantee_period_index", "renewal_window", "declared_rate", "gmir", "market_rate", "renewal_spread", "credited_rate_previous"]),
    ("locked_mva_reference_yield", ["guarantee_period_index", "renewal_window", "mva_ref_yield_at_issue", "mva_ref_yield", "locked_mva_reference_yield_previous"]),
    ("account_value_open", ["projection_month", "account_value", "account_value_close_previous"]),
    ("mgsv_open", ["projection_month", "mgsv_value", "mgsv_close_previous"]),
    ("free_withdrawal_available", ["policy_year", "month_in_policy_year", "free_wd_base", "account_value_open", "free_withdrawal_available_previous", "withdrawal"]),
    ("account_value_before_credit", ["account_value_open", "withdrawal"]),
    ("account_value_close", ["account_value_before_credit", "credited_rate"]),
    ("mgsv_close", ["mgsv_open", "withdrawal", "contract_charge_annual", "gmsv_rate"]),
    ("surrender_charge_rate", ["renewal_window", "schedule_rate", "attained_age_charge_cap", "charge_schedule"]),
    ("surrender_excess", ["account_value_close", "free_withdrawal_available"]),
    ("surrender_charge", ["surrender_excess", "surrender_charge_rate"]),
    ("mva_term", ["policy_year", "month_in_policy_year", "guarantee_period_years", "guarantee_period_index"]),
    ("market_value_adjustment", ["renewal_window", "locked_mva_reference_yield", "mva_ref_yield", "mva_term", "surrender_excess", "surrender_charge"]),
    ("surrender_value", ["account_value_close", "market_value_adjustment", "surrender_charge"]),
    ("surrender_benefit", ["surrender_value", "mgsv_close"]),
    ("death_benefit", ["account_value_close", "surrender_benefit"]),
]

for _name, _dependencies in _FORMULA_DEPS:
    VARIABLES.append(_variable(_name, "formula", unit="USD" if _name in {
        "account_value_open", "mgsv_open", "free_withdrawal_available",
        "account_value_before_credit", "account_value_close", "mgsv_close",
        "surrender_excess", "surrender_charge", "market_value_adjustment",
        "surrender_value", "surrender_benefit", "death_benefit",
    } else "number", required=False))

FORMULAS = [
    {
        "name": name.replace("_", " ").title(), "output_variable": name,
        "dependencies": dependencies, "function_ref": function_ref(name),
        "unit": next(item["unit"] for item in VARIABLES if item["name"] == name),
    }
    for name, dependencies in _FORMULA_DEPS
]

_SUPPORT_FORMULA_DEPS = [
    ("charge_schedule", ["guarantee_period_index"]),
    ("monthly_mortality", ["mortality_rate_annual"]),
    ("gmir_lapse_factor", ["gmir"]),
    ("base_lapse_rate", ["policy_year", "guarantee_period_years"]),
    ("dynamic_lapse_exponent", ["policy_year", "guarantee_period_years"]),
    ("dynamic_lapse_market", ["credited_rate", "market_rate", "dynamic_lapse_exponent"]),
    ("dynamic_lapse_rate", ["dynamic_lapse_market", "surrender_benefit", "account_value_close"]),
    ("mva_lapse_factor_at", ["mva_term", "renewal_window", "mva_lapse_factor"]),
    ("annual_lapse_rate", ["base_lapse_rate", "gmir_lapse_factor", "dynamic_lapse_rate", "mva_lapse_factor_at"]),
    ("monthly_lapse_rate", ["annual_lapse_rate"]),
    ("annuitization_rate", ["renewal_window"]),
    ("inforce_open", ["projection_month", "inforce_close_previous"]),
    ("maturity_rate", ["attained_age", "month_in_policy_year"]),
    ("inforce_close", ["inforce_open", "annuitization_rate", "monthly_mortality", "monthly_lapse_rate", "maturity_rate"]),
    ("death_claims", ["inforce_open", "annuitization_rate", "monthly_mortality", "death_benefit"]),
    ("surrender_claims", ["inforce_open", "annuitization_rate", "monthly_mortality", "monthly_lapse_rate", "surrender_benefit"]),
    ("annuitization_transfer", ["inforce_open", "annuitization_rate", "account_value_before_credit"]),
    ("maturity_benefit", ["inforce_open", "annuitization_rate", "monthly_mortality", "monthly_lapse_rate", "maturity_rate", "account_value_close"]),
    ("maintenance_expense", ["inforce_open", "policy_year", "annual_maintenance_expense", "expense_inflation_rate"]),
    ("premium_cash_flow", ["projection_month", "policy_month", "premium"]),
    ("acquisition_commission", ["projection_month", "policy_month", "premium", "commission_rate"]),
    ("gross_withdrawal", ["account_value_open", "withdrawal"]),
    ("free_withdrawal_allowance", ["policy_year", "month_in_policy_year", "free_wd_base", "account_value_open", "free_withdrawal_available_previous"]),
    ("chargeable_withdrawal", ["gross_withdrawal", "free_withdrawal_allowance"]),
    ("surrender_charge_on_withdrawal", ["chargeable_withdrawal", "surrender_charge_rate"]),
    ("withdrawal_mva", ["renewal_window", "locked_mva_reference_yield", "mva_ref_yield", "mva_term", "chargeable_withdrawal", "surrender_charge_on_withdrawal"]),
    ("net_withdrawal_benefit", ["gross_withdrawal", "surrender_charge_on_withdrawal", "withdrawal_mva"]),
    ("withdrawal_payment", ["inforce_open", "net_withdrawal_benefit"]),
    ("liability_outgo", ["withdrawal_payment", "death_claims", "surrender_claims", "annuitization_transfer", "maturity_benefit", "maintenance_expense", "acquisition_commission"]),
    ("net_cash_flow", ["premium_cash_flow", "liability_outgo"]),
    ("discount_factor", ["discount_rate_annual", "projection_month"]),
    ("pv_liability_outgo", ["liability_outgo", "discount_factor"]),
    ("interest_credited", ["account_value_close", "account_value_before_credit"]),
]

_USD_SUPPORT = {
    "interest_credited",
    "death_claims", "surrender_claims", "annuitization_transfer", "maturity_benefit",
    "maintenance_expense", "premium_cash_flow", "acquisition_commission",
    "gross_withdrawal", "free_withdrawal_allowance", "chargeable_withdrawal",
    "surrender_charge_on_withdrawal", "withdrawal_mva", "net_withdrawal_benefit",
    "withdrawal_payment", "liability_outgo", "net_cash_flow", "pv_liability_outgo",
}
for _name, _dependencies in _SUPPORT_FORMULA_DEPS:
    source = {"type": "formula"}
    if _name == "inforce_close":
        source["non_increasing"] = True
    VARIABLES.append(_variable(
        _name, "formula", unit="USD" if _name in _USD_SUPPORT else (
            "probability" if _name in {
                "monthly_mortality", "annual_lapse_rate", "monthly_lapse_rate",
                "annuitization_rate", "maturity_rate",
            } else "number"
        ), source=source, required=False,
    ))
    if _name == "charge_schedule":
        VARIABLES[-1]["data_type"] = "string"
        VARIABLES[-1]["unit"] = None
    FORMULAS.append({
        "name": _name.replace("_", " ").title(), "output_variable": _name,
        "dependencies": _dependencies, "function_ref": function_ref(_name),
        "unit": VARIABLES[-1]["unit"],
    })

VARIABLES.append({
    "name": "reserve", "display_name": "Illustrative reserve", "kind": "output",
    "data_type": "number", "unit": "USD", "required": False,
    "source": {
        "type": "valuation", "method": "illustrative_prospective_reserve",
        "cash_flow": "liability_outgo", "rate": "discount_rate_annual",
        "consistency_sum_of": "pv_liability_outgo",
    },
    "description": "Present value of future illustrative liability outgo.",
})

for _scenario_name in ("market_rate", "mva_ref_yield", "discount_rate_annual", "annual_maintenance_expense", "expense_inflation_rate", "commission_rate"):
    next(item for item in VARIABLES if item["name"] == _scenario_name)["allow_scenario_override"] = True

_PUBLISHED_END_OF_PERIOD = {
    "account_value_close", "mgsv_close", "surrender_benefit", "death_benefit", "reserve",
    "surrender_charge_rate",
}
PUBLISHED_OUTPUTS = [
    {"variable_name": name, "display_name": name.replace("_", " ").title(),
     "unit": next(item["unit"] for item in VARIABLES if item["name"] == name),
     "dimension": "Policy x scenario x projection month",
     "aggregation": (
         "beginning_of_period" if name == "account_value_open"
         else "end_of_period" if name in _PUBLISHED_END_OF_PERIOD else "sum"
     ),
     "description": f"Illustrative MYGA {name.replace('_', ' ')}."}
    for name in (
        "account_value_open", "account_value_close", "interest_credited", "premium_cash_flow",
        "gross_withdrawal", "surrender_charge_rate", "surrender_charge_on_withdrawal",
        "net_withdrawal_benefit", "mgsv_close", "surrender_benefit", "death_benefit",
        "death_claims", "surrender_claims", "annuitization_transfer", "maturity_benefit",
        "maintenance_expense", "liability_outgo", "net_cash_flow", "reserve",
    )
]

MODEL = {
    "name": "Five-Year MYGA Illustrative Model",
    "product_code": PRODUCT_CODE,
    "description": "Illustrative fixed deferred annuity account-value and surrender mechanics.",
    "version": {
        "version_label": "v0.1", "block_name": "Synthetic MYGA Block",
        "profile_name": "Illustrative", "basis": "Illustrative",
        "methodology": "Monthly account-value rollforward",
        "notes": "Synthetic five-year MYGA reference workflow; actuarial approval required.",
        "illustrative": True,
        "configuration": {
            "reference_only": True,
            "timing_convention": "BOM_WITHDRAWAL_BEFORE_INTEREST",
            "reference_classification": "mentoramp_simplified_development_reference",
            "generated_by": "MentorAmp Development Team",
            "reference_type": "internal_development_reference",
            "regulatory_claim": "No compliance claim; illustrative development reference only",
            "methodology": {
                "contract_mechanics": "myga_fixed_guaranteed_rollforward",
                "decrement_basis": "illustrative_best_estimate",
                "vm22_status": "illustrative_departure_not_prescribed",
                "assumption_sources": {
                    "base_lapse": "illustrative_duration_schedule",
                    "gmir_lapse_factor": "gmir_banded_factor",
                    "mva_lapse_factor": "best_estimate_factor_0_35",
                    "dynamic_lapse": "illustrative_spread_power",
                    "mortality": MORTALITY_TABLE_NAME,
                    "surrender_charge": SURRENDER_CHARGE_TABLE_NAME,
                    "surrender_charge_age_cap": SURRENDER_CHARGE_AGE_CAP_TABLE_NAME,
                },
            },
        },
    },
    "formula_group": {
        "name": "MYGA account-value rollforward", "description": "Monthly contract mechanics.",
        "lineage_state": "local", "version_label": "v1",
    },
}

MODEL_DEFINITION = {
    "model": MODEL, "variables": VARIABLES, "formulas": FORMULAS,
    "published_outputs": PUBLISHED_OUTPUTS,
}
