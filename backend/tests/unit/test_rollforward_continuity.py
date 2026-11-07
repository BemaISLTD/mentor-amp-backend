"""Tests for generic rollforward continuity and attribution engine (M5C).

Per CAS §7.2:
Asserts the fundamental accounting continuity invariant:
    Beginning Balance + Sum(Attributed Movements) = Ending Balance (± tolerance)
Verifies that any unbalanced schedule is flagged immediately and cannot silently pass.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.valuation.rollforward import (
    DAC_TEMPLATE,
    LDTI_LFPB_TEMPLATE,
    MRB_TEMPLATE,
    VM21_ATTRIBUTION_TEMPLATE,
    VM22_ATTRIBUTION_TEMPLATE,
    get_methodology_template,
    record_rollforward_summary,
    validate_continuity,
)
from app.db.database import Base
from app.db.models.project import Project
from app.db.models.rollforward import RollforwardSummary
from app.db.models.run import Run


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_ldti_rollforward_continuity_pass():
    opening = 10_000_000.00
    movements = {
        "new_business": 500_000.00,
        "expected_change": 320_000.00,
        "experience_variance": -140_000.00,
        "assumption_unlock": 210_000.00,
        "discount_rate_movement": -95_000.00,
    }
    # Expected ending = 10,000,000 + 500,000 + 320,000 - 140,000 + 210,000 - 95,000 = 10,795,000.00
    ending = 10_795_000.00

    result = validate_continuity(opening, movements, ending, tolerance=0.01)
    assert result.is_continuous is True
    assert result.difference == pytest.approx(0.0)
    assert result.expected_ending_balance == pytest.approx(ending)
    assert result.error_message is None


def test_dac_rollforward_amortization_pass():
    opening = 4_500_000.00
    movements = {
        "capitalized_costs": 800_000.00,
        "amortization_expense": -450_000.00,
        "terminations_adjustments": -50_000.00,
    }
    # Expected ending = 4,500,000 + 800,000 - 450,000 - 50,000 = 4,800,000.00
    ending = 4_800_000.00

    result = validate_continuity(opening, movements, ending, tolerance=0.01)
    assert result.is_continuous is True
    assert result.difference == pytest.approx(0.0)


def test_rollforward_continuity_breach_detected():
    opening = 1_000_000.00
    movements = {
        "new_issues": 100_000.00,
        "net_cash_flows": -50_000.00,
    }
    # Correct ending would be 1,050,000.00. We pass 1,050,500.00 (a $500 discrepancy)
    corrupt_ending = 1_050_500.00

    result = validate_continuity(opening, movements, corrupt_ending, tolerance=0.01)
    assert result.is_continuous is False
    assert result.difference == pytest.approx(-500.0)
    assert "Rollforward continuity breach" in result.error_message
    assert "1,050,000.00" in result.error_message
    assert "1,050,500.00" in result.error_message


def test_tolerance_boundary_behavior():
    opening = 100.00
    movements = {"step1": 10.00}
    
    # Delta of 0.004 within 0.01 tolerance -> PASS
    res_pass = validate_continuity(opening, movements, 110.004, tolerance=0.01)
    assert res_pass.is_continuous is True

    # Delta of 0.02 exceeding 0.01 tolerance -> FAIL
    res_fail = validate_continuity(opening, movements, 110.02, tolerance=0.01)
    assert res_fail.is_continuous is False


def test_get_methodology_templates():
    ldti_tpl = get_methodology_template("LDTI")
    assert ldti_tpl.methodology == "LDTI"
    assert ldti_tpl.basis == "GAAP"
    assert len(ldti_tpl.components) == 7

    vm21_tpl = get_methodology_template("vm-21")
    assert vm21_tpl.methodology == "VM21"
    assert vm21_tpl.basis == "STATUTORY"
    assert len(vm21_tpl.components) == 8


def test_record_rollforward_summary_persistence(db_session):
    # Seed project and run
    project = Project(id="proj-rf", name="Rollforward Project")
    run = Run(id="run-rf", project_id="proj-rf", run_type="valuation", basis="GAAP", methodology="LDTI")
    db_session.add_all([project, run])
    db_session.commit()

    opening = 5_000_000.0
    movements = {"new_business": 250_000.0, "expected_change": 150_000.0}
    ending = 5_400_000.0

    cont_res = validate_continuity(opening, movements, ending)
    records = record_rollforward_summary(
        db=db_session,
        run_id="run-rf",
        project_id="proj-rf",
        cohort_id="COHORT-2026-MYGA",
        methodology="LDTI",
        basis="GAAP",
        continuity_result=cont_res,
    )

    assert len(records) == 4  # opening, 2 movements, ending
    saved = db_session.query(RollforwardSummary).filter(RollforwardSummary.run_id == "run-rf").all()
    assert len(saved) == 4
    assert all(r.is_continuity_verified for r in saved)
    assert saved[0].component_name == "opening_balance"
    assert saved[0].component_order == 1
    assert saved[1].component_name == "new_business"
    assert saved[1].component_order == 2

from datetime import date
from decimal import Decimal
from app.core.valuation.taxonomy import (
    ValuationTaxonomyError,
    validate_basis,
    validate_basis_methodology_pair,
    validate_methodology,
)
from app.db.models.valuation_detail import ValuationRunDetail
from app.services.common import ServiceError


def test_rollforward_parent_run_identity_validation_mismatch(db_session):
    """Directive 10: Parent run identity validation, rejecting IDOR and contradictory metadata."""
    p1 = Project(id="proj-legit", name="Legit Project")
    p2 = Project(id="proj-foreign", name="Foreign Project")
    run_legit = Run(
        id="run-legit-val",
        project_id="proj-legit",
        run_type="valuation",
        basis="STATUTORY",
        methodology="VM22",
    )
    db_session.add_all([p1, p2, run_legit])
    db_session.commit()

    cont_res = validate_continuity(Decimal("1000.00"), {"step": Decimal("100.00")}, Decimal("1100.00"))

    # 1. Nonexistent parent run -> 404
    with pytest.raises(ServiceError) as exc_info:
        record_rollforward_summary(
            db=db_session,
            run_id="run-nonexistent",
            project_id="proj-legit",
            cohort_id="COHORT-1",
            methodology="VM22",
            basis="STATUTORY",
            continuity_result=cont_res,
        )
    assert exc_info.value.status_code == 404

    # 2. Foreign project IDOR attempt -> 404 (does not leak existence)
    with pytest.raises(ServiceError) as exc_info:
        record_rollforward_summary(
            db=db_session,
            run_id="run-legit-val",
            project_id="proj-foreign",
            cohort_id="COHORT-1",
            methodology="VM22",
            basis="STATUTORY",
            continuity_result=cont_res,
        )
    assert exc_info.value.status_code == 404

    # 3. Contradictory basis (run is STATUTORY, passed GAAP) -> 400
    with pytest.raises(ServiceError) as exc_info:
        record_rollforward_summary(
            db=db_session,
            run_id="run-legit-val",
            project_id="proj-legit",
            cohort_id="COHORT-1",
            methodology="VM22",
            basis="GAAP",
            continuity_result=cont_res,
        )
    assert exc_info.value.status_code == 400
    assert "contradicts parent run basis" in exc_info.value.message

    # 4. Contradictory methodology (run is VM22, passed LDTI) -> 400
    with pytest.raises(ServiceError) as exc_info:
        record_rollforward_summary(
            db=db_session,
            run_id="run-legit-val",
            project_id="proj-legit",
            cohort_id="COHORT-1",
            methodology="LDTI",
            basis="STATUTORY",
            continuity_result=cont_res,
        )
    assert exc_info.value.status_code == 400
    assert "contradicts parent run methodology" in exc_info.value.message


def test_controlled_numeric_policy_decimal_continuity():
    """Directive 11: Controlled Decimal numeric policy with configurable tolerance."""
    # 1. Exact equality
    res_exact = validate_continuity(
        Decimal("1000000.50"),
        {"step": Decimal("250000.25")},
        Decimal("1250000.75"),
        tolerance=Decimal("0.00"),
    )
    assert res_exact.is_continuous is True
    assert res_exact.difference == Decimal("0.00")

    # 2. High-magnitude balances ($100,000,000+)
    high_opening = Decimal("500000000.00")
    high_movements = {
        "interest_credited": Decimal("25000000.00"),
        "surrenders": Decimal("-45000000.00"),
        "net_premiums": Decimal("120000000.00"),
    }
    high_ending = Decimal("600000000.00")
    res_high = validate_continuity(high_opening, high_movements, high_ending, tolerance=Decimal("0.01"))
    assert res_high.is_continuous is True

    # 3. Decimal-cent edge cases and configurable tolerance
    res_edge_pass = validate_continuity(
        Decimal("100.000"),
        {"step": Decimal("10.004")},
        Decimal("110.000"),
        tolerance=Decimal("0.005"),
    )
    assert res_edge_pass.is_continuous is True

    res_edge_fail = validate_continuity(
        Decimal("100.000"),
        {"step": Decimal("10.006")},
        Decimal("110.000"),
        tolerance=Decimal("0.005"),
    )
    assert res_edge_fail.is_continuous is False

    # 4. Negative balances/movements (e.g. net liabilities and negative adjustments)
    res_negative = validate_continuity(
        Decimal("-5000.00"),
        {"charge": Decimal("-1200.00"), "reversal": Decimal("300.00")},
        Decimal("-5900.00"),
        tolerance=Decimal("0.01"),
    )
    assert res_negative.is_continuous is True


def test_taxonomy_launch_vs_future_tax_basis():
    """Directive 12: TAX rejected for Launch 1.0; extensible architecture preserved."""
    # Approved launch bases pass
    assert validate_basis("GAAP") == "GAAP"
    assert validate_basis("STATUTORY") == "STATUTORY"

    # TAX rejected in standard production validation
    with pytest.raises(ValuationTaxonomyError) as exc_info:
        validate_basis("TAX", allow_future=False)
    assert "NOT approved for Launch 1.0" in str(exc_info.value)

    # TAX accepted with explicit future extensible flag
    assert validate_basis("TAX", allow_future=True) == "TAX"

    # Pairing validation rejects TAX + VM22 for Launch 1.0
    with pytest.raises(ValuationTaxonomyError) as exc_info:
        validate_basis_methodology_pair("TAX", "VM22", allow_future=False)
    assert "not approved for launch 1.0" in str(exc_info.value).lower()

    # Extensible pairing succeeds with allow_future=True
    b, m = validate_basis_methodology_pair("TAX", "VM22", allow_future=True)
    assert b == "TAX"
    assert m == "VM22"


def test_subordinate_valuation_run_detail_proxies_canonical_run(db_session):
    """Directive 4: ValuationRunDetail derives identity directly from parent Run with zero drift."""
    project = Project(id="proj-vdetail", name="VDetail Project")
    run = Run(
        id="run-canonical-1",
        project_id="proj-vdetail",
        run_type="valuation",
        basis="STATUTORY",
        methodology="VM22",
        methodology_version="v2024.1",
        source_projection_run_id="run-proj-upstream-1",
        derived_dataset_id="ds-upstream-1",
    )
    db_session.add_all([project, run])
    db_session.commit()

    detail = ValuationRunDetail(
        run_id=run.id,
        run=run,
        valuation_date=date(2026, 12, 31),
        discount_curve_fingerprint="curve-sha-123",
        configuration={"key": "val"},
        summary_results={"reserve": 1000.0},
    )
    db_session.add(detail)
    db_session.commit()

    # Identity proxied from canonical Run
    assert detail.project_id == "proj-vdetail"
    assert detail.basis == "STATUTORY"
    assert detail.methodology == "VM22"
    assert detail.methodology_version == "v2024.1"
    assert detail.source_projection_run_id == "run-proj-upstream-1"
    assert detail.derived_dataset_id == "ds-upstream-1"
