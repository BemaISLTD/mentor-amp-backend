"""Pinned external benchmark metadata for the MentorAmp SPIA reference model."""

from datetime import date

from app.core.reference_model_metadata import ReferenceModelMetadata, ReferenceSource


LIFELIB_SPIA_REFERENCE = ReferenceModelMetadata(
    source=ReferenceSource(
        provider="lifelib Developers",
        library="lifelib uslib",
        model="SPIA_US_S",
        release="v0.17.2",
        commit="b706d8bbab530cc9ef94e06600e9c9d1468d8cb1",
        retrieved_on=date(2026, 10, 14),
        documentation_urls=(
            "https://lifelib.io/libraries/uslib/index.html",
            "https://lifelib.io/libraries/uslib/products/immediate_annuity/product-spec.html",
            "https://lifelib.io/libraries/uslib/products/immediate_annuity/technical-notes.html",
            "https://lifelib.io/libraries/uslib/products/immediate_annuity/model.html",
        ),
    ),
    compared_outputs=(
        "survival probabilities",
        "payment factors",
        "annuity cash flows",
        "refund cash flows",
        "commutation cash flows",
        "maintenance expenses",
        "gross liability cash flow",
        "net cash flow",
    ),
    excluded_uses=(
        "pricing",
        "reserve certification",
        "capital calculation",
        "regulatory compliance claims",
        "production run execution",
    ),
    limitations=(
        "The upstream uslib model is a draft standardized composite, not an insurer product.",
        "Public payout factors and pricing bases are unavailable; annual income is an input.",
        "The upstream SPIA model does not calculate discounting or reserves.",
        "Illustrative assumptions must be replaced by approved company data for production use.",
    ),
)
