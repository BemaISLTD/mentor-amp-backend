"""Tests for M5 Valuation RunPackage provenance and reproducibility (Directives 3 & 16, P0-03, V5-P0-C, V5-P0-D).

Verifies:
1. Pure reproducibility: Identical valuation inputs produce identical package fingerprints bit-for-bit.
2. Material sensitivity: Mutating ANY valuation provenance field alters the package fingerprint.
3. Envelope integrity: verify_envelope detects mismatches between run row and frozen valuation package.
4. Concrete governed valuation run package artifact verification.
5. P0-03 & V5-P0-C / V5-P0-D: Trusted provenance enforcement (mandatory DB session, authoritative upstream source,
   accepted-attempt artifact verification, DerivedDataset physical artifact + DB Model/ModelVersion verification,
   four-eyes dataset approval, product family match, and zero synthetic model/version fallbacks).
"""

import copy
from datetime import date
import gc
import json

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.artifacts import get_artifact_store
from app.core.execution import run_package
from app.core.execution.fingerprints import fingerprint
from app.core.execution.run_package import RunPackageError, verify, verify_envelope
import app.core.execution.run_package as core_run_pkg
from app.db.database import Base
from app.db.models.governance import DerivedDataset
from app.db.models.modeling import Model, ModelVersion
from app.db.models.project import Project
from app.db.models.project_member import ProjectMember
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact
from app.db.models.run_package import RunPackage
from app.db.models.user import User
from app.db.models.valuation_detail import ValuationRunDetail
from app.services.build_info import BuildIdentityUnavailable, build_identity
from app.services.common import ServiceError, iso, now_utc
from app.services.run_package_service import freeze_valuation_run_package
from app.services.run_submission_service import submit_valuation_run


SAMPLE_VALUATION_CONFIGURATION = {
    "project": {"id": "proj-m5-core", "name": "M5 Valuation Core"},
    "run_type": "valuation",
    "model": {"id": "model-spia-01", "name": "SPIA VM-22 Model", "product_code": "SPIA"},
    "model_version": {
        "id": "mv-spia-v1",
        "version_label": "v1.0.0",
        "basis": "STATUTORY",
        "methodology": "VM22",
        "methodology_version": "v2024.1",
        "block_name": "SPIA-Payout-2024",
        "profile_name": "Standard",
        "illustrative": False,
    },
    "valuation_date": "2026-12-31",
    "horizon_months": 360,
    "time_step": "monthly",
    "execution_backend": "cpu",
    "parameters": {"risk_margin": 0.025},
    "formulas": [
        {
            "id": "form-npr-1",
            "name": "StatutoryReserveFormula",
            "output_variable": "statutory_reserve",
            "function_ref": "dsl:pv_future_benefits",
            "version": "1.0",
            "dependencies": ["discount_factor", "expected_benefit_cf"],
            "expression_text": "expected_benefit_cf * discount_factor",
            "unit": "USD",
            "illustrative": False,
            "content_fingerprint": "1111111111111111111111111111111111111111111111111111111111111111",
        }
    ],
    "variables": [
        {
            "id": "var-reserve-1",
            "name": "statutory_reserve",
            "display_name": "Statutory Reserve",
            "version": "1.0",
            "kind": "prior_output",
            "data_type": "number",
            "unit": "USD",
            "required": True,
            "default_value": None,
            "source": {"type": "prior_output", "dataset_id": "ds-proj-run-baseline", "column_name": "reserve"},
            "allow_scenario_override": False,
            "content_fingerprint": "2222222222222222222222222222222222222222222222222222222222222222",
        }
    ],
    "datasets": {
        "inforce": [
            {
                "id": "inf-file-01",
                "name": "spia_inforce_2026.parquet",
                "version_label": "v1",
                "record_count": 5000,
                "fingerprint": "3333333333333333333333333333333333333333333333333333333333333333",
                "fingerprint_scheme": "sha256/parquet-rows/v1",
            }
        ],
        "assumption_tables": [
            {
                "id": "tbl-mort-01",
                "name": "vm22_prescribed_mortality",
                "set_id": "set-mort-2024",
                "set_name": "2024 VBT",
                "table_type": "mortality",
                "version_label": "v1",
                "lookup_keys": ["age", "gender"],
                "value_column": "q_x",
                "row_count": 120,
                "fingerprint": "4444444444444444444444444444444444444444444444444444444444444444",
            }
        ],
        "factor_tables": [],
    },
    "table_bindings": {
        "vm22_prescribed_mortality": {"kind": "assumption", "table_id": "tbl-mort-01"}
    },
    "scenario": {
        "id": "scen-naic-prescribed-1",
        "set_id": "set-scen-stat",
        "set_name": "NAIC Prescribed Curves",
        "name": "Baseline Prescribed Curve",
        "version_label": "v1.0",
        "scenario_type": "deterministic",
        "as_of_date": "2026-12-31",
        "path_count": 1,
        "fingerprint": "5555555555555555555555555555555555555555555555555555555555555555",
        "overrides": [],
    },
    "outputs": {
        "output_variables": ["statutory_reserve"],
        "trace_scope": {"mode": "none", "policy_ids": []},
        "max_traced_policies": 10,
        "published": {
            "statutory_reserve": {
                "display_name": "Statutory Reserve Balance",
                "unit": "USD",
                "dimension": "balance",
                "aggregation": "end_of_period",
            }
        },
    },
    "valuation": {
        "basis": "STATUTORY",
        "methodology": "VM22",
        "methodology_version": "v2024.1",
        "source_projection_run_id": "run-proj-spia-base-01",
        "derived_dataset_id": "ds-proj-run-baseline",
        "derived_dataset_fingerprint": "6666666666666666666666666666666666666666666666666666666666666666",
        "source_artifact_fingerprint": "7777777777777777777777777777777777777777777777777777777777777777",
        "discount_curve_fingerprint": "8888888888888888888888888888888888888888888888888888888888888888",
        "valuation_date": "2026-12-31",
        "valuation_parameters": {"prescribed_spread_bps": 125, "floor_rate": 0.015},
        "valuation_config_fingerprint": "9999999999999999999999999999999999999999999999999999999999999999",
    },
    "build": {
        "app_version": "1.0.0",
        "engine_version": "m5-valuation-1",
        "code_version": "release-v1.0",
        "build_fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "source_commit": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "source_dirty": False,
        "identity_source": "git",
    },
}


