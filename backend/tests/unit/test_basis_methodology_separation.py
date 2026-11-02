"""Tests for separation of accounting basis and actuarial methodology.

Per CAS §7 and Milestone 5 Architecture Invariants:
1. Basis (GAAP, STATUTORY, TAX) and Methodology (VM21, VM22, LDTI, DAC, MRB, ASC815)
   must be strictly decoupled.
2. Mixing basis and methodology into a single enum is strictly prohibited.
3. Every valuation run or governed model version must validate and freeze both.
"""

import pytest

from app.core.valuation.taxonomy import (
    APPROVED_BASES,
    APPROVED_METHODOLOGIES,
    EXTENSIBLE_BASES,
    ValuationTaxonomyError,
    validate_basis,
    validate_basis_methodology_pair,
    validate_methodology,
)


def test_approved_bases_and_methodologies_are_disjoint():
    """Bases and methodologies must never share identifiers."""
    intersection = set(EXTENSIBLE_BASES).intersection(set(APPROVED_METHODOLOGIES))
    assert len(intersection) == 0, f"Taxonomy overlap detected: {intersection}"


def test_validate_basis_accepts_valid_bases():
    assert validate_basis("GAAP") == "GAAP"
    assert validate_basis("gaap") == "GAAP"
    assert validate_basis("STATUTORY") == "STATUTORY"
    assert validate_basis("statutory") == "STATUTORY"
    # TAX is extensible future basis: rejected by default in Launch 1.0, accepted with allow_future=True
    with pytest.raises(ValuationTaxonomyError) as exc_info:
        validate_basis("TAX")
    assert "not approved for launch 1.0" in str(exc_info.value).lower()
    assert validate_basis("TAX", allow_future=True) == "TAX"


def test_validate_basis_rejects_methodologies_with_helpful_error():
    with pytest.raises(ValuationTaxonomyError) as exc_info:
        validate_basis("VM21")
    assert "actuarial methodology, NOT an accounting basis" in str(exc_info.value)

    with pytest.raises(ValuationTaxonomyError) as exc_info:
        validate_basis("LDTI")
    assert "actuarial methodology, NOT an accounting basis" in str(exc_info.value)

    with pytest.raises(ValuationTaxonomyError) as exc_info:
        validate_basis("DAC")
    assert "actuarial methodology, NOT an accounting basis" in str(exc_info.value)


def test_validate_basis_rejects_arbitrary_string():
    with pytest.raises(ValuationTaxonomyError):
        validate_basis("UNKNOWN_BASIS")
    with pytest.raises(ValuationTaxonomyError):
        validate_basis("")


def test_validate_methodology_accepts_valid_methodologies():
    assert validate_methodology("VM21") == "VM21"
    assert validate_methodology("vm-21") == "VM21"
    assert validate_methodology("VM22") == "VM22"
    assert validate_methodology("vm_22") == "VM22"
    assert validate_methodology("LDTI") == "LDTI"
    assert validate_methodology("DAC") == "DAC"
    assert validate_methodology("MRB") == "MRB"
    assert validate_methodology("ASC815") == "ASC815"
    assert validate_methodology("asc-815") == "ASC815"


def test_validate_methodology_rejects_bases_with_helpful_error():
    with pytest.raises(ValuationTaxonomyError) as exc_info:
        validate_methodology("GAAP")
    assert "accounting basis, NOT a methodology" in str(exc_info.value)

    with pytest.raises(ValuationTaxonomyError) as exc_info:
        validate_methodology("STATUTORY")
    assert "accounting basis, NOT a methodology" in str(exc_info.value)


def test_validate_basis_methodology_pairs():
    # Valid pairings
    b, m = validate_basis_methodology_pair("STATUTORY", "VM-21")
    assert b == "STATUTORY" and m == "VM21"

    b, m = validate_basis_methodology_pair("GAAP", "LDTI")
    assert b == "GAAP" and m == "LDTI"

    # TAX is extensible future basis: rejected by default in Launch 1.0, accepted with allow_future=True
    with pytest.raises(ValuationTaxonomyError) as exc_info:
        validate_basis_methodology_pair("TAX", "VM22")
    assert "not approved for launch 1.0" in str(exc_info.value).lower()

    b, m = validate_basis_methodology_pair("TAX", "VM22", allow_future=True)
    assert b == "TAX" and m == "VM22"

    # Unsupported pairing check
    with pytest.raises(ValuationTaxonomyError) as exc_info:
        validate_basis_methodology_pair("GAAP", "VM-21")
    assert "Unsupported basis/methodology combination" in str(exc_info.value)

    with pytest.raises(ValuationTaxonomyError) as exc_info:
        validate_basis_methodology_pair("STATUTORY", "LDTI")
    assert "Unsupported basis/methodology combination" in str(exc_info.value)
