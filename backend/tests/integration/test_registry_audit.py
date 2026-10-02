"""Registry mutations record their actor and before/after state in the same transaction."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user
from app.db.database import get_db
from app.db.models.audit_log import AuditLog
from app.db.models.formula import FormulaRegistry
from app.db.models.formula_dependency import FormulaDependency
from app.db.models.user import User
from app.db.models.variable import VariableRegistry
from app.main import app


@pytest.fixture()
def registry_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in (
        User.__table__, VariableRegistry.__table__, FormulaRegistry.__table__,
        FormulaDependency.__table__, AuditLog.__table__,
    ):
        table.create(engine)
    session = sessionmaker(bind=engine)()
    actor_id = "registry-actor"
    actor = User(id=actor_id, email="registry@example.com", full_name="Registry Actor", password_hash="unused")
    session.add(actor)
    session.commit()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=actor_id,
        roles=[SimpleNamespace(name="admin", permissions=[
            SimpleNamespace(name="registries:read"), SimpleNamespace(name="registries:write"),
        ])],
    )
    try:
        with TestClient(app) as client:
            yield client, session, actor_id
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def test_variable_and_formula_mutations_have_actor_and_audit_history(registry_client):
    client, session, actor_id = registry_client
    for name, kind in (("premium", "input"), ("reserve", "output")):
        response = client.post("/v1/variables/", json={"id": f"client-{name}", "name": name, "kind": kind})
        assert response.status_code == 201, response.text

    changed = client.put("/v1/variables/premium", json={"description": "Updated input"})
    assert changed.status_code == 200, changed.text
    premium = session.query(VariableRegistry).filter_by(name="premium").one()
    assert (premium.created_by, premium.updated_by) == (actor_id, actor_id)

    created = client.post("/v1/formulas/", json={
        "id": "formula-reserve", "name": "Reserve", "output_variable": "reserve",
        "function_ref": "spia.reserve", "dependencies": ["premium"],
    })
    assert created.status_code == 201, created.text
    changed = client.put("/v1/formulas/formula-reserve", json={"status": "active"})
    assert changed.status_code == 200, changed.text
    formula = session.get(FormulaRegistry, "formula-reserve")
    assert (formula.created_by, formula.updated_by) == (actor_id, actor_id)

    assert client.delete("/v1/formulas/formula-reserve").status_code == 204
    assert client.delete("/v1/variables/premium").status_code == 204

    events = session.query(AuditLog).order_by(AuditLog.id).all()
    assert [event.action for event in events] == [
        "variable.created", "variable.created", "variable.updated",
        "formula.created", "formula.updated", "formula.deleted", "variable.deleted",
    ]
    assert all(event.actor_user_id == actor_id for event in events)
    assert events[2].before_state["description"] is None
    assert events[2].after_state["description"] == "Updated input"
    assert events[3].after_state["dependencies"] == ["premium"]
    assert events[4].before_state["status"] == "draft"
    assert events[4].after_state["status"] == "active"
    assert events[5].before_state["function_ref"] == "spia.reserve"
    assert events[5].after_state is None
    assert events[6].before_state["name"] == "premium"
