"""Indexed schema validation uses the generic Data Manager registry path."""
import pytest
from app.db.models.data_manager import ImportSession
from app.products.shared_modules.synthetic import block_rows
from app.services.data_manager_service import _validate_inforce
from app.products.registry import register_all_products


@pytest.mark.parametrize("code", ["FIA", "RILA"])
def test_indexed_block_schema_and_conservation(code):
    rows = block_rows(code)
    issues = []
    _validate_inforce(ImportSession(options={"valuation_date": "2025-12-31"}), rows, issues)
    assert not [issue for issue in issues if issue["severity"] == "error"]
    rows[0]["account_value"] += 1
    issues = []
    _validate_inforce(ImportSession(options={"valuation_date": "2025-12-31"}), rows, issues)
    assert any(issue["code"] == "INVALID_INDEXED_STATE" for issue in issues)


def test_registration_is_idempotent():
    assert register_all_products() == ["SPIA", "MYGA", "FIA", "RILA"]
    assert register_all_products() == ["SPIA", "MYGA", "FIA", "RILA"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_bad_terms_report_issues_without_crashing_import(bad):
    rows = block_rows("RILA", 1)
    rows[0]["segment_1_term_months"] = bad
    issues = []
    _validate_inforce(ImportSession(options={"valuation_date": "2025-12-31"}), rows, issues)
    assert any(issue["severity"] == "error" for issue in issues)
