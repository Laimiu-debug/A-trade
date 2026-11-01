"""One frozen broad-market alignment admission; never alters account exits."""
from copy import deepcopy
import math

from .research_liquid_trend_breakout import day, positive
from .research_trend_breakout_retest import liquid_state

VERSION = "reclaim-market-alignment-v1"
SID = "strongUndercutReclaimV1"
POLICIES = ("original", "liquid", "liquidTrend")
PRICE_INDICES = ("sh000001", "sz399106")
SIGNAL_KEYS = {"signalId", "strategyId", "symbol", "signalDate", "originalRank"}


def price_trend_state(calendar, at, closes):
    required = calendar[max(0, at - 59):at + 1]
    evidence = {}
    for symbol in PRICE_INDICES:
        values = [closes.get(symbol, {}).get(d) for d in required]
        known = len(values) == 60 and all(positive(value) for value in values)
        mean20, mean60 = (math.fsum(values[-20:]) / 20, math.fsum(values) / 60) if known else (None, None)
        status = "unknown" if not known else "passed" if values[-1] > mean60 and mean20 > mean60 else "failed"
        evidence[symbol] = {"status": status, "close": values[-1] if values else None, "mean20": mean20, "mean60": mean60}
    statuses = [row["status"] for row in evidence.values()]
    status = "failed" if "failed" in statuses else "unknown" if "unknown" in statuses else "passed"
    return {"status": status, "indices": evidence, "windowStart": required[0], "windowEnd": required[-1]}


def build_admissions(calendar, original, amounts, closes, *, start, end):
    start, end = day(start), day(end)
    if (not isinstance(calendar, list) or not calendar or calendar != sorted(set(calendar)) or
            any(day(d) > end for d in calendar) or start not in calendar or end not in calendar or start > end):
        raise ValueError("Exact ordered independent target calendar/endpoints required")
    if not isinstance(original, list):
        raise ValueError("Frozen original schedule must be a list")
    identities, ranks, previous = set(), set(), None
    for row in original:
        if not isinstance(row, dict) or set(row) != SIGNAL_KEYS or row.get("strategyId") != SID:
            raise ValueError("Exact original immediate signal schema required")
        if (not isinstance(row["signalId"], str) or not row["signalId"] or row["signalId"] in identities or
                not isinstance(row["symbol"], str) or not row["symbol"] or type(row["originalRank"]) is not int or not 1 <= row["originalRank"] <= 5):
            raise ValueError("Unique original signal IDs and Top5 ranks required")
        d = day(row["signalDate"])
        ordering = (d, row["originalRank"], row["symbol"])
        if not start <= d <= end or d not in calendar or (d, row["originalRank"]) in ranks or (previous is not None and ordering <= previous):
            raise ValueError("Original schedule must be ordered and target-bound without duplicate ranks")
        identities.add(row["signalId"])
        ranks.add((d, row["originalRank"]))
        previous = ordering
    daily, by_date = [], {}
    for at, d in enumerate(calendar):
        if d < start:
            continue
        liquidity, trend = liquid_state(calendar, at, amounts), price_trend_state(calendar, at, closes)
        states = (liquidity["status"], trend["status"])
        combined = "failed" if "failed" in states else "unknown" if "unknown" in states else "passed"
        row = {"date": d, "liquidity": liquidity, "priceTrend": trend, "combinedStatus": combined}
        daily.append(row)
        by_date[d] = row
    schedules = {"original": deepcopy(original), "liquid": [], "liquidTrend": []}
    for row in original:
        state = by_date[row["signalDate"]]
        if state["liquidity"]["status"] == "passed":
            schedules["liquid"].append(deepcopy(row))
        if state["combinedStatus"] == "passed":
            schedules["liquidTrend"].append(deepcopy(row))
    return {"version": VERSION, "daily": daily, "schedules": schedules,
        "counts": {"targetSessions": len(daily), "liquidSessions": sum(r["liquidity"]["status"] == "passed" for r in daily),
            "alignedSessions": sum(r["combinedStatus"] == "passed" for r in daily),
            "unknownCombinedSessions": sum(r["combinedStatus"] == "unknown" for r in daily),
            "signals": {policy: len(rows) for policy, rows in schedules.items()}},
        "admissionOnly": True, "reranked": False, "delayedRetry": False, "targetAchieved": False}
