"""Tests for M5 Valuation RunPackage provenance and reproducibility (Directives 3 & 16).

Verifies:
1. Pure reproducibility: Identical valuation inputs produce identical package fingerprints bit-for-bit.
2. Material sensitivity: Mutating ANY valuation provenance field alters the package fingerprint.
3. Envelope integrity: verify_envelope detects mismatches between run row and frozen valuation package.
4. Concrete governed valuation run package artifact verification.
"""

import copy
import pytest

from app.core.execution import run_package
from app.core.execution.fingerprints import fingerprint
from app.core.execution.run_package import RunPackageError, verify, verify_envelope


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
    
    # Fingerprint covers the frozen configuration deterministically
    assert pkg1["fingerprint"] == pkg2["fingerprint"]
    assert pkg1["fingerprint"] == fingerprint(SAMPLE_VALUATION_CONFIGURATION)
    
    # Verification succeeds against expected fingerprint
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
    
    # 1. Matching run row -> succeeds
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

    # 2. Contradictory basis in run row -> raises RUN_INDEX_MISMATCH
    mismatched_run_basis = copy.deepcopy(matching_run)
    mismatched_run_basis["basis"] = "GAAP"
    with pytest.raises(RunPackageError) as exc_info:
        verify_envelope(pkg, package_row, mismatched_run_basis)
    assert exc_info.value.code == "RUN_INDEX_MISMATCH"
    assert "basis" in exc_info.value.message

    # 3. Contradictory methodology in run row -> raises RUN_INDEX_MISMATCH
    mismatched_run_meth = copy.deepcopy(matching_run)
    mismatched_run_meth["methodology"] = "VM21"
    with pytest.raises(RunPackageError) as exc_info:
        verify_envelope(pkg, package_row, mismatched_run_meth)
    assert exc_info.value.code == "RUN_INDEX_MISMATCH"
    assert "methodology" in exc_info.value.message


def test_tampered_package_fails_verification():
    """Directive 3: Tampering with package configuration raises PACKAGE_TAMPERED."""
    pkg = seal_valuation_package()
    # Maliciously modify the discount curve fingerprint without updating top-level fingerprint
    pkg["configuration"]["valuation"]["discount_curve_fingerprint"] = "TAMPERED_CURVE_HASH"
    
    with pytest.raises(RunPackageError) as exc_info:
        verify(pkg, expected_fingerprint=pkg["fingerprint"])
    assert exc_info.value.code == "PACKAGE_TAMPERED"


# =============================================================================
# Production Service Integration Tests (Directive 2 & Senior Review Blocker 2)
# =============================================================================

from datetime import date
import hashlib
import json
from unittest.mock import MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db.database import Base
from app.db.models.project import Project
from app.db.models.project_member import ProjectMember
from app.db.models.user import User
from app.db.models.run import Run
from app.db.models.run_package import RunPackage
from app.db.models.valuation_detail import ValuationRunDetail
from app.db.models.governance import DerivedDataset
from app.db.models.run_artifact import RunArtifact
from app.services.common import ServiceError, iso, now_utc
from app.services.build_info import BuildIdentityUnavailable, build_identity
from app.services.run_package_service import freeze_valuation_run_package
from app.services.run_submission_service import submit_valuation_run
from app.core.artifacts import get_artifact_store
import app.core.execution.run_package as core_run_pkg


@pytest.fixture()
def db_session_val():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def seeded_val_env(db_session_val):
    proj1 = Project(id="proj-val-1", name="Valuation Project 1")
    proj2 = Project(id="proj-val-2", name="Valuation Project 2")
    user = User(id="user-val-actuary", email="val@mentoramp.com", full_name="Val Actuary", password_hash="pwd")
    db_session_val.add_all([proj1, proj2, user])
    db_session_val.commit()
    mem1 = ProjectMember(project_id=proj1.id, user_id=user.id, role="owner")
    mem2 = ProjectMember(project_id=proj2.id, user_id=user.id, role="owner")
    db_session_val.add_all([mem1, mem2])
    db_session_val.commit()
    return {"session": db_session_val, "proj1": proj1, "proj2": proj2, "user": user}


