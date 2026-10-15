"""File-governed end-to-end workflow for the native SPIA reference model."""

import json
import os

import pytest

from app.core.artifacts import RUN_ARTIFACT_SCHEMAS, verified_parquet_rows
from app.core.execution import run_package
from app.core.projection_engine.engine import run_policy
from app.core.projection_engine.run_data import PolicyRecord, TableSpec
from app.db.models.data_manager import ValidationRun
from app.db.models.modeling import ModelVersion
from app.db.models.project import Project
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact
from app.db.models.run_package import RunPackage
from app.db.models.user import User
from app.products.spia_reference import config as spia
from app.services import data_manager_service, run_package_verification

from .wp1_support import Env


INFORCE_FIELDS = (
    "policy_id", "product_type", "issue_date", "issue_age", "gender", "premium",
    "monthly_payment", "annual_income", "benefit_form", "certain_months", "annuity_year",
    "payment_frequency", "payment_timing", "cola_rate", "rating_factor", "is_joint",
    "joint_issue_age", "joint_gender", "joint_rating_factor", "survivor_percentage",
    "reduction_trigger", "commutation_enabled", "issue_state_excludes_withdrawal",
    "cumulative_scheduled_paid_at_valuation", "commutation_fraction_at_valuation",
)
SCENARIO_FIELDS = (
    "scenario_name", "target_variable", "operation", "value",
    "applies_from_period", "applies_to_period",
)


@pytest.fixture()
def env():
    environment = Env()
    ids = environment.seed("A", horizon=24)
    environment.monkeypatch.setattr(
        data_manager_service, "get_artifact_store", lambda: environment.artifact_store,
    )
    with environment.Session() as db:
        project = db.get(Project, ids["project"])
        user = db.get(User, ids["admin"])
        seed = environment.seed_module
        model, version, group = seed.upsert_model(db, project, user, spia)
        seed.upsert_variables(db, spia)
        seed.upsert_definitions(db, version, spia)
        seed.upsert_formulas(db, version, group, spia)
        seed.upsert_published_outputs(db, version, spia)
        checks = seed.validate_model(db, model, version)
        assert checks["status"] == "validated", checks
        db.commit()
        ids["reference_version"] = version.id
    yield environment
    environment.close()


