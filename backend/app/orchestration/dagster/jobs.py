"""Dagster job definitions for the MentorAMP valuation workflow (M5D)."""

from dagster import AssetSelection, define_asset_job

mentoramp_valuation_pipeline_job = define_asset_job(
    name="mentoramp_valuation_pipeline_job",
    selection=AssetSelection.groups("actuarial_pipeline"),
    description="End-to-end governed actuarial calculation and disclosure pipeline.",
)
