import csv
import copy
import json
import math
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.products.myga_reference.metadata import (
    ENGINE_CROSSCHECK_TOLERANCE,
    INTERNAL_ARITHMETIC_PRECISION,
    LIFELIB_MYGA_REFERENCE,
    MYGA_DEVELOPMENT_REFERENCE,
)
from tools.myga_reference_crosscheck.myga_crosscheck import (
    REQUIRED_FIELDS,
    prevalidate_expected_fixture,
    reconcile_ledger,
    validate_projection_month,
)

FIXTURE_PATH = BACKEND_DIR / "tests" / "fixtures" / "myga_reference_simplified_v1.json"
SAMPLES_DIR = BACKEND_DIR.parent / "samples" / "myga"


@pytest.fixture(scope="module")
def fixture_data() -> dict:
    assert FIXTURE_PATH.is_file(), f"Fixture missing at {FIXTURE_PATH}"
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_myga_fixture_schema_and_metadata():
    """Verify the pinned reference fixture schema and immutable metadata boundary."""
    assert FIXTURE_PATH.is_file()
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert fixture["fixture_schema"] == "mentoramp.reference_fixture/v1"
    assert fixture["reference_classification"] == "mentoramp_simplified_development_reference"
    assert fixture["mva_status"] == "excluded_not_modeled"
    assert "NOT an independent LifeLib oracle run" in fixture["provenance_statement"]
    assert fixture["source"]["provider"] == "MentorAmp Development Team"
    assert fixture["source"]["generated_by"] == "MentorAmp Development Team"
    assert fixture["source"]["reference_type"] == "internal_development_reference"
    assert "commit" not in fixture["source"]

    config = MYGA_DEVELOPMENT_REFERENCE.as_configuration()
    assert config["schema_version"] == "mentoramp.reference_model/v1"
    assert config["classification"] == "mentoramp_simplified_development_reference"
    assert config["runtime_required"] is False
    assert config["production_dependency"] is False
    assert config["actuarial_status"] == "illustrative_not_approved"
    assert config["source"]["commit"] is None
    assert config["source"]["generated_by"] == "MentorAmp Development Team"
    assert "account_value_eom" in config["compared_outputs"]
    assert "net_withdrawal_benefit" in config["compared_outputs"]
    assert "net_insurer_cash_flow" in config["compared_outputs"]

    # Verify backwards compatibility alias
    assert LIFELIB_MYGA_REFERENCE == MYGA_DEVELOPMENT_REFERENCE


def test_myga_anchor_account_value_rollforward_precision(fixture_data):
    """Verify that native monthly rollforward arithmetic reconciles to the pinned fixture to <= 1e-12."""
    anchor = fixture_data["anchor_case"]
    premium = float(anchor["premium"])
    annual_rate = float(anchor["credited_rate"])
    monthly_rate = (1.0 + annual_rate) ** (1.0 / 12.0) - 1.0

    assert math.isclose(monthly_rate, anchor["monthly_credited_rate"], rel_tol=INTERNAL_ARITHMETIC_PRECISION)

    av = premium
    for row in anchor["rows"]:
        expected_av_bom = row["account_value_bom"]
        expected_interest = row["credited_interest"]
        expected_av_eom = row["account_value_eom"]
        gross_wd = row["gross_withdrawal"]

        # 1. BOM balance check
        assert math.isclose(av, expected_av_bom, abs_tol=INTERNAL_ARITHMETIC_PRECISION)

        # 2. Monthly interest crediting check
        calculated_interest = av * monthly_rate
        assert math.isclose(calculated_interest, expected_interest, abs_tol=INTERNAL_ARITHMETIC_PRECISION)

        # 3. EOM balance after gross withdrawal check
        av = av + calculated_interest - gross_wd
        assert math.isclose(av, expected_av_eom, abs_tol=INTERNAL_ARITHMETIC_PRECISION)

    # 4. Diagnostics check at Month 60 (5 years)
    diag = anchor["full_projection_diagnostics"]
    assert math.isclose(av, diag["ending_account_value"], abs_tol=INTERNAL_ARITHMETIC_PRECISION)
    assert diag["projection_months"] == 60


