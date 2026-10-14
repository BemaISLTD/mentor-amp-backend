"""Governed rollforward, report, derived-dataset, and run-step metadata."""

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.project_lifecycle import require_active_project
from app.db.models.governance import (
    DerivedDataset,
    Report,
    RollforwardJob,
    RollforwardStep,
    RollforwardTemplate,
    RunStep,
)
from app.db.models.modeling import Model, ModelVersion
from app.db.models.run import Run
from app.services import access
from app.services.common import ServiceError, conflict, iso, not_found, now_utc


def _serialize(row: Any) -> dict[str, Any]:
    return {
        column.name: iso(value) if column.name.endswith("_at") or column.name.endswith("_date") else value
        for column in row.__table__.columns
        if (value := getattr(row, column.name)) is not None
    }


def _state(row: Any) -> dict[str, Any]:
    return {key: value for key, value in _serialize(row).items() if key not in {"created_at", "updated_at"}}


def _audit_state(db: Session, row: Any) -> dict[str, Any]:
    state = _state(row)
    if isinstance(row, RollforwardTemplate):
        steps = db.query(RollforwardStep).filter(
            RollforwardStep.template_id == row.id, RollforwardStep.job_id.is_(None),
            RollforwardStep.status != "removed",
        ).order_by(RollforwardStep.sequence).all()
        state["steps"] = [_state(step) for step in steps]
    elif isinstance(row, RollforwardJob):
        steps = db.query(RollforwardStep).filter(
            RollforwardStep.job_id == row.id
        ).order_by(RollforwardStep.sequence).all()
        state["steps"] = [_state(step) for step in steps]
    return state


def _active(db: Session, model: type, object_id: str, label: str):
    query = db.query(model).filter(model.id == object_id)
    if hasattr(model, "deleted_at"):
        query = query.filter(model.deleted_at.is_(None))
    row = query.first()
    if row is None:
        raise not_found(f"{label} '{object_id}' not found.")
    return row


def _audit(db: Session, user: Any, action: str, row: Any, before: dict | None = None,
           context: dict | None = None) -> None:
    record_audit(
        db, actor_user_id=user.id, action=action,
        entity_type=row.__tablename__.removesuffix("s"), entity_id=row.id,
        before_state=before, after_state=_audit_state(db, row), context=context,
    )


def _project_write(db: Session, user: Any, project_id: str) -> None:
    access.require_project_access(db, user, project_id, write=True)
    require_active_project(db, project_id)


def list_templates(db: Session, project_id: str) -> list[dict[str, Any]]:
    return [_serialize(row) for row in db.query(RollforwardTemplate).filter(
        RollforwardTemplate.project_id == project_id, RollforwardTemplate.deleted_at.is_(None)
    ).order_by(RollforwardTemplate.name, RollforwardTemplate.version_number.desc()).all()]


def template_detail(db: Session, template_id: str) -> dict[str, Any]:
    row = _active(db, RollforwardTemplate, template_id, "Rollforward template")
    result = _serialize(row)
    result["steps"] = [_serialize(step) for step in db.query(RollforwardStep).filter(
        RollforwardStep.template_id == row.id, RollforwardStep.job_id.is_(None),
        RollforwardStep.status != "removed",
    ).order_by(RollforwardStep.sequence).all()]
    return result


def create_template(db: Session, project_id: str, payload: dict[str, Any], user: Any) -> dict[str, Any]:
    _project_write(db, user, project_id)
    version = db.get(ModelVersion, payload["model_version_id"])
    model = db.get(Model, version.model_id) if version else None
    if model is None or model.project_id != project_id or version.deleted_at is not None:
        raise ServiceError(422, "CROSS_PROJECT_REFERENCE", "Model version was not found in this project.")
    row = RollforwardTemplate(
        project_id=project_id, name=payload["name"], description=payload.get("description"),
        model_version_id=version.id, configuration=payload.get("configuration") or {},
        created_by=user.id, updated_by=user.id,
    )
    db.add(row)
    db.flush()
    for index, step in enumerate(payload.get("steps") or [], start=1):
        db.add(RollforwardStep(
            template_id=row.id, step_key=step["step_key"], name=step["name"],
            step_type=step["step_type"], sequence=step.get("sequence") or index,
            configuration=step.get("configuration") or {}, created_by=user.id, updated_by=user.id,
        ))
    db.flush()
    _audit(db, user, "rollforward_template.created", row)
    db.commit()
    return template_detail(db, row.id)


