"""Converts formula dependencies into GraphNode/GraphEdge for UI rendering."""

import uuid

from app.models.schemas import DependencyGraphView, FormulaDefinition, GraphEdge, GraphNode


def to_graph_view(formulas: list[FormulaDefinition]) -> DependencyGraphView:
    """Build a DependencyGraphView from a list of formulas."""
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []

    all_vars: set[str] = set()
    for f in formulas:
        all_vars.add(f.output_variable)
        all_vars.update(f.dependencies)

    formula_outputs = {f.output_variable for f in formulas}

    for var in all_vars:
        kind = "formula" if var in formula_outputs else "input"
        nodes[var] = GraphNode(id=var, label=var, kind=kind, status="valid")

    for f in formulas:
        for dep in f.dependencies:
            edges.append(
                GraphEdge(
                    id=str(uuid.uuid4()),
                    source=dep,
                    target=f.output_variable,
                )
            )

    return DependencyGraphView(nodes=list(nodes.values()), edges=edges)
