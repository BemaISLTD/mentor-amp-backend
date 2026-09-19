"""Present value calculations."""


def npv(cash_flow: float, rate: float, period: int) -> float:
    """Calculate net present value of a single cash flow.

    Args:
        cash_flow: The future cash amount
        rate: Annual discount rate (e.g., 0.05 for 5%)
        period: Number of years from now

    Returns:
        Present value of the cash flow
    """
    if period < 0:
        return cash_flow
    monthly_rate = rate / 12.0
    months = period
    return cash_flow / ((1.0 + monthly_rate) ** months)


def present_value(cash_flows: list[float], discount_rate: float) -> float:
    """Calculate the total present value of a series of cash flows.

    Args:
        cash_flows: List of cash amounts, one per month (index 0 = month 1)
        discount_rate: Annual discount rate (e.g., 0.05 for 5%)

    Returns:
        Total present value
    """
    total = 0.0
    for i, cf in enumerate(cash_flows):
        month = i + 1
        total += npv(cf, discount_rate, month)
    return total
