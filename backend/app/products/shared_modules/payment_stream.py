"""Payment stream calculations for SPIA products."""


def calculate_payment(policy_data: dict, month: int) -> float:
    """Calculate the payment amount for a given policy and month.

    For SPIA Lite: payment is fixed monthly amount derived from premium.
    Default: annual payment = premium / 10, monthly = annual / 12.

    Args:
        policy_data: Policy data dict with 'premium' and optionally 'payment_amount'
        month: Projection month (1-based)

    Returns:
        Payment amount for the month
    """
    # If policy specifies exact payment, use it
    if "payment_amount" in policy_data:
        raw = policy_data["payment_amount"]
        if raw is not None and str(raw).strip():
            return float(raw)

    # Otherwise derive from premium
    premium = _get_premium(policy_data)
    annual_payment = premium / 10.0  # Simple assumption: 10% payout rate
    return annual_payment / 12.0


def calculate_payment_stream(policy_data: dict, projection_months: int) -> list[float]:
    """Generate a list of payments for each month of the projection.

    Args:
        policy_data: Policy data dict
        projection_months: Total number of months to project

    Returns:
        List of payment amounts, one per month
    """
    return [calculate_payment(policy_data, m) for m in range(1, projection_months + 1)]


def _get_premium(policy_data: dict) -> float:
    """Extract premium from policy data, defaulting to 100000."""
    raw = policy_data.get("premium", 100000)
    if raw is None or str(raw).strip() == "":
        return 100000.0
    try:
        return float(raw)
    except (ValueError, TypeError):
        return 100000.0
