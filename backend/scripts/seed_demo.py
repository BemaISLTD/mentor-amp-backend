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
import secrets
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core import lifecycle  # noqa: E402
from app.core.execution.fingerprints import (  # noqa: E402
    INFORCE_FINGERPRINT_SCHEME,
    inforce_fingerprint,
    scenario_fingerprint,
    table_fingerprint,
)
from app.core.formula_engine.formulas import FORMULA_FUNCTIONS, FORMULA_METADATA  # noqa: E402
from app.data.validation.inforce_validator import validate_inforce  # noqa: E402
from app.data.validation.table_validator import validate_lookup_table  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402
from app.db.models.assumption import AssumptionSet, AssumptionTable  # noqa: E402
from app.db.models.factor import FactorSet, FactorTable  # noqa: E402
from app.db.models.formula import FormulaRegistry  # noqa: E402
from app.db.models.formula_dependency import FormulaDependency  # noqa: E402
from app.db.models.inforce import InforceFile, InforceRecord  # noqa: E402
from app.db.models.model_variable import ModelVariableDefinition  # noqa: E402
from app.db.models.modeling import FormulaGroup, Model, ModelPublishedOutput, ModelVersion  # noqa: E402
from app.db.models.project import Project  # noqa: E402
from app.db.models.projection import ProjectionSet  # noqa: E402
from app.db.models.reconciliation import ExpectedResultSet  # noqa: E402
from app.db.models.scenario import ScenarioSet, ScenarioTable  # noqa: E402
from app.db.models.variable import VariableRegistry  # noqa: E402
from app.db.models.user import User  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.products.registry import register_all_products  # noqa: E402
from app.products.myga_lite import config as myga  # noqa: E402
from app.products.spia_lite import config as spia  # noqa: E402
from app.services import (  # noqa: E402
    catalog_service, model_version_service, projection_set_service, reconciliation_service,
)
from app.services.common import fingerprint  # noqa: E402
from app.services.bootstrap import ensure_demo_user, ensure_member  # noqa: E402
from app.api.auth import initialize_builtin_roles  # noqa: E402
from app.services.model_definition import variable_specs  # noqa: E402
from app.services.run_package_service import override_problems  # noqa: E402

SAMPLES = BACKEND.parent / "samples" / "spia"
PROJECT_NAME = "MentorAmp Demo — SPIA (Illustrative)"
INFORCE_FILENAME = "synthetic_spia_inforce.csv"
VALUATION_DATE = date(2026, 12, 31)
SCENARIO_SET_NAME = "Illustrative deterministic scenarios"
PROJECTION_SET_NAME = "SPIA Illustrative — Q4 2026"
TRACED_POLICIES = ["SPIA-0001", "SPIA-0002", "SPIA-0003"]

MYGA_SAMPLES = BACKEND.parent / "samples" / "myga"
MYGA_PROJECT_NAME = "MentorAmp Demo — 5-Year MYGA (Illustrative)"
MYGA_INFORCE_FILENAME = "synthetic_myga_inforce.csv"
MYGA_REFERENCE_INFORCE_FILENAME = "synthetic_myga_inforce_10001.csv"
MYGA_EXPECTED_RESULTS_FILENAME = "expected_reference_myga_10001.csv"
MYGA_SCENARIO_SET_NAME = "5-Year MYGA deterministic scenarios"
MYGA_PROJECTION_SET_NAME = "5-Year MYGA Illustrative — Q4 2026"
MYGA_REVIEWER_EMAIL = "demo-reviewer@mentoramp.local"
MYGA_OUTPUTS = [
    "account_value_open", "account_value_close", "interest_credited", "premium_cash_flow",
    "gross_withdrawal", "surrender_charge_rate", "surrender_charge_on_withdrawal",
    "net_withdrawal_benefit", "mgsv_close", "surrender_benefit", "death_benefit",
    "death_claims", "surrender_claims", "annuitization_transfer", "maturity_benefit",
    "maintenance_expense", "liability_outgo", "net_cash_flow", "reserve",
]


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


