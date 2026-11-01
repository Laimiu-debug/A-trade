"""Pure board-cohort leader/lag experiment; no I/O, outcomes or account state.

Implements docs/signal-leader-lag-followers-v1.json. The older leadership
mathematics is retained, but no archived panel or legacy base eligibility is
fabricated. Group evidence is streamed independently of progress messages.
"""
from __future__ import annotations

from array import array
from collections import Counter, deque
from copy import deepcopy
from datetime import date
import hashlib
import json
import math
from statistics import mean


VERSION = "leader-lag-followers-v1"
STRATEGY_ID = "leaderLagFollowersV1"
MODES = ("control", "direction")
TOP_N, GROUP_SIZE, FORMATION_LAG = 5, 20, 10
THRESHOLDS = (
    ("meanStrengthPercentile20", ">=", .90),
    ("minimumStrengthPercentile20", ">=", .80),
    ("relativeReturn60Percentile", ">=", .90),
    ("closePathMaxDrawdown20", "<=", .10),
    ("signedPathEfficiency20", ">=", .35),
)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _positive(value):
    return _finite(value) and value > 0


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Expected canonical YYYY-MM-DD date")
    return value


def _bar_reason(bar, day, historical_status):
    if bar is None:
        return "missingAlignedRawBar"
    if not isinstance(bar, dict) or bar.get("date") != day:
        return "rawHistoryDateMismatch"
    if bar.get("priceBasisId", "raw-unadjusted") != "raw-unadjusted":
        return "rawPriceBasisUnknown"
    if "knownSuspended" in bar and type(bar["knownSuspended"]) is not bool:
        return "invalidKnownSuspensionFlag"
    halted = (historical_status is False or type(historical_status) in (int, float) and historical_status == 0
              or historical_status == "0")
    if bar.get("knownSuspended") is True or halted:
        return "knownSuspension"
    if not all(_positive(bar.get(k)) for k in ("open", "high", "low", "close", "volume")):
        return "nonpositiveOrUnknownRawOHLCV"
    if not bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]:
        return "invalidRawOHLCEnvelope"
    return None


class _Series:
    def __init__(self, security, calendar, end):
        values = security.get("bars")
        if not isinstance(values, list):
            raise ValueError("Security bars must be a list")
        by_date, previous = {}, None
        allowed = set(calendar)
        for bar in values:
            if not isinstance(bar, dict):
                raise ValueError("Raw bars must be objects with dates")
            day = _day(bar.get("date"))
            if previous is not None and day <= previous:
                raise ValueError("Raw bar dates must be unique and increasing")
            previous = day
            if day > end:
                continue  # No future numerical field is inspected.
            if day not in allowed:
                raise ValueError("Raw bar outside supplied source calendar")
            by_date[day] = bar
        status = security.get("tradeStatusByDate", {})
        if not isinstance(status, dict):
            raise ValueError("Historical trade status must be a date mapping")
        self.bars = [by_date.get(day) for day in calendar]
        self.reasons = [_bar_reason(bar, day, status.get(day)) for day, bar in zip(calendar, self.bars)]
        self.bad = array("I", [0])
        for reason in self.reasons:
            self.bad.append(self.bad[-1] + (reason is not None))

    def window_reason(self, at, slots):
        first = at - slots + 1
        if first < 0:
            return "insufficientCalendarHistory"
        if self.bad[at + 1] == self.bad[first]:
            return None
        return next(reason for reason in self.reasons[first:at + 1] if reason is not None)


def _relative_return(stock, benchmark, at, horizon):
    reason = stock.window_reason(at, horizon + 1)
    if reason is not None:
        return None, "stock:" + reason
    reason = benchmark.window_reason(at, horizon + 1)
    if reason is not None:
        return None, "benchmark:" + reason
    own = stock.bars[at]["close"] / stock.bars[at - horizon]["close"]
    base = benchmark.bars[at]["close"] / benchmark.bars[at - horizon]["close"]
    if not _positive(own) or not _positive(base):
        return None, "nonfiniteOrUnderflowIntermediateRatio"
    ratio = own / base
    if not _positive(ratio) or not _finite(ratio - 1):
        return None, "nonfiniteOrUnderflowRelativeRatio"
    return ratio - 1, None


