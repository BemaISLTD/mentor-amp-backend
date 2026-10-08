"""Governed upload → preview → map → validate → commit → approve lifecycle."""

import json
import mimetypes
import tempfile
import uuid
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.core import lifecycle
from app.core.artifacts import get_artifact_store
from app.core.audit import record_audit
from app.core.execution.fingerprints import (
    DATA_MANAGER_INFORCE_FINGERPRINT_SCHEME,
    inforce_fingerprint,
    scenario_fingerprint, table_fingerprint,
)
from app.core.project_lifecycle import require_active_project
from app.data.importers.base import detect_format, parse_file
from app.db.models.assumption import AssumptionSet, AssumptionTable
from app.db.models.data_manager import (
    ImportSession,
    ImportSessionEvent,
    MappingProfile,
    RejectedRecord,
    ValidationIssue,
    ValidationRun,
)
from app.db.models.factor import FactorSet, FactorTable
from app.db.models.inforce import InforceFile, InforceRecord
from app.db.models.model_variable import ModelVariableDefinition
from app.db.models.scenario import ScenarioSet, ScenarioTable
from app.services import access
from app.services.common import ServiceError, conflict, iso, not_found, now_utc

CATEGORIES = {"liability_inforce", "assumption_table", "factor_table", "scenario"}
KIND_TO_CATEGORY = {
    "inforce": "liability_inforce", "assumption": "assumption_table",
    "factor": "factor_table", "scenario": "scenario",
}
CATEGORY_TO_KIND = {value: key for key, value in KIND_TO_CATEGORY.items()}
DATASET_MODELS = {
    "inforce": InforceFile, "assumption": AssumptionTable,
    "factor": FactorTable, "scenario": ScenarioTable,
}
PARENT_COLUMNS = {
    "inforce": "parent_file_id", "assumption": "parent_table_id",
    "factor": "parent_table_id", "scenario": "parent_table_id",
}
INFORCE_FIELDS = {
    "policy_id": "string", "product_type": "string", "issue_date": "date",
    "issue_age": "integer", "gender": "string", "premium": "number",
    "monthly_payment": "number",
}
SCENARIO_FIELDS = {
    "scenario_name": "string", "target_variable": "string", "operation": "string",
    "value": "number", "applies_from_period": "integer", "applies_to_period": "integer",
}
VALID_OPERATIONS = {"set", "add", "subtract", "multiply", "percent_change"}
TRANSFORMS = {None, "", "strip", "upper", "lower", "integer", "number", "date_iso"}


def _event(db: Session, row: ImportSession, user: Any, action: str,
           details: dict[str, Any] | None = None, before_status: str | None = None) -> None:
    details = details or {}
    db.add(ImportSessionEvent(
        import_session_id=row.id, action=action, actor_user_id=user.id, details=details,
    ))
    record_audit(
        db, actor_user_id=user.id, action=f"import_session.{action}",
        entity_type="import_session", entity_id=row.id,
        before_state={"status": before_status} if before_status is not None else None,
        after_state={"status": row.status, "category": row.category, **details},
    )


def _project_write(db: Session, user: Any, project_id: str) -> None:
    access.require_project_access(db, user, project_id, write=True)
    require_active_project(db, project_id)


def _session(db: Session, session_id: str) -> ImportSession:
    row = db.get(ImportSession, session_id)
    if row is None:
        raise not_found(f"Import session '{session_id}' not found.")
    return row


def require_session_access(db: Session, user: Any, session_id: str, *, write: bool = False) -> ImportSession:
    row = _session(db, session_id)
    access.require_project_access(db, user, row.project_id, write=write)
    if write:
        require_active_project(db, row.project_id)
    return row


def serialize_session(row: ImportSession) -> dict[str, Any]:
    return {
        "id": row.id, "project_id": row.project_id, "category": row.category,
        "name": row.name, "version_label": row.version_label,
        "replaces_dataset_id": row.replaces_dataset_id, "status": row.status,
        "options": dict(row.options or {}), "original_filename": row.original_filename,
        "file_format": row.file_format, "raw_fingerprint": row.raw_fingerprint,
        "raw_size_bytes": row.raw_size_bytes, "row_count": row.row_count,
        "columns": list(row.columns or []), "detected_types": dict(row.detected_types or {}),
        "mapping_fields": list(row.mapping_fields or []),
        "mapping_profile_id": row.mapping_profile_id, "created_by": row.created_by,
        "updated_by": row.updated_by, "committed_by": row.committed_by,
        "committed_at": iso(row.committed_at), "dataset_kind": row.dataset_kind,
        "dataset_id": row.dataset_id, "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
    }


