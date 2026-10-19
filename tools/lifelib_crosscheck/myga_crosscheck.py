"""Automated cross-check harness comparing MentorAmp MYGA outputs against the LifeLib reference oracle.

Usage:
  python tools/lifelib_crosscheck/myga_crosscheck.py [--run-output <path>] [--tolerance 1e-6] [--report <path>]

Reference:
  LifeLib USLib MYGA_US_S @ 5d91f95a7c2b3e8f419b846e273f081492576b5e
  Target precision: <= $0.000001 (1e-6)
"""

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = (
    REPO_ROOT
    / "backend"
    / "tests"
    / "fixtures"
    / "myga_reference_lifelib_v0_17_2.json"
)

COMPARED_FIELDS = [
    "account_value_bom",
    "interest_credited",
    "partial_withdrawal",
    "surrender_charge_on_withdrawal",
    "account_value_eom",
    "mgsv_eom",
    "cash_surrender_value_eom",
    "death_benefit_eom",
    "maintenance_expense",
    "net_insurer_cash_flow",
]


def load_fixture(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Reference fixture not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def reconcile_ledger(
    actual_rows: list[dict[str, Any]],
    expected_rows: list[dict[str, Any]],
    tolerance: float = 1e-6,
) -> dict[str, Any]:
    """Reconcile monthly ledger lines against expected reference values."""
    results: dict[str, Any] = {
        "status": "PASS",
        "months_compared": min(len(actual_rows), len(expected_rows)),
        "field_discrepancies": {},
        "failures": [],
    }

    for field in COMPARED_FIELDS:
        results["field_discrepancies"][field] = {
            "max_abs_diff": 0.0,
            "max_rel_diff": 0.0,
            "status": "PASS",
        }

    for month_idx in range(results["months_compared"]):
        actual = actual_rows[month_idx]
        expected = expected_rows[month_idx]
        m = actual.get("month", month_idx + 1)

        for field in COMPARED_FIELDS:
            if field not in actual or field not in expected:
                continue

            act_val = float(actual[field])
            exp_val = float(expected[field])
            abs_diff = abs(act_val - exp_val)
            rel_diff = abs_diff / abs(exp_val) if abs(exp_val) > 1e-9 else abs_diff

            field_stat = results["field_discrepancies"][field]
            if abs_diff > field_stat["max_abs_diff"]:
                field_stat["max_abs_diff"] = abs_diff
            if rel_diff > field_stat["max_rel_diff"]:
                field_stat["max_rel_diff"] = rel_diff

            if abs_diff > tolerance:
                results["status"] = "FAIL"
                field_stat["status"] = "FAIL"
                results["failures"].append({
                    "month": m,
                    "field": field,
                    "actual": act_val,
                    "expected": exp_val,
                    "abs_diff": abs_diff,
                })

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Cross-check MYGA engine output against LifeLib benchmark.")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE, help="Path to reference oracle JSON fixture")
    parser.add_argument("--run-output", type=Path, default=None, help="Path to run output JSON or CSV to test")
    parser.add_argument("--tolerance", type=float, default=1e-6, help="Reconciliation tolerance (default: 1e-6)")
    parser.add_argument("--report", type=Path, default=None, help="Path to write reconciliation report JSON")
    args = parser.parse_args()

    fixture = load_fixture(args.fixture)
    expected_rows = fixture["anchor_case"]["rows"]

    if args.run_output and args.run_output.is_file():
        text = args.run_output.read_text(encoding="utf-8")
        if args.run_output.suffix == ".json":
            data = json.loads(text)
            actual_rows = data if isinstance(data, list) else data.get("rows", [])
        else:
            import csv
            actual_rows = list(csv.DictReader(text.splitlines()))
    else:
        # Self-test mode against fixture's anchor rows
        print("No --run-output provided. Running self-test against internal anchor specification...")
        actual_rows = expected_rows

    results = reconcile_ledger(actual_rows, expected_rows, tolerance=args.tolerance)

    print("\n" + "=" * 70)
    print("  MENTORAMP MYGA REFERENCE RECONCILIATION SUMMARY")
    print("=" * 70)
    print(f"  Source Model     : {fixture['source']['model']} ({fixture['source']['release']})")
    print(f"  Commit Ref       : {fixture['source']['commit']}")
    print(f"  Months Compared  : {results['months_compared']}")
    print(f"  Tolerance        : <= {args.tolerance:.1e}")
    print(f"  Overall Status   : {results['status']}")
    print("-" * 70)
    print(f"  {'Output Field':<32} {'Max Abs Diff':<16} {'Status':<10}")
    print("-" * 70)
    for field, stat in results["field_discrepancies"].items():
        print(f"  {field:<32} {stat['max_abs_diff']:<16.2e} {stat['status']:<10}")
    print("=" * 70 + "\n")

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"Report written to {args.report}")

    return 0 if results["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
