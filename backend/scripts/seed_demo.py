"""Seed the Milestone 1 demo into the configured database (idempotent).

Creates (or updates) the demo user, project (with the demo user as owner), SPIA illustrative
model, variables, formulas, published outputs, synthetic mortality table, synthetic inforce,
scenarios and a Projection Set. Running it twice creates nothing new. Contract: §G.

Nothing is marked "validated" by assumption: every status comes from a validator that ran here
(model checks, the inforce validator, the lookup-table validator, scenario override checks,
Projection Set validation). This script is also the explicit setup step that provisions the demo
user — request authentication never creates users.

Usage (from the repository root):
  backend/.venv/Scripts/python backend/scripts/seed_demo.py
"""

import csv
import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core import lifecycle  # noqa: E402
from app.core.execution.fingerprints import (  # noqa: E402
    inforce_fingerprint,
    scenario_fingerprint,
    table_fingerprint,
)
from app.core.formula_engine.formulas import FORMULA_FUNCTIONS, FORMULA_METADATA  # noqa: E402
from app.data.validation.inforce_validator import validate_inforce  # noqa: E402
from app.data.validation.table_validator import validate_lookup_table  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402
from app.db.models.assumption import AssumptionSet, AssumptionTable  # noqa: E402
from app.db.models.formula import FormulaRegistry  # noqa: E402
from app.db.models.formula_dependency import FormulaDependency  # noqa: E402
from app.db.models.inforce import InforceFile, InforceRecord  # noqa: E402
from app.db.models.modeling import FormulaGroup, Model, ModelPublishedOutput, ModelVersion  # noqa: E402
from app.db.models.project import Project  # noqa: E402
from app.db.models.projection import ProjectionSet  # noqa: E402
from app.db.models.scenario import ScenarioSet, ScenarioTable  # noqa: E402
from app.db.models.variable import VariableRegistry  # noqa: E402
from app.products.registry import register_all_products  # noqa: E402
from app.products.spia_lite import config as spia  # noqa: E402
from app.services import catalog_service, projection_set_service  # noqa: E402
from app.services.bootstrap import ensure_demo_user, ensure_member  # noqa: E402
from app.services.model_definition import to_variable_spec  # noqa: E402
from app.services.run_package_service import override_problems  # noqa: E402

SAMPLES = BACKEND.parent / "samples" / "spia"
PROJECT_NAME = "MentorAmp Demo — SPIA (Illustrative)"
INFORCE_FILENAME = "synthetic_spia_inforce.csv"
VALUATION_DATE = date(2026, 12, 31)
SCENARIO_SET_NAME = "Illustrative deterministic scenarios"
PROJECTION_SET_NAME = "SPIA Illustrative — Q4 2026"
TRACED_POLICIES = ["SPIA-0001", "SPIA-0002", "SPIA-0003"]


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def upsert_project(db, user, name: str = PROJECT_NAME) -> Project:
    project = db.query(Project).filter(Project.name == name).first()
    if project is None:
        project = Project(
            name=name,
            description="Synthetic data and illustrative formulas for the first end-to-end run (M1).",
        )
        db.add(project)
        db.flush()
    ensure_member(db, project.id, user.id)
    return project


def upsert_model(db, project, user) -> tuple[Model, ModelVersion, FormulaGroup]:
    definition = spia.MODEL
    model = db.query(Model).filter(Model.project_id == project.id, Model.name == definition["name"]).first()
    if model is None:
        model = Model(project_id=project.id, name=definition["name"], status=lifecycle.DRAFT)
        db.add(model)
    model.product_code = definition["product_code"]
    model.description = definition["description"]
    model.owner_user_id = user.id
    db.flush()

    version_def = definition["version"]
    version = (
        db.query(ModelVersion)
        .filter(ModelVersion.model_id == model.id, ModelVersion.version_label == version_def["version_label"])
        .first()
    )
    if version is None:
        version = ModelVersion(model_id=model.id, version_label=version_def["version_label"], status=lifecycle.DRAFT)
        db.add(version)
    for field in ("block_name", "profile_name", "basis", "methodology", "notes"):
        setattr(version, field, version_def[field])
    version.is_current = True
    version.illustrative = True
    db.flush()

    group_def = definition["formula_group"]
    group = (
        db.query(FormulaGroup)
        .filter(FormulaGroup.model_version_id == version.id, FormulaGroup.name == group_def["name"])
        .first()
    )
    if group is None:
        group = FormulaGroup(model_version_id=version.id, name=group_def["name"])
        db.add(group)
    group.description = group_def["description"]
    group.lineage_state = group_def["lineage_state"]
    group.version_label = group_def["version_label"]
    db.flush()
    return model, version, group


