"""Causal strategy budgets from complete, costed shadow portfolios.

This allocator is diagnostic. Training persistence and untouched test-set
comparisons belong to portfolio_study, not to this online decision function.
"""
from copy import deepcopy
import math


ROTATION_MODES = ("soft_rotation", "hard_rotation")


def regime_label(regime):
    if not isinstance(regime, dict) or regime.get("status") != "observed":
        return "unknown"
    return str(regime.get("trend", "unknown")) + "/" + str(regime.get("volatility", "unknown"))


def decide_rotation(day, cursor, mode, config, current, last_adjustment, curves, regime):
    """Only curves through this close may enter; returns already include costs."""
    identifiers = sorted(current)
    lookback = config["lookbackDays"]
    info = {"date": day, "status": "held", "reason": "insufficientShadowHistory",
            "before": deepcopy(current), "after": deepcopy(current), "scores": {},
            "regime": deepcopy(regime), "weightChange": 0.}
    histories = {key: [row for row in curves[key] if row["date"] <= day] for key in identifiers}
    # The first NAV has no preceding portfolio observation, so require K+1.
    if any(len(rows) < lookback + 1 for rows in histories.values()):
        return info
    label = regime_label(regime)
    for key in identifiers:
        history = histories[key]
        recent = history[-lookback:]
        if any(row.get("dailyReturn") is None or not row.get("valuationReliable", True) for row in recent):
            info["reason"] = "unreliableShadowValuation"
            return info
        recent_return = math.prod(1 + row["dailyReturn"] for row in recent) - 1
        same = [row["dailyReturn"] for row in history[-max(lookback, config["warmupDays"]):]
                if label != "unknown" and regime_label(row.get("regimeForReturn")) == label
                and row.get("dailyReturn") is not None and row.get("valuationReliable", True)]
        conditional = None
        blend = 0.
        if len(same) >= config["regimeMinObservations"]:
            # Express conditional observations on the same K-day scale.
            conditional = math.expm1(max(-700., min(700., sum(math.log1p(max(-.999999999999, value)) for value in same) / len(same) * lookback)))
            blend = config["regimeBlend"]
        score = (1 - blend) * recent_return + blend * (conditional or 0.)
        info["scores"][key] = {"recentNetReturn": recent_return, "sameRegimeNetReturn": conditional,
                               "sameRegimeObservations": len(same), "regimeBlendApplied": blend,
                               "score": score, "throughDate": history[-1]["date"]}
    if mode == "hard_rotation":
        # Retain the incumbent on exact ties; never switch for alphabet order.
        best = max(info["scores"][key]["score"] for key in identifiers)
        tied = [key for key in identifiers if abs(info["scores"][key]["score"] - best) <= 1e-12]
        winner = sorted(tied, key=lambda key: (-current[key], key))[0]
        target = {key: float(key == winner) for key in identifiers}
    else:
        maximum = max(item["score"] for item in info["scores"].values())
        values = {key: math.exp(max(-700., (info["scores"][key]["score"] - maximum) / config["temperature"])) for key in identifiers}
        total = sum(values.values())
        target = {key: value / total for key, value in values.items()}
    distance = sum(abs(target[key] - current[key]) for key in identifiers) / 2
    info.update({"proposedWeights": target, "weightChange": distance})
    if last_adjustment is not None and cursor - last_adjustment < config["cooldownDays"]:
        info["reason"] = "cooldown"
    elif distance <= 1e-12 or distance + 1e-12 < config["minWeightChange"]:
        info["reason"] = "belowWeightThreshold"
    else:
        info.update({"status": "adjusted", "reason": "recentNetPerformanceAndKnownRegime", "after": target})
    return info
