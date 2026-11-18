from copy import deepcopy
import csv
from pathlib import Path

import pytest

from app.db.models.data_manager import ImportSession
from app.products.inforce_registry import get_product_inforce_definition
from app.services.data_manager_service import _validate_inforce


COMMON = {
    "policy_id": "P-1",
    "product_type": "",
    "issue_date": "2025-01-01",
    "issue_age": 45,
    "gender": "F",
    "premium": 1_200.0,
}


def validate(row):
    issues = []
    _validate_inforce(
        ImportSession(options={"valuation_date": "2025-12-31"}),
        [deepcopy(row)],
        issues,
    )
    return issues


def errors(row):
    return [issue for issue in validate(row) if issue["severity"] == "error"]


def idi_row():
    return {
        **COMMON,
        "product_type": "IDI",
        "claim_status": "active",
        "occupation_class": "CLASS_1",
        "monthly_benefit": 5_000.0,
        "annual_premium": 1_200.0,
        "elimination_months": 3,
        "max_benefit_months": 60,
        "claim_months_elapsed": 0,
        "expense_per_policy_monthly": 10.0,
        "waive_premium_on_disability": 1,
        "discount_rate": 0.04,
    }


def va_row():
    return {
        **COMMON,
        "product_type": "VA",
        "account_value": 100_000.0,
        "eq_account_value": 60_000.0,
        "bond_account_value": 30_000.0,
        "fixed_account_value": 10_000.0,
        "eq_fund_code": "SP500_EQ",
        "bond_fund_code": "US_AGG_BOND",
        "fixed_credited_rate": 0.03,
        "me_fee_rate": 0.012,
        "admin_fee_rate": 0.002,
        "rider_fee_rate": 0.01,
        "fee_conversion_method": "simple_12th",
        "rider_type": "GMWB",
        "benefit_base": 100_000.0,
        "gmdb_base": 0.0,
        "gmwb_remaining_balance": 100_000.0,
        "rollup_rate": 0.0,
        "gwb_pct": 0.10,
        "withdrawal_amount_monthly": 500.0,
        "withdrawal_mode": "pro_rata",
        "surrender_charge_rate": 0.05,
        "discount_rate": 0.04,
        "rider_fee_basis": "benefit_base",
    }


def prt_row():
    return {
        **COMMON,
        "product_type": "PRT",
        "participant_id": "PART-1",
        "plan_group_id": "PLAN-1",
        "participant_status": "in_pay",
        "monthly_benefit": 1_000.0,
        "deferral_months": 0,
        "form_of_annuity": "life_only",
        "certain_months": 0,
        "joint_survivor_pct": 0.0,
        "cola_rate": 0.02,
        "cola_cap": 0.03,
        "expense_pct_of_benefit": 0.01,
        "discount_rate": 0.04,
    }


def life_row():
    return {
        **COMMON,
        "product_type": "LIFE",
        "life_plan_type": "TERM",
        "underwriting_class": "PREFERRED",
        "smoker_status": "NONSMOKER",
        "policy_status": "ACTIVE",
        "coverage_id": "COV-1",
        "face_amount": 500_000.0,
        "death_benefit_option": "LEVEL",
        "coverage_term_years": 20,
        "annualized_premium": 1_200.0,
        "modal_premium": 100.0,
        "premium_mode": "MONTHLY",
        "account_value": 0.0,
        "mortality_table_ref": "SYNTHETIC-LIFE-MORTALITY-V1",
        "lapse_table_ref": "SYNTHETIC-LIFE-LAPSE-V1",
        "valuation_basis_hint": "STATUTORY",
        "expected_cash_flow_currency": "USD",
    }


@pytest.mark.parametrize("code", ["IDI", "VA", "PRT", "LIFE"])
def test_phase7_products_have_canonical_inforce_definitions(code):
    assert get_product_inforce_definition(code) is not None


@pytest.mark.parametrize("factory", [idi_row, va_row, prt_row, life_row])
def test_phase7_valid_rows_pass_structural_validation(factory):
    assert errors(factory()) == []