def ensure_demo_reviewer(db) -> User:
    """Provision the independent reviewer used by the governed demo seed."""
    reviewer = db.query(User).filter(User.email == MYGA_REVIEWER_EMAIL).first()
    roles = initialize_builtin_roles(db)
    if reviewer is None:
        reviewer = User(
            email=MYGA_REVIEWER_EMAIL,
            full_name="MentorAmp Demo Reviewer",
            password_hash=hash_password(secrets.token_urlsafe(32)),
            roles=[roles["reviewer"]],
        )
        db.add(reviewer)
        db.flush()
    elif roles["reviewer"] not in reviewer.roles:
        reviewer.roles.append(roles["reviewer"])
        db.flush()
    return reviewer


def upsert_model(db, project, user, product=spia) -> tuple[Model, ModelVersion, FormulaGroup]:
    definition = product.MODEL
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
    if "configuration" in version_def:
        version.configuration = dict(version_def["configuration"] or {})
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


def upsert_variables(db, product=spia) -> None:
    """The global SEMANTIC catalog entries (identity only; never used to resolve values)."""
    for item in product.VARIABLES:
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
        row.product_applicability = sorted(set(row.product_applicability or []) | {product.PRODUCT_CODE})
        row.basis_applicability = []
        row.version = "v1"
    db.flush()


def upsert_definitions(db, version, product=spia) -> None:
    """This model version's own variable definitions (the only source of resolution).

    Scenario overrides are allowed only where the model explicitly permits them (in the SPIA
    illustrative model: the discount rate, which the Low Interest Rate scenario overrides).
    """
    for item in product.VARIABLES:
        row = (
            db.query(ModelVariableDefinition)
            .filter(ModelVariableDefinition.model_version_id == version.id,
                    ModelVariableDefinition.variable_name == item["name"])
            .first()
        )
        if row is None:
            row = ModelVariableDefinition(model_version_id=version.id, variable_name=item["name"], version="v1")
            db.add(row)
        values = {
            "display_name": item["display_name"],
            "description": item["description"],
            "kind": item["kind"],
            "data_type": item["data_type"],
            "unit": item.get("unit"),
            "required": item.get("required", True),
            "default_value": item.get("default_value"),
            "source": dict(item["source"]),
            "allow_scenario_override": bool(item.get("allow_scenario_override", False)),
        }
        changed = any(getattr(row, key) != value for key, value in values.items())
        for key, value in values.items():
            setattr(row, key, value)
        if changed and row.created_at is not None:
            number = int((row.version or "v1").lstrip("v") or 1)
            row.version = f"v{number + 1}"
    db.flush()


def upsert_formulas(db, version, group, product=spia) -> None:
    for item in product.FORMULAS:
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
        row.category = product.PRODUCT_CODE
        row.product_applicability = [product.PRODUCT_CODE]
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


def upsert_published_outputs(db, version, product=spia) -> None:
    for order, item in enumerate(product.PUBLISHED_OUTPUTS):
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
    checks = catalog_service.model_version_checks(db, formulas, published, version.id)
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
    # Scheme inforce-v2: {policy_id, data} records ordered by (unique) policy_id.
    file.fingerprint = inforce_fingerprint((row["policy_id"], row) for row in rows)
    file.fingerprint_scheme = INFORCE_FINGERPRINT_SCHEME
    file.description = "25 synthetic SPIA policies (SYNTHETIC — not real policyholders)"
    db.flush()
    return file


def upsert_scenarios(db, project, version) -> list[ScenarioTable]:
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
    variables = variable_specs(db, version.id)
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


def _approve_seeded_dataset(row, committer: User, reviewer: User, now: datetime) -> None:
    """Attach explicit commit and independent approval evidence to validated seed data."""
    row.status = lifecycle.APPROVED
    row.committed_by = row.committed_by or committer.id
    row.committed_at = row.committed_at or now
    row.approved_by = reviewer.id
    row.approved_at = row.approved_at or now