def seal_valuation_package(configuration=None, package_id="pkg-val-1", run_id="run-val-1"):
    cfg = copy.deepcopy(configuration or SAMPLE_VALUATION_CONFIGURATION)
    identity = {
        "package_id": package_id,
        "frozen_for_run_id": run_id,
        "run_set_id": None,
        "project_id": cfg["project"]["id"],
        "created_at": "2026-12-31T23:59:59Z",
        "created_by": {"id": "user-lead-actuary", "full_name": "Lead Actuary"},
    }
    governance = {
        "governance_mode": "governed",
        "is_governed": True,
        "frozen_at": "2026-12-31T23:59:59Z",
        "valuation_basis": cfg["valuation"]["basis"],
        "valuation_methodology": cfg["valuation"]["methodology"],
    }
    return run_package.seal(identity, governance, cfg)


def test_valuation_reproducibility_identical_rerun():
    """Directive 16: Identical valuation inputs produce the exact same package fingerprint."""
    pkg1 = seal_valuation_package()
    pkg2 = seal_valuation_package(package_id="pkg-val-DIFFERENT-ID", run_id="run-val-DIFFERENT-RUN")

    assert pkg1["fingerprint"] == pkg2["fingerprint"]
    assert pkg1["fingerprint"] == fingerprint(SAMPLE_VALUATION_CONFIGURATION)

    verified_cfg = verify(pkg1, expected_fingerprint=pkg1["fingerprint"])
    assert verified_cfg["valuation"]["methodology"] == "VM22"


@pytest.mark.parametrize(
    "field_path, mutated_value",
    [
        (("valuation", "basis"), "GAAP"),
        (("valuation", "methodology"), "VM21"),
        (("valuation", "methodology_version"), "v2025.1"),
        (("valuation", "source_projection_run_id"), "run-proj-spia-MUTATED"),
        (("valuation", "derived_dataset_id"), "ds-MUTATED"),
        (("valuation", "derived_dataset_fingerprint"), "666666666666666666666666666666666666666666666666666666666666666f"),
        (("valuation", "source_artifact_fingerprint"), "777777777777777777777777777777777777777777777777777777777777777f"),
        (("valuation", "discount_curve_fingerprint"), "888888888888888888888888888888888888888888888888888888888888888f"),
        (("valuation", "valuation_date"), "2027-12-31"),
        (("valuation", "valuation_config_fingerprint"), "999999999999999999999999999999999999999999999999999999999999999f"),
        (("valuation", "valuation_parameters", "prescribed_spread_bps"), 150),
    ],
)
def test_material_valuation_change_changes_package_fingerprint(field_path, mutated_value):
    """Directive 3 & 16: Proves mutating any material valuation field changes the package fingerprint."""
    baseline_fp = fingerprint(SAMPLE_VALUATION_CONFIGURATION)

    mutated_cfg = copy.deepcopy(SAMPLE_VALUATION_CONFIGURATION)
    target = mutated_cfg
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = mutated_value

    mutated_fp = fingerprint(mutated_cfg)
    assert mutated_fp != baseline_fp, f"Fingerprint failed to change when mutating {field_path}!"


def test_envelope_verification_enforces_run_row_and_valuation_identity_agreement():
    """Directive 3: verify_envelope ensures run row and package valuation metadata agree."""
    pkg = seal_valuation_package()
    package_row = {
        "id": "pkg-val-1",
        "run_id": "run-val-1",
        "project_id": "proj-m5-core",
        "fingerprint": pkg["fingerprint"],
        "schema_version": pkg["schema_version"],
        "fingerprint_algorithm": pkg["fingerprint_algorithm"],
    }

    matching_run = {
        "id": "run-val-1",
        "project_id": "proj-m5-core",
        "run_set_id": None,
        "run_package_fingerprint": pkg["fingerprint"],
        "scenario_id": "scen-naic-prescribed-1",
        "model_version_id": "mv-spia-v1",
        "projection_set_id": None,
        "horizon_months": 360,
        "valuation_date": "2026-12-31",
        "basis": "STATUTORY",
        "methodology": "VM22",
        "methodology_version": "v2024.1",
        "source_projection_run_id": "run-proj-spia-base-01",
        "derived_dataset_id": "ds-proj-run-baseline",
    }
    verify_envelope(pkg, package_row, matching_run)

    mismatched_run_basis = copy.deepcopy(matching_run)
    mismatched_run_basis["basis"] = "GAAP"
    with pytest.raises(RunPackageError) as exc_info:
        verify_envelope(pkg, package_row, mismatched_run_basis)
    assert exc_info.value.code == "RUN_INDEX_MISMATCH"
    assert "basis" in exc_info.value.message

    mismatched_run_meth = copy.deepcopy(matching_run)
    mismatched_run_meth["methodology"] = "VM21"
    with pytest.raises(RunPackageError) as exc_info:
        verify_envelope(pkg, package_row, mismatched_run_meth)
    assert exc_info.value.code == "RUN_INDEX_MISMATCH"
    assert "methodology" in exc_info.value.message


def test_tampered_package_fails_verification():
    """Directive 3: Tampering with package configuration raises PACKAGE_TAMPERED."""
    pkg = seal_valuation_package()
    pkg["configuration"]["valuation"]["discount_curve_fingerprint"] = "TAMPERED_CURVE_HASH"

    with pytest.raises(RunPackageError) as exc_info:
        verify(pkg, expected_fingerprint=pkg["fingerprint"])
    assert exc_info.value.code == "PACKAGE_TAMPERED"


# =============================================================================
# Production Service Integration Tests (Directive 2, Blocker 2, P0-03, V5-P0-C, V5-P0-D)
# =============================================================================


@pytest.fixture()
def db_session_val(tmp_path):
    db_file = tmp_path / "val_repro_test.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        gc.collect()
        engine.dispose()


@pytest.fixture()
def seeded_val_env(db_session_val):
    proj1 = Project(id="proj-val-1", name="Valuation Project 1")
    proj2 = Project(id="proj-val-2", name="Valuation Project 2")
    user = User(id="user-val-actuary", email="val@mentoramp.com", full_name="Val Actuary", password_hash="pwd")
    reviewer = User(id="user-val-reviewer", email="reviewer@mentoramp.com", full_name="Val Reviewer", password_hash="pwd")
    db_session_val.add_all([proj1, proj2, user, reviewer])
    db_session_val.commit()
    mem1 = ProjectMember(project_id=proj1.id, user_id=user.id, role="owner")
    mem2 = ProjectMember(project_id=proj2.id, user_id=user.id, role="owner")
    db_session_val.add_all([mem1, mem2])
    db_session_val.commit()
    return {"session": db_session_val, "proj1": proj1, "proj2": proj2, "user": user, "reviewer": reviewer}


