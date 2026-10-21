"""Cross-check MentorAmp's MYGA mathematics against an external LifeLib checkout.

LifeLib remains outside this repository.  The script copies the caller-supplied product
folder into a temporary work area, supplies aligned deterministic scenarios, executes the
external model, and compares it with MentorAmp's native declarative projection engine.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path


THRESHOLD = 0.000001
REFERENCE_COMMIT = "5d91f9558c52fc98bca71a5978a1327baf5b0d11"
SCENARIOS = {
    "Base": {"market_rate": 0.0445, "mva_ref_yield": 0.0500},
    "Rates Up 150bp": {"market_rate": 0.0595, "mva_ref_yield": 0.0650},
}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("reference_product", type=Path)
    parser.add_argument("repository", type=Path)
    parser.add_argument("scratch", type=Path)
    return parser.parse_args()


def _set_scenario(product: Path, values: dict[str, float]) -> None:
    path = product / "rate_scenario.csv"
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        fields = reader.fieldnames
        rows = list(reader)
    if not fields:
        raise RuntimeError("LifeLib rate_scenario.csv has no header")
    for row in rows:
        if row["scenario_id"] == "base":
            row["market_rate"] = str(values["market_rate"])
            row["mva_ref_yield"] = str(values["mva_ref_yield"])
    with path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _reference_commit(source: Path) -> str:
    for candidate in (source, *source.parents):
        git_path = candidate / ".git"
        if not git_path.exists():
            continue
        if git_path.is_file():
            marker = git_path.read_text(encoding="utf-8").strip()
            if not marker.startswith("gitdir: "):
                raise RuntimeError(f"Unrecognized Git metadata marker: {git_path}")
            git_path = (candidate / marker.removeprefix("gitdir: ")).resolve()
        head = (git_path / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: "):
            return head
        reference = head.removeprefix("ref: ")
        loose_ref = git_path / reference
        if loose_ref.is_file():
            return loose_ref.read_text(encoding="utf-8").strip()
        packed = git_path / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line and not line.startswith(("#", "^")):
                    commit, name = line.split(" ", 1)
                    if name == reference:
                        return commit
        raise RuntimeError(f"Cannot resolve Git reference {reference}")
    raise RuntimeError("The external reference path is not inside a Git checkout")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def _mentor_projection(myga, product: Path, scenario: dict[str, float]) -> list[dict[str, float]]:
    """Run the native declarative graph with independently parsed reference inputs."""
    from datetime import date

    from app.core.formula_engine.formulas import FORMULA_FUNCTIONS
    from app.core.projection_engine.engine import order_formulas, run_policy
    from app.core.projection_engine.run_data import (
        FormulaSpec, PolicyRecord, RunData, ScenarioSpec, TableSpec, VariableSpec,
    )

    myga.register_functions()
    variables = {
        row["name"]: VariableSpec(
            name=row["name"], kind=row["kind"], data_type=row.get("data_type", "number"),
            unit=row.get("unit"), source=dict(row.get("source") or {}),
            default_value=row.get("default_value"), required=row.get("required", True),
            allow_scenario_override=row.get("allow_scenario_override", False),
        )
        for row in myga.VARIABLES
    }
    formulas = order_formulas([
        FormulaSpec(
            id=f"myga-oracle-{index}", name=row["name"],
            output_variable=row["output_variable"], function_ref=row["function_ref"],
            dependencies=tuple(row["dependencies"]), illustrative=True,
        )
        for index, row in enumerate(myga.FORMULAS, start=1)
    ])

    mortality = TableSpec(
        id="external-mortality", name=myga.MORTALITY_TABLE_NAME,
        key_columns=("age", "gender"), value_column="qx",
        rows=[
            {"age": int(row["age"]), "gender": row["sex"], "qx": float(row["mort_rate"])}
            for row in _read_csv(product / "mort_table.csv")
        ],
    )
    charges = TableSpec(
        id="external-charges", name=myga.SURRENDER_CHARGE_TABLE_NAME,
        key_columns=("schedule", "year_in_gp"), value_column="rate", kind="factor",
        rows=[
            {"schedule": row["schedule"], "year_in_gp": int(row["contract_year"]),
             "rate": float(row["surr_charge_rate"])}
            for row in _read_csv(product / "surr_charge_table.csv")
        ],
    )
    source_caps = {
        int(row["age"]): float(row["surr_charge_cap"])
        for row in _read_csv(product / "surr_charge_age_cap.csv")
    }
    caps = TableSpec(
        id="external-age-caps", name=myga.SURRENDER_CHARGE_AGE_CAP_TABLE_NAME,
        key_columns=("attained_age",), value_column="cap", kind="factor",
        rows=[
            {"attained_age": age, "cap": source_caps.get(
                age, 1.0 if age < min(source_caps) else source_caps[max(source_caps)]
            )}
            for age in range(121)
        ],
    )
    for table in (mortality, charges, caps):
        if table.build_index():
            raise RuntimeError(f"Duplicate keys in {table.name}")

    policy = PolicyRecord(policy_id="MYGA-ORACLE-ANCHOR", data={
        "issue_date": "2026-12-31", "issue_age": 60, "gender": "M",
        "premium": 100_000.0, "account_value": 100_000.0, "mgsv_value": 87_500.0,
        "free_wd_base": 100_000.0, "guarantee_period_years": 5,
        "declared_rate": 0.0445, "gmir": 0.0025, "gmsv_rate": 0.028,
        "mva_ref_yield_at_issue": 0.05,
    })
    output_names = {
        "active_inforce": "inforce_open",
        "premium": "premium_cash_flow",
        "withdrawal": "withdrawal_payment",
        "death_claims": "death_claims",
        "surrender_claims": "surrender_claims",
        "annuitization": "annuitization_transfer",
        "maturity_claims": "maturity_benefit",
        "commission": "acquisition_commission",
        "maintenance_expense": "maintenance_expense",
        "net_cash_flow": "net_cash_flow",
        "account_value": "account_value_close",
        "mgsv": "mgsv_close",
    }
    run_data = RunData(
        variables=variables, formulas=formulas,
        tables={table.name: table for table in (mortality, charges, caps)},
        policies=[policy],
        scenario=ScenarioSpec(
            id="aligned-reference", name="Aligned reference",
            overrides=tuple({
                "target_variable": name, "operation": "set", "value": value,
                "applies_from_period": 1, "applies_to_period": 480,
            } for name, value in scenario.items()) + ({
                "target_variable": "withdrawal", "operation": "set", "value": 4_000.0,
                "applies_from_period": 13, "applies_to_period": 13,
            },),
        ),
        valuation_date=date(2026, 12, 31), horizon_months=480,
        output_variables=list(output_names.values()),
    )
    result = run_policy(run_data, policy, FORMULA_FUNCTIONS)
    if result.error:
        raise RuntimeError(f"MentorAmp engine failed: {result.error}")
    values = {(month, name): float(value) for month, name, value in result.outputs}
    return [
        {public: values[(month, internal)] for public, internal in output_names.items()}
        for month in range(1, 481)
    ]


def _compare(reference, mentor_rows: list[dict[str, float]]) -> dict:
    cashflows = reference.result_cf()
    account_values = reference.result_av()
    mapping = {
        "active_inforce": (cashflows, "pols_if"),
        "premium": (cashflows, "premiums"),
        "withdrawal": (cashflows, "withdrawals"),
        "death_claims": (cashflows, "claims_death"),
        "surrender_claims": (cashflows, "claims_lapse"),
        "annuitization": (cashflows, "claims_annuitization"),
        "maturity_claims": (cashflows, "claims_maturity"),
        "commission": (cashflows, "commissions"),
        "maintenance_expense": (cashflows, "expenses"),
        "net_cash_flow": (cashflows, "net_cf"),
        "account_value": (account_values, "av_pp"),
        "mgsv": (account_values, "mgsv_pp"),
    }
    metrics = {}
    worst = {"absolute_difference": -1.0}
    for name, (frame, column) in mapping.items():
        differences = [
            abs(float(frame.iloc[month][column]) - mentor_rows[month][name])
            for month in range(len(mentor_rows))
        ]
        maximum = max(differences)
        month = differences.index(maximum)
        metrics[name] = {"max_absolute_difference": maximum, "month_zero_based": month}
        if maximum > worst["absolute_difference"]:
            worst = {
                "metric": name,
                "month_zero_based": month,
                "absolute_difference": maximum,
                "mentoramp": mentor_rows[month][name],
                "lifelib": float(frame.iloc[month][column]),
            }
    return {"metrics": metrics, "worst": worst, "passed": worst["absolute_difference"] <= THRESHOLD}


def main() -> int:
    args = _arguments()
    repository = args.repository.resolve()
    backend = repository / "backend"
    if not backend.is_dir() and (repository / "app").is_dir():
        backend = repository
    sys.path.insert(0, str(backend))
    from app.products.myga_lite import config as myga  # noqa: PLC0415
    import modelx as mx  # noqa: PLC0415

    source = args.reference_product.resolve()
    if not (source / "MYGA_US_S" / "_system.json").is_file():
        raise SystemExit(f"Not a LifeLib fixed_deferred_annuity product folder: {source}")
    actual_reference_commit = _reference_commit(source)
    if actual_reference_commit != REFERENCE_COMMIT:
        raise SystemExit(
            f"LifeLib checkout is {actual_reference_commit}; required {REFERENCE_COMMIT}"
        )
    args.scratch.mkdir(parents=True, exist_ok=True)
    report = {
        "reference_commit": actual_reference_commit,
        "reference_commit_verified": True,
        "threshold": THRESHOLD,
        "reference_only": True,
        "comparison_scope": "native declarative projection engine; aligned external mortality and factor tables",
        "reference_input_sha256": {
            name: hashlib.sha256((source / name).read_bytes()).hexdigest()
            for name in ("mort_table.csv", "surr_charge_table.csv", "surr_charge_age_cap.csv")
        },
        "scenarios": {},
    }
    with tempfile.TemporaryDirectory(prefix="myga-oracle-", dir=args.scratch) as temporary:
        temporary_root = Path(temporary)
        for scenario_name, values in SCENARIOS.items():
            product = temporary_root / scenario_name.replace(" ", "_")
            shutil.copytree(source, product)
            _set_scenario(product, values)
            model = mx.read_model(product / "MYGA_US_S")
            try:
                reference = model.Projection[1]
                mentor_rows = _mentor_projection(myga, product, values)
                report["scenarios"][scenario_name] = _compare(reference, mentor_rows)
            finally:
                model.close()
    report["passed"] = all(row["passed"] for row in report["scenarios"].values())
    output = args.scratch / "lifelib_crosscheck_myga.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"report: {output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
