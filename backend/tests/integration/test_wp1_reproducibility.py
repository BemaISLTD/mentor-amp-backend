"""Work Package 1 — a submitted run executes its frozen package, never the live configuration.

A. Mutation after submit: formula, variable, assumption-table version, factor-table version,
   scenario and Projection Set edits made AFTER submission do not change the submitted run
   (and DO change a new submission, proving the edit was real).
B. Fingerprints: identical configuration -> identical package fingerprint; the final manifest
   fingerprint is a different hash that covers the outcome.
C. Dataset integrity: data edited in place after submission fails the run before calculation.
D. Factor pinning: the pinned factor table is used even when a newer table has the same name;
   unpinned or ambiguous factor tables are refused.
"""

import pytest
from sqlalchemy import update

from app.core.execution.fingerprints import fingerprint, scenario_fingerprint, table_fingerprint
from app.core.formula_engine.formulas import register_function
from app.db.immutability import ImmutableRecordError
from app.db.models.assumption import AssumptionTable
from app.db.models.factor import FactorSet, FactorTable
from app.db.models.formula import FormulaRegistry
from app.db.models.formula_dependency import FormulaDependency
from app.db.models.inforce import InforceRecord
from app.db.models.modeling import ModelPublishedOutput
from app.db.models.projection import ProjectionSet
from app.db.models.run_artifact import RunManifest
from app.db.models.run_package import RunAttempt, RunPackage
from app.db.models.scenario import ScenarioTable
from app.db.models.variable import VariableRegistry
from app.products.spia_lite import config as spia
from app.services import projection_set_service, run_execution_service
from app.services.common import ServiceError

from .wp1_support import SHORT_HORIZON, Env

register_function("test.zero", lambda **_inputs: 0.0, expression_text="x = 0 (test only)")
register_function(
    "test.scaled_payment",
    lambda expected_payment, payment_scale: expected_payment * payment_scale,
    expression_text="scaled_payment = expected_payment × payment_scale (test only)",
)


@pytest.fixture()
def env():
    environment = Env()
    environment.seed("A")
    yield environment
    environment.close()


def execute_and_summarise(env: Env, submitted: dict) -> dict[str, dict]:
    env.execute(submitted)
    return {name: env.summary(run_id) for name, run_id in submitted["by_scenario"].items()}


def package_of(env: Env, run_id: str) -> dict:
    response = env.get(f"/runs/{run_id}/package")
    assert response.status_code == 200, response.text
    return response.json()


def assert_same_result(actual: dict, expected: dict) -> None:
    assert actual["status"] == "success"
    assert actual["headline"]["value"] == expected["headline"]["value"]
    assert actual["totals"] == expected["totals"]
    assert actual["output_row_count"] == expected["output_row_count"]


# =============================================================================
# A + B: mutation after submit
# =============================================================================

def test_formula_edit_after_submit_does_not_change_the_submitted_run(env):
    baseline = execute_and_summarise(env, env.submit("A", ["Base"]))["Base"]
    submitted = env.submit("A", ["Base"])
    run_id = submitted["by_scenario"]["Base"]
    with env.Session() as db:
        formula = db.query(FormulaRegistry).filter_by(output_variable="expected_payment").one()
        formula.function_ref = "test.zero"
        formula.expression_text = "expected_payment = 0"
        db.commit()

    env.execute(submitted)
    assert_same_result(env.summary(run_id), baseline)
    frozen = {f["output_variable"]: f for f in package_of(env, run_id)["package"]["configuration"]["formulas"]}
    assert frozen["expected_payment"]["function_ref"] == spia.function_ref("expected_payment")
    # B: same configuration and build -> the same package fingerprint as the baseline run.
    assert env.run(run_id).run_package_fingerprint == baseline["run_package_fingerprint"]

    after = execute_and_summarise(env, env.submit("A", ["Base"]))["Base"]
    assert after["headline"]["value"] == 0.0  # the edit is real: a NEW run uses it
    assert after["run_package_fingerprint"] != baseline["run_package_fingerprint"]


