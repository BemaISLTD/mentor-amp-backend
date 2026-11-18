"""Capture reproducible CPU-only baseline reference runs for Phase 6 M6-00.

Runs the governed MYGA 25-policy x 480-month Base + Rates Up 150bp + replay workflow
through the real API and pure-CPU engine inside an isolated ephemeral Env(), printing:
- RunPackage ID & immutable package_fingerprint
- RunManifest fingerprint & schema_version
- RunArtifact row counts, byte sizes, and SHA-256 checksums
- Headline reserve, total undiscounted liability_outgo, and trace spot check
- Reconciliation parity against frozen MYGA golden oracle values
- Deterministic byte-for-byte Parquet replay proof
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402
from app.core.artifacts import IDENTITY_NEUTRAL_SCHEMA  # noqa: E402
from app.db.models.run import Run  # noqa: E402
from app.db.models.run_artifact import RunArtifact, RunManifest  # noqa: E402
from app.db.models.run_package import RunPackage  # noqa: E402
from app.products.myga_lite.golden import GOLDEN_RESULTS  # noqa: E402
from tests.integration.test_myga_end_to_end import (  # noqa: E402
    _assert_myga_25_policy_api_run_trace_and_parquet,
    _seed_myga,
)
from tests.integration.wp1_support import Env  # noqa: E402


def main() -> None:
    settings.auth_mode = "jwt"
    settings.app_env = "test"
    settings.debug = True
    started_total = time.perf_counter()
    env = Env()
    try:
        _seed_myga(env)
        started_exec = time.perf_counter()
        result = _assert_myga_25_policy_api_run_trace_and_parquet(env)
        exec_elapsed_s = time.perf_counter() - started_exec

        runs_report = []
        with env.Session() as db:
            for item in result["runs"]:
                run_id = item["id"]
                scenario_name = item["scenario"]["name"]
                run = db.get(Run, run_id)
                pkg = db.query(RunPackage).filter_by(run_id=run_id).one_or_none()
                manifest = db.query(RunManifest).filter_by(run_id=run_id).one()
                artifacts = sorted(
                    db.query(RunArtifact).filter_by(run_id=run_id, attempt_number=1).all(),
                    key=lambda row: (row.artifact_type, row.storage_uri),
                )
                artifact_rows = []
                for art in artifacts:
                    raw = env.artifact_store.read_bytes(art.storage_uri)
                    assert art.schema_version == IDENTITY_NEUTRAL_SCHEMA
                    artifact_rows.append({
                        "artifact_type": art.artifact_type,
                        "schema_version": art.schema_version,
                        "row_count": art.row_count,
                        "byte_size": len(raw),
                        "checksum_sha256": art.checksum_sha256,
                    })

                summary = result["summaries"][scenario_name]
                expected_reserve = (
                    GOLDEN_RESULTS["base_reserve"]
                    if scenario_name == "Base"
                    else GOLDEN_RESULTS["rates_up_reserve"]
                )
                expected_outgo = (
                    GOLDEN_RESULTS["base_undiscounted_outgo"]
                    if scenario_name == "Base"
                    else GOLDEN_RESULTS["rates_up_undiscounted_outgo"]
                )
                headline_val = summary["headline"]["value"]
                outgo_val = summary["totals"]["liability_outgo"]["value"]
                assert math.isclose(headline_val, expected_reserve, abs_tol=1e-6)
                assert math.isclose(outgo_val, expected_outgo, abs_tol=1e-6)

                runs_report.append({
                    "run_id": run_id,
                    "scenario": scenario_name,
                    "status": summary["status"],
                    "policy_count": summary.get("policy_count"),
                    "failed_policy_count": summary["failed_policy_count"],
                    "run_package_id": pkg.id if pkg else None,
                    "run_package_schema_version": pkg.schema_version if pkg else None,
                    "run_package_fingerprint": pkg.fingerprint if pkg else None,
                    "run_manifest_fingerprint": manifest.fingerprint,
                    "run_manifest_schema_version": manifest.schema_version,
                    "headline_reserve": headline_val,
                    "expected_reserve_oracle": expected_reserve,
                    "reserve_abs_diff": abs(headline_val - expected_reserve),
                    "total_liability_outgo": outgo_val,
                    "expected_outgo_oracle": expected_outgo,
                    "outgo_abs_diff": abs(outgo_val - expected_outgo),
                    "artifacts": artifact_rows,
                })

        trace_root = result["trace"]["root"]
        expected_trace = GOLDEN_RESULTS["base_policy_0001_month_30_surrender_benefit"]
        assert math.isclose(trace_root["value"], expected_trace, abs_tol=1e-6)

        payload = {
            "gate": "M6-00 CPU Reference Baseline",
            "execution_backend": "cpu",
            "product": "MYGA_LITE (25 policies x 480 months)",
            "elapsed_execution_plus_replay_seconds": round(exec_elapsed_s, 3),
            "elapsed_total_seconds": round(time.perf_counter() - started_total, 3),
            "trace_spot_check": {
                "policy_id": "MYGA-0001",
                "month": 30,
                "variable": trace_root["variable"],
                "value": trace_root["value"],
                "expected_oracle": expected_trace,
                "abs_diff": abs(trace_root["value"] - expected_trace),
            },
            "deterministic_parquet_replay": "PASS",
            "reconciliation": "PASS",
            "runs": runs_report,
        }
        print(json.dumps(payload, indent=2))
    finally:
        env.close()


if __name__ == "__main__":
    main()
