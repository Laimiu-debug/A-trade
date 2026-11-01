"""Causal first-120-session breakout with trend/early and market-liquidity gates.

This is an isolated raw-price development study, not a production scanner.
Unknown exact-calendar windows are retained in daily population counts.
"""
from collections import deque
from datetime import date
import math

STRATEGY_ID = "liquidTrendBreakoutV1"
POLICIES = ("breakout120", "trendEarly", "trendEarlyLiquid")


def day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Canonical ISO date required")
    return value


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def valid(bar):
    return (isinstance(bar, dict) and not bar.get("suspended") and
            all(positive(bar.get(k)) for k in ("open", "high", "low", "close", "volume", "amount")) and
            bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"])


def liquidity_state(calendar, at, market):
    required = calendar[max(0, at - 20):at + 1]
    if at < 20 or any(not positive(market.get(d)) for d in required):
        return {"status": "unknown", "totalAmountCny": market.get(calendar[at]), "mean5Cny": None, "priorMean20Cny": None}
    values = [market[d] for d in required]
    mean5, prior20 = math.fsum(values[-5:]) / 5, math.fsum(values[:-1]) / 20
    return {"status": "passed" if values[-1] >= 1e12 and mean5 >= prior20 else "failed",
            "totalAmountCny": values[-1], "mean5Cny": mean5, "priorMean20Cny": prior20}


def build_signals(dataset, market, *, start, end):
    start, end = day(start), day(end)
    if start > end:
        raise ValueError("Ordered interval required")
    supplied = dataset.get("calendar")
    if not isinstance(supplied, list) or not supplied or supplied != sorted(set(supplied)):
        raise ValueError("Independent ordered unique source calendar required")
    calendar = [day(d) for d in supplied if d <= end]
    positions = {d: i for i, d in enumerate(calendar)}
    targets = [d for d in calendar if start <= d <= end]
    if not targets:
        raise ValueError("Nonempty target interval required")
    candidates = {d: [] for d in targets}
    counts = {d: {"knownFalse": 0, "unknownWindow": 0, "commonCandidates": 0} for d in targets}
    securities = dataset.get("securities")
    if not isinstance(securities, list) or not securities:
        raise ValueError("Full source securities required")
    symbols = set()
    for security in securities:
        symbol = security.get("symbol")
        if not isinstance(symbol, str) or not symbol or symbol in symbols:
            raise ValueError("Unique nonempty symbols required")
        symbols.add(symbol)
        bars, previous_day = [None] * len(calendar), None
        for bar in security["bars"]:
            d = day(bar["date"])
            if d > end:
                continue
            if d not in positions or (previous_day is not None and d <= previous_day):
                raise ValueError("Duplicate, unordered or off-calendar historical bar")
            previous_day, bars[positions[d]] = d, bar
        invalid = [0]
        for bar in bars:
            invalid.append(invalid[-1] + (not valid(bar)))
        queue, previous_high = deque(), None
        for at, d in enumerate(calendar):
            while queue and queue[0] < at - 120:
                queue.popleft()
            prior_high = bars[queue[0]]["high"] if queue else None
            if d in candidates:
                if at < 140 or invalid[at + 1] != invalid[at - 140]:
                    counts[d]["unknownWindow"] += 1
                elif (bars[at]["close"] <= prior_high or bars[at - 1]["close"] > previous_high):
                    counts[d]["knownFalse"] += 1
                else:
                    amount = math.fsum(b["amount"] for b in bars[at - 20:at]) / 20
                    if amount < 50_000_000:
                        counts[d]["knownFalse"] += 1
                    else:
                        ma60 = math.fsum(b["close"] for b in bars[at - 59:at + 1]) / 60
                        ma120 = math.fsum(b["close"] for b in bars[at - 119:at + 1]) / 120
                        old_ma120 = math.fsum(b["close"] for b in bars[at - 139:at - 19]) / 120
                        prior_return = bars[at - 1]["close"] / bars[at - 21]["close"] - 1
                        trend = bars[at]["close"] > ma60 > ma120 > old_ma120 and prior_return <= .15
                        counts[d]["commonCandidates"] += 1
                        candidates[d].append({"symbol": symbol, "priorMeanAmount": amount,
                            "trendPassed": trend, "close": bars[at]["close"], "prior120High": prior_high,
                            "ma60": ma60, "ma120": ma120, "ma120Lag20": old_ma120, "prior20Return": prior_return})
            previous_high = prior_high
            if isinstance(bars[at], dict) and positive(bars[at].get("high")):
                while queue and bars[queue[-1]]["high"] <= bars[at]["high"]:
                    queue.pop()
                queue.append(at)
    schedules, daily, opportunities = {p: [] for p in POLICIES}, [], []
    for d in targets:
        rows = sorted(candidates[d], key=lambda r: (-r["priorMeanAmount"], r["symbol"]))
        market_state = liquidity_state(calendar, positions[d], market)
        for rank, row in enumerate(rows[:5], 1):
            identity = f"{STRATEGY_ID}:{d}:{row['symbol']}"
            signal = {"signalId": identity, "strategyId": STRATEGY_ID, "symbol": row["symbol"],
                      "signalDate": d, "originalRank": rank}
            admission = {"breakout120": True, "trendEarly": row["trendPassed"],
                         "trendEarlyLiquid": row["trendPassed"] and market_state["status"] == "passed"}
            for policy, admitted in admission.items():
                if admitted:
                    schedules[policy].append(dict(signal))
            opportunities.append({**signal, "evidence": row, "liquidity": market_state, "admission": admission})
        daily.append({"date": d, **counts[d], "fixedPopulation": len(symbols),
                      "commonTop5": min(5, len(rows)), "liquidity": market_state})
    return {"schedules": schedules, "daily": daily, "opportunities": opportunities,
            "metadata": {"fixedPopulation": len(symbols), "sourceStart": calendar[0], "sourceEnd": calendar[-1],
                         "start": start, "end": end, "signals": {p: len(s) for p, s in schedules.items()},
                         "sourceSlots": len(calendar), "rawPriceDiagnostic": True, "certified": False,
                         "targetAchieved": False}}
