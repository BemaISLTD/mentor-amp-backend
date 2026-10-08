"""Build and persist immutable snapshots of projection run inputs."""

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models.formula import FormulaRegistry
from app.db.models.inforce import InforceFile
from app.db.models.run_artifact import RunManifest
from app.db.models.variable import VariableRegistry
from app.models.schemas import ProjectionRunDefinition


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def build_manifest(db: Session, run_def: ProjectionRunDefinition) -> dict[str, Any]:
    files = (
        db.query(InforceFile)
        .filter(InforceFile.id.in_(run_def.dataset_ids))
        .order_by(InforceFile.id)
        .all()
        if run_def.dataset_ids
        else []
    )
    formulas = db.query(FormulaRegistry).filter(
        FormulaRegistry.deleted_at.is_(None)
    ).order_by(FormulaRegistry.id).all()
    variables = db.query(VariableRegistry).filter(
        VariableRegistry.deleted_at.is_(None)
    ).order_by(VariableRegistry.id).all()
    return {
        "schema_version": "v1",
        "code_version": settings.code_version,
        "run_definition": run_def.model_dump(mode="json"),
        "datasets": [
            {
                "id": item.id,
                "filename": item.filename,
                "file_type": item.file_type,
                "row_count": item.row_count,
                "uploaded_at": item.uploaded_at.isoformat(),
            }
            for item in files
        ],
        "formulas": [
            {
                "id": formula.id,
                "output_variable": formula.output_variable,
                "function_ref": formula.function_ref,
                "version": formula.version,
                "status": formula.status,
                "dependencies": sorted(
                    dependency.depends_on_variable
                    for dependency in formula.dependencies
                ),
            }
            for formula in formulas
        ],
        "variables": [
            {"id": variable.id, "name": variable.name, "version": variable.version}
            for variable in variables
        ],
    }


def create_run_manifest(
    db: Session, run_def: ProjectionRunDefinition
) -> RunManifest:
    if db.query(RunManifest).filter(RunManifest.run_id == run_def.id).first():
        raise ValueError(f"Run manifest for '{run_def.id}' already exists.")
    manifest = build_manifest(db, run_def)
    fingerprint = hashlib.sha256(_canonical_json(manifest).encode("utf-8")).hexdigest()
    record = RunManifest(
        run_id=run_def.id,
        fingerprint=fingerprint,
        manifest=manifest,
    )
    db.add(record)
    db.flush()
    return record
