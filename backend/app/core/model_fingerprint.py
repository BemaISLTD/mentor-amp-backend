"""Deterministic canonical fingerprinting for whole actuarial model definitions.

Scheme: mentoramp.model_definition/v1

Invariants:
1. Every semantic element affecting model execution is included:
   - ModelVersion calculation-affecting fields: block_name, profile_name, basis, methodology,
     configuration, illustrative, notes
   - Formula definitions: output_variable, function_ref, sorted dependencies, expression_text,
     version, category, basis/product applicability, unit, explanation, effective date,
     governed owner, and test cases
   - Variable definitions: variable_name, kind, data_type, unit, required, default_value,
     source rule, allow_scenario_override, version
   - Published outputs: variable_name, display_name, unit, dimension, aggregation
2. Excludes volatile, non-semantic metadata:
   - Timestamps (created_at, updated_at, validated_at, approved_at, published_at, etc.)
   - Actor IDs (created_by, updated_by, validated_by, approved_by, published_by, etc.)
   - Transient lifecycle state (status, is_current, version_number, parent_version_id)
3. Canonical deterministic sorting:
   - Formulas sorted by output_variable
   - Dependencies per formula sorted by variable name
   - Variable definitions sorted by variable_name
   - Published outputs sorted by variable_name
   - JSON dictionaries sorted by keys recursively
"""

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.formula import FormulaRegistry
from app.db.models.model_variable import ModelVariableDefinition
from app.db.models.modeling import ModelPublishedOutput, ModelVersion

MODEL_DEFINITION_SCHEME = "mentoramp.model_definition/v1"
MODEL_FINGERPRINT_SCHEME = MODEL_DEFINITION_SCHEME


def _canonical_json(data: Any) -> str:
    """Format data as deterministic canonical JSON with sorted keys."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def build_canonical_model_definition(db: Session, model_version_id: str) -> dict[str, Any]:
    """Assemble the canonical dictionary representing the model version's complete definition."""
    version = db.get(ModelVersion, model_version_id)
    if version is None:
        raise ValueError(f"Model version '{model_version_id}' not found.")

    # 1. Model version calculation-affecting fields (strictly semantic/calculation, no actor/notes/transient state)
    version_payload = {
        "block_name": version.block_name or "",
        "profile_name": version.profile_name or "",
        "basis": version.basis or "",
        "methodology": version.methodology or "",
        "illustrative": bool(version.illustrative),
        "configuration": dict(version.configuration or {}),
    }

    # 2. Formulas and dependencies
    formula_rows = (
        db.query(FormulaRegistry)
        .filter(
            FormulaRegistry.model_version_id == model_version_id,
            FormulaRegistry.deleted_at.is_(None),
        )
        .all()
    )
    # Sort deterministically by output_variable, then function_ref
    sorted_formulas = sorted(
        formula_rows, key=lambda f: (f.output_variable or "", f.function_ref or "", f.name or "")
    )
    formulas_payload = []
    for formula in sorted_formulas:
        dep_vars = sorted(dep.depends_on_variable for dep in formula.dependencies if dep.depends_on_variable)
        formulas_payload.append({
            "output_variable": formula.output_variable,
            "function_ref": formula.function_ref,
            "name": formula.name,
            "category": formula.category or "",
            "expression_text": formula.expression_text or "",
            "version": formula.version or "v1",
            "product_applicability": sorted(formula.product_applicability or []),
            "basis_applicability": sorted(formula.basis_applicability or []),
            "dependencies": dep_vars,
            "illustrative": bool(formula.illustrative),
            "explanation": formula.explanation or "",
            "unit": formula.unit or "",
            "effective_date": formula.effective_date.isoformat() if formula.effective_date else None,
            "owner_user_id": formula.owner_user_id,
            "test_cases": list(formula.test_cases or []),
        })

    # 3. Model variable definitions
    variable_rows = (
        db.query(ModelVariableDefinition)
        .filter(ModelVariableDefinition.model_version_id == model_version_id)
        .all()
    )
    sorted_variables = sorted(variable_rows, key=lambda v: v.variable_name or "")
    variables_payload = []
    for var in sorted_variables:
        variables_payload.append({
            "variable_name": var.variable_name,
            "kind": var.kind,
            "data_type": var.data_type,
            "unit": var.unit or "",
            "required": bool(var.required),
            "default_value": var.default_value,
            "source": dict(var.source or {}),
            "allow_scenario_override": bool(var.allow_scenario_override),
            "version": var.version or "v1",
        })

    # 4. Published outputs
    output_rows = (
        db.query(ModelPublishedOutput)
        .filter(ModelPublishedOutput.model_version_id == model_version_id)
        .all()
    )
    sorted_outputs = sorted(output_rows, key=lambda o: o.variable_name or "")
    outputs_payload = []
    for out in sorted_outputs:
        outputs_payload.append({
            "variable_name": out.variable_name,
            "display_name": out.display_name or "",
            "unit": out.unit or "",
            "dimension": out.dimension or "",
            "aggregation": out.aggregation or "sum",
        })

    return {
        "scheme": MODEL_DEFINITION_SCHEME,
        "model_version": version_payload,
        "formulas": formulas_payload,
        "variables": variables_payload,
        "published_outputs": outputs_payload,
    }


