"""Tests for immutable, reproducible run manifests."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.run_manifest import create_run_manifest
from app.db.models.formula import FormulaRegistry
from app.db.models.inforce import InforceFile
from app.db.models.run_artifact import RunManifest
from app.db.models.variable import VariableRegistry
from app.models.schemas import ProjectionRunDefinition


class QueryStub:
    def __init__(self, values):
        self.values = values

    def filter(self, *args):
        return self

    def order_by(self, *args):
        return self

    def first(self):
        return self.values[0] if self.values else None

    def all(self):
        return list(self.values)


class SessionStub:
    def __init__(self):
        self.manifest = None
        self.datasets = [
            SimpleNamespace(
                id="file-1",
                filename="policies.csv",
                file_type="csv",
                row_count=2,
                uploaded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        ]
        self.formulas = [
            SimpleNamespace(
                id="formula-1",
                output_variable="reserve",
                function_ref="calculate_reserve",
                version="v3",
                status="active",
                dependencies=[SimpleNamespace(depends_on_variable="account_value")],
            )
        ]
        self.variables = [
            SimpleNamespace(id="variable-1", name="reserve", version="v2")
        ]

    def query(self, model):
        values = {
            RunManifest: [self.manifest] if self.manifest else [],
            InforceFile: self.datasets,
            FormulaRegistry: self.formulas,
            VariableRegistry: self.variables,
        }[model]
        return QueryStub(values)

    def add(self, value):
        if isinstance(value, RunManifest):
            self.manifest = value

    def flush(self):
        pass


def test_manifest_snapshots_versions_and_cannot_be_recreated():
    db = SessionStub()
    run_definition = ProjectionRunDefinition(
        id="run-1",
        name="Quarter End",
        project_id="project-1",
        formula_database_id="formula-db-1",
        dataset_ids=["file-1"],
        scenario_ids=["base"],
        projection_length_months=12,
        selected_output_variables=["reserve"],
    )

    record = create_run_manifest(db, run_definition)

    assert len(record.fingerprint) == 64
    assert record.manifest["datasets"][0]["id"] == "file-1"
    assert record.manifest["formulas"][0]["version"] == "v3"
    assert record.manifest["variables"][0]["version"] == "v2"

    with pytest.raises(ValueError, match="already exists"):
        create_run_manifest(db, run_definition)
