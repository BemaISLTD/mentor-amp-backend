"""Architectural test proving generic Data Manager product decoupling.

Proves:
1. Adding a new product (e.g. FIA, RILA) does not require modifying generic Data Manager code.
2. Inforce column aliases (e.g. guarantee_term_years -> guarantee_period_years) map seamlessly.
"""

import math
from typing import Any

import pytest

from app.products.inforce_registry import (
    COMMON_INFORCE_FIELDS,
    ProductInforceDefinition,
    clear_product_inforce_registry,
    get_product_inforce_definition,
    register_inforce_product,
)
from app.services.data_manager_service import _validate_inforce
from app.db.models.data_manager import ImportSession


def test_generic_product_can_be_registered_without_modifying_data_manager():
    """Verify that a newly registered product (e.g. FIA) validates via the generic registry."""
    # Define a new synthetic product schema
    fia_definition = ProductInforceDefinition(
        product_code="FIA",
        fields={
            "index_strategy": "string",
            "participation_rate": "number",
            "cap_rate": "number",
        },
        aliases={
            "part_rate": "participation_rate",
        },
        amount_fields=("premium",),
        negative_allowed=False,
        validate_row=lambda num, item, issues: (
            issues.append({
                "severity": "error",
                "code": "CAP_RATE_EXCEEDED",
                "message": "cap_rate cannot exceed 100%",
                "row_number": num,
                "column_name": "cap_rate",
                "value": str(item.get("cap_rate")),
            })
            if isinstance(item.get("cap_rate"), (int, float)) and item["cap_rate"] > 1.0
            else None
        ),
    )

    try:
        # Register FIA without touching data_manager_service.py
        register_inforce_product(fia_definition)
        assert get_product_inforce_definition("FIA") is not None

        # Validate an FIA inforce dataset
        session = ImportSession(options={"valuation_date": "2026-12-31"})
        rows = [
            {
                "policy_id": "FIA-001",
                "product_type": "FIA",
                "issue_date": "2026-01-01",
                "issue_age": 55,
                "gender": "M",
                "premium": 100000.0,
                "index_strategy": "SP500_POINT_TO_POINT",
                "part_rate": 0.80,  # alias for participation_rate
                "cap_rate": 0.12,
            }
        ]
        issues = []
        _validate_inforce(session, rows, issues)

        # Alias must be mapped to canonical column
        assert rows[0]["participation_rate"] == 0.80
        # No errors for valid FIA row
        errors = [i for i in issues if i["severity"] == "error"]
        assert errors == [], f"Unexpected validation errors: {errors}"

        # Test that FIA product-specific validator hook triggers properly
        invalid_rows = [
            {
                "policy_id": "FIA-002",
                "product_type": "FIA",
                "issue_date": "2026-01-01",
                "issue_age": 55,
                "gender": "F",
                "premium": 50000.0,
                "index_strategy": "SP500_POINT_TO_POINT",
                "participation_rate": 1.0,
                "cap_rate": 1.50,  # exceeds 1.0 -> triggers CAP_RATE_EXCEEDED
            }
        ]
        invalid_issues = []
        _validate_inforce(session, invalid_rows, invalid_issues)
        assert any(i["code"] == "CAP_RATE_EXCEEDED" for i in invalid_issues)

    finally:
        clear_product_inforce_registry()


def test_myga_column_aliases_harmonization():
    """Verify MYGA recognizes both reference package columns and internal engine columns."""
    session = ImportSession(options={"valuation_date": "2026-12-31"})
    rows = [
        {
            "policy_id": "MYGA-ALIAS-01",
            "product_type": "MYGA",
            "issue_date": "2026-01-01",
            "issue_age": 60,
            "gender": "M",
            "premium": 100000.0,
            "guarantee_term_years": 5,  # alias for guarantee_period_years
            "credited_rate": 0.045,  # alias for declared_rate
            "gmir": 0.0025,
            "mgsv_rate": 0.02,  # alias for gmsv_rate
            "mva_ref_yield_at_issue": 0.05,
            "account_value": 100000.0,
            "mgsv_value": 87500.0,
            "free_wd_base": 10000.0,
        }
    ]
    issues = []
    _validate_inforce(session, rows, issues)

    errors = [i for i in issues if i["severity"] == "error"]
    assert errors == [], f"Unexpected errors with aliased columns: {errors}"
    assert rows[0]["guarantee_period_years"] == 5
    assert rows[0]["declared_rate"] == 0.045
    assert rows[0]["gmsv_rate"] == 0.02