def update_template(db: Session, template_id: str, payload: dict[str, Any], user: Any) -> dict[str, Any]:
    row = _active(db, RollforwardTemplate, template_id, "Rollforward template")
    _project_write(db, user, row.project_id)
    if row.status == "published":
        raise conflict("Published templates are immutable; create a new version.")
    before = _audit_state(db, row)
    for field in ("name", "description", "configuration"):
        if field in payload:
            setattr(row, field, payload[field])
    row.updated_by = user.id
    if "steps" in payload:
        existing = {step.step_key: step for step in db.query(RollforwardStep).filter(
            RollforwardStep.template_id == row.id, RollforwardStep.job_id.is_(None)
        ).all()}
        retained: set[str] = set()
        for index, step in enumerate(payload["steps"] or [], start=1):
            retained.add(step["step_key"])
            stored = existing.get(step["step_key"])
            if stored is None:
                stored = RollforwardStep(
                    template_id=row.id, step_key=step["step_key"], created_by=user.id,
                )
                db.add(stored)
            stored.name = step["name"]
            stored.step_type = step["step_type"]
            stored.sequence = step.get("sequence") or index
            stored.configuration = step.get("configuration") or {}
            stored.status = "draft"
            stored.updated_by = user.id
        for key, stored in existing.items():
            if key not in retained:
                stored.status = "removed"
                stored.updated_by = user.id
    db.flush()
    _audit(db, user, "rollforward_template.updated", row, before)
    db.commit()
    return template_detail(db, row.id)


def version_template(db: Session, template_id: str, user: Any) -> dict[str, Any]:
    source = _active(db, RollforwardTemplate, template_id, "Rollforward template")
    _project_write(db, user, source.project_id)
    number = (db.query(func.max(RollforwardTemplate.version_number)).filter(
        RollforwardTemplate.project_id == source.project_id,
        RollforwardTemplate.name == source.name,
    ).scalar() or 0) + 1
    row = RollforwardTemplate(
        project_id=source.project_id, name=source.name, description=source.description,
        version_number=number, parent_template_id=source.id,
        model_version_id=source.model_version_id, configuration=dict(source.configuration or {}),
        created_by=user.id, updated_by=user.id,
    )
    db.add(row)
    db.flush()
    for step in db.query(RollforwardStep).filter(
        RollforwardStep.template_id == source.id, RollforwardStep.job_id.is_(None),
        RollforwardStep.status != "removed",
    ).all():
        db.add(RollforwardStep(
            template_id=row.id, template_step_id=step.id, step_key=step.step_key, name=step.name,
            step_type=step.step_type, sequence=step.sequence,
            configuration=dict(step.configuration or {}), created_by=user.id, updated_by=user.id,
        ))
    db.flush()
    _audit(db, user, "rollforward_template.version_created", row,
           context={"parent_template_id": source.id})
    db.commit()
    return template_detail(db, row.id)


def publish_template(db: Session, template_id: str, user: Any) -> dict[str, Any]:
    row = _active(db, RollforwardTemplate, template_id, "Rollforward template")
    _project_write(db, user, row.project_id)
    before = _audit_state(db, row)
    row.status = "published"
    row.published_by = user.id
    row.published_at = now_utc()
    row.updated_by = user.id
    db.flush()
    _audit(db, user, "rollforward_template.published", row, before)
    db.commit()
    return template_detail(db, row.id)


def archive_template(db: Session, template_id: str, user: Any) -> None:
    row = _active(db, RollforwardTemplate, template_id, "Rollforward template")
    _project_write(db, user, row.project_id)
    before = _audit_state(db, row)
    row.status = "archived"
    row.deleted_at = now_utc()
    row.deleted_by = row.updated_by = user.id
    db.flush()
    _audit(db, user, "rollforward_template.archived", row, before, {"deletion": "soft"})
    db.commit()


def create_job(db: Session, project_id: str, payload: dict[str, Any], user: Any) -> dict[str, Any]:
    _project_write(db, user, project_id)
    template = _active(db, RollforwardTemplate, payload["template_id"], "Rollforward template")
    if template.project_id != project_id:
        raise ServiceError(422, "CROSS_PROJECT_REFERENCE", "Template was not found in this project.")
    if template.status != "published":
        raise conflict("A rollforward job requires a published template.")
    if payload["to_date"] < payload["from_date"]:
        raise ServiceError(422, "VALIDATION_ERROR", "to_date must not precede from_date.")
    row = RollforwardJob(
        project_id=project_id, template_id=template.id, name=payload["name"],
        from_date=payload["from_date"], to_date=payload["to_date"],
        parameters=payload.get("parameters") or {}, created_by=user.id, updated_by=user.id,
    )
    db.add(row)
    db.flush()
    for step in db.query(RollforwardStep).filter(
        RollforwardStep.template_id == template.id, RollforwardStep.job_id.is_(None),
        RollforwardStep.status != "removed",
    ).all():
        db.add(RollforwardStep(
            template_id=template.id, job_id=row.id, template_step_id=step.id,
            step_key=step.step_key, name=step.name, step_type=step.step_type,
            sequence=step.sequence, status="pending", configuration=dict(step.configuration or {}),
            created_by=user.id, updated_by=user.id,
        ))
    db.flush()
    _audit(db, user, "rollforward_job.created", row)
    db.commit()
    return job_detail(db, row.id)