def upsert_variables(db) -> None:
    for item in spia.VARIABLES:
        row = db.query(VariableRegistry).filter(VariableRegistry.name == item["name"]).first()
        if row is None:
            row = VariableRegistry(name=item["name"])
            db.add(row)
        row.display_name = item["display_name"]
        row.description = item["description"]
        row.data_type = item["data_type"]
        row.source_type = item["kind"]
        row.source = dict(item["source"])
        row.source_table = item["source"].get("table")
        row.lookup_keys = list((item["source"].get("key_map") or {}).keys())
        row.unit = item.get("unit")
        row.required = item.get("required", True)
        default = item.get("default_value")
        row.default_value = {"value": default} if default is not None else None
        row.product_applicability = [spia.PRODUCT_CODE]
        row.basis_applicability = []
        row.version = "v1"
    db.flush()


def upsert_formulas(db, version, group) -> None:
    for item in spia.FORMULAS:
        meta = FORMULA_METADATA[item["function_ref"]]
        row = (
            db.query(FormulaRegistry)
            .filter(FormulaRegistry.model_version_id == version.id,
                    FormulaRegistry.output_variable == item["output_variable"])
            .first()
        )
        if row is None:
            row = FormulaRegistry(output_variable=item["output_variable"], model_version_id=version.id)
            db.add(row)
        row.name = item["name"]
        row.function_ref = item["function_ref"]
        row.category = spia.PRODUCT_CODE
        row.product_applicability = [spia.PRODUCT_CODE]
        row.basis_applicability = [version.basis]
        row.version = "v1"
        # Validated only if its implementation is registered (checked, not assumed).
        row.status = lifecycle.VALIDATED if row.function_ref in FORMULA_FUNCTIONS else lifecycle.DRAFT
        row.group_id = group.id
        row.expression_text = meta.expression_text
        row.explanation = meta.explanation
        row.unit = item.get("unit")
        row.illustrative = True
        db.flush()
        existing = {dep.depends_on_variable for dep in row.dependencies}
        wanted = set(item["dependencies"])
        for dep in list(row.dependencies):
            if dep.depends_on_variable not in wanted:
                db.delete(dep)
        for name in sorted(wanted - existing):
            db.add(FormulaDependency(formula_id=row.id, depends_on_variable=name))
    db.flush()


def upsert_published_outputs(db, version) -> None:
    for order, item in enumerate(spia.PUBLISHED_OUTPUTS):
        row = (
            db.query(ModelPublishedOutput)
            .filter(ModelPublishedOutput.model_version_id == version.id,
                    ModelPublishedOutput.variable_name == item["variable_name"])
            .first()
        )
        if row is None:
            row = ModelPublishedOutput(model_version_id=version.id, variable_name=item["variable_name"])
            db.add(row)
        for field in ("display_name", "unit", "dimension", "aggregation", "description"):
            setattr(row, field, item[field])
        row.sort_order = order
    db.flush()


def validate_model(db, model, version) -> dict:
    formulas = db.query(FormulaRegistry).filter(FormulaRegistry.model_version_id == version.id).all()
    published = db.query(ModelPublishedOutput).filter(ModelPublishedOutput.model_version_id == version.id).all()
    checks = catalog_service.model_version_checks(db, formulas, published)
    status = lifecycle.VALIDATED if checks["status"] == "validated" else lifecycle.DRAFT
    version.status = status
    model.status = status
    db.flush()
    return checks