def _rank(values):
    """All finite values enter independently of leadership or eligibility."""
    ordered = sorted(((value, symbol) for symbol, value in values.items() if value is not None))
    size, index, ranks = len(ordered), 0, {symbol: None for symbol in values}
    while index < size:
        stop = index + 1
        while stop < size and ordered[stop][0] == ordered[index][0]:
            stop += 1
        percentile = (index + stop) / (2 * size)
        for _, symbol in ordered[index:stop]:
            ranks[symbol] = percentile
        index = stop
    records = [{"symbol": symbol, "value": values[symbol], "percentile": ranks[symbol]}
               for symbol in sorted(values) if values[symbol] is not None]
    return ranks, {"count": size, "sha256": _digest(records)}


def _leadership(stock, benchmark, at, calendar, history, rank60):
    values = {name: None for name, _, _ in THRESHOLDS}
    reasons = []
    for label, series in (("stock", stock), ("benchmark", benchmark)):
        reason = series.window_reason(at, 61)
        if reason:
            reasons.append(label + ":" + reason)
    ranks = list(history)
    if len(ranks) != 20 or any(value is None for value in ranks):
        reasons.append("incompleteHistoricalRS20Ranks")
    else:
        values["meanStrengthPercentile20"] = mean(ranks)
        values["minimumStrengthPercentile20"] = min(ranks)
    values["relativeReturn60Percentile"] = rank60
    if rank60 is None:
        reasons.append("relativeReturn60RankUnknown")
    if stock.window_reason(at, 21) is None:
        closes = [bar["close"] for bar in stock.bars[at - 20:at + 1]]
        peak, drawdown = closes[0], 0.
        for close in closes:
            peak = max(peak, close)
            drawdown = max(drawdown, (peak - close) / peak)
        values["closePathMaxDrawdown20"] = drawdown if _finite(drawdown) else None
        try:
            distance = math.fsum(abs(b - a) for a, b in zip(closes, closes[1:]))
            efficiency = (closes[-1] - closes[0]) / distance if distance > 0 else None
        except (OverflowError, ValueError, ZeroDivisionError):
            distance, efficiency = None, None
        if distance == 0:
            reasons.append("zeroPathDistance")
        elif not _positive(distance) or not _finite(efficiency):
            reasons.append("nonfinitePathEfficiency")
        else:
            values["signedPathEfficiency20"] = efficiency
    else:
        reasons.append("path20WindowUnknown")
    if any(value is None or not _finite(value) for value in values.values()) and not reasons:
        reasons.append("nonfiniteLeadershipValue")
    predicates = {key: None if values[key] is None else
        (values[key] >= threshold if operator == ">=" else values[key] <= threshold)
        for key, operator, threshold in THRESHOLDS}
    known = not reasons
    passed = all(predicates.values()) if known else None
    prior_high = None
    if stock.window_reason(at, 6) is None:
        prior_high = max(bar["high"] for bar in stock.bars[at - 5:at])
    close = stock.bars[at].get("close") if stock.bars[at] is not None else None
    breakout = close > prior_high if prior_high is not None else None
    return {"date": calendar[at], "status": "unknown" if not known else "passed" if passed else "failed",
        "reasons": list(dict.fromkeys(reasons)), "values": values, "predicates": predicates,
        "rankDates20": calendar[max(0, at - 19):at + 1], "rankHistory20": ranks,
        "knownRankCount20": sum(value is not None for value in ranks),
        "prior5High": prior_high, "close": close if _finite(close) else None, "breakout": breakout}


def _prepared(values):
    result = {"known": False, "reason": None, "count": len(values), "mean": None,
              "variance": None, "centered": None}
    if len(values) < 2:
        result["reason"] = "insufficientPairs"
    elif not all(_finite(value) for value in values):
        result["reason"] = "nonfiniteInput"
    elif all(value == values[0] for value in values):
        result["reason"] = "exactConstantInput"
    else:
        try:
            average = math.fsum(values) / len(values)
            centered = [value - average for value in values]
            variance = math.fsum(value * value for value in centered)
        except (OverflowError, ValueError):
            result["reason"] = "nonfiniteCentering"
            return result
        result.update(mean=average if _finite(average) else None,
                      variance=variance if _finite(variance) else None)
        if not _finite(average) or not _finite(variance):
            result["reason"] = "nonfiniteCentering"
        elif variance <= 0:
            result["reason"] = "zeroVariance"
        else:
            result.update(known=True, centered=centered)
    return result


