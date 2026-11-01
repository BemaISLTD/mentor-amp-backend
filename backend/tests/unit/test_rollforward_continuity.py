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
