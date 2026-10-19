"""Pinned external benchmark metadata for the MentorAmp MYGA reference model."""

from datetime import date

from app.core.reference_model_metadata import ReferenceModelMetadata, ReferenceSource


LIFELIB_MYGA_REFERENCE = ReferenceModelMetadata(
    source=ReferenceSource(
        provider="lifelib Developers",
        library="lifelib uslib",
        model="MYGA_US_S",
        release="v0.17.2",
        commit="5d91f95a7c2b3e8f419b846e273f081492576b5e",
        retrieved_on=date(2026, 10, 18),
        documentation_urls=(
            "https://lifelib.io/libraries/uslib/index.html",
            "https://lifelib.io/libraries/uslib/products/fixed_deferred_annuity/product-spec.html",
            "https://lifelib.io/libraries/uslib/products/fixed_deferred_annuity/technical-notes.html",
            "https://lifelib.io/libraries/uslib/products/fixed_deferred_annuity/model.html",
        ),
    ),
    compared_outputs=(
        "account_value_bom",
        "credited_interest",
        "partial_withdrawals",
        "surrender_charges_incurred",
        "account_value_eom",
        "mgsv_eom",
        "cash_surrender_value_eom",
        "death_benefit_eom",
        "maintenance_expenses",
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
        "The upstream uslib model is a development composite, not an insurer-specific contract.",
        "Market value adjustments (MVA) are optional rider extensions outside the core MYGA definition.",
        "Illustrative assumptions must be replaced by company-approved data for valuation use.",
    ),
)
