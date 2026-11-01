"""Frozen constructive-retest entry experiment; past data and T-close only."""
from collections import Counter
import math

from .research_liquid_trend_breakout import build_signals as seed_builder, day, positive, valid

SID = "trendBreakoutRetestV1"
POLICIES = ("immediate", "immediateLiquid", "retestLiquid")


def liquid_state(calendar, index, market):
    days = calendar[max(0, index - 20):index + 1]
    values = [market.get(d) for d in days]
    known = len(values) == 21 and all(positive(v) for v in values)
    prior, current5 = (math.fsum(values[:-1]) / 20, math.fsum(values[-5:]) / 5) if known else (None, None)
    return {"status": "unknown" if not known else "passed" if values[-1] >= 1e12 and current5 >= .8 * prior else "failed",
            "priorMean20Cny": prior, "currentMean5Cny": current5, "totalAmountCny": values[-1] if values else None}


def signal(symbol, date, rank, kind, seed_date=None):
    ident = f"{SID}:{kind}:{date}:{symbol}" + (":" + seed_date if seed_date else "")
    return {"signalId": ident, "strategyId": SID, "symbol": symbol, "signalDate": date, "originalRank": rank}


def build_signals(dataset, market, *, start, end):
    start, end = day(start), day(end)
    adjustment, basis = dataset.get("adjustment"), dataset.get("priceBasisId")
    if (adjustment not in ("raw", "none", "unadjusted") or basis not in (None, "raw-unadjusted") or
            (adjustment != "raw" and basis != "raw-unadjusted") or not dataset.get("calendarSource") or dataset["calendarSource"].startswith("observed")):
        raise ValueError("Explicit raw source and independent calendar required")
    calendar = [d for d in dataset["calendar"] if d <= end]
    if not start <= end or calendar != sorted(set(calendar)) or start not in calendar or end not in calendar:
        raise ValueError("Exact ordered independent target endpoints required")
    indices = {d: i for i, d in enumerate(calendar)}
    seed_start = calendar[max(0, indices[start] - 20)]
    seeds = seed_builder(dataset, market, start=seed_start, end=end)
    targets = [d for d in calendar if start <= d <= end]
    liquid = {d: liquid_state(calendar, indices[d], market) for d in targets}
    schedules = {p: [] for p in POLICIES}
    seed_by_symbol = {}
    for row in seeds["opportunities"]:
        if not row["evidence"]["trendPassed"]:
            continue
        seed_by_symbol.setdefault(row["symbol"], {})[row["signalDate"]] = row
        if row["signalDate"] >= start:
            value = signal(row["symbol"], row["signalDate"], row["originalRank"], "immediate")
            schedules["immediate"].append(value)
            if liquid[row["signalDate"]]["status"] == "passed":
                schedules["immediateLiquid"].append(dict(value))
    retests = {d: [] for d in targets}
    setup_rows, transitions = [], []
    for security in dataset["securities"]:
        symbol = security["symbol"]
        dates = seed_by_symbol.get(symbol, {})
        if not dates:
            continue
        lookup = {b["date"]: b for b in security["bars"] if b["date"] <= end}
        bars = [lookup.get(d) for d in calendar]
        active = None
        for index in range(indices[seed_start], len(calendar)):
            d, bar = calendar[index], bars[index]
            was_active = active is not None
            if active is not None:
                age = index - active["seedIndex"]
                reason = None
                if age > 20:
                    reason = "expired20Sessions"
                elif not valid(bar):
                    reason = "unknownAlignedBar"
                elif bar["close"] < active["level"]:
                    reason = "closeBelowBreakoutLevel"
                elif age >= 3:
                    ma60 = math.fsum(b["close"] for b in bars[index - 59:index + 1]) / 60
                    ma120 = math.fsum(b["close"] for b in bars[index - 119:index + 1]) / 120
                    lag120 = math.fsum(b["close"] for b in bars[index - 139:index - 19]) / 120
                    passed = (bar["low"] <= active["level"] * 1.03 and active["level"] <= bar["close"] <= active["level"] * 1.05
                        and bar["close"] > bar["open"] and bar["close"] > bars[index - 1]["close"]
                        and bar["close"] > ma60 > ma120 > lag120)
                    if passed:
                        reason = "firstConstructiveRetest"
                        if d >= start:
                            amount = math.fsum(b["amount"] for b in bars[index - 20:index]) / 20
                            retests[d].append({"symbol": symbol, "seedDate": active["seedDate"], "date": d,
                                "level": active["level"], "age": age, "priorMeanAmount": amount,
                                "low": bar["low"], "close": bar["close"], "ma60": ma60, "ma120": ma120, "ma120Lag20": lag120})
                if reason is None and age == 20:
                    reason = "expired20Sessions"
                if reason:
                    active.update(status=reason, endedAt=d, ageAtEnd=age)
                    transitions.append({"symbol": symbol, "seedDate": active["seedDate"], "date": d, "reason": reason})
                    active = None
            if d in dates:
                row = dates[d]
                if was_active:
                    transitions.append({"symbol": symbol, "seedDate": d, "date": d, "reason": "newSeedIgnoredWhileActive"})
                else:
                    active = {"symbol": symbol, "seedDate": d, "seedIndex": index,
                        "seedRank": row["originalRank"], "level": row["evidence"]["prior120High"], "status": "activeAtSourceEnd"}
                    setup_rows.append(active)
    retest_evidence = []
    for d in targets:
        ranked = sorted(retests[d], key=lambda row: (-row["priorMeanAmount"], row["symbol"]))
        for rank, row in enumerate(ranked, 1):
            admitted = rank <= 5 and liquid[d]["status"] == "passed"
            if admitted:
                schedules["retestLiquid"].append(signal(row["symbol"], d, rank, "retest", row["seedDate"]))
            retest_evidence.append({**row, "rank": rank, "admitted": admitted,
                "reason": "admitted" if admitted else "outsideOriginalTop5" if rank > 5 else "liquidityNotPassed"})
    for schedule in schedules.values():
        schedule.sort(key=lambda row: (row["signalDate"], row["originalRank"], row["symbol"]))
    return {"schedules": schedules, "setups": setup_rows, "transitions": transitions, "retests": retest_evidence,
        "seedEvidence": seeds, "daily": [{"date": d, "liquidity": liquid[d], "retestCandidates": len(retests[d])} for d in targets],
        "metadata": {"start": start, "end": end, "seedStart": seed_start, "fixedPopulation": len(dataset["securities"]),
            "signals": {p: len(v) for p, v in schedules.items()}, "setupStatusCounts": dict(Counter(r["status"] for r in setup_rows)),
            "rawPriceDiagnostic": True, "parameterSearch": False, "targetAchieved": False}}