def _correlate(x, y):
    result = {"known": False, "reason": None, "count": x["count"], "meanX": x["mean"],
        "meanY": y["mean"], "varianceX": x["variance"], "varianceY": y["variance"],
        "numerator": None, "denominator": None, "rawCorrelation": None, "correlation": None}
    if x["count"] != y["count"]:
        result["reason"] = "pairCountMismatch"
        return result
    if not x["known"] or not y["known"]:
        result["reason"] = ("x:" + x["reason"]) if not x["known"] else ("y:" + y["reason"])
        return result
    try:
        numerator = math.fsum(a * b for a, b in zip(x["centered"], y["centered"]))
        denominator = math.sqrt(x["variance"]) * math.sqrt(y["variance"])
    except (OverflowError, ValueError):
        result["reason"] = "nonfiniteCovarianceOrDenominator"
        return result
    result.update(numerator=numerator if _finite(numerator) else None,
                  denominator=denominator if _finite(denominator) else None)
    if not _finite(numerator) or not _positive(denominator):
        result["reason"] = "nonfiniteCovarianceOrDenominator"
        return result
    rho = numerator / denominator
    if not _finite(rho):
        result["reason"] = "nonfiniteCorrelation"
        return result
    result.update(known=True, rawCorrelation=rho, correlation=max(-1., min(1., rho)))
    return result


def _pearson(x, y):
    return _correlate(_prepared(x), _prepared(y))


def _vectors(series, benchmark, at):
    benchmark_reason = benchmark.window_reason(at, 61)
    benchmark_logs = None
    if benchmark_reason is None:
        bars = benchmark.bars[at - 60:at + 1]
        try:
            benchmark_logs = [math.log(b["close"] / a["close"]) for a, b in zip(bars, bars[1:])]
        except (ValueError, OverflowError, ZeroDivisionError):
            benchmark_reason = "nonfiniteLogReturn"
        if benchmark_logs is not None and not all(_finite(v) for v in benchmark_logs):
            benchmark_reason = "nonfiniteLogReturn"
    output = {}
    for symbol, stock in series.items():
        reason, values = stock.window_reason(at, 61), None
        if reason is not None:
            reason = "stock:" + reason
        elif benchmark_reason is not None:
            reason = "benchmark:" + benchmark_reason
        else:
            bars = stock.bars[at - 60:at + 1]
            try:
                values = [math.log(b["close"] / a["close"]) - base
                          for a, b, base in zip(bars, bars[1:], benchmark_logs)]
            except (ValueError, OverflowError, ZeroDivisionError):
                reason = "nonfiniteExcessLogReturn"
            if values is not None and not all(_finite(value) for value in values):
                reason, values = "nonfiniteExcessLogReturn", None
        output[symbol] = {"values": values if reason is None else None, "reason": reason,
                          "prepared": _prepared(values) if reason is None else None}
    return output


