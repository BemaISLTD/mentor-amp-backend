"""Build and runtime identity recorded in run packages and final manifests.

The build identity answers "which code produced this number?". Its executable part is the
``build_fingerprint``; a run package freezes it and a worker refuses to execute a package frozen
under a different build.

- local / test / development: computed from the source actually loaded — a SHA-256 over every
  ``.py`` file of the ``app`` package (path + content hash), so uncommitted edits change it. The
  git commit and a dirty flag are recorded for humans (git is optional; the fingerprint is not).
- staging / production: injected by the build, never read from git: ``SOURCE_COMMIT`` and
  ``BUILD_FINGERPRINT`` are required (``BuildIdentityUnavailable`` otherwise; the settings also
  refuse to start without them).

A ``BUILD_FINGERPRINT`` set explicitly (e.g. by CI) is used in every environment.
"""

import functools
import hashlib
import platform
import subprocess
from pathlib import Path
from typing import Any

from app.config import PROTECTED_ENVIRONMENTS, settings
from app.core.execution.fingerprints import fingerprint
from app.core.projection_engine.engine import ENGINE_VERSION
from app.version import APP_VERSION

BACKEND = Path(__file__).resolve().parents[2]
REPOSITORY = BACKEND.parent
SOURCE_PACKAGE = "app"


class BuildIdentityUnavailable(Exception):
    """The immutable build identity required in this environment is missing."""


def source_tree_fingerprint(root: Path, package: str = SOURCE_PACKAGE) -> str:
    """SHA-256 over (relative path, content hash) of every ``.py`` file under ``root/package``."""
    base = root / package
    digest = hashlib.sha256()
    for path in sorted(base.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii") + b"\n")
    return digest.hexdigest()


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-c", "safe.directory=*", *args],
            cwd=root, capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def git_state(root: Path, package: str = SOURCE_PACKAGE) -> dict[str, Any]:
    """Commit SHA and whether the package has uncommitted (or untracked) changes, if git works."""
    head = _git(root, "rev-parse", "HEAD")
    status = _git(root, "status", "--porcelain", "--untracked-files=all", "--", package)
    return {
        "source_commit": head.strip() if head else None,
        "source_dirty": None if status is None else bool(status.strip()),
    }


def source_identity(root: Path, package: str = SOURCE_PACKAGE) -> dict[str, Any]:
    """The computed (development) identity of the code under ``root/package``."""
    tree = source_tree_fingerprint(root, package)
    state = git_state(root, package)
    return {
        "source_tree_fingerprint": tree,
        "source_commit": state["source_commit"],
        "source_dirty": state["source_dirty"],
    }


@functools.lru_cache(maxsize=1)
def build_identity() -> dict[str, Any]:
    """The identity of the code this process runs (computed once, when first needed)."""
    base = {
        "app_version": APP_VERSION,
        "engine_version": ENGINE_VERSION,
        "code_version": settings.code_version,
    }
    if settings.build_fingerprint:
        return {
            **base,
            "build_fingerprint": settings.build_fingerprint,
            "source_commit": settings.source_commit,
            "source_dirty": None,
            "identity_source": "injected",
        }
    if settings.app_env in PROTECTED_ENVIRONMENTS:
        raise BuildIdentityUnavailable(
            f"APP_ENV={settings.app_env} requires an injected BUILD_FINGERPRINT and SOURCE_COMMIT."
        )
    source = source_identity(BACKEND)
    return {
        **base,
        # The executable identity: the loaded source plus the versions it declares.
        "build_fingerprint": fingerprint({
            "source_tree": source["source_tree_fingerprint"],
            "app_version": APP_VERSION,
            "engine_version": ENGINE_VERSION,
        }),
        "source_commit": settings.source_commit or source["source_commit"],
        "source_dirty": source["source_dirty"],
        "identity_source": "computed",
    }


# Fields that identify executable semantics; a package frozen under different values is refused.
EXECUTABLE_BUILD_FIELDS = ("engine_version", "build_fingerprint")


def build_mismatches(frozen: dict[str, Any], current: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        field: {"expected": frozen.get(field), "actual": current.get(field)}
        for field in EXECUTABLE_BUILD_FIELDS
        if frozen.get(field) != current.get(field)
    }


def runtime_environment() -> dict[str, str]:
    """Where an attempt ran (recorded in the final manifest; never hashed into the package)."""
    return {
        "app_env": settings.app_env,
        "python": platform.python_version(),
        "platform": platform.platform(terse=True),
    }
