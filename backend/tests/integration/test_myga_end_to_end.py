"""Governed MYGA upload-to-Parquet workflow through the shared API and engine."""

import math
import os
import time
from pathlib import Path

import pytest

from app.core.artifacts import IDENTITY_NEUTRAL_SCHEMA
from app.db.models.project import Project
from app.db.models.run_artifact import RunArtifact
from app.db.models.user import User
from app.products.myga_lite import config as myga
from app.products.myga_lite.golden import GOLDEN_RESULTS
from app.services import data_manager_service
from scripts.load_myga_definition import load_myga_definition

from .test_spia_reference_workflow import _import_dataset
from .wp1_support import Env


SAMPLES = Path(__file__).resolve().parents[3] / "samples"
INFORCE_FIELDS = (
    "policy_id", "product_type", "issue_date", "issue_age", "gender", "premium",
    "guarantee_period_years", "declared_rate", "gmir", "gmsv_rate",
    "mva_ref_yield_at_issue", "account_value", "mgsv_value", "free_wd_base",
)
SCENARIO_FIELDS = (
    "scenario_name", "target_variable", "operation", "value",
    "applies_from_period", "applies_to_period",
)


def _parquet_file_bytes(env: Env, run_id: str) -> list[tuple[str, int, bytes]]:
    """Compare the actual retained files, not a normalized or rewritten payload."""
    with env.Session() as db:
        artifacts = db.query(RunArtifact).filter_by(
            run_id=run_id, attempt_number=1,
        ).all()
        assert artifacts
        assert all(artifact.schema_version == IDENTITY_NEUTRAL_SCHEMA for artifact in artifacts)
        return sorted(
            (artifact.artifact_type, artifact.row_count,
             env.artifact_store.read_bytes(artifact.storage_uri))
            for artifact in artifacts
        )


@pytest.fixture()
def myga_env():
    environment = Env()
    _seed_myga(environment)
    yield environment
    environment.close()


def _seed_myga(environment: Env) -> None:
    ids = environment.seed("A", horizon=24)
    environment.monkeypatch.setattr(
        data_manager_service, "get_artifact_store", lambda: environment.artifact_store,
    )
    with environment.Session() as db:
        project = db.get(Project, ids["project"])
        user = db.get(User, ids["admin"])
        loaded = load_myga_definition(db, project, user)
        version = loaded["version"]
        checks = loaded["validation"]
        assert checks["status"] == "validated", checks
        db.commit()
        ids["myga_version"] = version.id


def _import_myga_inputs(env: Env, reviewer_headers: dict) -> dict[str, dict]:
    version_id = env.projects["A"]["myga_version"]
    inforce = (SAMPLES / "myga" / "synthetic_myga_inforce.csv").read_bytes()
    mortality = (SAMPLES / "spia" / "synthetic_mortality_gompertz.csv").read_bytes()
    charges = (SAMPLES / "myga" / "myga_surrender_charge.csv").read_bytes()
    age_caps = (SAMPLES / "myga" / "myga_sc_age_cap.csv").read_bytes()
    base = (
        "scenario_name,target_variable,operation,value,applies_from_period,applies_to_period\n"
        "Base,market_rate,set,0.0445,1,480\n"
        "Base,mva_ref_yield,set,0.05,1,480\n"
    ).encode()
    rates_up = (
        "scenario_name,target_variable,operation,value,applies_from_period,applies_to_period\n"
        "Rates Up 150bp,market_rate,set,0.0595,1,480\n"
        "Rates Up 150bp,mva_ref_yield,set,0.065,1,480\n"
    ).encode()
    return {
        "inforce": _import_dataset(
            env, reviewer_headers, category="liability_inforce", name="Synthetic MYGA block",
            content=inforce, fields=INFORCE_FIELDS,
            options={"valuation_date": "2026-12-31", "expected_record_count": 25},
        ),
        "mortality": _import_dataset(
            env, reviewer_headers, category="assumption_table",
            name=myga.MORTALITY_TABLE_NAME, content=mortality,
            fields=("age", "gender", "qx"),
            options={
                "table_type": "mortality", "lookup_keys": ["age", "gender"],
                "value_column": "qx", "model_version_id": version_id,
            },
        ),
        "charges": _import_dataset(
            env, reviewer_headers, category="factor_table",
            name=myga.SURRENDER_CHARGE_TABLE_NAME, content=charges,
            fields=("schedule", "year_in_gp", "rate"),
            options={
                "table_type": "surrender_charge",
                "lookup_keys": ["schedule", "year_in_gp"], "value_column": "rate",
                "model_version_id": version_id,
            },
        ),
        "age_caps": _import_dataset(
            env, reviewer_headers, category="factor_table",
            name=myga.SURRENDER_CHARGE_AGE_CAP_TABLE_NAME, content=age_caps,
            fields=("attained_age", "cap"),
            options={
                "table_type": "surrender_charge_age_cap",
                "lookup_keys": ["attained_age"], "value_column": "cap",
                "model_version_id": version_id,
            },
        ),
        "base": _import_dataset(
            env, reviewer_headers, category="scenario", name="MYGA Base",
            content=base, fields=SCENARIO_FIELDS,
            options={"model_version_id": version_id},
        ),
        "rates_up": _import_dataset(
            env, reviewer_headers, category="scenario", name="MYGA Rates Up",
            content=rates_up, fields=SCENARIO_FIELDS,
            options={"model_version_id": version_id},
        ),
    }


