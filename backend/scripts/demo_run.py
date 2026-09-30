"""M1 boss demo: run the SPIA illustrative Projection Set end to end through the real API.

Steps: system status → project → model → Projection Set → submit Run Set (Base + Low Interest
Rate) → watch progress → reserves by year → comparison → trace of one value → manifest.

Usage (from the repository root):
  # against a running server (uvicorn app.main:app --port 8000, from backend/):
  backend/.venv/Scripts/python backend/scripts/demo_run.py --base-url http://localhost:8000/v1
  # or in-process (no server needed; uses the same app and database):
  backend/.venv/Scripts/python backend/scripts/demo_run.py --in-process
"""

import argparse
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

PROJECT_NAME = "MentorAmp Demo — SPIA (Illustrative)"
TERMINAL = {"success", "partial_success", "failed", "cancelled"}


CONSOLE_SAFE = str.maketrans({"−": "-", "Σ": "sum ", "—": "-", "×": "x"})


def say(text: str = "") -> None:
    """Print text that any console can show (Windows cp1252 cannot print some math symbols)."""
    text = text.translate(CONSOLE_SAFE)
    encoding = sys.stdout.encoding or "utf-8"
    print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def money(value) -> str:
    return "—" if value is None else f"${value:,.2f}"


def make_client(args):
    if args.in_process:
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        prefix = "/v1"
    else:
        import httpx

        client = httpx.Client(timeout=120)
        prefix = args.base_url.rstrip("/")

    def call(method: str, path: str, **kwargs):
        response = client.request(method, f"{prefix}{path}", **kwargs)
        if response.status_code >= 400:
            raise SystemExit(f"{method} {path} -> {response.status_code}: {response.text}")
        return response.json()

    return call


def print_tree(node: dict, indent: str = "") -> None:
    value = node["value"]
    shown = f"{value:,.6f}" if isinstance(value, float) else value
    line = f"{indent}{node['variable']} = {shown} {node.get('unit') or ''}".rstrip()
    if node.get("formula") and node["formula"].get("expression_text"):
        line += f"    [{node['formula']['expression_text']}]"
    source = node.get("source") or {}
    if source.get("type") in ("assumption", "factor"):
        line += f"    <- {source.get('table')} {source.get('lookup_keys')} . {source.get('column')}"
    elif source.get("type") == "input":
        line += f"    <- inforce column '{source.get('column')}' of {source.get('lookup_keys')}"
    elif source.get("type") == "prior_output":
        line += f"    <- {source.get('of_variable')} at month {source.get('of_month')}"
    elif source.get("type") == "context":
        line += f"    <- run context '{source.get('field')}'"
    if node.get("scenario_override"):
        override = node["scenario_override"]
        line += f"    (scenario '{override['scenario']}': {override['operation']} {override['value']})"
    say(line)
    for child in node.get("children", []):
        print_tree(child, indent + "    ")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000/v1")
    parser.add_argument("--in-process", action="store_true")
    parser.add_argument("--years", type=int, default=10, help="Years of reserves to print")
    args = parser.parse_args()
    call = make_client(args)

    status = call("GET", "/system/status")
    say(f"System: {status['status']} · database {status['database']['status']} "
          f"({status['database']['latency_ms']} ms) · auth {status['auth_mode']} · "
          f"{status['engine']['registered_functions']} formula functions")

    projects = call("GET", "/projects/")["projects"]
    project = next((p for p in projects if p["name"] == PROJECT_NAME), None)
    if project is None:
        raise SystemExit("Demo project not found — run backend/scripts/seed_demo.py first.")
    say(f"\nProject: {project['name']}")

    models = call("GET", f"/projects/{project['id']}/models")["models"]
    for model in models:
        version = model["current_version"]
        say(f"Model:   {model['name']} {version['version_label']} · {model['product_code']} · "
              f"basis {version['basis']} · illustrative={version['illustrative']}")

    projection_sets = call("GET", f"/projects/{project['id']}/projection-sets")["projection_sets"]
    projection_set = projection_sets[0]
    say(f"Projection Set: {projection_set['name']} ({projection_set['status']}) · "
          f"{projection_set['counts']['policy_count']} policies · {projection_set['horizon_months']} months "
          f"from {projection_set['valuation_date']} · scenarios "
          f"{', '.join(s['name'] for s in projection_set['scenarios'])}")

    say("\nSubmitting Run Set …")
    started = time.perf_counter()
    submitted = call("POST", "/run-sets", json={
        "project_id": project["id"],
        "name": "Boss demo — Base vs Low Interest Rate",
        "projection_set_ids": [projection_set["id"]],
    })
    run_set_id = submitted["run_set"]["id"]
    while True:
        run_set = call("GET", f"/run-sets/{run_set_id}")
        progress = " | ".join(
            f"{run['scenario']['name']}: {run['status']} {run['progress']['percent']}%"
            for run in run_set["runs"]
        )
        say(f"  {run_set['status']:16} {progress}")
        if all(run["status"] in TERMINAL for run in run_set["runs"]):
            break
        time.sleep(2)
    say(f"Finished in {time.perf_counter() - started:.1f} s")

    runs = {run["scenario"]["name"]: run for run in run_set["runs"]}
    for name, run in runs.items():
        summary = call("GET", f"/runs/{run['id']}/summary")
        headline = summary["headline"] or {}
        say(f"\n== {name} ==  status {summary['status']} · {summary['policy_count']} policies · "
              f"{summary['output_row_count']} output rows · warnings {summary['warning_count']}")
        say(f"   {headline.get('label')}: {money(headline.get('value'))}")
        aggregates = call("GET", f"/runs/{run['id']}/aggregates",
                          params={"variables": "reserve,expected_payment"})
        say(f"   {'Year':<18}{'Reserve':>20}{'Expected payments':>22}{'Reserve change':>16}")
        for row in aggregates["rows"][: args.years + 1]:
            change = row["change_pct"]["reserve"]
            say(f"   {row['period_label']:<18}{money(row['values']['reserve']):>20}"
                  f"{money(row['values']['expected_payment']):>22}"
                  f"{('' if change is None else f'{change:+.2f}%'):>16}")

    if "Base" in runs and "Low Interest Rate" in runs:
        comparison = call("GET", "/comparisons", params={
            "baseline_run_id": runs["Base"]["id"], "current_run_id": runs["Low Interest Rate"]["id"],
        })
        say("\n== Comparison: Base vs Low Interest Rate ==")
        for change in comparison["changed_inputs"]:
            say(f"   changed input: {change['variable']} {change['baseline']} -> {change['current']}")
        for total in comparison["totals"]:
            pct = total["difference_pct"]
            say(f"   {total['label']:<32} {money(total['baseline']):>18} -> {money(total['current']):>18}"
                  f"   ({'' if pct is None else f'{pct:+.2f}%'})")
        for driver in comparison["attribution"]["drivers"]:
            say(f"   driver: {driver['label']} explains {money(driver['amount'])}")

    base = runs.get("Base") or next(iter(runs.values()))
    trace = call("GET", f"/runs/{base['id']}/trace", params={
        "policy_id": "SPIA-0001", "month": 12, "variable": "pv_expected_payment", "depth": 5,
    })
    say(f"\n== Trace: SPIA-0001, month 12 ({trace['period_end_date']}), scenario {trace['scenario']['name']} ==")
    print_tree(trace["root"], "   ")

    manifest = call("GET", f"/runs/{base['id']}/manifest")
    say(f"\nManifest fingerprint: {manifest['fingerprint']}")
    say("All values above are ILLUSTRATIVE — not actuarially approved.")


if __name__ == "__main__":
    main()
