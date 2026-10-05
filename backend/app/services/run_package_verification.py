"""Everything a worker checks about a run package before calculating (datasets aside).

Order (each failure stops the run before any calculation, with a stable error code):

1. ``PACKAGE_MISSING``             the run has no package;
2. ``PACKAGE_IDENTITY_MISMATCH``   package row, package document and run disagree on identity;
   ``RUN_INDEX_MISMATCH``          the run's index fields disagree with the frozen configuration;
3. ``PACKAGE_SCHEMA_UNSUPPORTED`` / ``PACKAGE_TAMPERED``   schema or configuration fingerprint;
4. ``BUILD_IDENTITY_UNAVAILABLE`` / ``BUILD_IDENTITY_MISMATCH``   this worker's build is not the
   build the package was frozen under (engine version or build fingerprint differ);
5. ``FUNCTION_NOT_REGISTERED`` / ``FORMULA_IMPLEMENTATION_MISMATCH``   the callable registered
   under a frozen ``function_ref`` is missing or is not the frozen implementation (its code
   fingerprint is recomputed now, never read from a cache).

Returns the verified configuration and the exact callables to execute (by ``function_ref``).
Dataset fingerprints are verified next by ``dataset_loader``.
"""

from collections.abc import Callable
from typing import Any

from app.core.execution import run_package
from app.core.execution.run_package import RunPackageError
from app.core.formula_engine.formulas import FORMULA_REGISTRY, implementation_fingerprint
from app.db.models.run import Run
from app.db.models.run_package import RunPackage
from app.services import build_info
from app.services.common import iso


def _package_row(package: RunPackage) -> dict[str, Any]:
    return {
        "id": package.id,
        "run_id": package.run_id,
        "project_id": package.project_id,
        "fingerprint": package.fingerprint,
        "schema_version": package.schema_version,
        "fingerprint_algorithm": package.fingerprint_algorithm,
    }


def _run_row(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "project_id": run.project_id,
        "run_set_id": run.run_set_id,
        "run_package_fingerprint": run.run_package_fingerprint,
        "scenario_id": run.scenario_id,
        "model_version_id": run.model_version_id,
        "projection_set_id": run.projection_set_id,
        "horizon_months": run.horizon_months,
        "valuation_date": iso(run.valuation_date),
    }


def current_build_identity() -> dict[str, Any]:
    """This worker's build identity (a seam for tests that simulate a different build)."""
    return build_info.build_identity()


def verify_build(configuration: dict[str, Any]) -> dict[str, Any]:
    try:
        current = current_build_identity()
    except build_info.BuildIdentityUnavailable as error:
        raise RunPackageError("BUILD_IDENTITY_UNAVAILABLE", str(error)) from error
    mismatches = build_info.build_mismatches(configuration.get("build") or {}, current)
    if mismatches:
        raise RunPackageError(
            "BUILD_IDENTITY_MISMATCH",
            "The run was frozen under a different build than this worker runs "
            f"({', '.join(sorted(mismatches))} differ); results would not be reproducible.",
            {"mismatched": mismatches},
        )
    return current


def verify_implementations(configuration: dict[str, Any]) -> dict[str, Callable[..., Any]]:
    functions: dict[str, Callable[..., Any]] = {}
    missing: list[str] = []
    mismatched: list[dict[str, Any]] = []
    for entry in configuration.get("formulas") or []:
        ref = entry["function_ref"]
        frozen = entry.get("implementation") or {}
        registration = FORMULA_REGISTRY.get(ref)
        if registration is None:
            missing.append(ref)
            continue
        actual = {
            "module": registration.func.__module__,
            "qualname": registration.func.__qualname__,
            "implementation_version": registration.implementation_version,
            "implementation_fingerprint": implementation_fingerprint(registration.func),
        }
        if actual != frozen:
            mismatched.append({"function_ref": ref, "expected": frozen, "actual": actual})
            continue
        functions[ref] = registration.func
    if missing:
        raise RunPackageError(
            "FUNCTION_NOT_REGISTERED",
            f"Formula functions not available on this worker: {sorted(missing)}.",
            {"function_refs": sorted(missing)},
        )
    if mismatched:
        raise RunPackageError(
            "FORMULA_IMPLEMENTATION_MISMATCH",
            "The code registered for "
            f"{', '.join(item['function_ref'] for item in mismatched)} is not the implementation "
            "the run was frozen with.",
            {"mismatched": mismatched},
        )
    return functions


def verify_for_execution(
    run: Run, package: RunPackage | None
) -> tuple[dict[str, Any], dict[str, Callable[..., Any]], dict[str, Any]]:
    """Return (configuration, verified callables, this worker's build identity) or raise."""
    if package is None or not run.run_package_fingerprint:
        raise RunPackageError(
            "PACKAGE_MISSING", "The run has no frozen run package, so it cannot execute reproducibly.",
        )
    document = package.package
    run_package.verify_envelope(document, _package_row(package), _run_row(run))
    configuration = run_package.verify(document, run.run_package_fingerprint)
    build = verify_build(configuration)
    functions = verify_implementations(configuration)
    return configuration, functions, build