def test_variable_source_edit_after_submit_does_not_change_the_submitted_run(env):
    baseline = execute_and_summarise(env, env.submit("A", ["Base"]))["Base"]
    submitted = env.submit("A", ["Base"])
    with env.Session() as db:
        variable = db.query(VariableRegistry).filter_by(name="discount_rate_annual").one()
        variable.source = {"type": "manual", "value": 0.10}
        variable.default_value = {"value": 0.10}
        db.commit()

    env.execute(submitted)
    assert_same_result(env.summary(submitted["by_scenario"]["Base"]), baseline)
    after = execute_and_summarise(env, env.submit("A", ["Base"]))["Base"]
    assert after["headline"]["value"] < baseline["headline"]["value"]  # 10% discounts more


def test_new_assumption_table_version_after_submit_does_not_change_the_submitted_run(env):
    ids = env.projects["A"]
    baseline = execute_and_summarise(env, env.submit("A", ["Base"]))["Base"]
    submitted = env.submit("A", ["Base"])
    with env.Session() as db:
        original = db.get(AssumptionTable, ids["table"])
        rows = [{**row, "qx": min(1.0, row["qx"] * 2)} for row in original.data]
        newer = AssumptionTable(
            set_id=original.set_id, table_name=original.table_name, table_type="mortality",
            lookup_keys=["age", "gender"], value_column="qx", data=rows, version_label="v2",
            status="validated", fingerprint=table_fingerprint(rows),
        )
        db.add(newer)
        db.flush()
        db.get(ProjectionSet, ids["projection_set"]).assumption_table_ids = [newer.id]
        db.commit()
        assert projection_set_service.validate(db, ids["projection_set"])["status"] == "validated"

    env.execute(submitted)
    run_id = submitted["by_scenario"]["Base"]
    assert_same_result(env.summary(run_id), baseline)
    tables = package_of(env, run_id)["package"]["configuration"]["datasets"]["assumption_tables"]
    assert [table["id"] for table in tables] == [ids["table"]]
    after = execute_and_summarise(env, env.submit("A", ["Base"]))["Base"]
    assert after["headline"]["value"] < baseline["headline"]["value"]  # doubled mortality


def test_scenario_edit_after_submit_does_not_change_the_submitted_run(env):
    ids = env.projects["A"]
    baseline = execute_and_summarise(env, env.submit("A", ["Low Interest Rate"]))["Low Interest Rate"]
    submitted = env.submit("A", ["Low Interest Rate"])
    with env.Session() as db:
        scenario = db.get(ScenarioTable, ids["scenarios"]["Low Interest Rate"])
        overrides = [{**scenario.overrides[0], "value": 0.01}]
        scenario.overrides = overrides
        scenario.fingerprint = scenario_fingerprint(overrides)  # edited and re-validated
        db.commit()

    env.execute(submitted)
    run_id = submitted["by_scenario"]["Low Interest Rate"]
    assert_same_result(env.summary(run_id), baseline)
    frozen = package_of(env, run_id)["package"]["configuration"]["scenario"]
    assert frozen["overrides"][0]["value"] == 0.03
    after = execute_and_summarise(env, env.submit("A", ["Low Interest Rate"]))["Low Interest Rate"]
    assert after["headline"]["value"] > baseline["headline"]["value"]  # 1% discounts less


def test_scenario_edited_without_revalidation_cannot_be_submitted(env):
    ids = env.projects["A"]
    with env.Session() as db:
        scenario = db.get(ScenarioTable, ids["scenarios"]["Low Interest Rate"])
        scenario.overrides = [{**scenario.overrides[0], "value": 0.02}]  # fingerprint now stale
        db.commit()
    with pytest.raises(ServiceError) as raised:
        env.submit("A", ["Low Interest Rate"])
    assert raised.value.code == "RUN_PACKAGE_INVALID"
    assert "SCENARIO_CHANGED" in {problem["code"] for problem in raised.value.details["problems"]}


def test_projection_set_edit_after_submit_does_not_change_the_submitted_run(env):
    ids = env.projects["A"]
    baseline = execute_and_summarise(env, env.submit("A", ["Base"]))["Base"]
    submitted = env.submit("A", ["Base"])
    with env.Session() as db:
        projection_set = db.get(ProjectionSet, ids["projection_set"])
        projection_set.horizon_months = 12
        projection_set.output_variables = ["reserve"]
        projection_set.parameters = {"note": "edited after submission"}
        db.commit()

    env.execute(submitted)
    result = env.summary(submitted["by_scenario"]["Base"])
    assert_same_result(result, baseline)
    assert result["period_count"] == SHORT_HORIZON


