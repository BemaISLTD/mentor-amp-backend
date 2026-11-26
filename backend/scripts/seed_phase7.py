"""Seed isolated, explicitly synthetic Phase 7 sandbox projects.

The script is idempotent for unchanged fixtures and refuses to overwrite changed
or approved actuarial content.  It never targets a database implicitly: callers
must configure DATABASE_URL, and shared production databases are forbidden by
the normal application safety controls.
"""

from __future__ import annotations

import csv
import sys
import uuid
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app.core import lifecycle  # noqa: E402
from app.core.execution.fingerprints import (  # noqa: E402
    INFORCE_FINGERPRINT_SCHEME, inforce_fingerprint, scenario_fingerprint, table_fingerprint,
)
from app.data.validation.table_validator import validate_lookup_table  # noqa: E402
from app.db.models.assumption import AssumptionSet, AssumptionTable  # noqa: E402
from app.db.models.data_manager import ImportSession  # noqa: E402
from app.db.models.factor import FactorSet, FactorTable  # noqa: E402
from app.db.models.inforce import InforceFile, InforceRecord  # noqa: E402
from app.db.models.projection import ProjectionSet  # noqa: E402
from app.db.models.scenario import ScenarioSet, ScenarioTable  # noqa: E402
from app.products.idi_lite import config as idi  # noqa: E402
from app.products.prt_lite import config as prt  # noqa: E402
from app.products.registry import register_all_products  # noqa: E402
from app.products.va_lite import config as va  # noqa: E402
from app.services import projection_set_service  # noqa: E402
from app.services.common import now_utc  # noqa: E402
from app.services.data_manager_service import _validate_inforce  # noqa: E402
from app.services.model_definition import variable_specs  # noqa: E402
from app.services.run_package_service import override_problems  # noqa: E402
from scripts import seed_demo as catalog  # noqa: E402

VALUATION_DATE = date(2026, 12, 31)
HORIZON_MONTHS = 24


def _rows(code: str) -> list[dict]:
    path = ROOT / "samples" / code.lower() / f"synthetic_{code.lower()}_inforce.csv"
    with path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    issues: list[dict] = []
    _validate_inforce(ImportSession(options={"valuation_date": VALUATION_DATE.isoformat()}), rows, issues)
    errors = [issue for issue in issues if issue["severity"] == "error"]
    if errors:
        raise ValueError(errors)
    return rows


def _inforce(db, project, user, rows: list[dict], code: str) -> InforceFile:
    filename = f"synthetic_{code.lower()}_inforce.csv"
    digest = inforce_fingerprint((row["policy_id"], row) for row in rows)
    stored = db.query(InforceFile).filter_by(project_id=project.id, filename=filename).first()
    if stored is not None:
        if stored.fingerprint != digest:
            raise ValueError(f"{filename} changed; create a new governed version.")
        return stored
    stored = InforceFile(
        project_id=project.id, filename=filename, file_type="csv", row_count=len(rows),
        columns_detected=list(rows[0]), status=lifecycle.VALIDATED,
        version_label="synthetic-v1", version_group_id=str(uuid.uuid4()),
        fingerprint=digest, fingerprint_scheme=INFORCE_FINGERPRINT_SCHEME,
        description="Synthetic illustrative engineering fixture; not production approved.",
    )
    db.add(stored)
    db.flush()
    db.add_all(InforceRecord(file_id=stored.id, policy_id=row["policy_id"], data=row) for row in rows)
    return stored


def _table(db, set_row, model, name: str, keys: list[str], value: str,
           data: list[dict], table_type: str):
    problems = validate_lookup_table(data, keys, value)
    if problems:
        raise ValueError(problems)
    digest = table_fingerprint(data)
    row = db.query(model).filter_by(set_id=set_row.id, table_name=name).first()
    if row is not None:
        if row.fingerprint != digest:
            raise ValueError(f"Synthetic table {name} changed; create a new version.")
        return row
    row = model(
        set_id=set_row.id, table_name=name, table_type=table_type,
        lookup_keys=keys, value_column=value, data=data, fingerprint=digest,
        version_label="synthetic-v1", version_group_id=str(uuid.uuid4()),
        status=lifecycle.VALIDATED,
        description="Synthetic illustrative engineering fixture; not production approved.",
    )
    db.add(row)
    return row


