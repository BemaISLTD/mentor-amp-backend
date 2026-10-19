"""Validated provenance for external actuarial reference-model benchmarks.

Reference models are development and validation sources only. They are never runtime engines for
MentorAmp runs, and their presence in this metadata must not imply actuarial approval or
regulatory compliance.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReferenceSource(BaseModel):
    """One immutable upstream source identity."""

    model_config = ConfigDict(frozen=True)

    provider: str = Field(min_length=1)
    library: str = Field(min_length=1)
    model: str = Field(min_length=1)
    release: str = Field(min_length=1)
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    retrieved_on: date
    documentation_urls: tuple[str, ...] = Field(min_length=1)


class ReferenceModelMetadata(BaseModel):
    """Governance boundary and comparison scope for an external benchmark."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["mentoramp.reference_model/v1"] = "mentoramp.reference_model/v1"
    classification: Literal[
        "external_development_benchmark", "mentoramp_simplified_development_reference"
    ] = "external_development_benchmark"
    source: ReferenceSource
    runtime_required: bool = False
    production_dependency: bool = False
    actuarial_status: Literal["illustrative_not_approved"] = "illustrative_not_approved"
    regulatory_status: Literal["not_regulatory_compliant"] = "not_regulatory_compliant"
    compared_outputs: tuple[str, ...] = Field(min_length=1)
    excluded_uses: tuple[str, ...] = Field(min_length=1)
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def enforce_external_only_boundary(self):
        if self.runtime_required:
            raise ValueError("An external reference model cannot be required at runtime.")
        if self.production_dependency:
            raise ValueError("An external reference model cannot be a production dependency.")
        return self

    def as_configuration(self) -> dict:
        """Return canonical JSON-compatible metadata for ModelVersion.configuration."""
        return self.model_dump(mode="json")
