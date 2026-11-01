"""Join historical narrative phases to frozen daily observations, without trading.

The phase labels are retrospective research annotations. They must never be
joined into an entry signal as if they had been known at the phase start.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts/strategy-search"
OUT = BASE / "market-phase-atlas-v1"
DAILY_INPUTS = [
    BASE / "market-state-panel-v1/daily.jsonl.gz",
    BASE / "supplemental-market-state-panel-v1/daily.jsonl.gz",
]


def read_json(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8-sig") as stream:
        return json.load(stream)


def write_json(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def identity(path):
    return {"path": path.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def load_daily():
    rows = []
    for path in DAILY_INPUTS:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                raw = json.loads(line)
                price, amount = raw["price20"], raw["amount"]
                numerator, denominator = price["positiveCount"], price["evaluableCount"]
                assert denominator > 0
                breadth = numerator / denominator
                assert abs(breadth - price["positiveReturn20Breadth"]) < 1e-12
                assert numerator + price["zeroCount"] + price["negativeCount"] == denominator
                rows.append({
                    "date": raw["date"], "positiveCount20": numerator,
                    "evaluableCount20": denominator, "fixedPopulation": raw["fixedPopulation"],
                    "breadth20Pct": breadth * 100,
                    "medianStockReturn20Pct": price["medianAbsoluteReturn20"] * 100,
                    "coverage20Pct": price["coverage"] * 100,
                    "medianAmountRatio20": amount["medianRelativeToOwnPrior20"],
                    "matchedBreadthChange5Pp": raw["matched5"]["matchedBreadthChange5"],
                    "persistentStrongCount": raw["qCounts"]["true"],
                    "persistentStrongUnknownCount": raw["qCounts"]["unknown"],
                })
    rows.sort(key=lambda row: row["date"])
    assert len({row["date"] for row in rows}) == len(rows)
    assert {row["fixedPopulation"] for row in rows} == {3141}
    return rows


def describe_days(rows):
    """Summarize daily measures, not average security returns over the phase."""
    assert rows
    lo = min(rows, key=lambda r: r["breadth20Pct"])
    hi = max(rows, key=lambda r: r["breadth20Pct"])
    return {
        "observedStart": rows[0]["date"], "observedEnd": rows[-1]["date"],
        "tradingDays": len(rows),
        "breadth20MedianPct": statistics.median(r["breadth20Pct"] for r in rows),
        "breadth20StartPct": rows[0]["breadth20Pct"],
        "breadth20EndPct": rows[-1]["breadth20Pct"],
        "breadth20Minimum": {"date": lo["date"], "pct": lo["breadth20Pct"]},
        "breadth20Maximum": {"date": hi["date"], "pct": hi["breadth20Pct"]},
        "medianDailyAmountRatio20": statistics.median(r["medianAmountRatio20"] for r in rows),
        "minimumCoverage20Pct": min(r["coverage20Pct"] for r in rows),
    }


def strategy_attribution(phases):
    paths = {
        "contractionNoProgress5": BASE / "contraction-policy-screen-v1/noProgress5.result.json.gz",
        "contractionPressureNoProgress5": BASE / "force-context-screen-v1/pressure.result.json.gz",
    }
    result = {}
    for key, path in paths.items():
        raw = read_json(path)
        entries = {}
        for fill in raw["trades"]:
            if fill["side"] != "buy":
                continue
            entry_key = (fill["symbol"], fill["date"])
            assert entry_key not in entries, "Multiple buys need lot-aware attribution"
            entries[entry_key] = fill["signalDate"]
        groups = defaultdict(list)
        for trade in raw["closedTrades"]:
            signal_date = entries[(trade["symbol"], trade["entryDate"])]
            matched = [p for p in phases if p["startDate"] <= signal_date <= p["endDate"]]
            assert len(matched) == 1, (signal_date, matched)
            groups[matched[0]["id"]].append(dict(trade, signalDate=signal_date))
        open_groups = defaultdict(int)
        for position in raw["positions"]:
            signal_date = entries[(position["symbol"], position["entryDate"])]
            matched = [p for p in phases if p["startDate"] <= signal_date <= p["endDate"]]
            assert len(matched) == 1
            open_groups[matched[0]["id"]] += 1
        summaries = []
        for phase in phases:
            if phase["startDate"] > raw["config"]["endDate"]:
                continue
            trades = groups[phase["id"]]
            values = [t["returnPct"] for t in trades]
            wins, losses = [v for v in values if v > 0], [v for v in values if v < 0]
            summaries.append({
                "phaseId": phase["id"], "phaseTitle": phase["title"], "closedTrades": len(values),
                "openPositionsExcluded": open_groups[phase["id"]],
                "meanNetTradeReturnPct": statistics.mean(values) if values else None,
                "medianNetTradeReturnPct": statistics.median(values) if values else None,
                "winCount": len(wins), "lossCount": len(losses),
                "meanWinnerPct": statistics.mean(wins) if wins else None,
                "meanLoserPct": statistics.mean(losses) if losses else None,
                "closedNetPnlAmount": sum(t["pnlAmount"] for t in trades),
            })
        assert sum(s["closedTrades"] for s in summaries) == len(raw["closedTrades"])
        result[key] = {
            "source": identity(path), "groups": summaries,
            "totalClosedTrades": len(raw["closedTrades"]),
            "openPositionsExcluded": len(raw["positions"]),
            "closedTrades": [t for values in groups.values() for t in values],
            "meaning": "Existing net closed trade outcomes grouped by original signal date. Not phase account returns or a new backtest; phase labels are retrospective and selection-biased.",
        }
    return result


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    daily = load_daily()
    write_json("daily-background.json", daily)
    months = defaultdict(list)
    for row in daily:
        months[row["date"][:7]].append(row)
    write_json("monthly-background.json", [dict(month=month, **describe_days(rows)) for month, rows in months.items()])
    history_paths = [OUT / "history-2022-2023.json", OUT / "history-2024-2025.json"]
    if not all(path.exists() for path in history_paths):
        print(json.dumps({"dailyRows": len(daily), "monthlyRows": len(months), "historyPending": True}))
        return
    histories = [read_json(path) for path in history_paths]
    phases = []
    for history in histories:
        phases.extend(history["phases"])
    phases.sort(key=lambda p: p["startDate"])
    for i, phase in enumerate(phases):
        phase.setdefault("id", f"phase-{i+1:02d}")
        assert phase["startDate"] <= phase["endDate"]
        if i:
            assert phases[i-1]["endDate"] < phase["startDate"], "Overlapping narrative phases"
        phase["dailyBackground"] = describe_days([r for r in daily if phase["startDate"] <= r["date"] <= phase["endDate"]])
        phase["labelAvailability"] = "retrospectiveResearchOnly"
    for row in daily:
        assert sum(p["startDate"] <= row["date"] <= p["endDate"] for p in phases) == 1, row["date"]
    assert sum(p["dailyBackground"]["tradingDays"] for p in phases) == len(daily)
    attribution = strategy_attribution(phases)
    write_json("strategy-phase-attribution.json", attribution)
    atlas = {
        "schemaVersion": 1, "generatedAt": datetime.now(timezone.utc).isoformat(),
        "scope": {"startDate": daily[0]["date"], "endDate": daily[-1]["date"], "tradingDays": len(daily), "fixedPopulation": 3141,
                  "observedSymbolPrefixes": {"sh60": 1667, "sz00": 1474}, "boardScope": "Main-board local survivor sample; excludes ChiNext, STAR and Beijing"},
        "sourceIdentities": [identity(path) for path in DAILY_INPUTS + history_paths],
        "definitions": {
            "breadth20Pct": "On each date: positive raw close-to-close returns over 20 trading sessions / evaluable members of the fixed 3,141-symbol sample * 100. Not daily advancing share or whole-A-share breadth.",
            "breadth20MedianPct": "Median of the daily breadth20Pct readings within the narrative phase; not a pooled security-day ratio.",
            "medianDailyAmountRatio20": "Median across phase days of the cross-sectional median of today's turnover amount / own preceding 20-session mean amount.",
            "phaseBoundaries": "Retrospective broad narrative boundaries from dated historical reports; approximate transitions, not automatically detected trading states.",
        },
        "limitations": [
            "Fixed current-survivor main-board sample (1667 sh60 and 1474 sz00 symbols); excludes ChiNext, STAR and Beijing. Historical full-market membership and historical industry/theme classification are not established.",
            "Raw prices and available observations are sufficient for broad exploratory context, not an investable total-return index.",
            "Representative names are historical attention/leadership examples from reports; they are not an exhaustive return ranking or proof of causal market influence.",
            "Theme and sentiment annotations may have become knowable after the phase start. They must not be used as contemporaneous strategy inputs.",
            "2024–2025 history has already been inspected in prior research; it is not an untouched holdout.",
            "This atlas does not certify a stable profitable strategy, controlled ownership, or a high probability of any target return.",
        ],
        "phases": phases,
        "nextExperiment": {
            "objective": "Test whether causal market breadth, leadership persistence and observable pressure distinguish repeatable small-loss/large-win contexts.",
            "rules": [
                "Freeze the existing contraction plus pressure-context candidate and its exits before slicing performance.",
                "At signal close T use only observations available through T; retain source publication/availability dates for external annotations.",
                "Compare pressure features against direction-only and no-pressure baselines under identical eligibility, costs, sizing and exits.",
                "Report every eligible period, failed signals and open outcomes; do not select only named historical winners.",
                "Evaluate signal distributions, net expectancy, loss tails, account drawdown and temporal concentration without a preset average-winner or +30% gate.",
                "Treat new state cutoffs developed on this atlas as exploratory; use forward observation or genuinely later frozen periods for confirmation.",
            ],
        },
        "newStrategyTrials": 0, "certified": False, "targetAchieved": False,
    }
    write_json("atlas.json", atlas)
    print(json.dumps({"dailyRows": len(daily), "monthlyRows": len(months), "phases": len(phases), "attributedTrades": {k: v["totalClosedTrades"] for k,v in attribution.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
