"""Trace collection for analytical artifact storage."""

from app.core.artifacts import RunArtifactBuffer
from app.models.schemas import ProjectionContext, VariableResolutionResult


def log_resolution(
    context: ProjectionContext,
    result: VariableResolutionResult,
    artifact_buffer: RunArtifactBuffer | None = None,
) -> None:
    """Buffer a resolution trace for the run's next Parquet partition flush."""
    if not context.run_id or context.run_id in ("r1", "test", "placeholder"):
        return
    if artifact_buffer is not None:
        artifact_buffer.add_trace(context, result)
