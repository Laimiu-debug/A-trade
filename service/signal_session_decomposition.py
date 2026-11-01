"""Causal raw open/close decomposition; price descriptions, not control evidence.

Exactly 21 aligned raw sessions end at T. Only the last 20 sessions contribute
open/close returns. There is no I/O, adjustment, position state or future label.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
import hashlib
import math
from statistics import mean

from service.signal_contraction_price_proxy import (
    HARD_REASONS, UNKNOWN_REASONS, _calendar_valid, _day, _nonnegative, _positive,
)

VERSION = "raw-session-decomposition-v1"
CANDIDATE_ID = "rawRepeatedIntradayAdvance"
CONTROL_ID = "control_" + CANDIDATE_ID
HISTORY_SLOTS, TOP_N = 21, 5
CALIPERS = {"totalLogReturn20": .05, "closeMaxDrawdown20": .05,
            "signedEfficiency20": .15, "logPrior20MeanAmount": math.log(2)}


def evaluate_session_path(symbol, day, calendar21, raw21, eligibility, *, known_suspended=False):
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("Symbol is required")
    day = _day(day)
    if not isinstance(eligibility, dict) or type(known_suspended) is not bool:
        raise ValueError("Eligibility object and boolean halt state required")
    quality = {"priceBasisId": "raw-unadjusted", "certified": False, "targetAchieved": False,
        "actionEvidence": "unverified", "historicalEligibilityVerified": False,
        "comparableRightsVerified": False, "unknownHistoricalFields": deepcopy(eligibility.get("unknown", []))}

    def finish(kind, reason, features=None):
        features = features or {}
        signal = None
        if kind in ("intraday", "overnight"):
            signal = {"version": VERSION, "strategyId": CANDIDATE_ID if kind == "intraday" else CONTROL_ID,
                "symbol": symbol, "signalDate": day, "eligible": True, "decompositionClass": kind,
                "prior20MeanAmount": features["prior20MeanAmount"], "features": deepcopy(features),
                "quality": deepcopy(quality), "certified": False, "targetAchieved": False}
        return {"version": VERSION, "symbol": symbol, "date": day, "kind": kind, "reason": reason,
                "features": features, "quality": quality, "eligibility": deepcopy(eligibility),
                "knownSuspended": known_suspended, "signal": signal}

    reason, eligible = eligibility.get("reason"), eligibility.get("eligible")
    if type(eligible) is not bool:
        return finish("unevaluable", "baseEligibilityUnknown")
    missing_amount = reason == "amount20BelowMinimum" and not _nonnegative(eligibility.get("amount20"))
    missing_amplitude = reason == "amplitude20BelowMinimum" and not _nonnegative(eligibility.get("amplitude20"))
    if not eligible and reason in HARD_REASONS and not (missing_amount or missing_amplitude):
        return finish("ineligible", "baseEligibilityRejected", {"baseReason": reason})
    if missing_amount or missing_amplitude:
        return finish("unevaluable", "baseAmountUnknown" if missing_amount else "baseAmplitudeUnknown")
    if reason in UNKNOWN_REASONS and not (known_suspended and reason == "missingDailyBar"):
        return finish("unevaluable", reason)
    current = raw21[-1] if isinstance(raw21, (list, tuple)) and raw21 else None
    if known_suspended:
        if isinstance(current, dict) and _positive(current.get("volume")):
            return finish("unevaluable", "suspensionContradictsPositiveVolume")
        return finish("unevaluable", "knownSuspension")
    if reason == "suspendedOrZeroVolume":
        return finish("unevaluable", "suspensionStatusUnknown")
    if not eligible:
        return finish("unevaluable", "baseEligibilityReasonUnknown")
    if not isinstance(calendar21, (list, tuple)) or not isinstance(raw21, (list, tuple)):
        return finish("unevaluable", "invalidHistoryWindow")
    if len(calendar21) != HISTORY_SLOTS or len(raw21) != HISTORY_SLOTS:
        return finish("unevaluable", "insufficient21SessionHistory")
    try:
        valid = _calendar_valid(tuple(calendar21), day)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        return finish("unevaluable", "invalidExchangeCalendarAlignment")
    for expected, bar in zip(calendar21, raw21):
        if bar is None:
            return finish("unevaluable", "missingAlignedRawBar")
        if not isinstance(bar, dict) or bar.get("date") != expected:
            return finish("unevaluable", "rawHistoryDateMismatch")
        if bar.get("priceBasisId") != "raw-unadjusted":
            return finish("unevaluable", "rawPriceBasisUnknown")
        if "knownSuspended" in bar and type(bar["knownSuspended"]) is not bool:
            return finish("unevaluable", "invalidHistoricalHaltFlag")
        if bar.get("knownSuspended") is True:
            return finish("unevaluable", "historicalHaltContradictsPositiveVolume" if _positive(bar.get("volume")) else "historicalHaltInWindow")
        if not all(_positive(bar.get(k)) for k in ("open", "high", "low", "close", "volume")):
            return finish("unevaluable", "nonpositiveOrUnknownRawOHLCV")
        if not bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]:
            return finish("unevaluable", "invalidRawOHLCEnvelope")
    if any(not _positive(b.get("amount")) for b in raw21[:-1]):
        return finish("unevaluable", "prior20AmountUnknown")

    try:
        close_logs = [math.log(b["close"] / a["close"]) for a, b in zip(raw21, raw21[1:])]
        intraday = [math.log(b["close"] / b["open"]) for b in raw21[1:]]
        overnight = [math.log(b["open"] / a["close"]) for a, b in zip(raw21, raw21[1:])]
        total = math.log(raw21[-1]["close"] / raw21[0]["close"])
    except (OverflowError, ValueError):
        return finish("unevaluable", "nonfiniteDerivedFeature")
    if not all(math.isfinite(x) for x in (*close_logs, *intraday, *overnight, total)):
        return finish("unevaluable", "nonfiniteDerivedFeature")
    isum, osum, variation = math.fsum(intraday), math.fsum(overnight), math.fsum(abs(x) for x in close_logs)
    peak, drawdown = raw21[0]["close"], 0.
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        for b in raw21:
            peak = max(peak, b["close"])
            drawdown = max(drawdown, float((Decimal(str(peak)) - Decimal(str(b["close"]))) / Decimal(str(peak))))
        return_pass = Decimal(str(raw21[-1]["close"])) >= Decimal("1.10") * Decimal(str(raw21[0]["close"]))
    efficiency = total / variation if variation else 0.
    ipos = sum(b["close"] > b["open"] for b in raw21[1:])
    opos = sum(b["open"] > a["close"] for a, b in zip(raw21, raw21[1:]))
    amount = mean(b["amount"] for b in raw21[:-1])
    gains = sorted((x for x in overnight if x > 0), reverse=True)
    positive_gaps = math.fsum(gains)
    features = {"historyStart": calendar21[0], "historyEnd": day, "returnStart": calendar21[1],
        "returnSessions": 20, "priorAmountEnd": calendar21[-2], "prior20MeanAmount": amount,
        "startClose": raw21[0]["close"], "endClose": raw21[-1]["close"],
        "totalLogReturn20": total, "simpleReturn20": raw21[-1]["close"] / raw21[0]["close"] - 1,
        "intradayLogReturn20": isum, "overnightLogReturn20": osum,
        "decompositionResidual": math.fsum((isum, osum, -total)),
        "closeMaxDrawdown20": drawdown, "signedEfficiency20": efficiency,
        "positiveIntradaySessions": ipos, "positiveOvernightSessions": opos,
        "intradaySignedShare": isum / total if total else None,
        "overnightSignedShare": osum / total if total else None,
        "largestPositiveGapShare": gains[0] / positive_gaps if gains else None,
        "topThreePositiveGapShare": math.fsum(gains[:3]) / positive_gaps if gains else None}
    if not all(math.isfinite(v) for v in features.values() if type(v) in (float, int)):
        return finish("unevaluable", "nonfiniteDerivedFeature")
    conditions = {"returnAtLeast10Pct": return_pass,
        "drawdownAtMost15Pct": drawdown <= .15, "efficiencyAtLeast035": efficiency >= .35}
    features["conditions"] = conditions
    if not all(conditions.values()):
        return finish("notTriggered", "commonUpwardPathNotMet", features)
    iok, ook = isum >= (2 / 3) * total and ipos >= 12, osum >= (2 / 3) * total and opos >= 12
    if iok and ook:
        raise ValueError("Positive-return decomposition classes cannot overlap")
    return finish("intraday" if iok else "overnight" if ook else "notTriggered",
                  "repeatedIntradayAdvance" if iok else "repeatedOvernightAdvance" if ook else "noDominantRepeatedComponent", features)


def _matching_features(row):
    f = row["features"]
    return {"totalLogReturn20": f["totalLogReturn20"], "closeMaxDrawdown20": f["closeMaxDrawdown20"],
            "signedEfficiency20": f["signedEfficiency20"], "logPrior20MeanAmount": math.log(row["prior20MeanAmount"])}


def select_session_pairs(day, classified_signals):
    """Fixed Top5, then greedy causal caliper matching; never drop an anchor."""
    day = _day(day)
    rows, symbols = deepcopy(list(classified_signals)), set()
    for r in rows:
        if not isinstance(r, dict):
            raise ValueError("Classified signal must be an object")
        kind = r.get("decompositionClass")
        if (r.get("signalDate") != day or r.get("eligible") is not True or kind not in ("intraday", "overnight")
                or r.get("strategyId") != (CANDIDATE_ID if kind == "intraday" else CONTROL_ID)
                or not isinstance(r.get("symbol"), str) or not r["symbol"] or r["symbol"] in symbols
                or not _positive(r.get("prior20MeanAmount"))):
            raise ValueError("One day of unique classified eligible symbols required")
        symbols.add(r["symbol"])
        f = r.get("features", {})
        if not isinstance(f, dict) or any(type(f.get(k)) not in (int, float) or not math.isfinite(f[k]) for k in
                ("totalLogReturn20", "closeMaxDrawdown20", "signedEfficiency20")):
            raise ValueError("Finite matching features required")
        # The tiny upper-domain allowance only accommodates log telescoping;
        # it never relaxes the registered >= .35 signal or <= .15 pair gate.
        if not (f["totalLogReturn20"] > 0 and 0 <= f["closeMaxDrawdown20"] <= .15 and .35 <= f["signedEfficiency20"] <= 1 + 1e-12):
            raise ValueError("Matching features outside declared path domain")
        if f.get("prior20MeanAmount") != r["prior20MeanAmount"] or not all(_positive(f.get(k)) for k in ("startClose", "endClose")):
            raise ValueError("Ranking or source-close features inconsistent")
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            if Decimal(str(f["endClose"])) < Decimal("1.10") * Decimal(str(f["startClose"])):
                raise ValueError("Matching path below registered return gate")
        component = "intradayLogReturn20" if kind == "intraday" else "overnightLogReturn20"
        count = "positiveIntradaySessions" if kind == "intraday" else "positiveOvernightSessions"
        if (type(f.get(component)) not in (int, float) or not math.isfinite(f[component])
                or f[component] < (2 / 3) * f["totalLogReturn20"]
                or type(f.get(count)) is not int or not 12 <= f[count] <= 20):
            raise ValueError("Declared decomposition class fails its predicates")
    by_class = {k: sorted([r for r in rows if r["decompositionClass"] == k],
                          key=lambda r: (-r["prior20MeanAmount"], r["symbol"])) for k in ("intraday", "overnight")}
    for kind, ranked in by_class.items():
        for rank, r in enumerate(ranked, 1):
            r.update(rank=rank, selectedTop5=(kind == "intraday" and rank <= TOP_N),
                     score=r["prior20MeanAmount"], rawScore=r["prior20MeanAmount"])
    signals, pairs, used = [], [], set()
    for anchor in by_class["intraday"][:TOP_N]:
        af, feasible = _matching_features(anchor), []
        for control in by_class["overnight"]:
            if control["symbol"] in used:
                continue
            cf = _matching_features(control)
            deltas = {k: abs(af[k] - cf[k]) for k in CALIPERS}
            # Ratio comparison preserves the inclusive 2x boundary directly.
            amount_ratio = max(anchor["prior20MeanAmount"], control["prior20MeanAmount"]) / min(anchor["prior20MeanAmount"], control["prior20MeanAmount"])
            deltas["logPrior20MeanAmount"] = math.log(amount_ratio)
            if amount_ratio <= 2 and all(deltas[k] <= CALIPERS[k] for k in CALIPERS if k != "logPrior20MeanAmount"):
                distance = math.fsum(deltas[k] / CALIPERS[k] for k in CALIPERS)
                feasible.append((distance, control["symbol"], control, deltas, amount_ratio))
        feasible.sort(key=lambda r: (r[0], r[1]))
        pair_id = hashlib.sha256(f"{VERSION}:{day}:{anchor['symbol']}".encode()).hexdigest()[:24]
        chosen = feasible[0] if feasible else None
        pair = {"pairId": pair_id, "date": day, "anchorSymbol": anchor["symbol"], "anchorRank": anchor["rank"],
            "controlSymbol": chosen[1] if chosen else None, "matched": bool(chosen),
            "distance": chosen[0] if chosen else None, "absoluteFeatureDeltas": chosen[3] if chosen else None,
            "amountRatio": chosen[4] if chosen else None, "feasibleUnusedControls": len(feasible),
            "reason": "matched" if chosen else "noUnusedControlWithinCalipers"}
        own = {**deepcopy(anchor), "pairId": pair_id, "pairedSymbol": pair["controlSymbol"], "matchedAtSignal": bool(chosen)}
        signals.append(own)
        if chosen:
            other = deepcopy(chosen[2])
            used.add(other["symbol"])
            other.update(rank=anchor["rank"], sourceClassRank=other["rank"], pairId=pair_id,
                pairedSymbol=anchor["symbol"], pairedStrategyId=CANDIDATE_ID, matchedAtSignal=True, selectedTop5=False)
            signals.append(other)
        pairs.append(pair)
    return {"date": day, "rankedIntraday": by_class["intraday"], "rankedOvernight": by_class["overnight"],
            "signals": signals, "pairs": pairs, "requestedCount": len(pairs), "matchedCount": len(used),
            "shortfall": len(pairs) - len(used), "activeExclusionApplied": False}
