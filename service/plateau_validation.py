"""Predeclared risk, neighborhood and parameter-identifiability checks.

These checks describe sampled behavior; no holdout observation selects a grid.
"""
import hashlib
import json
import math
from statistics import mean, median, pstdev


STABILITY_DEFAULTS = {"maxNeighborhoodRelativeStd": 1., "minNeighborhoodReturnRetention": .5}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def risk_failures(result, settings):
    metrics = result.get("metrics", {})
    failures = []
    count = metrics.get("closedTradeCount")
    if not finite(count) or count < settings["minClosedTrades"]:
        failures.append("insufficient_closed_trades")
    drawdown = metrics.get("maxDrawdownPct")
    if not finite(drawdown) or not 0 <= drawdown <= settings["maxDrawdownPct"]:
        failures.append("drawdown_limit_exceeded_or_missing")
    invested = metrics.get("investedDays")
    if not finite(invested) or invested < settings["minInvestedDays"]:
        failures.append("insufficient_invested_days")
    return failures


def neighborhood_statistics(values, settings):
    valid = [value for value in values if finite(value)]
    average = mean(valid) if valid else None
    center = median(valid) if valid else None
    deviation = pstdev(valid) if valid else None
    # A nonpositive median cannot establish broadly positive performance.
    relative = deviation / center if center is not None and center > 0 else None
    if relative is not None and not math.isfinite(relative):
        relative = None
    retention = center / max(valid) if valid and max(valid) > 0 else None
    failures = []
    if len(valid) != len(values) or len(valid) < settings["minNeighborhoodSize"]:
        failures.append("insufficient_valid_neighborhood")
    if average is None or average <= 0:
        failures.append("nonpositive_neighborhood_mean")
    positive = sum(value > 0 for value in valid) / len(valid) if valid else None
    if positive is None or positive < settings["minNeighborPositiveFraction"]:
        failures.append("insufficient_positive_neighbors")
    if deviation is None or deviation > settings["maxNeighborhoodStdPct"]:
        failures.append("neighborhood_absolute_dispersion")
    if relative is None or relative > settings.get("maxNeighborhoodRelativeStd", STABILITY_DEFAULTS["maxNeighborhoodRelativeStd"]):
        failures.append("neighborhood_relative_dispersion")
    if retention is None or retention < settings.get("minNeighborhoodReturnRetention", STABILITY_DEFAULTS["minNeighborhoodReturnRetention"]):
        failures.append("neighborhood_peak_not_retained")
    return {"size": len(values), "validCount": len(valid), "meanNetReturnPct": average,
            "medianNetReturnPct": center, "minNetReturnPct": min(valid) if valid else None,
            "stdNetReturnPct": deviation, "relativeStd": relative, "returnRetention": retention,
            "positiveFraction": positive, "passed": not failures, "failures": failures}


def behavior_fingerprint(result):
    """Hash actual fills/NAV, excluding parameters, reasons and audit labels."""
    curve, trades = result.get("curve"), result.get("trades")
    if not isinstance(curve, list) or not curve or not isinstance(trades, list):
        return None
    fields = {"curve": ("date", "equity", "cash", "marketValue"),
              "trades": ("date", "symbol", "side", "quantity", "price", "fee")}
    payload = {key: [{field: row.get(field) for field in names} for row in result[key]]
               for key, names in fields.items()}
    packed = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(packed.encode()).hexdigest()


def behavior_diagnostics(candidates, fingerprints, axes):
    complete = all(fingerprints.get(row["candidateId"]) for row in candidates)
    inactive = []
    if complete:
        for dimension, axis in enumerate(axes):
            fibers = {}
            for row in candidates:
                fixed = tuple(value for i, value in enumerate(row["coordinates"]) if i != dimension)
                fibers.setdefault(fixed, set()).add(fingerprints[row["candidateId"]])
            if all(len(values) == 1 for values in fibers.values()):
                inactive.append(axis["path"])
    unique = len({value for value in fingerprints.values() if value})
    return {"complete": complete, "distinctBehaviorCount": unique, "inactiveAxes": inactive,
            "uninformative": complete and unique == 1,
            "definition": "exact actual fill and account path hashes; parameter/audit labels excluded"}


def check_identifiability(stats, fingerprints, settings):
    valid = [value for value in fingerprints if value]
    distinct = len(set(valid))
    stats["distinctBehaviorCount"] = distinct
    stats["behaviorIdentifiable"] = len(valid) == len(fingerprints) and distinct >= settings["minNeighborhoodSize"]
    if not stats["behaviorIdentifiable"]:
        stats["passed"] = False
        stats.setdefault("failures", []).append("unidentifiable_parameter_behavior")
    return stats
