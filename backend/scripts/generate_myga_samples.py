"""Generate the SYNTHETIC MYGA sample files used for testing and reference models (deterministic).

Writes:
  samples/myga/synthetic_myga_inforce.csv       25 synthetic MYGA policies (no real people)
  samples/myga/myga_surrender_charge.csv        Surrender charge schedules (3, 5, 7, 10 years)
  samples/myga/myga_sc_age_cap.csv              Age caps and illustrative contractual waiver provisions

Run from the repository root or the backend folder:
  backend/.venv/Scripts/python backend/scripts/generate_myga_samples.py
"""

import csv
import random
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

SAMPLES = BACKEND.parent / "samples" / "myga"
SEED = 20261231
POLICY_COUNT = 25


def generate_surrender_charge_tables(samples_dir: Path) -> None:
    # 1. Surrender charge schedule table
    sc_rows = [
        # 3-Year Standard Schedule
        {"schedule_id": "SC_3YR_STANDARD", "policy_year": 1, "surrender_charge_rate": 0.07},
        {"schedule_id": "SC_3YR_STANDARD", "policy_year": 2, "surrender_charge_rate": 0.06},
        {"schedule_id": "SC_3YR_STANDARD", "policy_year": 3, "surrender_charge_rate": 0.05},
        {"schedule_id": "SC_3YR_STANDARD", "policy_year": 4, "surrender_charge_rate": 0.00},
        # 5-Year Standard Schedule (Anchor)
        {"schedule_id": "SC_5YR_STANDARD", "policy_year": 1, "surrender_charge_rate": 0.07},
        {"schedule_id": "SC_5YR_STANDARD", "policy_year": 2, "surrender_charge_rate": 0.06},
        {"schedule_id": "SC_5YR_STANDARD", "policy_year": 3, "surrender_charge_rate": 0.05},
        {"schedule_id": "SC_5YR_STANDARD", "policy_year": 4, "surrender_charge_rate": 0.04},
        {"schedule_id": "SC_5YR_STANDARD", "policy_year": 5, "surrender_charge_rate": 0.03},
        {"schedule_id": "SC_5YR_STANDARD", "policy_year": 6, "surrender_charge_rate": 0.00},
        # 7-Year Standard Schedule
        {"schedule_id": "SC_7YR_STANDARD", "policy_year": 1, "surrender_charge_rate": 0.08},
        {"schedule_id": "SC_7YR_STANDARD", "policy_year": 2, "surrender_charge_rate": 0.07},
        {"schedule_id": "SC_7YR_STANDARD", "policy_year": 3, "surrender_charge_rate": 0.06},
        {"schedule_id": "SC_7YR_STANDARD", "policy_year": 4, "surrender_charge_rate": 0.05},
        {"schedule_id": "SC_7YR_STANDARD", "policy_year": 5, "surrender_charge_rate": 0.04},
        {"schedule_id": "SC_7YR_STANDARD", "policy_year": 6, "surrender_charge_rate": 0.03},
        {"schedule_id": "SC_7YR_STANDARD", "policy_year": 7, "surrender_charge_rate": 0.02},
        {"schedule_id": "SC_7YR_STANDARD", "policy_year": 8, "surrender_charge_rate": 0.00},
        # 10-Year Standard Schedule
        {"schedule_id": "SC_10YR_STANDARD", "policy_year": 1, "surrender_charge_rate": 0.09},
        {"schedule_id": "SC_10YR_STANDARD", "policy_year": 2, "surrender_charge_rate": 0.08},
        {"schedule_id": "SC_10YR_STANDARD", "policy_year": 3, "surrender_charge_rate": 0.07},
        {"schedule_id": "SC_10YR_STANDARD", "policy_year": 4, "surrender_charge_rate": 0.06},
        {"schedule_id": "SC_10YR_STANDARD", "policy_year": 5, "surrender_charge_rate": 0.05},
        {"schedule_id": "SC_10YR_STANDARD", "policy_year": 6, "surrender_charge_rate": 0.04},
        {"schedule_id": "SC_10YR_STANDARD", "policy_year": 7, "surrender_charge_rate": 0.03},
        {"schedule_id": "SC_10YR_STANDARD", "policy_year": 8, "surrender_charge_rate": 0.02},
        {"schedule_id": "SC_10YR_STANDARD", "policy_year": 9, "surrender_charge_rate": 0.01},
        {"schedule_id": "SC_10YR_STANDARD", "policy_year": 10, "surrender_charge_rate": 0.00},
    ]
    with (samples_dir / "myga_surrender_charge.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["schedule_id", "policy_year", "surrender_charge_rate"])
        writer.writeheader()
        writer.writerows(sc_rows)

    # 2. Age cap and contractual waiver schedules
    cap_rows = [
        {"schedule_id": "AGE_CAP_59_HALF", "min_age": 59.5, "waiver_type": "attained_age", "surrender_charge_multiplier": 0.0},
        {"schedule_id": "RMD_WAIVER", "min_age": 73.0, "waiver_type": "rmd_waiver", "surrender_charge_multiplier": 0.0},
        {"schedule_id": "NURSING_HOME_WAIVER", "min_age": 0.0, "waiver_type": "confinement", "surrender_charge_multiplier": 0.0},
        {"schedule_id": "TERMINAL_ILLNESS_WAIVER", "min_age": 0.0, "waiver_type": "terminal_diagnosis", "surrender_charge_multiplier": 0.0},
    ]
    with (samples_dir / "myga_sc_age_cap.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["schedule_id", "min_age", "waiver_type", "surrender_charge_multiplier"])
        writer.writeheader()
        writer.writerows(cap_rows)


def generate_inforce(path: Path) -> None:
    rng = random.Random(SEED)
    schedules = {
        3: "SC_3YR_STANDARD",
        5: "SC_5YR_STANDARD",
        7: "SC_7YR_STANDARD",
        10: "SC_10YR_STANDARD",
    }
    rates_by_term = {
        3: [0.0350, 0.0375, 0.0400],
        5: [0.0425, 0.0450, 0.0475, 0.0500],
        7: [0.0475, 0.0500, 0.0525],
        10: [0.0500, 0.0525, 0.0550],
    }

    rows = []
    for index in range(1, POLICY_COUNT + 1):
        term = rng.choice([3, 5, 5, 5, 7, 7, 10])
        issue_year = rng.randint(2020, 2026)
        issue_month = rng.randint(1, 12)
        issue_day = 1
        issue_age = rng.randint(45, 78)
        gender = "M" if index % 2 else "F"
        premium = float(rng.randrange(25000, 500001, 5000))
        credited_rate = rng.choice(rates_by_term[term])
        free_wd_pct = 0.10
        mgsv_rate = 0.02
        mgsv_load = 0.875

        rows.append({
            "policy_id": f"MYGA-{index:04d}",
            "product_type": "MYGA",
            "issue_date": f"{issue_year}-{issue_month:02d}-{issue_day:02d}",
            "issue_age": issue_age,
            "gender": gender,
            "premium": premium,
            "guarantee_term_years": term,
            "credited_rate": credited_rate,
            "free_withdrawal_pct": free_wd_pct,
            "surrender_charge_schedule_id": schedules[term],
            "mgsv_rate": mgsv_rate,
            "mgsv_premium_load": mgsv_load,
        })

    # Policy MYGA-0001 is the Development Reference Anchor Benchmark:
    # Issued 2022-01-01, Age 62, Male, 100,000 USD premium, 5-yr guarantee at 4.5%, 10% free withdrawal
    rows[0].update({
        "issue_date": "2022-01-01",
        "issue_age": 62,
        "gender": "M",
        "premium": 100000.0,
        "guarantee_term_years": 5,
        "credited_rate": 0.045,
        "free_withdrawal_pct": 0.10,
        "surrender_charge_schedule_id": "SC_5YR_STANDARD",
        "mgsv_rate": 0.02,
        "mgsv_premium_load": 0.875,
    })

    # Policy MYGA-0002 is the Excess Withdrawal Reference Point:
    # 5-yr guarantee, 4.5% rate, with excess partial withdrawal
    rows[1].update({
        "issue_date": "2022-01-01",
        "issue_age": 65,
        "gender": "F",
        "premium": 100000.0,
        "guarantee_term_years": 5,
        "credited_rate": 0.045,
        "free_withdrawal_pct": 0.10,
        "surrender_charge_schedule_id": "SC_5YR_STANDARD",
        "mgsv_rate": 0.02,
        "mgsv_premium_load": 0.875,
    })

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    generate_surrender_charge_tables(SAMPLES)
    generate_inforce(SAMPLES / "synthetic_myga_inforce.csv")
    print(f"Successfully generated synthetic MYGA sample files in {SAMPLES}")


if __name__ == "__main__":
    main()