def _assert_myga_25_policy_api_run_trace_and_parquet(env: Env) -> dict:
    ids = env.projects["A"]
    reviewer = env.user(
        "myga-reviewer@example.com", role="reviewer", memberships={"A": "viewer"},
    )
    imported = _import_myga_inputs(env, reviewer["headers"])
    datasets = {name: item["dataset"] for name, item in imported.items()}
    outputs = [
        "account_value_close", "mgsv_close", "surrender_benefit", "death_benefit",
        "death_claims", "surrender_claims", "annuitization_transfer", "maturity_benefit",
        "maintenance_expense", "liability_outgo", "net_cash_flow", "reserve",
    ]
    created = env.client.post(
        f"/v1/projects/{ids['project']}/projection-sets",
        headers=env.admin_headers(),
        json={
            "name": "MYGA Illustrative Base and Rates Up",
            "model_version_id": ids["myga_version"],
            "inforce_file_ids": [datasets["inforce"]["id"]],
            "assumption_table_ids": [datasets["mortality"]["id"]],
            "factor_table_ids": [datasets["charges"]["id"], datasets["age_caps"]["id"]],
            "scenario_ids": [datasets["base"]["id"], datasets["rates_up"]["id"]],
            "valuation_date": "2026-12-31", "horizon_months": 480,
            "time_step": "monthly", "output_variables": outputs,
            "trace_scope": {"mode": "selected_policies", "policy_ids": ["MYGA-0001"]},
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

    started = time.perf_counter()
    submitted = env.client.post("/v1/run-sets", headers=env.admin_headers(), json={
        "project_id": ids["project"], "name": "MYGA governed end-to-end",
        "projection_set_ids": [projection_set_id],
        "scenario_ids": [datasets["base"]["id"], datasets["rates_up"]["id"]],
    })
    elapsed = time.perf_counter() - started
    assert submitted.status_code == 202, submitted.text
    assert elapsed < 120.0  # Allow headroom for Windows / local dev background load
    runs = submitted.json()["runs"]
    assert len(runs) == 2
    assert {run["scenario"]["name"] for run in runs} == {"Base", "Rates Up 150bp"}

    summaries = {}
    for item in runs:
        summary = env.summary(item["id"])
        summaries[item["scenario"]["name"]] = summary
        assert summary["status"] == "success", summary
        assert summary["failed_policy_count"] == 0
        with env.Session() as db:
            artifacts = db.query(RunArtifact).filter_by(run_id=item["id"]).all()
            assert artifacts
            assert all(row.storage_uri.endswith(".parquet") for row in artifacts)
            assert all(row.checksum_sha256 and row.row_count > 0 for row in artifacts)

    assert math.isclose(summaries["Base"]["headline"]["value"], GOLDEN_RESULTS["base_reserve"], abs_tol=1e-6)
    assert math.isclose(summaries["Rates Up 150bp"]["headline"]["value"], GOLDEN_RESULTS["rates_up_reserve"], abs_tol=1e-6)
    assert math.isclose(
        summaries["Base"]["totals"]["liability_outgo"]["value"],
        GOLDEN_RESULTS["base_undiscounted_outgo"],
        abs_tol=1e-6,
    )
    assert math.isclose(
        summaries["Rates Up 150bp"]["totals"]["liability_outgo"]["value"],
        GOLDEN_RESULTS["rates_up_undiscounted_outgo"],
        abs_tol=1e-6,
    )

    base_run = next(item["id"] for item in runs if item["scenario"]["name"] == "Base")
    trace = env.get(
        f"/runs/{base_run}/trace", policy_id="MYGA-0001", month=30,
        variable="surrender_benefit", depth=8,
    )
    assert trace.status_code == 200, trace.text
    root = trace.json()["root"]

    def variables(node: dict) -> set[str]:
        return {node["variable"]} | {
            variable
            for child in node.get("children", [])
            for variable in variables(child)
        }

    assert root["variable"] == "surrender_benefit"
    assert math.isclose(root["value"], GOLDEN_RESULTS["base_policy_0001_month_30_surrender_benefit"], abs_tol=1e-6)

    def child(node: dict, variable: str) -> dict:
        return next(item for item in node["children"] if item["variable"] == variable)

    contractual = child(root, "surrender_value")
    floor = child(root, "mgsv_close")
    account = child(contractual, "account_value_close")
    adjustment = child(contractual, "market_value_adjustment")
    charge = child(contractual, "surrender_charge")
    excess = child(adjustment, "surrender_excess")
    free_amount = child(excess, "free_withdrawal_available")
    schedule_rate = child(child(charge, "surrender_charge_rate"), "schedule_rate")

    assert {"account_value_close", "free_withdrawal_available", "surrender_excess",
            "market_value_adjustment", "surrender_charge", "surrender_value",
            "mgsv_close", "surrender_benefit"}.issubset(variables(root))
    assert excess["value"] == pytest.approx(account["value"] - free_amount["value"])
    assert adjustment["value"] >= -charge["value"]
    assert adjustment["value"] <= charge["value"]
    assert contractual["value"] == pytest.approx(
        account["value"] + adjustment["value"] - charge["value"]
    )
    assert root["value"] == max(contractual["value"], floor["value"])
    assert schedule_rate["source"]["table"] == myga.SURRENDER_CHARGE_TABLE_NAME

    repeated = env.client.post("/v1/run-sets", headers=env.admin_headers(), json={
        "project_id": ids["project"], "name": "MYGA deterministic replay",
        "projection_set_ids": [projection_set_id],
        "scenario_ids": [datasets["base"]["id"]],
    })
    assert repeated.status_code == 202, repeated.text
    repeated_run = repeated.json()["runs"][0]["id"]
    assert env.summary(repeated_run)["status"] == "success"
    assert _parquet_file_bytes(env, base_run) == _parquet_file_bytes(env, repeated_run)
    return {"runs": runs, "summaries": summaries, "trace": trace.json()}


def test_myga_25_policy_api_run_trace_and_parquet(myga_env):
    _assert_myga_25_policy_api_run_trace_and_parquet(myga_env)


@pytest.mark.skipif(
    not os.environ.get("MENTORAMP_TEST_POSTGRES_URL"),
    reason="set MENTORAMP_TEST_POSTGRES_URL to a disposable PostgreSQL database",
)
def test_myga_workflow_on_disposable_postgres():
    environment = Env(
        database_url=os.environ["MENTORAMP_TEST_POSTGRES_URL"], reset_schema=True,
    )
    try:
        _seed_myga(environment)
        _assert_myga_25_policy_api_run_trace_and_parquet(environment)
    finally:
        environment.close()
