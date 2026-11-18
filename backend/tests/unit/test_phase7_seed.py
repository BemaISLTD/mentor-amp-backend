import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core import lifecycle
from app.db.database import Base
from app.db.models.inforce import InforceFile
from app.db.models.project import Project
from app.db.models.projection import ProjectionSet
from app.services import run_submission_service
from app.services.common import ServiceError
from scripts.seed_phase7 import seed_phase7


def test_phase7_seed_is_idempotent_and_life_versions_are_four_eyes_approved(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'phase7-seed.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with Session() as db:
            first = seed_phase7(db)
            first_ids = {code: item["project"].id for code, item in first.items()}
            second = seed_phase7(db)
            assert {code: item["project"].id for code, item in second.items()} == first_ids

            assert db.query(Project).filter(Project.id.in_(first_ids.values())).count() == 4
            projections = db.query(ProjectionSet).filter(
                ProjectionSet.project_id.in_([first_ids[code] for code in ("IDI", "VA", "PRT")])
            ).all()
            assert len(projections) == 3
            assert all(row.status in lifecycle.RUNNABLE_PROJECTION_SET_STATUSES for row in projections)

            life_versions = db.query(InforceFile).filter(
                InforceFile.project_id == first_ids["LIFE"]
            ).order_by(InforceFile.version_number).all()
            assert [row.version_number for row in life_versions] == [1, 2]
            assert all(row.status == lifecycle.APPROVED for row in life_versions)
            assert all(row.committed_by and row.approved_by for row in life_versions)
            assert all(row.committed_by != row.approved_by for row in life_versions)
            assert life_versions[1].parent_file_id == life_versions[0].id

            life_projection = ProjectionSet(
                project_id=first_ids["LIFE"], name="LIFE calculations excluded",
                status=lifecycle.DRAFT, model_version_id=None,
                inforce_file_ids=[life_versions[-1].id],
                valuation_date=life_versions[-1].uploaded_at.date(), horizon_months=12,
                created_by=first["LIFE"]["user"].id, updated_by=first["LIFE"]["user"].id,
            )
            db.add(life_projection)
            db.flush()
            with pytest.raises(ServiceError) as excluded:
                run_submission_service.submit_run_set(db, {
                    "project_id": first_ids["LIFE"],
                    "projection_set_ids": [life_projection.id],
                    "name": "LIFE exclusion proof",
                }, first["LIFE"]["user"])
            assert excluded.value.status_code == 422
            assert excluded.value.code == "MODEL_NOT_CONFIGURED"
    finally:
        engine.dispose()
