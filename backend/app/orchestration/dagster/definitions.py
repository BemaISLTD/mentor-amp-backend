"""Dagster Definitions entrypoint for MentorAMP (M5D)."""

from dagster import Definitions

from app.orchestration.dagster.assets import (
    aggregation_asset,
    inforce_data_asset,
    projection_run_asset,
    reconciliation_report_asset,
    reporting_asset,
    rollforward_asset,
    validation_asset,
    valuation_asset,
)
from app.orchestration.dagster.jobs import mentoramp_valuation_pipeline_job

defs = Definitions(
    assets=[
        inforce_data_asset,
        validation_asset,
        projection_run_asset,
        aggregation_asset,
        valuation_asset,
        rollforward_asset,
        reconciliation_report_asset,
        reporting_asset,
    ],
    jobs=[
        mentoramp_valuation_pipeline_job,
    ],
)
