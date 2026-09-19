"""Builds ProjectionContext for each step of the projection loop."""

from app.models.schemas import ProjectionContext, ProjectionRunDefinition


def build_context(
    run_def: ProjectionRunDefinition,
    policy_data: dict,
    policy_id: str,
    scenario_id: str,
    month: int,
) -> ProjectionContext:
    """Create a ProjectionContext for a specific policy/scenario/month step.

    Args:
        run_def: The run configuration
        policy_data: The policy's inforce data dict
        policy_id: The policy's unique ID
        scenario_id: The scenario being run
        month: 1-based projection month (1 = first month)

    Returns:
        A fully populated ProjectionContext
    """
    # Calculate derived fields
    base_age = None
    if policy_data and isinstance(policy_data, dict):
        raw_age = policy_data.get("issue_age") or policy_data.get("age")
        if raw_age is not None:
            try:
                base_age = int(float(str(raw_age)))
            except (ValueError, TypeError):
                pass

    attained_age = None
    if base_age is not None:
        # Convert months to years for age calculation
        years_elapsed = (month - 1) / 12.0
        attained_age = int(base_age + years_elapsed)

    return ProjectionContext(
        project_id=run_def.project_id,
        run_id=run_def.id,
        product=policy_data.get("product_type", "") if policy_data else "",
        policy_id=policy_id,
        scenario_id=scenario_id,
        projection_month=month,
        duration=month,
        attained_age=attained_age,
        assumption_set=None,
        factor_set=None,
        scenario_set=None,
        projection_key=None,
    )
