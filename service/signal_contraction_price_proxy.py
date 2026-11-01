"""Causal, stateless raw-price contraction diagnostic; not an action certificate.

The caller supplies exactly aligned exchange-session dates and raw series.bars
views, ending at T. No market I/O, reference adjustment, position input, state,
cooldown or implicit amount reconstruction is performed here.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from functools import lru_cache
import math
from statistics import mean


VERSION = "raw-contraction-price-diagnostic-v1"
CANDIDATE_ID = "rawThreeStageContractionBreakoutDiagnostic"
HISTORY_SLOTS, TOP_N = 81, 5
UNKNOWN_REASONS = {"missingDailyBar", "notObservedYet", "invalidHistory", "strictHistoryMissing"}
HARD_REASONS = {"delisted", "notListed", "notHistoricalMember", "historicalSt",
                "insufficientListingHistory", "amount20BelowMinimum", "amplitude20BelowMinimum"}
NUMERIC_COMPARISON_BASIS = {
    "range": "Decimal(str(rawPrice)); local context precision 28; ROUND_HALF_EVEN; no epsilon",
    "rangePredicate": "D1 > D2 > D3 and D3 <= Decimal('0.08')",
    "movingAveragesVolumeAndAmount": "statistics.mean; Python float comparisons; no epsilon",
}


def _positive(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _nonnegative(value):
    return _positive(value) or type(value) in (int, float) and value == 0


@lru_cache(maxsize=8192)
def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Dates must be YYYY-MM-DD")
    return value


@lru_cache(maxsize=2048)
def _calendar_valid(days, today):
    return bool(days) and days[-1] == today and all(_day(d) <= today for d in days) and all(
        left < right for left, right in zip(days, days[1:]))


def evaluate_contraction(symbol, day, calendar81, raw81, eligibility, *, known_suspended=False):
    """Return {kind, reason, features, quality, signal}, with one exclusive kind.

    Known hard eligibility exclusions take precedence. Required-input failures
    remain unevaluable, distinct from a fully observed false shape predicate.
    Unknown identity fields under the caller's diagnostic eligibility policy
    remain recorded; a successful raw predicate never verifies those fields.
    """
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("Symbol is required")
    day = _day(day)
    if not isinstance(eligibility, dict) or type(known_suspended) is not bool:
        raise ValueError("Eligibility must be an object and known_suspended boolean")
    quality = {"certified": False, "targetAchieved": False, "interpretation": "raw-price-only",
               "actionEvidence": "unverified", "historicalEligibilityVerified": False,
               "comparableRightsVerified": False, "priceBasisId": "raw-unadjusted",
               "unknownHistoricalFields": deepcopy(eligibility.get("unknown", []))}

    def finish(kind, reason, features=None, signal=None):
        return {"version": VERSION, "candidateId": CANDIDATE_ID, "symbol": symbol, "date": day,
                "kind": kind, "reason": reason, "features": deepcopy(features or {}),
                "eligibility": deepcopy(eligibility), "knownSuspended": known_suspended,
                "quality": deepcopy(quality), "signal": deepcopy(signal)}

    reason = eligibility.get("reason")
    eligible = eligibility.get("eligible")
    if type(eligible) is not bool:
        return finish("unevaluable", "baseEligibilityUnknown")
    missing_base_amount = reason == "amount20BelowMinimum" and not _nonnegative(eligibility.get("amount20"))
    missing_base_amplitude = reason == "amplitude20BelowMinimum" and not _nonnegative(eligibility.get("amplitude20"))
    if not eligible and reason in HARD_REASONS and not (missing_base_amount or missing_base_amplitude):
        return finish("ineligible", "baseEligibilityRejected", {"baseReason": reason})
    if missing_base_amount or missing_base_amplitude:
        return finish("unevaluable", "baseAmountUnknown" if missing_base_amount else "baseAmplitudeUnknown")
    if reason in UNKNOWN_REASONS and not (known_suspended and reason == "missingDailyBar"):
        return finish("unevaluable", reason)
    current = raw81[-1] if isinstance(raw81, (list, tuple)) and raw81 else None
    if known_suspended:
        if isinstance(current, dict) and _positive(current.get("volume")):
            return finish("unevaluable", "suspensionContradictsPositiveVolume")
        return finish("ineligible", "knownSuspension")
    if reason == "suspendedOrZeroVolume":
        return finish("unevaluable", "suspensionStatusUnknown")
    if not eligible:
        return finish("unevaluable", "baseEligibilityReasonUnknown", {"baseReason": reason})
    if not isinstance(calendar81, (list, tuple)) or not isinstance(raw81, (list, tuple)):
        return finish("unevaluable", "invalidHistoryWindow")
    if len(calendar81) != HISTORY_SLOTS or len(raw81) != HISTORY_SLOTS:
        return finish("unevaluable", "insufficient81SessionHistory", {
            "calendarSlots": len(calendar81), "rawSlots": len(raw81), "requiredSlots": HISTORY_SLOTS})
    try:
        valid_calendar = _calendar_valid(tuple(calendar81), day)
    except (ValueError, TypeError):
        valid_calendar = False
    if not valid_calendar:
        return finish("unevaluable", "invalidExchangeCalendarAlignment")
    for index, (expected_day, bar) in enumerate(zip(calendar81, raw81)):
        facts = {"slot": index, "expectedDate": expected_day}
        if bar is None:
            return finish("unevaluable", "missingAlignedRawBar", facts)
        if not isinstance(bar, dict) or bar.get("date") != expected_day:
            return finish("unevaluable", "rawHistoryDateMismatch", facts)
        if bar.get("priceBasisId") != "raw-unadjusted":
            return finish("unevaluable", "rawPriceBasisUnknown", facts)
        if not all(_positive(bar.get(key)) for key in ("open", "high", "low", "close", "volume")):
            return finish("unevaluable", "nonpositiveOrUnknownRawOHLCV", facts)
        if not bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]:
            return finish("unevaluable", "invalidRawOHLCEnvelope", facts)
    prior20 = raw81[-21:-1]
    for bar in prior20:
        if not _positive(bar.get("amount")):
            return finish("unevaluable", "prior20AmountUnknown", {"date": bar["date"]})

    prior40 = raw81[-41:-1]
    high40 = max(bar["high"] for bar in prior40)
    ma80 = mean(bar["close"] for bar in raw81[1:])
    previous_ma80 = mean(bar["close"] for bar in raw81[:-1])
    volume40 = mean(bar["volume"] for bar in prior40)
    volume_ratio40 = current["volume"] / volume40
    amount20 = mean(bar["amount"] for bar in prior20)
    blocks = []
    decimal_ranges = []
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        for number, block in enumerate((raw81[-31:-21], raw81[-21:-11], raw81[-11:-1]), 1):
            high = max(bar["high"] for bar in block)
            low = min(bar["low"] for bar in block)
            decimal_range = Decimal(str(high)) / Decimal(str(low)) - Decimal(1)
            decimal_ranges.append(decimal_range)
            blocks.append({"id": number, "startDate": block[0]["date"], "endDate": block[-1]["date"],
                           "sessions": len(block), "maxHigh": high, "minLow": low,
                           "rangeRatio": float(decimal_range), "rangeDecimal": str(decimal_range),
                           "meanVolume": mean(bar["volume"] for bar in block)})
        decreasing = decimal_ranges[0] > decimal_ranges[1] > decimal_ranges[2]
        last_range_small = decimal_ranges[2] <= Decimal("0.08")
    calculated = [high40, ma80, previous_ma80, volume40, volume_ratio40, amount20,
                  *(b["rangeRatio"] for b in blocks), *(b["meanVolume"] for b in blocks)]
    if not all(math.isfinite(value) for value in calculated):
        return finish("unevaluable", "nonfiniteDerivedFeature")
    conditions = {
        "closeAbovePrior40High": current["close"] > high40,
        "closeAboveMA80": current["close"] > ma80,
        "ma80Rising": ma80 > previous_ma80,
        "breakoutVolumeRatioAtLeast1_2": volume_ratio40 >= 1.2,
        "rangesStrictlyDecreasing": decreasing,
        "lastRangeAtMost0_08": last_range_small,
        "lastMeanVolumeAtMost0_70First": blocks[2]["meanVolume"] <= .70 * blocks[0]["meanVolume"],
    }
    features = {"historyStart": calendar81[0], "historyEnd": day, "observations": HISTORY_SLOTS,
        "current": {key: current[key] for key in ("date", "open", "high", "low", "close", "volume")},
        "prior40Window": {"startDate": prior40[0]["date"], "endDate": prior40[-1]["date"], "sessions": 40},
        "prior40High": high40, "ma80": ma80, "previousMa80": previous_ma80,
        "prior40MeanVolume": volume40, "volumeRatio40": volume_ratio40,
        "contractionBlocks": blocks, "prior20MeanAmount": amount20,
        "rankingWindow": {"startDate": prior20[0]["date"], "endDate": prior20[-1]["date"], "sessions": 20},
        "numericComparisonBasis": deepcopy(NUMERIC_COMPARISON_BASIS), "conditions": conditions,
        "failedConditions": [key for key, passed in conditions.items() if not passed]}
    if features["failedConditions"]:
        return finish("notTriggered", features["failedConditions"][0] + "NotMet", features)
    signal = {"version": VERSION, "strategyId": CANDIDATE_ID, "symbol": symbol, "signalDate": day,
              "eligible": True, "prior20MeanAmount": amount20, "features": features,
              "certified": False, "targetAchieved": False, "actionEvidence": "unverified",
              "quality": quality, "entryRule": "oneAttemptNextExchangeSessionOpen;TPlusOneSellLock"}
    return finish("signaled", "rawThreeStageContractionBreakoutObserved", features, signal)


def rank_contraction_signals(signals):
    """Rank every raw signal, marking Top5 before external active exclusions."""
    result = deepcopy(list(signals))
    dates, symbols = set(), set()
    for row in result:
        if (not isinstance(row, dict) or row.get("eligible") is not True or row.get("strategyId") != CANDIDATE_ID
                or not isinstance(row.get("symbol"), str) or not row["symbol"] or row["symbol"] in symbols
                or not _positive(row.get("prior20MeanAmount"))):
            raise ValueError("Rank unique eligible signals with a positive finite prior20MeanAmount")
        dates.add(_day(row.get("signalDate")))
        symbols.add(row["symbol"])
    if len(dates) > 1:
        raise ValueError("Rank only one signal session at a time")
    result.sort(key=lambda row: (-row["prior20MeanAmount"], row["symbol"]))
    for rank, row in enumerate(result, 1):
        row.update(rank=rank, selectedTop5=rank <= TOP_N,
                   score=row["prior20MeanAmount"], rawScore=row["prior20MeanAmount"])
    return result