def _tables(db, project, code: str):
    assumptions = db.query(AssumptionSet).filter_by(project_id=project.id, name=f"{code} synthetic assumptions").first()
    if assumptions is None:
        assumptions = AssumptionSet(project_id=project.id, name=f"{code} synthetic assumptions")
        db.add(assumptions)
        db.flush()
    factors = db.query(FactorSet).filter_by(project_id=project.id, name=f"{code} synthetic factors").first()
    if factors is None:
        factors = FactorSet(project_id=project.id, name=f"{code} synthetic factors")
        db.add(factors)
        db.flush()
    assumption_tables = []
    factor_tables = []
    if code == "IDI":
        mortality = [{"attained_age": age, "gender": gender, "rate": 0.005}
                     for age in range(0, 121) for gender in ("F", "M")]
        incidence = [{"attained_age": age, "gender": gender, "occupation_class": occ, "rate": 0.01}
                     for age in range(0, 121) for gender in ("F", "M") for occ in ("CLASS_1", "CLASS_2", "CLASS_3")]
        lapse = [{"policy_year": year, "rate": 0.03} for year in range(1, 11)]
        for name, keys, data in (
            (idi.ACTIVE_MORTALITY_TABLE, ["attained_age", "gender"], mortality),
            (idi.INCIDENCE_TABLE, ["attained_age", "gender", "occupation_class"], incidence),
            (idi.ACTIVE_LAPSE_TABLE, ["policy_year"], lapse),
            (idi.DISABLED_TERMINATION_TABLE, ["attained_age", "gender"], [{**r, "rate": 0.08} for r in mortality]),
            (idi.DISABLED_MORTALITY_TABLE, ["attained_age", "gender"], [{**r, "rate": 0.01} for r in mortality]),
        ):
            assumption_tables.append(_table(db, assumptions, AssumptionTable, name, keys, "rate", data, "synthetic"))
        dlr = [{"attained_age": age, "occupation_class": occ, "factor": 6.0}
               for age in range(0, 121) for occ in ("CLASS_1", "CLASS_2", "CLASS_3")]
        factor_tables.append(_table(db, factors, FactorTable, idi.NEW_CLAIM_DLR_TABLE,
                                    ["attained_age", "occupation_class"], "factor", dlr, "synthetic"))
    elif code == "VA":
        mortality = [{"attained_age": age, "gender": gender, "rate": 0.01}
                     for age in range(0, 121) for gender in ("F", "M")]
        lapse = [{"attained_age": age, "rate": 0.04} for age in range(0, 121)]
        assumption_tables.extend([
            _table(db, assumptions, AssumptionTable, va.MORTALITY_TABLE,
                   ["attained_age", "gender"], "rate", mortality, "synthetic"),
            _table(db, assumptions, AssumptionTable, va.LAPSE_TABLE,
                   ["attained_age"], "rate", lapse, "synthetic"),
        ])
        monthly = {0: 0.004, 1: 0.008, 2: -0.004, 3: -0.02}
        paths = [{"path_id": path, "projection_month": month, "fund_code": fund,
                  "return": rate if fund == "SP500_EQ" else rate / 3.0}
                 for path, rate in monthly.items() for month in range(1, HORIZON_MONTHS + 1)
                 for fund in ("SP500_EQ", "US_AGG_BOND")]
        factor_tables.append(_table(db, factors, FactorTable, va.FUND_RETURN_TABLE,
                                    ["path_id", "projection_month", "fund_code"], "return", paths, "synthetic"))
    elif code == "PRT":
        mortality = [{"attained_age": age, "gender": gender, "rate": 0.02}
                     for age in range(0, 121) for gender in ("F", "M")]
        spouse = [
            {"form_of_annuity": form, "spouse_age": -1, "spouse_gender": "NOT_APPLICABLE", "rate": 0.0}
            for form in ("life_only", "certain_and_life")
        ] + [
            {"form_of_annuity": "joint_survivor", "spouse_age": age, "spouse_gender": gender, "rate": 0.015}
            for age in range(0, 121) for gender in ("F", "M")
        ]
        assumption_tables.extend([
            _table(db, assumptions, AssumptionTable, prt.PRIMARY_MORTALITY_TABLE,
                   ["attained_age", "gender"], "rate", mortality, "synthetic"),
            _table(db, assumptions, AssumptionTable, prt.SPOUSE_MORTALITY_TABLE,
                   ["form_of_annuity", "spouse_age", "spouse_gender"], "rate", spouse, "synthetic"),
            _table(db, assumptions, AssumptionTable, prt.OPTIONAL_TERMINATION_TABLE,
                   ["policy_year"], "rate", [{"policy_year": y, "rate": 0.02} for y in range(1, 11)], "synthetic"),
        ])
    db.flush()
    return assumption_tables, factor_tables


