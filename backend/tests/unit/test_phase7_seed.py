from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core import lifecycle
from app.db.database import Base
from app.db.models.inforce import InforceFile
from app.db.models.project import Project
from app.db.models.projection import ProjectionSet
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
    finally:
        engine.dispose()