def upsert_mortality(db, project) -> AssumptionTable:
    assumption_set = (
        db.query(AssumptionSet)
        .filter(AssumptionSet.project_id == project.id, AssumptionSet.name == "Illustrative assumptions")
        .first()
    )
    if assumption_set is None:
        assumption_set = AssumptionSet(
            project_id=project.id, name="Illustrative assumptions",
            description="SYNTHETIC assumptions for the M1 demo.",
        )
        db.add(assumption_set)
        db.flush()
    rows = [
        {"age": int(row["age"]), "gender": row["gender"], "qx": float(row["qx"])}
        for row in read_csv(SAMPLES / "synthetic_mortality_gompertz.csv")
    ]
    table = (
        db.query(AssumptionTable)
        .filter(AssumptionTable.set_id == assumption_set.id, AssumptionTable.table_name == spia.MORTALITY_TABLE_NAME)
        .first()
    )
    if table is None:
        table = AssumptionTable(set_id=assumption_set.id, table_name=spia.MORTALITY_TABLE_NAME)
        db.add(table)
    table.table_type = "mortality"
    table.lookup_keys = ["age", "gender"]
    table.value_column = "qx"
    table.data = rows
    table.version_label = "v1"
    errors = validate_lookup_table(rows, table.lookup_keys, table.value_column, (0.0, 1.0))
    table.status = lifecycle.NEEDS_REVIEW if errors else lifecycle.VALIDATED
    table.fingerprint = table_fingerprint(rows)
    table.description = (
        "Synthetic Gompertz mortality by age and gender (SYNTHETIC — not a published table). "
        "Terminal age 120."
    )
    db.flush()
    return table


def upsert_inforce(db, project) -> InforceFile:
    rows = read_csv(SAMPLES / INFORCE_FILENAME)
    file = (
        db.query(InforceFile)
        .filter(InforceFile.project_id == project.id, InforceFile.filename == INFORCE_FILENAME)
        .first()
    )
    if file is None:
        file = InforceFile(project_id=project.id, filename=INFORCE_FILENAME, file_type="csv")
        db.add(file)
        db.flush()
        db.add_all(InforceRecord(file_id=file.id, policy_id=row["policy_id"], data=row) for row in rows)
    file.row_count = len(rows)
    file.columns_detected = list(rows[0].keys())
    errors = validate_inforce(rows)
    file.status = lifecycle.NEEDS_REVIEW if errors else lifecycle.VALIDATED
    file.version_label = "v2026.12"
    # Same order the run loader uses: policy_id, then load order.
    file.fingerprint = inforce_fingerprint(sorted(rows, key=lambda row: row["policy_id"]))
    file.description = "25 synthetic SPIA policies (SYNTHETIC — not real policyholders)"
    db.flush()
    return file


def upsert_scenarios(db, project) -> list[ScenarioTable]:
    scenario_set = (
        db.query(ScenarioSet)
        .filter(ScenarioSet.project_id == project.id, ScenarioSet.name == SCENARIO_SET_NAME)
        .first()
    )
    if scenario_set is None:
        scenario_set = ScenarioSet(
            project_id=project.id, name=SCENARIO_SET_NAME,
            description="Base and a lower discount rate. SYNTHETIC.",
        )
        db.add(scenario_set)
        db.flush()
    variables = {row.name: to_variable_spec(row) for row in db.query(VariableRegistry).all()}
    definitions = [
        ("Base", "Discount rate 4.5% (the variable default).", []),
        ("Low Interest Rate", "Discount rate set to 3.0%.",
         [{"target_variable": "discount_rate_annual", "operation": "set", "value": 0.03,
           "applies_from_period": None, "applies_to_period": None}]),
    ]
    scenarios = []
    for name, description, overrides in definitions:
        scenario = (
            db.query(ScenarioTable)
            .filter(ScenarioTable.set_id == scenario_set.id, ScenarioTable.scenario_name == name)
            .first()
        )
        if scenario is None:
            scenario = ScenarioTable(set_id=scenario_set.id, scenario_name=name)
            db.add(scenario)
        scenario.description = description
        scenario.overrides = overrides
        problems = override_problems(f"Scenario '{name}'", overrides, variables)
        scenario.status = lifecycle.NEEDS_REVIEW if problems else lifecycle.VALIDATED
        scenario.version_label = "v1"
        scenario.scenario_type = "deterministic"
        scenario.as_of_date = VALUATION_DATE
        scenario.path_count = 1
        scenario.fingerprint = scenario_fingerprint(overrides)
        db.flush()
        scenarios.append(scenario)
    return scenarios


