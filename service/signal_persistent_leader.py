"""One stateless, raw-price persistent-leadership candidate.

Reads archived panel strength values; never computes a new cross-section,
changes an old rank, uses positions, or executes a trade. The caller must bind
the audited panel/source hashes and register the executable before real use.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import math
from statistics import mean


VERSION = "raw-persistent-price-leader-v1"
CANDIDATE_ID = "rawPersistentPriceLeaderBreakoutDiagnostic"
PANEL_FEATURE_VERSION = "daily-leadership-raw-v1"
HISTORY_SLOTS, TOP_N = 61, 5
UNKNOWN_BASE_REASONS = {"invalidHistory", "missingDailyBar", "notObservedYet"}
THRESHOLDS = (
    ("meanStrengthPercentile20", ">=", .90),
    ("minimumStrengthPercentile20", ">=", .80),
    ("relativeReturn60Percentile", ">=", .90),
    ("closePathMaxDrawdown20", "<=", .10),
    ("signedPathEfficiency20", ">=", .35),
)


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _bounded(value, low=0, high=1):
    return _finite(value) and low <= value <= high


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Dates must be YYYY-MM-DD")
    return value


def _dates(values, allowed):
    return (isinstance(values, list) and all(isinstance(v, str) for v in values)
            and values == sorted(set(values)) and set(values).issubset(allowed))


def _complete(group, required, window):
    if not isinstance(group, dict) or group.get("complete") is not True:
        return False
    if any(type(group.get(k)) is not int or group[k] != required for k in
           ("requiredSessions", "availableCalendarSessions", "observedSessions", "validSessions")):
        return False
    return (all(group.get(key) == [] for key in ("missingDates", "invalidDates", "haltDates", "reasons"))
            and _dates(group.get("unknownTradeStatusDates"), set(window)))


def _raw_window_valid(bars, days):
    return bool(days) and len(bars) == len(days) and all(
        isinstance(bar, dict) and bar.get("date") == day and bar.get("priceBasisId") == "raw-unadjusted"
        and all(_finite(bar.get(k)) and bar[k] > 0 for k in ("open", "high", "low", "close", "volume"))
        and bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]
        for bar, day in zip(bars, days))


def _capture_predicates(panel, days, bars, predicates):
    """Preserve independently knowable predicates even if final entry is barred.

    This evidence pass cannot admit a candidate: all original full-panel/raw
    and base gates below still decide its exclusive kind. A missing strength
    archive does not erase an independently known path or raw breakout.
    """
    groups = panel.get("completeness")
    groups = groups if isinstance(groups, dict) else {}
    reasons = panel.get("featureReasons")
    reasons = reasons if isinstance(reasons, dict) else None
    history = panel.get("trailing20DailyStrengthPercentiles")
    counts = panel.get("trailing20StrengthCrossSectionCounts")
    history_ok = (isinstance(history, list) and len(history) == 20 and all(_bounded(v) for v in history)
        and isinstance(counts, list) and len(counts) == 20
        and all(type(n) is int and 1 <= n <= 3141 for n in counts)
        and panel.get("strengthRankSessionDates20") == list(days[-20:])
        and type(panel.get("knownStrengthRankSessions20")) is int and panel["knownStrengthRankSessions20"] == 20
        and type(panel.get("availableStrengthRankSessions20")) is int and panel["availableStrengthRankSessions20"] == 20
        and _bounded(panel.get("relativeReturn20Percentile"))
        and type(panel.get("relativeReturn20CrossSectionCount")) is int
        and history[-1] == panel.get("relativeReturn20Percentile")
        and counts[-1] == panel.get("relativeReturn20CrossSectionCount")
        and _finite(panel.get("return20")) and _finite(panel.get("relativeReturn20")))
    halts = panel.get("haltDates")
    halts_known = _dates(halts, set(days))

    def raw_known(length):
        return (len(days) >= length and halts_known and not set(halts).intersection(days[-length:])
                and all(_dates(panel.get(key), set(days)) and not set(panel[key]).intersection(days[-length:])
                        for key in ("missingDates", "invalidDates"))
                and _raw_window_valid(bars[-length:], days[-length:]))

    for field, operator, threshold in THRESHOLDS:
        value = panel.get(field)
        bounds_ok = _bounded(value, -1 if field == "signedPathEfficiency20" else 0)
        predicates[field]["value"] = value if bounds_ok else None
        if not bounds_ok or reasons is None or reasons.get(field):
            continue
        if field in ("meanStrengthPercentile20", "minimumStrengthPercentile20"):
            if (not history_ok or not raw_known(40) or reasons.get("return20") or reasons.get("relativeReturn20")
                    or not all(_complete(groups.get(k), 21, days[-21:]) for k in ("security20", "benchmark20"))):
                continue
            expected = mean(history) if field == "meanStrengthPercentile20" else min(history)
            if value != expected:
                continue
        elif field == "relativeReturn60Percentile":
            size = panel.get("relativeReturn60CrossSectionCount")
            if (not raw_known(61) or type(size) is not int or not 1 <= size <= 3141
                    or not _finite(panel.get("return60")) or not _finite(panel.get("relativeReturn60"))
                    or reasons.get("return60") or reasons.get("relativeReturn60")
                    or not all(_complete(groups.get(k), 61, days) for k in ("security60", "benchmark60"))):
                continue
        elif not raw_known(21) or not _complete(groups.get("path20"), 21, days[-21:]):
            continue
        predicates[field].update(passed=value >= threshold if operator == ">=" else value <= threshold, reason=None)
    if raw_known(6):
        value, threshold = bars[-1]["close"], max(bar["high"] for bar in bars[-6:-1])
        predicates["closeAbovePrior5High"].update(value=value, threshold=threshold, passed=value > threshold, reason=None)


def evaluate_persistent_leader(symbol, day, calendar61, raw61, panel_row):
    """Return an exclusive kind and evidence for all six registered predicates.

    Identity/version failures are unknown, not another security's rejection.
    The base sidecar is a diagnostic decision label, not independent proof of
    listing age or amount. All required panel/raw evidence precedes threshold
    decisions. Fully observed false predicates become notTriggered; missing or
    contradictory evidence becomes unevaluable. Inputs are never modified.
    """
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("Symbol is required")
    day = _day(day)
    predicates = {field: {"value": None, "operator": op, "threshold": threshold, "passed": None,
                          "reason": "requiredEvidenceUnavailable"}
                  for field, op, threshold in THRESHOLDS}
    predicates["closeAbovePrior5High"] = {"value": None, "operator": ">", "threshold": None, "passed": None,
                                          "reason": "requiredEvidenceUnavailable"}
    features = {}
    quality = {"certified": False, "targetAchieved": False, "priceBasisId": "raw-unadjusted",
               "actionEvidence": "unverified", "historicalEligibilityVerified": False,
               "interpretation": "price-leadership-diagnostic;not-market-influence",
               "unknownHistoricalFields": []}

    def finish(kind, reason, signal=None):
        return {"version": VERSION, "candidateId": CANDIDATE_ID, "symbol": symbol, "date": day,
                "kind": kind, "reason": reason, "predicates": deepcopy(predicates),
                "features": deepcopy(features), "quality": deepcopy(quality), "signal": deepcopy(signal)}

    if not isinstance(panel_row, dict) or panel_row.get("symbol") != symbol or panel_row.get("asOfDate") != day:
        return finish("unevaluable", "panelIdentityMismatch")
    if panel_row.get("featureVersion") != PANEL_FEATURE_VERSION:
        return finish("unevaluable", "panelFeatureVersionMismatch")
    # Collect causal evidence before eligibility classification; this pass has
    # no effect on whether all mandatory requirements for a signal are met.
    try:
        evidence_calendar_ok = (isinstance(calendar61, (list, tuple)) and isinstance(raw61, (list, tuple))
            and 0 < len(calendar61) == len(raw61) <= 61 and calendar61[-1] == day
            and all(_day(d) <= day for d in calendar61)
            and all(a < b for a, b in zip(calendar61, calendar61[1:])))
    except (ValueError, TypeError):
        evidence_calendar_ok = False
    if evidence_calendar_ok and panel_row.get("rawPriceBasis") == "raw-unadjusted":
        _capture_predicates(panel_row, calendar61, raw61, predicates)
    state = panel_row.get("baseEligibility")
    if (not isinstance(state, dict) or type(state.get("eligible")) is not bool
            or not isinstance(state.get("reason"), str) or not state["reason"]
            or not isinstance(state.get("unknown"), list) or not all(isinstance(k, str) for k in state["unknown"])):
        return finish("unevaluable", "baseEligibilityUnknown")
    quality["unknownHistoricalFields"] = deepcopy(state["unknown"])
    features["baseEligibility"] = {key: deepcopy(state[key]) for key in ("eligible", "reason", "unknown")}
    if state["reason"] in UNKNOWN_BASE_REASONS:
        return finish("unevaluable", state["reason"])
    if state["eligible"] is False:
        return finish("ineligible", "legacyDiagnosticEligibilityRejected")
    if not isinstance(calendar61, (list, tuple)) or not isinstance(raw61, (list, tuple)):
        return finish("unevaluable", "invalidHistoryWindow")
    if len(calendar61) != HISTORY_SLOTS or len(raw61) != HISTORY_SLOTS:
        return finish("unevaluable", "insufficient61SessionHistory")
    try:
        calendar_ok = (calendar61[-1] == day and all(_day(d) <= day for d in calendar61)
                       and all(a < b for a, b in zip(calendar61, calendar61[1:])))
    except (ValueError, TypeError):
        calendar_ok = False
    if not calendar_ok:
        return finish("unevaluable", "invalidExchangeCalendarAlignment")
    for expected, bar in zip(calendar61, raw61):
        if bar is None:
            features["invalidDate"] = expected
            return finish("unevaluable", "missingAlignedRawBar")
        if not isinstance(bar, dict) or bar.get("date") != expected:
            features["invalidDate"] = expected
            return finish("unevaluable", "rawHistoryDateMismatch")
        if bar.get("priceBasisId") != "raw-unadjusted":
            features["invalidDate"] = expected
            return finish("unevaluable", "rawPriceBasisUnknown")
        if not all(_finite(bar.get(k)) and bar[k] > 0 for k in ("open", "high", "low", "close", "volume")):
            features["invalidDate"] = expected
            return finish("unevaluable", "nonpositiveOrUnknownRawOHLCV")
        if not bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]:
            features["invalidDate"] = expected
            return finish("unevaluable", "invalidRawOHLCEnvelope")
    if panel_row.get("rawPriceBasis") != "raw-unadjusted":
        return finish("unevaluable", "panelRawPriceBasisUnknown")
    if (any(panel_row.get(k) != [] for k in ("missingDates", "invalidDates", "haltDates"))
            or not _dates(panel_row.get("unknownTradeStatusDates"), set(calendar61))):
        return finish("unevaluable", "panelRawWindowEvidenceUnavailable")
    if any(type(panel_row.get(k)) is not int or panel_row[k] != 61 for k in
           ("requiredSessions", "availableCalendarSessions", "observedSessions", "validSessions")):
        return finish("unevaluable", "panelRawWindowCountsMismatch")
    groups = panel_row.get("completeness")
    if not isinstance(groups, dict):
        return finish("unevaluable", "panelCompletenessUnknown")
    for name, required in (("security60", 61), ("benchmark60", 61), ("security20", 21), ("benchmark20", 21), ("path20", 21)):
        if not _complete(groups.get(name), required, calendar61[-required:]):
            features["unavailableGroup"] = name
            return finish("unevaluable", "requiredPanelWindowUnavailable")
    if not _complete(panel_row.get("benchmarkQuality"), 61, calendar61):
        return finish("unevaluable", "benchmarkEvidenceUnavailable")

    history = panel_row.get("trailing20DailyStrengthPercentiles")
    counts = panel_row.get("trailing20StrengthCrossSectionCounts")
    history_dates = panel_row.get("strengthRankSessionDates20")
    if (not isinstance(history, list) or len(history) != 20 or not all(_bounded(v) for v in history)
            or not isinstance(counts, list) or len(counts) != 20
            or not all(type(n) is int and 1 <= n <= 3141 for n in counts)
            or history_dates != list(calendar61[-20:])
            or type(panel_row.get("knownStrengthRankSessions20")) is not int
            or panel_row["knownStrengthRankSessions20"] != 20
            or type(panel_row.get("availableStrengthRankSessions20")) is not int
            or panel_row["availableStrengthRankSessions20"] != 20):
        return finish("unevaluable", "archivedStrengthHistoryUnavailable")
    for horizon in (20, 60):
        percentile = panel_row.get(f"relativeReturn{horizon}Percentile")
        size = panel_row.get(f"relativeReturn{horizon}CrossSectionCount")
        if (not _bounded(percentile) or type(size) is not int or not 1 <= size <= 3141
                or not _finite(panel_row.get(f"return{horizon}"))
                or not _finite(panel_row.get(f"relativeReturn{horizon}"))):
            return finish("unevaluable", "currentStrengthRankUnavailable")
    if (history[-1] != panel_row["relativeReturn20Percentile"]
            or counts[-1] != panel_row["relativeReturn20CrossSectionCount"]):
        return finish("unevaluable", "archivedCurrentRankMismatch")
    if (not _bounded(panel_row.get("meanStrengthPercentile20"))
            or not _bounded(panel_row.get("minimumStrengthPercentile20"))
            or mean(history) != panel_row["meanStrengthPercentile20"]
            or min(history) != panel_row["minimumStrengthPercentile20"]):
        return finish("unevaluable", "archivedStrengthAggregateMismatch")
    if (not _bounded(panel_row.get("closePathMaxDrawdown20"))
            or not _bounded(panel_row.get("signedPathEfficiency20"), -1, 1)):
        return finish("unevaluable", "pathFeatureUnavailable")
    feature_reasons = panel_row.get("featureReasons")
    required_fields = {field for field, _, _ in THRESHOLDS} | {
        "return20", "return60", "relativeReturn20", "relativeReturn60"}
    if (not isinstance(feature_reasons, dict)
            or any(feature_reasons.get(field) for field in required_fields)):
        return finish("unevaluable", "panelRequiredFeatureContradiction")

    for field, operator, threshold in THRESHOLDS:
        value = panel_row[field]
        predicates[field].update(value=value, passed=value >= threshold if operator == ">=" else value <= threshold, reason=None)
    prior5 = raw61[-6:-1]
    prior_high = max(bar["high"] for bar in prior5)
    current = raw61[-1]
    predicates["closeAbovePrior5High"].update(value=current["close"], threshold=prior_high,
        passed=current["close"] > prior_high, reason=None)
    features.update(historyStart=calendar61[0], historyEnd=day, observations=61,
        panelFeatureVersion=PANEL_FEATURE_VERSION,
        archivedRankDates20=list(history_dates), archivedRankPercentiles20=list(history),
        archivedRankCrossSectionCounts20=list(counts), prior5High=prior_high,
        prior5Window={"startDate": calendar61[-6], "endDate": calendar61[-2], "sessions": 5},
        breakoutBars=[{key: bar[key] for key in ("date", "open", "high", "low", "close", "volume")} for bar in raw61[-6:]],
        requiredPanelValues={key: panel_row[key] for key, _, _ in THRESHOLDS},
        failedPredicates=[key for key, value in predicates.items() if value["passed"] is False])
    if features["failedPredicates"]:
        return finish("notTriggered", features["failedPredicates"][0] + "NotMet")
    signal = {"version": VERSION, "strategyId": CANDIDATE_ID, "symbol": symbol, "signalDate": day,
        "meanStrengthPercentile20": panel_row["meanStrengthPercentile20"],
        "relativeReturn60Percentile": panel_row["relativeReturn60Percentile"],
        "features": features, "predicates": predicates, "quality": quality,
        "certified": False, "targetAchieved": False, "actionEvidence": "unverified"}
    return finish("signaled", "persistentPriceLeadershipBreakoutObserved", signal)


def rank_persistent_leader_signals(signals):
    """Copy/rank all raw candidates, marking Top5 before external active checks."""
    rows = deepcopy(list(signals))
    dates, symbols = set(), set()
    for row in rows:
        if (not isinstance(row, dict) or row.get("strategyId") != CANDIDATE_ID
                or not isinstance(row.get("symbol"), str) or not row["symbol"] or row["symbol"] in symbols
                or not _bounded(row.get("meanStrengthPercentile20"))
                or not _bounded(row.get("relativeReturn60Percentile"))):
            raise ValueError("Rank unique raw candidate signals with known bounded ranking fields")
        symbols.add(row["symbol"])
        dates.add(_day(row.get("signalDate")))
    if len(dates) > 1:
        raise ValueError("Rank one signal session at a time")
    rows.sort(key=lambda row: (-row["meanStrengthPercentile20"], -row["relativeReturn60Percentile"], row["symbol"]))
    for rank, row in enumerate(rows, 1):
        row.update(rank=rank, selectedTop5=rank <= TOP_N,
                   score=row["meanStrengthPercentile20"], rawScore=row["meanStrengthPercentile20"])
    return rows
