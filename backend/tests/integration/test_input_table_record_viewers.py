"""Paginated assumption and factor table record viewers."""

import pytest

from app.db.models.factor import FactorSet, FactorTable

from .wp1_support import Env


@pytest.fixture()
def env():
    environment = Env()
    environment.seed("VIEWERS")
    yield environment
    environment.close()


def test_viewer_01_assumption_records_paginate_and_filter(env):
    table_id = env.projects["VIEWERS"]["table"]
    page = env.client.get(
        f"/v1/inputs/assumptions/{table_id}/records",
        params={"limit": 2, "offset": 1, "search": "M"},
        headers=env.admin_headers(),
    )
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["table_id"] == table_id
    assert body["limit"] == 2
    assert body["offset"] == 1
    assert len(body["records"]) <= 2
    assert {"age", "gender", "qx"}.issubset(body["columns"])


def test_viewer_01_factor_records_paginate_and_filter(env):
    project_id = env.projects["VIEWERS"]["project"]
    with env.Session() as db:
        factor_set = FactorSet(project_id=project_id, name="Viewer factors")
        db.add(factor_set)
        db.flush()
        table = FactorTable(
            set_id=factor_set.id, table_name="Surrender charge", table_type="surrender_charge",
            lookup_keys=["year"], value_column="rate", status="approved",
            version_label="v1", version_group_id="viewer-factor",
            data=[{"year": 1, "rate": 0.07}, {"year": 2, "rate": 0.06}, {"year": 3, "rate": 0.05}],
        )
        db.add(table)
        db.commit()
        table_id = table.id
    page = env.client.get(
        f"/v1/inputs/factors/{table_id}/records",
        params={"limit": 1, "offset": 0, "search": "0.06"},
        headers=env.admin_headers(),
    )
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 1
    assert page.json()["records"] == [{"year": 2, "rate": 0.06}]