def upsert_projection_set(db, project, version, inforce, table, scenarios, user) -> ProjectionSet:
    projection_set = (
        db.query(ProjectionSet)
        .filter(ProjectionSet.project_id == project.id, ProjectionSet.name == PROJECTION_SET_NAME,
                ProjectionSet.version_label == "v1")
        .first()
    )
    if projection_set is None:
        projection_set = ProjectionSet(
            project_id=project.id, name=PROJECTION_SET_NAME, version_label="v1", created_by=user.id,
            valuation_date=VALUATION_DATE, horizon_months=600, status=lifecycle.DRAFT,
        )
        db.add(projection_set)
    projection_set.description = "Illustrative SPIA valuation for the M1 demo."
    projection_set.model_version_id = version.id
    projection_set.inforce_file_ids = [inforce.id]
    projection_set.assumption_table_ids = [table.id]
    projection_set.factor_table_ids = []
    projection_set.scenario_ids = [scenario.id for scenario in scenarios]
    projection_set.valuation_date = VALUATION_DATE
    projection_set.horizon_months = 600
    projection_set.time_step = "monthly"
    projection_set.output_variables = [
        "survival_probability", "expected_payment", "pv_expected_payment", "reserve",
    ]
    projection_set.trace_scope = {"mode": "selected_policies", "policy_ids": TRACED_POLICIES}
    projection_set.parameters = {}
    db.flush()
    return projection_set


def seed(db, project_name: str = PROJECT_NAME) -> dict:
    """Seed one demo project into ``db``; return the objects created (used by tests too)."""
    register_all_products()
    user = ensure_demo_user(db)
    project = upsert_project(db, user, project_name)
    model, version, group = upsert_model(db, project, user)
    upsert_variables(db)
    upsert_formulas(db, version, group)
    upsert_published_outputs(db, version)
    model_checks = validate_model(db, model, version)
    table = upsert_mortality(db, project)
    inforce = upsert_inforce(db, project)
    scenarios = upsert_scenarios(db, project)
    projection_set = upsert_projection_set(db, project, version, inforce, table, scenarios, user)
    db.commit()
    validated = projection_set_service.validate(db, projection_set.id)
    return {
        "user": user, "project": project, "model": model, "version": version,
        "model_checks": model_checks, "table": table, "inforce": inforce, "scenarios": scenarios,
        "projection_set": projection_set, "validation": validated,
    }


def main() -> None:
    db = SessionLocal()
    try:
        seeded = seed(db)
        print("Seed complete.")
        print(f"  project        {seeded['project'].id}  {seeded['project'].name}")
        print(f"  model version  {seeded['version'].id}  {seeded['model'].name} "
              f"{seeded['version'].version_label} ({seeded['version'].status})")
        print(f"  inforce file   {seeded['inforce'].id}  {seeded['inforce'].row_count} policies "
              f"({seeded['inforce'].status})")
        print(f"  mortality      {seeded['table'].id}  {len(seeded['table'].data)} rows ({seeded['table'].status})")
        for scenario in seeded["scenarios"]:
            print(f"  scenario       {scenario.id}  {scenario.scenario_name} ({scenario.status})")
        validated = seeded["validation"]
        print(f"  projection set {seeded['projection_set'].id}  status={validated['status']}")
        for check in validated["validation"]["checks"]:
            print(f"    [{check['status']:7}] {check['label']}: {check['message']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
