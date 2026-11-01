"""Causal entry rules over calendar-aligned force-statistics rows.

The caller supplies one row per market session, including rows whose missing
observations are represented by None. This module never fills gaps, computes
prices, ranks stocks or inspects positions. Signals occur at the current close.
"""
from datetime import date
import math


VERSION = "force-statistics-signals-v1"
STRATEGY_IDS = ("directionBreakout", "pressureBreakout", "pressurePullback")


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def _condition(rows, index, strategy_id):
    """Return true/false/unknown and independently copied required evidence."""
    if index < 2:
        return "unknown", None
    row = rows[index]
    prefix = "direction" if strategy_id == "directionBreakout" else "pressure"
    key5, key20 = prefix + "5", prefix + "20"
    recent = [rows[i].get(key5) for i in range(index - 2, index + 1)]
    level20, close = row.get(key20), row.get("close")
    values = [level20, close, *recent]
    prices = [close]
    features = {key20: level20, key5: recent[-1], key5 + "History": recent,
                "windowDates3": [r["date"] for r in rows[index - 2:index + 1]], "close": close}
    if strategy_id == "pressurePullback":
        previous = rows[index - 1]
        prior_close, prior_ma = previous.get("close"), previous.get("ma20")
        high = row.get("previousHigh")
        retreat, volume_ratio = row.get("pullback3Return"), row.get("pullback3VolumeRatio")
        values.extend((prior_close, prior_ma, high, retreat, volume_ratio))
        prices.extend((prior_close, prior_ma, high))
        features.update(previousClose=prior_close, previousMA20=prior_ma, previousHigh=high,
                        pullback3Return=retreat, pullback3VolumeRatio=volume_ratio)
    else:
        high = row.get("prior10High")
        values.append(high)
        prices.append(high)
        features["prior10High"] = high
    # Missing required evidence takes precedence over an independently false
    # predicate; it never turns into a measured zero or a qualifying signal.
    if not all(_finite(value) for value in values) or any(value <= 0 for value in prices):
        return "unknown", None
    if strategy_id == "pressurePullback" and volume_ratio < 0:
        return "unknown", None
    checks = {"positive20": level20 > 0,
              "positive5ForThreeSessions": all(value > 0 for value in recent),
              "rising5": recent[-1] > recent[-2], "priceBreakout": close > high}
    if strategy_id == "pressurePullback":
        checks.update(pullbackReturnNegative=retreat < 0, pullbackVolumeContracted=volume_ratio < 1,
                      previousCloseAtOrAboveMA20=prior_close >= prior_ma)
    features["conditions"] = checks
    return ("true" if all(checks.values()) else "false"), features


def generate_force_stat_signals(rows):
    """Generate three independent pre-position signal streams without mutation.

    Each uninterrupted run of true conditions emits only on its first day.
    False or unknown re-arms that rule; the next signal must still have all
    three aligned positive short-window observations. Nonfinite/non-numeric
    required fields are unknown. Contradictory row dates fail closed.
    """
    if not isinstance(rows, (list, tuple)):
        raise ValueError("rows must be a calendar-aligned list or tuple")
    previous_day = ""
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("date"), str):
            raise ValueError("Each statistics row requires an ISO date")
        day = row["date"]
        try:
            valid = date.fromisoformat(day).isoformat() == day
        except ValueError:
            valid = False
        if not valid or day <= previous_day:
            raise ValueError("Statistics dates must be canonical, unique and increasing")
        previous_day = day
    signals = {sid: [] for sid in STRATEGY_IDS}
    counts = {sid: {key: 0 for key in ("evaluated", "conditionTrue", "conditionFalse", "unknown",
                                      "signals", "suppressedConsecutive")} for sid in STRATEGY_IDS}
    prior_true = {sid: False for sid in STRATEGY_IDS}
    for index, row in enumerate(rows):
        for sid in STRATEGY_IDS:
            status, features = _condition(rows, index, sid)
            tally = counts[sid]
            tally["evaluated"] += 1
            tally[{"true": "conditionTrue", "false": "conditionFalse", "unknown": "unknown"}[status]] += 1
            if status == "true":
                if prior_true[sid]:
                    tally["suppressedConsecutive"] += 1
                else:
                    key20 = "direction20" if sid == "directionBreakout" else "pressure20"
                    signals[sid].append({"signalDate": row["date"], "score": features[key20], "features": features})
                    tally["signals"] += 1
            prior_true[sid] = status == "true"
    return {"version": VERSION, "signals": signals, "counts": counts}
