"""Run the six preregistered independent cohort groups, at most three at a time."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/volume-lead-breakout-v1"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run_group(group):
    slug = group.replace("/", "-")
    argv = [sys.executable, "-X", "utf8", "-B", "scripts/run-volume-lead-breakout.py", "run", "--group", group]
    started = datetime.now(timezone.utc).isoformat()
    with (OUT / "execution-logs" / (slug + ".stdout.log")).open("x", encoding="utf-8") as stdout:
        with (OUT / "execution-logs" / (slug + ".stderr.log")).open("x", encoding="utf-8") as stderr:
            result = subprocess.run(argv, cwd=ROOT, stdout=stdout, stderr=stderr)
    row = {"group": group, "argv": argv, "startedAt": started,
           "completedAt": datetime.now(timezone.utc).isoformat(), "exitCode": result.returncode}
    print(json.dumps(row, ensure_ascii=False), flush=True)
    return row


def main():
    plan = json.loads((OUT / "plan.json").read_text("utf-8"))
    audit = json.loads((OUT / "input-validation.json").read_text("utf-8"))
    assert audit["passed"] and audit["planSha256"] == sha(OUT / "plan.json")
    assert plan["sourcePins"]["scripts/execute-volume-lead-breakout.py"] == sha(Path(__file__))
    directory = OUT / "execution-logs"
    directory.mkdir(exist_ok=False)
    start = {"startedAt": datetime.now(timezone.utc).isoformat(), "driverSha256": sha(Path(__file__)),
             "planSha256": sha(OUT / "plan.json"), "inputValidationSha256": sha(OUT / "input-validation.json"),
             "maxConcurrentGroups": 3, "automaticRetries": 0}
    (directory / "driver-start.json").write_text(json.dumps(start, indent=2) + "\n", encoding="utf-8")
    groups = [p + "/" + b for p in ("2022-2023", "2024-2025") for b in ("main", "chinext", "star")]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = [f.result() for f in as_completed([pool.submit(run_group, group) for group in groups])]
    receipt = {**start, "completedAt": datetime.now(timezone.utc).isoformat(),
               "groups": sorted(results, key=lambda row: row["group"]),
               "passed": all(row["exitCode"] == 0 for row in results)}
    (directory / "driver-completion.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    if not receipt["passed"]:
        raise RuntimeError("A group failed; preserve logs and inspect before any recovery")
    subprocess.run([sys.executable, "-X", "utf8", "-B", "scripts/run-volume-lead-breakout.py", "collect"],
                   cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