def _import_dataset(
    env: Env,
    reviewer_headers: dict,
    *,
    category: str,
    name: str,
    content: bytes,
    fields: tuple[str, ...],
    options: dict,
) -> dict:
    headers = env.admin_headers()
    opened = env.client.post("/v1/import-sessions", headers=headers, json={
        "project_id": env.projects["A"]["project"],
        "category": category,
        "name": name,
        "options": options,
    })
    assert opened.status_code == 201, opened.text
    session_id = opened.json()["id"]
    uploaded = env.client.post(
        f"/v1/import-sessions/{session_id}/files",
        headers=headers,
        files={"file": (f"{name}.csv", content, "text/csv")},
    )
    assert uploaded.status_code == 200, uploaded.text
    preview = env.client.get(f"/v1/import-sessions/{session_id}/preview", headers=headers)
    assert preview.status_code == 200, preview.text
    assert preview.json()["sample_rows"]
    mapped = env.client.put(
        f"/v1/import-sessions/{session_id}/mapping",
        headers=headers,
        json={"fields": [
            {"source_column": field, "target_field": field} for field in fields
        ]},
    )
    assert mapped.status_code == 200, mapped.text
    validated = env.client.post(
        f"/v1/import-sessions/{session_id}/validate", headers=headers
    )
    assert validated.status_code == 201, validated.text
    assert validated.json()["error_count"] == 0, validated.text
    committed = env.client.post(
        f"/v1/import-sessions/{session_id}/commit", headers=headers
    )
    assert committed.status_code == 201, committed.text
    dataset = committed.json()
    approved = env.client.post(
        f"/v1/datasets/{dataset['kind']}/{dataset['id']}/approve",
        headers=reviewer_headers,
        json={"reason": "Independent SPIA reference workflow review"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    return {
        "dataset": approved.json(),
        "validation_id": validated.json()["id"],
        "session_id": session_id,
    }


def _canonical_rows(env: Env, validation_id: str) -> list[dict]:
    with env.Session() as db:
        validation = db.get(ValidationRun, validation_id)
        return verified_parquet_rows(
            env.artifact_store,
            validation.canonical_storage_uri,
            validation.canonical_checksum_sha256,
            validation.canonical_row_count,
            validation.canonical_schema_version,
            supported_schemas=data_manager_service.DATA_MANAGER_ARTIFACT_SCHEMAS,
        )


def _reference_inputs(env: Env, reviewer_headers: dict) -> dict[str, dict]:
    inforce = (
        "policy_id,product_type,issue_date,issue_age,gender,premium,monthly_payment,annual_income,"
        "benefit_form,certain_months,annuity_year,payment_frequency,payment_timing,"
        "cola_rate,rating_factor,is_joint,joint_issue_age,joint_gender,joint_rating_factor,"
        "survivor_percentage,reduction_trigger,commutation_enabled,"
        "issue_state_excludes_withdrawal,cumulative_scheduled_paid_at_valuation,"
        "commutation_fraction_at_valuation\n"
        "IA-MENTORAMP-ANCHOR,SPIA,2026-12-31,65,M,0,500,6000,life_only,0,2026,12,"
        "arrears,0,1,0,65,M,1,1,either,0,0,0,0\n"
    ).encode()
    mortality = (
        "age,gender,base_mortality_rate\n"
        "65,M,0.006704\n66,M,0.007417\n"
    ).encode()
    improvement = (
        "age,gender,mortality_improvement_rate\n"
        "65,M,0.01\n66,M,0.01\n"
    ).encode()
    surrender_charge = (
        "policy_year,surrender_charge_rate\n"
        "2,0.08\n3,0.07\n4,0.06\n5,0.05\n6,0.04\n"
        "7,0.03\n8,0.02\n9,0.01\n10,0.0\n"
    ).encode()
    scenario = (
        "scenario_name,target_variable,operation,value,applies_from_period,applies_to_period\n"
        "Reference Base,discount_rate_annual,set,0.045,1,24\n"
    ).encode()
    return {
        "inforce": _import_dataset(
            env, reviewer_headers, category="liability_inforce", name="SPIA reference inforce",
            content=inforce, fields=INFORCE_FIELDS,
            options={"valuation_date": "2026-12-31", "expected_record_count": 1},
        ),
        "mortality": _import_dataset(
            env, reviewer_headers, category="assumption_table",
            name=spia.MORTALITY_TABLE_NAME, content=mortality,
            fields=("age", "gender", "base_mortality_rate"),
            options={
                "table_type": "mortality", "lookup_keys": ["age", "gender"],
                "value_column": "base_mortality_rate",
            },
        ),
        "improvement": _import_dataset(
            env, reviewer_headers, category="assumption_table",
            name=spia.IMPROVEMENT_TABLE_NAME, content=improvement,
            fields=("age", "gender", "mortality_improvement_rate"),
            options={
                "table_type": "mortality_improvement",
                "lookup_keys": ["age", "gender"],
                "value_column": "mortality_improvement_rate",
            },
        ),
        "surrender_charge": _import_dataset(
            env, reviewer_headers, category="factor_table",
            name=spia.SURRENDER_CHARGE_TABLE_NAME, content=surrender_charge,
            fields=("policy_year", "surrender_charge_rate"),
            options={
                "table_type": "surrender_charge",
                "lookup_keys": ["policy_year"],
                "value_column": "surrender_charge_rate",
                "model_version_id": env.projects["A"]["reference_version"],
            },
        ),
        "scenario": _import_dataset(
            env, reviewer_headers, category="scenario", name="Reference Base",
            content=scenario, fields=SCENARIO_FIELDS,
            options={"model_version_id": env.projects["A"]["reference_version"]},
        ),
    }


def _assert_reference_model_data_manager_run_parquet_and_file_rebuild(env):
    ids = env.projects["A"]
    reviewer = env.user(
        "reference-reviewer@example.com", role="reviewer", memberships={"A": "viewer"},
    )
    imported = _reference_inputs(env, reviewer["headers"])
    datasets = {name: item["dataset"] for name, item in imported.items()}
    outputs = [
        "survival_probability", "annuity_payment", "maintenance_expense",
        "liability_cash_flow", "net_cash_flow", "pv_liability_cash_flow", "reserve",
    ]
    created = env.client.post(
        f"/v1/projects/{ids['project']}/projection-sets",
        headers=env.admin_headers(),
        json={
            "name": "SPIA reference governed workflow",
            "model_version_id": ids["reference_version"],
            "inforce_file_ids": [datasets["inforce"]["id"]],
            "assumption_table_ids": [
                datasets["mortality"]["id"], datasets["improvement"]["id"],
            ],
            "factor_table_ids": [datasets["surrender_charge"]["id"]],
            "scenario_ids": [datasets["scenario"]["id"]],
            "valuation_date": "2026-12-31",
            "horizon_months": 24,
            "time_step": "monthly",
            "output_variables": outputs,
            "trace_scope": {
                "mode": "selected_policies", "policy_ids": ["IA-MENTORAMP-ANCHOR"],
            },
            "parameters": {},
        },
    )
    assert created.status_code == 201, created.text
    projection_set_id = created.json()["id"]
    validation = env.client.post(
        f"/v1/projection-sets/{projection_set_id}/validate", headers=env.admin_headers(),
    )
    assert validation.status_code == 200, validation.text
    assert validation.json()["status"] == "validated", validation.text

    submitted = env.client.post("/v1/run-sets", headers=env.admin_headers(), json={
        "project_id": ids["project"],
        "name": "SPIA reference end-to-end",
        "projection_set_ids": [projection_set_id],
        "scenario_ids": [datasets["scenario"]["id"]],
    })
    assert submitted.status_code == 202, submitted.text
    run_id = submitted.json()["runs"][0]["id"]
    summary = env.summary(run_id)
    assert summary["status"] == "success", summary
    assert summary["failed_policy_count"] == 0

    fixture = json.loads(
        (env.seed_module.BACKEND / "tests" / "fixtures" /
         "spia_reference_lifelib_v0_17_2.json").read_text(encoding="utf-8")
    )
    survival = env.get(
        f"/runs/{run_id}/results", variable="survival_probability",
    ).json()["rows"]
    payments = env.get(
        f"/runs/{run_id}/results", variable="annuity_payment",
    ).json()["rows"]
    assert [row["value"] for row in survival] == pytest.approx(
        [row["survival_probability"] for row in fixture["rows"]], rel=1e-12, abs=1e-12
    )
    assert [row["value"] for row in payments] == pytest.approx(
        [row["annuity_payment"] for row in fixture["rows"]], rel=1e-12, abs=1e-12
    )

    with env.Session() as db:
        run = db.get(Run, run_id)
        package = db.query(RunPackage).filter_by(run_id=run_id).one()
        configuration, functions, _build = run_package_verification.verify_for_execution(
            run, package
        )
        output_artifacts = db.query(RunArtifact).filter_by(
            run_id=run_id, artifact_type="outputs"
        ).all()
    stored_rows = []
    for artifact in output_artifacts:
        stored_rows.extend(verified_parquet_rows(
            env.artifact_store,
            artifact.storage_uri,
            artifact.checksum_sha256,
            artifact.row_count,
            artifact.schema_version,
            supported_schemas=RUN_ARTIFACT_SCHEMAS,
            expected_run_id=run_id,
            expected_attempt_number=run.accepted_attempt_number,
        ))
    assert stored_rows

    inforce_rows = _canonical_rows(env, imported["inforce"]["validation_id"])
    policies = [PolicyRecord(
        policy_id=str(row["policy_id"]), data=row,
        dataset_id=datasets["inforce"]["id"], dataset_name=datasets["inforce"]["name"],
    ) for row in inforce_rows]
    table_rows = {
        datasets["mortality"]["id"]: _canonical_rows(
            env, imported["mortality"]["validation_id"]
        ),
        datasets["improvement"]["id"]: _canonical_rows(
            env, imported["improvement"]["validation_id"]
        ),
        datasets["surrender_charge"]["id"]: _canonical_rows(
            env, imported["surrender_charge"]["validation_id"]
        ),
    }
    table_entries = {
        entry["id"]: entry
        for collection in ("assumption_tables", "factor_tables")
        for entry in configuration["datasets"][collection]
    }
    tables = {}
    for name, binding in configuration["table_bindings"].items():
        entry = table_entries[binding["table_id"]]
        table = TableSpec(
            id=entry["id"], name=name, key_columns=tuple(entry["lookup_keys"]),
            value_column=entry["value_column"], rows=table_rows[entry["id"]],
            fingerprint=entry["fingerprint"], kind=binding["kind"],
        )
        assert table.build_index() == []
        tables[name] = table
    rebuilt_data = run_package.build_run_data(configuration, policies, tables)
    rebuilt = run_policy(rebuilt_data, policies[0], functions)
    assert rebuilt.error is None, rebuilt.error and rebuilt.error.as_dict()

    stored = {
        (row["projection_month"], row["variable_name"]): json.loads(row["value_json"])["value"]
        for row in stored_rows if row["policy_id"] == policies[0].policy_id
    }
    for month, variable, value in rebuilt.outputs:
        assert stored[(month, variable)] == pytest.approx(value, rel=1e-12, abs=1e-12)

    frozen_datasets = configuration["datasets"]
    assert [row["id"] for row in frozen_datasets["inforce"]] == [datasets["inforce"]["id"]]
    assert {row["id"] for row in frozen_datasets["assumption_tables"]} == {
        datasets["mortality"]["id"], datasets["improvement"]["id"],
    }
    assert [row["id"] for row in frozen_datasets["factor_tables"]] == [
        datasets["surrender_charge"]["id"]
    ]
    assert configuration["model_version"]["configuration"]["reference_model"]["source"][
        "commit"
    ] == "b706d8bbab530cc9ef94e06600e9c9d1468d8cb1"


def test_reference_model_data_manager_run_parquet_and_file_rebuild(env):
    _assert_reference_model_data_manager_run_parquet_and_file_rebuild(env)


@pytest.mark.parametrize(
    "database",
    ["neondb", "production_test", "mentoramp_production", "latest"],
)
def test_postgres_workflow_refuses_unsafe_database_names(database):
    with pytest.raises(ValueError, match="Refusing to reset database"):
        Env(
            database_url=f"postgresql://user:password@db:5432/{database}",
            reset_schema=True,
        )


@pytest.mark.skipif(
    not os.environ.get("MENTORAMP_TEST_POSTGRES_URL"),
    reason="set MENTORAMP_TEST_POSTGRES_URL to a disposable PostgreSQL database",
)
def test_reference_model_workflow_on_disposable_postgres():
    environment = Env(
        database_url=os.environ["MENTORAMP_TEST_POSTGRES_URL"], reset_schema=True
    )
    try:
        ids = environment.seed("A", horizon=24)
        environment.monkeypatch.setattr(
            data_manager_service, "get_artifact_store", lambda: environment.artifact_store,
        )
        with environment.Session() as db:
            project = db.get(Project, ids["project"])
            user = db.get(User, ids["admin"])
            seed = environment.seed_module
            model, version, group = seed.upsert_model(db, project, user, spia)
            seed.upsert_variables(db, spia)
            seed.upsert_definitions(db, version, spia)
            seed.upsert_formulas(db, version, group, spia)
            seed.upsert_published_outputs(db, version, spia)
            checks = seed.validate_model(db, model, version)
            assert checks["status"] == "validated", checks
            db.commit()
            ids["reference_version"] = version.id
        _assert_reference_model_data_manager_run_parquet_and_file_rebuild(environment)
    finally:
        environment.close()
