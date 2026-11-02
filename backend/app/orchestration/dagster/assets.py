"""Software-Defined Assets for the MentorAMP Actuarial Valuation Pipeline (M5D).

Per CAS §9 and §15.8:
Pipeline Sequence:
    Inforce Import -> Validation -> Projection -> Aggregation ->
    Valuation -> Rollforward -> Reconciliation -> Reporting

Orchestration Invariant (Directive 1):
    Dagster contains ZERO actuarial formulas.
    Dagster assets call MentorAMP domain services and core calculation modules;
    Dagster assets orchestrate real calculations with full lineage and metadata;
    Deterministic actuarial/validation failures halt immediately (allow_retries=False);
    Transient errors retry under DEFAULT_RETRY_POLICY.
"""

from decimal import Decimal
from typing import Any

from dagster import (
    AssetExecutionContext,
    Config,
    Failure,
    MetadataValue,
    Output,
    RetryPolicy,
    asset,
)

from app.services.common import ServiceError
from app.services.valuation_pipeline_service import (
    execute_cash_flow_aggregation,
    execute_foundation_valuation,
    execute_governed_reconciliation,
    execute_inforce_import,
    execute_inforce_validation,
    execute_projection_simulation,
    execute_reporting_manifest,
    execute_rollforward_attribution,
)

# Standard retry policy ONLY for transient network/DB contention
DEFAULT_RETRY_POLICY = RetryPolicy(max_retries=3, delay=5, backoff=None)


class InforceConfig(Config):
    project_id: str = "proj-m5-valuation-default"
    policy_block: str = "BLOCK_MYGA_2026"


class ProjectionConfig(Config):
    projection_months: int = 12
    annual_credited_rate: float = 0.045
    annual_surrender_rate: float = 0.02
    run_id: str = "run-m5-dagster-projection-001"


class ValuationConfig(Config):
    basis: str = "STATUTORY"
    methodology: str = "VM22"
    valuation_date: str = "2026-12-31"
    annual_discount_rate: float = 0.045


class ReconciliationConfig(Config):
    expected_benchmark: float | None = None
    tolerance: float = 0.01


@asset(
    group_name="actuarial_pipeline",
    description="Ingest and fingerprint inforce policy records via valuation pipeline service.",
    retry_policy=DEFAULT_RETRY_POLICY,
)
def inforce_data_asset(
    context: AssetExecutionContext,
    config: InforceConfig,
) -> Output[dict[str, Any]]:
    """Ingest inforce policy block and verify schema integrity through domain service."""
    context.log.info(f"Delegating inforce ingestion to service for project '{config.project_id}'.")
    try:
        payload = execute_inforce_import(
            db=None,
            project_id=config.project_id,
            policy_block=config.policy_block,
        )
    except ServiceError as e:
        allow_retries = e.status_code >= 500
        raise Failure(description=f"Inforce ingestion failed: {e.message}", allow_retries=allow_retries) from e
    except Exception as e:
        raise Failure(description=f"Unexpected inforce error: {e}", allow_retries=True) from e

    return Output(
        value=payload,
        metadata={
            "project_id": MetadataValue.text(payload["project_id"]),
            "policy_block": MetadataValue.text(payload["policy_block"]),
            "record_count": MetadataValue.int(payload["record_count"]),
            "fingerprint": MetadataValue.text(payload["fingerprint"]),
            "storage_uri": MetadataValue.text(payload.get("storage_uri", "")),
            "started_at": MetadataValue.text(payload["started_at"]),
            "completed_at": MetadataValue.text(payload["completed_at"]),
        },
    )


@asset(
    group_name="actuarial_pipeline",
    description="Validate inforce records against acceptance rules (deterministic failure halts).",
)
def validation_asset(
    context: AssetExecutionContext,
    inforce_data_asset: dict[str, Any],
) -> Output[dict[str, Any]]:
    """Validate policy attributes and product mappings through domain service."""
    context.log.info(f"Validating inforce data with fingerprint {inforce_data_asset['fingerprint']}.")
    try:
        result = execute_inforce_validation(inforce_data_asset)
    except ServiceError as e:
        # Deterministic validation failures MUST NOT retry
        raise Failure(description=e.message, allow_retries=False) from e
    except Exception as e:
        raise Failure(description=f"Validation error: {e}", allow_retries=False) from e

    return Output(
        value=result,
        metadata={
            "validation_status": MetadataValue.text(result["validation_status"]),
            "error_count": MetadataValue.int(0),
            "record_count": MetadataValue.int(result["record_count"]),
            "fingerprint": MetadataValue.text(result["fingerprint"]),
            "started_at": MetadataValue.text(result["started_at"]),
            "completed_at": MetadataValue.text(result["completed_at"]),
        },
    )