def _ensure_governed_spia_model(
    db,
    project_id,
    model_id="model-spia-01",
    mv_id="mv-spia-v1",
    mv_status="approved",
    product_code="SPIA",
):
    model = db.get(Model, model_id)
    if model is None:
        model = Model(
            id=model_id,
            project_id=project_id,
            name="SPIA VM-22 Model" if model_id == "model-spia-01" else f"Model {model_id}",
            product_code=product_code,
            status="approved",
            created_by="user-val-actuary",
        )
        db.add(model)
        db.flush()
    mv = db.get(ModelVersion, mv_id)
    if mv is None:
        mv = ModelVersion(
            id=mv_id,
            model_id=model.id,
            version_label="v1.0.0" if mv_id == "mv-spia-v1" else f"v-{mv_id}",
            status=mv_status,
            basis="STATUTORY",
            methodology="VM22",
            created_by="user-val-actuary",
            approved_by="user-val-reviewer" if mv_status in ("approved", "published") else None,
            approved_at=now_utc() if mv_status in ("approved", "published") else None,
        )
        db.add(mv)
        db.flush()
    return model, mv


def _seed_source_projection_run(db, project_id, run_id="run-src-proj-1", status="success", accepted_attempt=1):
    _ensure_governed_spia_model(db, project_id)
    store = get_artifact_store()
    rows = [{"policy_id": "P1", "month": 1, "cash_flow": 100.0, "reserve": 5000.0}]
    stored = store.write_rows(
        run_id=run_id,
        artifact_type="cash_flows",
        rows=rows,
        attempt_number=1,
        schema_version="m2-v1",
    )

    src_config = {
        "project": {"id": project_id, "name": "Valuation Project"},
        "run_type": "projection",
        "model": {"id": "model-spia-01", "name": "SPIA VM-22 Model", "product_code": "SPIA"},
        "model_version": {
            "id": "mv-spia-v1",
            "version_label": "v1.0.0",
            "basis": "STATUTORY",
            "methodology": "VM22",
            "methodology_version": "v2024.1",
        },
        "valuation_date": "2026-12-31",
        "horizon_months": 12,
        "parameters": {"spread": 50},
        "formulas": [],
        "variables": [],
        "datasets": {"inforce": []},
        "build": build_identity(),
    }
    src_identity = {
        "package_id": f"pkg-{run_id}",
        "frozen_for_run_id": run_id,
        "project_id": project_id,
        "created_at": iso(now_utc()),
    }
    src_governance = {
        "governance_mode": "governed",
        "is_governed": True,
        "frozen_at": iso(now_utc()),
    }
    sealed = core_run_pkg.seal(src_identity, src_governance, src_config)

    src_run = Run(
        id=run_id,
        project_id=project_id,
        name=f"Source Projection {run_id}",
        status=status,
        run_type="projection",
        model_version_id="mv-spia-v1",
        horizon_months=12,
        valuation_date=date(2026, 12, 31),
        run_package_fingerprint=sealed["fingerprint"],
        accepted_attempt_number=accepted_attempt if status in ("success", "completed") else None,
        attempt_count=max(accepted_attempt or 0, 1),
    )
    db.add(src_run)
    db.flush()

    src_pkg_row = RunPackage(
        id=sealed["identity"]["package_id"],
        run_id=src_run.id,
        project_id=project_id,
        schema_version=sealed["schema_version"],
        fingerprint_algorithm=sealed["fingerprint_algorithm"],
        fingerprint=sealed["fingerprint"],
        package=sealed,
    )
    db.add(src_pkg_row)

    src_art = RunArtifact(
        id=f"art-cf-{run_id}",
        run_id=src_run.id,
        attempt_number=1,
        artifact_type="cash_flows",
        storage_uri=stored.uri,
        storage_backend="local",
        file_format="parquet",
        schema_version="m2-v1",
        row_count=stored.row_count,
        checksum_sha256=stored.checksum_sha256,
    )
    db.add(src_art)
    db.commit()
    return src_run, src_pkg_row, src_art


def test_real_service_freeze_valuation_run_package_success(seeded_val_env):
    """Directive 2 & Blocker 2: Calling freeze_valuation_run_package succeeds with verified model identity from source run."""
    db = seeded_val_env["session"]
    proj = seeded_val_env["proj1"]
    user = seeded_val_env["user"]
    src_run, src_pkg, src_art = _seed_source_projection_run(db, proj.id)

    run = Run(id="run-val-actual-1", project_id=proj.id, name="Test Valuation Run")
    db.add(run)
    db.commit()

    pkg = freeze_valuation_run_package(
        db=db,
        project=proj,
        run=run,
        user=user,
        basis="STATUTORY",
        methodology="VM22",
        methodology_version="v2024.1",
        source_projection_run_id=src_run.id,
        valuation_date="2026-12-31",
        valuation_parameters={"spread_bps": 100},
    )

    assert pkg.fingerprint is not None
    assert len(pkg.fingerprint) == 64
    assert pkg.configuration["valuation"]["basis"] == "STATUTORY"
    assert pkg.configuration["valuation"]["methodology"] == "VM22"
    assert pkg.configuration["valuation"]["methodology_version"] == "v2024.1"
    assert pkg.configuration["valuation"]["source_projection_run_id"] == src_run.id
    assert pkg.configuration["valuation"]["source_run_package_fingerprint"] == src_pkg.fingerprint
    assert pkg.configuration["valuation"]["source_artifact_id"] == src_art.id
    assert pkg.configuration["valuation"]["source_artifact_fingerprint"] == src_art.checksum_sha256
    assert pkg.configuration["model"]["id"] == "model-spia-01"
    assert pkg.configuration["model_version"]["id"] == "mv-spia-v1"
    assert pkg.configuration["valuation"]["valuation_parameters"]["spread_bps"] == 100
    assert pkg.configuration["build"]["identity_source"] in ("source_tree", "injected", "computed")


def test_service_freeze_fails_on_missing_or_corrupt_build_identity(seeded_val_env, monkeypatch):
    """Directive 2 & Blocker 2: Fails closed (503) when build identity is missing or fallback."""
    import app.services.run_package_service as rps

    db = seeded_val_env["session"]
    proj = seeded_val_env["proj1"]
    user = seeded_val_env["user"]
    src_run, _, _ = _seed_source_projection_run(db, proj.id)
    run = Run(id="run-val-actual-2", project_id=proj.id)

    def mock_unavailable():
        raise BuildIdentityUnavailable("No build identity available")

    monkeypatch.setattr(rps, "build_identity", mock_unavailable)

    with pytest.raises(ServiceError) as exc_info:
        freeze_valuation_run_package(
            db=db,
            project=proj,
            run=run,
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
            source_projection_run_id=src_run.id,
        )
    assert exc_info.value.code == "BUILD_IDENTITY_UNAVAILABLE"
    assert exc_info.value.status_code == 503

    def mock_fallback():
        return {
            "app_version": "0.1.0",
            "build_fingerprint": "0" * 64,
            "source_commit": "0" * 40,
            "identity_source": "fallback",
        }

    monkeypatch.setattr(rps, "build_identity", mock_fallback)

    with pytest.raises(ServiceError) as exc_info2:
        freeze_valuation_run_package(
            db=db,
            project=proj,
            run=run,
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
            source_projection_run_id=src_run.id,
        )
    assert exc_info2.value.code == "BUILD_IDENTITY_UNAVAILABLE"


