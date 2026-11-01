"""Tests for M5 Valuation RunPackage provenance and reproducibility (Directives 3 & 16).

Verifies:
1. Pure reproducibility: Identical valuation inputs produce identical package fingerprints bit-for-bit.
2. Material sensitivity: Mutating ANY valuation provenance field alters the package fingerprint.
3. Envelope integrity: verify_envelope detects mismatches between run row and frozen valuation package.
4. Concrete governed valuation run package artifact verification.
"""

import copy
import pytest

from app.core.execution import run_package
from app.core.execution.fingerprints import fingerprint
from app.core.execution.run_package import RunPackageError, verify, verify_envelope


SAMPLE_VALUATION_CONFIGURATION = {
    "project": {"id": "proj-m5-core", "name": "M5 Valuation Core"},
    "run_type": "valuation",
    "model": {"id": "model-spia-01", "name": "SPIA VM-22 Model", "product_code": "SPIA"},
    "model_version": {
        "id": "mv-spia-v1",
        "version_label": "v1.0.0",
        "basis": "STATUTORY",
        "methodology": "VM22",
        "methodology_version": "v2024.1",
        "block_name": "SPIA-Payout-2024",
        "profile_name": "Standard",
        "illustrative": False,
    },
    "valuation_date": "2026-12-31",
    "horizon_months": 360,
    "time_step": "monthly",
    "execution_backend": "cpu",
    "parameters": {"risk_margin": 0.025},
    "formulas": [
        {
            "id": "form-npr-1",
            "name": "StatutoryReserveFormula",
            "output_variable": "statutory_reserve",
            "function_ref": "dsl:pv_future_benefits",
            "version": "1.0",
            "dependencies": ["discount_factor", "expected_benefit_cf"],
            "expression_text": "expected_benefit_cf * discount_factor",
            "unit": "USD",
            "illustrative": False,
            "content_fingerprint": "1111111111111111111111111111111111111111111111111111111111111111",
        }
    ],
    "variables": [
        {
            "id": "var-reserve-1",
            "name": "statutory_reserve",
            "display_name": "Statutory Reserve",
            "version": "1.0",
            "kind": "prior_output",
            "data_type": "number",
            "unit": "USD",
            "required": True,
            "default_value": None,
            "source": {"type": "prior_output", "dataset_id": "ds-proj-run-baseline", "column_name": "reserve"},
            "allow_scenario_override": False,
            "content_fingerprint": "2222222222222222222222222222222222222222222222222222222222222222",
        }
    ],
    "datasets": {
        "inforce": [
            {
                "id": "inf-file-01",
                "name": "spia_inforce_2026.parquet",
                "version_label": "v1",
                "record_count": 5000,
                "fingerprint": "3333333333333333333333333333333333333333333333333333333333333333",
                "fingerprint_scheme": "sha256/parquet-rows/v1",
            }
        ],
        "assumption_tables": [
            {
                "id": "tbl-mort-01",
                "name": "vm22_prescribed_mortality",
                "set_id": "set-mort-2024",
                "set_name": "2024 VBT",
                "table_type": "mortality",
                "version_label": "v1",
                "lookup_keys": ["age", "gender"],
                "value_column": "q_x",
                "row_count": 120,
                "fingerprint": "4444444444444444444444444444444444444444444444444444444444444444",
            }
        ],
        "factor_tables": [],
    },
    "table_bindings": {
        "vm22_prescribed_mortality": {"kind": "assumption", "table_id": "tbl-mort-01"}
    },
    "scenario": {
        "id": "scen-naic-prescribed-1",
        "set_id": "set-scen-stat",
        "set_name": "NAIC Prescribed Curves",
        "name": "Baseline Prescribed Curve",
        "version_label": "v1.0",
        "scenario_type": "deterministic",
        "as_of_date": "2026-12-31",
        "path_count": 1,
        "fingerprint": "5555555555555555555555555555555555555555555555555555555555555555",
        "overrides": [],
    },
    "outputs": {
        "output_variables": ["statutory_reserve"],
        "trace_scope": {"mode": "none", "policy_ids": []},
        "max_traced_policies": 10,
        "published": {
            "statutory_reserve": {
                "display_name": "Statutory Reserve Balance",
                "unit": "USD",
                "dimension": "balance",
                "aggregation": "end_of_period",
            }
        },
    },
    "valuation": {
        "basis": "STATUTORY",
        "methodology": "VM22",
        "methodology_version": "v2024.1",
        "source_projection_run_id": "run-proj-spia-base-01",
        "derived_dataset_id": "ds-proj-run-baseline",
        "derived_dataset_fingerprint": "6666666666666666666666666666666666666666666666666666666666666666",
        "source_artifact_fingerprint": "7777777777777777777777777777777777777777777777777777777777777777",
        "discount_curve_fingerprint": "8888888888888888888888888888888888888888888888888888888888888888",
        "valuation_date": "2026-12-31",
        "valuation_parameters": {"prescribed_spread_bps": 125, "floor_rate": 0.015},
        "valuation_config_fingerprint": "9999999999999999999999999999999999999999999999999999999999999999",
    },
    "build": {
        "app_version": "1.0.0",
        "engine_version": "m5-valuation-1",
        "code_version": "release-v1.0",
        "build_fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "source_commit": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "source_dirty": False,
        "identity_source": "git",
    },
}


