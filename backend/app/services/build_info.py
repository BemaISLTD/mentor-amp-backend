"""Build and runtime identity recorded in run packages and final manifests."""

import functools
import hashlib
import platform
import subprocess
from pathlib import Path

from app.config import settings
from app.core.projection_engine.engine import ENGINE_VERSION
from app.version import APP_VERSION

REPOSITORY = Path(__file__).resolve().parents[3]
IMAGE_SOURCE_COMMIT = Path("/source-commit")


@functools.lru_cache(maxsize=1)
def _source_digest() -> str:
    """Content identity remains accurate for dirty checkouts and bind-mounted development code."""
    backend = Path(__file__).resolve().parents[2]
    files = sorted(
        [*backend.joinpath("app").rglob("*.py"), *backend.joinpath("scripts").rglob("*.py")]
        + [backend / "requirements.txt", backend / "pyproject.toml"],
        key=lambda path: path.as_posix(),
    )
    digest = hashlib.sha256()
    for path in files:
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        digest.update(path.relative_to(backend).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


@functools.lru_cache(maxsize=1)
def _git_commit() -> str | None:
    """Best-effort commit SHA for local development (deployments set SOURCE_COMMIT)."""
    if settings.app_env not in ("local", "test", "development"):
        return None
    try:
        result = subprocess.run(
            ["git", "-c", "safe.directory=*", "rev-parse", "HEAD"],
            cwd=REPOSITORY, capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and len(sha) == 40 else None


def build_identity() -> dict[str, str | None]:
    """The code that froze or executed a run. Part of the hashed package configuration."""
    image_commit = IMAGE_SOURCE_COMMIT.read_text().strip() if IMAGE_SOURCE_COMMIT.is_file() else None
    return {
        "app_version": APP_VERSION,
        "engine_version": ENGINE_VERSION,
        "code_version": f"{settings.code_version}+sha256:{_source_digest()}",
        "source_commit": settings.source_commit or image_commit or _git_commit(),
    }


def runtime_environment() -> dict[str, str]:
    """Where an attempt ran (recorded in the final manifest; never hashed into the package)."""
    return {
        "app_env": settings.app_env,
        "python": platform.python_version(),
        "platform": platform.platform(terse=True),
    }