def test_service_freeze_foreign_source_projection_run_fails(seeded_val_env):
    """Directive 2: Foreign source projection run fails closed with 404 RUN_NOT_FOUND."""
    db = seeded_val_env["session"]
    proj1 = seeded_val_env["proj1"]
    proj2 = seeded_val_env["proj2"]
    user = seeded_val_env["user"]

    foreign_run = Run(id="run-proj2-source", project_id=proj2.id, name="Foreign Run")
    db.add(foreign_run)
    db.commit()

    target_run = Run(id="run-val-target", project_id=proj1.id)

    with pytest.raises(ServiceError) as exc_info:
        freeze_valuation_run_package(
            db=db,
            project=proj1,
            run=target_run,
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
            source_projection_run_id=foreign_run.id,
        )
    assert exc_info.value.code == "RUN_NOT_FOUND"
    assert exc_info.value.status_code == 404


def test_service_freeze_foreign_derived_dataset_fails(seeded_val_env):
    """Directive 2: Foreign derived dataset fails closed with 404 DATASET_NOT_FOUND."""
    db = seeded_val_env["session"]
    proj1 = seeded_val_env["proj1"]
    proj2 = seeded_val_env["proj2"]
    user = seeded_val_env["user"]

    foreign_ds = DerivedDataset(
        id="ds-foreign-1",
        project_id=proj2.id,
        name="foreign_dataset",
        dataset_type="cash_flows",
        version_number=1,
        status="approved",
        fingerprint="a" * 64,
        created_by="user-1",
        approved_by="user-2",
        approved_at=now_utc(),
    )
    db.add(foreign_ds)
    db.commit()

    target_run = Run(id="run-val-target-2", project_id=proj1.id)

    with pytest.raises(ServiceError) as exc_info:
        freeze_valuation_run_package(
            db=db,
            project=proj1,
            run=target_run,
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
            derived_dataset_id=foreign_ds.id,
        )
    assert exc_info.value.code == "DATASET_NOT_FOUND"
    assert exc_info.value.status_code == 404


def test_service_freeze_unapproved_derived_dataset_fails(seeded_val_env):
    """Directive 2: Governed valuation run rejects unapproved derived dataset with 400 DATASET_NOT_APPROVED."""
    db = seeded_val_env["session"]
    proj1 = seeded_val_env["proj1"]
    user = seeded_val_env["user"]

    draft_ds = DerivedDataset(
        id="ds-draft-1",
        project_id=proj1.id,
        name="draft_dataset",
        dataset_type="cash_flows",
        version_number=1,
        status="draft",
        fingerprint="b" * 64,
        created_by="user-1",
    )
    db.add(draft_ds)
    db.commit()

    target_run = Run(id="run-val-target-3", project_id=proj1.id)

    with pytest.raises(ServiceError) as exc_info:
        freeze_valuation_run_package(
            db=db,
            project=proj1,
            run=target_run,
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
            derived_dataset_id=draft_ds.id,
        )
    assert exc_info.value.code == "DATASET_NOT_APPROVED"
    assert exc_info.value.status_code == 400


def test_service_freeze_dataset_fingerprint_mismatch_fails(seeded_val_env):
    """Directive 2: Dataset fingerprint mismatch fails closed with 400 FINGERPRINT_MISMATCH."""
    db = seeded_val_env["session"]
    proj1 = seeded_val_env["proj1"]
    user = seeded_val_env["user"]

    approved_ds = DerivedDataset(
        id="ds-approved-1",
        project_id=proj1.id,
        name="approved_dataset",
        dataset_type="cash_flows",
        version_number=1,
        status="approved",
        fingerprint="c" * 64,
        created_by="user-1",
        approved_by="user-2",
        approved_at=now_utc(),
    )
    db.add(approved_ds)
    db.commit()

    target_run = Run(id="run-val-target-4", project_id=proj1.id)

    with pytest.raises(ServiceError) as exc_info:
        freeze_valuation_run_package(
            db=db,
            project=proj1,
            run=target_run,
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
            derived_dataset_id=approved_ds.id,
            derived_dataset_fingerprint="f" * 64,
        )
    assert exc_info.value.code == "FINGERPRINT_MISMATCH"
    assert exc_info.value.status_code == 400


def test_service_freeze_reproducibility_identical_rerun(seeded_val_env):
    """Blocker 2: Identical calls to freeze_valuation_run_package produce identical package fingerprints from trusted PostgreSQL source."""
    db = seeded_val_env["session"]
    proj1 = seeded_val_env["proj1"]
    user = seeded_val_env["user"]

    src_run, src_pkg, src_art = _seed_source_projection_run(db, proj1.id)

    run1 = Run(id="run-rerun-1", project_id=proj1.id)
    run2 = Run(id="run-rerun-2", project_id=proj1.id)

    pkg1 = freeze_valuation_run_package(
        db=db,
        project=proj1,
        run=run1,
        user=user,
        basis="STATUTORY",
        methodology="VM22",
        methodology_version="v2024.1",
        valuation_date="2026-12-31",
        valuation_parameters={"spread_bps": 125},
        source_projection_run_id=src_run.id,
    )
    pkg2 = freeze_valuation_run_package(
        db=db,
        project=proj1,
        run=run2,
        user=user,
        basis="STATUTORY",
        methodology="VM22",
        methodology_version="v2024.1",
        valuation_date="2026-12-31",
        valuation_parameters={"spread_bps": 125},
        source_projection_run_id=src_run.id,
    )

    assert pkg1.fingerprint == pkg2.fingerprint
    assert pkg1.configuration["valuation"]["source_run_package_fingerprint"] == src_pkg.fingerprint
    assert pkg1.configuration["valuation"]["source_artifact_fingerprint"] == src_art.checksum_sha256


