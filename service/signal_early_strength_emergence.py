"""Registered early-strength entry, from archived ranks and causal raw prices.

No old strategy/Q, position, future label, or market-population recomputation is
used. The caller binds the full audited panel and raw source. Historical status
unknowns remain limitations; neither a rank nor a diagnostic eligibility label
certifies trade execution, historical identity, or economic adjusted returns.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date
import math
from statistics import mean

VERSION = "raw-early-strength-emergence-v1"
CANDIDATE_ID = "rawEarlyStrengthEmergence"
CONTROL_ID = "control_rawEarlyStrengthEmergence"
GROUPS = (CANDIDATE_ID, CONTROL_ID)
SOURCE_FEATURE_VERSION = "daily-leadership-raw-v1"
HISTORY_SLOTS, ADVANCE_SLOTS, CONFIRMATION_OFFSET, TOP_N = 61, 6, 2, 5
RANK_THRESHOLD, HISTORY_MEAN_MAX, RANK60_MAX = .90, .50, .80
FALSE_BASE_REASONS = {"delisted", "notHistoricalMember", "notListed", "historicalSt",
    "suspendedOrZeroVolume", "insufficientListingHistory", "amount20BelowMinimum", "amplitude20BelowMinimum"}
UNKNOWN_BASE_REASONS = {"invalidHistory", "missingDailyBar", "notObservedYet", "strictHistoryMissing"}


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _bounded(value):
    return _finite(value) and 0 <= value <= 1


def _positive(value):
    return _finite(value) and value > 0


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Expected canonical ISO date")
    return value


def _symbol(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected nonempty symbol")
    return value


def _calendar(values, *, day=None, maximum=None):
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("Nonempty ordered calendar required")
    result = list(values)
    if any(_day(d) != d for d in result) or any(a >= b for a, b in zip(result, result[1:])):
        raise ValueError("Calendar must be unique and increasing")
    if day is not None and result[-1] != _day(day) or maximum is not None and len(result) > maximum:
        raise ValueError("Calendar must end at day and respect its causal bound")
    return result


def _strings(values):
    return (isinstance(values, list) and all(isinstance(v, str) and v for v in values)
            and len(values) == len(set(values)))


def _dates(values, allowed):
    return _strings(values) and values == sorted(values) and set(values).issubset(allowed)


def _complete(group, required, window):
    return (len(window) == required and isinstance(group, dict) and group.get("complete") is True
        and all(type(group.get(k)) is int and group[k] == required for k in
                ("requiredSessions", "availableCalendarSessions", "observedSessions", "validSessions"))
        and all(group.get(k) == [] for k in ("missingDates", "invalidDates", "haltDates", "reasons"))
        and _dates(group.get("unknownTradeStatusDates"), set(window)))


def _clear(reasons, fields):
    return isinstance(reasons, dict) and all(k not in reasons or reasons[k] == [] for k in fields)


def _reject_future_evidence(row, day):
    date_fields = ("missingDates", "invalidDates", "haltDates", "unknownTradeStatusDates")
    containers = [row, row.get("benchmarkQuality")]
    groups = row.get("completeness")
    if isinstance(groups, dict):
        containers.extend(groups.values())
    sequences = [row.get("strengthRankSessionDates20")]
    sequences.extend(container.get(key) for container in containers if isinstance(container, dict) for key in date_fields)
    for values in sequences:
        if not isinstance(values, list):
            continue
        for value in values:
            try:
                canonical = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
            except ValueError:
                canonical = False
            if canonical and value > day:
                raise ValueError("Future dated panel evidence is not causal")


def _qualification(state):
    reason = state.get("reason") if isinstance(state, dict) else None
    unknown = state.get("unknown") if isinstance(state, dict) else None
    valid = (isinstance(state, dict) and type(state.get("eligible")) is bool
             and isinstance(reason, str) and bool(reason) and _strings(unknown)
             and ("certified" not in state or state["certified"] is False)
             and ("affectsFeaturesOrRanks" not in state or state["affectsFeaturesOrRanks"] is False))
    result = "unknown"
    if valid:
        if state["eligible"] is True and reason == "eligible":
            result = "true"
        elif state["eligible"] is False and reason in FALSE_BASE_REASONS:
            result = "false"
    return {"state": result, "eligible": state["eligible"] if valid else None,
            "reason": reason if isinstance(reason, str) else None,
            "unknown": deepcopy(unknown) if _strings(unknown) else [],
            "evidenceValid": valid, "certified": False}


def project_panel(symbol, day, calendar61, row):
    """Project independent evidence; unrelated missing features do not erase it.

    Identity/calendar contradictions are caller contract errors. Missing quality
    evidence or malformed numerical fields are unknown, never fabricated zero.
    """
    _symbol(symbol)
    days = _calendar(calendar61, day=day, maximum=HISTORY_SLOTS)
    if (not isinstance(row, dict) or row.get("symbol") != symbol or row.get("asOfDate") != day
            or row.get("featureVersion") != SOURCE_FEATURE_VERSION):
        raise ValueError("Full source panel identity/version mismatch")
    _reject_future_evidence(row, day)
    groups, reasons = row.get("completeness"), row.get("featureReasons")
    groups = groups if isinstance(groups, dict) else {}
    basis = row.get("rawPriceBasis") == "raw-unadjusted"
    projected = {"version": VERSION, "sourceFeatureVersion": SOURCE_FEATURE_VERSION,
        "symbol": symbol, "date": day, "calendar61": days, "qualification": _qualification(row.get("baseEligibility"))}
    for horizon in (20, 60):
        field = f"relativeReturn{horizon}"
        value, count = row.get(field+"Percentile"), row.get(field+"CrossSectionCount")
        required = horizon+1
        known = (basis and _bounded(value) and type(count) is int and 1 <= count <= 3141
            and all(_finite(row.get(k)) and row[k] >= -1 for k in (f"return{horizon}", field))
            and _clear(reasons, (f"return{horizon}", field, field+"Percentile"))
            and all(_complete(groups.get(k), required, days[-required:]) for k in
                    (f"security{horizon}", f"benchmark{horizon}")))
        projected[f"rank{horizon}"] = {"known": known, "value": value if _bounded(value) else None,
            "count": count if type(count) is int and 0 <= count <= 3141 else None,
            "reasons": [] if known else ["requiredRankEvidenceUnavailable"]}
    values = row.get("trailing20DailyStrengthPercentiles")
    counts = row.get("trailing20StrengthCrossSectionCounts")
    history_dates = row.get("strengthRankSessionDates20")
    history_known = (projected["rank20"]["known"] and len(days) >= 20
        and isinstance(values, list) and len(values) == 20 and all(_bounded(v) for v in values)
        and isinstance(counts, list) and len(counts) == 20 and all(type(n) is int and 1 <= n <= 3141 for n in counts)
        and history_dates == days[-20:]
        and all(type(row.get(k)) is int and row[k] == 20 for k in
                ("knownStrengthRankSessions20", "availableStrengthRankSessions20"))
        and values[-1] == projected["rank20"]["value"] and counts[-1] == projected["rank20"]["count"]
        and _bounded(row.get("meanStrengthPercentile20")) and row["meanStrengthPercentile20"] == mean(values)
        and _bounded(row.get("minimumStrengthPercentile20")) and row["minimumStrengthPercentile20"] == min(values)
        and _clear(reasons, ("meanStrengthPercentile20", "minimumStrengthPercentile20")))
    projected["history20"] = {"known": bool(history_known),
        "values": [v if _bounded(v) else None for v in values] if isinstance(values, list) else None,
        "counts": [n if type(n) is int and 0 <= n <= 3141 else None for n in counts] if isinstance(counts, list) else None,
        "dates": deepcopy(history_dates) if isinstance(history_dates, list) and all(v is None or isinstance(v, str) for v in history_dates) else None,
        "mean": row.get("meanStrengthPercentile20") if _bounded(row.get("meanStrengthPercentile20")) else None,
        "minimum": row.get("minimumStrengthPercentile20") if _bounded(row.get("minimumStrengthPercentile20")) else None,
        "reasons": [] if history_known else ["requiredHistoricalRankEvidenceUnavailable"]}
    amount = row.get("prior20MeanAmount")
    amount_known = (basis and _positive(amount) and type(row.get("knownAmountSessions20")) is int
        and row["knownAmountSessions20"] == 20 and _complete(groups.get("amountPrior20"), 20, days[-21:-1])
        and _clear(reasons, ("prior20MeanAmount",)))
    projected["amountPrior20"] = {"known": amount_known, "value": amount if _positive(amount) else None,
                                  "reasons": [] if amount_known else ["requiredExplicitAmountEvidenceUnavailable"]}
    projected.update(certified=False, targetAchieved=False)
    return deepcopy(projected)


def _raw_quality(days, bars):
    reasons, unknown_status = [], []
    if not isinstance(bars, (list, tuple)) or len(bars) != len(days):
        return {"known": False, "reasons": ["rawWindowLengthMismatch"], "unknownTradeStatusDates": []}
    if len(days) != ADVANCE_SLOTS:
        reasons.append("insufficientSixSessionHistory")
    for day, bar in zip(days, bars):
        flag = None if not isinstance(bar, dict) else bar.get("knownSuspended")
        status = None if not isinstance(bar, dict) else bar.get("tradeStatusState")
        if status == "unknown" or status is None and flag is not True:
            unknown_status.append(day)
        if bar is None:
            reasons.append("missingRawBar")
        elif not isinstance(bar, dict) or bar.get("date") != day:
            reasons.append("rawDateMismatch")
        elif bar.get("priceBasisId") != "raw-unadjusted":
            reasons.append("rawBasisUnknown")
        else:
            if not all(_positive(bar.get(k)) for k in ("open", "high", "low", "close", "volume")):
                reasons.append("invalidRawOHLCV")
            elif not bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]:
                reasons.append("invalidRawEnvelope")
            if flag is True or status == "halt":
                reasons.append("suspensionContradictsPositiveVolume" if _positive(bar.get("volume")) else "knownSuspension")
            elif flag is not None and type(flag) is not bool:
                reasons.append("invalidSuspensionEvidence")
            if status is not None and status not in ("unknown", "trading", "halt"):
                reasons.append("invalidTradeStatusEvidence")
    return {"known": not reasons, "reasons": sorted(set(reasons)), "unknownTradeStatusDates": unknown_status}


def _combine(states):
    return "unknown" if "unknown" in states else "false" if "false" in states else "true"


def _predicate(known, value, threshold, operator):
    passed = None if not known else value > threshold if operator == ">" else value <= threshold
    return {"known": known, "value": value, "operator": operator, "threshold": threshold,
            "state": "unknown" if passed is None else "true" if passed else "false"}


def evaluate_advance(symbol, day, calendar6, raw6, benchmark6):
    """Six aligned raw bars; multiplicative benchmark excess, not beta residuals."""
    _symbol(symbol)
    days = _calendar(calendar6, day=day, maximum=ADVANCE_SLOTS)
    stock, benchmark = _raw_quality(days, raw6), _raw_quality(days, benchmark6)
    own = excess = ratio = benchmark_ratio = None
    if stock["known"]:
        ratio = raw6[-1]["close"] / raw6[0]["close"]
        if _positive(ratio) and _finite(ratio-1):
            own = ratio-1
        else:
            stock["known"] = False
            stock["reasons"].append("nonfiniteOrUnderflowDerivedRatio")
    if benchmark["known"]:
        benchmark_ratio = benchmark6[-1]["close"] / benchmark6[0]["close"]
        if not _positive(benchmark_ratio):
            benchmark["known"] = False
            benchmark["reasons"].append("nonfiniteOrUnderflowDerivedRatio")
    if stock["known"] and benchmark["known"]:
        relative_ratio = ratio / benchmark_ratio
        if _positive(relative_ratio) and _finite(relative_ratio-1):
            excess = relative_ratio-1
    predicates = {"own5": _predicate(own is not None, own, 0, ">"),
                  "excess5": _predicate(excess is not None, excess, 0, ">")}
    return {"symbol": symbol, "date": day, "window": days, "own5": own, "excess5": excess,
            "stockQuality": stock, "benchmarkQuality": benchmark, "predicates": predicates,
            "state": _combine([v["state"] for v in predicates.values()]), "certified": False}


def _validate_projection(row, symbol, day, days):
    if (not isinstance(row, dict) or row.get("version") != VERSION or row.get("sourceFeatureVersion") != SOURCE_FEATURE_VERSION
            or row.get("symbol") != symbol or row.get("date") != day or row.get("calendar61") != days):
        raise ValueError("Full projected population identity/calendar differs")
    for key in ("rank20", "rank60", "amountPrior20", "history20"):
        value = row.get(key)
        if not isinstance(value, dict) or type(value.get("known")) is not bool or not _strings(value.get("reasons")):
            raise ValueError("Malformed projected known evidence")
        if value["known"] and value["reasons"] or not value["known"] and not value["reasons"]:
            raise ValueError("Projected known/reason contradiction")
        if value["known"]:
            if key in ("rank20", "rank60") and (not _bounded(value.get("value")) or type(value.get("count")) is not int or not 1 <= value["count"] <= 3141):
                raise ValueError("Malformed known rank")
            if key == "amountPrior20" and not _positive(value.get("value")):
                raise ValueError("Malformed known amount")
            if key == "history20":
                values, counts = value.get("values"), value.get("counts")
                if (not isinstance(values, list) or len(values) != 20 or not all(_bounded(v) for v in values)
                    or not isinstance(counts, list) or len(counts) != 20 or not all(type(n) is int and 1 <= n <= 3141 for n in counts)
                    or value.get("dates") != days[-20:] or value.get("mean") != mean(values) or value.get("minimum") != min(values)
                    or row["rank20"]["known"] is not True or values[-1] != row["rank20"]["value"] or counts[-1] != row["rank20"]["count"]):
                    raise ValueError("Malformed known history")
    qualification = row.get("qualification")
    if not isinstance(qualification, dict) or qualification.get("state") not in ("true", "false", "unknown"):
        raise ValueError("Malformed qualification")
    expected = _qualification({k: qualification.get(k) for k in ("eligible", "reason", "unknown")})
    if qualification["state"] != expected["state"]:
        raise ValueError("Qualification state contradicts evidence")


def _early(previous, current):
    history, rank = previous["history20"], current["rank60"]
    predicates = {"priorHistoryMean": _predicate(history["known"], history["mean"], HISTORY_MEAN_MAX, "<="),
                  "rank60AtFormation": _predicate(rank["known"], rank["value"], RANK60_MAX, "<=")}
    return {"state": _combine([p["state"] for p in predicates.values()]), "predicates": predicates,
            "historyDate": previous["date"], "rank60Date": current["date"]}


def _selection(day, raw):
    full = sorted((deepcopy(r) for r in raw if r["selectionEligible"]), key=lambda r: (-r["prior20MeanAmount"], r["symbol"]))
    if len({r["symbol"] for r in full}) != len(full):
        raise ValueError("Duplicate same-day confirmation")
    ranking = [{**r, "rank": n, "selectedTop5": n <= TOP_N} for n, r in enumerate(full, 1)]
    slots = [{**r, "candidateAdmitted": r["earlyMask"] == "true", "baselineAdmitted": True} for r in ranking[:TOP_N]]
    signals = []
    for sid in GROUPS:
        for slot in slots:
            if sid == CONTROL_ID or slot["candidateAdmitted"]:
                signals.append({**deepcopy(slot), "version": VERSION, "strategyId": sid,
                    "pairedStrategyId": CONTROL_ID if sid == CANDIDATE_ID else CANDIDATE_ID,
                    "signalDate": day, "eligible": True, "certified": False, "targetAchieved": False})
    return {"date": day, "candidateId": CANDIDATE_ID, "controlId": CONTROL_ID, "fullRanking": ranking,
            "slots": slots, "signals": signals, "policyCounts": {sid: sum(r["strategyId"] == sid for r in signals) for sid in GROUPS}}


class EarlyStrengthEmergenceStudy:
    """Position-free first confirmation; only previous projections and episodes persist."""
    def __init__(self, target_calendar, symbols, *, formation_calendar=None):
        self._calendar = _calendar(target_calendar)
        self._full = _calendar(formation_calendar if formation_calendar is not None else target_calendar)
        if not isinstance(symbols, (list, tuple)) or not symbols or len(set(symbols)) != len(symbols):
            raise ValueError("Unique fixed symbol population required")
        self._symbols = sorted(_symbol(s) for s in symbols)
        self._symbol_set = set(self._symbols)
        self._positions = {d: i for i, d in enumerate(self._full)}
        start = self._positions.get(self._calendar[0])
        if start is None or self._full[start:start+len(self._calendar)] != self._calendar:
            raise ValueError("Target calendar must be a contiguous full-calendar segment")
        self._index, self._previous, self._finished = -1, None, False
        self._episodes, self._locks = [], {}

    def _counts(self, index):
        return {"activatedEpisodes": len(self._episodes), "lockedSymbols": sum(index <= e["lockThroughIndex"] for e in self._locks.values()),
                "stateCounts": dict(sorted(Counter(e["state"] for e in self._episodes).items()))}

    def _transition(self, episode, day, index, state, reason, evidence):
        old = episode["state"]
        episode.update(state=state, reason=reason, lastProcessedDate=day)
        if state != "observing":
            episode.update(terminalDate=day, terminalIndex=index)
        return {"date": day, "index": index, "symbol": episode["symbol"], "episodeId": episode["episodeId"],
                "offset": index-episode["formationIndex"], "fromState": old, "toState": state,
                "reason": reason, "evidence": deepcopy(evidence)}

    def step(self, day, index, features_by_symbol, raw_by_symbol, benchmark6):
        if (self._finished or type(index) is not int or index != self._index+1 or not 0 <= index < len(self._calendar)
                or _day(day) != self._calendar[index]):
            raise ValueError("Consume every target session once in order")
        if (not isinstance(features_by_symbol, dict) or set(features_by_symbol) != self._symbol_set
                or not isinstance(raw_by_symbol, dict) or set(raw_by_symbol) != self._symbol_set):
            raise ValueError("Full fixed-symbol projection/raw population required")
        pos = self._positions[day]
        calendar61, calendar6 = self._full[max(0, pos-60):pos+1], self._full[max(0, pos-5):pos+1]
        for symbol in self._symbols:
            _validate_projection(features_by_symbol[symbol], symbol, day, calendar61)
        transitions, confirmations, formations, evaluations = [], [], [], []
        # Existing episodes advance first; no eligibility filter erases the rank observation.
        for episode in self._locks.values():
            if episode["state"] != "observing":
                continue
            current = features_by_symbol[episode["symbol"]]
            offset = index-episode["formationIndex"]
            if not 1 <= offset <= CONFIRMATION_OFFSET:
                raise ValueError("Unconsumed observation outside its fixed clock")
            rank = current["rank20"]
            evidence = {"rank20": rank, "qualification": current["qualification"]}
            episode["observationDates"].append(day)
            if not rank["known"]:
                transitions.append(self._transition(episode, day, index, "rankUnknown", "requiredRankBecameUnknown", evidence))
            elif rank["value"] < RANK_THRESHOLD:
                transitions.append(self._transition(episode, day, index, "rankLost", "rankBelowThreshold", evidence))
            elif offset == CONFIRMATION_OFFSET:
                qualification = current["qualification"]
                state = {"true": "confirmed", "false": "qualificationRejected", "unknown": "qualificationUnknown"}[qualification["state"]]
                episode["confirmationDate"] = day
                transitions.append(self._transition(episode, day, index, state, "firstTwoSessionConfirmation", evidence))
                confirmations.append({"date": day, "symbol": episode["symbol"], "episodeId": episode["episodeId"],
                    "formationDate": episode["formationDate"], "earlyMask": episode["earlyMask"],
                    "prior20MeanAmount": episode["prior20MeanAmount"], "qualification": deepcopy(qualification),
                    "selectionEligible": qualification["state"] == "true"})
            else:
                transitions.append(self._transition(episode, day, index, "observing", "rankRetained", evidence))
        for symbol in self._symbols:
            current = features_by_symbol[symbol]
            previous = None if self._previous is None else self._previous[symbol]
            if previous is None:
                crossing, crossing_reason = "unknown", "previousFullPanelUnavailable"
            elif not previous["rank20"]["known"] or not current["rank20"]["known"]:
                crossing, crossing_reason = "unknown", "requiredAdjacentRankUnknown"
            else:
                crossing = "true" if previous["rank20"]["value"] < RANK_THRESHOLD <= current["rank20"]["value"] else "false"
                crossing_reason = "knownUpcross" if crossing == "true" else "noUpcross"
            locked = self._locks.get(symbol)
            locked = locked if locked is not None and index <= locked["lockThroughIndex"] else None
            evaluation = {"date": day, "index": index, "symbol": symbol, "previousDate": None if previous is None else previous["date"],
                "previousRank20": None if previous is None else deepcopy(previous["rank20"]), "rank20": deepcopy(current["rank20"]),
                "crossing": crossing, "crossingReason": crossing_reason,
                "lockedByEpisodeId": None if locked is None else locked["episodeId"],
                "lockThroughIndex": None if locked is None else locked["lockThroughIndex"], "formationDecision": "notFormed"}
            if crossing == "true" and locked is not None:
                evaluation["formationDecision"] = "formationSuppressedByLock"
                formations.append({"date": day, "index": index, "symbol": symbol, "kind": "formationSuppressedByLock",
                    "episodeId": None, "lockedByEpisodeId": locked["episodeId"], "lockThroughIndex": locked["lockThroughIndex"], "episode": None})
            elif crossing == "true":
                advance = evaluate_advance(symbol, day, calendar6, raw_by_symbol[symbol], benchmark6)
                early = _early(previous, current)
                common = {"qualification": current["qualification"]["state"],
                          "amountPrior20": "true" if current["amountPrior20"]["known"] else "unknown",
                          "own5": advance["predicates"]["own5"]["state"], "excess5": advance["predicates"]["excess5"]["state"]}
                gate = _combine(list(common.values()))
                state = {"true": "observing", "false": "formationRejected", "unknown": "formationUnknown"}[gate]
                episode = {"episodeId": day+":"+symbol, "symbol": symbol, "formationDate": day, "formationIndex": index,
                    "lockThroughIndex": index+CONFIRMATION_OFFSET,
                    "deadlineDate": self._calendar[index+CONFIRMATION_OFFSET] if index+CONFIRMATION_OFFSET < len(self._calendar) else None,
                    "state": state, "reason": "formationCommon"+gate.title(), "earlyMask": early["state"],
                    "earlyMaskEvidence": early, "formationCommon": {"state": gate, "predicates": common},
                    "prior20MeanAmount": current["amountPrior20"]["value"],
                    "formationEvidence": {"previousRank20": deepcopy(previous["rank20"]), "rank20": deepcopy(current["rank20"]),
                        "qualification": deepcopy(current["qualification"]), "amountPrior20": deepcopy(current["amountPrior20"]), "advance": advance},
                    "lastProcessedDate": day, "terminalDate": day if state != "observing" else None,
                    "terminalIndex": index if state != "observing" else None, "confirmationDate": None, "observationDates": []}
                self._episodes.append(episode)
                self._locks[symbol] = episode
                formations.append({"date": day, "index": index, "symbol": symbol, "kind": state,
                    "episodeId": episode["episodeId"], "lockedByEpisodeId": None,
                    "lockThroughIndex": episode["lockThroughIndex"], "episode": deepcopy(episode)})
                evaluation["formationDecision"] = state
            evaluations.append(evaluation)
        selection = _selection(day, confirmations)
        self._previous = deepcopy(features_by_symbol)
        self._index = index
        return deepcopy({"version": VERSION, "date": day, "index": index, "evaluations": evaluations,
            "formations": formations, "transitions": transitions, "rawConfirmations": confirmations,
            "selection": selection, "signals": selection["signals"], "episodeCounts": self._counts(index),
            "certified": False, "targetAchieved": False})

    def finish(self, cutoff):
        if self._finished or self._index < 0 or _day(cutoff) != self._calendar[self._index]:
            raise ValueError("Finish once at the last consumed session")
        transitions = []
        for episode in self._locks.values():
            if episode["state"] == "observing":
                if self._index >= episode["lockThroughIndex"]:
                    raise ValueError("Unconsumed confirmation cannot become censored")
                transitions.append(self._transition(episode, cutoff, self._index, "censoredAtStudyCutoff",
                                                     "studyEndedBeforeConfirmation", {}))
        self._finished = True
        return deepcopy({"version": VERSION, "cutoff": cutoff, "lastIndex": self._index,
            "transitions": transitions, "episodes": self._episodes, "episodeCounts": self._counts(self._index),
            "certified": False, "targetAchieved": False})
