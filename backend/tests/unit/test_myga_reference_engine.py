"""Unit and reconciliation tests for the native MYGA reference-model anchor and sample data."""

import csv
import json
import math
from pathlib import Path

import pytest

from app.products.myga_reference.metadata import LIFELIB_MYGA_REFERENCE

BACKEND_DIR = Path(__file__).resolve().parents[2]
FIXTURE_PATH = BACKEND_DIR / "tests" / "fixtures" / "myga_reference_lifelib_v0_17_2.json"
SAMPLES_DIR = BACKEND_DIR.parent / "samples" / "myga"


@pytest.fixture(scope="module")
def fixture_data() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_myga_fixture_schema_and_metadata():
    """Verify the pinned reference fixture schema and immutable metadata boundary."""
    assert FIXTURE_PATH.is_file()
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert fixture["fixture_schema"] == "mentoramp.reference_fixture/v1"
    assert fixture["source"]["model"] == "MYGA_US_S"
    assert fixture["source"]["release"] == "v0.17.2"

    config = LIFELIB_MYGA_REFERENCE.as_configuration()
    assert config["schema_version"] == "mentoramp.reference_model/v1"
    assert config["runtime_required"] is False
    assert config["production_dependency"] is False
    assert config["actuarial_status"] == "illustrative_not_approved"
    assert "account_value_eom" in config["compared_outputs"]
    assert "cash_surrender_value_eom" in config["compared_outputs"]


def test_myga_anchor_account_value_rollforward_precision(fixture_data):
    """Verify that native monthly rollforward arithmetic reconciles to the pinned fixture to <= 1e-12."""
    anchor = fixture_data["anchor_case"]
    premium = float(anchor["premium"])
    annual_rate = float(anchor["credited_rate"])
    monthly_rate = (1.0 + annual_rate) ** (1.0 / 12.0) - 1.0

    assert math.isclose(monthly_rate, anchor["monthly_credited_rate"], rel_tol=1e-12)

    av = premium
    for row in anchor["rows"]:
        m = row["month"]
        expected_av_bom = row["account_value_bom"]
        expected_interest = row["interest_credited"]
        expected_av_eom = row["account_value_eom"]
        wd = row["partial_withdrawal"]

        # 1. BOM balance check
        assert math.isclose(av, expected_av_bom, abs_tol=1e-12)

        # 2. Monthly interest crediting check
        calculated_interest = av * monthly_rate
        assert math.isclose(calculated_interest, expected_interest, abs_tol=1e-12)

        # 3. EOM balance after withdrawal check
        av = av + calculated_interest - wd
        assert math.isclose(av, expected_av_eom, abs_tol=1e-12)

    # 4. Diagnostics check at Month 60 (5 years)
    diag = anchor["full_projection_diagnostics"]
    assert math.isclose(av, diag["ending_account_value"], abs_tol=1e-12)
    assert diag["projection_months"] == 60


def test_myga_free_withdrawal_allowance_and_surrender_charges(fixture_data):
    """Verify free partial withdrawal thresholding and surrender charge mechanics."""
    anchor = fixture_data["anchor_case"]
    m12_row = [r for r in anchor["rows"] if r["month"] == 12][0]

    # Month 12: $4,000 withdrawal is within the 10% ($10,000) limit -> 0 charge
    assert m12_row["partial_withdrawal"] == 4000.0
    assert m12_row["chargeable_withdrawal"] == 0.0
    assert m12_row["surrender_charge_on_withdrawal"] == 0.0

    # Excess case: Month 24 has a $20,000 withdrawal
    excess = fixture_data["excess_withdrawal_case"]
    m24_row = [r for r in excess["rows"] if r["month"] == 24][0]

    assert m24_row["partial_withdrawal"] == 20000.0
    # In Year 2, prior anniversary AV was $104,500.00; free limit is 10% = $10,450.00
    expected_free = 104500.0 * 0.10
    expected_chargeable = 20000.0 - expected_free
    assert math.isclose(m24_row["chargeable_withdrawal"], expected_chargeable, abs_tol=1e-12)

    # Year 2 surrender charge rate is 6%
    expected_sc = expected_chargeable * 0.06
    assert math.isclose(m24_row["surrender_charge_on_withdrawal"], expected_sc, abs_tol=1e-12)


def test_myga_mgsv_nonforfeiture_statutory_floor(fixture_data):
    """Verify NAIC Model #805 MGSV accumulation and CSV >= MGSV floor protection."""
    anchor = fixture_data["anchor_case"]
    premium = float(anchor["premium"])
    mgsv_load = float(anchor["mgsv_premium_load"])
    annual_mgsv_rate = float(anchor["mgsv_rate"])
    m_mgsv_rate = (1.0 + annual_mgsv_rate) ** (1.0 / 12.0) - 1.0

    mgsv = premium * mgsv_load
    for row in anchor["rows"]:
        interest = mgsv * m_mgsv_rate
        wd = row["partial_withdrawal"]
        mgsv = mgsv + interest - wd

        assert math.isclose(mgsv, row["mgsv_eom"], abs_tol=1e-12)
        # Cash Surrender Value must never be lower than the statutory MGSV floor
        assert row["cash_surrender_value_eom"] >= row["mgsv_eom"] - 1e-12


def test_myga_sample_inforce_and_schedules_integrity():
    """Verify that samples/myga/ files exist, conform to schema, and contain valid actuarial data."""
    inforce_csv = SAMPLES_DIR / "synthetic_myga_inforce.csv"
    sc_csv = SAMPLES_DIR / "myga_surrender_charge.csv"
    cap_csv = SAMPLES_DIR / "myga_sc_age_cap.csv"

    assert inforce_csv.is_file()
    assert sc_csv.is_file()
    assert cap_csv.is_file()

    # Verify surrender charge schedules
    with sc_csv.open(encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
    schedules = {row["schedule_id"] for row in reader}
    assert {"SC_3YR_STANDARD", "SC_5YR_STANDARD", "SC_7YR_STANDARD", "SC_10YR_STANDARD"} <= schedules

    # Verify each schedule monotonically steps down to 0
    by_sched: dict[str, list[dict]] = {}
    for r in reader:
        by_sched.setdefault(r["schedule_id"], []).append(r)
    for sched_id, rows in by_sched.items():
        sorted_rows = sorted(rows, key=lambda x: int(x["policy_year"]))
        assert float(sorted_rows[-1]["surrender_charge_rate"]) == 0.0

    # Verify inforce policies
    with inforce_csv.open(encoding="utf-8") as f:
        inforce = list(csv.DictReader(f))

    assert len(inforce) == 25
    anchor_policy = inforce[0]
    assert anchor_policy["policy_id"] == "MYGA-0001"
    assert anchor_policy["product_type"] == "MYGA"
    assert float(anchor_policy["premium"]) == 100000.0
    assert int(anchor_policy["guarantee_term_years"]) == 5
    assert float(anchor_policy["credited_rate"]) == 0.045
    assert float(anchor_policy["free_withdrawal_pct"]) == 0.10
    assert anchor_policy["surrender_charge_schedule_id"] == "SC_5YR_STANDARD"

    # All policies reference valid schedules
    for p in inforce:
        assert p["surrender_charge_schedule_id"] in schedules
        assert 20000.0 <= float(p["premium"]) <= 1000000.0
        assert 0.02 <= float(p["credited_rate"]) <= 0.08
