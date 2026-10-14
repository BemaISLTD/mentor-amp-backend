"""Generate the SYNTHETIC SPIA sample files used by the M1 demo (deterministic).

Writes:
  samples/spia/synthetic_spia_inforce.csv       25 synthetic policies (no real people)
  samples/spia/synthetic_mortality_gompertz.csv synthetic Gompertz mortality (not a published table)

Run from the repository root or the backend folder:
  backend/.venv/Scripts/python backend/scripts/generate_spia_samples.py
"""

import csv
import random
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.products.spia_lite import config as spia  # noqa: E402

SAMPLES = BACKEND.parent / "samples" / "spia"
SEED = 20261231
POLICY_COUNT = 25


def generate_inforce(path: Path) -> None:
    rng = random.Random(SEED)
    rows = []
    for index in range(1, POLICY_COUNT + 1):
        issue_year = rng.randint(2021, 2026)
        issue_month = rng.randint(1, 12)
        monthly_payment = rng.randrange(500, 3001, 25)
        rows.append({
            "policy_id": f"SPIA-{index:04d}",
            "product_type": "SPIA",
            "issue_date": f"{issue_year}-{issue_month:02d}-01",
            "issue_age": rng.randint(58, 82),
            "gender": "M" if index % 2 else "F",
            "premium": round(monthly_payment * 12 * 12, -3),
            "monthly_payment": monthly_payment,
        })
    # SPIA-0001 is the worked example in the contract: issued 2022-03-01 at age 67, male, 1,650/month.
    rows[0].update({"issue_date": "2022-03-01", "issue_age": 67, "gender": "M",
                    "monthly_payment": 1650, "premium": 250000})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def generate_mortality(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["age", "gender", "qx"])
        for age in range(spia.MORTALITY_TABLE_MIN_AGE, spia.MORTALITY_TABLE_MAX_AGE + 1):
            for gender in ("F", "M"):
                writer.writerow([age, gender, f"{spia.gompertz_qx(age, gender):.6f}"])


def main() -> None:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    generate_inforce(SAMPLES / "synthetic_spia_inforce.csv")
    generate_mortality(SAMPLES / "synthetic_mortality_gompertz.csv")
    print(f"Wrote samples to {SAMPLES}")


if __name__ == "__main__":
    main()