def _form_group(anchor, formation_day, window_dates, vectors):
    anchor_vector = vectors[anchor]
    global_reason = anchor_vector["reason"]
    if global_reason is None and not anchor_vector["prepared"]["known"]:
        global_reason = anchor_vector["prepared"]["reason"]
    evaluations = []
    for symbol in sorted(vectors):
        if symbol == anchor:
            continue
        vector = vectors[symbol]
        reason = vector["reason"] or ("anchor:" + global_reason if global_reason else None)
        correlation = None
        if reason is None:
            correlation = _correlate(anchor_vector["prepared"], vector["prepared"])
            reason = correlation["reason"]
        known = correlation is not None and correlation["known"]
        qualifies = known and correlation["correlation"] >= .30
        evaluations.append({"symbol": symbol,
            "status": "unknown" if not known else "qualified" if qualifies else "belowThreshold",
            "reason": reason if not known else "correlationAtLeast030" if qualifies else "correlationBelow030",
            "rawCorrelation": correlation["rawCorrelation"] if known else None,
            "correlation": correlation["correlation"] if known else None,
            "pearson": correlation, "rank": None, "selected": False})
    qualified = sorted((row for row in evaluations if row["status"] == "qualified"),
                       key=lambda row: (-row["correlation"], row["symbol"]))
    for rank, row in enumerate(qualified, 1):
        row.update(rank=rank, selected=rank <= GROUP_SIZE)
    members = [{"symbol": row["symbol"], "frozenMemberRank": row["rank"],
                "correlation60": row["correlation"], "rawCorrelation60": row["rawCorrelation"]}
               for row in qualified[:GROUP_SIZE]]
    kind = "unknown" if global_reason else "formed" if len(members) == GROUP_SIZE else "incomplete"
    return {"version": VERSION, "anchorSymbol": anchor, "peerFormationDate": formation_day,
        "kind": kind, "reason": "anchor:" + global_reason if global_reason else
            "fixedSynchronousPeerGroupFormed" if kind == "formed" else "insufficientCorrelatedPeers",
        "windowDates61": list(window_dates), "returnDates60": list(window_dates[1:]),
        "requiredCount": GROUP_SIZE, "selectedCount": len(members), "members": members,
        "counts": {"inputSymbols": len(vectors), "candidateCount": len(evaluations),
            "qualifiedCount": len(qualified), "statusCounts": dict(sorted(Counter(r["status"] for r in evaluations).items())),
            "reasonCounts": dict(sorted(Counter(r["reason"] for r in evaluations).items()))},
        "candidateEvaluationsSha256": _digest(evaluations), "candidateEvaluations": evaluations}


def _relationship(leader_values, member_values, window_dates):
    dates = list(window_dates[1:])
    definitions = {
        "synchronous59": (leader_values[1:], member_values[1:], dates[1:], dates[1:]),
        "forward59": (leader_values[:-1], member_values[1:], dates[:-1], dates[1:]),
        "reverse59": (member_values[:-1], leader_values[1:], dates[:-1], dates[1:]),
    }
    coefficients = {}
    for name, (x, y, x_dates, y_dates) in definitions.items():
        coefficients[name] = {**_pearson(x, y), "xDates": x_dates, "yDates": y_dates}
    reasons = [name + ":" + item["reason"] for name, item in coefficients.items() if not item["known"]]
    known = not reasons
    passed = (coefficients["forward59"]["correlation"] > max(0.,
        coefficients["synchronous59"]["correlation"], coefficients["reverse59"]["correlation"])) if known else None
    return {"status": "known" if known else "unknown", "reasons": reasons,
        "windowDates61": list(window_dates), "returnDates60": dates,
        "coefficients": coefficients, "directionPassed": passed}


def _edge_key(row):
    return (row["frozenMemberRank"], row["originalLeaderRank"], row["anchorSymbol"], row["symbol"])


def _select_edges(raw_edges):
    """Owner and member rank are fixed before any relation is evaluated."""
    ordered, owner_by_symbol, owners = sorted(deepcopy(raw_edges), key=_edge_key), {}, []
    for edge in ordered:
        owner = owner_by_symbol.get(edge["symbol"])
        if owner is None:
            owner = edge
            owner_by_symbol[edge["symbol"]] = edge
            owners.append(edge)
        edge.update(ownerFormationId=owner["formationId"],
            ownershipStatus="owner" if owner is edge else "duplicateSuppressed",
            originalRank=None, selectedTop5=False, relationshipStatus="notEvaluated")
    for rank, owner in enumerate(owners, 1):
        owner.update(originalRank=rank, selectedTop5=rank <= TOP_N,
                     disposition="selectedTop5" if rank <= TOP_N else "dailyRankExcluded")
    for edge in ordered:
        owner = owner_by_symbol[edge["symbol"]]
        edge["ownerOriginalRank"] = owner["originalRank"]
        if edge["ownershipStatus"] != "owner":
            edge["disposition"] = "duplicateSuppressed"
    return ordered, owners[:TOP_N]


