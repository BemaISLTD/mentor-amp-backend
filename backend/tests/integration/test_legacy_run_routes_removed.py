"""The pre-package projection control plane must not be reachable in production."""

from fastapi.testclient import TestClient

from app.main import app


LEGACY_PREFIX = "/v1/legacy-runs"


def test_legacy_run_routes_are_absent_from_openapi_and_router():
    assert not any(
        getattr(route, "path", "").startswith(LEGACY_PREFIX) for route in app.routes
    )
    assert not any(path.startswith(LEGACY_PREFIX) for path in app.openapi()["paths"])


def test_legacy_execution_and_evidence_urls_return_not_found():
    with TestClient(app) as client:
        assert client.post(f"{LEGACY_PREFIX}/", json={}).status_code == 404
        for suffix in (
            "/run-id",
            "/run-id/results",
            "/run-id/summary",
            "/run-id/events",
            "/run-id/reports/reconciliation?baseline_run_id=other",
        ):
            assert client.get(f"{LEGACY_PREFIX}{suffix}").status_code == 404
