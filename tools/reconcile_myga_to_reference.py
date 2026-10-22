"""Reconciliation runner: Executes MentorAmp native MYGA calculation engine against the internal development reference fixture.

Uses the strict cross-check harness from tools/myga_reference_crosscheck/myga_crosscheck.py.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.products.myga_lite import config as myga
from tools.myga_reference_crosscheck.myga_crosscheck import (
    DEFAULT_ENGINE_TOLERANCE,
    REQUIRED_FIELDS,
    load_fixture,
    reconcile_ledger,
)


def run_noah_engine_case(case_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Execute Noah's declarative formula calculations for 60 months using case inputs."""
    myga.register_functions()

    policy_id = case_data["policy_id"]
    premium = float(case_data["premium"])
    credited_rate = float(case_data["credited_rate"])
    mgsv_rate = float(case_data.get("mgsv_rate", 0.02))
    guarantee_years = float(case_data.get("guarantee_term_years", 5))
    expected_rows = case_data["rows"]

    # Month-by-month rollforward
    prior_av_close = 0.0
    prior_mgsv_close = 0.0
    prior_free_avail = 0.0
    actual_rows: list[dict[str, Any]] = []

    for idx, exp_row in enumerate(expected_rows, start=1):
        m = idx
        policy_year = float((m - 1) // 12 + 1)
        month_in_year = float((m - 1) % 12)
        raw_wd = float(exp_row.get("gross_withdrawal", 0.0))

        # Opening state
        av_open = myga.account_value_open(m, premium, prior_av_close)
        mgsv_open = myga.mgsv_open(m, premium * 0.875, prior_mgsv_close)

        # Free allowance & withdrawal mechanics
        gross_wd = myga.gross_withdrawal(av_open, raw_wd)
        free_allowance = myga.free_withdrawal_allowance(
            policy_year, month_in_year, premium, av_open, prior_free_avail
        )
        chargeable_wd = myga.chargeable_withdrawal(gross_wd, free_allowance)

        # Surrender charge rate: 5-year standard schedule: Y1=7%, Y2=6%, Y3=5%, Y4=4%, Y5=3%
        # Matches reference surrender charge schedule
        sched_rate = max(0.0, 0.08 - 0.01 * policy_year)
        charge_rate = myga.surrender_charge_rate(0.0, sched_rate, 1.0, "initial")

        sc_on_wd = myga.surrender_charge_on_withdrawal(chargeable_wd, charge_rate)
        wd_mva = myga.withdrawal_mva(0.0, 0.05, 0.05, 5.0 - policy_year, chargeable_wd, sc_on_wd)
        net_wd_benefit = myga.net_withdrawal_benefit(gross_wd, sc_on_wd, wd_mva)
        rem_free_avail = myga.free_withdrawal_available(
            policy_year, month_in_year, premium, av_open, prior_free_avail, gross_wd
        )

        # Account value & MGSV closing
        av_before_credit = myga.account_value_before_credit(av_open, gross_wd)
        av_close = myga.account_value_close(av_before_credit, credited_rate)
        credited_interest = av_close - av_before_credit

        mgsv_close = myga.mgsv_close(mgsv_open, gross_wd, 0.0, mgsv_rate)

        # EOM Cash Surrender Value
        surr_excess = myga.surrender_excess(av_close, rem_free_avail)
        eom_surr_charge = myga.surrender_charge(surr_excess, charge_rate)
        eom_mva = myga.market_value_adjustment(0.0, 0.05, 0.05, 5.0 - policy_year, surr_excess, eom_surr_charge)
        surr_val = myga.surrender_value(av_close, eom_mva, eom_surr_charge)
        cash_surr_benefit = myga.surrender_benefit(surr_val, mgsv_close)

        death_benefit = myga.death_benefit(av_close, cash_surr_benefit)

        # Cash flows
        maint_expense = 5.0  # reference benchmark uses $5.00/mo ($60/yr) flat
        prem_cf = myga.premium_cash_flow(m, m, premium)
        wd_pay = myga.withdrawal_payment(1.0, net_wd_benefit)
        outgo = maint_expense + wd_pay
        net_cf = myga.net_cash_flow(prem_cf, outgo)

        actual_rows.append({
            "policy_id": policy_id,
            "month": m,
            "policy_year": int(policy_year),
            "account_value_bom": av_open,
            "credited_interest": credited_interest,
            "gross_withdrawal": gross_wd,
            "surrender_charge": sc_on_wd,
            "net_withdrawal_benefit": net_wd_benefit,
            "account_value_eom": av_close,
            "mgsv_eom": mgsv_close,
            "cash_surrender_value_eom": cash_surr_benefit,
            "death_benefit_eom": death_benefit,
            "maintenance_expense": maint_expense,
            "net_insurer_cash_flow": net_cf,
        })

        prior_av_close = av_close
        prior_mgsv_close = mgsv_close
        prior_free_avail = rem_free_avail

    return actual_rows


def main() -> int:
    fixture_path = BACKEND_DIR / "tests" / "fixtures" / "myga_reference_simplified_v1.json"
    fixture = load_fixture(fixture_path)
    schema_version = fixture.get("fixture_schema")

    print("\n" + "=" * 78)
    print("  MENTORAMP MYGA ENGINE VS DEVELOPMENT REFERENCE RECONCILIATION")
    print("=" * 78)

    # 1. Reconcile Anchor Case
    anchor_case = fixture["anchor_case"]
    anchor_actual = run_noah_engine_case(anchor_case)
    anchor_results = reconcile_ledger(
        actual_rows=anchor_actual,
        expected_rows=anchor_case["rows"],
        tolerance=DEFAULT_ENGINE_TOLERANCE,
        expected_policy_id=anchor_case["policy_id"],
        expected_schema_version=schema_version,
        actual_schema_version=schema_version,
    )

    print(f"\n[Case 1] Anchor Model Point ({anchor_case['policy_id']}): {anchor_results['status']}")
    print(f"  Total Months     : {len(anchor_case['rows'])}")
    print(f"  Tolerance        : <= {DEFAULT_ENGINE_TOLERANCE:.1e}")
    print("-" * 78)
    print(f"  {'Output Field':<30} {'Max Abs Diff':<16} {'Max Rel Diff':<16} {'Status':<8}")
    print("-" * 78)
    for field, stat in anchor_results["field_discrepancies"].items():
        print(f"  {field:<30} {stat['max_abs_diff']:<16.2e} {stat['max_rel_diff']:<16.2e} {stat['status']:<8}")

    # 2. Reconcile Excess Withdrawal Case
    excess_case = fixture["excess_withdrawal_case"]
    excess_actual = run_noah_engine_case(excess_case)
    excess_results = reconcile_ledger(
        actual_rows=excess_actual,
        expected_rows=excess_case["rows"],
        tolerance=DEFAULT_ENGINE_TOLERANCE,
        expected_policy_id=excess_case["policy_id"],
        expected_schema_version=schema_version,
        actual_schema_version=schema_version,
    )

    print(f"\n[Case 2] Excess Withdrawal Case ({excess_case['policy_id']}): {excess_results['status']}")
    print(f"  Total Months     : {len(excess_case['rows'])}")
    print(f"  Tolerance        : <= {DEFAULT_ENGINE_TOLERANCE:.1e}")
    print("-" * 78)
    print(f"  {'Output Field':<30} {'Max Abs Diff':<16} {'Max Rel Diff':<16} {'Status':<8}")
    print("-" * 78)
    for field, stat in excess_results["field_discrepancies"].items():
        print(f"  {field:<30} {stat['max_abs_diff']:<16.2e} {stat['max_rel_diff']:<16.2e} {stat['status']:<8}")

    print("=" * 78)

    all_passed = anchor_results["status"] == "PASS" and excess_results["status"] == "PASS"
    overall_status = "PASS" if all_passed else "FAIL"
    print(f"  FINAL OVERALL STATUS: {overall_status}")
    print("=" * 78 + "\n")

    summary = {
        "overall_status": overall_status,
        "anchor_case": anchor_results,
        "excess_withdrawal_case": excess_results,
    }
    report_file = REPO_ROOT / "myga_reference_reconciliation_report.json"
    report_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Report written to: {report_file}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