def _counts():
    return {"currentCandidateCount": 0, "temporalCandidateCount": 0, "leaderEventCount": 0,
        "leaderGroupKinds": {key: 0 for key in ("formed", "incomplete", "unknown")},
        "rawEdges": 0, "uniqueOwners": 0, "duplicateSuppressed": 0, "selectedTop5": 0,
        "dailyRankExcluded": 0, "knownRelations": 0, "unknownRelations": 0,
        "directionPass": 0, "directionFail": 0,
        "signals": {mode: 0 for mode in MODES}, "notScheduled": {mode: 0 for mode in MODES},
        "policyCounts": {mode: {key: 0 for key in ("included", "excluded", "unknown")} for mode in MODES}}


def _add_counts(total, daily):
    for key, value in daily.items():
        if isinstance(value, dict):
            _add_counts(total[key], value)
        else:
            total[key] += value


def build_leader_lag_followers(dataset, start, end, progress=None, on_group=None):
    """Build both schedules without reading outcomes or changing any input.

    ``on_group`` receives a detached full group record. Returned leader events
    retain a compact summary and its full group digest. No evidence is written
    here; the runner owns exclusive persistence and source/hash bindings.
    """
    start, end = _day(start), _day(end)
    if start > end or not isinstance(dataset, dict) or dataset.get("priceBasisId") != "raw-unadjusted":
        raise ValueError("Use an ordered interval and explicit raw-unadjusted dataset")
    supplied = dataset.get("calendar")
    if not isinstance(supplied, list) or not supplied or supplied != sorted(set(supplied)):
        raise ValueError("Source calendar must be nonempty, unique and increasing")
    for day in supplied:
        _day(day)
    calendar = [day for day in supplied if day <= end]
    targets = [day for day in calendar if start <= day <= end]
    if not targets:
        raise ValueError("Requested interval has no source trading sessions")
    securities = dataset.get("securities")
    if not isinstance(securities, list) or not securities:
        raise ValueError("A nonempty fixed security cohort is required")
    by_symbol = {}
    for security in securities:
        symbol = security.get("symbol") if isinstance(security, dict) else None
        if not isinstance(symbol, str) or not symbol.strip() or symbol in by_symbol:
            raise ValueError("Fixed cohort symbols must be unique and nonempty")
        by_symbol[symbol] = security
    benchmark_source = dataset.get("benchmark")
    if not isinstance(benchmark_source, dict) or benchmark_source.get("symbol") != "sh000300":
        raise ValueError("The registered benchmark must be sh000300")
    symbols = sorted(by_symbol)
    series = {symbol: _Series(by_symbol[symbol], calendar, end) for symbol in symbols}
    benchmark = _Series(benchmark_source, calendar, end)
    index = {day: at for at, day in enumerate(calendar)}
    first_target = index[targets[0]]
    first_rank = max(0, first_target - 29)
    histories = {symbol: deque(maxlen=20) for symbol in symbols}
    state_ring = deque(maxlen=11)
    rank_rows, daily_rows, events, all_edges, opportunities = [], [], [], [], []
    schedules = {mode: [] for mode in MODES}
    counts = _counts()
    for at in range(first_rank, len(calendar)):
        day = calendar[at]
        ranks, populations, reason_counts = {}, {}, {}
        for horizon in (20, 60):
            values, reasons = {}, Counter()
            for symbol in symbols:
                value, reason = _relative_return(series[symbol], benchmark, at, horizon)
                values[symbol] = value
                if reason:
                    reasons[reason] += 1
            ranks[horizon], populations[str(horizon)] = _rank(values)
            reason_counts[str(horizon)] = dict(sorted(reasons.items()))
        rank_rows.append({"date": day, "populations": populations, "reasonCounts": reason_counts})
        current = {}
        for symbol in symbols:
            histories[symbol].append(ranks[20][symbol])
            current[symbol] = _leadership(series[symbol], benchmark, at, calendar,
                                          histories[symbol], ranks[60][symbol])
        state_ring.append((day, current))
        if at < first_target:
            continue
        formation_at = at - FORMATION_LAG
        formation_day = calendar[formation_at] if formation_at >= 0 else None
        past = state_ring[0][1] if len(state_ring) == 11 and state_ring[0][0] == formation_day else None
        current_candidates, temporal_candidates = [], []
        activation_counts, formation_counts = Counter(), Counter()
        for symbol in symbols:
            state = current[symbol]
            activation = "unknown" if state["status"] == "unknown" else (
                "candidate" if state["status"] == "passed" and state["breakout"] else "notTriggered")
            activation_counts[activation] += 1
            if activation != "candidate":
                continue
            prior = past[symbol] if past is not None else None
            formation_status = prior["status"] if prior is not None else "unknown"
            formation_counts[formation_status] += 1
            candidate = {"symbol": symbol, "formationDate": day, "peerFormationDate": formation_day,
                "currentEvidence": deepcopy(state), "formationEvidence": deepcopy(prior),
                "formationStatus": formation_status, "formationReason":
                    "formationStateUnavailable" if prior is None else
                    "formationFiveConditionsPassed" if formation_status == "passed" else
                    "formationFiveConditionsFailed" if formation_status == "failed" else "formationEvidenceUnknown",
                "originalLeaderRank": None, "selectedTop5": False}
            current_candidates.append(candidate)
            if formation_status == "passed":
                temporal_candidates.append(candidate)
        temporal_candidates.sort(key=lambda row: (-row["currentEvidence"]["values"]["meanStrengthPercentile20"],
            -row["currentEvidence"]["values"]["relativeReturn60Percentile"], row["symbol"]))
        for rank, candidate in enumerate(temporal_candidates, 1):
            candidate.update(originalLeaderRank=rank, selectedTop5=rank <= TOP_N)
        selected_leaders = temporal_candidates[:TOP_N]
        daily_counts = _counts()
        daily_counts.update(currentCandidateCount=len(current_candidates), temporalCandidateCount=len(temporal_candidates),
                            leaderEventCount=len(selected_leaders))
        vectors = _vectors(series, benchmark, formation_at) if selected_leaders else None
        raw_edges, day_event_ids = [], []
        for leader in selected_leaders:
            anchor = leader["symbol"]
            event_id = f"{STRATEGY_ID}:{day}:{anchor}"
            window_dates = calendar[formation_at - 60:formation_at + 1]
            group = _form_group(anchor, formation_day, window_dates, vectors)
            group_sha = _digest(group)
            identity = {"version": VERSION, "eventId": event_id, "formationDate": day,
                "peerFormationDate": formation_day, "anchorSymbol": anchor,
                "originalLeaderRank": leader["originalLeaderRank"], "groupSha256": group_sha}
            if on_group is not None:
                on_group(deepcopy({**identity, "group": group}))
            summary = {key: deepcopy(value) for key, value in group.items() if key != "candidateEvaluations"}
            events.append({**identity, "currentEvidence": deepcopy(leader["currentEvidence"]),
                "formationEvidence": deepcopy(leader["formationEvidence"]), "groupSummary": summary})
            day_event_ids.append(event_id)
            daily_counts["leaderGroupKinds"][group["kind"]] += 1
            if group["kind"] != "formed":
                continue
            for member in group["members"]:
                symbol = member["symbol"]
                raw_edges.append({"formationId": event_id + ":" + symbol, "eventId": event_id,
                    "formationDate": day, "peerFormationDate": formation_day,
                    "symbol": symbol, "anchorSymbol": anchor,
                    "originalLeaderRank": leader["originalLeaderRank"],
                    "frozenMemberRank": member["frozenMemberRank"], "groupSha256": group_sha,
                    "synchronousCorrelation60": member["correlation60"]})
        edges, selected_members = _select_edges(raw_edges)
        daily_counts.update(rawEdges=len(edges),
            uniqueOwners=sum(edge["ownershipStatus"] == "owner" for edge in edges),
            duplicateSuppressed=sum(edge["ownershipStatus"] == "duplicateSuppressed" for edge in edges),
            selectedTop5=len(selected_members),
            dailyRankExcluded=sum(edge["disposition"] == "dailyRankExcluded" for edge in edges))
        for edge in selected_members:
            relation = _relationship(vectors[edge["anchorSymbol"]]["values"], vectors[edge["symbol"]]["values"],
                                     calendar[formation_at - 60:formation_at + 1])
            known = relation["status"] == "known"
            edge["relationshipStatus"] = relation["status"]
            daily_counts["knownRelations" if known else "unknownRelations"] += 1
            if known:
                daily_counts["directionPass" if relation["directionPassed"] else "directionFail"] += 1
            decisions, signals = {}, {}
            for mode in MODES:
                included = known and (mode == "control" or relation["directionPassed"])
                status = "unknown" if not known else "included" if included else "excluded"
                reason = "jointRelationshipUnknown" if not known else "completeSynchronousPeer" if mode == "control" else (
                    "directionAdvantagePassed" if included else "directionAdvantageNotMet")
                decisions[mode] = {"status": status, "reason": reason}
                signal = {"signalId": edge["formationId"], "strategyId": STRATEGY_ID, "symbol": edge["symbol"],
                    "signalDate": day, "originalRank": edge["originalRank"]} if included else None
                signals[mode] = signal
                daily_counts["policyCounts"][mode][status] += 1
                daily_counts["signals" if included else "notScheduled"][mode] += 1
                if included:
                    schedules[mode].append(deepcopy(signal))
            opportunities.append({**deepcopy(edge), "relationship": relation, "decisions": decisions, "signals": signals})
        all_edges.extend(edges)
        daily_rows.append({"formationDate": day, "peerFormationDate": formation_day,
            "rankPopulations": deepcopy(populations), "rankReasonCounts": deepcopy(reason_counts),
            "activationCounts": {key: activation_counts[key] for key in ("candidate", "notTriggered", "unknown")},
            "formationCounts": {key: formation_counts[key] for key in ("passed", "failed", "unknown")},
            "leaderCandidates": current_candidates,
            "leaderRanking": [{"symbol": row["symbol"], "originalLeaderRank": row["originalLeaderRank"],
                               "selectedTop5": row["selectedTop5"]} for row in temporal_candidates],
            "leaderEventIds": day_event_ids, "memberEdgeIds": [edge["formationId"] for edge in edges],
            "selectedFormationIds": [edge["formationId"] for edge in selected_members], "counts": daily_counts})
        _add_counts(counts, daily_counts)
        if progress is not None and (len(daily_rows) % 10 == 0 or at == len(calendar) - 1):
            progress({"phase": "leader-lag-prefix", "date": day, "processedTargetDays": len(daily_rows),
                "targetDayCount": len(targets), "leaderEventCount": counts["leaderEventCount"],
                "groupCount": sum(counts["leaderGroupKinds"].values()), "opportunityCount": len(opportunities)})
    for mode in MODES:
        schedules[mode].sort(key=lambda row: (row["signalDate"], row["originalRank"], row["symbol"], row["signalId"]))
    counts.update(sampleCount=len(symbols), targetDayCount=len(targets), rankDayCount=len(rank_rows),
                  opportunityCount=len(opportunities))
    return {"version": VERSION, "strategyId": STRATEGY_ID, "modes": list(MODES),
        "calendar": calendar, "targetDates": targets, "rankRows": rank_rows, "dailyRows": daily_rows,
        "leaderEvents": events, "memberEdges": all_edges, "opportunities": opportunities,
        "schedules": schedules, "counts": counts, "basis": {
            "protocol": "docs/signal-leader-lag-followers-v1.json", "priceBasisId": "raw-unadjusted",
            "benchmarkSymbol": "sh000300", "rankScope": "entire fixed board cohort, separate valid21/61-slot populations",
            "rankDigest": "canonical sorted-symbol list of known {symbol,value,percentile}; no eligibility prefilter",
            "rankClockStart": calendar[first_rank], "formationLag": FORMATION_LAG, "groupSize": GROUP_SIZE,
            "peerCorrelationMinimum": .30, "leaderThresholds": [{"field": f, "operator": op, "threshold": v} for f, op, v in THRESHOLDS],
            "memberOrder": ["frozenMemberRank", "originalLeaderRank", "anchorSymbol", "symbol"],
            "groupEvidence": "full detached on_group records; returned summaries retain full group and evaluation hashes",
            "unknownHistoricalStatus": "unverified; never inferred from current names or missing records",
            "legacyEligibilityApplied": False, "accountStateRead": False, "noBackfill": True,
            "interpretation": "board-scoped lagged association, not causal influence or market-wide leadership"},
        "certified": False, "targetAchieved": False}
