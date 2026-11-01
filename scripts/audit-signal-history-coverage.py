#!/usr/bin/env python3
"""Verify saved missing-history batches and write a new offline evidence package."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.signal_history_coverage import (coverage_summary, encoded, load_calendar,
    load_manifest, load_universe_audit, normalize_history_coverage, sha256)

START, END = "2021-08-02", "2023-12-31"
REGISTERED_UNIVERSE_COUNT, REGISTERED_CALENDAR_COUNT = 125, 587
AUDIT = ROOT / "artifacts/strategy-search/historical-universe-coverage-v1.json"
AUDIT_SHA = "51717d281484c3ac71c70c4442ff2d491d3fb062b4257707efe27f151b563961"
CALENDAR = ROOT / "artifacts/strategy-search/provider-probe/2021-2023-calendar/calendar-with-warmup.json"
CALENDAR_SHA = "fc52f4fc74ef1d460c45a4227349b687c4619da4af33b505f478aceb5750119e"


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--manifest", nargs=2, action="append", required=True, metavar=("PATH", "SHA256"),
                        help="Repeat for v1 and each completed v2 batch; pinned byte hash required")
    result.add_argument("--output", type=Path, required=True, help="New, nonexistent output directory")
    result.add_argument("--allow-partial", action="store_true", help="Explicit partial quality report; never certify coverage")
    return result


def execute(args):
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Output exists; evidence must not be overwritten")
    for path, _ in args.manifest:
        parent = Path(path).resolve().parent
        if output == parent or output.is_relative_to(parent) or parent.is_relative_to(output):
            raise ValueError("Output must be independent of source bundle directories")
    audit = load_universe_audit(AUDIT, AUDIT_SHA)
    calendar = load_calendar(CALENDAR, CALENDAR_SHA, start=START, end=END)
    if len(audit["symbols"]) != REGISTERED_UNIVERSE_COUNT or len(calendar["dates"]) != REGISTERED_CALENDAR_COUNT:
        raise ValueError("Unexpected registered universe or calendar size")
    bundles = [load_manifest(path, digest) for path, digest in args.manifest]
    package = normalize_history_coverage(bundles, calendar, audit, start=START, end=END,
                                         require_all=not args.allow_partial)
    summary = coverage_summary(package)
    output.mkdir(parents=True, exist_ok=False)
    files = {}
    for name, value in [("normalized-history.json.gz", package), ("coverage-summary.json", summary)]:
        content = encoded(value)
        if name.endswith(".gz"):
            content = gzip.compress(content, mtime=0)
        else:
            content = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8") + b"\n"
        with (output / name).open("xb") as stream:
            stream.write(content)
        files[name] = {"sha256": sha256(content), "byteLength": len(content)}
    receipt = {"schema": "signal-history-coverage-receipt-v1", "status": "completed",
        "fingerprint": package["fingerprint"], "range": package["range"],
        "includedSecurityCount": package["includedSecurityCount"],
        "partialEvidenceOnly": package["partialEvidenceOnly"], "files": files,
        "code": {str(p.relative_to(ROOT)).replace("\\", "/"): sha256(p.read_bytes())
                 for p in [Path(__file__).resolve(), ROOT / "service/signal_history_coverage.py"]},
        "inputs": {"universeAudit": AUDIT_SHA, "calendar": CALENDAR_SHA,
                   "manifests": [{"bundle": Path(path).parent.name, "file": Path(path).name,
                                  "sha256": digest} for path, digest in args.manifest]},
        "returnsCalculated": False, "certified": False, "targetAchieved": False,
        "sourceFilesModified": False, "userDatabaseOpened": False}
    with (output / "receipt.json").open("xb") as stream:
        stream.write(encoded(receipt) + b"\n")
    return {"output": str(output), "fingerprint": package["fingerprint"],
            "includedSecurityCount": package["includedSecurityCount"], "totals": package["totals"]}


if __name__ == "__main__":
    print(json.dumps(execute(parser().parse_args()), ensure_ascii=False))
