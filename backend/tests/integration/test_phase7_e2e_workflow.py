"""Phase 7 DB -> engine -> Parquet -> trace -> reconciliation -> report proof."""

from pathlib import Path

from scripts.review_phase7 import generate


def test_phase7_isolated_end_to_end_workflow(tmp_path: Path):
    evidence = generate(tmp_path / "evidence")

    assert evidence["classification"] == "SYNTHETIC_ENGINEERING_EVIDENCE"
    for code, expected_count in (("IDI", 2), ("VA", 4), ("PRT", 2)):
        assert len(evidence[code]) == expected_count
        for run in evidence[code]:
            assert run["status"] == "success"
            assert len(run["run_package_fingerprint"]) == 64
            assert run["engine_version"] == "m3-cpu-dsl-1"
            assert {item["type"] for item in run["artifacts"]} >= {"outputs", "traces"}
            assert run["trace_root"]

    assert evidence["reconciliation"]["outcomes"] == ["PASS", "FAIL"]
    assert evidence["prt_block_aggregation"]["participant_count"] == 4
    assert evidence["prt_block_aggregation"]["projection_year_rows"]
    assert evidence["life_readiness"]["version_numbers"] == [1, 2]
    assert evidence["life_readiness"]["four_eyes_approved"] is True
    assert evidence["life_readiness"]["calculation_status"] == "MODEL_NOT_CONFIGURED"
    assert evidence["report"]["schema_version"] == "mentoramp.report/v1"
    assert evidence["report"]["is_production_approved"] is False
    assert {"report.generated", "report.exported", "report.published"} <= set(
        evidence["report"]["audit_actions"]
    )
    assert (tmp_path / "evidence" / "phase7-evidence.json").is_file()
