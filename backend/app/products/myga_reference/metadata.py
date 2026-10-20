"""Development reference metadata for the MentorAmp MYGA model.

Tolerance Hierarchy:
- INTERNAL_ARITHMETIC_PRECISION (1e-12): Used for exact mathematical identity checks,
  closed-form recursion verification, and unit tests within the reference package.
- ENGINE_CROSSCHECK_TOLERANCE (1e-6): Used for reconciliation against Noah's projection
  engine output to allow for floating-point noise and SIMD/vectorized compiler differences.
"""

from datetime import date

from app.core.reference_model_metadata import ReferenceModelMetadata, ReferenceSource

# Tolerance standards for MYGA development reference vs engine reconciliation
INTERNAL_ARITHMETIC_PRECISION: float = 1e-12
ENGINE_CROSSCHECK_TOLERANCE: float = 1e-6

MYGA_DEVELOPMENT_REFERENCE = ReferenceModelMetadata(
    classification="mentoramp_simplified_development_reference",
    source=ReferenceSource(
        provider="MentorAmp Development Team",
        library="mentoramp-actuarial-reference",
        model="MYGA_SIMPLIFIED_DEVELOPMENT_REFERENCE",
        release="v1.0",
        commit=None,  # Not generated from an external Git commit
        retrieved_on=date(2026, 10, 18),
        generated_by="MentorAmp Development Team",
        reference_type="internal_development_reference",
        documentation_urls=(
            # Background / reference documentation (not fixture provenance)
            "https://lifelib.io/libraries/uslib/index.html",
            "https://lifelib.io/libraries/uslib/products/fixed_deferred_annuity/product-spec.html",
            "https://lifelib.io/libraries/uslib/products/fixed_deferred_annuity/technical-notes.html",
        ),
    ),
    compared_outputs=(
        "account_value_bom",
        "credited_interest",
        "gross_withdrawal",
        "surrender_charge",
        "net_withdrawal_benefit",
        "account_value_eom",
        "mgsv_eom",
        "cash_surrender_value_eom",
        "death_benefit_eom",
        "maintenance_expense",
        "net_insurer_cash_flow",
    ),
    excluded_uses=(
        "pricing",
        "reserve certification",
        "capital calculation",
        "regulatory compliance claims",
        "production run execution",
    ),
    limitations=(
        "This fixture is an internally generated development reference, not an independent LifeLib oracle run.",
        "Market Value Adjustment (MVA) is not modeled in this development reference.",
        "MGSV nonforfeiture accumulation rate is an illustrative contractual assumption, not a verified statutory derivation under Model #805.",
        "Surrender charge and waiver schedules are illustrative contractual examples.",
    ),
)

# Backwards compatibility alias
LIFELIB_MYGA_REFERENCE = MYGA_DEVELOPMENT_REFERENCE
