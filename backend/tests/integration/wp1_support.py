"""Shared set-up for the Work Package 1 tests: an in-memory database seeded with real demo
projects, JWT users with project roles, and helpers to submit and execute runs.

Runs use a 24-month horizon (instead of the demo's 600) so the tests stay fast; the engine,
package, loader and persistence paths are exactly the production ones.
"""

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401 - register every table
from app.api.auth import initialize_builtin_roles
from app.core.security import create_access_token, hash_password
from app.db.database import Base, get_db
from app.db.models.project_member import ProjectMember
from app.db.models.run import Run
from app.db.models.run_output import RunOutput
from app.db.models.trace_log import TraceLog
from app.db.models.user import User
from app.main import app
from app.services import projection_set_service, run_execution_service, run_submission_service

BACKEND = Path(__file__).resolve().parents[2]
SHORT_HORIZON = 24


def load_seed_module():
    spec = importlib.util.spec_from_file_location("seed_demo_wp1", BACKEND / "scripts" / "seed_demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Env:
    """One isolated database + API client. Call ``close()`` when done."""

    def __init__(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.monkeypatch = pytest.MonkeyPatch()
        self.monkeypatch.setattr(run_execution_service, "SessionLocal", self.Session)
        self.seed_module = load_seed_module()

        def override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        self.projects: dict[str, dict] = {}

    def close(self) -> None:
        app.dependency_overrides.clear()
        self.monkeypatch.undo()
        self.engine.dispose()

    # -- seeding ---------------------------------------------------------------------------
    def seed(self, key: str, horizon: int = SHORT_HORIZON) -> dict:
        with self.Session() as db:
            seeded = self.seed_module.seed(db, project_name=f"WP1 {key}")
            projection_set = seeded["projection_set"]
            projection_set.horizon_months = horizon
            db.commit()
            validated = projection_set_service.validate(db, projection_set.id)
            assert validated["status"] == "validated", validated["validation"]
            version = seeded["version"]
            ids = {
                "project": seeded["project"].id,
                "model": seeded["model"].id,
                "version": version.id,
                "projection_set": projection_set.id,
                "inforce": seeded["inforce"].id,
                "table": seeded["table"].id,
                "scenarios": {scenario.scenario_name: scenario.id for scenario in seeded["scenarios"]},
                "admin": seeded["user"].id,
            }
        self.projects[key] = ids
        return ids

    # -- users -------------------------------------------------------------------------------
    def user(self, email: str, role: str = "actuary", memberships: dict[str, str] | None = None) -> dict:
        """Create a user with a platform role and project memberships; return auth headers."""
        with self.Session() as db:
            roles = initialize_builtin_roles(db)
            user = User(email=email, full_name=email.split("@")[0], password_hash=hash_password("x" * 12),
                        roles=[roles[role]])
            db.add(user)
            db.flush()
            for project_key, project_role in (memberships or {}).items():
                db.add(ProjectMember(project_id=self.projects[project_key]["project"], user_id=user.id,
                                     role=project_role))
            db.commit()
            user_id = user.id
        return {"id": user_id, "headers": {"Authorization": f"Bearer {create_access_token(user_id)}"}}

    def admin_headers(self) -> dict:
        admin_id = next(iter(self.projects.values()))["admin"]
        return {"Authorization": f"Bearer {create_access_token(admin_id)}"}

    # -- runs --------------------------------------------------------------------------------
    def submit(self, key: str, scenarios: list[str] | None = None, projection_set_id: str | None = None) -> dict:
        """Submit through the service (as the demo admin) WITHOUT executing."""
        ids = self.projects[key]
        with self.Session() as db:
            admin = db.get(User, ids["admin"])
            payload = {
                "project_id": ids["project"],
                "name": "WP1 test",
                "projection_set_ids": [projection_set_id or ids["projection_set"]],
                "scenario_ids": [ids["scenarios"][name] for name in scenarios] if scenarios else [],
            }
            result = run_submission_service.submit_run_set(db, payload, admin)
        result["by_scenario"] = {run["scenario"]["name"]: run["id"] for run in result["runs"]}
        return result

    def execute(self, submitted: dict) -> None:
        run_execution_service.execute_run_set(submitted["run_set"]["id"])

    def run(self, run_id: str) -> Run:
        with self.Session() as db:
            run = db.get(Run, run_id)
            db.expunge(run)
            return run

    def row_counts(self, run_id: str) -> tuple[int, int]:
        with self.Session() as db:
            outputs = db.query(func.count(RunOutput.id)).filter(RunOutput.run_id == run_id).scalar()
            traces = db.query(func.count(TraceLog.id)).filter(TraceLog.run_id == run_id).scalar()
            return outputs, traces

    def get(self, path: str, headers: dict | None = None, **params):
        return self.client.get(f"/v1{path}", params=params, headers=headers or self.admin_headers())

    def summary(self, run_id: str) -> dict:
        response = self.get(f"/runs/{run_id}/summary")
        assert response.status_code == 200, response.text
        return response.json()
