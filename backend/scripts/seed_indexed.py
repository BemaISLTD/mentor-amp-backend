"""Seed isolated illustrative FIA/RILA models using existing catalog validators.

No actuarial approvals are fabricated. These models execute in sandbox mode until
independent owner review; frozen packages still retain all source fingerprints.
"""

from datetime import date
from pathlib import Path
import csv
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import seed_demo as catalog
from app.core import lifecycle
from app.core.execution.fingerprints import inforce_fingerprint, INFORCE_FINGERPRINT_SCHEME, scenario_fingerprint, table_fingerprint
from app.data.validation.inforce_validator import validate_inforce
from app.db.models.data_manager import ImportSession
from app.services.data_manager_service import _validate_inforce
from app.data.validation.table_validator import validate_lookup_table
from app.db.models.factor import FactorSet, FactorTable
from app.db.models.inforce import InforceFile, InforceRecord
from app.db.models.projection import ProjectionSet
from app.db.models.scenario import ScenarioSet, ScenarioTable
from app.products.fia_lite import config as fia
from app.products.rila_lite import config as rila
from app.products.registry import register_all_products
from app.products.shared_modules.synthetic import block_rows, index_paths
from app.services import projection_set_service
from app.services.model_definition import variable_specs
from app.services.run_package_service import override_problems


def seed_indexed(db, code="RILA", *, count=30, months=72, project_name=None):
    if code not in {"FIA", "RILA"} or not 1 <= months <= 120 or count < 1:
        raise ValueError("Unsupported synthetic block configuration")
    product = fia if code == "FIA" else rila
    register_all_products()
    user = catalog.ensure_demo_user(db)
    project = catalog.upsert_project(db, user, project_name or f"M4 {code} illustrative block")
    model, version, group = catalog.upsert_model(db, project, user, product)
    if version.approved_at or version.published_at:
        raise ValueError("Refusing to edit an approved model; create a new version")
    catalog.upsert_variables(db, product)
    catalog.upsert_definitions(db, version, product)
    catalog.upsert_formulas(db, version, group, product)
    catalog.upsert_published_outputs(db, version, product)
    checks = catalog.validate_model(db, model, version)
    if checks["status"] != "validated":
        raise ValueError(checks)
    rows = block_rows(code, count)
    if code == "RILA" and count == 30:
        fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/rila_representative_block_inforce.csv"
        with fixture.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    errors = validate_inforce(rows)
    product_issues = []
    _validate_inforce(ImportSession(options={"valuation_date": "2025-12-31"}), rows, product_issues)
    errors += [issue for issue in product_issues if issue["severity"] == "error"]
    if errors:
        raise ValueError(errors)
    file = db.query(InforceFile).filter_by(project_id=project.id, filename=f"synthetic_{code}.csv").first()
    digest = inforce_fingerprint((row["policy_id"], row) for row in rows)
    if file is None:
        file = InforceFile(project_id=project.id, filename=f"synthetic_{code}.csv", file_type="csv")
        db.add(file)
        db.flush()
        db.add_all(InforceRecord(file_id=file.id, policy_id=row["policy_id"], data=row) for row in rows)
        file.row_count = len(rows)
        file.columns_detected = list(rows[0])
        file.version_label = "synthetic-v1"
        file.fingerprint = digest
        file.fingerprint_scheme = INFORCE_FINGERPRINT_SCHEME
        file.status = lifecycle.VALIDATED
    elif file.fingerprint != digest:
        raise ValueError("Synthetic inforce changed; use a new project/version")
    factors = db.query(FactorSet).filter_by(project_id=project.id, name=f"{code} synthetic factors").first()
    if factors is None:
        factors = FactorSet(project_id=project.id, name=f"{code} synthetic factors")
        db.add(factors)
        db.flush()
    tables = []
    for name, keys, value, data in (
        (f"{code}_INDEX_PATHS", ["path_id", "month", "index_code"], "level", index_paths(months)),
        (f"{code}_SURRENDER", ["policy_year"], "rate", [{"policy_year": y, "rate": max(0, .07 - .01 * (y - 1))} for y in range(1, 12)]),
        (f"{code}_OPTION_BUDGET", ["policy_year"], "budget", [{"policy_year": y, "budget": .02} for y in range(1, 12)]),
    ):
        errors = validate_lookup_table(data, keys, value)
        if errors:
            raise ValueError(errors)
        table = db.query(FactorTable).filter_by(set_id=factors.id, table_name=name).first()
        digest = table_fingerprint(data)
        if table is None:
            table = FactorTable(set_id=factors.id, table_name=name, table_type="synthetic",
                                lookup_keys=keys, value_column=value, data=data, fingerprint=digest,
                                version_label="synthetic-v1", status=lifecycle.VALIDATED)
            db.add(table)
        elif table.fingerprint != digest:
            raise ValueError("Synthetic factors changed; use a new project/version")
        tables.append(table)
    scenario_set = db.query(ScenarioSet).filter_by(project_id=project.id, name=f"{code} synthetic paths").first()
    if scenario_set is None:
        scenario_set = ScenarioSet(project_id=project.id, name=f"{code} synthetic paths")
        db.add(scenario_set)
        db.flush()
    scenarios = []
    for path, name in enumerate(("Base", "Bull", "Bear", "Stress")):
        overrides = [{"target_variable": "path_id", "operation": "set", "value": path}]
        problems = override_problems(name, overrides, variable_specs(db, version.id))
        if problems:
            raise ValueError(problems)
        scenario = db.query(ScenarioTable).filter_by(set_id=scenario_set.id, scenario_name=name).first()
        if scenario is None:
            scenario = ScenarioTable(set_id=scenario_set.id, scenario_name=name, overrides=overrides,
                                     fingerprint=scenario_fingerprint(overrides), version_label="synthetic-v1",
                                     status=lifecycle.VALIDATED, as_of_date=date(2025, 12, 31))
            db.add(scenario)
        scenarios.append(scenario)
    db.flush()
    projection = db.query(ProjectionSet).filter_by(project_id=project.id, name=f"{code} representative block").first()
    if projection is None:
        projection = ProjectionSet(project_id=project.id, name=f"{code} representative block", version_label="v1",
                                   created_by=user.id, valuation_date=date(2025, 12, 31), horizon_months=months)
        db.add(projection)
    projection.model_version_id = version.id
    projection.inforce_file_ids = [file.id]
    projection.factor_table_ids = [table.id for table in tables]
    projection.assumption_table_ids = []
    projection.scenario_ids = [scenario.id for scenario in scenarios]
    projection.output_variables = [v["variable_name"] for v in product.PUBLISHED_OUTPUTS]
    projection.trace_scope = {"mode": "selected_policies", "policy_ids": [rows[0]["policy_id"]]}
    projection.parameters = {"governance_mode": "sandbox", "actuarial_status": "PENDING_OWNER_APPROVAL"}
    projection.status = lifecycle.DRAFT
    db.commit()
    validation = projection_set_service.validate(db, projection.id, user)
    if validation["status"] not in {"validated", "needs_review"}:
        raise ValueError(validation)
    return {"project": project, "user": user, "model": model, "version": version,
            "inforce": file, "factors": tables, "scenarios": scenarios,
            "projection_set": projection, "validation": validation}


if __name__ == "__main__":
    with catalog.SessionLocal() as session:
        for code in ("FIA", "RILA"):
            result = seed_indexed(session, code)
            print(code, result["project"].id, result["projection_set"].id, result["validation"]["status"])