def _upsert_myga_inforce_file(
    db, project, committer: User, reviewer: User, filename: str,
) -> InforceFile:
    rows = read_csv(MYGA_SAMPLES / filename)
    errors = validate_inforce(rows)
    if errors:
        raise RuntimeError(f"MYGA inforce seed validation failed for {filename}: {errors}")
    source_fingerprint = inforce_fingerprint((row["policy_id"], row) for row in rows)
    file = (
        db.query(InforceFile)
        .filter(InforceFile.project_id == project.id,
                InforceFile.filename == filename)
        .first()
    )
    if file is None:
        file_id = str(uuid.uuid4())
        file = InforceFile(
            id=file_id, project_id=project.id, filename=filename,
            file_type="csv", version_group_id=file_id,
        )
        db.add(file)
        db.add_all(
            InforceRecord(file_id=file.id, policy_id=row["policy_id"], data=row)
            for row in rows
        )
    if file.fingerprint and file.fingerprint != source_fingerprint:
        raise RuntimeError(
            f"Seed source {filename} changed after registration; create a governed new version."
        )
    file.row_count = len(rows)
    file.columns_detected = list(rows[0])
    file.version_label = "v2026.12"
    file.fingerprint = source_fingerprint
    file.fingerprint_scheme = INFORCE_FINGERPRINT_SCHEME
    file.description = "25 synthetic MYGA policies (SYNTHETIC — not real policyholders)"
    if filename == MYGA_REFERENCE_INFORCE_FILENAME:
        file.description = "MYGA-10001 synthetic reference policy (SYNTHETIC data)"
    _approve_seeded_dataset(file, committer, reviewer, datetime.now(timezone.utc))
    db.flush()
    return file


def upsert_myga_inforce(db, project, committer: User, reviewer: User) -> InforceFile:
    return _upsert_myga_inforce_file(
        db, project, committer, reviewer, MYGA_INFORCE_FILENAME,
    )


def upsert_myga_expected_results(
    db, project, user: User, reviewer: User | None = None
) -> ExpectedResultSet | None:
    """Register the externally supplied MYGA-10001 benchmark when the file is available."""
    path = MYGA_SAMPLES / MYGA_EXPECTED_RESULTS_FILENAME
    if not path.is_file():
        return None
    rows, _ = reconciliation_service.parse_csv(path.read_bytes())
    source_reference = f"samples/myga/{MYGA_EXPECTED_RESULTS_FILENAME}"
    source_fingerprint = fingerprint(rows)
    existing = db.query(ExpectedResultSet).filter(
        ExpectedResultSet.project_id == project.id,
        ExpectedResultSet.source_reference == source_reference,
    ).first()
    if existing is not None:
        if existing.fingerprint != source_fingerprint:
            raise RuntimeError(
                "The MYGA-10001 expected-result source changed after registration; "
                "register it as a governed new version."
            )
        if reviewer is not None and reviewer.id != existing.created_by and existing.status != "approved":
            reconciliation_service.approve_expected_set(db, existing.id, reviewer.id)
            db.refresh(existing)
        return existing
    created = reconciliation_service.create_expected_set(db, project.id, {
        "name": "MYGA-10001 Development Reference",
        "product_code": "myga_5yr",
        "version_label": "v1",
        "source_reference": source_reference,
        "timing_convention": "BOM_WITHDRAWAL_BEFORE_INTEREST",
        "granularity": "policy_month",
        "rows": rows,
    }, user.id)
    if reviewer is not None and reviewer.id != user.id:
        reconciliation_service.approve_expected_set(db, created["id"], reviewer.id)
    return db.query(ExpectedResultSet).filter(
        ExpectedResultSet.project_id == project.id,
        ExpectedResultSet.source_reference == source_reference,
    ).one()


def upsert_myga_mortality(
    db, project, committer: User, reviewer: User,
) -> AssumptionTable:
    assumption_set = (
        db.query(AssumptionSet)
        .filter(AssumptionSet.project_id == project.id,
                AssumptionSet.name == "5-Year MYGA illustrative assumptions")
        .first()
    )
    if assumption_set is None:
        assumption_set = AssumptionSet(
            project_id=project.id, name="5-Year MYGA illustrative assumptions",
            description="Synthetic assumptions for the governed MYGA demo.",
        )
        db.add(assumption_set)
        db.flush()
    rows = [
        {"age": int(row["age"]), "gender": row["gender"], "qx": float(row["qx"])}
        for row in read_csv(SAMPLES / "synthetic_mortality_gompertz.csv")
    ]
    table = (
        db.query(AssumptionTable)
        .filter(AssumptionTable.set_id == assumption_set.id,
                AssumptionTable.table_name == myga.MORTALITY_TABLE_NAME)
        .first()
    )
    if table is None:
        table_id = str(uuid.uuid4())
        table = AssumptionTable(
            id=table_id, set_id=assumption_set.id, table_name=myga.MORTALITY_TABLE_NAME,
            table_type="mortality", lookup_keys=["age", "gender"], value_column="qx",
            version_group_id=table_id,
        )
        db.add(table)
    errors = validate_lookup_table(rows, ["age", "gender"], "qx", (0.0, 1.0))
    if errors:
        raise RuntimeError(f"MYGA mortality seed validation failed: {errors}")
    table.table_type = "mortality"
    table.lookup_keys = ["age", "gender"]
    table.value_column = "qx"
    table.data = rows
    table.version_label = "v1"
    table.fingerprint = table_fingerprint(rows)
    table.description = "Synthetic Gompertz mortality by age and gender."
    _approve_seeded_dataset(table, committer, reviewer, datetime.now(timezone.utc))
    db.flush()
    return table


