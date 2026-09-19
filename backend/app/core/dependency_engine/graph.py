"""Dependency graph — builds DAG, topo sort, detects cycles and missing deps."""

from app.models.schemas import DependencyError, FormulaDefinition


def build_adjacency(formulas: list[FormulaDefinition]) -> dict[str, set[str]]:
    """Build adjacency list: output_variable → set of dependency variable names."""
    graph: dict[str, set[str]] = {}
    for f in formulas:
        graph[f.output_variable] = set(f.dependencies)
    return graph


def topological_sort(formulas: list[FormulaDefinition]) -> list[str]:
    """Return variables in topological order (dependencies first).
    
    Raises ValueError with cycle path if circular dependency detected.
    """
    graph = build_adjacency(formulas)
    all_vars: set[str] = set()
    for output, deps in graph.items():
        all_vars.add(output)
        all_vars.update(deps)

    visited: set[str] = set()
    in_progress: set[str] = set()
    order: list[str] = []

    def visit(node: str, path: list[str]) -> None:
        if node in visited:
            return
        if node in in_progress:
            cycle_start = path.index(node)
            cycle = path[cycle_start:] + [node]
            raise ValueError(f"Circular dependency: {' → '.join(cycle)}")
        in_progress.add(node)
        for dep in graph.get(node, set()):
            visit(dep, path + [node])
        in_progress.discard(node)
        visited.add(node)
        order.append(node)

    # Visit formula outputs first, then orphan dependencies
    for formula in formulas:
        visit(formula.output_variable, [])

    # Add any variables that are dependencies but not formula outputs
    for var in all_vars:
        if var not in visited:
            visited.add(var)
            order.append(var)

    return order


def detect_circular_dependencies(formulas: list[FormulaDefinition]) -> list[DependencyError]:
    """Return list of circular dependency errors found in the graph."""
    try:
        topological_sort(formulas)
        return []
    except ValueError as e:
        msg = str(e)
        path_str = msg.replace("Circular dependency: ", "")
        variables = [v.strip() for v in path_str.split("→")]
        return [
            DependencyError(
                type="circular_dependency",
                variable_id=variables[0] if variables else "",
                message=msg,
                path=variables,
            )
        ]


def detect_missing_dependencies(
    formulas: list[FormulaDefinition],
    registered_variables: set[str],
) -> list[DependencyError]:
    """Return errors for any dependency not in the registered variables set."""
    errors: list[DependencyError] = []
    for f in formulas:
        for dep in f.dependencies:
            if dep not in registered_variables:
                errors.append(
                    DependencyError(
                        type="missing_dependency",
                        variable_id=dep,
                        message=f"Formula '{f.name}' depends on '{dep}', which is not registered.",
                        path=[f.output_variable, dep],
                    )
                )
    return errors


def detect_duplicate_outputs(formulas: list[FormulaDefinition]) -> list[DependencyError]:
    """Return errors if multiple formulas claim the same output variable."""
    seen: dict[str, list[str]] = {}
    for f in formulas:
        seen.setdefault(f.output_variable, []).append(f.name)

    errors: list[DependencyError] = []
    for var, names in seen.items():
        if len(names) > 1:
            errors.append(
                DependencyError(
                    type="missing_dependency",  # closest match from our enums
                    variable_id=var,
                    message=f"Duplicate output '{var}' produced by formulas: {', '.join(names)}.",
                    path=[var],
                )
            )
    return errors


def validate_dependencies(
    formulas: list[FormulaDefinition],
    registered_variables: set[str],
) -> list[DependencyError]:
    """Run all dependency validation checks. Returns list of all errors found."""
    errors: list[DependencyError] = []
    errors.extend(detect_circular_dependencies(formulas))
    errors.extend(detect_missing_dependencies(formulas, registered_variables))
    errors.extend(detect_duplicate_outputs(formulas))
    return errors