def create_session(db: Session, payload: dict[str, Any], user: Any) -> dict[str, Any]:
    project_id = payload["project_id"]
    _project_write(db, user, project_id)
    category = payload["category"]
    if category not in CATEGORIES:
        raise ServiceError(422, "VALIDATION_ERROR", f"Unsupported import category '{category}'.")
    replaces = payload.get("replaces_dataset_id")
    if replaces:
        _dataset(db, CATEGORY_TO_KIND[category], replaces, project_id=project_id)
    row = ImportSession(
        project_id=project_id, category=category, name=payload["name"],
        version_label=payload.get("version_label"), replaces_dataset_id=replaces,
        options=payload.get("options") or {}, created_by=user.id, updated_by=user.id,
    )
    db.add(row)
    db.flush()
    _event(db, row, user, "opened", {"replaces_dataset_id": replaces})
    db.commit()
    db.refresh(row)
    return serialize_session(row)


def list_sessions(db: Session, project_id: str, category: str | None = None) -> list[dict[str, Any]]:
    query = db.query(ImportSession).filter(ImportSession.project_id == project_id)
    if category:
        query = query.filter(ImportSession.category == category)
    return [serialize_session(row) for row in query.order_by(ImportSession.created_at.desc()).all()]


def _parse_bytes(filename: str, content: bytes) -> list[dict[str, Any]]:
    suffix = Path(filename).suffix
    temporary = Path(tempfile.mkstemp(suffix=suffix)[1])
    try:
        temporary.write_bytes(content)
        return parse_file(str(temporary))
    finally:
        temporary.unlink(missing_ok=True)


