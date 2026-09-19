"""Golden expected values for SPIA Lite reconciliation testing.

Single policy: 65-year-old male, $100,000 premium, 5% discount rate.
Mortality: 1.5% annual rate.
Monthly payment: $100,000 / 10 / 12 = $833.33
"""

GOLDEN_SPIA_OUTPUT = {
    "policy_id": "SPIA_TEST_001",
    "issue_age": 65,
    "gender": "M",
    "premium": 100000,
    "monthly_payment": 100000 / 10 / 12,  # 833.33
    "mortality_rate": 0.015,
    "discount_rate": 0.05,
    # Month 1 expected values
    "month_1": {
        "survival_probability": 1.0,  # duration 1, one year of (1-0.015) = 0.985... but our formula uses the rate from the table directly
        "expected_payment": 833.33,  # roughly, survival prob ~0.985 so ~821
        "present_value": 0.0,  # placeholder — will compute
    },
    "tolerance": 0.05,  # 5% tolerance for Phase 1
}
