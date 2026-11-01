"""Taxonomy and validation for accounting bases and actuarial methodologies.

Per CAS §7, Milestone 5 Architecture Invariants, and ACT-DEC decisions:
- Basis and Methodology must NEVER be mixed into a single enum.
- Basis: GAAP, STATUTORY, and optionally TAX (governed extensible).
- Methodology: VM21, VM22, LDTI, DAC, MRB, ASC815.
"""

from typing import Final, Literal

BasisType = Literal["GAAP", "STATUTORY", "TAX"]
MethodologyType = Literal["VM21", "VM22", "LDTI", "DAC", "MRB", "ASC815"]

APPROVED_BASES: Final[tuple[str, ...]] = ("GAAP", "STATUTORY")
EXTENSIBLE_BASES: Final[tuple[str, ...]] = ("GAAP", "STATUTORY", "TAX")

APPROVED_METHODOLOGIES: Final[tuple[str, ...]] = (
    "VM21",
    "VM22",
    "LDTI",
    "DAC",
    "MRB",
    "ASC815",
)

# Standard regulatory/accounting alignment mapping
METHODOLOGY_PRIMARY_BASIS: Final[dict[str, str]] = {
    "VM21": "STATUTORY",
    "VM22": "STATUTORY",
    "LDTI": "GAAP",
    "DAC": "GAAP",
    "MRB": "GAAP",
    "ASC815": "GAAP",
}

VALID_BASIS_METHODOLOGY_PAIRS: Final[set[tuple[str, str]]] = {
    ("STATUTORY", "VM21"),
    ("STATUTORY", "VM22"),
    ("GAAP", "LDTI"),
    ("GAAP", "DAC"),
    ("GAAP", "MRB"),
    ("GAAP", "ASC815"),
    # Tax basis may apply to specific statutory or GAAP methodologies where required
    ("TAX", "VM21"),
    ("TAX", "VM22"),
    ("TAX", "LDTI"),
    ("TAX", "DAC"),
    ("TAX", "MRB"),
    ("TAX", "ASC815"),
}


class ValuationTaxonomyError(ValueError):
    """Raised when an invalid basis, methodology, or mixed taxonomy is encountered."""
    pass


def normalize_token(val: str) -> str:
    """Normalize input strings by stripping whitespace and uppercase transformation."""
    if not val or not isinstance(val, str):
        raise ValuationTaxonomyError("Value must be a non-empty string.")
    return val.strip().upper().replace("-", "").replace("_", "")


def validate_basis(basis: str, allow_extensible: bool = True) -> str:
    """Validate that the given basis conforms to approved basis vocabularies.
    
    Rejects methodologies passed as bases (e.g. 'VM21' or 'LDTI').
    """
    if not basis:
        raise ValuationTaxonomyError("Basis cannot be empty.")
    
    clean = basis.strip().upper()
    
    # Catch methodology passed as basis
    if clean.replace("-", "").replace("_", "") in APPROVED_METHODOLOGIES:
        raise ValuationTaxonomyError(
            f"Invalid basis '{basis}'. '{basis}' is an actuarial methodology, NOT an accounting basis. "
            f"Basis must be one of {EXTENSIBLE_BASES}."
        )
        
    allowed = EXTENSIBLE_BASES if allow_extensible else APPROVED_BASES
    if clean not in allowed:
        raise ValuationTaxonomyError(
            f"Invalid basis '{basis}'. Must be one of {allowed}."
        )
    return clean


def validate_methodology(methodology: str) -> str:
    """Validate that the given methodology conforms to approved methodology vocabularies.
    
    Rejects bases passed as methodologies (e.g. 'GAAP' or 'STATUTORY').
    """
    if not methodology:
        raise ValuationTaxonomyError("Methodology cannot be empty.")
        
    clean = methodology.strip().upper().replace("-", "").replace("_", "")
    
    # Catch basis passed as methodology
    if clean in EXTENSIBLE_BASES:
        raise ValuationTaxonomyError(
            f"Invalid methodology '{methodology}'. '{methodology}' is an accounting basis, NOT a methodology. "
            f"Methodology must be one of {APPROVED_METHODOLOGIES}."
        )
        
    if clean not in APPROVED_METHODOLOGIES:
        raise ValuationTaxonomyError(
            f"Invalid methodology '{methodology}'. Must be one of {APPROVED_METHODOLOGIES}."
        )
    return clean


def validate_basis_methodology_pair(
    basis: str,
    methodology: str,
    strict_alignment: bool = False,
) -> tuple[str, str]:
    """Validate and normalize a basis and methodology pair.
    
    If strict_alignment is True, requires the basis to match the regulatory primary basis
    (e.g. STATUTORY for VM-21, GAAP for LDTI).
    """
    valid_b = validate_basis(basis)
    valid_m = validate_methodology(methodology)
    
    if (valid_b, valid_m) not in VALID_BASIS_METHODOLOGY_PAIRS:
        raise ValuationTaxonomyError(
            f"Unsupported basis/methodology combination: basis='{valid_b}', methodology='{valid_m}'."
        )
        
    if strict_alignment and valid_b != "TAX":
        expected = METHODOLOGY_PRIMARY_BASIS.get(valid_m)
        if expected and valid_b != expected:
            raise ValuationTaxonomyError(
                f"Strict alignment violation: methodology '{valid_m}' requires primary basis '{expected}', "
                f"but got '{valid_b}'."
            )
            
    return valid_b, valid_m
