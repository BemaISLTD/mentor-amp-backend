"""Taxonomy and validation for accounting bases and actuarial methodologies.

Per CAS §7, Milestone 5 Architecture Invariants, and ACT-DEC decisions:
- Basis and Methodology must NEVER be mixed into a single enum.
- Basis: GAAP, STATUTORY (Launch 1.0 approved); TAX (known future extensible).
- Methodology: VM21, VM22, LDTI, DAC, MRB, ASC815.
- Directive 12: Production validation rejects unapproved basis/methodology pairs;
  TAX is not approved for Launch 1.0 production execution.
"""

from typing import Final, Literal

BasisType = Literal["GAAP", "STATUTORY", "TAX"]
MethodologyType = Literal["VM21", "VM22", "LDTI", "DAC", "MRB", "ASC815"]

# Directive 12: Separate supported launch bases from known/future extensible bases
SUPPORTED_LAUNCH_BASES: Final[tuple[str, ...]] = ("GAAP", "STATUTORY")
KNOWN_FUTURE_BASES: Final[tuple[str, ...]] = ("TAX",)
ALL_KNOWN_BASES: Final[tuple[str, ...]] = SUPPORTED_LAUNCH_BASES + KNOWN_FUTURE_BASES

SUPPORTED_LAUNCH_METHODOLOGIES: Final[tuple[str, ...]] = (
    "VM21",
    "VM22",
    "LDTI",
    "DAC",
    "MRB",
    "ASC815",
)

# Standard regulatory/accounting primary alignment
METHODOLOGY_PRIMARY_BASIS: Final[dict[str, str]] = {
    "VM21": "STATUTORY",
    "VM22": "STATUTORY",
    "LDTI": "GAAP",
    "DAC": "GAAP",
    "MRB": "GAAP",
    "ASC815": "GAAP",
}

# Approved Launch 1.0 basis/methodology combinations
VALID_LAUNCH_PAIRS: Final[set[tuple[str, str]]] = {
    ("STATUTORY", "VM21"),
    ("STATUTORY", "VM22"),
    ("GAAP", "LDTI"),
    ("GAAP", "DAC"),
    ("GAAP", "MRB"),
    ("GAAP", "ASC815"),
}

# Future extensible pairs (architecture-ready, but NOT approved for production launch)
FUTURE_EXTENSIBLE_PAIRS: Final[set[tuple[str, str]]] = {
    ("TAX", "VM21"),
    ("TAX", "VM22"),
    ("TAX", "LDTI"),
    ("TAX", "DAC"),
    ("TAX", "MRB"),
    ("TAX", "ASC815"),
}

# Backwards compatibility alias
VALID_BASIS_METHODOLOGY_PAIRS: Final[set[tuple[str, str]]] = VALID_LAUNCH_PAIRS | FUTURE_EXTENSIBLE_PAIRS
APPROVED_BASES: Final[tuple[str, ...]] = SUPPORTED_LAUNCH_BASES
EXTENSIBLE_BASES: Final[tuple[str, ...]] = ALL_KNOWN_BASES
APPROVED_METHODOLOGIES: Final[tuple[str, ...]] = SUPPORTED_LAUNCH_METHODOLOGIES


class ValuationTaxonomyError(ValueError):
    """Raised when an invalid basis, methodology, or mixed taxonomy is encountered."""
    pass


def normalize_token(val: str) -> str:
    """Normalize input strings by stripping whitespace and uppercase transformation."""
    if not val or not isinstance(val, str):
        raise ValuationTaxonomyError("Value must be a non-empty string.")
    return val.strip().upper().replace("-", "").replace("_", "")


def validate_basis(basis: str, allow_future: bool = False) -> str:
    """Validate that the given basis conforms to approved basis vocabularies.

    Rejects methodologies passed as bases (e.g. 'VM21' or 'LDTI').
    Enforces Directive 12: TAX is rejected for Launch 1.0 production runs unless allow_future=True.
    """
    if not basis:
        raise ValuationTaxonomyError("Basis cannot be empty.")

    clean = basis.strip().upper()

    # Catch methodology passed as basis
    if clean.replace("-", "").replace("_", "") in SUPPORTED_LAUNCH_METHODOLOGIES:
        raise ValuationTaxonomyError(
            f"Invalid basis '{basis}'. '{basis}' is an actuarial methodology, NOT an accounting basis. "
            f"Basis must be one of {SUPPORTED_LAUNCH_BASES}."
        )

    if clean in KNOWN_FUTURE_BASES and not allow_future:
        raise ValuationTaxonomyError(
            f"Basis '{clean}' is recognized as a future extensible basis, but is NOT approved "
            f"for Launch 1.0 production execution. Approved launch bases: {SUPPORTED_LAUNCH_BASES}."
        )

    allowed = ALL_KNOWN_BASES if allow_future else SUPPORTED_LAUNCH_BASES
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
    if clean in ALL_KNOWN_BASES:
        raise ValuationTaxonomyError(
            f"Invalid methodology '{methodology}'. '{methodology}' is an accounting basis, NOT a methodology. "
            f"Methodology must be one of {SUPPORTED_LAUNCH_METHODOLOGIES}."
        )

    if clean not in SUPPORTED_LAUNCH_METHODOLOGIES:
        raise ValuationTaxonomyError(
            f"Invalid methodology '{methodology}'. Must be one of {SUPPORTED_LAUNCH_METHODOLOGIES}."
        )
    return clean


def validate_basis_methodology_pair(
    basis: str,
    methodology: str,
    strict_alignment: bool = False,
    allow_future: bool = False,
) -> tuple[str, str]:
    """Validate and normalize a basis and methodology pair.

    Directive 12: Rejects TAX + VM22 or other TAX combinations by default.
    If strict_alignment is True, requires the basis to match the regulatory primary basis
    (e.g. STATUTORY for VM-21, GAAP for LDTI).
    """
    valid_b = validate_basis(basis, allow_future=allow_future)
    valid_m = validate_methodology(methodology)

    valid_pairs = VALID_LAUNCH_PAIRS | (FUTURE_EXTENSIBLE_PAIRS if allow_future else set())
    if (valid_b, valid_m) not in valid_pairs:
        if valid_b in KNOWN_FUTURE_BASES:
            raise ValuationTaxonomyError(
                f"Basis '{valid_b}' with methodology '{valid_m}' is not approved for Launch 1.0 production execution."
            )
        raise ValuationTaxonomyError(
            f"Unsupported basis/methodology combination: basis='{valid_b}', methodology='{valid_m}'. "
            f"Supported launch combinations: {sorted(VALID_LAUNCH_PAIRS)}"
        )

    if strict_alignment and valid_b != "TAX":
        expected = METHODOLOGY_PRIMARY_BASIS.get(valid_m)
        if expected and valid_b != expected:
            raise ValuationTaxonomyError(
                f"Strict alignment violation: methodology '{valid_m}' requires primary basis '{expected}', "
                f"but got '{valid_b}'."
            )

    return valid_b, valid_m
