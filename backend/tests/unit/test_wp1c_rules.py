"""WP1 correction — package envelope identity, scenario override governance, inforce fingerprint v2."""

import pytest

from app.core.execution import run_package
from app.core.execution.fingerprints import DuplicatePolicyIds, inforce_fingerprint
from app.core.projection_engine.run_data import VariableSpec
from app.services.run_package_service import override_problems

# =============================================================================
# Package envelope (P1 #4)
# =============================================================================

DOCUMENT = {
    "schema_version": run_package.PACKAGE_SCHEMA_VERSION,
    "fingerprint_algorithm": "sha256/canonical-json/v1",
    "fingerprint": "fp",
    "identity": {"package_id": "pkg", "frozen_for_run_id": "run", "project_id": "p", "run_set_id": "rs"},
    "configuration": {
        "project": {"id": "p"}, "scenario": {"id": "s"}, "model_version": {"id": "mv"},
        "projection_set": {"id": "ps"}, "horizon_months": 24, "valuation_date": "2026-12-31",
    },
}
ROW = {"id": "pkg", "run_id": "run", "project_id": "p", "fingerprint": "fp",
       "schema_version": run_package.PACKAGE_SCHEMA_VERSION, "fingerprint_algorithm": "sha256/canonical-json/v1"}
RUN = {"id": "run", "project_id": "p", "run_set_id": "rs", "run_package_fingerprint": "fp", "scenario_id": "s",
       "model_version_id": "mv", "projection_set_id": "ps", "horizon_months": 24, "valuation_date": "2026-12-31"}


def test_a_consistent_envelope_passes():
    run_package.verify_envelope(DOCUMENT, ROW, RUN)


@pytest.mark.parametrize("where, key, value", [
    ("identity", "package_id", "other"),
    ("identity", "frozen_for_run_id", "other-run"),
    ("identity", "project_id", "other-project"),
    ("identity", "run_set_id", "other-set"),
    ("row", "run_id", "other-run"),
    ("row", "fingerprint", "other-fp"),
    ("run", "run_package_fingerprint", "other-fp"),
    ("run", "project_id", "other-project"),
])
def test_relabelled_identity_is_refused(where, key, value):
    document, row, run = dict(DOCUMENT), dict(ROW), dict(RUN)
    if where == "identity":
        document["identity"] = {**document["identity"], key: value}
    elif where == "row":
        row[key] = value
    else:
        run[key] = value
    with pytest.raises(run_package.RunPackageError) as raised:
        run_package.verify_envelope(document, row, run)
    assert raised.value.code == "PACKAGE_IDENTITY_MISMATCH"


@pytest.mark.parametrize("key, value", [
    ("scenario_id", "other-scenario"), ("model_version_id", "other"), ("projection_set_id", "other"),
    ("horizon_months", 12), ("valuation_date", "2027-01-31"),
])
def test_run_index_disagreeing_with_the_package_is_refused(key, value):
    with pytest.raises(run_package.RunPackageError) as raised:
        run_package.verify_envelope(DOCUMENT, ROW, {**RUN, key: value})
    assert raised.value.code == "RUN_INDEX_MISMATCH"


# =============================================================================
# Scenario override governance (P1 #8)
# =============================================================================

VARIABLES = {
    "discount_rate_annual": VariableSpec(name="discount_rate_annual", kind="manual",
                                         source={"type": "manual", "value": 0.045},
                                         allow_scenario_override=True),
    "monthly_payment": VariableSpec(name="monthly_payment", kind="input", source={"type": "input"}),
    "expected_payment": VariableSpec(name="expected_payment", kind="formula", source={"type": "formula"},
                                     allow_scenario_override=True),
    "gender": VariableSpec(name="gender", kind="input", data_type="string", source={"type": "input"},
                           allow_scenario_override=True),
}


def codes(*overrides):
    return {problem.code for problem in override_problems("S", list(overrides), VARIABLES)}


def override(**fields):
    return {"target_variable": "discount_rate_annual", "operation": "set", "value": 0.03, **fields}


def test_a_valid_permitted_override_has_no_problems():
    assert codes(override()) == set()
    assert codes(override(applies_from_period=1, applies_to_period=12),
                 override(applies_from_period=13, applies_to_period=None, operation="add", value=0.01)) == set()


@pytest.mark.parametrize("fields, code", [
    ({"applies_from_period": 12, "applies_to_period": 6}, "SCENARIO_PERIOD_RANGE_INVALID"),
    ({"applies_from_period": -1}, "SCENARIO_PERIOD_INVALID"),
    ({"applies_to_period": 1201}, "SCENARIO_PERIOD_INVALID"),
    ({"applies_from_period": True}, "SCENARIO_PERIOD_INVALID"),
    ({"applies_from_period": 1.5}, "SCENARIO_PERIOD_INVALID"),
    ({"operation": "divide"}, "SCENARIO_OPERATION_INVALID"),
    ({"operation": "add", "value": "high"}, "SCENARIO_VALUE_INVALID"),
    ({"operation": "multiply", "value": True}, "SCENARIO_VALUE_INVALID"),
    ({"target_variable": "monthly_payment"}, "SCENARIO_TARGET_NOT_OVERRIDABLE"),
    ({"target_variable": "expected_payment"}, "SCENARIO_TARGET_CALCULATED"),
    ({"target_variable": "gender", "value": 1}, "SCENARIO_TARGET_NOT_NUMERIC"),
    ({"target_variable": "no_such_variable"}, "SCENARIO_TARGET_UNKNOWN"),
])
def test_invalid_overrides_are_refused(fields, code):
    assert code in codes(override(**fields))


@pytest.mark.parametrize("first, second", [
    ((None, None), (5, 10)),          # open-ended covers everything
    ((0, 12), (12, 24)),              # inclusive bounds touch at month 12
    ((6, None), (1, 6)),
])
def test_overlapping_overrides_of_one_variable_are_refused(first, second):
    found = codes(override(applies_from_period=first[0], applies_to_period=first[1]),
                  override(applies_from_period=second[0], applies_to_period=second[1], value=0.02))
    assert "SCENARIO_OVERRIDE_OVERLAP" in found


# =============================================================================
# Inforce fingerprint v2 (P1 #9)
# =============================================================================

RECORDS = [("SPIA-0002", {"monthly_payment": "900"}), ("SPIA-0001", {"monthly_payment": "1650"})]


def test_policy_identity_is_part_of_the_fingerprint():
    renamed = [("SPIA-0009", RECORDS[0][1]), RECORDS[1]]  # same data, different policy_id
    assert inforce_fingerprint(renamed) != inforce_fingerprint(RECORDS)


def test_data_is_part_of_the_fingerprint():
    changed = [("SPIA-0002", {"monthly_payment": "901"}), RECORDS[1]]
    assert inforce_fingerprint(changed) != inforce_fingerprint(RECORDS)


def test_storage_order_does_not_change_the_fingerprint():
    assert inforce_fingerprint(list(reversed(RECORDS))) == inforce_fingerprint(RECORDS)


def test_duplicate_policy_ids_are_refused():
    with pytest.raises(DuplicatePolicyIds):
        inforce_fingerprint([*RECORDS, ("SPIA-0001", {"monthly_payment": "5"})])
