"""Read-only local strategy validation, writing only to the chosen output folder.

Example: python -B scripts/validate-research.py --workspace <A-trade workspace>
  --symbols sh600000 sh600036 sh600519 sz000001 sz000333 sz000651
No source data or local source paths are printed. Results are screening evidence,
not a total-return estimate or a recommendation to buy the selected securities.
"""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from service.research_service import snapshot_local
from service.backtest_engine import BacktestEngine
from service.plateau import PlateauRunner
from service.strategies import catalogue


def run(runner):
    while not runner.step(days=100):
        pass
    return runner.result()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--output", default="artifacts/research-validation")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    data = snapshot_local(Path(args.workspace).resolve(), args.symbols, args.end)
    results = []
    for strategy in catalogue():
        config = {"strategyIds": [strategy["id"]], "startDate": args.start, "endDate": args.end,
                  "partialTakeProfitPct": 0}
        started = time.monotonic()
        baseline = run(BacktestEngine(data, config))
        assert all(t["signalDate"] < t["date"] for t in baseline["trades"])
        assert all(point["cash"] >= -1e-6 for point in baseline["curve"])
        scan = {"axes": [{"path": "stopLossPct", "values": [.04, .06, .08]},
                         {"path": "maxHoldingDays", "values": [20, 40, 60]}],
                "holdoutFraction": .2, "folds": 3, "minTrades": 3, "maxDrawdownPct": 30}
        plateau = run(PlateauRunner(data, config, scan))
        report = {"strategyId": strategy["id"], "elapsedSeconds": round(time.monotonic()-started, 3),
                  "baseline": baseline, "plateau": plateau}
        (output / (strategy["id"] + ".json")).write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
        summary = {"strategyId": strategy["id"], "elapsedSeconds": report["elapsedSeconds"],
                   "metrics": baseline["metrics"], "quality": baseline["quality"],
                   "regions": len(plateau["regions"]), "recommendation": plateau["recommendation"],
                   "validationPassed": (plateau.get("validation") or {}).get("passed"),
                   "trialCount": plateau["trialCount"], "split": plateau["split"],
                   "warnings": plateau["warnings"]}
        results.append(summary)
        print(json.dumps({"strategy": strategy["id"], "closedTrades": baseline["metrics"]["closedTradeCount"],
                          "returnPct": baseline["metrics"]["totalReturnPct"], "regions": len(plateau["regions"]),
                          "validationPassed": summary["validationPassed"], "seconds": report["elapsedSeconds"]}, ensure_ascii=False), flush=True)
    summary = {"datasetFingerprint": data["fingerprint"], "symbols": args.symbols,
               "selection": "Explicit illustrative current-stock basket; not a historical unbiased universe.",
               "dataWarnings": data["warnings"], "results": results}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