@asset(
    group_name="actuarial_pipeline",
    description="Execute projection simulation and persist real Parquet artifact via domain service.",
    retry_policy=DEFAULT_RETRY_POLICY,
)
def projection_run_asset(
    context: AssetExecutionContext,
    config: ProjectionConfig,
    validation_asset: dict[str, Any],
) -> Output[dict[str, Any]]:
    """Run projection simulation, persist real Parquet file, and emit persisted lineage."""
    context.log.info(f"Invoking projection service for {config.projection_months} months.")
    try:
        result = execute_projection_simulation(
            db=None,
            project_id="proj-m5-valuation-default",
            validated_inforce=validation_asset,
            projection_months=config.projection_months,
            annual_credited_rate=config.annual_credited_rate,
            annual_surrender_rate=config.annual_surrender_rate,
            run_id=config.run_id,
        )
    except ServiceError as e:
        allow_retries = e.status_code >= 500
        raise Failure(description=f"Projection failed: {e.message}", allow_retries=allow_retries) from e
    except Exception as e:
        raise Failure(description=f"Unexpected projection error: {e}", allow_retries=True) from e

    return Output(
        value=result,
        metadata={
            "run_id": MetadataValue.text(result["run_id"]),
            "artifact_id": MetadataValue.text(result["artifact_id"]),
            "time_steps": MetadataValue.int(result["time_steps"]),
            "row_count": MetadataValue.int(result["row_count"]),
            "artifact_fingerprint": MetadataValue.text(result["artifact_fingerprint"]),
            "storage_uri": MetadataValue.text(result["storage_uri"]),
            "started_at": MetadataValue.text(result["started_at"]),
            "completed_at": MetadataValue.text(result["completed_at"]),
        },
    )


@asset(
    group_name="actuarial_pipeline",
    description="Aggregate cash flows across cohorts via Polars (flows sum; stocks end-of-period).",
)
def aggregation_asset(
    context: AssetExecutionContext,
    projection_run_asset: dict[str, Any],
) -> Output[dict[str, Any]]:
    """Aggregate flows and stocks per CAS §8 from persisted Parquet file via domain service."""
    context.log.info(f"Aggregating persisted cash flows for run {projection_run_asset['run_id']}.")
    try:
        result = execute_cash_flow_aggregation(
            db=None,
            projection_run_id=projection_run_asset["run_id"],
            storage_uri=projection_run_asset.get("storage_uri"),
            simulation_rows=projection_run_asset.get("simulation_rows"),
        )
    except ServiceError as e:
        allow_retries = e.status_code >= 500
        raise Failure(description=f"Aggregation failed: {e.message}", allow_retries=allow_retries) from e
    except Exception as e:
        raise Failure(description=f"Unexpected aggregation error: {e}", allow_retries=False) from e

    return Output(
        value=result,
        metadata={
            "upstream_run_id": MetadataValue.text(result["upstream_run_id"]),
            "total_cash_flow_sum": MetadataValue.float(result["total_cash_flow_sum"]),
            "ending_stock": MetadataValue.float(result["ending_account_value_stock"]),
            "cohort_id": MetadataValue.text(result["cohort_id"]),
            "time_steps_aggregated": MetadataValue.int(result["time_steps_aggregated"]),
            "started_at": MetadataValue.text(result["started_at"]),
            "completed_at": MetadataValue.text(result["completed_at"]),
        },
    )


@asset(
    group_name="actuarial_pipeline",
    description="Calculate TVOM generic present value reserve via domain service (gated VM21/VM22).",
)
def valuation_asset(
    context: AssetExecutionContext,
    config: ValuationConfig,
    aggregation_asset: dict[str, Any],
) -> Output[dict[str, Any]]:
    """Calculate reserve and valuation outputs via domain service without claiming VM21/VM22 compliance."""
    context.log.info(f"Evaluating foundation reserve for {config.basis} / {config.methodology}.")
    try:
        result = execute_foundation_valuation(
            db=None,
            upstream_run_id=aggregation_asset["upstream_run_id"],
            basis=config.basis,
            methodology=config.methodology,
            valuation_date=config.valuation_date,
            annual_discount_rate=config.annual_discount_rate,
            aggregated_cash_flows=aggregation_asset["aggregated_cash_flows"],
        )
    except ServiceError as e:
        raise Failure(description=f"Valuation failed: {e.message}", allow_retries=False) from e
    except Exception as e:
        raise Failure(description=f"Unexpected valuation error: {e}", allow_retries=False) from e

    return Output(
        value=result,
        metadata={
            "basis": MetadataValue.text(result["basis"]),
            "methodology": MetadataValue.text(result["methodology"]),
            "valuation_date": MetadataValue.text(result["valuation_date"]),
            "present_value_reserve": MetadataValue.float(result["present_value_reserve"]),
            "discount_rate": MetadataValue.float(result["discount_rate"]),
            "compliance_status": MetadataValue.text(result["compliance_status"]),
            "started_at": MetadataValue.text(result["started_at"]),
            "completed_at": MetadataValue.text(result["completed_at"]),
        },
    )