def test_myga_free_withdrawal_allowance_and_surrender_charges(fixture_data):
    """Verify free partial withdrawal thresholding, surrender charge, net benefit, and insurer cash flow."""
    anchor = fixture_data["anchor_case"]
    m12_row = [r for r in anchor["rows"] if r["month"] == 12][0]

    # Month 12: $4,000 withdrawal is within the 10% ($10,000) limit -> 0 charge
    assert m12_row["gross_withdrawal"] == 4000.0
    assert m12_row["surrender_charge"] == 0.0
    assert m12_row["net_withdrawal_benefit"] == 4000.0
    assert m12_row["maintenance_expense"] == 5.0
    # Insurer cash flow = -net_benefit - expense = -4000 - 5 = -4005
    assert m12_row["net_insurer_cash_flow"] == -4005.0

    # Excess case: Month 24 has a $20,000 gross withdrawal
    excess = fixture_data["excess_withdrawal_case"]
    m24_row = [r for r in excess["rows"] if r["month"] == 24][0]

    assert m24_row["gross_withdrawal"] == 20000.0
    # In Year 2 for MYGA-0002, prior anniversary AV was $104,500.00; free limit is 10% = $10,450.00
    expected_free = 104500.0 * 0.10
    expected_chargeable = 20000.0 - expected_free
    expected_sc = expected_chargeable * 0.06  # 6% surrender charge in Year 2
    expected_net_benefit = 20000.0 - expected_sc

    assert math.isclose(m24_row["surrender_charge"], expected_sc, abs_tol=1e-9)
    assert math.isclose(m24_row["net_withdrawal_benefit"], expected_net_benefit, abs_tol=1e-9)
    # Insurer cash flow must deduct net benefit paid to policyholder, not gross
    expected_insurer_cf = -expected_net_benefit - 5.0
    assert math.isclose(m24_row["net_insurer_cash_flow"], expected_insurer_cf, abs_tol=1e-9)


def test_myga_mgsv_nonforfeiture_contractual_floor(fixture_data):
    """Verify contractual MGSV accumulation and CSV >= MGSV floor protection."""
    anchor = fixture_data["anchor_case"]
    premium = float(anchor["premium"])
    mgsv_load = float(anchor["mgsv_premium_load"])
    annual_mgsv_rate = float(anchor["mgsv_rate"])
    m_mgsv_rate = (1.0 + annual_mgsv_rate) ** (1.0 / 12.0) - 1.0

    mgsv = premium * mgsv_load
    for row in anchor["rows"]:
        interest = mgsv * m_mgsv_rate
        gross_wd = row["gross_withdrawal"]
        mgsv = mgsv + interest - gross_wd

        assert math.isclose(mgsv, row["mgsv_eom"], abs_tol=INTERNAL_ARITHMETIC_PRECISION)
        # Cash Surrender Value must never be lower than the contractual MGSV floor
        assert row["cash_surrender_value_eom"] >= row["mgsv_eom"] - INTERNAL_ARITHMETIC_PRECISION


