import io

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


def test_inforce_upload_commits_after_staging_cleanup(tmp_path, monkeypatch):
    session = RecordingSession()
    observed_path = None

    def successful_import(file_path, db, validate_func, store_func):
        nonlocal observed_path
        del db, validate_func, store_func
        observed_path = file_path
        assert imports.Path(file_path).exists()
        return {
            "row_count": 1,
            "stored_count": 1,
            "columns": ["policy_id"],
            "errors": [],
        }

    monkeypatch.setattr(imports, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(imports, "import_file", successful_import)
    monkeypatch.setattr(imports, "require_active_project", lambda db, project_id: None)

    result = imports.upload_inforce(upload(), project_id="project-1", db=session)

    assert result["stored_count"] == 1
    assert observed_path is not None and not imports.Path(observed_path).exists()
    assert session.commits == 1
    assert session.rollbacks == 0


def test_inforce_upload_rolls_back_and_removes_file_on_failure(tmp_path, monkeypatch):
    session = RecordingSession()

    def failed_import(file_path, db, validate_func, store_func):
        del db, validate_func, store_func
        assert imports.Path(file_path).exists()
        raise RuntimeError("database write failed")

    monkeypatch.setattr(imports, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(imports, "import_file", failed_import)
    monkeypatch.setattr(imports, "require_active_project", lambda db, project_id: None)

    with pytest.raises(RuntimeError, match="database write failed"):
        imports.upload_inforce(upload(), project_id="project-1", db=session)

    assert list(tmp_path.iterdir()) == []
    assert session.commits == 0
    assert session.rollbacks == 1
