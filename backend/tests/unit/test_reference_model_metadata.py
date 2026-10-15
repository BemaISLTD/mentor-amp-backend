from pathlib import Path

from pydantic import ValidationError
import pytest

from app.core.reference_model_metadata import ReferenceModelMetadata
from app.products.spia_reference.metadata import LIFELIB_SPIA_REFERENCE


def test_spia_reference_metadata_is_pinned_and_json_serializable():
    configuration = LIFELIB_SPIA_REFERENCE.as_configuration()

    assert configuration["schema_version"] == "mentoramp.reference_model/v1"
    assert configuration["classification"] == "external_development_benchmark"
    assert configuration["source"]["release"] == "v0.17.2"
    assert configuration["source"]["commit"] == "b706d8bbab530cc9ef94e06600e9c9d1468d8cb1"
    assert configuration["source"]["retrieved_on"] == "2026-10-14"
    assert configuration["runtime_required"] is False
    assert configuration["production_dependency"] is False
    assert configuration["actuarial_status"] == "illustrative_not_approved"
    assert configuration["regulatory_status"] == "not_regulatory_compliant"
    assert "pricing" in configuration["excluded_uses"]
    assert "reserve certification" in configuration["excluded_uses"]
    assert "commutation cash flows" in configuration["compared_outputs"]


@pytest.mark.parametrize("field", ["runtime_required", "production_dependency"])
def test_reference_metadata_rejects_application_dependency(field):
    payload = LIFELIB_SPIA_REFERENCE.as_configuration()
    payload[field] = True

    with pytest.raises(ValidationError):
        ReferenceModelMetadata.model_validate(payload)


def test_lifelib_is_not_a_production_requirement():
    requirements = (Path(__file__).resolve().parents[2] / "requirements.txt").read_text(
        encoding="utf-8"
    ).lower().splitlines()
    packages = {
        line.split("=", 1)[0].split("<", 1)[0].split(">", 1)[0].strip()
        for line in requirements
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "lifelib" not in packages
    assert "modelx" not in packages