def test_myga_sample_inforce_and_schedules_integrity():
    """Verify that samples/myga/ files exist, conform to schema, and contain valid actuarial data."""
    inforce_csv = SAMPLES_DIR / "synthetic_myga_inforce.csv"
    sc_csv = SAMPLES_DIR / "myga_surrender_charge.csv"
    cap_csv = SAMPLES_DIR / "myga_sc_age_cap.csv"

    assert inforce_csv.is_file()
    assert sc_csv.is_file()
    assert cap_csv.is_file()

    with sc_csv.open(encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
    schedules = {row["schedule_id"] for row in reader}
    assert {"SC_3YR_STANDARD", "SC_5YR_STANDARD", "SC_7YR_STANDARD", "SC_10YR_STANDARD"} <= schedules

    by_sched: dict[str, list[dict]] = {}
    for r in reader:
        by_sched.setdefault(r["schedule_id"], []).append(r)
    for sched_id, rows in by_sched.items():
        sorted_rows = sorted(rows, key=lambda x: int(x["policy_year"]))
        assert float(sorted_rows[-1]["surrender_charge_rate"]) == 0.0

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

    for p in inforce:
        assert p["surrender_charge_schedule_id"] in schedules
        assert 20000.0 <= float(p["premium"]) <= 1000000.0
        assert 0.02 <= float(p["credited_rate"]) <= 0.08


def test_myga_sample_data_generator_reproducibility():
    """Verify deterministic generator produces bit-for-bit exact files to committed samples."""
    import importlib.util

    gen_path = BACKEND_DIR / "scripts" / "generate_myga_samples.py"
    assert gen_path.is_file()

    spec = importlib.util.spec_from_file_location("gen_myga", gen_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        module.generate_surrender_charge_tables(tmppath)
        module.generate_inforce(tmppath / "synthetic_myga_inforce.csv")

        # Compare files
        for filename in ["synthetic_myga_inforce.csv", "myga_surrender_charge.csv", "myga_sc_age_cap.csv"]:
            generated_content = (tmppath / filename).read_text(encoding="utf-8").replace("\r\n", "\n")
            committed_content = (SAMPLES_DIR / filename).read_text(encoding="utf-8").replace("\r\n", "\n")
            assert generated_content == committed_content, f"File {filename} drifted from generator output!"


# =============================================================================
# NEGATIVE TESTS FOR FALSE-PASS CONDITIONS (T-01 through T-14)
# =============================================================================

@pytest.fixture()
def base_rows(fixture_data) -> list[dict]:
    return copy.deepcopy(fixture_data["anchor_case"]["rows"])


def test_t01_zero_actual_rows(base_rows):
    """T-01: Comparator must fail when actual output contains zero rows."""
    res = reconcile_ledger(actual_rows=[], expected_rows=base_rows)
    assert res["status"] == "FAIL"
    assert any("zero rows" in err for err in res["errors"])


def test_t02_only_first_expected_month(base_rows):
    """T-02: Comparator must fail when actual output has only the first month."""
    res = reconcile_ledger(actual_rows=[base_rows[0]], expected_rows=base_rows)
    assert res["status"] == "FAIL"
    assert any("Missing 59 expected keys" in err for err in res["errors"])


def test_t03_expected_60_actual_59(base_rows):
    """T-03: Comparator must fail when actual output has 59 months instead of 60."""
    res = reconcile_ledger(actual_rows=base_rows[:-1], expected_rows=base_rows)
    assert res["status"] == "FAIL"
    assert any("Missing 1 expected keys" in err for err in res["errors"])


def test_t04_expected_60_actual_61(base_rows):
    """T-04: Comparator must fail when actual output has an extra month (61 months)."""
    extra_row = copy.deepcopy(base_rows[-1])
    extra_row["month"] = 61
    res = reconcile_ledger(actual_rows=base_rows + [extra_row], expected_rows=base_rows)
    assert res["status"] == "FAIL"
    assert any("unexpected extra keys" in err for err in res["errors"])


def test_t05_missing_middle_month(base_rows):
    """T-05: Comparator must fail when a middle month (e.g. month 30) is missing."""
    filtered = [r for r in base_rows if r["month"] != 30]
    res = reconcile_ledger(actual_rows=filtered, expected_rows=base_rows)
    assert res["status"] == "FAIL"
    assert any("Missing 1 expected keys" in err for err in res["errors"])


def test_t06_duplicate_month(base_rows):
    """T-06: Comparator must fail when actual output contains duplicate months."""
    dupe = copy.deepcopy(base_rows[11])  # month 12
    res = reconcile_ledger(actual_rows=base_rows + [dupe], expected_rows=base_rows)
    assert res["status"] == "FAIL"
    assert any("Duplicate" in err for err in res["errors"])


def test_t07_wrong_month_id_with_copied_values(base_rows):
    """T-07: Comparator must fail when a row has an incorrect month ID."""
    tampered = copy.deepcopy(base_rows)
    tampered[15]["month"] = 99
    res = reconcile_ledger(actual_rows=tampered, expected_rows=base_rows)
    assert res["status"] == "FAIL"
    assert any("Missing 1 expected keys" in err for err in res["errors"])
    assert any("unexpected extra keys" in err for err in res["errors"])


def test_t08_missing_required_output_variable(base_rows):
    """T-08: Comparator must fail when any required output variable is omitted."""
    tampered = copy.deepcopy(base_rows)
    del tampered[10]["net_insurer_cash_flow"]
    res = reconcile_ledger(actual_rows=tampered, expected_rows=base_rows)
    assert res["status"] == "FAIL"
    assert any("missing required field 'net_insurer_cash_flow'" in err for err in res["errors"])


def test_t09_nan_value(base_rows):
    """T-09: Comparator must fail when actual output contains NaN."""
    tampered = copy.deepcopy(base_rows)
    tampered[5]["account_value_eom"] = float("nan")
    res = reconcile_ledger(actual_rows=tampered, expected_rows=base_rows)
    assert res["status"] == "FAIL"
    assert any("NaN" in err for err in res["errors"])


def test_t10_positive_infinity(base_rows):
    """T-10: Comparator must fail when actual output contains +Infinity."""
    tampered = copy.deepcopy(base_rows)
    tampered[5]["credited_interest"] = float("inf")
    res = reconcile_ledger(actual_rows=tampered, expected_rows=base_rows)
    assert res["status"] == "FAIL"
    assert any("+Infinity" in err for err in res["errors"])


def test_t11_negative_infinity(base_rows):
    """T-11: Comparator must fail when actual output contains -Infinity."""
    tampered = copy.deepcopy(base_rows)
    tampered[5]["net_insurer_cash_flow"] = float("-inf")
    res = reconcile_ledger(actual_rows=tampered, expected_rows=base_rows)
    assert res["status"] == "FAIL"
    assert any("-Infinity" in err for err in res["errors"])


def test_t12_wrong_policy_id(base_rows):
    """T-12: Comparator must fail when actual output references an unexpected policy ID."""
    tampered = copy.deepcopy(base_rows)
    for r in tampered:
        r["policy_id"] = "WRONG-POLICY-999"
    res = reconcile_ledger(
        actual_rows=tampered,
        expected_rows=base_rows,
        expected_policy_id="MYGA-0001",
    )
    assert res["status"] == "FAIL"
    assert any("policy_id mismatch" in err or "Missing" in err for err in res["errors"])


def test_t13_wrong_schema_version(base_rows):
    """T-13: Comparator must fail when schema version does not match expected version."""
    res = reconcile_ledger(
        actual_rows=base_rows,
        expected_rows=base_rows,
        expected_schema_version="mentoramp.reference_fixture/v1",
        actual_schema_version="mentoramp.reference_fixture/v0_deprecated",
    )
    assert res["status"] == "FAIL"
    assert any("Schema version mismatch" in err for err in res["errors"])


def test_t14_correct_full_result_set(base_rows):
    """T-14: Comparator must PASS only when the complete, exact, finite result set matches."""
    res = reconcile_ledger(
        actual_rows=base_rows,
        expected_rows=base_rows,
        expected_policy_id="MYGA-0001",
        expected_schema_version="mentoramp.reference_fixture/v1",
        actual_schema_version="mentoramp.reference_fixture/v1",
    )
    assert res["status"] == "PASS"
    assert len(res["errors"]) == 0
    for field, stat in res["field_discrepancies"].items():
        assert stat["status"] == "PASS"
        assert stat["max_abs_diff"] == 0.0


# =============================================================================
# FINAL CLEANUP TESTS (F-01 through F-09)
# =============================================================================

def test_f01_month_as_float_fails(base_rows):
    """F-01: Month as a float (e.g. 1.5 or 1.0) or string float must fail without uncaught exception."""
    tampered_float = copy.deepcopy(base_rows)
    tampered_float[0]["month"] = 1.5
    res1 = reconcile_ledger(actual_rows=tampered_float, expected_rows=base_rows)
    assert res1["status"] == "FAIL"
    assert any("cannot be a float" in err for err in res1["errors"])

    tampered_str_float = copy.deepcopy(base_rows)
    tampered_str_float[0]["month"] = "1.5"
    res2 = reconcile_ledger(actual_rows=tampered_str_float, expected_rows=base_rows)
    assert res2["status"] == "FAIL"
    assert any("decimal or exponential format" in err for err in res2["errors"])

    # Also verify validate_projection_month directly
    m_val, m_err = validate_projection_month(1.5)
    assert m_val is None
    assert "cannot be a float" in m_err


def test_f02_month_as_bool_fails(base_rows):
    """F-02: Month as a boolean (e.g. True/False) must fail because bool is a subclass of int in Python."""
    tampered = copy.deepcopy(base_rows)
    tampered[0]["month"] = True
    res = reconcile_ledger(actual_rows=tampered, expected_rows=base_rows)
    assert res["status"] == "FAIL"
    assert any("cannot be a boolean" in err for err in res["errors"])

    # Also verify validate_projection_month directly
    m_val, m_err = validate_projection_month(True)
    assert m_val is None
    assert "cannot be a boolean" in m_err


def test_f03_month_zero_or_negative_fails(base_rows):
    """F-03: Projection month <= 0 must fail."""
    tampered_zero = copy.deepcopy(base_rows)
    tampered_zero[0]["month"] = 0
    res_zero = reconcile_ledger(actual_rows=tampered_zero, expected_rows=base_rows)
    assert res_zero["status"] == "FAIL"
    assert any("must be strictly positive" in err for err in res_zero["errors"])

    tampered_neg = copy.deepcopy(base_rows)
    tampered_neg[0]["month"] = -5
    res_neg = reconcile_ledger(actual_rows=tampered_neg, expected_rows=base_rows)
    assert res_neg["status"] == "FAIL"
    assert any("must be strictly positive" in err for err in res_neg["errors"])

    # Direct helper checks
    assert validate_projection_month(0)[0] is None
    assert validate_projection_month(-1)[0] is None


def test_f04_missing_actual_schema_version_fails(base_rows):
    """F-04: When expected schema version is defined, missing actual schema version must fail."""
    res_none = reconcile_ledger(
        actual_rows=base_rows,
        expected_rows=base_rows,
        expected_schema_version="mentoramp.reference_fixture/v1",
        actual_schema_version=None,
    )
    assert res_none["status"] == "FAIL"
    assert any("Missing actual schema version" in err for err in res_none["errors"])

    res_empty = reconcile_ledger(
        actual_rows=base_rows,
        expected_rows=base_rows,
        expected_schema_version="mentoramp.reference_fixture/v1",
        actual_schema_version="",
    )
    assert res_empty["status"] == "FAIL"
    assert any("Missing actual schema version" in err for err in res_empty["errors"])


def test_f05_mismatched_schema_version_fails(base_rows):
    """F-05: Mismatched schema version must fail with structured error."""
    res = reconcile_ledger(
        actual_rows=base_rows,
        expected_rows=base_rows,
        expected_schema_version="mentoramp.reference_fixture/v1",
        actual_schema_version="mentoramp.reference_fixture/v2_unexpected",
    )
    assert res["status"] == "FAIL"
    assert any("Schema version mismatch" in err for err in res["errors"])


def test_f06_malformed_expected_fixture_triggers_reference_fixture_invalid(base_rows):
    """F-06: Malformed expected fixture must return REFERENCE_FIXTURE_INVALID without unhandled traceback."""
    # 1. Empty expected rows
    res_empty = reconcile_ledger(actual_rows=base_rows, expected_rows=[])
    assert res_empty["status"] == "REFERENCE_FIXTURE_INVALID"
    assert any("empty or not a list" in err for err in res_empty["errors"])

    # 2. Expected rows missing required fields
    malformed_rows = [{"policy_id": "MYGA-0001", "month": 1}]
    res_missing_fields = reconcile_ledger(actual_rows=base_rows, expected_rows=malformed_rows)
    assert res_missing_fields["status"] == "REFERENCE_FIXTURE_INVALID"
    assert any("missing required field" in err for err in res_missing_fields["errors"])

    # 3. Expected rows with invalid month type (e.g. float)
    bad_month_rows = copy.deepcopy(base_rows[:2])
    bad_month_rows[0]["month"] = 1.5
    res_bad_month = reconcile_ledger(actual_rows=base_rows, expected_rows=bad_month_rows)
    assert res_bad_month["status"] == "REFERENCE_FIXTURE_INVALID"
    assert any("invalid month" in err for err in res_bad_month["errors"])

    # 4. Expected rows with duplicate keys
    dupe_rows = copy.deepcopy(base_rows[:2]) + [copy.deepcopy(base_rows[0])]
    res_dupe = reconcile_ledger(actual_rows=base_rows, expected_rows=dupe_rows)
    assert res_dupe["status"] == "REFERENCE_FIXTURE_INVALID"
    assert any("Duplicate key in expected fixture" in err for err in res_dupe["errors"])


def test_f07_excess_withdrawal_description_matches_arithmetic(fixture_data):
    """F-07: Descriptive metadata in excess_withdrawal_case must not drift from actual arithmetic.

    Arithmetic:
      prior anniversary AV = 104,500
      free limit = 10% = 10,450
      gross withdrawal = 20,000
      chargeable withdrawal = 9,550
      surrender charge = 6% × 9,550 = 573
    """
    excess_case = fixture_data["excess_withdrawal_case"]
    desc = excess_case["description"]

    # Verify updated accurate numbers are present
    assert "$10,450 free limit" in desc
    assert "$9,550 chargeable withdrawal" in desc
    assert "6% surrender charge" in desc or "6% SC" in desc

    # Verify stale erroneous values do NOT appear
    assert "$10,050" not in desc
    assert "$9,950" not in desc

    # Re-verify underlying month 24 row numbers
    m24 = [r for r in excess_case["rows"] if r["month"] == 24][0]
    assert math.isclose(m24["surrender_charge"], 573.0, abs_tol=1e-6)
    assert m24["gross_withdrawal"] == 20000.0


def test_f08_tolerance_semantics_and_metadata_verification(fixture_data):
    """F-08: Verify tolerance semantics (1e-12 internal vs 1e-6 engine cross-check) and metadata."""
    # Verify module constants
    assert INTERNAL_ARITHMETIC_PRECISION == 1e-12
    assert ENGINE_CROSSCHECK_TOLERANCE == 1e-6

    # Verify fixture tolerance object
    tol = fixture_data.get("tolerance", {})
    assert tol["internal_arithmetic_precision"] == 1e-12
    assert tol["engine_crosscheck_tolerance"] == 1e-6
    assert "description" in tol
    assert "Internal arithmetic precision is 1e-12" in tol["description"]
    assert "1e-6" in tol["description"]


def test_f09_contractual_waiver_naming_convention():
    """F-09: Verify contractual waiver labels are used, and false statutory labels are absent."""
    cap_csv = SAMPLES_DIR / "myga_sc_age_cap.csv"
    assert cap_csv.is_file()
    content = cap_csv.read_text(encoding="utf-8")

    # Ensure rmd_waiver exists and rmd_statutory does NOT
    assert "rmd_waiver" in content
    assert "rmd_statutory" not in content

    # Check generator source code as well
    gen_file = BACKEND_DIR / "scripts" / "generate_myga_samples.py"
    gen_content = gen_file.read_text(encoding="utf-8")
    assert "rmd_statutory" not in gen_content
    assert "statutory waiver provisions" not in gen_content
    assert "rmd_waiver" in gen_content
