"""Compare dated public membership observations with the frozen local sample.

Read-only offline coverage audit; do not select a strategy or compute returns.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts/strategy-search"
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("_coverage_source", ROOT / "scripts/run-strategy-search.py")
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    supplement = BASE / "evidence-supplement-v1"
    fetch = read(supplement / "manifest.json")
    registration = read(supplement / "protocol.json")
    baseline_path = BASE / "signal-baseline-v1/snapshot.json"
    baseline = read(baseline_path)
    selected = set(baseline["symbols"])
    current_files = set(source.discover_symbols(BASE / "source-workspace"))
    exclusions = {item["symbol"]: item["reason"] for item in baseline["coverage"].get("exclusions", [])}
    failure_rows = baseline["coverage"].get("failures", [])
    failures = {item["symbol"]: item["reason"] for item in failure_rows if item.get("symbol")}
    rows, missing_union, member_union = [], set(), set()
    code_regex = re.compile(registration["selection"]["codeRegex"])
    for request in fetch["requests"]:
        if request["api"] != "query_all_stock":
            continue
        path = supplement / request["path"]
        if sha(path) != request["sha256"] or request.get("complete") is not True:
            raise ValueError("Dated membership response is changed or incomplete")
        payload = read(path)
        members = {row["code"].replace(".", ""): row for values in payload["rows"]
                   for row in [dict(zip(payload["fields"], values))] if code_regex.fullmatch(row["code"])}
        missing = sorted(set(members) - selected)
        missing_union.update(missing)
        member_union.update(members)
        details = []
        for symbol in missing:
            reason = ("currentLocalDailyFileAbsent" if symbol not in current_files else
                      exclusions.get(symbol) or failures.get(symbol) or "notExplainedByFrozenExclusions")
            details.append({"symbol": symbol, "reportedName": members[symbol].get("code_name"),
                "reportedTradeStatus": members[symbol].get("tradeStatus"), "localCoverageReason": reason,
                "delistingCauseProven": False})
        rows.append({"date": payload["parameters"]["day"], "providerMainBoardCount": len(members),
            "presentInFrozenSampleCount": len(set(members) & selected), "missingFromFrozenSampleCount": len(missing),
            "missingFraction": len(missing) / len(members), "missing": details,
            "missingReasonCounts": dict(Counter(item["localCoverageReason"] for item in details)),
            "frozenSampleNotInThisSnapshotCount": len(selected - set(members)),
            "membershipResponseSha256": request["sha256"]})
    result = {"schemaVersion": 1, "executedAt": datetime.now(timezone.utc).isoformat(),
        "scriptSha256": sha(Path(__file__)), "baselineSnapshotSha256": sha(baseline_path),
        "baselineFingerprint": baseline["fingerprint"], "frozenLocalSampleCount": len(selected),
        "observedMembershipUnionCount": len(member_union), "missingUnionCount": len(missing_union),
        "missingUnionSymbols": sorted(missing_union), "currentFileEnumerationCount": len(current_files),
        "dailySnapshots": rows, "returnsCalculated": False, "certified": False,
        "limitations": ["Four query-date snapshots do not prove complete daily historical membership.",
            "A missing current file is not itself proof of delisting; no economic outcomes or direction of bias inferred.",
            "Coverage complete in the original local snapshot refers to enumerated present-day local files, not historical market coverage.",
            "Do not replace the registered trial sample after observing results; expansion needs a new data version and trial registration."]}
    path = BASE / "historical-universe-coverage-v1.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"missingUnionCount": len(missing_union), "snapshots": [{key: row[key] for key in (
        "date", "providerMainBoardCount", "presentInFrozenSampleCount", "missingFromFrozenSampleCount", "missingReasonCounts")}
        for row in rows]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