# =============================================================================
# B: fingerprint semantics
# =============================================================================

def test_package_and_final_manifest_fingerprints_mean_different_things(env):
    first = env.submit("A", ["Base"])
    second = env.submit("A", ["Base"])
    env.execute(first)
    env.execute(second)
    run_a, run_b = first["by_scenario"]["Base"], second["by_scenario"]["Base"]
    manifest_a = env.get(f"/runs/{run_a}/manifest").json()
    manifest_b = env.get(f"/runs/{run_b}/manifest").json()

    # Identical configuration -> identical package fingerprint...
    assert manifest_a["run_package"]["fingerprint"] == manifest_b["run_package"]["fingerprint"]
    # ...but each run's final manifest (outcome, run ID, times) has its own fingerprint.
    assert manifest_a["final_manifest"]["fingerprint"] != manifest_b["final_manifest"]["fingerprint"]
    assert manifest_a["final_manifest"]["fingerprint"] != manifest_a["run_package"]["fingerprint"]

    with env.Session() as db:
        for run_id, view in ((run_a, manifest_a), (run_b, manifest_b)):
            package = db.query(RunPackage).filter_by(run_id=run_id).one()
            # The package fingerprint covers exactly the configuration (not identity/governance).
            assert fingerprint(package.package["configuration"]) == package.fingerprint
            # The final manifest was not modified after it was fingerprinted.
            final = db.get(RunManifest, run_id)
            assert fingerprint(final.manifest) == final.fingerprint == view["final_manifest"]["fingerprint"]
            assert final.manifest["runtime"]["status"] == "success"
            assert final.manifest["configuration"]["run_package_fingerprint"] == package.fingerprint


def test_run_packages_and_final_manifests_are_write_once(env):
    submitted = env.submit("A", ["Base"])
    env.execute(submitted)
    run_id = submitted["by_scenario"]["Base"]
    with env.Session() as db:
        package = db.query(RunPackage).filter_by(run_id=run_id).one()
        package.fingerprint = "0" * 64
        with pytest.raises(ImmutableRecordError):
            db.flush()
        db.rollback()
        final = db.get(RunManifest, run_id)
        final.manifest = {"edited": True}
        with pytest.raises(ImmutableRecordError):
            db.flush()


# =============================================================================
# C: dataset and package integrity before calculation
# =============================================================================

