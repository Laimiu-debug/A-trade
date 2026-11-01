"""Observed price-path risk alongside frozen net-target proxy events.

The -10/-20 percent levels describe raw lows relative to entry fill. They are
neither net liquidation values nor stop orders. No entry/exit is changed.
"""
from __future__ import annotations

from collections import Counter
from statistics import median

from .signal_target_study import SignalTargetStudy, HALTED

VERSION = "signal-path-risk-observations-v1"
BOUNDARIES = (-10, -20)


class PathRiskStudy:
    def __init__(self, dataset, config):
        self.study = SignalTargetStudy(dataset, config)

    def evaluate(self, event):
        if event["entry"]["status"] != "filled":
            raise ValueError("Path observations require a filled proxy event")
        study = self.study
        symbol = event["symbol"]
        security = study.securities[symbol]
        bars = study.by_date[symbol]
        entry = event["entry"]
        start = study.calendar_index[entry["date"]]
        hit = event["outcomes"]["nextOpenNet30"]
        through = hit["hitDate"] or study.end_date
        if event["activeUntil"] != through:
            raise ValueError("Frozen event observation cutoff does not match")
        if hit["hitDate"] and (study.calendar_index[through] - start + 1 != hit["tradingDays"]):
            raise ValueError("Frozen target date/offset does not match calendar")
        first = {str(level): None for level in BOUNDARIES}
        gaps = []
        mae = 0.
        for offset, day in enumerate(study.calendar[start:], 1):
            if day > through:
                break
            # The frozen open exit precedes any later daily low. Never look at
            # exit-day or post-exit lows, even when those are extreme.
            if day == hit["hitDate"]:
                low_return = (event["terminal"]["price"] / entry["price"] - 1) * 100
            else:
                bar_index = bars.get(day)
                bar = study.bars[symbol][bar_index] if bar_index is not None else None
                if security.get("tradeStatusByDate", {}).get(day) in HALTED:
                    continue
                if bar is None or bar["volume"] <= 0:
                    gaps.append({"date": day, "tradingDay": offset,
                                 "reason": "missingBar" if bar is None else "unexplainedZeroVolume"})
                    continue
                low_return = (bar["low"] / entry["price"] - 1) * 100
            mae = min(mae, low_return)
            for level in BOUNDARIES:
                if first[str(level)] is None and low_return <= level + 1e-10:
                    first[str(level)] = {"date": day, "tradingDay": offset,
                                         "entryDayTPlusOneLocked": offset == 1}
        # Independent traversal must agree with the already-frozen gross MAE.
        if abs(mae - event["excursions"]["maePct"]) > 1e-8:
            raise ValueError("Independent raw-price MAE differs from frozen event")
        issues = event["quality"]["executionCapacityIssues"]
        invalid_offsets = [item.get("tradingDay", 1) for item in issues]
        if entry.get("executionValid") is not True:
            invalid_offsets.append(1)
        return {"eventId": event["eventId"], "strategyId": event["strategyId"], "symbol": symbol,
                "entryDate": entry["date"], "targetDate": hit["hitDate"],
                "targetTradingDays": hit["tradingDays"], "throughDate": through,
                "potentialFollowupSessions": len(study.calendar) - start,
                "observedFollowupSessions": study.calendar_index[through] - start + 1,
                "firstObservedBoundary": first, "missing": gaps,
                "invalidExecutionOffsets": sorted(set(invalid_offsets)), "grossMaePct": mae,
                "terminal": event["terminal"], "certified": False}


def classify(row, boundary, horizon=None):
    """Conservative classification: unresolved path data stays in denominator."""
    if boundary not in BOUNDARIES:
        raise ValueError("Only fixed descriptive boundaries are supported")
    stop = min(horizon or row["potentialFollowupSessions"], row["observedFollowupSessions"])
    if any(gap["tradingDay"] <= stop for gap in row["missing"]) or any(
            offset <= stop for offset in row["invalidExecutionOffsets"]):
        return "dataUnresolved"
    target = row["targetTradingDays"]
    target = target if target is not None and target <= stop else None
    risk = row["firstObservedBoundary"][str(boundary)]
    risk = risk["tradingDay"] if risk is not None and risk["tradingDay"] <= stop else None
    if target is not None:
        # A completed open exit has no subsequent daily range. A tie should
        # never be manufactured from that day's low.
        if risk is not None and risk >= target:
            raise ValueError("Risk observation cannot follow a frozen target exit")
        return "boundaryThenTarget" if risk is not None else "targetWithoutObservedBoundary"
    return "boundaryStillWaiting" if risk is not None else "neitherObserved"


def summarize_paths(rows, horizons=(10, 60, 120, 250)):
    summaries = []
    categories = ("targetWithoutObservedBoundary", "boundaryThenTarget", "boundaryStillWaiting",
                  "neitherObserved", "dataUnresolved")
    for group in sorted({row["strategyId"] for row in rows}):
        selected = [row for row in rows if row["strategyId"] == group]
        waiting = [row for row in selected if row["targetDate"] is None]
        marks = [row["terminal"].get("netMarkedReturnPct") for row in waiting]
        marks = [value for value in marks if value is not None]
        observations = []
        for horizon in (None, *horizons):
            cohort = [row for row in selected if horizon is None or row["potentialFollowupSessions"] >= horizon]
            for boundary in BOUNDARIES:
                counts = Counter(classify(row, boundary, horizon) for row in cohort)
                successes = counts["targetWithoutObservedBoundary"]
                unresolved = counts["dataUnresolved"]
                observations.append({"horizonSessions": horizon, "rawPriceBoundaryPct": boundary,
                    "cohortCount": len(cohort), "insufficientPotentialFollowupCount": len(selected) - len(cohort),
                    "counts": {key: counts[key] for key in categories},
                    "targetWithoutBoundaryMissingOutcomeBounds": {
                        "lower": successes / len(cohort) if cohort else None,
                        "upper": (successes + unresolved) / len(cohort) if cohort else None,
                        "meaning": "observed-by-cutoff bounds;not-confidence-or-eventual-success-probability"}})
        summaries.append({"strategyId": group, "filledCount": len(selected),
            "stillWaitingCount": len(waiting),
            "waitingMedianSessions": median(row["observedFollowupSessions"] for row in waiting) if waiting else None,
            "waitingRawMarkedLossCount": sum(value < 0 for value in marks),
            "waitingMedianNetMarkedReturnPct": median(marks) if marks else None,
            "waitingMarkMeaning": "raw-price mark less entry costs;no sale fees or share/cash entitlements;may be stale",
            "observations": observations})
    return {"version": VERSION, "groups": summaries, "targetAchieved": False, "certified": False,
        "definitions": {"boundary": "daily raw low relative to entry fill;entry day included;not executable stop",
            "target": "existing frozen full-position net opening price proxy;no event re-selection",
            "deadline": "none;horizons are comparison scales;ongoing events are right censored",
            "ordering": "main target exits at open;subsequent lows excluded;intraday target ordering not inferred",
            "data": "unresolved rows retained;unknown rights and opening execution remain uncertified",
            "cohort": "all filled events with sufficient potential calendar followup, including early hits",
            "statistics": "development descriptive counts;overlapping symbols and market days are dependent"}}
