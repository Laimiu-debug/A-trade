"""Append a separately registered descriptive audit to completed signal trials."""
from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.portfolio_universe import snapshot_universe
from service.signal_path_risk import PathRiskStudy, summarize_paths

spec = importlib.util.spec_from_file_location("_frozen_signal_runner", ROOT / "scripts/run-signal-target-study.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    base = ROOT / "artifacts/strategy-search"
    output = base / "signal-path-risk-v1"
    inputs = []
    for name in ("signal-baseline-v1", "signal-extensions-v1"):
        directory = base / name
        protocol = read(directory / "protocol.json")
        snapshot = read(directory / "snapshot.json")
        receipt = read(directory / "attempts/attempt-0001/completion.json")
        runner.verify_completion(directory, receipt, receipt["protocolHash"], snapshot["fingerprint"])
        if runner._digest(protocol) != receipt["protocolHash"]:
            raise ValueError("Frozen input protocol hash differs")
        inputs.append({"directory": directory, "protocol": protocol, "snapshot": snapshot, "receipt": receipt})
    if len({item["snapshot"]["fingerprint"] for item in inputs}) != 1:
        raise ValueError("Trials used different snapshots")
    if any(item["protocol"]["signalStart"] != inputs[0]["protocol"]["signalStart"] or
           item["protocol"]["signalEnd"] != inputs[0]["protocol"]["signalEnd"] for item in inputs):
        raise ValueError("Trials used different observation boundaries")
    # The original strategy results have already been inspected. Register this
    # only as a post-hoc development path audit, never an untouched trial.
    output.mkdir(exist_ok=False)
    definition = {"schemaVersion": 1, "kind": "post-hoc-development-path-audit",
        "registeredAt": datetime.now(timezone.utc).isoformat(),
        "frozenTrials": [{"directory": item["directory"].name,
            "protocolHash": item["receipt"]["protocolHash"],
            "resultSha256": item["receipt"]["files"]["result"]["sha256"]} for item in inputs],
        "snapshotFingerprint": inputs[0]["snapshot"]["fingerprint"],
        "rawPriceBoundariesPct": [-10, -20], "comparisonHorizons": [10, 60, 120, 250],
        "meaning": "Existing net opening target versus observed gross-price adverse path;not stop orders or an independent strategy trial",
        "implementationHashes": {str(path.relative_to(ROOT)): digest(path) for path in (
            Path(__file__), ROOT / "service/signal_path_risk.py", ROOT / "tests/test_signal_path_risk.py")},
        "changesEntriesOrExits": False, "certified": False, "targetAchieved": False}
    runner.source_tools.write_json(output / "protocol.json", definition)
    protocol = inputs[0]["protocol"]
    dataset = runner.source_tools.bound_snapshot(snapshot_universe(Path(protocol["workspace"]),
        read(inputs[0]["directory"] / "selection.json"), protocol["signalEnd"], protocol["signalStart"]),
        protocol["signalStart"], protocol["signalEnd"])
    if dataset["fingerprint"] != definition["snapshotFingerprint"]:
        raise ValueError("Source changed since frozen trials; no reanalysis allowed")
    rows = []
    for item in inputs:
        evaluator = PathRiskStudy(dataset, item["protocol"]["config"])
        path = item["directory"] / item["receipt"]["files"]["result"]["path"]
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            events = json.load(stream)["events"]
        count = 0
        for event in events:
            if event["entry"]["status"] == "filled":
                rows.append(evaluator.evaluate(event))
                count += 1
        print(f"Verified raw-price path for {item['directory'].name}: {count} filled proxy events", flush=True)
        del evaluator
    with gzip.open(output / "observations.jsonl.gz", "xt", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    summary = summarize_paths(rows)
    summary.update(eventCount=len(rows), snapshotFingerprint=definition["snapshotFingerprint"],
                   originalMaeReconciledCount=len(rows))
    runner.source_tools.write_json(output / "summary.json", summary)
    runner.source_tools.write_json(output / "completion.json", {
        "files": {name: digest(output / name) for name in ("protocol.json", "observations.jsonl.gz", "summary.json")},
        "completedAt": datetime.now(timezone.utc).isoformat(), "certified": False})
    print(json.dumps({"groups": [{"strategyId": group["strategyId"], "filled": group["filledCount"],
        "waiting": group["stillWaitingCount"], "waitingMedianSessions": group["waitingMedianSessions"],
        "h250risk20": [row for row in group["observations"] if row["horizonSessions"] == 250
                       and row["rawPriceBoundaryPct"] == -20][0]} for group in summary["groups"]
        if not group["strategyId"].startswith("control_")]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
