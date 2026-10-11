"""Freeze a liquidity-stratified local universe before inspecting outcomes.

Reads only the configured TDX source. Never repairs or writes market.sqlite3.
The selection is reproducible but local-file availability is not a historical
listing archive; missing/delisted securities remain an explicit limitation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from statistics import median
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from service import tdx
from service.backtest_engine import BacktestEngine
from service.plateau import PlateauRunner

SEED = "A-trade-force-study-2022-liquidity-v1"


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")


def configured_root(workspace):
    path = Path(workspace).resolve() / "market.sqlite3"
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT value FROM meta WHERE key='path'").fetchone()
    if not row:
        raise ValueError("No configured source")
    return tdx.normalize_directory(json.loads(row[0]))


def select_universe(root, cutoff="2022-12-30", count=50):
    if count % 5 or not 5 <= count <= 50:
        raise ValueError("count must be a multiple of five in 5..50")
    candidates, failures = [], []
    cutoff_number = int(cutoff.replace("-", ""))
    for market in ("sh", "sz"):
        for path in sorted((root / "vipdoc" / market / "lday").glob("*.day")):
            symbol = path.stem
            try:
                board, code = tdx.symbol_parts(symbol)
                if tdx.security_kind(board, code) != "stock":
                    continue
                data = tdx.read_stable(tdx.within(path, root))
                tdx.validate_size(len(data))
                # Selection uses only records available on/before cutoff.
                prefix = []
                for row in tdx.DAY.iter_unpack(data):
                    if row[0] > cutoff_number:
                        break
                    prefix.append(row)
                if len(prefix) < 120 or prefix[-1][0] != cutoff_number:
                    continue
                sample = prefix[-60:]
                if any(row[6] <= 0 for row in sample):
                    continue
                turnover = median(row[5] for row in sample)
                if turnover <= 0:
                    continue
                candidates.append({"symbol": symbol, "medianAmount60": turnover,
                                   "preCutoffBars": len(prefix)})
            except (ValueError, OSError) as error:
                failures.append({"symbol": symbol, "reason": str(error)})
    candidates.sort(key=lambda item: (item["medianAmount60"], item["symbol"]))
    selected = []
    for group in range(5):
        bucket = candidates[len(candidates)*group//5:len(candidates)*(group+1)//5]
        ranked = sorted(bucket, key=lambda item: hashlib.sha256((SEED+item["symbol"]).encode()).hexdigest())
        if len(ranked) < count//5:
            raise ValueError("Insufficient pre-period universe")
        selected += [{**item, "liquidityGroup": group+1} for item in ranked[:count//5]]
    return {"seed": SEED, "cutoff": cutoff, "eligibleCount": len(candidates), "selected": selected,
            "selectionFailures": failures,
            "method": "Five equal-count strata of preceding 60 sessions median amount, fixed SHA256 order within each stratum; 120 pre-cutoff rows, no zero-volume row in selection window.",
            "limitations": ["Local-file availability may omit delisted names; this is not a complete historical universe.",
                            "No removal/replacement based on subsequent returns or validation outcomes."]}


def load_local(root, selection, end):
    names, warnings = tdx.read_name_catalogue(root)
    securities = []
    for item in selection["selected"]:
        symbol = item["symbol"]
        rows = tdx.parse_daily(tdx.read_stable(tdx.daily_path(root, symbol)))
        rows = [row for row in rows if row["date"] <= end]
        name = names.get(symbol, {})
        if isinstance(name, dict):
            name = name.get("name", symbol)
        securities.append({"symbol": symbol, "name": name or symbol, "bars": rows})
    return {"securities": securities, "adjustment": "raw", "source": "Configured TDX files; frozen stratified sample",
            "warnings": selection["limitations"]+warnings, "selection": selection}


def run(runner):
    while not runner.step(days=200):
        pass
    return runner.result()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--output", default="artifacts/expanded-research")
    parser.add_argument("--select-only", action="store_true")
    parser.add_argument("--start", default="2023-01-03")
    parser.add_argument("--end", default="2026-10-09")
    parser.add_argument("--snapshot", help="Optional frozen enriched dataset; symbols must match selection")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    root = configured_root(args.workspace)
    selection_path = output/"selection.json"
    if selection_path.exists():
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
    else:
        selection = select_universe(root)
        save(selection_path, selection)
    print(json.dumps({"eligible": selection["eligibleCount"], "selected": len(selection["selected"]),
                      "cutoff": selection["cutoff"]}), flush=True)
    if args.select_only:
        save(output/"local-snapshot.json", load_local(root, selection, args.end))
        return
    dataset = json.loads(Path(args.snapshot).read_text(encoding="utf-8")) if args.snapshot else load_local(root, selection, args.end)
    if {item["symbol"] for item in dataset["securities"]} != {item["symbol"] for item in selection["selected"]}:
        raise ValueError("Snapshot universe differs from frozen selection")
    dataset["fingerprint"] = hashlib.sha256(json.dumps(dataset, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    plans = [
        ("forceGoldenCross", [{"path": "strategyParams.forceGoldenCross.minDominance", "values": [0, .05, .1]},
                              {"path": "maxHoldingDays", "values": [10, 20, 40]}]),
        ("forceTrend", [{"path": "strategyParams.forceTrend.confirmDays", "values": [2, 3, 5]},
                        {"path": "strategyParams.forceTrend.minDominance", "values": [.05, .1, .2]}]),
    ]
    # Written before the first result; this protocol does not tune on outcomes.
    protocol = {"start": args.start, "end": args.end, "plans": plans, "selectionSeed": SEED,
                "datasetFingerprint": dataset["fingerprint"], "holdoutFraction": .2, "folds": 3,
                "limits": "Prior six-stock same-period history has been inspected; holdout is isolated from this scan, not globally untouched."}
    save(output/"protocol.json", protocol)
    summaries = []
    for strategy, axes in plans:
        config = {"strategyIds": [strategy], "startDate": args.start, "endDate": args.end,
                  "partialTakeProfitPct": 0, "maxHoldingDays": 20}
        started = time.monotonic()
        baseline = run(BacktestEngine(dataset, config))
        save(output/(strategy+"-baseline.json"), baseline)
        scan = {"axes": axes, "holdoutFraction": .2, "folds": 3, "minTrades": 10, "maxDrawdownPct": 30}
        plateau = run(PlateauRunner(dataset, config, scan))
        save(output/(strategy+"-plateau.json"), plateau)
        summary = {"strategy": strategy, "metrics": baseline["metrics"], "quality": baseline["quality"],
                   "regions": len(plateau["regions"]), "recommendation": plateau["recommendation"],
                   "validationPassed": (plateau.get("validation") or {}).get("passed"),
                   "split": plateau["split"], "diagnostics": plateau.get("diagnostics"),
                   "seconds": round(time.monotonic()-started, 2)}
        summaries.append(summary)
        save(output/"summary.json", {"protocol": protocol, "results": summaries})
        print(json.dumps({"strategy": strategy, "returnPct": baseline["metrics"]["totalReturnPct"],
                          "closedTrades": baseline["metrics"]["closedTradeCount"], "regions": len(plateau["regions"]),
                          "seconds": summary["seconds"]}), flush=True)


if __name__ == "__main__":
    main()
