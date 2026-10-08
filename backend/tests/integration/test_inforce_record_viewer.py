from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user
from app.db.database import get_db
from app.db.models.inforce import InforceFile, InforceRecord
from app.db.models.project import Project
from app.main import app


@pytest.fixture()
def viewer():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Project.__table__.create(engine)
    InforceFile.__table__.create(engine)
    InforceRecord.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    session.add(Project(id="project-1", name="Viewer project"))
    infile = InforceFile(
        id="file-1",
        project_id="project-1",
        filename="policies.csv",
        file_type="csv",
        row_count=3,
        columns_detected=["policy_id", "state", "age"],
    )
    session.add(infile)
    session.add_all(
        [
            InforceRecord(id=1, file_id=infile.id, policy_id="P001", data={"state": "NY", "age": 65}),
            InforceRecord(id=2, file_id=infile.id, policy_id="P002", data={"state": "CA", "age": 72}),
            InforceRecord(id=3, file_id=infile.id, policy_id="P003", data={"state": "NY", "age": 80}),
        ]
    )
    session.commit()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="reviewer",
        roles=[
            SimpleNamespace(
                name="admin",
                permissions=[SimpleNamespace(name="imports:read")],
            )
        ],
    )
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    session.close()
    engine.dispose()


def test_record_viewer_paginates_and_reports_metadata(viewer):
    response = viewer.get(
        "/v1/imports/inforce/file-1/records",
        params={"page": 2, "page_size": 2},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "policies.csv"
    assert body["columns"] == ["policy_id", "state", "age"]
    assert body["total"] == 3
    assert body["total_pages"] == 2
    assert [record["policy_id"] for record in body["records"]] == ["P003"]


def test_record_viewer_filters_declared_text_and_numeric_columns(viewer):
    state = viewer.get(
        "/v1/imports/inforce/file-1/records",
        params={"filter_column": "state", "filter_operator": "eq", "filter_value": "NY"},
    )
    age = viewer.get(
        "/v1/imports/inforce/file-1/records",
        params={"filter_column": "age", "filter_operator": "gte", "filter_value": "70"},
    )

    assert state.status_code == 200
    assert [row["policy_id"] for row in state.json()["records"]] == ["P001", "P003"]
    assert age.status_code == 200
    assert [row["policy_id"] for row in age.json()["records"]] == ["P002", "P003"]


def test_record_viewer_rejects_undeclared_columns_and_unbounded_pages(viewer):
    unsafe = viewer.get(
        "/v1/imports/inforce/file-1/records",
        params={"filter_column": "unknown", "filter_value": "x"},
    )
    oversized = viewer.get(
        "/v1/imports/inforce/file-1/records",
        params={"page_size": 201},
    )

    assert unsafe.status_code == 400
    assert unsafe.json()["error"]["code"] == "BAD_REQUEST"
    assert oversized.status_code == 422
    assert oversized.json()["error"]["code"] == "VALIDATION_ERROR"


def test_record_viewer_returns_not_found_for_unknown_file(viewer):
    response = viewer.get("/v1/imports/inforce/missing/records")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
