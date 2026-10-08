"""WP1 correction P0 #1 — executable formula identity, registration safety, build identity."""

import shutil
import subprocess
from pathlib import Path

import pytest

from app.core.formula_engine import formulas
from app.core.formula_engine.formulas import (
    FORMULA_REGISTRY,
    FormulaRegistrationConflict,
    implementation_fingerprint,
    register_function,
)
from app.services import build_info


def payment(monthly_payment, survival_probability):
    return monthly_payment * survival_probability


def payment_changed(monthly_payment, survival_probability):
    return monthly_payment * survival_probability * 1.0000001


def make_scaled(scale):
    def scaled(value):
        return value * scale
    return scaled


@pytest.fixture()
def registry_keys():
    keys: list[str] = []
    yield keys
    for key in keys:
        FORMULA_REGISTRY.pop(key, None)


def test_identical_registration_is_idempotent(registry_keys):
    registry_keys.append("test.wp1c.payment")
    first = register_function("test.wp1c.payment", payment, implementation_version="t1")
    second = register_function("test.wp1c.payment", payment, implementation_version="t1")
    assert second is first
    assert FORMULA_REGISTRY["test.wp1c.payment"].identity() == first.identity()


def test_a_different_implementation_under_the_same_key_is_refused(registry_keys):
    registry_keys.append("test.wp1c.payment")
    register_function("test.wp1c.payment", payment)
    with pytest.raises(FormulaRegistrationConflict):
        register_function("test.wp1c.payment", payment_changed)
    with pytest.raises(FormulaRegistrationConflict):  # same code, different declared version
        register_function("test.wp1c.payment", payment, implementation_version="t2")
    assert FORMULA_REGISTRY["test.wp1c.payment"].func is payment  # never silently replaced


def test_implementation_fingerprint_tracks_executable_code():
    assert implementation_fingerprint(payment) == implementation_fingerprint(payment)
    assert implementation_fingerprint(payment) != implementation_fingerprint(payment_changed)
    # Closure values are part of the executable identity.
    assert implementation_fingerprint(make_scaled(2.0)) != implementation_fingerprint(make_scaled(3.0))
    assert implementation_fingerprint(make_scaled(2.0)) == implementation_fingerprint(make_scaled(2.0))
    with pytest.raises(TypeError):
        implementation_fingerprint(len)  # only plain Python functions are allowed


def test_formula_functions_is_a_read_only_view():
    with pytest.raises(TypeError):
        formulas.FORMULA_FUNCTIONS["anything"] = payment  # type: ignore[index]


# --- build identity -------------------------------------------------------------------------

def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "safe.directory=*", *args], cwd=root, check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required for this test")
def test_dirty_working_tree_cannot_masquerade_as_clean_head(tmp_path):
    (tmp_path / "app").mkdir()
    module = tmp_path / "app" / "formulas.py"
    module.write_text("def f(x):\n    return x * 2\n", encoding="utf-8")
    git(tmp_path, "init", "-q")
    git(tmp_path, "-c", "user.email=t@example.com", "-c", "user.name=t", "add", "-A")
    git(tmp_path, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "clean")

    clean = build_info.source_identity(tmp_path)
    assert clean["source_dirty"] is False and len(clean["source_commit"]) == 40

    module.write_text("def f(x):\n    return x * 3\n", encoding="utf-8")  # uncommitted edit
    dirty = build_info.source_identity(tmp_path)
    assert dirty["source_dirty"] is True
    assert dirty["source_commit"] == clean["source_commit"]  # HEAD alone cannot tell them apart...
    assert dirty["source_tree_fingerprint"] != clean["source_tree_fingerprint"]  # ...the identity does

    module.write_text("def f(x):\n    return x * 2\n", encoding="utf-8")  # back to HEAD
    assert build_info.source_identity(tmp_path) == clean

    (tmp_path / "app" / "extra.py").write_text("X = 1\n", encoding="utf-8")  # untracked source
    untracked = build_info.source_identity(tmp_path)
    assert untracked["source_dirty"] is True
    assert untracked["source_tree_fingerprint"] != clean["source_tree_fingerprint"]


def test_source_fingerprint_does_not_need_git(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "a.py").write_text("A = 1\n", encoding="utf-8")
    identity = build_info.source_identity(tmp_path)  # not a git repository
    assert identity["source_commit"] is None and identity["source_dirty"] is None
    assert len(identity["source_tree_fingerprint"]) == 64


def test_protected_environments_never_compute_the_build_identity(monkeypatch):
    monkeypatch.setattr(build_info.settings, "app_env", "production")
    monkeypatch.setattr(build_info.settings, "build_fingerprint", None)
    build_info.build_identity.cache_clear()
    try:
        with pytest.raises(build_info.BuildIdentityUnavailable):
            build_info.build_identity()
        monkeypatch.setattr(build_info.settings, "build_fingerprint", "f" * 64)
        monkeypatch.setattr(build_info.settings, "source_commit", "c" * 40)
        build_info.build_identity.cache_clear()
        identity = build_info.build_identity()
        assert identity["build_fingerprint"] == "f" * 64 and identity["identity_source"] == "injected"
    finally:
        build_info.build_identity.cache_clear()


def test_executable_build_fields_are_compared():
    frozen = {"engine_version": "e1", "build_fingerprint": "b1", "code_version": "x"}
    assert build_info.build_mismatches(frozen, {**frozen, "code_version": "y"}) == {}
    assert set(build_info.build_mismatches(frozen, {**frozen, "build_fingerprint": "b2"})) == {"build_fingerprint"}
