"""External reference provenance validation tests (REF-01 through REF-05).

Verifies that:
- REF-01: External source with no commit -> fails with ValueError.
- REF-02: External source with short commit ("abc") -> fails regex validation.
- REF-03: External source with 40 non-hex characters -> fails regex validation.
- REF-04: External source with valid 40-character hex SHA -> succeeds.
- REF-05: Internal development reference with commit None -> succeeds.
"""

from datetime import date
from pydantic import ValidationError
import pytest

from app.core.reference_model_metadata import ReferenceModelMetadata, ReferenceSource


def _valid_source_kwargs(commit: str | None = None) -> dict:
    return {
        "provider": "Test Provider",
        "library": "test-lib",
        "model": "test-model",
        "release": "1.0.0",
        "commit": commit,
        "retrieved_on": date(2026, 1, 1),
        "documentation_urls": ("https://example.com/docs",),
    }


def _valid_metadata_kwargs(source: ReferenceSource, classification: str) -> dict:
    return {
        "schema_version": "mentoramp.reference_model/v1",
        "classification": classification,
        "source": source,
        "runtime_required": False,
        "production_dependency": False,
        "actuarial_status": "illustrative_not_approved",
        "regulatory_status": "not_regulatory_compliant",
        "compared_outputs": ("reserve", "account_value_close"),
        "excluded_uses": ("pricing", "capital"),
        "limitations": ("development test only",),
    }


def test_ref_01_external_source_with_no_commit_fails():
    """REF-01: External benchmark without a commit SHA is rejected by governance validator."""
    source = ReferenceSource(**_valid_source_kwargs(commit=None))
    with pytest.raises(ValidationError) as exc:
        ReferenceModelMetadata(**_valid_metadata_kwargs(source, "external_development_benchmark"))
    assert "An external development benchmark must have a pinned commit SHA." in str(exc.value)


def test_ref_02_external_source_with_short_commit_fails():
    """REF-02: Commit with fewer than 40 characters fails regex format validation."""
    with pytest.raises(ValidationError) as exc:
        ReferenceSource(**_valid_source_kwargs(commit="abc"))
    assert "String should match pattern" in str(exc.value)


def test_ref_03_external_source_with_40_non_hex_fails():
    """REF-03: 40-character string containing non-hex characters fails regex format validation."""
    with pytest.raises(ValidationError) as exc:
        ReferenceSource(**_valid_source_kwargs(commit="g" * 40))
    assert "String should match pattern" in str(exc.value)


def test_ref_04_external_source_with_valid_40_hex_passes():
    """REF-04: External benchmark with valid 40-character hex SHA passes validation cleanly."""
    valid_sha = "0123456789abcdef0123456789ABCDEF01234567"
    source = ReferenceSource(**_valid_source_kwargs(commit=valid_sha))
    meta = ReferenceModelMetadata(**_valid_metadata_kwargs(source, "external_development_benchmark"))
    assert meta.source.commit == valid_sha
    assert meta.classification == "external_development_benchmark"


def test_ref_05_internal_reference_with_commit_none_passes():
    """REF-05: Internal development reference explicitly allows commit=None."""
    source = ReferenceSource(**_valid_source_kwargs(commit=None))
    meta = ReferenceModelMetadata(
        **_valid_metadata_kwargs(source, "mentoramp_simplified_development_reference")
    )
    assert meta.source.commit is None
    assert meta.classification == "mentoramp_simplified_development_reference"
