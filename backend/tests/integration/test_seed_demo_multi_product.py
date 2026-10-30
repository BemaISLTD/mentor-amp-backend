"""The demo seeder provisions runnable SPIA and MYGA examples without duplicates."""

import csv
import json
import shutil
from pathlib import Path

from app.db.models.assumption import AssumptionTable
from app.db.models.factor import FactorTable
from app.db.models.inforce import InforceFile, InforceRecord
from app.db.models.modeling import Model, ModelVersion
from app.db.models.project import Project
from app.db.models.projection import ProjectionSet
from app.db.models.reconciliation import ExpectedResultSet
from app.db.models.scenario import ScenarioTable

from .wp1_support import Env


def test_seed_01_multi_product_seed_is_idempotent_and_myga_is_runnable():
    env = Env()
    try:
        with env.Session() as db:
            env.seed_module.seed(db)
            first = env.seed_module.seed_myga(db)
            first_ids = {
                "project": first["project"].id,
                "model": first["model"].id,
                "version": first["version"].id,
                "inforce": first["inforce"].id,
                "mortality": first["mortality"].id,
                "factors": [row.id for row in first["factors"]],
                "scenarios": [row.id for row in first["scenarios"]],
                "projection_set": first["projection_set"].id,
            }
            assert first["version"].status == "published"
            assert first["version"].published_at is not None
            assert first["inforce"].status == "approved"
            assert first["inforce"].committed_by != first["inforce"].approved_by
            assert first["mortality"].status == "approved"
            assert first["mortality"].committed_by != first["mortality"].approved_by
            assert all(row.status == "approved" for row in first["factors"])
            assert all(row.committed_by != row.approved_by for row in first["factors"])
            assert all(row.status == "approved" for row in first["scenarios"])
            assert all(row.committed_by != row.approved_by for row in first["scenarios"])
            assert first["validation"]["status"] == "validated"
            assert {"account_value_open", "premium_cash_flow", "interest_credited"}.issubset(
                set(first["projection_set"].output_variables)
            )

            second = env.seed_module.seed_myga(db)
            assert second["project"].id == first_ids["project"]
            assert second["model"].id == first_ids["model"]
            assert second["version"].id == first_ids["version"]
            assert second["projection_set"].id == first_ids["projection_set"]

            assert db.query(Project).count() == 2
            assert db.query(Model).count() == 2
            assert db.query(ModelVersion).count() == 2
            expected_inforce_count = (
                3
                if (env.seed_module.MYGA_SAMPLES / env.seed_module.MYGA_REFERENCE_INFORCE_FILENAME).is_file()
                else 2
            )
            assert db.query(InforceFile).count() == expected_inforce_count
            if first["reference_inforce"] is not None:
                assert first["reference_inforce"].filename == env.seed_module.MYGA_REFERENCE_INFORCE_FILENAME
                assert first["reference_inforce"].row_count == 1
                assert first["reference_inforce"].status == "approved"
            if first["expected_result_set"] is not None:
                assert first["expected_result_set"].row_count == 60
                assert first["expected_result_set"].status == "approved"
            assert db.query(InforceRecord).filter_by(file_id=first_ids["inforce"]).count() == 25
            assert db.query(AssumptionTable).filter_by(id=first_ids["mortality"]).count() == 1
            assert db.query(FactorTable).filter(FactorTable.id.in_(first_ids["factors"])).count() == 2
            assert db.query(ScenarioTable).filter(ScenarioTable.id.in_(first_ids["scenarios"])).count() == 2
            assert db.query(ProjectionSet).filter_by(id=first_ids["projection_set"]).count() == 1
    finally:
        env.close()


def test_seed_01_registers_optional_myga_10001_package_idempotently(tmp_path, monkeypatch):
    env = Env()
    try:
        source_dir = Path(env.seed_module.MYGA_SAMPLES)
        for source in source_dir.iterdir():
            if source.is_file():
                shutil.copy2(source, tmp_path / source.name)

        with (tmp_path / env.seed_module.MYGA_INFORCE_FILENAME).open(
            newline="", encoding="utf-8-sig"
        ) as handle:
            inforce_row = next(csv.DictReader(handle))
        inforce_row["policy_id"] = "MYGA-10001"
        reference_inforce = tmp_path / env.seed_module.MYGA_REFERENCE_INFORCE_FILENAME
        with reference_inforce.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(inforce_row))
            writer.writeheader()
            writer.writerow(inforce_row)

        fixture = json.loads(
            (Path(__file__).parents[1] / "fixtures" / "myga_reference_simplified_v1.json")
            .read_text(encoding="utf-8")
        )
        expected_rows = fixture["anchor_case"]["rows"]
        expected_path = tmp_path / env.seed_module.MYGA_EXPECTED_RESULTS_FILENAME
        with expected_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(expected_rows[0]))
            writer.writeheader()
            for row in expected_rows:
                writer.writerow({**row, "policy_id": "MYGA-10001"})

        monkeypatch.setattr(env.seed_module, "MYGA_SAMPLES", tmp_path)
        with env.Session() as db:
            first = env.seed_module.seed_myga(db, project_name="MYGA optional package")
            assert first["reference_inforce"].filename == env.seed_module.MYGA_REFERENCE_INFORCE_FILENAME
            assert first["reference_inforce"].row_count == 1
            assert first["expected_result_set"].row_count == 60
            assert first["projection_set"].inforce_file_ids == [first["reference_inforce"].id]
            assert first["projection_set"].horizon_months == 60
            assert first["projection_set"].trace_scope["policy_ids"] == ["MYGA-10001"]
            assert {
                "account_value_open", "gross_withdrawal", "surrender_charge_on_withdrawal",
                "net_withdrawal_benefit", "net_cash_flow",
            }.issubset(first["projection_set"].output_variables)

            second = env.seed_module.seed_myga(db, project_name="MYGA optional package")
            assert second["reference_inforce"].id == first["reference_inforce"].id
            assert second["expected_result_set"].id == first["expected_result_set"].id
            assert db.query(ExpectedResultSet).filter_by(
                project_id=first["project"].id
            ).count() == 1
    finally:
        env.close()