@pytest.fixture()
def calculation_spy(monkeypatch):
    calls = {"count": 0}
    real = run_execution_service.run_policy

    def spy(*args, **kwargs):
        calls["count"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(run_execution_service, "run_policy", spy)
    return calls


def assert_refused_before_calculation(env: Env, run_id: str, error_type: str, calls: dict) -> None:
    run = env.run(run_id)
    assert run.status == "failed" and run.accepted_attempt_number is None
    assert calls["count"] == 0  # not a single policy was calculated
    assert env.row_counts(run_id) == (0, 0)
    with env.Session() as db:
        attempt = db.query(RunAttempt).filter_by(run_id=run_id).one()
        assert attempt.status == "failed" and attempt.error_type == error_type
        assert attempt.cleanup_status == "not_needed"
    blocked = env.get(f"/runs/{run_id}/aggregates")
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "RESULTS_NOT_AVAILABLE"
    manifest = env.get(f"/runs/{run_id}/manifest").json()["final_manifest"]["manifest"]
    assert manifest["runtime"]["status"] == "failed" and manifest["runtime"]["attempt"]["error_type"] == error_type


def test_inforce_record_edited_after_submit_fails_before_calculation(env, calculation_spy):
    submitted = env.submit("A", ["Base"])
    with env.Session() as db:
        record = db.query(InforceRecord).filter_by(policy_id="SPIA-0001").one()
        record.data = {**record.data, "monthly_payment": "9999"}
        db.commit()
    env.execute(submitted)
    assert_refused_before_calculation(env, submitted["by_scenario"]["Base"], "DATASET_FINGERPRINT_MISMATCH", calculation_spy)


def test_inforce_record_deleted_after_submit_fails_before_calculation(env, calculation_spy):
    submitted = env.submit("A", ["Base"])
    with env.Session() as db:
        db.delete(db.query(InforceRecord).filter_by(policy_id="SPIA-0025").one())
        db.commit()
    env.execute(submitted)
    assert_refused_before_calculation(env, submitted["by_scenario"]["Base"], "DATASET_FINGERPRINT_MISMATCH", calculation_spy)


def test_assumption_table_edited_in_place_fails_before_calculation(env, calculation_spy):
    submitted = env.submit("A", ["Base"])
    with env.Session() as db:
        table = db.get(AssumptionTable, env.projects["A"]["table"])
        rows = [dict(row) for row in table.data]
        rows[10]["qx"] = rows[10]["qx"] * 1.5
        table.data = rows  # fingerprint deliberately NOT updated
        db.commit()
    env.execute(submitted)
    assert_refused_before_calculation(env, submitted["by_scenario"]["Base"], "DATASET_FINGERPRINT_MISMATCH", calculation_spy)


def test_tampered_run_package_fails_before_calculation(env, calculation_spy):
    submitted = env.submit("A", ["Base"])
    run_id = submitted["by_scenario"]["Base"]
    with env.Session() as db:
        package = db.query(RunPackage).filter_by(run_id=run_id).one()
        document = dict(package.package)
        document["configuration"] = {**document["configuration"], "horizon_months": 6}
        # Bypass the ORM guard, as a direct database edit would (PostgreSQL also has a trigger).
        db.execute(update(RunPackage.__table__).where(RunPackage.__table__.c.id == package.id)
                   .values(package=document))
        db.commit()
    env.execute(submitted)
    assert_refused_before_calculation(env, run_id, "PACKAGE_TAMPERED", calculation_spy)


def test_package_frozen_for_another_engine_version_is_refused(env, calculation_spy, monkeypatch):
    submitted = env.submit("A", ["Base"])
    monkeypatch.setattr(run_execution_service, "ENGINE_VERSION", "some-later-engine")
    env.execute(submitted)
    assert_refused_before_calculation(env, submitted["by_scenario"]["Base"], "ENGINE_VERSION_MISMATCH", calculation_spy)


# =============================================================================
# D: factor pinning
# =============================================================================

def add_factor_model(env: Env, key: str) -> list[str]:
    """Extend the project's SPIA model with scaled_payment = expected_payment × payment_scale,
    where payment_scale comes from factor table TEST_PAYMENT_SCALE. Two tables share that name:
    the older one (scale 2.0) and a NEWER one (scale 3.0). Returns [older_id, newer_id]."""
    ids = env.projects[key]
    with env.Session() as db:
        db.add_all([
            VariableRegistry(
                name="payment_scale", display_name="Payment scale", data_type="number",
                source_type="factor", unit="factor", required=True, version="v1",
                source={"type": "factor", "table": "TEST_PAYMENT_SCALE", "value_column": "scale",
                        "key_map": {"gender": "gender"}},
                source_table="TEST_PAYMENT_SCALE", lookup_keys=["gender"],
                product_applicability=["SPIA"], basis_applicability=[],
            ),
            VariableRegistry(
                name="scaled_payment", display_name="Scaled payment", data_type="number",
                source_type="formula", unit="USD", required=False, version="v1",
                source={"type": "formula"}, lookup_keys=[], product_applicability=["SPIA"],
                basis_applicability=[],
            ),
        ])
        db.flush()
        formula = FormulaRegistry(
            name="Scaled payment", output_variable="scaled_payment", function_ref="test.scaled_payment",
            model_version_id=ids["version"], status="validated", version="v1", category="SPIA",
            product_applicability=["SPIA"], basis_applicability=[], illustrative=True,
        )
        db.add(formula)
        db.flush()
        db.add_all([FormulaDependency(formula_id=formula.id, depends_on_variable=name)
                    for name in ("expected_payment", "payment_scale")])
        db.add(ModelPublishedOutput(
            model_version_id=ids["version"], variable_name="scaled_payment", display_name="Scaled payment",
            unit="USD", aggregation="sum", dimension="Policy × scenario × projection month",
        ))
        factor_set = FactorSet(project_id=ids["project"], name="Test factors")
        db.add(factor_set)
        db.flush()
        table_ids = []
        for scale in (2.0, 3.0):
            rows = [{"gender": "M", "scale": scale}, {"gender": "F", "scale": scale}]
            table = FactorTable(
                set_id=factor_set.id, table_name="TEST_PAYMENT_SCALE", table_type="payment_scale",
                lookup_keys=["gender"], value_column="scale", data=rows, status="validated",
                fingerprint=table_fingerprint(rows), version_label=f"scale-{scale}",
            )
            db.add(table)
            db.flush()
            table_ids.append(table.id)
        projection_set = db.get(ProjectionSet, ids["projection_set"])
        projection_set.output_variables = list(projection_set.output_variables) + ["scaled_payment"]
        projection_set.factor_table_ids = [table_ids[0]]
        db.commit()
        assert projection_set_service.validate(db, projection_set.id)["status"] == "validated"
    return table_ids


def scale_used(summary: dict) -> float:
    return summary["totals"]["scaled_payment"]["value"] / summary["totals"]["expected_payment"]["value"]


def test_pinned_factor_table_is_used_even_when_a_newer_same_name_table_exists(env):
    older, newer = add_factor_model(env, "A")
    result = execute_and_summarise(env, env.submit("A", ["Base"]))["Base"]
    assert scale_used(result) == pytest.approx(2.0, rel=1e-12)  # the pinned (older) table
    run_id = env.submit("A", ["Base"])["by_scenario"]["Base"]
    configuration = package_of(env, run_id)["package"]["configuration"]
    assert configuration["table_bindings"]["TEST_PAYMENT_SCALE"] == {"kind": "factor", "table_id": older}
    assert [table["id"] for table in configuration["datasets"]["factor_tables"]] == [older]
    assert newer not in str(configuration)


def test_repinning_a_factor_table_after_submit_does_not_change_the_submitted_run(env):
    older, newer = add_factor_model(env, "A")
    submitted = env.submit("A", ["Base"])
    with env.Session() as db:
        db.get(ProjectionSet, env.projects["A"]["projection_set"]).factor_table_ids = [newer]
        db.commit()
        assert projection_set_service.validate(db, env.projects["A"]["projection_set"])["status"] == "validated"
    env.execute(submitted)
    assert scale_used(env.summary(submitted["by_scenario"]["Base"])) == pytest.approx(2.0, rel=1e-12)
    after = execute_and_summarise(env, env.submit("A", ["Base"]))["Base"]
    assert scale_used(after) == pytest.approx(3.0, rel=1e-12)


def test_factor_table_edited_in_place_fails_before_calculation(env, calculation_spy):
    older, _newer = add_factor_model(env, "A")
    submitted = env.submit("A", ["Base"])
    with env.Session() as db:
        table = db.get(FactorTable, older)
        table.data = [{"gender": "M", "scale": 9.0}, {"gender": "F", "scale": 9.0}]
        db.commit()
    env.execute(submitted)
    assert_refused_before_calculation(env, submitted["by_scenario"]["Base"], "DATASET_FINGERPRINT_MISMATCH", calculation_spy)


@pytest.mark.parametrize("pinned, code", [("none", "TABLE_NOT_PINNED"), ("both", "TABLE_AMBIGUOUS")])
def test_unpinned_or_ambiguous_factor_tables_are_refused(env, pinned, code):
    older, newer = add_factor_model(env, "A")
    ps_id = env.projects["A"]["projection_set"]
    with env.Session() as db:
        db.get(ProjectionSet, ps_id).factor_table_ids = [] if pinned == "none" else [older, newer]
        db.commit()
        result = projection_set_service.validate(db, ps_id)
    assert result["status"] == "draft"
    check = next(c for c in result["validation"]["checks"] if c["code"] == "assumption_tables_pinned")
    assert check["status"] == "fail"
    # Even if a (manipulated) Projection Set were marked validated, the freeze refuses it.
    with env.Session() as db:
        db.get(ProjectionSet, ps_id).status = "validated"
        db.commit()
    with pytest.raises(ServiceError) as raised:
        env.submit("A", ["Base"])
    assert code in {problem["code"] for problem in raised.value.details["problems"]}
