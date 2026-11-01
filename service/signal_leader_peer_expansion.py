"""Causal, fixed-membership benchmark-excess peer expansion diagnostics.

The caller binds the historical universe and supplies the original parent
qualification at F and T. F=T-10: 61 closes ending at F form 60 returns;
11 closes from F through T measure two subsequent five-session intervals.
Subtracting benchmark log returns is not beta adjustment or factor removal.
No I/O, future labels, holdings, eligibility refresh or member replacement.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date
import math

VERSION = "raw-leader-peer-expansion-v1"
CANDIDATE_ID = "rawLeaderPeerExpansion"
CONTROL_ID = "control_rawLeaderPeerExpansion"
PARENT_ID = "rawPersistentPriceLeaderBreakoutDiagnostic"
HISTORY_SLOTS, GROUP_SIZE = 71, 20
FORMATION_SLOTS, EXPANSION_SLOTS = 61, 11
MIN_CORRELATION = .30


def _positive(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Dates must be canonical YYYY-MM-DD")
    return value


def _symbol(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A nonempty symbol is required")
    return value


def _mapping(value):
    if not isinstance(value, dict):
        raise ValueError("Symbol inputs must be mappings")
    for symbol in value:
        _symbol(symbol)
    return value


def _calendar(values, slots, *, first=None, last=None):
    if not isinstance(values, (list, tuple)) or len(values) != slots:
        return "insufficientAlignedHistory"
    try:
        canonical = all(_day(day) == day for day in values)
    except (ValueError, TypeError):
        return "invalidExchangeCalendarAlignment"
    if not canonical or any(a >= b for a, b in zip(values, values[1:])) or (
            first is not None and values[0] != first) or (last is not None and values[-1] != last):
        return "invalidExchangeCalendarAlignment"
    return None


def _eligibility(value):
    if not isinstance(value, dict) or type(value.get("eligible")) is not bool:
        return "unevaluable", "baseEligibilityUnknown"
    unknown = value.get("unknown")
    if (not isinstance(unknown, list) or any(not isinstance(x, str) or not x.strip() for x in unknown)
            or len(unknown) != len(set(unknown))):
        return "unevaluable", "invalidBaseEligibilityUnknownFields"
    reason = value.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return "unevaluable", "invalidBaseEligibilityReason"
    if value["eligible"] is False:
        return "ineligible", "baseEligibilityRejected"
    if reason != "eligible":
        return "unevaluable", "inconsistentBaseEligibilityReason"
    return "eligible", "eligible"


def _window(raw, days):
    if not isinstance(raw, (list, tuple)) or len(raw) != len(days):
        return "insufficientAlignedRawHistory", None
    for index, (expected, bar) in enumerate(zip(days, raw)):
        if bar is None:
            return "missingAlignedRawBar", index
        if not isinstance(bar, dict) or bar.get("date") != expected:
            return "rawHistoryDateMismatch", index
        if bar.get("priceBasisId") != "raw-unadjusted":
            return "rawPriceBasisUnknown", index
        if "knownSuspended" in bar and type(bar["knownSuspended"]) is not bool:
            return "invalidKnownSuspensionFlag", index
        if bar.get("knownSuspended") is True:
            return ("suspensionContradictsPositiveVolume" if _positive(bar.get("volume")) else "knownSuspension"), index
        if not all(_positive(bar.get(key)) for key in ("open", "high", "low", "close", "volume")):
            return "nonpositiveOrUnknownRawOHLCV", index
        if not bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]:
            return "invalidRawOHLCEnvelope", index
    return None, None


def _logs(raw):
    try:
        result = [math.log(b["close"]/a["close"]) for a, b in zip(raw, raw[1:])]
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    return result if all(math.isfinite(value) for value in result) else None


def _center(values):
    average = math.fsum(values)/len(values)
    centered = [value-average for value in values]
    variance = math.fsum(value*value for value in centered)
    return centered, variance


def _quality():
    return {"priceBasisId": "raw-unadjusted", "actionEvidence": "unverified", "certified": False,
        "targetAchieved": False, "historicalEligibilityVerified": False, "historicalTradeStatusVerified": False,
        "comparableRightsVerified": False,
        "interpretation": "benchmark-excess-log-return association;not beta-adjusted or causal peer influence",
        "unknownTradeStatusPolicy": "No known suspension plus positive bars does not certify historical trading status"}


def form_peer_group(anchor, as_of, calendar61, raw_by_symbol, benchmark61, eligibility_by_symbol):
    """Evaluate every supplied F-universe symbol and freeze up to 20 peers.

    The caller supplies only F-60..F. No future suffix is silently truncated.
    All failures remain individual evidence; partial groups cannot be expanded.
    rawCorrelation is the two-pass Pearson output; correlation is projected
    to [-1,1] before the registered threshold and deterministic ordering.
    """
    anchor, as_of = _symbol(anchor), _day(as_of)
    raw_by_symbol, eligibility_by_symbol = _mapping(raw_by_symbol), _mapping(eligibility_by_symbol)
    days = list(calendar61) if isinstance(calendar61, (list, tuple)) else []
    symbols = sorted(raw_by_symbol)
    calendar_reason = _calendar(calendar61, FORMATION_SLOTS, last=as_of)
    anchor_state, anchor_reason = _eligibility(eligibility_by_symbol.get(anchor))
    benchmark_reason, benchmark_slot = _window(benchmark61, days) if calendar_reason is None else (calendar_reason, None)
    anchor_raw_reason, anchor_slot = _window(raw_by_symbol.get(anchor), days) if calendar_reason is None else (calendar_reason, None)
    benchmark_logs = _logs(benchmark61) if benchmark_reason is None else None
    if benchmark_reason is None and benchmark_logs is None:
        benchmark_reason = "nonfiniteBenchmarkLogReturn"
    anchor_center, anchor_variance = None, None
    global_reason = (calendar_reason or ("anchor:"+anchor_reason if anchor_state != "eligible" else None)
                     or ("benchmark:"+benchmark_reason if benchmark_reason else None)
                     or ("anchor:"+anchor_raw_reason if anchor_raw_reason else None))
    if global_reason is None:
        anchor_logs = _logs(raw_by_symbol[anchor])
        if anchor_logs is None:
            global_reason = "anchor:nonfiniteLogReturn"
        else:
            anchor_excess = [value-base for value, base in zip(anchor_logs, benchmark_logs)]
            anchor_center, anchor_variance = _center(anchor_excess)
            if not math.isfinite(anchor_variance) or anchor_variance <= 0:
                global_reason = "anchor:zeroOrNonfiniteExcessVariance"
    evaluations = []
    for symbol in symbols:
        eligible = eligibility_by_symbol.get(symbol)
        record = {"symbol": symbol, "status": "unevaluable", "reason": None, "rawCorrelation": None,
            "correlation": None, "excessVariance": None, "selected": False, "rank": None,
            "eligibility": deepcopy(eligible), "invalidSlot": None}
        if symbol == anchor:
            record.update(status="excludedSelf", reason="excludedSelf")
        else:
            state, reason = _eligibility(eligible)
            raw_reason, slot = _window(raw_by_symbol[symbol], days) if calendar_reason is None else (calendar_reason, None)
            if state != "eligible":
                record.update(status=state, reason=reason)
            elif raw_reason is not None:
                record.update(reason=raw_reason, invalidSlot=slot)
            elif global_reason is not None:
                record.update(reason=global_reason)
            else:
                logs = _logs(raw_by_symbol[symbol])
                if logs is None:
                    record.update(reason="nonfiniteLogReturn")
                else:
                    centered, variance = _center([value-base for value, base in zip(logs, benchmark_logs)])
                    record["excessVariance"] = variance if math.isfinite(variance) else None
                    if not math.isfinite(variance) or variance <= 0:
                        record.update(reason="zeroOrNonfiniteExcessVariance")
                    else:
                        covariance = math.fsum(x*y for x, y in zip(anchor_center, centered))
                        denominator = math.sqrt(anchor_variance)*math.sqrt(variance)
                        rho = covariance/denominator if denominator > 0 else float("nan")
                        if not math.isfinite(rho):
                            record.update(reason="nonfiniteCorrelation")
                        else:
                            correlation = max(-1., min(1., rho))
                            passes = correlation >= MIN_CORRELATION
                            record.update(rawCorrelation=rho, correlation=correlation,
                                status="qualified" if passes else "belowThreshold",
                                reason="correlationAtLeast030" if passes else "correlationBelow030")
        evaluations.append(record)
    ranked = sorted((r for r in evaluations if r["status"] == "qualified"), key=lambda r: (-r["correlation"], r["symbol"]))
    for rank, record in enumerate(ranked, 1):
        record.update(rank=rank, selected=rank <= GROUP_SIZE)
    members = [r["symbol"] for r in ranked[:GROUP_SIZE]]
    counts = {"inputSymbols": len(symbols), "candidateEvaluations": len(evaluations), "qualifiedPeers": len(ranked),
        "selectedCount": len(members), "statusCounts": dict(Counter(r["status"] for r in evaluations)),
        "reasonCounts": dict(Counter(r["reason"] for r in evaluations))}
    return {"version": VERSION, "anchor": anchor, "formationDate": as_of,
        "kind": "formed" if len(members) == GROUP_SIZE and global_reason is None else "unevaluable",
        "reason": global_reason or ("fixedPeerGroupFormed" if len(members) == GROUP_SIZE else "insufficientCorrelatedPeers"),
        "window": {"startDate": days[0] if days else None, "endDate": as_of, "closeSessions": FORMATION_SLOTS,
            "returnSessions": FORMATION_SLOTS-1, "providedCalendarSessions": len(days)},
        "members": members, "correlations": {r["symbol"]: r["correlation"] for r in ranked[:GROUP_SIZE]},
        "selectedCount": len(members), "requiredCount": GROUP_SIZE, "candidateEvaluations": evaluations, "counts": counts,
        "anchorEvidence": {"eligibility": deepcopy(eligibility_by_symbol.get(anchor)), "eligibilityState": anchor_state,
            "eligibilityReason": anchor_reason, "rawReason": anchor_raw_reason, "invalidSlot": anchor_slot,
            "excessVariance": anchor_variance},
        "benchmarkEvidence": {"reason": benchmark_reason, "invalidSlot": benchmark_slot}, "quality": _quality()}


def evaluate_peer_expansion(group, calendar11, raw_by_symbol, benchmark11):
    """Observe the frozen members on (F,F+5] and (F+5,T], never replace them."""
    if not isinstance(group, dict):
        raise ValueError("A formation record is required")
    anchor, formation = _symbol(group.get("anchor")), _day(group.get("formationDate"))
    raw_by_symbol = _mapping(raw_by_symbol)
    days = list(calendar11) if isinstance(calendar11, (list, tuple)) else []
    members = deepcopy(group.get("members"))
    calendar_reason = _calendar(calendar11, EXPANSION_SLOTS, first=formation)
    group_reason = None
    if (group.get("version") != VERSION or group.get("kind") != "formed" or group.get("selectedCount") != GROUP_SIZE
            or type(group.get("selectedCount")) is not int or group.get("requiredCount") != GROUP_SIZE):
        group_reason = "formationGroupNotComplete"
    elif (not isinstance(members, list) or len(members) != GROUP_SIZE or
          any(not isinstance(symbol, str) or not symbol.strip() for symbol in members) or
          len(set(members)) != GROUP_SIZE or anchor in members):
        group_reason = "invalidFixedMembership"
    elif (not isinstance(group.get("correlations"), dict) or set(group["correlations"]) != set(members) or
          any(not _finite(value) or not MIN_CORRELATION <= value <= 1 for value in group["correlations"].values())):
        group_reason = "invalidFrozenCorrelations"
    if not isinstance(members, list):
        members = []
    benchmark_reason, benchmark_slot = _window(benchmark11, days) if calendar_reason is None else (calendar_reason, None)
    benchmark_returns = None
    if benchmark_reason is None:
        try:
            benchmark_returns = (math.log(benchmark11[5]["close"]/benchmark11[0]["close"]),
                                 math.log(benchmark11[10]["close"]/benchmark11[5]["close"]))
            if not all(math.isfinite(value) for value in benchmark_returns):
                benchmark_reason = "nonfiniteBenchmarkLogReturn"
        except (ValueError, OverflowError, ZeroDivisionError):
            benchmark_reason = "nonfiniteBenchmarkLogReturn"
    global_reason = group_reason or calendar_reason or ("benchmark:"+benchmark_reason if benchmark_reason else None)
    evaluations = []
    for symbol in members:
        record = {"symbol": symbol, "kind": "unevaluable", "reason": None, "beforeRelativeLogReturn": None,
            "nowRelativeLogReturn": None, "beforePositive": None, "nowPositive": None, "invalidSlot": None}
        if not isinstance(symbol, str) or not symbol.strip():
            reason, slot = "invalidFixedMembership", None
        else:
            reason, slot = _window(raw_by_symbol.get(symbol), days) if calendar_reason is None else (calendar_reason, None)
        if reason is not None:
            record.update(reason=reason, invalidSlot=slot)
        elif global_reason is not None:
            record.update(reason=global_reason)
        else:
            raw = raw_by_symbol[symbol]
            try:
                before = math.log(raw[5]["close"]/raw[0]["close"])-benchmark_returns[0]
                now = math.log(raw[10]["close"]/raw[5]["close"])-benchmark_returns[1]
                valid = math.isfinite(before) and math.isfinite(now)
            except (ValueError, OverflowError, ZeroDivisionError):
                valid = False
            if not valid:
                record.update(reason="nonfiniteRelativeLogReturn")
            else:
                record.update(kind="observed", reason="fixedMemberObserved", beforeRelativeLogReturn=before,
                    nowRelativeLogReturn=now, beforePositive=before > 0, nowPositive=now > 0)
        evaluations.append(record)
    observed = sum(row["kind"] == "observed" for row in evaluations)
    complete = global_reason is None and observed == GROUP_SIZE
    before_count = sum(r["beforePositive"] for r in evaluations) if complete else None
    now_count = sum(r["nowPositive"] for r in evaluations) if complete else None
    change = now_count-before_count if complete else None
    conditions = {"nowAtLeast12": now_count >= 12 if complete else None, "increaseAtLeast4": change >= 4 if complete else None}
    expanded = all(conditions.values()) if complete else None
    return {"version": VERSION, "anchor": anchor, "formationDate": formation, "date": days[-1] if days else None,
        "kind": "expanded" if expanded else "notExpanded" if complete else "unevaluable",
        "reason": "peerExpansionObserved" if expanded else "peerExpansionThresholdNotMet" if complete else (global_reason or "memberPathUnevaluable"),
        "expanded": expanded, "members": members, "fixedDenominator": GROUP_SIZE, "observedMembers": observed,
        "beforeCount": before_count, "nowCount": now_count, "countChange": change, "conditions": conditions,
        "window": {"formationDate": formation, "middleDate": days[5] if len(days) > 5 else None,
            "endDate": days[-1] if days else None, "closeSessions": EXPANSION_SLOTS,
            "beforeReturnSessions": 5, "nowReturnSessions": 5, "providedCalendarSessions": len(days)},
        "memberEvaluations": evaluations, "benchmarkEvidence": {"reason": benchmark_reason, "invalidSlot": benchmark_slot},
        "quality": _quality()}


def select_peer_signals(day, ranked_parent_top5, outcomes_by_symbol):
    """Keep every already-ranked parent Top5 baseline; mask expanded anchors.

    Parent F/T qualification and rank are upstream. No sorting, promotion,
    matching, position exclusions or current member eligibility occurs here.
    """
    day = _day(day)
    if not isinstance(ranked_parent_top5, (list, tuple)) or len(ranked_parent_top5) > 5:
        raise ValueError("At most five ordered parent signals required")
    outcomes_by_symbol = _mapping(outcomes_by_symbol)
    seen, candidates, baseline, decisions = set(), [], [], []
    for expected_rank, parent in enumerate(ranked_parent_top5, 1):
        if not isinstance(parent, dict):
            raise ValueError("Parent signal must be an object")
        symbol = _symbol(parent.get("symbol"))
        if (symbol in seen or parent.get("signalDate") != day or parent.get("strategyId") != PARENT_ID or
            parent.get("eligible") is not True or parent.get("selectedTop5") is not True or
            type(parent.get("rank")) is not int or parent["rank"] != expected_rank):
            raise ValueError("Unique same-day contiguous parent Top5 ranks required")
        seen.add(symbol)
        outcome = outcomes_by_symbol.get(symbol)
        if outcome is None:
            kind, reason, formation = "unevaluable", "missingPeerExpansion", None
        else:
            if (not isinstance(outcome, dict) or outcome.get("version") != VERSION or outcome.get("anchor") != symbol or
                outcome.get("date") != day or outcome.get("kind") not in ("expanded", "notExpanded", "unevaluable")):
                raise ValueError("Peer outcome identity, date or kind differs")
            kind, reason, formation = outcome["kind"], outcome.get("reason"), outcome.get("formationDate")
            if not isinstance(reason, str) or not reason or _day(formation) >= day:
                raise ValueError("Peer outcome formation/reason differs")
            if kind in ("expanded", "notExpanded"):
                before, now = outcome.get("beforeCount"), outcome.get("nowCount")
                if (type(before) is not int or type(now) is not int or not 0 <= before <= GROUP_SIZE or not 0 <= now <= GROUP_SIZE or
                    outcome.get("fixedDenominator") != GROUP_SIZE or outcome.get("observedMembers") != GROUP_SIZE or
                    outcome.get("countChange") != now-before or outcome.get("expanded") is not (now >= 12 and now-before >= 4) or
                    (kind == "expanded") != outcome["expanded"]):
                    raise ValueError("Observed expansion predicate/counts differ")
            elif outcome.get("expanded") is not None:
                raise ValueError("Unevaluable expansion cannot be a known false")
        accepted = kind == "expanded"
        decision = {"symbol": symbol, "rank": expected_rank, "kind": kind, "reason": reason,
            "formationDate": formation, "baselineAdmitted": True, "candidateAdmitted": accepted}
        decisions.append(decision)
        base = deepcopy(parent)
        base.update(strategyId=CONTROL_ID, parentStrategyId=PARENT_ID, peerExpansionKind=kind,
                    peerExpansionReason=reason, formationDate=formation, certified=False, targetAchieved=False)
        baseline.append(base)
        if accepted:
            candidate = deepcopy(base)
            candidate["strategyId"] = CANDIDATE_ID
            candidates.append(candidate)
    return {"version": VERSION, "date": day, "candidateId": CANDIDATE_ID, "controlId": CONTROL_ID,
        "signals": candidates+baseline, "candidateSignals": candidates, "baselineSignals": baseline,
        "decisions": decisions, "classificationCounts": dict(Counter(r["kind"] for r in decisions)),
        "parentTop5Count": len(baseline), "candidateCount": len(candidates), "baselineCount": len(baseline),
        "activeExclusionApplied": False, "certified": False, "targetAchieved": False}