def job_detail(db: Session, job_id: str) -> dict[str, Any]:
    row = _active(db, RollforwardJob, job_id, "Rollforward job")
    result = _serialize(row)
    result["steps"] = [_serialize(step) for step in db.query(RollforwardStep).filter(
        RollforwardStep.job_id == row.id
    ).order_by(RollforwardStep.sequence).all()]
    return result


def list_jobs(db: Session, project_id: str) -> list[dict[str, Any]]:
    return [job_detail(db, row.id) for row in db.query(RollforwardJob).filter(
        RollforwardJob.project_id == project_id
    ).order_by(RollforwardJob.created_at.desc()).all()]


def update_job(db: Session, job_id: str, payload: dict[str, Any], user: Any) -> dict[str, Any]:
    row = _active(db, RollforwardJob, job_id, "Rollforward job")
    _project_write(db, user, row.project_id)
    before = _audit_state(db, row)
    allowed_transitions = {
        "draft": {"queued", "cancelled"}, "queued": {"running", "cancelled"},
        "running": {"success", "failed", "cancelled"},
    }
    if "status" in payload and payload["status"] != row.status:
        if payload["status"] not in allowed_transitions.get(row.status, set()):
            raise conflict(f"Cannot change rollforward job from {row.status} to {payload['status']}.")
        row.status = payload["status"]
        if row.status == "running":
            row.started_at = now_utc()
        if row.status in {"success", "failed", "cancelled"}:
            row.completed_at = now_utc()
    if "run_set_id" in payload:
        row.run_set_id = payload["run_set_id"]
    row.updated_by = user.id
    db.flush()
    _audit(db, user, "rollforward_job.updated", row, before)
    db.commit()
    return job_detail(db, row.id)


def _versioned_create(db: Session, model: type, project_id: str, payload: dict[str, Any], user: Any):
    _project_write(db, user, project_id)
    common = {
        "project_id": project_id, "name": payload["name"], "description": payload.get("description"),
        "created_by": user.id, "updated_by": user.id,
    }
    if model is Report:
        row = Report(**common, report_type=payload["report_type"],
                     definition=payload.get("definition") or {})
    else:
        for run_id in payload.get("source_run_ids") or []:
            run = db.get(Run, run_id)
            if run is None or run.project_id != project_id:
                raise ServiceError(422, "CROSS_PROJECT_REFERENCE", "A source run was not found in this project.")
        row = DerivedDataset(
            **common, dataset_type=payload["dataset_type"],
            source_run_ids=payload.get("source_run_ids") or [], schema=payload.get("schema") or {},
            storage_uri=payload.get("storage_uri"), storage_backend=payload.get("storage_backend"),
            file_format=payload.get("file_format"), row_count=payload.get("row_count"),
            fingerprint=payload.get("fingerprint"),
        )
    db.add(row)
    db.flush()
    _audit(db, user, f"{row.__tablename__.removesuffix('s')}.created", row)
    db.commit()
    db.refresh(row)
    return _serialize(row)


def create_report(db: Session, project_id: str, payload: dict[str, Any], user: Any) -> dict[str, Any]:
    return _versioned_create(db, Report, project_id, payload, user)


def create_dataset(db: Session, project_id: str, payload: dict[str, Any], user: Any) -> dict[str, Any]:
    return _versioned_create(db, DerivedDataset, project_id, payload, user)


def list_versioned(db: Session, model: type, project_id: str) -> list[dict[str, Any]]:
    return [_serialize(row) for row in db.query(model).filter(
        model.project_id == project_id, model.deleted_at.is_(None)
    ).order_by(model.name, model.version_number.desc()).all()]


def versioned_detail(db: Session, model: type, object_id: str) -> dict[str, Any]:
    label = "Report" if model is Report else "Derived dataset"
    return _serialize(_active(db, model, object_id, label))


def update_versioned(db: Session, model: type, object_id: str, payload: dict[str, Any], user: Any):
    label = "Report" if model is Report else "Derived dataset"
    row = _active(db, model, object_id, label)
    _project_write(db, user, row.project_id)
    if row.status == "published":
        raise conflict(f"Published {label.lower()} versions are immutable; create a new version.")
    before = _state(row)
    fields = ("name", "description", "report_type", "definition", "dataset_type", "source_run_ids",
              "schema", "storage_uri", "storage_backend", "file_format", "row_count", "fingerprint")
    for field in fields:
        if field in payload and hasattr(row, field):
            setattr(row, field, payload[field])
    if model is DerivedDataset:
        for run_id in row.source_run_ids or []:
            run = db.get(Run, run_id)
            if run is None or run.project_id != row.project_id:
                raise ServiceError(422, "CROSS_PROJECT_REFERENCE",
                                   "A source run was not found in this project.")
    row.updated_by = user.id
    db.flush()
    _audit(db, user, f"{row.__tablename__.removesuffix('s')}.updated", row, before)
    db.commit()
    db.refresh(row)
    return _serialize(row)


