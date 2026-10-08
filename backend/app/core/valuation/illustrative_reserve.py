"""Illustrative prospective reserve (M1). ILLUSTRATIVE — NOT ACTUARIALLY APPROVED.

reserve_H = 0
reserve_t = (reserve_{t+1} + cash_flow_{t+1}) × (1 + i)^(−1/12),   t = H−1 … 0
          = Σ_{k=t+1..H} cash_flow_k × (1 + i)^(−(k−t)/12)

Cash flows are paid at the end of each month (the same timing as the projection).
Contract reference: docs/build/02_BACKEND_FRONTEND_CONTRACT.md §F.5.
"""

METHOD = "illustrative_prospective_reserve"
EXPRESSION_TEXT = "reserve_t = Σ_{k>t} expected_payment_k × (1 + i)^(−(k−t)/12)"


def prospective_reserve(cash_flows: list[float], annual_rate: float) -> list[float]:
    """Return reserves for months 0..H given cash flows for months 1..H (list index 0 = month 1)."""
    horizon = len(cash_flows)
    monthly_discount = (1.0 + annual_rate) ** (-1.0 / 12.0)
    reserves = [0.0] * (horizon + 1)
    for t in range(horizon - 1, -1, -1):
        reserves[t] = (reserves[t + 1] + cash_flows[t]) * monthly_discount
    return reserves