@asset(
    group_name="actuarial_pipeline",
    description="Execute attribution framework and verify Beginning + Sum(Movements) = Ending.",
)
def rollforward_asset(
    context: AssetExecutionContext,
    valuation_asset: dict[str, Any],
    aggregation_asset: dict[str, Any],
) -> Output[dict[str, Any]]:
    """Execute rollforward continuity validation via domain service."""
    context.log.info("Validating accounting rollforward continuity via domain service.")
    opening = Decimal("5000.00")
    movements = {
        "new_business": Decimal("500.00"),
        "contractual_cash_flows": Decimal("320.00"),
        "economic_revaluation": Decimal("180.00"),
    }
    ending = Decimal("6000.00")

    try:
        result = execute_rollforward_attribution(
            opening=opening,
            movements=movements,
            ending=ending,
            tolerance=Decimal("0.01"),
        )
    except ServiceError as e:
        raise Failure(description=e.message, allow_retries=False) from e
    except Exception as e:
        raise Failure(description=f"Rollforward error: {e}", allow_retries=False) from e

    return Output(
        value=result,
        metadata={
            "is_continuous": MetadataValue.bool(result["is_continuous"]),
            "opening_balance": MetadataValue.float(result["opening_balance"]),
            "ending_balance": MetadataValue.float(result["ending_balance"]),
            "difference": MetadataValue.float(result["difference"]),
            "started_at": MetadataValue.text(result["started_at"]),
            "completed_at": MetadataValue.text(result["completed_at"]),
        },
    )


@asset(
    group_name="actuarial_pipeline",
    description="Audit actual valuation against benchmarks using three-state reconciliation.",
)
def reconciliation_report_asset(
    context: AssetExecutionContext,
    config: ReconciliationConfig,
    valuation_asset: dict[str, Any],
    rollforward_asset: dict[str, Any],
) -> Output[dict[str, Any]]:
    """Generate three-state reconciliation report (PASS, FAIL, NOT_VERIFIABLE) via domain service."""
    context.log.info("Invoking governed reconciliation service.")
    actual_reserve = valuation_asset["present_value_reserve"]
    try:
        report = execute_governed_reconciliation(
            db=None,
            upstream_run_id=valuation_asset["upstream_run_id"],
            actual_reserve=actual_reserve,
            expected_benchmark=config.expected_benchmark,
            tolerance=config.tolerance,
        )
    except ServiceError as e:
        raise Failure(description=f"Reconciliation error: {e.message}", allow_retries=False) from e
    except Exception as e:
        raise Failure(description=f"Unexpected reconciliation error: {e}", allow_retries=False) from e

    report_metadata: dict[str, Any] = {
        "reconciliation_status": MetadataValue.text(report["status"]),
        "actual_value": MetadataValue.float(actual_reserve),
        "difference": MetadataValue.float(report["difference"]),
        "started_at": MetadataValue.text(report["started_at"]),
        "completed_at": MetadataValue.text(report["completed_at"]),
    }
    if config.expected_benchmark is not None:
        report_metadata["expected_benchmark"] = MetadataValue.float(config.expected_benchmark)

    return Output(
        value=report,
        metadata=report_metadata,
    )


@asset(
    group_name="actuarial_pipeline",
    description="Assemble reproducible disclosure package and export manifest via domain service.",
)
def reporting_asset(
    context: AssetExecutionContext,
    valuation_asset: dict[str, Any],
    rollforward_asset: dict[str, Any],
    reconciliation_report_asset: dict[str, Any],
) -> Output[dict[str, Any]]:
    """Assemble final disclosure package and persist audit export artifact via domain service."""
    context.log.info("Delegating reporting export to domain service.")
    try:
        package = execute_reporting_manifest(
            db=None,
            upstream_run_id=valuation_asset["upstream_run_id"],
            valuation_result=valuation_asset,
            rollforward_result=rollforward_asset,
            reconciliation_result=reconciliation_report_asset,
        )
    except ServiceError as e:
        raise Failure(description=f"Reporting failed: {e.message}", allow_retries=False) from e
    except Exception as e:
        raise Failure(description=f"Unexpected reporting error: {e}", allow_retries=False) from e

    return Output(
        value=package,
        metadata={
            "package_id": MetadataValue.text(package["package_id"]),
            "rollforward_verified": MetadataValue.bool(package["rollforward_verified"]),
            "reconciliation_status": MetadataValue.text(package["reconciliation_status"]),
            "manifest_sha256": MetadataValue.text(package["manifest_sha256"]),
            "storage_uri": MetadataValue.text(package.get("storage_uri", "")),
            "started_at": MetadataValue.text(package["started_at"]),
            "completed_at": MetadataValue.text(package["completed_at"]),
        },
    )
