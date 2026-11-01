"""Frozen leader-return incremental prediction; pure, account-free research.

Implements docs/leader-incremental-prediction-v1.json. Parent membership is an
input, never reconstructed from future predictions, prices, amounts or fills.
"""
from collections import Counter
from copy import deepcopy
import math

import numpy as np

from .research_leader_lag import _Series, _day, _finite


VERSION = "leader-incremental-prediction-v1"
FEATURES = ("own", "peerMean", "index", "anchor")
MODELS = ("baseline", "augmented")
TRAINING_PAIRS, FORECAST_SLOTS, RIDGE_LAMBDA = 60, 5, 1.0
SCORE_STATES = ("forecastBeyondEnd", "fitUnknown", "featureUnknown", "targetBeyondEnd",
                "targetUnknown", "numericUnknown", "scored")
STATUS_DOMAINS = {
    "fitStatus": ("known", "fitUnknown"),
    "featureStatus": ("known", "featureUnknown", "forecastBeyondEnd"),
    "predictionStatus": ("known", "fitUnknown", "featureUnknown", "forecastBeyondEnd", "numericUnknown"),
    "targetStatus": ("known", "targetUnknown", "targetBeyondEnd"),
    "scoreStatus": SCORE_STATES,
}
BASIS = {
    "population": "All parent formations retained; only formed groups create all20 original members, without amount/Top5/breakout filters",
    "return": "Simple decimal close(d)/close(d-1)-1; frozen _Series exact raw OHLCV/envelope/status validity",
    "training": "60 feature slots F-60..F-1, next-slot member targets through F, requiring62 price slots F-61..F; all60 complete jointly",
    "peerMean": "math.fsum of all other19 fixed members divided by19; self and anchor excluded; no missing-peer deletion",
    "standardization": "Training-only arithmetic mean and population std /60; exact constant columns mapped to0 in training and prediction; y unscaled",
    "ridge": "sum squared errors + 1*sum squared slopes; unpenalized intercept=yMean; numpy.linalg.solve(Z.T@Z+I, Z.T@(y-yMean))",
    "prediction": "Frozen F models, features known at d=A..A+4, target d+1; common four-feature support; no refitting",
    "cutoff": "Dates beyond registered end are null planned slots; no numerical bars after end; known prediction survives unknown/censored target",
    "errors": "Only common scored rows; augmented-minus-baseline MSE/MAE deltas; fixed zero forecast descriptive benchmark",
    "scope": "Conditional selected-event development diagnostic, no account, trading-profit, causal leadership or holdout claim",
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _formations(values, calendar, start, end, securities):
    _require(isinstance(values, list), "Ordered parent formations required")
    indexes = {day: i for i, day in enumerate(calendar)}
    result, identities, slots, previous = [], set(), set(), None
    for original in values:
        _require(isinstance(original, dict), "Parent formation must be an object")
        day = _day(original.get("formationDate"))
        if day > end:
            continue  # Ignore future numeric/group evidence.
        rank, anchor = original.get("originalLeaderRank"), original.get("anchorSymbol")
        _require(start <= day <= end and day in indexes and anchor in securities
                 and type(rank) is int and 1 <= rank <= 5, "Parent calendar/anchor/rank mismatch")
        key, identity = (day, rank, anchor), original.get("parentEventId")
        _require(identity == f"leaderLagFollowersV1:{day}:{anchor}" and identity not in identities
                 and (day, rank) not in slots and (previous is None or previous < key),
                 "Duplicate or misordered parent identity/rank")
        at = indexes[day]
        _require(at >= 10 and original.get("peerFormationDate") == calendar[at - 10], "Parent F must equal A-10")
        status, members = original.get("status"), original.get("members")
        _require(status in ("formed", "locked", "notFormed") and isinstance(members, list)
                 and len(members) <= 20, "Invalid parent status/members")
        _require(status != "formed" or (original.get("groupKind") == "formed" and len(members) == 20),
                 "Formed parent requires original20 peers")
        seen = set()
        for i, member in enumerate(members, 1):
            _require(isinstance(member, dict), "Member must be an object")
            symbol = member.get("symbol")
            _require(symbol in securities and symbol != anchor and symbol not in seen
                     and type(member.get("frozenMemberRank")) is int and member["frozenMemberRank"] == i,
                     "Invalid parent member identity/order")
            seen.add(symbol)
        identities.add(identity); slots.add((day, rank)); previous = key
        result.append(deepcopy(original))
    return result


def simple_return(source, calendar, at):
    """Adjacent-slot return and all raw-quality reasons, without gap compression."""
    if at < 1:
        return None, ["insufficientCalendarHistory"]
    if at >= len(calendar):
        return None, ["returnBeyondEnd"]
    reasons = [calendar[i] + ":" + source.reasons[i] for i in (at - 1, at)
               if source.reasons[i] is not None]
    if reasons:
        return None, reasons
    ratio = source.bars[at]["close"] / source.bars[at - 1]["close"]
    value = ratio - 1
    if not _finite(ratio) or ratio <= 0 or not _finite(value):
        return None, ["nonfiniteOrUnderflowReturn"]
    return value, []


def fit_ridge(features, targets, feature_names):
    """One fixed sum-loss ridge fit; numerical failures propagate to common fit."""
    _require(len(features) == TRAINING_PAIRS and len(targets) == TRAINING_PAIRS,
             "Exactly60 complete training pairs required")
    width = len(feature_names)
    _require(width in (3, 4) and all(len(row) == width for row in features), "Ridge feature width differs")
    _require(all(_finite(x) for row in features for x in row) and all(_finite(y) for y in targets),
             "Ridge values must be finite")
    means, scales, constant = [], [], []
    for i, name in enumerate(feature_names):
        values = [row[i] for row in features]
        exact_constant = all(value == values[0] for value in values)
        center = values[0] if exact_constant else math.fsum(values) / TRAINING_PAIRS
        scale = 0.0 if exact_constant else math.sqrt(math.fsum((value - center) ** 2 for value in values) / TRAINING_PAIRS)
        if not _finite(center) or not _finite(scale) or (not exact_constant and scale == 0):
            raise FloatingPointError("nonfiniteOrUnderflowTrainingMoments")
        means.append(center); scales.append(scale)
        if exact_constant:
            constant.append(name)
    z = [[0.0 if scale == 0 else (value - center) / scale
          for value, center, scale in zip(row, means, scales)] for row in features]
    center_y = targets[0] if all(y == targets[0] for y in targets) else math.fsum(targets) / TRAINING_PAIRS
    matrix = [[math.fsum(row[i] * row[j] for row in z) + (RIDGE_LAMBDA if i == j else 0.0)
               for j in range(width)] for i in range(width)]
    rhs = [math.fsum(row[i] * (y - center_y) for row, y in zip(z, targets)) for i in range(width)]
    if not all(_finite(v) for v in [center_y, *rhs, *(v for row in matrix for v in row)]):
        raise FloatingPointError("nonfiniteNormalEquations")
    with np.errstate(over="raise", invalid="raise", divide="raise"):
        beta = np.linalg.solve(np.asarray(matrix, dtype=np.float64), np.asarray(rhs, dtype=np.float64)).tolist()
    if not all(_finite(v) for v in beta):
        raise FloatingPointError("nonfiniteRidgeSolution")
    return {"featureNames": list(feature_names), "means": means, "scales": scales,
            "constantColumns": constant, "beta": beta, "intercept": center_y,
            "normalMatrix": matrix, "rhs": rhs}


def _predict(model, features):
    z = [0.0 if scale == 0 else (features[name] - center) / scale
         for name, center, scale in zip(model["featureNames"], model["means"], model["scales"])]
    value = model["intercept"] + math.fsum(b * v for b, v in zip(model["beta"], z))
    if not _finite(value):
        raise FloatingPointError("nonfinitePrediction")
    return value


def _empty_errors():
    return {"baseline": {"squared": None, "absolute": None},
            "augmented": {"squared": None, "absolute": None},
            "zero": {"squared": None, "absolute": None}, "mseDelta": None, "maeDelta": None}


def _errors(predictions, actual):
    result = {}
    for mode, value in (*predictions.items(), ("zero", 0.0)):
        error = value - actual
        result[mode] = {"squared": error * error, "absolute": abs(error)}
    result["mseDelta"] = result["augmented"]["squared"] - result["baseline"]["squared"]
    result["maeDelta"] = result["augmented"]["absolute"] - result["baseline"]["absolute"]
    if not all(_finite(v) for name in (*MODELS, "zero") for v in result[name].values()) or not all(
            _finite(result[name]) for name in ("mseDelta", "maeDelta")):
        raise FloatingPointError("nonfiniteScore")
    return result


def build_leader_incremental_prediction(dataset, formations, *, start, end, progress=None):
    """Return all parent events, common fits and five immutable forecast slots."""
    start, end = _day(start), _day(end)
    _require(start <= end and dataset.get("priceBasisId") == "raw-unadjusted", "Ordered dates and raw source required")
    supplied = dataset.get("calendar")
    _require(isinstance(supplied, list) and supplied and supplied == sorted(set(supplied))
             and all(_day(d) == d for d in supplied), "Unique increasing source calendar required")
    calendar = [day for day in supplied if day <= end]
    target_dates = [day for day in calendar if start <= day <= end]
    _require(target_dates, "No target sessions")
    originals = {}
    _require(isinstance(dataset.get("securities"), list), "Fixed securities list required")
    for security in dataset["securities"]:
        _require(isinstance(security, dict), "Security must be an object")
        symbol = security.get("symbol")
        _require(isinstance(symbol, str) and symbol and symbol not in originals, "Unique security symbols required")
        originals[symbol] = security
    benchmark = dataset.get("benchmark")
    _require(isinstance(benchmark, dict) and benchmark.get("symbol") == "sh000300", "CSI300 benchmark required")
    saved = _formations(formations, calendar, start, end, originals)
    formed = [row for row in saved if row["status"] == "formed"]
    needed = {row["anchorSymbol"] for row in formed}
    needed.update(member["symbol"] for row in formed for member in row["members"])
    sources = {symbol: _Series(originals[symbol], calendar, end) for symbol in sorted(needed)}
    index_source = _Series(benchmark, calendar, end)
    indexes, cache = {day: i for i, day in enumerate(calendar)}, {}

    def day(at):
        return calendar[at] if 0 <= at < len(calendar) else None

    def ret(symbol, at):
        key = (symbol, at)
        if key not in cache:
            cache[key] = simple_return(index_source if symbol is None else sources[symbol], calendar, at)
        return cache[key]

    def feature_values(member, members, anchor, at):
        values, reasons = {}, []
        for name, symbol in (("own", member), ("index", None), ("anchor", anchor)):
            values[name], issues = ret(symbol, at)
            reasons.extend(name + ":" + issue for issue in issues)
        peers, peer_reasons = [], []
        for symbol in members:
            if symbol == member:
                continue
            value, issues = ret(symbol, at)
            peers.append(value)
            peer_reasons.extend("peer:" + symbol + ":" + issue for issue in issues)
        values["peerMean"] = None
        if not peer_reasons:
            try:
                value = math.fsum(peers) / 19
                if not _finite(value):
                    raise FloatingPointError("nonfinitePeerMean")
                values["peerMean"] = value
            except (OverflowError, ValueError, FloatingPointError):
                peer_reasons.append("nonfinitePeerMean")
        reasons.extend(peer_reasons)
        return {name: values[name] for name in FEATURES}, reasons

    fits, rows = [], []
    for event_index, event in enumerate(formed):
        a, f = indexes[event["formationDate"]], indexes[event["peerFormationDate"]]
        members = [member["symbol"] for member in event["members"]]
        for member in event["members"]:
            symbol, anchor = member["symbol"], event["anchorSymbol"]
            identity = {name: event[name] for name in ("parentEventId", "formationDate", "peerFormationDate", "anchorSymbol", "originalLeaderRank")}
            identity.update(memberSymbol=symbol, frozenMemberRank=member["frozenMemberRank"],
                            fitId=event["parentEventId"] + ":" + symbol)
            pairs = []
            for at in range(f - TRAINING_PAIRS, f):
                features, reasons = feature_values(symbol, members, anchor, at)
                actual, target_reasons = ret(symbol, at + 1)
                reasons = ["feature:" + reason for reason in reasons] + ["target:" + reason for reason in target_reasons]
                pairs.append({"featureDate": day(at), "targetDate": day(at + 1), "features": features,
                              "actualReturn": actual, "status": "unknown" if reasons else "known", "reasons": reasons})
            fit = {**identity, "trainingFeatureDates": [day(i) for i in range(f - 60, f)],
                   "trainingTargetDates": [day(i) for i in range(f - 59, f + 1)],
                   "trainingPriceDates": [day(i) for i in range(f - 61, f + 1)],
                   "trainingPairs": pairs, "knownPairs": sum(pair["status"] == "known" for pair in pairs),
                   "fitStatus": "fitUnknown", "reasons": [], "models": {name: None for name in MODELS}}
            if fit["knownPairs"] != TRAINING_PAIRS:
                fit["reasons"] = ["incompleteTrainingPairs"]
            else:
                try:
                    models = {name: fit_ridge([[p["features"][feature] for feature in FEATURES[:width]] for p in pairs],
                                             [p["actualReturn"] for p in pairs], FEATURES[:width])
                              for name, width in (("baseline", 3), ("augmented", 4))}
                    fit.update(fitStatus="known", models=models)
                except (OverflowError, ValueError, FloatingPointError, np.linalg.LinAlgError):
                    fit["reasons"] = ["numericFitFailure"]
            fits.append(fit)
            for offset in range(FORECAST_SLOTS):
                at = a + offset
                forecast_date, target_date = day(at), day(at + 1)
                if forecast_date is None:
                    features, feature_reasons = {name: None for name in FEATURES}, ["forecastBeyondEnd"]
                else:
                    features, feature_reasons = feature_values(symbol, members, anchor, at)
                actual, target_reasons = (None, ["targetBeyondEnd"]) if target_date is None else ret(symbol, at + 1)
                feature_status = "forecastBeyondEnd" if forecast_date is None else "featureUnknown" if feature_reasons else "known"
                prediction_status = ("forecastBeyondEnd" if forecast_date is None else "fitUnknown" if fit["fitStatus"] != "known"
                                     else "featureUnknown" if feature_reasons else "known")
                predictions, prediction_reasons = {name: None for name in MODELS}, []
                if prediction_status == "known":
                    try:
                        predictions = {name: _predict(fit["models"][name], features) for name in MODELS}
                    except (OverflowError, ValueError, FloatingPointError):
                        predictions = {name: None for name in MODELS}
                        prediction_status, prediction_reasons = "numericUnknown", ["nonfinitePrediction"]
                target_status = "targetBeyondEnd" if target_date is None else "targetUnknown" if target_reasons else "known"
                status = ("forecastBeyondEnd" if forecast_date is None else "fitUnknown" if fit["fitStatus"] != "known"
                          else "featureUnknown" if feature_reasons else "targetBeyondEnd" if target_date is None
                          else "targetUnknown" if target_reasons else "numericUnknown" if prediction_status != "known" else "scored")
                errors = _empty_errors()
                if status == "scored":
                    try:
                        errors = _errors(predictions, actual)
                    except (OverflowError, ValueError, FloatingPointError):
                        status = "numericUnknown"
                        prediction_reasons.append("nonfiniteScore")
                rows.append({**identity, "rowId": identity["fitId"] + ":" + str(offset), "offset": offset,
                    "forecastDate": forecast_date, "targetDate": target_date, "fitStatus": fit["fitStatus"],
                    "featureStatus": feature_status, "predictionStatus": prediction_status, "targetStatus": target_status,
                    "scoreStatus": status, "features": features, "featureReasons": feature_reasons,
                    "predictionReasons": prediction_reasons, "targetReasons": list(target_reasons),
                    "actualReturn": actual, "predictions": predictions, "errors": errors,
                    "crosses20240924": forecast_date is not None and target_date is not None
                        and forecast_date < "2024-09-24" <= target_date})
        if progress is not None and ((event_index + 1) % 10 == 0 or event_index + 1 == len(formed)):
            progress({"stage": "conditionalPrediction", "completedEvents": event_index + 1,
                      "formedEvents": len(formed), "fits": len(fits), "rows": len(rows)})
    form_counts = Counter(row["status"] for row in saved)
    counts = {"formations": len(saved), "formationStatusCounts": {s: form_counts[s] for s in ("formed", "locked", "notFormed")},
              "fits": len(fits), "plannedRows": len(rows),
              "fitStatusCounts": {s: sum(fit["fitStatus"] == s for fit in fits) for s in STATUS_DOMAINS["fitStatus"]},
              "rowStatusCounts": {field: {s: sum(row[field] == s for row in rows) for s in states}
                                  for field, states in STATUS_DOMAINS.items()},
              "unassignedForecastDates": sum(row["forecastDate"] is None for row in rows),
              "crosses20240924": sum(row["crosses20240924"] for row in rows)}
    metadata = {"version": VERSION, "calendar": calendar, "targetDates": target_dates,
                "basis": deepcopy(BASIS), "numpyVersion": np.__version__}
    return {"metadata": metadata, "formations": saved, "fits": fits, "rows": rows, "counts": counts}