def test_service_submit_valuation_run_end_to_end(seeded_val_env):
    """Blocker 2: submit_valuation_run derives provenance from PostgreSQL source projection run and persists canonical DB records."""
    db = seeded_val_env["session"]
    proj1 = seeded_val_env["proj1"]
    user = seeded_val_env["user"]

    src_run, src_pkg, src_art = _seed_source_projection_run(db, proj1.id)

    payload = {
        "project_id": proj1.id,
        "name": "Production Valuation Run 2026",
        "basis": "STATUTORY",
        "methodology": "VM22",
        "methodology_version": "v2024.1",
        "valuation_date": "2026-12-31",
        "horizon_months": 120,
        "valuation_parameters": {"spread_bps": 125},
        "source_projection_run_id": src_run.id,
    }

    result = submit_valuation_run(db, payload, user)

    assert "run" in result
    run_info = result["run"]
    run_id = run_info["id"]
    assert run_info["basis"] == "STATUTORY"
    assert run_info["methodology"] == "VM22"
    assert run_info["status"] == "pending"

    persisted_run = db.get(Run, run_id)
    assert persisted_run is not None
    assert persisted_run.basis == "STATUTORY"
    assert persisted_run.methodology == "VM22"
    assert persisted_run.run_package_fingerprint == run_info["run_package_fingerprint"]
    assert persisted_run.model_version_id == "mv-spia-v1"

    persisted_pkg = db.get(RunPackage, run_info["run_package_id"])
    assert persisted_pkg is not None
    assert persisted_pkg.package["configuration"]["model"]["id"] == "model-spia-01"
    assert persisted_pkg.run_id == run_id
    assert persisted_pkg.fingerprint == run_info["run_package_fingerprint"]
    assert persisted_pkg.package["configuration"]["valuation"]["source_run_package_fingerprint"] == src_pkg.fingerprint
    assert persisted_pkg.package["configuration"]["valuation"]["source_artifact_fingerprint"] == src_art.checksum_sha256

    persisted_detail = db.get(ValuationRunDetail, run_id)
    assert persisted_detail is not None
    assert persisted_detail.run_id == run_id
    assert str(persisted_detail.valuation_date) == "2026-12-31"
    assert persisted_detail.basis == "STATUTORY"
    assert persisted_detail.methodology == "VM22"


def test_rejection_of_caller_supplied_base_package_or_fake_build_sha(seeded_val_env):
    """P0-03 & Blocker 2: Caller-supplied base_package_document is explicitly rejected with 400 UNTRUSTED_PROVENANCE_FORBIDDEN."""
    db = seeded_val_env["session"]
    proj1 = seeded_val_env["proj1"]
    user = seeded_val_env["user"]

    src_run, _, _ = _seed_source_projection_run(db, proj1.id)

    fake_caller_payload = {
        "project_id": proj1.id,
        "name": "Malicious Caller Submission",
        "basis": "STATUTORY",
        "methodology": "VM22",
        "methodology_version": "v2024.1",
        "valuation_date": "2026-12-31",
        "source_projection_run_id": src_run.id,
        "base_package_document": {
            "configuration": {
                "build": {
                    "build_fingerprint": "bad" * 21 + "b",
                    "source_commit": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                },
                "formulas": [{"id": "fake_formula"}],
            }
        },
    }

    with pytest.raises(ServiceError) as exc_info:
        submit_valuation_run(db, fake_caller_payload, user)
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "UNTRUSTED_PROVENANCE_FORBIDDEN"


def test_rejection_of_tampered_source_run_package(seeded_val_env):
    """Blocker 2: Tampering with source RunPackage causes SOURCE_PACKAGE_TAMPERED."""
    db = seeded_val_env["session"]
    proj1 = seeded_val_env["proj1"]
    user = seeded_val_env["user"]

    src_run, src_pkg, _ = _seed_source_projection_run(db, proj1.id)

    tampered_package = copy.deepcopy(src_pkg.package)
    tampered_package["configuration"]["parameters"]["tampered_key"] = "malicious"
    db.execute(
        sa.text("UPDATE run_packages SET package = :pkg WHERE id = :id"),
        {"pkg": json.dumps(tampered_package), "id": src_pkg.id},
    )
    db.commit()
    db.expire_all()

    target_run = Run(id="run-val-tamper-target", project_id=proj1.id)

    with pytest.raises(ServiceError) as exc_info:
        freeze_valuation_run_package(
            db=db,
            project=proj1,
            run=target_run,
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
            source_projection_run_id=src_run.id,
        )
    assert exc_info.value.code == "SOURCE_PACKAGE_TAMPERED"
    assert exc_info.value.status_code == 400


def test_rejection_of_incomplete_source_run_status(seeded_val_env):
    """Blocker 2: Source run with pending status fails with INVALID_SOURCE_RUN_STATUS."""
    db = seeded_val_env["session"]
    proj1 = seeded_val_env["proj1"]
    user = seeded_val_env["user"]

    src_run, _, _ = _seed_source_projection_run(db, proj1.id, run_id="run-pending-src", status="pending")

    target_run = Run(id="run-val-pending-target", project_id=proj1.id)

    with pytest.raises(ServiceError) as exc_info:
        freeze_valuation_run_package(
            db=db,
            project=proj1,
            run=target_run,
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
            source_projection_run_id=src_run.id,
        )
    assert exc_info.value.code == "INVALID_SOURCE_RUN_STATUS"
    assert exc_info.value.status_code == 400


def test_physical_artifact_existence_and_checksum_verification(seeded_val_env):
    """Blocker 2: Physical artifact file existence and byte SHA-256 matching are strictly enforced."""
    db = seeded_val_env["session"]
    proj1 = seeded_val_env["proj1"]
    user = seeded_val_env["user"]

    src_run, _, src_art = _seed_source_projection_run(db, proj1.id, run_id="run-art-check")
    store = get_artifact_store()
    path = store.path_for_uri(src_art.storage_uri)
    assert path.exists()

    original_bytes = path.read_bytes()
    path.write_bytes(original_bytes + b"corruption")

    target_run1 = Run(id="run-val-art-corrupt", project_id=proj1.id)
    with pytest.raises(ServiceError) as exc_info:
        freeze_valuation_run_package(
            db=db,
            project=proj1,
            run=target_run1,
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
            source_projection_run_id=src_run.id,
        )
    assert exc_info.value.code == "FINGERPRINT_MISMATCH"
    assert exc_info.value.status_code == 400

    path.unlink()
    target_run2 = Run(id="run-val-art-missing", project_id=proj1.id)
    with pytest.raises(ServiceError) as exc_info2:
        freeze_valuation_run_package(
            db=db,
            project=proj1,
            run=target_run2,
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
            source_projection_run_id=src_run.id,
        )
    assert exc_info2.value.code == "PHYSICAL_ARTIFACT_MISSING"
    assert exc_info2.value.status_code == 400