def _upsert_myga_factor(
    db, factor_set: FactorSet, committer: User, reviewer: User, *, table_name: str,
    filename: str, table_type: str, lookup_keys: list[str], value_column: str,
) -> FactorTable:
    raw_rows = read_csv(MYGA_SAMPLES / filename)
    rows = []
    for raw in raw_rows:
        row = dict(raw)
        for key in lookup_keys:
            if key != "schedule":
                row[key] = int(row[key])
        row[value_column] = float(row[value_column])
        rows.append(row)
    errors = validate_lookup_table(rows, lookup_keys, value_column, (0.0, 1.0))
    if errors:
        raise RuntimeError(f"MYGA factor seed validation failed for {table_name}: {errors}")
    table = (
        db.query(FactorTable)
        .filter(FactorTable.set_id == factor_set.id, FactorTable.table_name == table_name)
        .first()
    )
    if table is None:
        table_id = str(uuid.uuid4())
        table = FactorTable(
            id=table_id, set_id=factor_set.id, table_name=table_name,
            table_type=table_type, version_group_id=table_id,
        )
        db.add(table)
    table.table_type = table_type
    table.lookup_keys = lookup_keys
    table.value_column = value_column
    table.data = rows
    table.version_label = "v1"
    table.fingerprint = table_fingerprint(rows)
    table.description = f"Synthetic {table_type.replace('_', ' ')} factors for the MYGA demo."
    _approve_seeded_dataset(table, committer, reviewer, datetime.now(timezone.utc))
    db.flush()
    return table


def upsert_myga_factors(
    db, project, committer: User, reviewer: User,
) -> list[FactorTable]:
    factor_set = (
        db.query(FactorSet)
        .filter(FactorSet.project_id == project.id,
                FactorSet.name == "5-Year MYGA illustrative factors")
        .first()
    )
    if factor_set is None:
        factor_set = FactorSet(
            project_id=project.id, name="5-Year MYGA illustrative factors",
            description="Synthetic surrender-charge factors for the governed MYGA demo.",
        )
        db.add(factor_set)
        db.flush()
    return [
        _upsert_myga_factor(
            db, factor_set, committer, reviewer,
            table_name=myga.SURRENDER_CHARGE_TABLE_NAME,
            filename="myga_surrender_charge.csv", table_type="surrender_charge",
            lookup_keys=["schedule", "year_in_gp"], value_column="rate",
        ),
        _upsert_myga_factor(
            db, factor_set, committer, reviewer,
            table_name=myga.SURRENDER_CHARGE_AGE_CAP_TABLE_NAME,
            filename="myga_sc_age_cap.csv", table_type="surrender_charge_age_cap",
            lookup_keys=["attained_age"], value_column="cap",
        ),
    ]