def _scenarios(db, project, user, version, code: str):
    scenario_set = db.query(ScenarioSet).filter_by(project_id=project.id, name=f"{code} synthetic scenarios").first()
    if scenario_set is None:
        scenario_set = ScenarioSet(project_id=project.id, name=f"{code} synthetic scenarios")
        db.add(scenario_set)
        db.flush()
    definitions = (
        (("Base", 1.0), ("Stress", 1.25)) if code == "IDI"
        else (("Base", 0), ("Bull", 1), ("Bear", 2), ("Stress", 3)) if code == "VA"
        else (("Base", 0.04), ("LowRate", 0.02))
    )
    target = "incidence_multiplier" if code == "IDI" else "path_id" if code == "VA" else "discount_rate"
    scenarios = []
    for name, value in definitions:
        overrides = [{"target_variable": target, "operation": "set", "value": value}]
        problems = override_problems(name, overrides, variable_specs(db, version.id))
        if problems:
            raise ValueError(problems)
        row = db.query(ScenarioTable).filter_by(set_id=scenario_set.id, scenario_name=name).first()
        digest = scenario_fingerprint(overrides)
        if row is None:
            row = ScenarioTable(
                set_id=scenario_set.id, scenario_name=name, overrides=overrides,
                fingerprint=digest, version_label="synthetic-v1", status=lifecycle.VALIDATED,
                as_of_date=VALUATION_DATE,
            )
            db.add(row)
        elif row.fingerprint != digest:
            raise ValueError(f"Synthetic scenario {code}/{name} changed.")
        scenarios.append(row)
    db.flush()
    return scenarios