def test_distinctness_of_source_run_package_fingerprint_vs_source_artifact_fingerprint(seeded_val_env):
    """Blocker 2: source_run_package_fingerprint is NOT a source_artifact_fingerprint."""
    db = seeded_val_env["session"]
    proj1 = seeded_val_env["proj1"]
    user = seeded_val_env["user"]

    src_run, src_pkg, src_art = _seed_source_projection_run(db, proj1.id, run_id="run-distinct-fp")

    target_run = Run(id="run-val-distinct-fp", project_id=proj1.id)
    pkg = freeze_valuation_run_package(
        db=db,
        project=proj1,
        run=target_run,
        user=user,
        basis="STATUTORY",
        methodology="VM22",
        methodology_version="v2024.1",
        source_projection_run_id=src_run.id,
    )

    val_block = pkg.configuration["valuation"]
    src_pkg_fp = val_block["source_run_package_fingerprint"]
    src_art_fp = val_block["source_artifact_fingerprint"]

    assert src_pkg_fp == src_pkg.fingerprint
    assert src_art_fp == src_art.checksum_sha256
    assert src_pkg_fp != src_art_fp


def test_rejection_of_missing_model_identity_and_missing_source(seeded_val_env):
    """P0-03: Valuation run fails closed with 400 VALUATION_SOURCE_REQUIRED when no upstream source is provided."""
    db = seeded_val_env["session"]
    proj1 = seeded_val_env["proj1"]
    user = seeded_val_env["user"]

    run_no_source = Run(id="run-val-no-source", project_id=proj1.id)

    with pytest.raises(ServiceError) as exc_info:
        freeze_valuation_run_package(
            db=db,
            project=proj1,
            run=run_no_source,
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
        )
    assert exc_info.value.code == "VALUATION_SOURCE_REQUIRED"
    assert exc_info.value.status_code == 400


def test_p0_03_db_none_rejected(seeded_val_env):
    """P0-03: Calling freeze_valuation_run_package with db=None fails closed with 500 DATABASE_REQUIRED."""
    proj1 = seeded_val_env["proj1"]
    user = seeded_val_env["user"]
    run = Run(id="run-val-db-none", project_id=proj1.id)

    with pytest.raises(ServiceError) as exc_info:
        freeze_valuation_run_package(
            db=None,
            project=proj1,
            run=run,
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
            source_projection_run_id="run-src-proj-1",
        )
    assert exc_info.value.status_code == 500
    assert exc_info.value.code == "DATABASE_REQUIRED"


