"""Generate the canonical SYNTHETIC MYGA sample files (mentoramp.myga.inforce/v1).

Writes:
  samples/myga/synthetic_myga_inforce.csv          25 synthetic MYGA policies under canonical schema
  samples/myga/anchor_policy.csv                   1 anchor MYGA benchmark policy
  samples/myga/myga_surrender_charge.csv           Surrender charge schedule table (initial & renewal)
  samples/myga/myga_sc_age_cap.csv                 Attained-age surrender charge cap table
  samples/myga/synthetic_myga_inforce_10001.csv    Single-policy reference inforce for MYGA-10001
  samples/myga/expected_reference_myga_10001.csv   60-month expected reference ledger for MYGA-10001
  samples/myga/synthetic_myga_inforce_100001.csv   Single-policy reference inforce for MYGA-100001 (alias)
  samples/myga/expected_reference_myga_100001.csv  60-month expected reference ledger for MYGA-100001 (alias)

Run from the repository root or the backend folder:
  backend/.venv/Scripts/python backend/scripts/generate_myga_samples.py
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

SAMPLES = BACKEND.parent / "samples" / "myga"
FIXTURE_PATH = BACKEND / "tests" / "fixtures" / "myga_reference_simplified_v1.json"
POLICY_COUNT = 25

CANONICAL_INFORCE_FIELDS = [
    "policy_id",
    "product_type",
    "issue_date",
    "issue_age",
    "gender",
    "premium",
    "guarantee_period_years",
    "declared_rate",
    "gmir",
    "gmsv_rate",
    "mva_ref_yield_at_issue",
    "account_value",
    "mgsv_value",
    "free_wd_base",
]


def generate_surrender_charge_tables(samples_dir: Path) -> None:
    """Generate canonical surrender charge factor tables.

    Includes illustrative contractual waiver provisions (e.g., rmd_waiver, terminal illness).
    """
    # 1. Surrender charge schedule table
    sc_rows = []
    for yr, rate in enumerate([0.09, 0.08, 0.07, 0.06, 0.05], start=1):
        sc_rows.append({"schedule": "initial", "year_in_gp": yr, "rate": f"{rate:.2f}"})
    for yr, rate in enumerate([0.05, 0.04, 0.03, 0.02, 0.01], start=1):
        sc_rows.append({"schedule": "renewal", "year_in_gp": yr, "rate": f"{rate:.2f}"})

    with (samples_dir / "myga_surrender_charge.csv").open("w", newline="\n", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["schedule", "year_in_gp", "rate"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(sc_rows)

    # 2. Attained-age surrender charge cap table
    def _cap_for_age(age: int) -> str:
        if age <= 93:
            return "1.00"
        elif age == 94:
            return "0.04"
        elif age == 95:
            return "0.03"
        elif age == 96:
            return "0.02"
        elif age == 97:
            return "0.01"
        else:
            return "0.00"

    cap_rows = [{"attained_age": age, "cap": _cap_for_age(age)} for age in range(121)]
    with (samples_dir / "myga_sc_age_cap.csv").open("w", newline="\n", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["attained_age", "cap"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(cap_rows)


def generate_inforce(path: Path) -> None:
    """Generate the 25-policy synthetic MYGA inforce dataset (mentoramp.myga.inforce/v1)."""
    rates = [0.0425, 0.0430, 0.0435, 0.0440, 0.0445]
    rows = []
    for i in range(1, POLICY_COUNT + 1):
        premium = 50000 + (i - 1) * 10000
        age = 45 + (i - 1)
        gender = "F" if i % 2 == 1 else "M"
        rate = rates[(i - 1) % len(rates)]
        mgsv = int(premium * 0.875)
        rows.append({
            "policy_id": f"MYGA-{i:04d}",
            "product_type": "MYGA",
            "issue_date": "2026-12-31",
            "issue_age": age,
            "gender": gender,
            "premium": premium,
            "guarantee_period_years": 5,
            "declared_rate": f"{rate:.4f}",
            "gmir": "0.0025",
            "gmsv_rate": "0.028",
            "mva_ref_yield_at_issue": "0.05",
            "account_value": premium,
            "mgsv_value": mgsv,
            "free_wd_base": premium,
        })

    with path.open("w", newline="\n", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANONICAL_INFORCE_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def generate_anchor_policy(path: Path) -> None:
    """Generate the single anchor benchmark policy."""
    row = {
        "policy_id": "MYGA-ANCHOR",
        "product_type": "MYGA",
        "issue_date": "2026-12-31",
        "issue_age": 60,
        "gender": "M",
        "premium": 100000,
        "guarantee_period_years": 5,
        "declared_rate": "0.0445",
        "gmir": "0.0025",
        "gmsv_rate": "0.028",
        "mva_ref_yield_at_issue": "0.05",
        "account_value": 100000,
        "mgsv_value": 87500,
        "free_wd_base": 100000,
    }
    with path.open("w", newline="\n", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANONICAL_INFORCE_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerow(row)


def generate_reference_package(samples_dir: Path, fixture_path: Path) -> None:
    """Generate the canonical reference inforce and 60-month expected results CSVs.

    Extracts the verified anchor case from myga_reference_simplified_v1.json and writes:
    - synthetic_myga_inforce_10001.csv & expected_reference_myga_10001.csv (primary M3 benchmark)
    - synthetic_myga_inforce_100001.csv & expected_reference_myga_100001.csv (4-zero alias)
    """
    if not fixture_path.is_file():
        raise FileNotFoundError(f"Reference fixture not found: {fixture_path}")

    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    anchor = fixture["anchor_case"]
    expected_rows_raw = anchor["rows"]
    if not expected_rows_raw:
        raise ValueError("anchor_case contains no rows")

    for policy_id, suffix in [("MYGA-10001", "10001"), ("MYGA-100001", "100001")]:
        # 1. Inforce file (1 contract)
        inforce_row = {
            "policy_id": policy_id,
            "product_type": "MYGA",
            "issue_date": "2026-12-31",
            "issue_age": anchor.get("issue_age", 62),
            "gender": anchor.get("gender", "M"),
            "premium": int(anchor.get("premium", 100000)),
            "guarantee_period_years": anchor.get("guarantee_term_years", 5),
            "declared_rate": f"{anchor.get('credited_rate', 0.045):.4f}",
            "gmir": "0.0025",
            "gmsv_rate": f"{anchor.get('mgsv_rate', 0.02):.4f}",
            "mva_ref_yield_at_issue": "0.05",
            "account_value": int(anchor.get("premium", 100000)),
            "mgsv_value": int(anchor.get("premium", 100000) * anchor.get("mgsv_premium_load", 0.875)),
            "free_wd_base": int(anchor.get("premium", 100000)),
        }
        inforce_path = samples_dir / f"synthetic_myga_inforce_{suffix}.csv"
        with inforce_path.open("w", newline="\n", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CANONICAL_INFORCE_FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerow(inforce_row)

        # 2. Expected results file (60 monthly ledger rows)
        expected_path = samples_dir / f"expected_reference_myga_{suffix}.csv"
        fieldnames = list(expected_rows_raw[0].keys())
        with expected_path.open("w", newline="\n", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            for r in expected_rows_raw:
                writer.writerow({**r, "policy_id": policy_id})


def main() -> None:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    generate_surrender_charge_tables(SAMPLES)
    generate_inforce(SAMPLES / "synthetic_myga_inforce.csv")
    generate_anchor_policy(SAMPLES / "anchor_policy.csv")
    generate_reference_package(SAMPLES, FIXTURE_PATH)
    print(f"Successfully generated canonical synthetic MYGA sample files in {SAMPLES}")


if __name__ == "__main__":
    main()