def test_idi_invalid_status_and_active_claim_duration_are_rejected():
    row = idi_row()
    row["claim_status"] = "unknown"
    assert any(issue["code"] == "INVALID_IDI_STATE" for issue in errors(row))
    row["claim_status"] = "active"
    row["claim_months_elapsed"] = 2
    assert any(
        issue["code"] == "INVALID_IDI_STATE" and issue["column_name"] == "claim_months_elapsed"
        for issue in errors(row)
    )


def test_va_nonconserving_subaccounts_and_missing_gmwb_balance_are_rejected():
    row = va_row()
    row["eq_account_value"] += 1.0
    del row["gmwb_remaining_balance"]
    found = errors(row)
    assert sum(issue["code"] == "INVALID_VA_STATE" for issue in found) >= 2


def test_prt_joint_survivor_requires_valid_spouse_fields():
    row = prt_row()
    row.update({
        "form_of_annuity": "joint_survivor",
        "joint_survivor_pct": 0.5,
    })
    found = errors(row)
    assert {issue["column_name"] for issue in found if issue["code"] == "INVALID_PRT_STATE"} >= {
        "spouse_age", "spouse_gender"
    }
    row.update({"spouse_age": 62, "spouse_gender": "M"})
    assert errors(row) == []


def test_life_preserves_premium_frequency_fidelity():
    assert errors(life_row()) == []
    row = life_row()
    row["modal_premium"] = 1_200.0
    found = errors(row)
    assert any(issue["code"] == "INVALID_LIFE_PREMIUM_FREQUENCY" for issue in found)


def test_life_multi_coverage_and_riders_validate_relationships():
    row = life_row()
    row.update({
        "coverage_2_id": "COV-2",
        "coverage_2_plan_type": "TERM",
        "coverage_2_face_amount": 100_000.0,
        "rider_1_code": "ADB",
        "rider_1_face_amount": 50_000.0,
        "rider_1_premium": 5.0,
        "rider_2_code": "WOP",
        "rider_2_face_amount": 1_200.0,
        "rider_2_premium": 2.0,
    })
    assert errors(row) == []
    row["coverage_2_id"] = "COV-1"
    assert any(
        issue["code"] == "INVALID_LIFE_STATE" and issue["column_name"] == "coverage_2_id"
        for issue in errors(row)
    )


def test_term_requires_coverage_term_and_ul_cash_value_cannot_exceed_account_value():
    term = life_row()
    del term["coverage_term_years"]
    assert any(issue["column_name"] == "coverage_term_years" for issue in errors(term))
    ul = life_row()
    ul["life_plan_type"] = "UL"
    ul.pop("coverage_term_years")
    ul["account_value"] = 10_000.0
    ul["cash_surrender_value"] = 11_000.0
    assert any(issue["column_name"] == "cash_surrender_value" for issue in errors(ul))


def test_life_semantic_aliases_are_accepted_but_annual_premium_is_not_modal_premium():
    row = life_row()
    row["annual_premium"] = row.pop("annualized_premium")
    row["sex"] = row.pop("gender")
    row["smoking_status"] = row.pop("smoker_status")
    row["risk_class"] = row.pop("underwriting_class")
    row["sum_assured"] = row.pop("face_amount")
    assert errors(row) == []
    definition = get_product_inforce_definition("LIFE")
    assert definition.aliases.get("annual_premium") == "annualized_premium"
    assert "modal_premium" not in definition.aliases.values()


@pytest.mark.parametrize("product", ["idi", "va", "prt", "life"])
def test_phase7_synthetic_sample_files_pass_canonical_validation(product):
    root = Path(__file__).resolve().parents[3]
    path = root / "samples" / product / f"synthetic_{product}_inforce.csv"
    with path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    issues = []
    _validate_inforce(
        ImportSession(options={"valuation_date": "2026-12-31"}), rows, issues
    )
    assert not [issue for issue in issues if issue["severity"] == "error"], issues