def compute_canonical_fingerprint_from_components(
    version_payload: dict[str, Any],
    formulas: list[dict[str, Any]],
    variables: list[dict[str, Any]],
    published_outputs: list[dict[str, Any]],
    scheme: str = MODEL_DEFINITION_SCHEME,
) -> str:
    """Compute deterministic fingerprint from canonical dictionary components."""
    sorted_formulas = sorted(
        formulas,
        key=lambda f: (f.get("output_variable") or "", f.get("function_ref") or "", f.get("name") or "")
    )
    norm_formulas = []
    for f in sorted_formulas:
        deps = sorted(f.get("dependencies") or [])
        norm_formulas.append({
            "output_variable": f.get("output_variable"),
            "function_ref": f.get("function_ref"),
            "name": f.get("name"),
            "category": f.get("category") or "",
            "expression_text": f.get("expression_text") or "",
            "version": f.get("version") or "v1",
            "product_applicability": sorted(f.get("product_applicability") or []),
            "basis_applicability": sorted(f.get("basis_applicability") or []),
            "dependencies": deps,
            "illustrative": bool(f.get("illustrative")),
            "explanation": f.get("explanation") or "",
            "unit": f.get("unit") or "",
            "effective_date": f.get("effective_date"),
            "owner_user_id": f.get("owner_user_id"),
            "test_cases": list(f.get("test_cases") or []),
        })

    sorted_vars = sorted(variables, key=lambda v: v.get("variable_name") or "")
    norm_vars = []
    for v in sorted_vars:
        norm_vars.append({
            "variable_name": v.get("variable_name"),
            "kind": v.get("kind"),
            "data_type": v.get("data_type"),
            "unit": v.get("unit") or "",
            "required": bool(v.get("required")),
            "default_value": v.get("default_value"),
            "source": dict(v.get("source") or {}),
            "allow_scenario_override": bool(v.get("allow_scenario_override")),
            "version": v.get("version") or "v1",
        })

    sorted_outs = sorted(published_outputs, key=lambda o: o.get("variable_name") or "")
    norm_outs = []
    for o in sorted_outs:
        norm_outs.append({
            "variable_name": o.get("variable_name"),
            "display_name": o.get("display_name") or "",
            "unit": o.get("unit") or "",
            "dimension": o.get("dimension") or "",
            "aggregation": o.get("aggregation") or "sum",
        })

    norm_version = {
        "block_name": version_payload.get("block_name") or "",
        "profile_name": version_payload.get("profile_name") or "",
        "basis": version_payload.get("basis") or "",
        "methodology": version_payload.get("methodology") or "",
        "illustrative": bool(version_payload.get("illustrative")),
        "configuration": dict(version_payload.get("configuration") or {}),
    }

    canonical = {
        "scheme": scheme,
        "model_version": norm_version,
        "formulas": norm_formulas,
        "variables": norm_vars,
        "published_outputs": norm_outs,
    }
    serialized = _canonical_json(canonical).encode("utf-8")
    digest = hashlib.sha256(serialized).hexdigest()
    return f"{scheme}:{digest}"


def compute_model_definition_fingerprint(*args: Any, **kwargs: Any) -> str:
    """Compute the deterministic SHA256 hex digest of the canonical model definition.

    Accepts either:
        (db: Session, model_version_id: str, [scheme: str])
    or:
        (version_payload: dict, formulas: list, variables: list, published_outputs: list, [scheme: str])
    """
    if len(args) >= 1 and isinstance(args[0], Session):
        db = args[0]
        model_version_id = args[1]
        scheme = kwargs.get("scheme", args[2] if len(args) > 2 else MODEL_DEFINITION_SCHEME)
        canonical = build_canonical_model_definition(db, model_version_id)
        canonical["scheme"] = scheme
        serialized = _canonical_json(canonical).encode("utf-8")
        digest = hashlib.sha256(serialized).hexdigest()
        return f"{scheme}:{digest}"

    version_payload = args[0] if len(args) > 0 else kwargs.get("version_payload", {})
    formulas = args[1] if len(args) > 1 else kwargs.get("formulas", [])
    variables = args[2] if len(args) > 2 else kwargs.get("variables", [])
    published_outputs = args[3] if len(args) > 3 else kwargs.get("published_outputs", [])
    scheme = kwargs.get("scheme", args[4] if len(args) > 4 else MODEL_DEFINITION_SCHEME)
    return compute_canonical_fingerprint_from_components(
        version_payload, formulas, variables, published_outputs, scheme=scheme
    )