def upsert_myga_scenarios(
    db, project, version, committer: User, reviewer: User,
) -> list[ScenarioTable]:
    scenario_set = (
        db.query(ScenarioSet)
        .filter(ScenarioSet.project_id == project.id,
                ScenarioSet.name == MYGA_SCENARIO_SET_NAME)
        .first()
    )
    if scenario_set is None:
        scenario_set = ScenarioSet(
            project_id=project.id, name=MYGA_SCENARIO_SET_NAME,
            description="Base and +150 bps deterministic MYGA scenarios.",
        )
        db.add(scenario_set)
        db.flush()
    definitions = [
        ("Base", 0.0445, 0.05),
        ("Rates Up (+150 bps)", 0.0595, 0.065),
    ]
    variables = variable_specs(db, version.id)
    scenarios = []
    for name, market_rate, mva_yield in definitions:
        overrides = [
            {"target_variable": "market_rate", "operation": "set", "value": market_rate,
             "applies_from_period": 1, "applies_to_period": 480},
            {"target_variable": "mva_ref_yield", "operation": "set", "value": mva_yield,
             "applies_from_period": 1, "applies_to_period": 480},
        ]
        problems = override_problems(f"Scenario '{name}'", overrides, variables)
        if problems:
            raise RuntimeError(f"MYGA scenario seed validation failed: {problems}")
        scenario = (
            db.query(ScenarioTable)
            .filter(ScenarioTable.set_id == scenario_set.id,
                    ScenarioTable.scenario_name == name)
            .first()
        )
        if scenario is None:
            scenario_id = str(uuid.uuid4())
            scenario = ScenarioTable(
                id=scenario_id, set_id=scenario_set.id, scenario_name=name,
                version_group_id=scenario_id,
            )
            db.add(scenario)
        scenario.description = (
            "Current illustrative assumptions." if name == "Base"
            else "Illustrative market and MVA reference yields increased by 150 bps."
        )
        scenario.overrides = overrides
        scenario.version_label = "v1"
        scenario.scenario_type = "deterministic"
        scenario.as_of_date = VALUATION_DATE
        scenario.path_count = 1
        scenario.fingerprint = scenario_fingerprint(overrides)
        _approve_seeded_dataset(scenario, committer, reviewer, datetime.now(timezone.utc))
        db.flush()
        scenarios.append(scenario)
    return scenarios


def upsert_myga_projection_set(
    db, project, version, inforce, mortality, factors, scenarios, user,
) -> ProjectionSet:
    projection_set = (
        db.query(ProjectionSet)
        .filter(ProjectionSet.project_id == project.id,
                ProjectionSet.name == MYGA_PROJECTION_SET_NAME,
                ProjectionSet.version_label == "v1")
        .first()
    )
    if projection_set is None:
        projection_set = ProjectionSet(
            project_id=project.id, name=MYGA_PROJECTION_SET_NAME, version_label="v1",
            created_by=user.id, valuation_date=VALUATION_DATE, horizon_months=480,
            status=lifecycle.DRAFT,
        )
        db.add(projection_set)
    projection_set.description = "Governed illustrative projection for the 5-Year MYGA model."
    projection_set.model_version_id = version.id
    projection_set.inforce_file_ids = [inforce.id]
    projection_set.assumption_table_ids = [mortality.id]
    projection_set.factor_table_ids = [row.id for row in factors]
    projection_set.scenario_ids = [row.id for row in scenarios]
    projection_set.valuation_date = VALUATION_DATE
    projection_set.horizon_months = (
        60 if inforce.filename == MYGA_REFERENCE_INFORCE_FILENAME else 480
    )
    projection_set.time_step = "monthly"
    projection_set.output_variables = MYGA_OUTPUTS
    first_policy = db.query(InforceRecord).filter(
        InforceRecord.file_id == inforce.id
    ).order_by(InforceRecord.policy_id).first()
    projection_set.trace_scope = {
        "mode": "selected_policies",
        "policy_ids": [first_policy.policy_id] if first_policy else [],
    }
    projection_set.parameters = {}
    projection_set.updated_by = user.id
    db.flush()
    return projection_set