def seed_product(db, product):
    code = product.PRODUCT_CODE
    register_all_products()
    user = catalog.ensure_demo_user(db)
    project = catalog.upsert_project(db, user, f"MentorAmp Demo — {code} (Illustrative)")
    model, version, group = catalog.upsert_model(db, project, user, product)
    if version.approved_at or version.published_at:
        raise ValueError("Refusing to edit an approved model version.")
    catalog.upsert_variables(db, product)
    catalog.upsert_definitions(db, version, product)
    catalog.upsert_formulas(db, version, group, product)
    catalog.upsert_published_outputs(db, version, product)
    checks = catalog.validate_model(db, model, version)
    if checks["status"] != lifecycle.VALIDATED:
        raise ValueError(checks)
    rows = _rows(code)
    inforce = _inforce(db, project, user, rows, code)
    assumptions, factors = _tables(db, project, code)
    scenarios = _scenarios(db, project, user, version, code)
    projection = db.query(ProjectionSet).filter_by(
        project_id=project.id, name=f"{code} Phase 7 illustrative block", version_label="v1"
    ).first()
    if projection is None:
        projection = ProjectionSet(
            project_id=project.id, name=f"{code} Phase 7 illustrative block", version_label="v1",
            created_by=user.id, updated_by=user.id, valuation_date=VALUATION_DATE,
            horizon_months=HORIZON_MONTHS,
        )
        db.add(projection)
    projection.model_version_id = version.id
    projection.inforce_file_ids = [inforce.id]
    projection.assumption_table_ids = [row.id for row in assumptions]
    projection.factor_table_ids = [row.id for row in factors]
    projection.scenario_ids = [row.id for row in scenarios]
    projection.output_variables = [row["variable_name"] for row in product.PUBLISHED_OUTPUTS]
    projection.trace_scope = {"mode": "selected_policies", "policy_ids": [rows[0]["policy_id"]]}
    projection.parameters = {
        "governance_mode": "sandbox", "is_synthetic": True,
        "is_production_approved": False, "actuarial_status": "PENDING_OWNER_CONFIRMATION",
    }
    projection.status = lifecycle.DRAFT
    db.commit()
    validation = projection_set_service.validate(db, projection.id, user)
    if validation["status"] not in {lifecycle.VALIDATED, lifecycle.NEEDS_REVIEW}:
        raise ValueError(validation)
    return {
        "project": project,
        "projection_set": projection,
        "validation": validation,
        "user": user,
        "inforce": inforce,
        "scenarios": scenarios,
    }


def seed_life_readiness(db):
    user = catalog.ensure_demo_user(db)
    reviewer = catalog.ensure_demo_reviewer(db)
    project = catalog.upsert_project(db, user, "MentorAmp Demo — Life Readiness (Canonical Schema Only)")
    rows_v1 = _rows("LIFE")
    rows_v2 = [{**row} for row in rows_v1]
    rows_v2[0]["annualized_premium"] = 1260.0
    rows_v2[0]["modal_premium"] = 105.0
    rows_v2[0]["premium"] = 1260.0
    group = str(uuid.uuid5(uuid.NAMESPACE_URL, f"mentoramp:{project.id}:life-readiness"))
    parent = None
    files = []
    for number, rows in ((1, rows_v1), (2, rows_v2)):
        filename = f"synthetic_life_inforce_v{number}.csv"
        digest = inforce_fingerprint((row["policy_id"], row) for row in rows)
        stored = db.query(InforceFile).filter_by(
            version_group_id=group, version_number=number
        ).first()
        if stored is None:
            stored = InforceFile(
                project_id=project.id, filename=filename, file_type="csv", row_count=len(rows),
                columns_detected=list(rows[0]), status=lifecycle.APPROVED,
                version_label=f"v{number}", version_number=number, version_group_id=group,
                parent_file_id=parent.id if parent else None,
                fingerprint=digest, fingerprint_scheme=INFORCE_FINGERPRINT_SCHEME,
                committed_by=user.id, committed_at=now_utc(),
                approved_by=reviewer.id, approved_at=now_utc(),
                description="Synthetic LIFE schema-readiness fixture; calculations excluded.",
            )
            db.add(stored)
            db.flush()
            db.add_all(InforceRecord(file_id=stored.id, policy_id=row["policy_id"], data=row) for row in rows)
        elif stored.fingerprint != digest:
            raise ValueError(f"Life readiness v{number} changed; create a new governed version.")
        files.append(stored)
        parent = stored
    db.commit()
    return {"project": project, "versions": files, "user": user, "reviewer": reviewer}


def seed_phase7(db):
    products = {product.PRODUCT_CODE: seed_product(db, product) for product in (idi, va, prt)}
    products["LIFE"] = seed_life_readiness(db)
    return products


if __name__ == "__main__":
    with catalog.SessionLocal() as session:
        result = seed_phase7(session)
        for code, item in result.items():
            print(code, item["project"].id)