def _detected_types(rows: list[dict[str, Any]], columns: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for column in columns:
        values = [row.get(column) for row in rows if row.get(column) not in (None, "")][:50]
        if not values:
            result[column] = "unknown"
            continue
        try:
            for value in values:
                int(str(value))
            result[column] = "integer"
            continue
        except (TypeError, ValueError):
            pass
        try:
            for value in values:
                float(str(value))
            result[column] = "number"
            continue
        except (TypeError, ValueError):
            pass
        try:
            for value in values:
                date.fromisoformat(str(value))
            result[column] = "date"
        except (TypeError, ValueError):
            result[column] = "string"
    return result


def upload_file(db: Session, row: ImportSession, filename: str, content: bytes,
                user: Any) -> dict[str, Any]:
    if row.status == "committed":
        raise conflict("A committed import session cannot accept another file.")
    before_status = row.status
    try:
        file_format = detect_format(filename)
        rows = _parse_bytes(filename, content)
    except ValueError as error:
        raise ServiceError(400, "UNSUPPORTED_FILE_FORMAT", str(error)) from error
    columns = list(rows[0].keys()) if rows else []
    stored = get_artifact_store().write_bytes(row.id, "raw", filename, content)
    row.original_filename = Path(filename.replace("\\", "/")).name
    row.file_format = file_format
    row.raw_storage_uri = stored.uri
    row.raw_fingerprint = stored.checksum_sha256
    row.raw_size_bytes = len(content)
    row.row_count = len(rows)
    row.columns = columns
    row.detected_types = _detected_types(rows, columns)
    row.mapping_fields = []
    row.mapping_profile_id = None
    row.status = "uploaded"
    row.updated_by = user.id
    db.flush()
    _event(db, row, user, "uploaded", {
        "filename": row.original_filename, "format": file_format,
        "row_count": len(rows), "raw_fingerprint": stored.checksum_sha256,
    }, before_status)
    db.commit()
    return {
        **serialize_session(row), "file_id": row.id,
        "preview": rows[:20],
    }


def _raw_rows(row: ImportSession) -> list[dict[str, Any]]:
    if not row.raw_storage_uri or not row.original_filename:
        raise conflict("Upload a file before this operation.")
    return _parse_bytes(row.original_filename, get_artifact_store().read_bytes(row.raw_storage_uri))


def preview(row: ImportSession, count: int) -> dict[str, Any]:
    rows = _raw_rows(row)
    return {
        "import_session_id": row.id, "columns": list(row.columns or []),
        "detected_types": dict(row.detected_types or {}), "row_count": len(rows),
        "sample_rows": rows[:count], "raw_fingerprint": row.raw_fingerprint,
    }


def raw_file(row: ImportSession) -> tuple[bytes, str, str]:
    if not row.raw_storage_uri or not row.original_filename:
        raise conflict("Upload a file before downloading it.")
    media_type = mimetypes.guess_type(row.original_filename)[0] or "application/octet-stream"
    return get_artifact_store().read_bytes(row.raw_storage_uri), row.original_filename, media_type


def content_disposition(filename: str) -> str:
    return f"attachment; filename*=UTF-8''{quote(filename, safe='')}"


def serialize_profile(row: MappingProfile) -> dict[str, Any]:
    return {
        "id": row.id, "project_id": row.project_id, "category": row.category,
        "name": row.name, "version_number": row.version_number,
        "parent_profile_id": row.parent_profile_id, "fields": list(row.fields or []),
        "created_by": row.created_by, "created_at": iso(row.created_at),
    }


def create_mapping_profile(db: Session, payload: dict[str, Any], user: Any) -> dict[str, Any]:
    _project_write(db, user, payload["project_id"])
    if payload["category"] not in CATEGORIES:
        raise ServiceError(422, "VALIDATION_ERROR", "Unsupported mapping profile category.")
    _validate_mapping_shape(payload["fields"])
    parent = db.query(MappingProfile).filter(
        MappingProfile.project_id == payload["project_id"],
        MappingProfile.category == payload["category"], MappingProfile.name == payload["name"],
    ).order_by(MappingProfile.version_number.desc()).first()
    row = MappingProfile(
        project_id=payload["project_id"], category=payload["category"], name=payload["name"],
        version_number=(parent.version_number + 1 if parent else 1),
        parent_profile_id=parent.id if parent else None,
        fields=payload["fields"], created_by=user.id,
    )
    db.add(row)
    db.flush()
    record_audit(db, actor_user_id=user.id, action="mapping_profile.created",
                 entity_type="mapping_profile", entity_id=row.id,
                 after_state=serialize_profile(row))
    db.commit()
    db.refresh(row)
    return serialize_profile(row)


def list_mapping_profiles(db: Session, project_id: str, category: str | None) -> list[dict[str, Any]]:
    query = db.query(MappingProfile).filter(MappingProfile.project_id == project_id)
    if category:
        query = query.filter(MappingProfile.category == category)
    return [serialize_profile(row) for row in query.order_by(
        MappingProfile.name, MappingProfile.version_number.desc()
    ).all()]


def _validate_mapping_shape(fields: list[dict[str, Any]]) -> None:
    if not fields:
        raise ServiceError(422, "VALIDATION_ERROR", "At least one mapping field is required.")
    targets: set[str] = set()
    for field in fields:
        if not field.get("source_column") or not field.get("target_field"):
            raise ServiceError(422, "VALIDATION_ERROR", "Every mapping needs source_column and target_field.")
        if field["target_field"] in targets:
            raise ServiceError(422, "VALIDATION_ERROR", f"Target '{field['target_field']}' is mapped twice.")
        targets.add(field["target_field"])
        if field.get("transform") not in TRANSFORMS:
            raise ServiceError(422, "VALIDATION_ERROR", f"Unsupported transform '{field.get('transform')}'.")


def set_mapping(db: Session, row: ImportSession, payload: dict[str, Any], user: Any) -> dict[str, Any]:
    if row.status == "committed":
        raise conflict("A committed import session is immutable.")
    if not row.raw_storage_uri:
        raise conflict("Upload a file before mapping it.")
    before_status = row.status
    profile_id, fields = payload.get("profile_id"), payload.get("fields")
    if bool(profile_id) == bool(fields):
        raise ServiceError(422, "VALIDATION_ERROR", "Provide either profile_id or fields.")
    if profile_id:
        profile = db.get(MappingProfile, profile_id)
        if profile is None or profile.project_id != row.project_id or profile.category != row.category:
            raise not_found(f"Mapping profile '{profile_id}' not found.")
        fields = list(profile.fields or [])
        row.mapping_profile_id = profile.id
    else:
        row.mapping_profile_id = None
    _validate_mapping_shape(fields)
    missing = sorted({field["source_column"] for field in fields} - set(row.columns or []))
    if missing:
        raise ServiceError(422, "VALIDATION_ERROR", f"Source columns are missing: {', '.join(missing)}.")
    row.mapping_fields = fields
    row.status = "mapped"
    row.updated_by = user.id
    db.flush()
    _event(db, row, user, "mapped", {
        "profile_id": row.mapping_profile_id, "field_count": len(fields),
    }, before_status)
    db.commit()
    return serialize_session(row)


def _transform(value: Any, transform: str | None) -> Any:
    if value is None:
        return None
    if transform in (None, ""):
        return value
    if transform == "strip":
        return str(value).strip()
    if transform == "upper":
        return str(value).strip().upper()
    if transform == "lower":
        return str(value).strip().lower()
    if transform == "integer":
        return int(float(str(value)))
    if transform == "number":
        return float(str(value))
    if transform == "date_iso":
        return date.fromisoformat(str(value)).isoformat()
    return value


def _mapped_rows(row: ImportSession) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = _raw_rows(row)
    mapped: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for number, source in enumerate(rows, start=1):
        target: dict[str, Any] = {}
        for field in row.mapping_fields or []:
            try:
                target[field["target_field"]] = _transform(
                    source.get(field["source_column"]), field.get("transform")
                )
            except (TypeError, ValueError) as error:
                issues.append(_issue(
                    "error", "TRANSFORM_FAILED", f"Could not apply transform: {error}", number,
                    field["target_field"], source.get(field["source_column"]),
                ))
                target[field["target_field"]] = source.get(field["source_column"])
        if row.category == "scenario":
            target.setdefault("applies_from_period", 0)
            target.setdefault("applies_to_period", 1200)
        mapped.append(target)
    return mapped, issues


def _issue(severity: str, code: str, message: str, row: int | None = None,
           column: str | None = None, value: Any = None) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message,
            "row_number": row, "column_name": column, "value": value}


def _coerce(value: Any, kind: str) -> Any:
    if value in (None, ""):
        return value
    if kind == "integer":
        return int(float(str(value)))
    if kind == "number":
        return float(str(value))
    if kind == "date":
        return date.fromisoformat(str(value)).isoformat()
    return str(value).strip()


