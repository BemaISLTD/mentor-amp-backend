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
    # Pruned aliases from canonical normalized row
    assert "guarantee_term_years" not in rows[0]
    assert "credited_rate" not in rows[0]
    assert "mgsv_rate" not in rows[0]


def test_dm_myga_01_and_02_canonical_and_alias_produce_identical_fingerprint():
    """DM-MYGA-01 & DM-MYGA-02: Upload with canonical names vs aliases produces identical normalized rows & fingerprint."""
    from app.services.data_manager_service import _canonical_fingerprint

    session = ImportSession(options={"valuation_date": "2026-12-31"})

    # DM-MYGA-01: Canonical names
    canonical_rows = [
        {
            "policy_id": "MYGA-0001",
            "product_type": "MYGA",
            "issue_date": "2026-12-31",
            "issue_age": 60,
            "gender": "M",
            "premium": 100000.0,
            "guarantee_period_years": 5,
            "declared_rate": 0.0445,
            "gmir": 0.0025,
            "gmsv_rate": 0.028,
            "mva_ref_yield_at_issue": 0.05,
            "account_value": 100000.0,
            "mgsv_value": 87500.0,
            "free_wd_base": 100000.0,
        }
    ]
    issues_canon = []
    _validate_inforce(session, canonical_rows, issues_canon)
    assert not [i for i in issues_canon if i["severity"] == "error"]
    fp_canon, scheme_canon = _canonical_fingerprint("liability_inforce", canonical_rows)

    # DM-MYGA-02: Economically identical file using aliases
    alias_rows = [
        {
            "policy_id": "MYGA-0001",
            "product_type": "MYGA",
            "issue_date": "2026-12-31",
            "issue_age": 60,
            "gender": "M",
            "premium": 100000.0,
            "guarantee_term_years": 5,       # alias for guarantee_period_years
            "credited_rate": 0.0445,         # alias for declared_rate
            "gmir": 0.0025,
            "mgsv_rate": 0.028,              # alias for gmsv_rate
            "mva_ref_yield_at_issue": 0.05,
            "account_value": 100000.0,
            "mgsv_value": 87500.0,
            "free_wd_base": 100000.0,
        }
    ]
    issues_alias = []
    _validate_inforce(session, alias_rows, issues_alias)
    assert not [i for i in issues_alias if i["severity"] == "error"]
    fp_alias, scheme_alias = _canonical_fingerprint("liability_inforce", alias_rows)

    # Both must yield identical normalized rows and identical fingerprint
    assert canonical_rows == alias_rows
    assert fp_canon == fp_alias
    assert scheme_canon == scheme_alias


def test_dm_myga_03_alias_and_canonical_both_present_same_value():
    """DM-MYGA-03: Alias and canonical field both present with same value normalized once."""
    from app.services.data_manager_service import _canonical_fingerprint

    session = ImportSession(options={"valuation_date": "2026-12-31"})
    rows = [
        {
            "policy_id": "MYGA-0001",
            "product_type": "MYGA",
            "issue_date": "2026-12-31",
            "issue_age": 60,
            "gender": "M",
            "premium": 100000.0,
            "guarantee_period_years": 5,
            "guarantee_term_years": 5,       # both present with identical value
            "declared_rate": 0.0445,
            "credited_rate": 0.0445,         # both present with identical value
            "gmir": 0.0025,
            "gmsv_rate": 0.028,
            "mgsv_rate": 0.028,              # both present with identical value
            "mva_ref_yield_at_issue": 0.05,
            "account_value": 100000.0,
            "mgsv_value": 87500.0,
            "free_wd_base": 100000.0,
        }
    ]
    issues = []
    _validate_inforce(session, rows, issues)
    assert not [i for i in issues if i["severity"] == "error"]

    # Canonical columns present once, aliases pruned
    assert rows[0]["declared_rate"] == 0.0445
    assert "credited_rate" not in rows[0]
    assert rows[0]["guarantee_period_years"] == 5
    assert "guarantee_term_years" not in rows[0]


