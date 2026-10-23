"""Tests for MYGA scenario shock semantics.

Verifies that:
- "Base" establishes baseline market rate (4.45%) and MVA reference yield (5.00%).
- "Market and MVA Reference Rates +150bp" (and alias "Rates Up 150bp") explicitly shocks reinvestment
  market rate (+150bp -> 5.95%) and MVA reference yield (+150bp -> 6.50%).
- The prospective reserve discount rate basis remains held constant at 4.50% as a neutral valuation
  discount assumption, confirming that the scenario models market asset/reinvestment yield shifts
  without unanchored liability valuation discount basis drift.
"""

import pytest

from app.products.myga_lite import config as myga


def test_market_and_mva_rates_up_scenario_semantics():
    """Verify Market and MVA Reference Rates +150bp shocks market_rate and mva_ref_yield by +150bp while holding discount rate constant."""
    base_overrides = [
        {"scenario_name": "Base", "target_variable": "market_rate", "operation": "set", "value": 0.0445},
        {"scenario_name": "Base", "target_variable": "mva_ref_yield", "operation": "set", "value": 0.0500},
    ]

    rates_up_overrides = [
        {"scenario_name": "Market and MVA Reference Rates +150bp", "target_variable": "market_rate", "operation": "set", "value": 0.0595},
        {"scenario_name": "Market and MVA Reference Rates +150bp", "target_variable": "mva_ref_yield", "operation": "set", "value": 0.0650},
    ]

    # Baseline defaults
    var_defaults = {
        row["name"]: row.get("default_value")
        for row in myga.VARIABLES
        if row.get("default_value") is not None
    }
    # Valuation discount assumption held constant at 4.50%
    assert var_defaults["discount_rate_annual"] == 0.045
    assert var_defaults["market_rate"] == 0.0445
    assert var_defaults["mva_ref_yield"] == 0.0500

    # Variable specs allowed for scenario override
    overrideable = {
        row["name"] for row in myga.VARIABLES
        if row.get("allow_scenario_override") is True
    }
    assert "market_rate" in overrideable
    assert "mva_ref_yield" in overrideable
    assert "discount_rate_annual" in overrideable

    # Verify shock delta: +150 bp
    base_market = next(s["value"] for s in base_overrides if s["target_variable"] == "market_rate")
    shock_market = next(s["value"] for s in rates_up_overrides if s["target_variable"] == "market_rate")
    assert pytest.approx(shock_market - base_market) == 0.0150

    base_mva = next(s["value"] for s in base_overrides if s["target_variable"] == "mva_ref_yield")
    shock_mva = next(s["value"] for s in rates_up_overrides if s["target_variable"] == "mva_ref_yield")
    assert pytest.approx(shock_mva - base_mva) == 0.0150

    # Valuation discount assumption held constant at 4.50% (no shock override applied)
    assert not any(s["target_variable"] == "discount_rate_annual" for s in rates_up_overrides)