def version_versioned(db: Session, model: type, object_id: str, user: Any):
    label = "Report" if model is Report else "Derived dataset"
    source = _active(db, model, object_id, label)
    _project_write(db, user, source.project_id)
    number = (db.query(func.max(model.version_number)).filter(
        model.project_id == source.project_id, model.name == source.name
    ).scalar() or 0) + 1
    if model is Report:
        row = Report(
            project_id=source.project_id, name=source.name, report_type=source.report_type,
            description=source.description, version_number=number, parent_report_id=source.id,
            definition=dict(source.definition or {}), created_by=user.id, updated_by=user.id,
        )
    else:
        row = DerivedDataset(
            project_id=source.project_id, name=source.name, dataset_type=source.dataset_type,
            description=source.description, version_number=number, parent_dataset_id=source.id,
            source_run_ids=list(source.source_run_ids or []), schema=dict(source.schema or {}),
            storage_uri=source.storage_uri, storage_backend=source.storage_backend,
            file_format=source.file_format, row_count=source.row_count,
            fingerprint=source.fingerprint,
            created_by=user.id, updated_by=user.id,
        )
    db.add(row)
    db.flush()
    _audit(db, user, f"{row.__tablename__.removesuffix('s')}.version_created", row,
           context={"parent_id": source.id})
    db.commit()
    db.refresh(row)
    return _serialize(row)


def publish_versioned(db: Session, model: type, object_id: str, user: Any):
    label = "Report" if model is Report else "Derived dataset"
    row = _active(db, model, object_id, label)
    _project_write(db, user, row.project_id)
    before = _state(row)
    row.status = "published"
    row.published_by = user.id
    row.published_at = now_utc()
    row.updated_by = user.id
    db.flush()
    _audit(db, user, f"{row.__tablename__.removesuffix('s')}.published", row, before)
    db.commit()
    db.refresh(row)
    return _serialize(row)


def archive_versioned(db: Session, model: type, object_id: str, user: Any) -> None:
    label = "Report" if model is Report else "Derived dataset"
    row = _active(db, model, object_id, label)
    _project_write(db, user, row.project_id)
    before = _state(row)
    row.status = "archived"
    row.deleted_at = now_utc()
    row.deleted_by = row.updated_by = user.id
    db.flush()
    _audit(db, user, f"{row.__tablename__.removesuffix('s')}.archived", row, before,
           {"deletion": "soft"})
    db.commit()


def run_steps(db: Session, run_id: str) -> list[dict[str, Any]]:
    return [_serialize(row) for row in db.query(RunStep).filter(
        RunStep.run_id == run_id
    ).order_by(RunStep.sequence).all()]


RUN_STEP_DEFINITIONS = (
    ("verify_inputs", "Verify frozen inputs", 1),
    ("calculate", "Calculate projections", 2),
    ("finalize", "Finalize canonical outcome", 3),
)


def reset_run_steps(db: Session, run_id: str) -> None:
    """Create or reset the stable step records when a run attempt is claimed."""
    existing = {
        row.step_key: row for row in db.query(RunStep).filter(RunStep.run_id == run_id).all()
    }
    for key, name, sequence in RUN_STEP_DEFINITIONS:
        row = existing.get(key)
        if row is None:
            row = RunStep(run_id=run_id, step_key=key, name=name, sequence=sequence)
            db.add(row)
        row.status = "pending"
        row.progress_total = 0
        row.progress_done = 0
        row.metrics = None
        row.error_code = None
        row.error_message = None
        row.started_at = None
        row.completed_at = None


def update_run_step(db: Session, run_id: str, step_key: str, status: str, *,
                    progress_total: int | None = None, progress_done: int | None = None,
                    metrics: dict[str, Any] | None = None, error_code: str | None = None,
                    error_message: str | None = None) -> None:
    row = db.query(RunStep).filter(
        RunStep.run_id == run_id, RunStep.step_key == step_key
    ).first()
    if row is None:
        return
    row.status = status
    if status == "running" and row.started_at is None:
        row.started_at = now_utc()
    if status in {"success", "failed", "skipped", "cancelled"}:
        row.completed_at = now_utc()
    if progress_total is not None:
        row.progress_total = progress_total
    if progress_done is not None:
        row.progress_done = progress_done
    if metrics is not None:
        row.metrics = metrics
    row.error_code = error_code
    row.error_message = error_message
