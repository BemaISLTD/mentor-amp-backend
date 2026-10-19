"""Strict reconciliation harness comparing MentorAmp MYGA outputs against the reference development oracle.

Usage:
  python tools/lifelib_crosscheck/myga_crosscheck.py [--run-output <path>] [--tolerance 1e-6] [--report <path>]

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
    / "myga_reference_simplified_v1.json"
)

REQUIRED_FIELDS = [
    "account_value_bom",
    "credited_interest",
    "gross_withdrawal",
    "surrender_charge",
    "net_withdrawal_benefit",
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
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("fixture_schema") != "mentoramp.reference_fixture/v1":
        raise ValueError(f"Unknown fixture schema: {data.get('fixture_schema')}")
    return data


def reconcile_ledger(
    actual_rows: list[dict[str, Any]],
    expected_rows: list[dict[str, Any]],
    tolerance: float = 1e-6,
    expected_policy_id: str | None = None,
    expected_schema_version: str | None = None,
    actual_schema_version: str | None = None,
) -> dict[str, Any]:
    """Strictly reconcile monthly ledger rows against expected reference values.

    Requires:
      - Schema version agreement (if specified)
      - Exact canonical key-set agreement: set((policy_id, month))
      - No duplicate keys
      - No missing or extra months
      - All required fields present in every row
      - All values strictly finite numeric floats (no NaN, +inf, -inf)
      - Value discrepancies <= tolerance
    """
    results: dict[str, Any] = {
        "status": "PASS",
        "errors": [],
        "field_discrepancies": {
            field: {"max_abs_diff": 0.0, "max_rel_diff": 0.0, "status": "PASS"}
            for field in REQUIRED_FIELDS
        },
    }

    # 1. Schema version check
    if expected_schema_version and actual_schema_version:
        if expected_schema_version != actual_schema_version:
            results["status"] = "FAIL"
            results["errors"].append(
                f"Schema version mismatch: expected '{expected_schema_version}', got '{actual_schema_version}'"
            )

    # 2. Check for empty actual output
    if not actual_rows:
        results["status"] = "FAIL"
        results["errors"].append("Actual run output contains zero rows.")
        return results

    if not expected_rows:
        results["status"] = "FAIL"
        results["errors"].append("Expected fixture rows are empty.")
        return results

    # 3. Build expected key map and detect any fixture defects
    expected_map: dict[tuple[str, int], dict[str, Any]] = {}
    for r in expected_rows:
        pol = str(r.get("policy_id", expected_policy_id or ""))
        try:
            m = int(r["month"])
        except (KeyError, ValueError, TypeError):
            results["status"] = "FAIL"
            results["errors"].append(f"Expected row missing valid 'month': {r}")
            return results
        key = (pol, m)
        if key in expected_map:
            results["status"] = "FAIL"
            results["errors"].append(f"Duplicate key in expected fixture: {key}")
            return results
        expected_map[key] = r

    # 4. Build actual key map and check for duplicates or invalid keys
    actual_map: dict[tuple[str, int], dict[str, Any]] = {}
    for r in actual_rows:
        pol = str(r.get("policy_id", expected_policy_id or ""))
        try:
            m = int(r["month"])
        except (KeyError, ValueError, TypeError):
            results["status"] = "FAIL"
            results["errors"].append(f"Actual row missing valid 'month': {r}")
            continue

        key = (pol, m)
        if key in actual_map:
            results["status"] = "FAIL"
            results["errors"].append(f"Duplicate month/key in actual output: {key}")
            continue
        actual_map[key] = r

    # 5. Key set agreement check (catches missing, extra, wrong month, or wrong policy ID)
    expected_keys = set(expected_map.keys())
    actual_keys = set(actual_map.keys())

    missing_keys = expected_keys - actual_keys
    extra_keys = actual_keys - expected_keys

    if missing_keys:
        results["status"] = "FAIL"
        results["errors"].append(
            f"Missing {len(missing_keys)} expected keys: {sorted(missing_keys)[:10]}"
        )

    if extra_keys:
        results["status"] = "FAIL"
        results["errors"].append(
            f"Found {len(extra_keys)} unexpected extra keys: {sorted(extra_keys)[:10]}"
        )

    # 6. Deep value comparison for matching keys
    common_keys = sorted(expected_keys & actual_keys, key=lambda k: (k[0], k[1]))

    for key in common_keys:
        act = actual_map[key]
        exp = expected_map[key]
        pol, m = key

        # Check policy_id explicitly if declared
        if expected_policy_id and str(act.get("policy_id", "")) != expected_policy_id:
            results["status"] = "FAIL"
            results["errors"].append(
                f"Month {m}: policy_id mismatch: expected '{expected_policy_id}', got '{act.get('policy_id')}'"
            )

        # Check each required field
        for field in REQUIRED_FIELDS:
            stat = results["field_discrepancies"][field]

            if field not in act:
                results["status"] = "FAIL"
                stat["status"] = "FAIL"
                results["errors"].append(f"Key {key}: missing required field '{field}'")
                continue

            raw_val = act[field]
            try:
                act_val = float(raw_val)
            except (ValueError, TypeError):
                results["status"] = "FAIL"
                stat["status"] = "FAIL"
                results["errors"].append(
                    f"Key {key}: field '{field}' has non-numeric value: {raw_val!r}"
                )
                continue

            # Check for NaN / Infinity
            if math.isnan(act_val):
                results["status"] = "FAIL"
                stat["status"] = "FAIL"
                results["errors"].append(f"Key {key}: field '{field}' is NaN")
                continue

            if math.isinf(act_val):
                results["status"] = "FAIL"
                stat["status"] = "FAIL"
                results["errors"].append(
                    f"Key {key}: field '{field}' is {'+Infinity' if act_val > 0 else '-Infinity'}"
                )
                continue

            exp_val = float(exp[field])
            abs_diff = abs(act_val - exp_val)
            rel_diff = abs_diff / abs(exp_val) if abs(exp_val) > 1e-9 else abs_diff

            if abs_diff > stat["max_abs_diff"]:
                stat["max_abs_diff"] = abs_diff
            if rel_diff > stat["max_rel_diff"]:
                stat["max_rel_diff"] = rel_diff

            if abs_diff > tolerance:
                results["status"] = "FAIL"
                stat["status"] = "FAIL"
                results["errors"].append(
                    f"Key {key}: field '{field}' diff {abs_diff:.2e} exceeds tolerance {tolerance:.2e} (actual: {act_val}, expected: {exp_val})"
                )

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Strict cross-check of MYGA output against reference oracle.")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE, help="Path to reference oracle JSON fixture")
    parser.add_argument("--run-output", type=Path, default=None, help="Path to run output JSON or CSV to test")
    parser.add_argument("--tolerance", type=float, default=1e-6, help="Reconciliation tolerance (default: 1e-6)")
    parser.add_argument("--report", type=Path, default=None, help="Path to write reconciliation report JSON")
    args = parser.parse_args()

    fixture = load_fixture(args.fixture)
    expected_rows = fixture["anchor_case"]["rows"]
    expected_policy_id = fixture["anchor_case"]["policy_id"]
    schema_version = fixture.get("fixture_schema")

    is_self_test = args.run_output is None

    if is_self_test:
        actual_rows = expected_rows
        actual_schema = schema_version
    else:
        text = args.run_output.read_text(encoding="utf-8")
        if args.run_output.suffix == ".json":
            data = json.loads(text)
            actual_rows = data if isinstance(data, list) else data.get("rows", [])
            actual_schema = data.get("fixture_schema") if isinstance(data, dict) else schema_version
        else:
            import csv
            actual_rows = list(csv.DictReader(text.splitlines()))
            actual_schema = schema_version

    results = reconcile_ledger(
        actual_rows=actual_rows,
        expected_rows=expected_rows,
        tolerance=args.tolerance,
        expected_policy_id=expected_policy_id,
        expected_schema_version=schema_version,
        actual_schema_version=actual_schema,
    )

    print("\n" + "=" * 76)
    if is_self_test:
        print("  CROSSCHECK TOOL SELF-TEST: " + results["status"])
        print("=" * 76)
        print("  Notice: No MentorAmp run output was supplied (--run-output).")
        print("  This verifies only that the expected fixture can be parsed and reconciled")
        print("  with itself. It is NOT an external actuarial reconciliation.")
    else:
        print(f"  MENTORAMP VS REFERENCE: {results['status']}")
        print("=" * 76)

    print(f"  Reference Model  : {fixture['source']['model']} ({fixture['source']['release']})")
    print(f"  Classification   : {fixture.get('reference_classification')}")
    print(f"  Expected Policy  : {expected_policy_id}")
    print(f"  Total Months     : {len(expected_rows)}")
    print(f"  Tolerance        : <= {args.tolerance:.1e}")
    print(f"  Overall Status   : {results['status']}")
    print("-" * 76)
    print(f"  {'Output Field':<30} {'Max Abs Diff':<16} {'Max Rel Diff':<16} {'Status':<8}")
    print("-" * 76)
    for field, stat in results["field_discrepancies"].items():
        print(
            f"  {field:<30} {stat['max_abs_diff']:<16.2e} {stat['max_rel_diff']:<16.2e} {stat['status']:<8}"
        )
    print("=" * 76)

    if results["errors"]:
        print(f"\nDiscrepancy / Error Details ({len(results['errors'])} issues):")
        for err in results["errors"][:15]:
            print(f"  - {err}")
        if len(results["errors"]) > 15:
            print(f"  ... and {len(results['errors']) - 15} more.")

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nReport written to {args.report}")

    return 0 if results["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