def seal_valuation_package(configuration=None, package_id="pkg-val-1", run_id="run-val-1"):
    cfg = copy.deepcopy(configuration or SAMPLE_VALUATION_CONFIGURATION)
    identity = {
        "package_id": package_id,
        "frozen_for_run_id": run_id,
        "run_set_id": None,
        "project_id": cfg["project"]["id"],
        "created_at": "2026-12-31T23:59:59Z",
        "created_by": {"id": "user-lead-actuary", "full_name": "Lead Actuary"},
    }
    governance = {
        "governance_mode": "governed",
        "is_governed": True,
        "frozen_at": "2026-12-31T23:59:59Z",
        "valuation_basis": cfg["valuation"]["basis"],
        "valuation_methodology": cfg["valuation"]["methodology"],
    }
    return run_package.seal(identity, governance, cfg)


def test_valuation_reproducibility_identical_rerun():
    """Directive 16: Identical valuation inputs produce the exact same package fingerprint."""
    pkg1 = seal_valuation_package()
    pkg2 = seal_valuation_package(package_id="pkg-val-DIFFERENT-ID", run_id="run-val-DIFFERENT-RUN")
    
    # Fingerprint covers the frozen configuration deterministically
    assert pkg1["fingerprint"] == pkg2["fingerprint"]
    assert pkg1["fingerprint"] == fingerprint(SAMPLE_VALUATION_CONFIGURATION)
    
    # Verification succeeds against expected fingerprint
    verified_cfg = verify(pkg1, expected_fingerprint=pkg1["fingerprint"])
    assert verified_cfg["valuation"]["methodology"] == "VM22"


@pytest.mark.parametrize(
    "field_path, mutated_value",
    [
        (("valuation", "basis"), "GAAP"),
        (("valuation", "methodology"), "VM21"),
        (("valuation", "methodology_version"), "v2025.1"),
        (("valuation", "source_projection_run_id"), "run-proj-spia-MUTATED"),
        (("valuation", "derived_dataset_id"), "ds-MUTATED"),
        (("valuation", "derived_dataset_fingerprint"), "666666666666666666666666666666666666666666666666666666666666666f"),
        (("valuation", "source_artifact_fingerprint"), "777777777777777777777777777777777777777777777777777777777777777f"),
        (("valuation", "discount_curve_fingerprint"), "888888888888888888888888888888888888888888888888888888888888888f"),
        (("valuation", "valuation_date"), "2027-12-31"),
        (("valuation", "valuation_config_fingerprint"), "999999999999999999999999999999999999999999999999999999999999999f"),
        (("valuation", "valuation_parameters", "prescribed_spread_bps"), 150),
    ],
)
def test_material_valuation_change_changes_package_fingerprint(field_path, mutated_value):
    """Directive 3 & 16: Proves mutating any material valuation field changes the package fingerprint."""
    baseline_fp = fingerprint(SAMPLE_VALUATION_CONFIGURATION)
    
    mutated_cfg = copy.deepcopy(SAMPLE_VALUATION_CONFIGURATION)
    target = mutated_cfg
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = mutated_value

    mutated_fp = fingerprint(mutated_cfg)
    assert mutated_fp != baseline_fp, f"Fingerprint failed to change when mutating {field_path}!"


def test_envelope_verification_enforces_run_row_and_valuation_identity_agreement():
    """Directive 3: verify_envelope ensures run row and package valuation metadata agree."""
    pkg = seal_valuation_package()
    package_row = {
        "id": "pkg-val-1",
        "run_id": "run-val-1",
        "project_id": "proj-m5-core",
        "fingerprint": pkg["fingerprint"],
        "schema_version": pkg["schema_version"],
        "fingerprint_algorithm": pkg["fingerprint_algorithm"],
    }
    
    # 1. Matching run row -> succeeds
    matching_run = {
        "id": "run-val-1",
        "project_id": "proj-m5-core",
        "run_set_id": None,
        "run_package_fingerprint": pkg["fingerprint"],
        "scenario_id": "scen-naic-prescribed-1",
        "model_version_id": "mv-spia-v1",
        "projection_set_id": None,
        "horizon_months": 360,
        "valuation_date": "2026-12-31",
        "basis": "STATUTORY",
        "methodology": "VM22",
        "methodology_version": "v2024.1",
        "source_projection_run_id": "run-proj-spia-base-01",
        "derived_dataset_id": "ds-proj-run-baseline",
    }
    verify_envelope(pkg, package_row, matching_run)

    # 2. Contradictory basis in run row -> raises RUN_INDEX_MISMATCH
    mismatched_run_basis = copy.deepcopy(matching_run)
    mismatched_run_basis["basis"] = "GAAP"
    with pytest.raises(RunPackageError) as exc_info:
        verify_envelope(pkg, package_row, mismatched_run_basis)
    assert exc_info.value.code == "RUN_INDEX_MISMATCH"
    assert "basis" in exc_info.value.message

    # 3. Contradictory methodology in run row -> raises RUN_INDEX_MISMATCH
    mismatched_run_meth = copy.deepcopy(matching_run)
    mismatched_run_meth["methodology"] = "VM21"
    with pytest.raises(RunPackageError) as exc_info:
        verify_envelope(pkg, package_row, mismatched_run_meth)
    assert exc_info.value.code == "RUN_INDEX_MISMATCH"
    assert "methodology" in exc_info.value.message


def test_tampered_package_fails_verification():
    """Directive 3: Tampering with package configuration raises PACKAGE_TAMPERED."""
    pkg = seal_valuation_package()
    # Maliciously modify the discount curve fingerprint without updating top-level fingerprint
    pkg["configuration"]["valuation"]["discount_curve_fingerprint"] = "TAMPERED_CURVE_HASH"
    
    with pytest.raises(RunPackageError) as exc_info:
        verify(pkg, expected_fingerprint=pkg["fingerprint"])
    assert exc_info.value.code == "PACKAGE_TAMPERED"