def _seed_source_projection_run(db, project_id, run_id="run-src-proj-1", status="success"):
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

    # 1. BuildIdentityUnavailable -> ServiceError(503, BUILD_IDENTITY_UNAVAILABLE)
    def mock_unavailable():
        raise BuildIdentityUnavailable("No build identity available")
    monkeypatch.setattr(rps, "build_identity", mock_unavailable)

    with pytest.raises(ServiceError) as exc_info:
        freeze_valuation_run_package(
            db=db, project=proj, run=run, user=user,
            basis="STATUTORY", methodology="VM22", methodology_version="v2024.1",
            source_projection_run_id=src_run.id,
        )
    assert exc_info.value.code == "BUILD_IDENTITY_UNAVAILABLE"
    assert exc_info.value.status_code == 503

    # 2. Corrupt/fallback zero identity -> ServiceError(503, BUILD_IDENTITY_UNAVAILABLE)
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
            db=db, project=proj, run=run, user=user,
            basis="STATUTORY", methodology="VM22", methodology_version="v2024.1",
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
            db=db, project=proj1, run=target_run, user=user,
            basis="STATUTORY", methodology="VM22", methodology_version="v2024.1",
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
    )
    db.add(foreign_ds)
    db.commit()

    target_run = Run(id="run-val-target-2", project_id=proj1.id)

    with pytest.raises(ServiceError) as exc_info:
        freeze_valuation_run_package(
            db=db, project=proj1, run=target_run, user=user,
            basis="STATUTORY", methodology="VM22", methodology_version="v2024.1",
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
            db=db, project=proj1, run=target_run, user=user,
            basis="STATUTORY", methodology="VM22", methodology_version="v2024.1",
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
    )
    db.add(approved_ds)
    db.commit()

    target_run = Run(id="run-val-target-4", project_id=proj1.id)

    with pytest.raises(ServiceError) as exc_info:
        freeze_valuation_run_package(
            db=db, project=proj1, run=target_run, user=user,
            basis="STATUTORY", methodology="VM22", methodology_version="v2024.1",
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
        db=db, project=proj1, run=run1, user=user,
        basis="STATUTORY", methodology="VM22", methodology_version="v2024.1",
        valuation_date="2026-12-31",
        valuation_parameters={"spread_bps": 125},
        source_projection_run_id=src_run.id,
    )
    pkg2 = freeze_valuation_run_package(
        db=db, project=proj1, run=run2, user=user,
        basis="STATUTORY", methodology="VM22", methodology_version="v2024.1",
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

    # Verify canonical database records
    persisted_run = db.get(Run, run_id)
    assert persisted_run is not None
    assert persisted_run.basis == "STATUTORY"
    assert persisted_run.methodology == "VM22"
    assert persisted_run.run_package_fingerprint == run_info["run_package_fingerprint"]
    assert persisted_run.model_version_id == "mv-spia-v1"

    persisted_pkg = db.get(RunPackage, run_info["run_package_id"])
    assert persisted_pkg is not None
    assert persisted_pkg.package["configuration"]["model"]["id"] == "model-spia-01"
    assert persisted_pkg is not None
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
    """Blocker 2: Caller-supplied base_package_document and fake build identity are rejected; build identity is always from build_identity()."""
    db = seeded_val_env["session"]
    proj1 = seeded_val_env["proj1"]
    user = seeded_val_env["user"]

    src_run, _, _ = _seed_source_projection_run(db, proj1.id)

    # Caller attempts to submit fake build SHA and fake formulas via base_package_document
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

    result = submit_valuation_run(db, fake_caller_payload, user)
    val_pkg = db.get(RunPackage, result["run"]["run_package_id"])

    # Must NOT contain fake caller build info or fake formulas
    pkg_build = val_pkg.package["configuration"]["build"]
    trusted_build = build_identity()
    assert pkg_build["build_fingerprint"] == trusted_build["build_fingerprint"]
    assert pkg_build["source_commit"] == trusted_build["source_commit"]
    assert val_pkg.package["configuration"]["formulas"] != [{"id": "fake_formula"}]


def test_rejection_of_tampered_source_run_package(seeded_val_env):
    """Blocker 2: Tampering with source RunPackage causes SOURCE_PACKAGE_TAMPERED."""
    db = seeded_val_env["session"]
    proj1 = seeded_val_env["proj1"]
    user = seeded_val_env["user"]

    src_run, src_pkg, _ = _seed_source_projection_run(db, proj1.id)

    # Tamper with the package configuration bypassing ORM immutability via raw SQL
    import sqlalchemy as sa
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
            db=db, project=proj1, run=target_run, user=user,
            basis="STATUTORY", methodology="VM22", methodology_version="v2024.1",
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
            db=db, project=proj1, run=target_run, user=user,
            basis="STATUTORY", methodology="VM22", methodology_version="v2024.1",
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

    # 1. Tamper with file content -> FINGERPRINT_MISMATCH
    original_bytes = path.read_bytes()
    path.write_bytes(original_bytes + b"corruption")

    target_run1 = Run(id="run-val-art-corrupt", project_id=proj1.id)
    with pytest.raises(ServiceError) as exc_info:
        freeze_valuation_run_package(
            db=db, project=proj1, run=target_run1, user=user,
            basis="STATUTORY", methodology="VM22", methodology_version="v2024.1",
            source_projection_run_id=src_run.id,
        )
    assert exc_info.value.code == "FINGERPRINT_MISMATCH"
    assert exc_info.value.status_code == 400

    # 2. Delete file -> PHYSICAL_ARTIFACT_MISSING
    path.unlink()
    target_run2 = Run(id="run-val-art-missing", project_id=proj1.id)
    with pytest.raises(ServiceError) as exc_info2:
        freeze_valuation_run_package(
            db=db, project=proj1, run=target_run2, user=user,
            basis="STATUTORY", methodology="VM22", methodology_version="v2024.1",
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
        db=db, project=proj1, run=target_run, user=user,
        basis="STATUTORY", methodology="VM22", methodology_version="v2024.1",
        source_projection_run_id=src_run.id,
    )

    val_block = pkg.configuration["valuation"]
    src_pkg_fp = val_block["source_run_package_fingerprint"]
    src_art_fp = val_block["source_artifact_fingerprint"]

    assert src_pkg_fp == src_pkg.fingerprint
    assert src_art_fp == src_art.checksum_sha256
    # The package configuration hash is fundamentally distinct from the Parquet byte checksum
    assert src_pkg_fp != src_art_fp


def test_rejection_of_missing_model_identity(seeded_val_env):
    """Blocker 2: Zero fake defaults: valuation run fails closed with 400 MODEL_IDENTITY_REQUIRED when model identity cannot be established."""
    db = seeded_val_env["session"]
    proj1 = seeded_val_env["proj1"]
    user = seeded_val_env["user"]

    # Run without source run, without derived dataset, and without model_id
    run_no_model = Run(id="run-val-no-model", project_id=proj1.id)

    with pytest.raises(ServiceError) as exc_info:
        freeze_valuation_run_package(
            db=db, project=proj1, run=run_no_model, user=user,
            basis="STATUTORY", methodology="VM22", methodology_version="v2024.1",
        )
    assert exc_info.value.code == "MODEL_IDENTITY_REQUIRED"
    assert exc_info.value.status_code == 400
