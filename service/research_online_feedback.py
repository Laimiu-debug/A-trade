"""Pure, point-in-time admission from matured shadow-account episodes.

This module neither generates stock signals nor executes accounts. Inputs may
contain the eventual history; a decision at T consumes only exits through T-1
and the already-lagged environment stored for each formation date.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import math

VERSION = "research-online-feedback-v1"
WINDOW_SLOTS = 126
MIN_FORMATION_DAYS = 20
RIDGE_LAMBDA = 1.0
FEATURES = ("breadth20", "amountRatio", "continuousRetentionLower")
MODES = ("intercept", "ridge")
_PATHS = (("price20", "positiveReturn20Breadth"),
          ("amount", "medianRelativeToOwnPrior20"),
          ("retention", "continuous", "retentionLower"))


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _day(value):
    _require(isinstance(value, str), "Calendar dates must be ISO strings")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Calendar dates must be valid ISO dates") from exc
    _require(parsed.isoformat() == value, "Calendar dates must use YYYY-MM-DD")
    return value


def _features(day, environment, calendar, indexes):
    row = environment.get(day)
    if row is None:
        return [None] * 3, ["missingEnvironmentRow"]
    at = indexes[day]
    expected = calendar[at - 1] if at else None
    _require(row.get("asOfDate") == expected,
             "Environment asOfDate must be exactly formationDate minus one calendar slot")
    values, reasons = [], []
    for name, path in zip(FEATURES, _PATHS):
        value = row
        for field in path:
            if value is None:
                break
            _require(isinstance(value, dict), "Environment feature containers must be objects")
            value = value.get(field)
        if value is None:
            reasons.append(name + "Unavailable")
        else:
            _require(_finite(value), "Environment features must be finite numbers or null")
            _require(value > 0 if name == "amountRatio" else 0 <= value <= 1,
                     "Environment features must retain original ratio units")
        values.append(value)
    if expected is None:
        _require(all(v is None for v in values), "First calendar slot has no prior-close environment")
    return values, reasons


def _solve_3x3(matrix, rhs):
    """Three-variable partial-pivot elimination; never changes its inputs."""
    _require(len(matrix) == 3 and all(len(row) == 3 for row in matrix) and len(rhs) == 3,
             "Exactly three ridge slopes required")
    work = [list(row) + [value] for row, value in zip(matrix, rhs)]
    _require(all(_finite(value) for row in work for value in row), "Nonfinite normal equations")
    for col in range(3):
        pivot = max(range(col, 3), key=lambda row: abs(work[row][col]))
        _require(work[pivot][col] != 0, "Singular normal equations")
        work[col], work[pivot] = work[pivot], work[col]
        for row in range(col + 1, 3):
            ratio = work[row][col] / work[col][col]
            work[row][col] = 0.0
            for item in range(col + 1, 4):
                work[row][item] -= ratio * work[col][item]
    result = [0.0] * 3
    for row in range(2, -1, -1):
        result[row] = (work[row][3] - math.fsum(work[row][col] * result[col]
                                             for col in range(row + 1, 3))) / work[row][row]
    _require(all(_finite(value) for value in result), "Nonfinite ridge solution")
    return result


def _training_summary(labels):
    days = len({row["formationDate"] for row in labels})
    total = math.fsum(row["weight"] for row in labels)
    result = {"episodeCount": len(labels), "formationDayCount": days, "weightSum": total,
              "featureMeans": None, "featureStdDevs": None, "zeroVarianceColumns": [],
              "labelMean": None, "normalMatrix": None, "rhs": None, "slopes": None}
    if not labels:
        return result
    weights = [row["weight"] / total for row in labels]

    def moments(values):
        # A truly constant column stays exactly constant even if fractional
        # per-episode weights would round its weighted mean away from that value.
        if all(value == values[0] for value in values):
            return values[0], 0.0
        center = math.fsum(w * value for w, value in zip(weights, values))
        variance = math.fsum(w * (value - center) ** 2 for w, value in zip(weights, values))
        _require(_finite(center) and _finite(variance) and variance >= 0,
                 "Nonfinite training moments")
        return center, math.sqrt(variance)

    moments_x = [moments([row["features"][i] for row in labels]) for i in range(3)]
    means = [pair[0] for pair in moments_x]
    scales = [pair[1] for pair in moments_x]
    # Only the mean of y is needed; do not add clipping or target transforms.
    ys = [row["netReturn"] for row in labels]
    ymean = ys[0] if all(y == ys[0] for y in ys) else math.fsum(w * y for w, y in zip(weights, ys))
    _require(_finite(ymean), "Nonfinite weighted label mean")
    result.update(featureMeans=means, featureStdDevs=scales,
                  zeroVarianceColumns=[name for name, scale in zip(FEATURES, scales) if scale == 0],
                  labelMean=ymean)
    return result


def _fit(labels, summary, current):
    means, scales = summary["featureMeans"], summary["featureStdDevs"]
    z = [[0.0 if scale == 0 else (value - center) / scale
          for value, center, scale in zip(row["features"], means, scales)] for row in labels]
    weights = [row["weight"] / summary["weightSum"] for row in labels]
    matrix = [[math.fsum(w * row[i] * row[j] for w, row in zip(weights, z))
               + (RIDGE_LAMBDA if i == j else 0.0) for j in range(3)] for i in range(3)]
    rhs = [math.fsum(w * row[i] * (label["netReturn"] - summary["labelMean"])
                     for w, row, label in zip(weights, z, labels)) for i in range(3)]
    slopes = _solve_3x3(matrix, rhs)
    standardized = [0.0 if scale == 0 else (value - center) / scale
                    for value, center, scale in zip(current, means, scales)]
    predicted = summary["labelMean"] + math.fsum(b * value for b, value in zip(slopes, standardized))
    _require(_finite(predicted), "Nonfinite ridge prediction")
    summary.update(normalMatrix=matrix, rhs=rhs, slopes=slopes)
    return predicted


def build_online_feedback(calendar, target_dates, environment_daily_rows, shadow_episodes):
    """Return every requested T's intercept/ridge decisions and audit evidence.

    Each supplied episode is one original formation identity, with an actual
    entry and either a final exit plus decimal netReturn or null exit/return.
    Partial exits do not mature a label. Unknown feature rows are retained as
    rejected-label evidence. Immature ages include entry and the current A.
    The 126-slot window truncates at the source-calendar start without adding a
    minimum-calendar-length condition. Fallback retains all original signals.
    """
    _require(isinstance(calendar, list) and bool(calendar), "Nonempty source calendar required")
    for day in calendar:
        _day(day)
    _require(calendar == sorted(set(calendar)), "Calendar must be unique and increasing")
    indexes = {day: index for index, day in enumerate(calendar)}
    _require(isinstance(target_dates, list) and all(day in indexes for day in target_dates)
             and target_dates == sorted(set(target_dates)), "Target dates must be an ordered unique calendar subset")
    _require(isinstance(environment_daily_rows, list) and isinstance(shadow_episodes, list),
             "Environment and episode inputs must be lists")
    environment = {}
    for row in environment_daily_rows:
        _require(isinstance(row, dict) and row.get("formationDate") in indexes,
                 "Environment rows must identify a source-calendar formation date")
        day = row["formationDate"]
        _require(day not in environment, "Duplicate environment formation date")
        environment[day] = row
    episodes, identities = [], set()
    for episode in shadow_episodes:
        _require(isinstance(episode, dict), "Shadow episodes must be objects")
        identity = episode.get("formationId")
        _require(isinstance(identity, str) and bool(identity.strip()) and identity not in identities,
                 "One unique nonempty formationId per shadow episode required")
        formed, entered, exited = (episode.get(key) for key in ("formationDate", "entryDate", "exitDate"))
        _require(formed in indexes and entered in indexes and formed < entered,
                 "Entry must follow formation on the source calendar")
        _require("exitDate" in episode and "netReturn" in episode, "Explicit final exit and decimal label fields required")
        _require(exited is None or (exited in indexes and entered < exited),
                 "Final exit must be null or follow entry under the T+1 sell constraint")
        _require(episode["netReturn"] is None if exited is None else _finite(episode["netReturn"]),
                 "Only fully closed episodes may carry a finite decimal netReturn")
        identities.add(identity)
        episodes.append(episode)
    # Canonical order makes neither input ordering nor future valid values a
    # tie-breaker in a past decision or its point-in-time audit trail.
    episodes.sort(key=lambda row: (row["formationDate"], row["formationId"]))
    rows = []
    for day in target_dates:
        at = indexes[day] - 1
        as_of = calendar[at] if at >= 0 else None
        first = max(0, at - WINDOW_SLOTS + 1)
        window_start = calendar[first] if at >= 0 else None
        current, current_reasons = _features(day, environment, calendar, indexes)
        labels, rejected, immature = [], [], []
        entered_count = closed_count = before_window = window_count = 0
        for episode in episodes:
            if as_of is None or episode["entryDate"] > as_of:
                continue
            entered_count += 1
            exited = episode["exitDate"]
            if exited is None or exited > as_of:
                immature.append({"formationId": episode["formationId"], "formationDate": episode["formationDate"],
                                 "entryDate": episode["entryDate"], "ageSlots": at - indexes[episode["entryDate"]] + 1})
                continue
            closed_count += 1
            if exited < window_start:
                before_window += 1
                continue
            window_count += 1
            _require(episode["formationDate"] < day, "Training formation must precede decision T")
            features, reasons = _features(episode["formationDate"], environment, calendar, indexes)
            identity = {key: episode[key] for key in ("formationId", "formationDate", "entryDate", "exitDate")}
            if reasons:
                rejected.append({**identity, "reasons": reasons})
            else:
                labels.append({**identity, "netReturn": episode["netReturn"], "features": features})
        day_counts = Counter(row["formationDate"] for row in labels)
        for label in labels:
            count = day_counts[label["formationDate"]]
            label.update(formationDayCount=count, weight=1.0 / count)
        summary = _training_summary(labels)
        reason = ("insufficientTrainingFormationDays" if len(day_counts) < MIN_FORMATION_DAYS else
                  "currentFeaturesUnknown" if current_reasons else None)
        if reason:
            models = {mode: {"status": "fallback", "reason": reason, "prediction": None, "allow": True} for mode in MODES}
        else:
            predictions = {"intercept": summary["labelMean"], "ridge": _fit(labels, summary, current)}
            models = {mode: {"status": "fitted", "reason": "positivePrediction" if value > 0 else "nonPositivePrediction",
                             "prediction": value, "allow": value > 0} for mode, value in predictions.items()}
        ages = [item["ageSlots"] for item in immature]
        rows.append({"formationDate": day, "asOfDate": as_of,
            "window": {"requestedSlots": WINDOW_SLOTS, "availableSlots": at - first + 1 if at >= 0 else 0,
                       "startDate": window_start, "endDate": as_of, "historyTruncated": at + 1 < WINDOW_SLOTS},
            "currentFeatures": dict(zip(FEATURES, current)),
            "currentFeatureStatus": "unknown" if current_reasons else "complete", "currentFeatureReasons": current_reasons,
            "trainingLabels": labels, "trainingSummary": summary, "labelXUnknown": rejected, "immature": immature,
            "support": {"enteredByAsOfCount": entered_count, "closedByAsOfCount": closed_count,
                "closedBeforeWindowCount": before_window, "closedInWindowCount": window_count,
                "labelXUnknownCount": len(rejected), "eligibleEpisodeCount": len(labels),
                "eligibleFormationDayCount": len(day_counts), "immatureCount": len(immature),
                "immatureAgeSlots": {"minimum": min(ages) if ages else None,
                                     "mean": math.fsum(ages) / len(ages) if ages else None,
                                     "maximum": max(ages) if ages else None}}, "models": models})
    return {"version": VERSION, "parameters": {"windowSlots": WINDOW_SLOTS, "minimumFormationDays": MIN_FORMATION_DAYS,
            "featureOrder": list(FEATURES), "ridgeLambda": RIDGE_LAMBDA, "interceptPenalized": False,
            "objective": "sum(weight*squaredError)/sum(weight) + ridgeLambda*sum(slopesSquared)",
            "weighting": "Each eligible episode has 1/count(eligible episodes sharing formationDate in the current exit window)",
            "standardization": "Training-weighted population mean and standard deviation; exact constant columns have z=0",
            "decision": "prediction>0; otherwise reject; both modes retain all signals on insufficient days or unknown current X",
            "clock": "T uses A=T-1; final exits within the inclusive 126-source-slot window ending A",
            "immatureAge": "Source-calendar slots from actual entry through A, inclusive"},
            "targetDates": list(target_dates), "dailyRows": rows,
            "counts": {"targetDayCount": len(rows), "modeCounts": {mode: dict(Counter(
                "fallback" if row["models"][mode]["status"] == "fallback" else
                "allowed" if row["models"][mode]["allow"] else "rejected" for row in rows)) for mode in MODES}},
            "signalsGenerated": False, "accountExecutions": 0, "certified": False, "targetAchieved": False}