def test_p0_03_caller_invented_model_without_source_rejected(seeded_val_env):
    """P0-03: Caller cannot bypass source verification by passing invented model_id and model_version_id."""
    db = seeded_val_env["session"]
    proj1 = seeded_val_env["proj1"]
    user = seeded_val_env["user"]

    run = Run(id="run-val-invented-model", project_id=proj1.id)
    with pytest.raises(ServiceError) as exc_info:
        freeze_valuation_run_package(
            db=db,
            project=proj1,
            run=run,
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
            model_id="fake-caller-model-999",
            model_version_id="fake-caller-mv-999",
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "VALUATION_SOURCE_REQUIRED"

    with pytest.raises(ServiceError) as exc_sub:
        submit_valuation_run(
            db,
            {
                "project_id": proj1.id,
                "name": "Fake Caller Model Valuation",
                "basis": "STATUTORY",
                "methodology": "VM22",
                "methodology_version": "v2024.1",
                "model_id": "fake-caller-model-999",
                "model_version_id": "fake-caller-mv-999",
            },
            user,
        )
    assert exc_sub.value.status_code == 400
    assert exc_sub.value.code == "VALUATION_SOURCE_REQUIRED"


def test_p0_03_fabricated_foreign_unapproved_and_mismatched_model_selectors_rejected(seeded_val_env):
    """P0-03: Fabricated, foreign-project, unapproved, and lineage-conflicting model/model_version selectors fail closed."""
    db = seeded_val_env["session"]
    proj1 = seeded_val_env["proj1"]
    proj2 = seeded_val_env["proj2"]
    user = seeded_val_env["user"]

    src_run, _, _ = _seed_source_projection_run(db, proj1.id, run_id="run-src-p003")

    # 1. Fabricated model_id / model_version_id -> 400 MODEL_NOT_FOUND / MODEL_VERSION_NOT_FOUND
    with pytest.raises(ServiceError) as exc_fab_m:
        freeze_valuation_run_package(
            db=db,
            project=proj1,
            run=Run(id="r-fab-m", project_id=proj1.id),
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
            source_projection_run_id=src_run.id,
            model_id="nonexistent-model-xyz",
        )
    assert exc_fab_m.value.status_code == 400
    assert exc_fab_m.value.code == "MODEL_NOT_FOUND"

    with pytest.raises(ServiceError) as exc_fab_mv:
        submit_valuation_run(
            db,
            {
                "project_id": proj1.id,
                "name": "Fab MV",
                "basis": "STATUTORY",
                "methodology": "VM22",
                "methodology_version": "v2024.1",
                "source_projection_run_id": src_run.id,
                "model_version_id": "nonexistent-mv-xyz",
            },
            user,
        )
    assert exc_fab_mv.value.status_code == 400
    assert exc_fab_mv.value.code == "MODEL_VERSION_NOT_FOUND"

    # 2. Foreign-project model / model_version -> 404
    foreign_model, foreign_mv = _ensure_governed_spia_model(
        db, proj2.id, model_id="model-foreign-p2", mv_id="mv-foreign-p2", mv_status="approved"
    )
    db.commit()

    with pytest.raises(ServiceError) as exc_for_m:
        freeze_valuation_run_package(
            db=db,
            project=proj1,
            run=Run(id="r-for-m", project_id=proj1.id),
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
            source_projection_run_id=src_run.id,
            model_id=foreign_model.id,
        )
    assert exc_for_m.value.status_code == 404
    assert exc_for_m.value.code == "MODEL_NOT_FOUND"

    with pytest.raises(ServiceError) as exc_for_mv:
        submit_valuation_run(
            db,
            {
                "project_id": proj1.id,
                "name": "Foreign MV",
                "basis": "STATUTORY",
                "methodology": "VM22",
                "methodology_version": "v2024.1",
                "source_projection_run_id": src_run.id,
                "model_version_id": foreign_mv.id,
            },
            user,
        )
    assert exc_for_mv.value.status_code == 404
    assert exc_for_mv.value.code == "MODEL_VERSION_NOT_FOUND"

    # 3. Unapproved (draft) model_version in same project -> 400 MODEL_VERSION_NOT_APPROVED
    _, draft_mv = _ensure_governed_spia_model(
        db, proj1.id, model_id="model-spia-01", mv_id="mv-spia-draft-v2", mv_status="draft"
    )
    db.commit()

    with pytest.raises(ServiceError) as exc_draft_mv:
        freeze_valuation_run_package(
            db=db,
            project=proj1,
            run=Run(id="r-draft-mv", project_id=proj1.id),
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
            source_projection_run_id=src_run.id,
            model_version_id=draft_mv.id,
        )
    assert exc_draft_mv.value.status_code == 400
    assert exc_draft_mv.value.code == "MODEL_VERSION_NOT_APPROVED"

    # 4. Approved model / model_version in same project that conflicts with source run lineage -> 400 MISMATCH
    other_model, other_mv = _ensure_governed_spia_model(
        db, proj1.id, model_id="model-other-p1", mv_id="mv-other-p1", mv_status="approved"
    )
    db.commit()

    with pytest.raises(ServiceError) as exc_mismatch:
        submit_valuation_run(
            db,
            {
                "project_id": proj1.id,
                "name": "Lineage Mismatch Valuation",
                "basis": "STATUTORY",
                "methodology": "VM22",
                "methodology_version": "v2024.1",
                "source_projection_run_id": src_run.id,
                "model_id": other_model.id,
                "model_version_id": other_mv.id,
            },
            user,
        )
    assert exc_mismatch.value.status_code == 400
    assert exc_mismatch.value.code in ("MODEL_LINEAGE_MISMATCH", "MODEL_VERSION_LINEAGE_MISMATCH")


def test_v5_p0_c_derived_dataset_provenance_physical_and_model_verification(seeded_val_env):
    """V5-P0-C: DerivedDataset provenance requires four-eyes approval, physical Parquet verification,
    authoritative DB Model/ModelVersion rows (no 'M5'/'v1.0' fallbacks), and product family agreement."""
    db = seeded_val_env["session"]
    proj1 = seeded_val_env["proj1"]
    user = seeded_val_env["user"]

    src_run, _, src_art = _seed_source_projection_run(db, proj1.id, run_id="run-src-ds-v5")

    # 1. Self-approved DerivedDataset -> 400 SELF_APPROVED_DATASET_FORBIDDEN
    self_ds = DerivedDataset(
        id="ds-self-approved",
        project_id=proj1.id,
        source_run_id=src_run.id,
        source_artifact_id=src_art.id,
        source_model_id="model-spia-01",
        source_model_version_id="mv-spia-v1",
        name="self_approved_ds",
        dataset_type="projection_output",
        version_number=1,
        product_code="SPIA",
        status="approved",
        storage_uri=src_art.storage_uri,
        file_format="parquet",
        row_count=src_art.row_count,
        fingerprint=src_art.checksum_sha256,
        created_by="user-val-actuary",
        approved_by="user-val-actuary",
        approved_at=now_utc(),
    )
    db.add(self_ds)
    db.commit()

    with pytest.raises(ServiceError) as exc_self:
        freeze_valuation_run_package(
            db=db,
            project=proj1,
            run=Run(id="r-ds-self", project_id=proj1.id),
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
            derived_dataset_id=self_ds.id,
            valuation_date="2026-12-31",
        )
    assert exc_self.value.status_code == 400
    assert exc_self.value.code == "SELF_APPROVED_DATASET_FORBIDDEN"

    # 2. Approved DerivedDataset with missing source_artifact_id AND missing storage_uri -> 400 DATASET_ARTIFACT_REQUIRED
    no_art_ds = DerivedDataset(
        id="ds-no-art",
        project_id=proj1.id,
        source_run_id=src_run.id,
        source_artifact_id=None,
        source_model_id="model-spia-01",
        source_model_version_id="mv-spia-v1",
        name="no_art_ds",
        dataset_type="projection_output",
        version_number=1,
        product_code="SPIA",
        status="approved",
        storage_uri=None,
        file_format="parquet",
        row_count=1,
        fingerprint=src_art.checksum_sha256,
        created_by="user-val-actuary",
        approved_by="user-val-reviewer",
        approved_at=now_utc(),
    )
    db.add(no_art_ds)
    db.commit()

    with pytest.raises(ServiceError) as exc_no_art:
        freeze_valuation_run_package(
            db=db,
            project=proj1,
            run=Run(id="r-ds-no-art", project_id=proj1.id),
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
            derived_dataset_id=no_art_ds.id,
            valuation_date="2026-12-31",
        )
    assert exc_no_art.value.status_code == 400
    assert exc_no_art.value.code in ("DATASET_ARTIFACT_REQUIRED", "PHYSICAL_ARTIFACT_MISSING")

    # 3. Approved DerivedDataset referencing absent DB Model/ModelVersion -> 400 MODEL_NOT_FOUND (no 'M5'/'v1.0' fallback!)
    absent_model_ds = DerivedDataset(
        id="ds-absent-model",
        project_id=proj1.id,
        source_run_id=src_run.id,
        source_artifact_id=src_art.id,
        source_model_id="model-ghost-999",
        source_model_version_id="mv-ghost-999",
        name="absent_model_ds",
        dataset_type="projection_output",
        version_number=1,
        product_code="SPIA",
        status="approved",
        storage_uri=src_art.storage_uri,
        file_format="parquet",
        row_count=src_art.row_count,
        fingerprint=src_art.checksum_sha256,
        created_by="user-val-actuary",
        approved_by="user-val-reviewer",
        approved_at=now_utc(),
    )
    db.add(absent_model_ds)
    db.commit()

    with pytest.raises(ServiceError) as exc_ghost_m:
        submit_valuation_run(
            db,
            {
                "project_id": proj1.id,
                "name": "Ghost Model Dataset Valuation",
                "basis": "STATUTORY",
                "methodology": "VM22",
                "methodology_version": "v2024.1",
                "valuation_date": "2026-12-31",
                "derived_dataset_id": absent_model_ds.id,
            },
            user,
        )
    assert exc_ghost_m.value.status_code == 400
    assert exc_ghost_m.value.code == "MODEL_NOT_FOUND"

    # 4. Approved DerivedDataset with product_code mismatch vs Model.product_code -> 400 PRODUCT_MODEL_MISMATCH
    prod_mismatch_ds = DerivedDataset(
        id="ds-prod-mismatch",
        project_id=proj1.id,
        source_run_id=src_run.id,
        source_artifact_id=src_art.id,
        source_model_id="model-spia-01",
        source_model_version_id="mv-spia-v1",
        name="prod_mismatch_ds",
        dataset_type="projection_output",
        version_number=1,
        product_code="FIA",  # Model is SPIA!
        status="approved",
        storage_uri=src_art.storage_uri,
        file_format="parquet",
        row_count=src_art.row_count,
        fingerprint=src_art.checksum_sha256,
        created_by="user-val-actuary",
        approved_by="user-val-reviewer",
        approved_at=now_utc(),
    )
    db.add(prod_mismatch_ds)
    db.commit()

    with pytest.raises(ServiceError) as exc_prod_mm:
        freeze_valuation_run_package(
            db=db,
            project=proj1,
            run=Run(id="r-ds-prod-mm", project_id=proj1.id),
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
            derived_dataset_id=prod_mismatch_ds.id,
            valuation_date="2026-12-31",
        )
    assert exc_prod_mm.value.status_code == 400
    assert exc_prod_mm.value.code == "PRODUCT_MODEL_MISMATCH"

    # 5. Valid four-eyes approved DerivedDataset succeeds end-to-end through submit_valuation_run
    valid_ds = DerivedDataset(
        id="ds-valid-spia",
        project_id=proj1.id,
        source_run_id=src_run.id,
        source_artifact_id=src_art.id,
        source_model_id="model-spia-01",
        source_model_version_id="mv-spia-v1",
        name="valid_spia_ds",
        dataset_type="projection_output",
        version_number=1,
        product_code="SPIA",
        status="approved",
        storage_uri=src_art.storage_uri,
        file_format="parquet",
        row_count=src_art.row_count,
        fingerprint=src_art.checksum_sha256,
        created_by="user-val-actuary",
        approved_by="user-val-reviewer",
        approved_at=now_utc(),
    )
    db.add(valid_ds)
    db.commit()

    sub_res = submit_valuation_run(
        db,
        {
            "project_id": proj1.id,
            "name": "Governed DerivedDataset Valuation",
            "basis": "STATUTORY",
            "methodology": "VM22",
            "methodology_version": "v2024.1",
            "valuation_date": "2026-12-31",
            "derived_dataset_id": valid_ds.id,
        },
        user,
    )
    assert sub_res["run"]["derived_dataset_id"] == valid_ds.id
    val_pkg = db.get(RunPackage, sub_res["run"]["run_package_id"])
    assert val_pkg.package["configuration"]["model"]["id"] == "model-spia-01"
    assert val_pkg.package["configuration"]["model_version"]["id"] == "mv-spia-v1"
    assert val_pkg.package["configuration"]["valuation"]["derived_dataset_fingerprint"] == src_art.checksum_sha256
    assert val_pkg.package["configuration"]["valuation"]["source_artifact_fingerprint"] == src_art.checksum_sha256


def test_v5_p0_d_source_run_accepted_attempt_and_artifact_type_verification(seeded_val_env):
    """V5-P0-D: Valuation RunPackage freeze rejects missing accepted_attempt_number, stale attempt artifacts,
    and non-canonical artifact types."""
    db = seeded_val_env["session"]
    proj1 = seeded_val_env["proj1"]
    user = seeded_val_env["user"]

    src_run, _, src_art = _seed_source_projection_run(db, proj1.id, run_id="run-src-attempt-v5", accepted_attempt=2)

    # Create a canonical artifact for attempt 2 as well so we can test selecting attempt 1 vs attempt 2
    store = get_artifact_store()
    stored_att2 = store.write_rows(
        run_id=src_run.id,
        artifact_type="outputs",
        rows=[{"policy_id": "P1", "month": 1, "cash_flow": 200.0, "reserve": 5100.0}],
        attempt_number=2,
        schema_version="m2-v1",
    )
    art_att2 = RunArtifact(
        id="art-att2-canonical",
        run_id=src_run.id,
        attempt_number=2,
        artifact_type="outputs",
        storage_uri=stored_att2.uri,
        storage_backend="local",
        file_format="parquet",
        schema_version="m2-v1",
        row_count=stored_att2.row_count,
        checksum_sha256=stored_att2.checksum_sha256,
    )
    stored_log_att2 = store.write_rows(
        run_id=src_run.id,
        artifact_type="log",
        rows=[{"policy_id": "P1", "month": 1, "cash_flow": 200.0, "reserve": 5100.0}],
        attempt_number=2,
        schema_version="m2-v1",
    )
    art_log_att2 = RunArtifact(
        id="art-att2-log",
        run_id=src_run.id,
        attempt_number=2,
        artifact_type="log",
        storage_uri=stored_log_att2.uri,
        storage_backend="local",
        file_format="parquet",
        schema_version="m2-v1",
        row_count=stored_log_att2.row_count,
        checksum_sha256=stored_log_att2.checksum_sha256,
    )
    db.add_all([art_att2, art_log_att2])
    db.commit()

    # 1. Passing stale attempt 1 artifact ID when accepted_attempt_number == 2 -> 400 ARTIFACT_ATTEMPT_MISMATCH
    with pytest.raises(ServiceError) as exc_stale:
        freeze_valuation_run_package(
            db=db,
            project=proj1,
            run=Run(id="r-stale-att", project_id=proj1.id),
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
            source_projection_run_id=src_run.id,
            source_artifact_id=src_art.id,  # attempt 1!
            valuation_date="2026-12-31",
        )
    assert exc_stale.value.status_code == 400
    assert exc_stale.value.code == "ARTIFACT_ATTEMPT_MISMATCH"

    # 2. Passing non-canonical artifact_type='log' -> 400 INVALID_ARTIFACT_TYPE
    with pytest.raises(ServiceError) as exc_type:
        freeze_valuation_run_package(
            db=db,
            project=proj1,
            run=Run(id="r-bad-type", project_id=proj1.id),
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
            source_projection_run_id=src_run.id,
            source_artifact_id=art_log_att2.id,
            valuation_date="2026-12-31",
        )
    assert exc_type.value.status_code == 400
    assert exc_type.value.code == "INVALID_ARTIFACT_TYPE"

    # 3. Omitting source_artifact_id automatically selects the accepted attempt 2 canonical artifact (never attempt 1!)
    pkg_att2 = freeze_valuation_run_package(
        db=db,
        project=proj1,
        run=Run(id="r-auto-att2", project_id=proj1.id),
        user=user,
        basis="STATUTORY",
        methodology="VM22",
        methodology_version="v2024.1",
        source_projection_run_id=src_run.id,
        valuation_date="2026-12-31",
    )
    assert pkg_att2.configuration["valuation"]["source_artifact_id"] == art_att2.id
    assert pkg_att2.configuration["valuation"]["source_artifact_fingerprint"] == art_att2.checksum_sha256

    # 4. Source run with accepted_attempt_number = None -> 400 ACCEPTED_ATTEMPT_MISSING
    src_run.accepted_attempt_number = None
    db.commit()
    with pytest.raises(ServiceError) as exc_no_acc:
        freeze_valuation_run_package(
            db=db,
            project=proj1,
            run=Run(id="r-no-acc", project_id=proj1.id),
            user=user,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v2024.1",
            source_projection_run_id=src_run.id,
            valuation_date="2026-12-31",
        )
    assert exc_no_acc.value.status_code == 400
    assert exc_no_acc.value.code == "ACCEPTED_ATTEMPT_MISSING"
