"""Package review evidence and exact committed sources; require a clean worktree."""

import json
from pathlib import Path
import shutil
import subprocess
import zipfile

from alembic.config import Config
from alembic.script import ScriptDirectory
from app.products.fia_lite import config as fia
from app.products.rila_lite import config as rila

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "98039a2"


def main():
    git = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"

    def command(*args):
        return subprocess.check_output([git, *args], cwd=ROOT).decode("utf-8")

    status = command("status", "--short")
    if status.strip():
        raise RuntimeError("Commit the reviewed changes before packaging:\n" + status)
    branch = command("branch", "--show-current").strip()
    if branch != "feat/m4-fia-rila-foundation":
        raise RuntimeError("Review must be packaged from the M4 feature branch")
    evidence = ROOT / ".m4-review" / "evidence"
    if not (evidence / "scenario_preservation.json").is_file():
        raise RuntimeError("Run scripts.review_indexed first")
    log = ROOT / ".m4-review" / "full_pytest.log"
    if not log.is_file():
        raise RuntimeError("Full regression log is required")
    config = Config()
    config.set_main_option("script_location", str(ROOT / "backend/app/db/migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"Expected one Alembic head: {heads}")
    files = command("diff", "--name-only", BASELINE, "HEAD").splitlines()
    metadata = {"status": "IMPLEMENTED, PENDING SENIOR ACTUARIAL RE-REVIEW",
                "branch": branch, "remote_branch": f"origin/{branch}",
                "remote_url": command("remote", "get-url", "origin").strip(),
                "baseline_sha": command("rev-parse", BASELINE).strip(),
                "final_clean_sha": command("rev-parse", "HEAD").strip(),
                "git_status_short": status, "alembic_heads": heads,
                "postgres_migration_verification": "NOT RUN: no disposable PostgreSQL URL supplied; no schema changes",
                "actuarial_status": "PENDING_OWNER_APPROVAL", "changed_files": files,
                "regression_log": "logs/full_pytest.log",
                "limitations": "See docs/m4/IMPLEMENTATION.md; sandbox evidence is not governed actuarial acceptance"}
    destination = ROOT / "noah_m4_fia_rila_review.zip"
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("completion_report.json", json.dumps(metadata, indent=2) + "\n")
        archive.writestr("full.patch", command("diff", "--binary", BASELINE, "HEAD"))
        archive.writestr("data_dictionary.json", json.dumps({p.PRODUCT_CODE: p.VARIABLES for p in (fia, rila)}, indent=2) + "\n")
        archive.write(ROOT / "NOAH_M4_FIA_RILA_IMPLEMENTATION_README.md", "controlling_specification.md")
        archive.write(log, "logs/full_pytest.log")
        for extra in sorted((ROOT / ".m4-review").glob("*.log")):
            if extra != log:
                archive.write(extra, f"logs/{extra.name}")
        for file in files:
            path = ROOT / file
            if path.is_file():
                archive.write(path, f"source/{file}")
        for path in sorted(evidence.rglob("*")):
            if path.is_file():
                archive.write(path, f"evidence/{path.relative_to(evidence).as_posix()}")
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Review archive validation failed")
    print(destination)
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