def seed_myga(db, project_name: str = MYGA_PROJECT_NAME) -> dict:
    """Seed the governed 5-Year MYGA demo using the sample assets available in the repo."""
    register_all_products()
    user = ensure_demo_user(db)
    reviewer = ensure_demo_reviewer(db)
    project = upsert_project(db, user, project_name)
    ensure_member(db, project.id, reviewer.id, role="viewer")
    model, version, group = upsert_model(db, project, user, myga)

    # Published versions are immutable. A repeated seed verifies and reuses the existing version.
    if version.published_at is None:
        version.created_by = version.created_by or user.id
        version.updated_by = user.id
        version.last_material_modified_by = version.last_material_modified_by or user.id
        version.last_material_modified_at = version.last_material_modified_at or datetime.now(timezone.utc)
        model.created_by = model.created_by or user.id
        model.updated_by = user.id
        upsert_variables(db, myga)
        upsert_definitions(db, version, myga)
        upsert_formulas(db, version, group, myga)
        upsert_published_outputs(db, version, myga)
        db.commit()
        model_version_service.validate_version(db, version.id, user)
        model_version_service.approve_version(
            db, version.id, reviewer, reason="Independent approval of governed demo model.",
        )
        model_version_service.publish_version(db, version.id, user)
        db.refresh(version)

    mortality = upsert_myga_mortality(db, project, user, reviewer)
    inforce = upsert_myga_inforce(db, project, user, reviewer)
    reference_inforce = None
    if (MYGA_SAMPLES / MYGA_REFERENCE_INFORCE_FILENAME).is_file():
        reference_inforce = _upsert_myga_inforce_file(
            db, project, user, reviewer, MYGA_REFERENCE_INFORCE_FILENAME,
        )
    factors = upsert_myga_factors(db, project, user, reviewer)
    scenarios = upsert_myga_scenarios(db, project, version, user, reviewer)
    projection_set = upsert_myga_projection_set(
        db, project, version, reference_inforce or inforce, mortality, factors, scenarios, user,
    )
    db.commit()
    validated = projection_set_service.validate(db, projection_set.id, user)
    expected_result_set = upsert_myga_expected_results(db, project, user, reviewer=reviewer)
    return {
        "user": user, "reviewer": reviewer, "project": project, "model": model,
        "version": version, "mortality": mortality, "inforce": inforce,
        "factors": factors, "scenarios": scenarios, "projection_set": projection_set,
        "validation": validated, "reference_inforce": reference_inforce,
        "expected_result_set": expected_result_set,
    }


def seed(db, project_name: str = PROJECT_NAME) -> dict:
    """Seed one demo project into ``db``; return the objects created (used by tests too)."""
    register_all_products()
    user = ensure_demo_user(db)
    project = upsert_project(db, user, project_name)
    model, version, group = upsert_model(db, project, user)
    upsert_variables(db)
    upsert_definitions(db, version)
    upsert_formulas(db, version, group)
    upsert_published_outputs(db, version)
    model_checks = validate_model(db, model, version)
    table = upsert_mortality(db, project)
    inforce = upsert_inforce(db, project)
    scenarios = upsert_scenarios(db, project, version)
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
        spia_seeded = seed(db)
        myga_seeded = seed_myga(db)
        print("Seed complete: SPIA and 5-Year MYGA.")
        print(f"  project        {spia_seeded['project'].id}  {spia_seeded['project'].name}")
        print(f"  model version  {spia_seeded['version'].id}  {spia_seeded['model'].name} "
              f"{spia_seeded['version'].version_label} ({spia_seeded['version'].status})")
        print(f"  inforce file   {spia_seeded['inforce'].id}  {spia_seeded['inforce'].row_count} policies "
              f"({spia_seeded['inforce'].status})")
        print(f"  mortality      {spia_seeded['table'].id}  {len(spia_seeded['table'].data)} rows "
              f"({spia_seeded['table'].status})")
        for scenario in spia_seeded["scenarios"]:
            print(f"  scenario       {scenario.id}  {scenario.scenario_name} ({scenario.status})")
        validated = spia_seeded["validation"]
        print(f"  projection set {spia_seeded['projection_set'].id}  status={validated['status']}")
        for check in validated["validation"]["checks"]:
            print(f"    [{check['status']:7}] {check['label']}: {check['message']}")
        print(f"  project        {myga_seeded['project'].id}  {myga_seeded['project'].name}")
        print(f"  model version  {myga_seeded['version'].id}  {myga_seeded['model'].name} "
              f"{myga_seeded['version'].version_label} ({myga_seeded['version'].status})")
        print(f"  inforce file   {myga_seeded['inforce'].id}  "
              f"{myga_seeded['inforce'].row_count} policies ({myga_seeded['inforce'].status})")
        print(f"  projection set {myga_seeded['projection_set'].id}  "
              f"status={myga_seeded['validation']['status']}")
        if myga_seeded["expected_result_set"] is None:
            print("  expected result skipped: samples/myga/expected_reference_myga_10001.csv is absent")
    finally:
        db.close()


if __name__ == "__main__":
    main()
