import io
from types import SimpleNamespace

import pytest
from fastapi import UploadFile

from app.api import imports


class RecordingSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def upload(name: str = "policies.csv", content: bytes = b"policy_id\nP1\n") -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(content))


def test_staged_upload_is_removed_after_success(tmp_path, monkeypatch):
    monkeypatch.setattr(imports, "UPLOAD_DIR", tmp_path)

    with imports._staged_upload(upload()) as path:
        assert path.exists()
        assert path.read_text(encoding="utf-8") == "policy_id\nP1\n"

    assert list(tmp_path.iterdir()) == []


def test_staged_upload_is_removed_after_processing_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(imports, "UPLOAD_DIR", tmp_path)

    with pytest.raises(RuntimeError, match="parse failed"):
        with imports._staged_upload(upload()) as path:
            assert path.exists()
            raise RuntimeError("parse failed")

    assert list(tmp_path.iterdir()) == []


def test_partial_upload_is_removed_when_copy_fails(tmp_path, monkeypatch):
    class BrokenStream(io.BytesIO):
        def read(self, size=-1):
            if self.tell() > 0:
                raise OSError("connection lost")
            return super().read(1)

    monkeypatch.setattr(imports, "UPLOAD_DIR", tmp_path)
    broken = UploadFile(filename="policies.csv", file=BrokenStream(b"policy_id\nP1\n"))

    with pytest.raises(OSError, match="connection lost"):
        imports._save_upload(broken)

    assert list(tmp_path.iterdir()) == []


def test_upload_filename_cannot_escape_staging_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(imports, "UPLOAD_DIR", tmp_path)

    with imports._staged_upload(upload("../../outside.csv")) as path:
        assert path.parent == tmp_path
        assert path.name.endswith("_outside.csv")


def test_inforce_upload_delegates_to_governed_lifecycle(monkeypatch):
    session = RecordingSession()
    row = SimpleNamespace(id="session-1")
    observed = {}

    monkeypatch.setattr(imports.data_manager_service, "create_session",
                        lambda db, payload, user: {"id": "session-1"})
    monkeypatch.setattr(imports.data_manager_service, "require_session_access",
                        lambda db, user, session_id, write: row)
    def upload_file(db, lifecycle_row, filename, content, user):
        observed.update(filename=filename, content=content)
        return {"row_count": 1, "columns": ["policy_id"]}
    monkeypatch.setattr(imports.data_manager_service, "upload_file", upload_file)
    monkeypatch.setattr(imports.data_manager_service, "set_mapping",
                        lambda db, lifecycle_row, payload, user: None)
    monkeypatch.setattr(imports.data_manager_service, "validate_session",
                        lambda db, lifecycle_row, user: {"id": "validation-1", "error_count": 0})
    monkeypatch.setattr(imports.data_manager_service, "validation_issues", lambda db, value: [])
    monkeypatch.setattr(imports.data_manager_service, "commit_session",
                        lambda db, lifecycle_row, user: {"id": "dataset-1", "status": "validated"})
    monkeypatch.setattr(imports, "require_active_project", lambda db, project_id: None)
    monkeypatch.setattr(imports.access, "require_project_access", lambda db, user, project_id, write: None)

    result = imports.upload_inforce(user=object(), file=upload(), project_id="project-1", db=session)

    assert result["stored_count"] == 1
    assert result["dataset_id"] == "dataset-1"
    assert observed == {"filename": "policies.csv", "content": b"policy_id\nP1\n"}
    # Transaction boundaries belong to the persistent lifecycle service.
    assert session.commits == 0
    assert session.rollbacks == 0


def test_inforce_upload_preserves_session_when_lifecycle_upload_fails(monkeypatch):
    session = RecordingSession()
    row = SimpleNamespace(id="session-1")
    monkeypatch.setattr(imports.data_manager_service, "create_session",
                        lambda db, payload, user: {"id": "session-1"})
    monkeypatch.setattr(imports.data_manager_service, "require_session_access",
                        lambda db, user, session_id, write: row)
    def failed_upload(db, lifecycle_row, filename, content, user):
        raise RuntimeError("database write failed")
    monkeypatch.setattr(imports.data_manager_service, "upload_file", failed_upload)
    monkeypatch.setattr(imports, "require_active_project", lambda db, project_id: None)
    monkeypatch.setattr(imports.access, "require_project_access", lambda db, user, project_id, write: None)

    with pytest.raises(RuntimeError, match="database write failed"):
        imports.upload_inforce(user=object(), file=upload(), project_id="project-1", db=session)

    assert session.commits == 0
    assert session.rollbacks == 0
