"""Build and runtime identity recorded in run packages and final manifests."""

import functools
import platform
import subprocess
from pathlib import Path

from app.config import settings
from app.core.projection_engine.engine import ENGINE_VERSION
from app.version import APP_VERSION

REPOSITORY = Path(__file__).resolve().parents[3]


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
    return {
        "app_version": APP_VERSION,
        "engine_version": ENGINE_VERSION,
        "code_version": settings.code_version,
        "source_commit": settings.source_commit or _git_commit(),
    }


def runtime_environment() -> dict[str, str]:
    """Where an attempt ran (recorded in the final manifest; never hashed into the package)."""
    return {
        "app_env": settings.app_env,
        "python": platform.python_version(),
        "platform": platform.platform(terse=True),
    }