def _required_and_types(rows: list[dict[str, Any]], spec: dict[str, str],
                        issues: list[dict[str, Any]]) -> None:
    columns = set(rows[0]) if rows else set()
    for field in spec:
        if field not in columns:
            issues.append(_issue("error", "REQUIRED_FIELD_NOT_MAPPED",
                                 f"Required field '{field}' is not mapped.", column=field))
    for number, row in enumerate(rows, start=1):
        for field, kind in spec.items():
            value = row.get(field)
            if value in (None, ""):
                issues.append(_issue("error", "MISSING_VALUE", f"'{field}' is required.",
                                     number, field, value))
                continue
            try:
                row[field] = _coerce(value, kind)
            except (TypeError, ValueError):
                issues.append(_issue("error", "TYPE_MISMATCH", f"'{field}' must be {kind}.",
                                     number, field, value))


def _duplicate_issues(rows: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    seen: dict[tuple, int] = {}
    issues: list[dict[str, Any]] = []
    for number, row in enumerate(rows, start=1):
        key = tuple(str(row.get(field, "")) for field in keys)
        if key in seen:
            issues.append(_issue("error", "DUPLICATE_KEY",
                                 f"Duplicate key {key}; first seen on row {seen[key]}.", number,
                                 ",".join(keys), list(key)))
        else:
            seen[key] = number
    return issues


def _validate_inforce(row: ImportSession, rows: list[dict[str, Any]],
                      issues: list[dict[str, Any]]) -> None:
    _required_and_types(rows, INFORCE_FIELDS, issues)
    issues.extend(_duplicate_issues(rows, ["policy_id"]))
    valuation = (row.options or {}).get("valuation_date")
    for number, item in enumerate(rows, start=1):
        gender = str(item.get("gender", "")).upper()
        item["gender"] = gender
        if gender and gender not in {"M", "F"}:
            issues.append(_issue("error", "INVALID_GENDER", "gender must be M or F.",
                                 number, "gender", gender))
        age = item.get("issue_age")
        if isinstance(age, int) and not 0 <= age <= 120:
            issues.append(_issue("warning", "RANGE_WARNING", "issue_age is outside 0–120.",
                                 number, "issue_age", age))
        for field in ("premium", "monthly_payment"):
            value = item.get(field)
            if isinstance(value, (int, float)) and value < 0:
                issues.append(_issue("warning", "RANGE_WARNING", f"{field} is negative.",
                                     number, field, value))
        if valuation and item.get("issue_date"):
            try:
                if date.fromisoformat(str(item["issue_date"])) > date.fromisoformat(str(valuation)):
                    issues.append(_issue("error", "EFFECTIVE_DATE_INVALID",
                                         "issue_date is after the valuation date.", number,
                                         "issue_date", item["issue_date"]))
            except ValueError:
                pass


def _table_options(row: ImportSession, rows: list[dict[str, Any]]) -> tuple[list[str], str]:
    options = row.options or {}
    columns = list(rows[0]) if rows else []
    value_column = options.get("value_column") or (columns[-1] if columns else "value")
    lookup_keys = list(options.get("lookup_keys") or [column for column in columns if column != value_column])
    return lookup_keys, value_column


def _validate_table(row: ImportSession, rows: list[dict[str, Any]],
                    issues: list[dict[str, Any]]) -> None:
    keys, value_column = _table_options(row, rows)
    spec = {key: "string" for key in keys}
    spec[value_column] = "number"
    _required_and_types(rows, spec, issues)
    issues.extend(_duplicate_issues(rows, keys))
    probability = value_column.lower() in {"qx", "mortality_rate", "lapse_rate", "probability"}
    for number, item in enumerate(rows, start=1):
        value = item.get(value_column)
        if not isinstance(value, (int, float)):
            continue
        if probability and not 0 <= value <= 1:
            issues.append(_issue("error", "PROBABILITY_RANGE", f"{value_column} must be between 0 and 1.",
                                 number, value_column, value))
        elif ("rate" in value_column.lower() or value_column.lower() == "qx") and 1 < value <= 100:
            issues.append(_issue("warning", "UNIT_WARNING",
                                 f"{value_column} looks like a percentage rather than a decimal rate.",
                                 number, value_column, value))


def _validate_scenario(db: Session, row: ImportSession, rows: list[dict[str, Any]],
                       issues: list[dict[str, Any]]) -> None:
    _required_and_types(rows, SCENARIO_FIELDS, issues)
    model_version_id = (row.options or {}).get("model_version_id")
    definitions: dict[str, ModelVariableDefinition] = {}
    if model_version_id:
        if access.project_id_of(db, "model_version", model_version_id) != row.project_id:
            issues.append(_issue("error", "MODEL_VERSION_NOT_FOUND",
                                 "The selected model version was not found in this project."))
        else:
            definitions = {item.variable_name: item for item in db.query(ModelVariableDefinition).filter(
                ModelVariableDefinition.model_version_id == model_version_id
            ).all()}
    intervals: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    for number, item in enumerate(rows, start=1):
        operation = str(item.get("operation", ""))
        if operation not in VALID_OPERATIONS:
            issues.append(_issue("error", "INVALID_OPERATION", f"Unsupported operation '{operation}'.",
                                 number, "operation", operation))
        start, end = item.get("applies_from_period"), item.get("applies_to_period")
        if isinstance(start, int) and isinstance(end, int):
            if start < 0 or end > 1200 or end < start:
                issues.append(_issue("error", "INVALID_PERIOD_RANGE",
                                     "Periods must satisfy 0 <= from <= to <= 1200.", number))
            target = f"{item.get('scenario_name', '')}:{item.get('target_variable', '')}"
            for prior_start, prior_end, prior_row in intervals[target]:
                if start <= prior_end and prior_start <= end:
                    issues.append(_issue("error", "SCENARIO_OVERRIDE_OVERLAP",
                                         f"Override overlaps row {prior_row}.", number,
                                         "target_variable", target))
            intervals[target].append((start, end, number))
        if definitions:
            definition = definitions.get(str(item.get("target_variable", "")))
            if definition is None:
                issues.append(_issue("error", "SCENARIO_TARGET_UNKNOWN",
                                     "Scenario target is not defined by the model version.", number,
                                     "target_variable", item.get("target_variable")))
            elif not definition.allow_scenario_override:
                issues.append(_issue("error", "SCENARIO_TARGET_NOT_OVERRIDABLE",
                                     "The model version does not allow this override.", number,
                                     "target_variable", item.get("target_variable")))


def _canonical_order(category: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if category == "liability_inforce":
        return sorted(rows, key=lambda item: str(item.get("policy_id", "")))
    return sorted(rows, key=lambda item: json.dumps(item, sort_keys=True, default=str))


def _canonical_fingerprint(category: str, rows: list[dict[str, Any]]) -> tuple[str, str]:
    if category == "liability_inforce":
        value = inforce_fingerprint((str(row.get("policy_id")), row) for row in rows)
        return value, DATA_MANAGER_INFORCE_FINGERPRINT_SCHEME
    if category == "scenario":
        overrides = [{key: value for key, value in item.items() if key != "scenario_name"}
                     for item in rows]
        return scenario_fingerprint(overrides), "scenario-canonical-v1"
    return table_fingerprint(rows), "table-canonical-v1"


def validate_session(db: Session, row: ImportSession, user: Any) -> dict[str, Any]:
    if row.status not in {"mapped", "validated", "needs_review", "invalid"}:
        raise conflict("Upload and map the file before validation.")
    before_status = row.status
    mapped, issues = _mapped_rows(row)
    if not mapped:
        issues.append(_issue("error", "EMPTY_FILE", "The uploaded file contains no records."))
    expected = (row.options or {}).get("expected_record_count")
    if expected is not None and len(mapped) != int(expected):
        issues.append(_issue("error", "RECORD_COUNT_MISMATCH",
                             f"Expected {expected} records but found {len(mapped)}."))
    if row.category == "liability_inforce":
        _validate_inforce(row, mapped, issues)
    elif row.category in {"assumption_table", "factor_table"}:
        _validate_table(row, mapped, issues)
    else:
        _validate_scenario(db, row, mapped, issues)

    errors = [issue for issue in issues if issue["severity"] == "error"]
    warnings = [issue for issue in issues if issue["severity"] == "warning"]
    rejected_numbers = {issue["row_number"] for issue in errors if issue["row_number"] is not None}
    accepted = _canonical_order(
        row.category,
        [item for number, item in enumerate(mapped, start=1) if number not in rejected_numbers],
    )
    canonical_uri = None
    canonical_fingerprint = None
    scheme = None
    if accepted:
        canonical = get_artifact_store().write_rows(row.id, "canonical", accepted)
        canonical_uri = canonical.uri
        canonical_fingerprint, scheme = _canonical_fingerprint(row.category, accepted)
    validation = ValidationRun(
        import_session_id=row.id, status="invalid" if errors else "passed",
        error_count=len(errors), warning_count=len(warnings), accepted_count=len(accepted),
        rejected_count=len(rejected_numbers), canonical_storage_uri=canonical_uri,
        canonical_fingerprint=canonical_fingerprint, fingerprint_scheme=scheme,
        created_by=user.id, completed_at=now_utc(),
    )
    db.add(validation)
    db.flush()
    for issue in issues:
        db.add(ValidationIssue(
            validation_run_id=validation.id, severity=issue["severity"], code=issue["code"],
            message=issue["message"], row_number=issue["row_number"],
            column_name=issue["column_name"], value=issue["value"],
        ))
    if rejected_numbers:
        rejected_rows = [{
            "row_number": number, "raw_json": json.dumps(_raw_rows(row)[number - 1], default=str),
            "canonical_json": json.dumps(mapped[number - 1], default=str),
        } for number in sorted(rejected_numbers)]
        artifact = get_artifact_store().write_rows(row.id, "rejected", rejected_rows)
        for number in sorted(rejected_numbers):
            reasons = [issue for issue in errors if issue["row_number"] == number]
            db.add(RejectedRecord(validation_run_id=validation.id, row_number=number,
                                  storage_uri=artifact.uri, reasons=reasons))
    row.status = "invalid" if errors else ("needs_review" if warnings else "validated")
    row.updated_by = user.id
    db.flush()
    _event(db, row, user, "validated", {
        "validation_run_id": validation.id, "errors": len(errors), "warnings": len(warnings),
        "accepted": len(accepted), "rejected": len(rejected_numbers),
        "canonical_fingerprint": canonical_fingerprint,
    }, before_status)
    db.commit()
    return serialize_validation(validation)


def serialize_validation(row: ValidationRun) -> dict[str, Any]:
    return {
        "id": row.id, "import_session_id": row.import_session_id, "status": row.status,
        "error_count": row.error_count, "warning_count": row.warning_count,
        "accepted_count": row.accepted_count, "rejected_count": row.rejected_count,
        "canonical_fingerprint": row.canonical_fingerprint,
        "fingerprint_scheme": row.fingerprint_scheme, "created_by": row.created_by,
        "created_at": iso(row.created_at), "completed_at": iso(row.completed_at),
    }


def validation_issues(db: Session, validation_id: str) -> list[dict[str, Any]]:
    return [{
        "id": issue.id, "severity": issue.severity, "code": issue.code,
        "message": issue.message, "row_number": issue.row_number,
        "column_name": issue.column_name, "value": issue.value,
    } for issue in db.query(ValidationIssue).filter(
        ValidationIssue.validation_run_id == validation_id
    ).order_by(ValidationIssue.id).all()]


def require_validation_access(db: Session, user: Any, validation_id: str) -> ValidationRun:
    validation = db.get(ValidationRun, validation_id)
    session = db.get(ImportSession, validation.import_session_id) if validation else None
    if validation is None or session is None:
        raise not_found(f"Validation run '{validation_id}' not found.")
    access.require_project_access(db, user, session.project_id)
    return validation


def rejected_records(db: Session, validation_id: str) -> list[dict[str, Any]]:
    records = db.query(RejectedRecord).filter(
        RejectedRecord.validation_run_id == validation_id
    ).order_by(RejectedRecord.row_number).all()
    artifact_cache: dict[str, dict[int, dict[str, Any]]] = {}
    result = []
    for record in records:
        if record.storage_uri not in artifact_cache:
            artifact_cache[record.storage_uri] = {
                int(item["row_number"]): item for item in get_artifact_store().read_rows(record.storage_uri)
            }
        artifact = artifact_cache[record.storage_uri].get(record.row_number, {})
        result.append({
            "id": record.id, "row_number": record.row_number,
            "raw_record": json.loads(artifact.get("raw_json", "{}")),
            "canonical_record": json.loads(artifact.get("canonical_json", "{}")),
            "reasons": list(record.reasons or []),
        })
    return result


def _latest_validation(db: Session, row: ImportSession) -> ValidationRun:
    validation = db.query(ValidationRun).filter(
        ValidationRun.import_session_id == row.id
    ).order_by(ValidationRun.created_at.desc()).first()
    if validation is None:
        raise conflict("Validate the mapped file before committing it.")
    return validation


def _dataset_project(db: Session, kind: str, dataset: Any) -> str | None:
    if kind == "inforce":
        return dataset.project_id
    set_model = {"assumption": AssumptionSet, "factor": FactorSet, "scenario": ScenarioSet}[kind]
    owner = db.get(set_model, dataset.set_id)
    return owner.project_id if owner else None


def _dataset(db: Session, kind: str, dataset_id: str, project_id: str | None = None):
    model = DATASET_MODELS.get(kind)
    if model is None:
        raise ServiceError(422, "VALIDATION_ERROR", f"Unsupported dataset kind '{kind}'.")
    row = db.get(model, dataset_id)
    if row is None or (project_id is not None and _dataset_project(db, kind, row) != project_id):
        raise not_found(f"Dataset '{dataset_id}' not found.")
    return row


def _version_metadata(predecessor: Any | None, session: ImportSession) -> dict[str, Any]:
    return {
        "version_number": predecessor.version_number + 1 if predecessor else 1,
        "version_group_id": predecessor.version_group_id or predecessor.id if predecessor else str(uuid.uuid4()),
        "version_label": session.version_label or f"v{predecessor.version_number + 1 if predecessor else 1}",
    }


def _set(db: Session, model: type, project_id: str, name: str):
    row = db.query(model).filter(model.project_id == project_id, model.name == name).first()
    if row is None:
        row = model(project_id=project_id, name=name)
        db.add(row)
        db.flush()
    return row


def commit_session(db: Session, row: ImportSession, user: Any) -> dict[str, Any]:
    if row.status == "committed":
        raise conflict("This import session has already been committed.")
    if row.status not in {"validated", "needs_review"}:
        raise conflict("A session with validation errors cannot be committed.")
    before_status = row.status
    validation = _latest_validation(db, row)
    if validation.status != "passed" or not validation.canonical_storage_uri:
        raise conflict("The latest validation did not produce a committable dataset.")
    rows = get_artifact_store().read_rows(validation.canonical_storage_uri)
    kind = CATEGORY_TO_KIND[row.category]
    predecessor = _dataset(db, kind, row.replaces_dataset_id, row.project_id) if row.replaces_dataset_id else None
    version = _version_metadata(predecessor, row)
    common = {
        **version, "status": lifecycle.NEEDS_REVIEW if validation.warning_count else lifecycle.VALIDATED,
        "fingerprint": validation.canonical_fingerprint, "raw_fingerprint": row.raw_fingerprint,
        "mapping_version": _mapping_version(db, row), "validation_run_id": validation.id,
        "import_session_id": row.id, "committed_by": user.id, "committed_at": now_utc(),
    }
    if kind == "inforce":
        dataset = InforceFile(
            project_id=row.project_id, filename=row.name, file_type=row.file_format or "unknown",
            row_count=len(rows), columns_detected=list(rows[0]) if rows else [],
            fingerprint_scheme=DATA_MANAGER_INFORCE_FINGERPRINT_SCHEME,
            parent_file_id=predecessor.id if predecessor else None, **common,
        )
        db.add(dataset)
        db.flush()
        db.add_all([InforceRecord(file_id=dataset.id, policy_id=str(item["policy_id"]), data=item)
                    for item in rows])
    elif kind in {"assumption", "factor"}:
        set_model = AssumptionSet if kind == "assumption" else FactorSet
        table_model = AssumptionTable if kind == "assumption" else FactorTable
        owner = _set(db, set_model, row.project_id, (row.options or {}).get("set_name") or row.name)
        keys, value_column = _table_options(row, rows)
        dataset = table_model(
            set_id=owner.id, table_name=row.name,
            table_type=(row.options or {}).get("table_type") or "generic",
            lookup_keys=keys, value_column=value_column, data=rows,
            parent_table_id=predecessor.id if predecessor else None, **common,
        )
        db.add(dataset)
        db.flush()
    else:
        owner = _set(db, ScenarioSet, row.project_id, (row.options or {}).get("set_name") or row.name)
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in rows:
            grouped[str(item.get("scenario_name") or row.name)].append(
                {key: value for key, value in item.items() if key != "scenario_name"}
            )
        if predecessor and len(grouped) != 1:
            raise conflict("A replacement scenario import must contain exactly one scenario.")
        datasets = []
        for scenario_name, overrides in grouped.items():
            scenario_common = dict(common)
            scenario_common.update(_version_metadata(predecessor, row))
            scenario_common["fingerprint"] = scenario_fingerprint(overrides)
            dataset = ScenarioTable(
                set_id=owner.id, scenario_name=scenario_name, overrides=overrides,
                scenario_type=(row.options or {}).get("scenario_type") or "deterministic",
                as_of_date=date.fromisoformat(row.options["as_of_date"])
                if (row.options or {}).get("as_of_date") else None,
                parent_table_id=predecessor.id if predecessor else None, **scenario_common,
            )
            db.add(dataset)
            db.flush()
            datasets.append(dataset)
        dataset = datasets[0]
    if kind != "scenario":
        datasets = [dataset]
    row.status = "committed"
    row.committed_by = row.updated_by = user.id
    row.committed_at = now_utc()
    row.dataset_kind = kind
    row.dataset_id = dataset.id
    db.flush()
    _event(db, row, user, "committed", {
        "dataset_kind": kind, "dataset_id": dataset.id,
        "dataset_ids": [item.id for item in datasets],
        "version_number": dataset.version_number,
        "canonical_fingerprint": validation.canonical_fingerprint,
    }, before_status)
    for item in datasets:
        record_audit(db, actor_user_id=user.id, action="dataset.committed",
                     entity_type=f"{kind}_dataset", entity_id=item.id,
                     after_state=serialize_dataset(db, kind, item))
    db.commit()
    response = serialize_dataset(db, kind, dataset)
    response["dataset_ids"] = [item.id for item in datasets]
    return response


def _mapping_version(db: Session, row: ImportSession) -> int:
    profile = db.get(MappingProfile, row.mapping_profile_id) if row.mapping_profile_id else None
    return profile.version_number if profile else 1


def serialize_dataset(db: Session, kind: str, row: Any) -> dict[str, Any]:
    base = {
        "id": row.id, "kind": kind, "project_id": _dataset_project(db, kind, row),
        "status": row.status, "version_label": row.version_label,
        "version_number": row.version_number, "version_group_id": row.version_group_id,
        "parent_dataset_id": getattr(row, PARENT_COLUMNS[kind]),
        "fingerprint": row.fingerprint, "raw_fingerprint": row.raw_fingerprint,
        "mapping_version": row.mapping_version, "validation_run_id": row.validation_run_id,
        "import_session_id": row.import_session_id, "committed_by": row.committed_by,
        "committed_at": iso(row.committed_at), "approved_by": row.approved_by,
        "approved_at": iso(row.approved_at), "rejected_by": row.rejected_by,
        "rejected_at": iso(row.rejected_at), "review_reason": row.review_reason,
    }
    if kind == "inforce":
        base.update({"name": row.filename, "row_count": row.row_count,
                     "columns": list(row.columns_detected or []),
                     "fingerprint_scheme": row.fingerprint_scheme})
    elif kind in {"assumption", "factor"}:
        base.update({"name": row.table_name, "table_type": row.table_type,
                     "lookup_keys": list(row.lookup_keys or []), "value_column": row.value_column,
                     "row_count": len(row.data or [])})
    else:
        base.update({"name": row.scenario_name, "scenario_type": row.scenario_type,
                     "row_count": len(row.overrides or [])})
    return base


def review_dataset(db: Session, kind: str, dataset_id: str, approve: bool,
                   reason: str, user: Any) -> dict[str, Any]:
    row = _dataset(db, kind, dataset_id)
    project_id = _dataset_project(db, kind, row)
    access.require_project_access(db, user, project_id)
    require_active_project(db, project_id)
    if row.status not in {lifecycle.VALIDATED, lifecycle.NEEDS_REVIEW}:
        raise conflict(f"Only validated or needs_review datasets can be reviewed; status is '{row.status}'.")
    if row.committed_by == user.id:
        raise ServiceError(409, "SELF_APPROVAL_FORBIDDEN", "A dataset must be reviewed by another user.")
    before = serialize_dataset(db, kind, row)
    if approve:
        row.status = lifecycle.APPROVED
        row.approved_by = user.id
        row.approved_at = now_utc()
    else:
        row.status = lifecycle.REJECTED
        row.rejected_by = user.id
        row.rejected_at = now_utc()
    row.review_reason = reason
    db.flush()
    record_audit(db, actor_user_id=user.id,
                 action=f"dataset.{'approved' if approve else 'rejected'}",
                 entity_type=f"{kind}_dataset", entity_id=row.id,
                 before_state=before, after_state=serialize_dataset(db, kind, row),
                 context={"reason": reason})
    db.commit()
    return serialize_dataset(db, kind, row)


def dataset_versions(db: Session, kind: str, dataset_id: str) -> list[dict[str, Any]]:
    source = _dataset(db, kind, dataset_id)
    return [serialize_dataset(db, kind, row) for row in db.query(DATASET_MODELS[kind]).filter(
        DATASET_MODELS[kind].version_group_id == source.version_group_id
    ).order_by(DATASET_MODELS[kind].version_number.desc()).all()]


def _dataset_rows(db: Session, kind: str, row: Any) -> list[dict[str, Any]]:
    if kind == "inforce":
        return [dict(item.data or {}) for item in db.query(InforceRecord).filter(
            InforceRecord.file_id == row.id
        ).order_by(InforceRecord.policy_id).all()]
    if kind in {"assumption", "factor"}:
        return list(row.data or [])
    return [{"scenario_name": row.scenario_name, **item} for item in row.overrides or []]


def compare_datasets(db: Session, kind: str, dataset_id: str, other_id: str,
                     limit: int = 50) -> dict[str, Any]:
    left = _dataset(db, kind, dataset_id)
    project_id = _dataset_project(db, kind, left)
    right = _dataset(db, kind, other_id, project_id)
    left_rows, right_rows = _dataset_rows(db, kind, left), _dataset_rows(db, kind, right)
    if kind == "inforce":
        keys = ["policy_id"]
    elif kind in {"assumption", "factor"}:
        keys = list(left.lookup_keys or [])
    else:
        keys = ["target_variable", "applies_from_period", "applies_to_period"]
    def indexed(rows: list[dict[str, Any]]) -> dict[tuple, dict[str, Any]]:
        return {tuple(str(row.get(key, "")) for key in keys): row for row in rows}
    old, new = indexed(left_rows), indexed(right_rows)
    added = sorted(new.keys() - old.keys())
    removed = sorted(old.keys() - new.keys())
    changed = sorted(key for key in old.keys() & new.keys() if old[key] != new[key])
    changes = (
        [{"change": "added", "key": list(key), "after": new[key]} for key in added]
        + [{"change": "removed", "key": list(key), "before": old[key]} for key in removed]
        + [{"change": "changed", "key": list(key), "before": old[key], "after": new[key]}
           for key in changed]
    )
    return {"dataset_id": dataset_id, "other_id": other_id, "keys": keys,
            "added_count": len(added), "removed_count": len(removed),
            "changed_count": len(changed), "changes": changes[:limit]}


def import_log(db: Session, session_id: str) -> list[dict[str, Any]]:
    return [{"id": item.id, "action": item.action, "actor_user_id": item.actor_user_id,
             "details": dict(item.details or {}), "created_at": iso(item.created_at)}
            for item in db.query(ImportSessionEvent).filter(
                ImportSessionEvent.import_session_id == session_id
            ).order_by(ImportSessionEvent.id).all()]