def test_dm_myga_04_alias_and_canonical_disagree_validation_error():
    """DM-MYGA-04: Alias and canonical field disagree produces ALIAS_CONFLICT error."""
    session = ImportSession(options={"valuation_date": "2026-12-31"})
    rows = [
        {
            "policy_id": "MYGA-0001",
            "product_type": "MYGA",
            "issue_date": "2026-12-31",
            "issue_age": 60,
            "gender": "M",
            "premium": 100000.0,
            "guarantee_period_years": 5,
            "declared_rate": 0.045,          # disagrees with credited_rate
            "credited_rate": 0.050,          # disagrees
            "gmir": 0.0025,
            "gmsv_rate": 0.028,
            "mva_ref_yield_at_issue": 0.05,
            "account_value": 100000.0,
            "mgsv_value": 87500.0,
            "free_wd_base": 100000.0,
        }
    ]
    issues = []
    _validate_inforce(session, rows, issues)
    errors = [i for i in issues if i["severity"] == "error"]
    assert any(i["code"] == "ALIAS_CONFLICT" for i in errors)


def test_schema_01_no_schema_version_uses_deliberate_default():
    """SCHEMA-01: When no schema version is supplied, deliberate default v1 is used."""
    defn = get_product_inforce_definition("MYGA")
    assert defn is not None
    assert defn.schema_version == "v1"

    session = ImportSession(options={"valuation_date": "2026-12-31"})
    rows = [
        {
            "policy_id": "MYGA-0001",
            "product_type": "MYGA",
            "issue_date": "2026-12-31",
            "issue_age": 60,
            "gender": "M",
            "premium": 100000.0,
            "guarantee_period_years": 5,
            "declared_rate": 0.045,
            "gmir": 0.025,
            "gmsv_rate": 0.028,
            "mva_ref_yield_at_issue": 0.05,
            "account_value": 100000.0,
            "mgsv_value": 87500.0,
            "free_wd_base": 100000.0,
        }
    ]
    issues = []
    _validate_inforce(session, rows, issues)
    errors = [i for i in issues if i["severity"] == "error"]
    assert errors == []


def test_schema_02_explicit_v1_uses_v1():
    """SCHEMA-02: Explicit v1 schema version is accepted and resolves to registered v1 definition."""
    defn = get_product_inforce_definition("MYGA", "v1")
    assert defn is not None
    assert defn.schema_version == "v1"

    session = ImportSession(options={"valuation_date": "2026-12-31", "schema_version": "v1"})
    rows = [
        {
            "policy_id": "MYGA-0001",
            "product_type": "MYGA",
            "issue_date": "2026-12-31",
            "issue_age": 60,
            "gender": "M",
            "premium": 100000.0,
            "guarantee_period_years": 5,
            "declared_rate": 0.045,
            "gmir": 0.025,
            "gmsv_rate": 0.028,
            "mva_ref_yield_at_issue": 0.05,
            "account_value": 100000.0,
            "mgsv_value": 87500.0,
            "free_wd_base": 100000.0,
        }
    ]
    issues = []
    _validate_inforce(session, rows, issues)
    errors = [i for i in issues if i["severity"] == "error"]
    assert errors == []


def test_schema_03_explicit_unknown_v99_fails():
    """SCHEMA-03: Explicit unsupported schema version v99 fails with INFORCE_SCHEMA_VERSION_NOT_SUPPORTED."""
    # Direct lookup returns None rather than silently falling back to v1
    assert get_product_inforce_definition("MYGA", "v99") is None

    session = ImportSession(options={"valuation_date": "2026-12-31", "schema_version": "v99"})
    rows = [
        {
            "policy_id": "MYGA-0001",
            "product_type": "MYGA",
            "issue_date": "2026-12-31",
            "issue_age": 60,
            "gender": "M",
            "premium": 100000.0,
            "guarantee_period_years": 5,
            "declared_rate": 0.045,
            "gmir": 0.025,
            "gmsv_rate": 0.028,
            "mva_ref_yield_at_issue": 0.05,
            "account_value": 100000.0,
            "mgsv_value": 87500.0,
            "free_wd_base": 100000.0,
        }
    ]
    issues = []
    _validate_inforce(session, rows, issues)
    errors = [i for i in issues if i["severity"] == "error"]
    assert any(i["code"] == "INFORCE_SCHEMA_VERSION_NOT_SUPPORTED" for i in errors)
    assert not any(i["code"] == "INVALID_PRODUCT_TYPE" for i in errors)


