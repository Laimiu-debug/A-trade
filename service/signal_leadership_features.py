"""Causal raw-price leadership descriptors, without signals or future labels.

The caller supplies aligned exchange-calendar slots ending at T, at most 61.
Short prefixes are warmup, not permission to shorten a required window. A raw
view must carry priceBasisId='raw-unadjusted'; this declares a format, not a
corporate-action bridge. Unknown trade status does not certify tradability.
daily_features never changes its inputs. Ranking and persistence explicitly
add fields to the supplied rows in place and return that same list.
"""
from __future__ import annotations

from collections import deque
from datetime import date
from functools import lru_cache
import math
from statistics import mean


FEATURE_VERSION = "daily-leadership-raw-v1"
PRICE_BASIS = "raw-unadjusted"
FEATURE_FIELDS = (
    "return20", "return60", "relativeReturn20", "relativeReturn60",
    "positiveCloseReturnSessions20", "closePathMaxDrawdown20",
    "signedPathEfficiency20", "closeAboveMA20Sessions20",
    "amountToday", "prior20MeanAmount", "amountRelativeToOwnPrior20",
)


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def _positive(value):
    return _finite(value) and value > 0


@lru_cache(maxsize=8192)
def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Dates must be YYYY-MM-DD")
    return value


@lru_cache(maxsize=2048)
def _valid_calendar(days, today):
    return bool(days) and days[-1] == today and all(_day(d) <= today for d in days) and all(
        a < b for a, b in zip(days, days[1:]))


def _trade_state(value):
    # Only explicitly supplied daily flags are interpreted. No current status,
    # security name, zero-volume observation, or missing bar implies a halt.
    if value is False or type(value) is int and value == 0 or value == "0":
        return "halted"
    if value is True or type(value) is int and value == 1 or value == "1":
        return "trading"
    return "unknown"


def _observations(days, bars, statuses):
    observations = []
    for day, bar, status in zip(days, bars, statuses):
        state = _trade_state(status)
        reasons = []
        missing = bar is None
        aligned = isinstance(bar, dict) and bar.get("date") == day
        basis = aligned and bar.get("priceBasisId") == PRICE_BASIS
        volume = basis and _positive(bar.get("volume"))
        if missing:
            reasons.append("missingDailyBar")
        elif not aligned:
            reasons.append("rawDateMismatch")
        elif not basis:
            reasons.append("rawPriceBasisUnknown")
        else:
            if not all(_positive(bar.get(k)) for k in ("open", "high", "low", "close", "volume")):
                reasons.append("nonpositiveOrUnknownRawOHLCV")
            elif not bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]:
                reasons.append("invalidRawOHLCEnvelope")
        if state == "halted":
            reasons.append("suspensionContradictsPositiveVolume" if volume else "knownSuspension")
        price_valid = not reasons
        amount_valid = bool(basis and volume and state != "halted" and _positive(bar.get("amount")))
        amount_reasons = []
        if not amount_valid:
            if missing or not aligned or not basis:
                amount_reasons = reasons[:1]
            elif state == "halted":
                amount_reasons = ["suspensionContradictsPositiveVolume" if volume else "knownSuspension"]
            else:
                amount_reasons = ["nonpositiveOrUnknownRawVolume" if not volume else "explicitRawAmountUnknown"]
        observations.append({"date": day, "bar": bar, "priceValid": price_valid,
            "amountValid": amount_valid, "missing": missing, "halted": state == "halted",
            "unknownTradeStatus": state == "unknown", "reasons": reasons, "amountReasons": amount_reasons})
    return observations


def _coverage(observations, required, *, amount=False):
    valid_key, reason_key = ("amountValid", "amountReasons") if amount else ("priceValid", "reasons")
    valid = sum(row[valid_key] for row in observations)
    missing = [r["date"] for r in observations if r["missing"]]
    invalid = [r["date"] for r in observations if not r[valid_key] and not r["missing"]]
    reasons = sorted({reason for row in observations for reason in row[reason_key]})
    if len(observations) < required:
        reasons.insert(0, "insufficientCalendarHistory")
    return {"requiredSessions": required, "availableCalendarSessions": len(observations),
        "observedSessions": sum(not row["missing"] for row in observations), "validSessions": valid,
        "complete": len(observations) == required and valid == required,
        "missingDates": missing, "invalidDates": invalid,
        "haltDates": [r["date"] for r in observations if r["halted"]],
        "unknownTradeStatusDates": [r["date"] for r in observations if r["unknownTradeStatus"]],
        "reasons": reasons}


def daily_features(symbol, day, calendar61, raw61, benchmark61, *, trade_status61=None, benchmark_trade_status61=None):
    """One JSON-safe feature row from aligned raw slots at or before day.

    Required prices: returns/path need 21 or 61 slots; twenty comparisons with
    each day's trailing MA20 need 39 slots. Raw amount has its own validity gate
    (aligned raw basis, positive volume/explicit amount, no explicit halt), so a
    bad OHLC field does not erase independently known amount. All means use
    statistics.mean; signed-path distance uses math.fsum. Nonfinite derived
    values are null with a reason, never NaN/Infinity.
    """
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("Symbol is required")
    day = _day(day)
    if not all(isinstance(x, (list, tuple)) for x in (calendar61, raw61, benchmark61)):
        raise ValueError("Calendar, raw and benchmark inputs must be aligned sequences")
    size = len(calendar61)
    if size > 61 or len(raw61) != size or len(benchmark61) != size:
        raise ValueError("Use equal-length aligned windows with at most 61 slots")
    if not _valid_calendar(tuple(calendar61), day):
        raise ValueError("Calendar must be uniquely ordered and end exactly at day")
    if trade_status61 is None:
        trade_status61 = [None] * size
    if not isinstance(trade_status61, (list, tuple)) or len(trade_status61) != size:
        raise ValueError("Trade status must be absent or aligned with the calendar")
    if benchmark_trade_status61 is None:
        benchmark_trade_status61 = [None] * size
    if not isinstance(benchmark_trade_status61, (list, tuple)) or len(benchmark_trade_status61) != size:
        raise ValueError("Benchmark trade status must be absent or aligned with the calendar")
    raw = _observations(calendar61, raw61, trade_status61)
    benchmark = _observations(calendar61, benchmark61, benchmark_trade_status61)
    row = {"featureVersion": FEATURE_VERSION, "symbol": symbol, "asOfDate": day,
        "rawPriceBasis": PRICE_BASIS, "comparableActionEvidence": "unverified",
        "historicalMembershipEvidence": "unknown", "historicalStEvidence": "unknown",
        "historicalListingEvidence": "unknown", "historicalTradeStatusEvidence": "explicit-daily-flags-and-unknowns",
        "certified": False, "targetAchieved": False,
        "completeness": {}, "featureReasons": {}}
    row.update({field: None for field in FEATURE_FIELDS})
    row.update({k: v for k, v in _coverage(raw, 61).items() if k in (
        "requiredSessions", "availableCalendarSessions", "observedSessions", "validSessions",
        "missingDates", "invalidDates", "haltDates", "unknownTradeStatusDates")})
    row["benchmarkQuality"] = _coverage(benchmark, 61)
    coverage = row["completeness"]

    def assign(field, value):
        if _finite(value):
            row[field] = value
        else:
            row["featureReasons"][field] = ["nonfiniteDerivedFeature"]

    def unavailable(fields, *groups):
        reasons = sorted({r for group in groups for r in group["reasons"]})
        for field in fields:
            row["featureReasons"][field] = reasons or ["requiredWindowUnavailable"]

    for horizon in (20, 60):
        stock_window, bench_window = raw[-(horizon + 1):], benchmark[-(horizon + 1):]
        stock_cov = coverage[f"security{horizon}"] = _coverage(stock_window, horizon + 1)
        bench_cov = coverage[f"benchmark{horizon}"] = _coverage(bench_window, horizon + 1)
        stock_ratio = bench_ratio = None
        if stock_cov["complete"]:
            stock_ratio = stock_window[-1]["bar"]["close"] / stock_window[0]["bar"]["close"]
            if _positive(stock_ratio):
                assign(f"return{horizon}", stock_ratio - 1)
            else:
                row["featureReasons"][f"return{horizon}"] = ["nonfiniteOrUnderflowDerivedRatio"]
        else:
            unavailable([f"return{horizon}"], stock_cov)
        if bench_cov["complete"]:
            bench_ratio = bench_window[-1]["bar"]["close"] / bench_window[0]["bar"]["close"]
        if stock_cov["complete"] and bench_cov["complete"]:
            if _positive(stock_ratio) and _positive(bench_ratio):
                ratio = stock_ratio / bench_ratio
                if _positive(ratio):
                    assign(f"relativeReturn{horizon}", ratio - 1)
                else:
                    row["featureReasons"][f"relativeReturn{horizon}"] = ["nonfiniteOrUnderflowDerivedRatio"]
            else:
                row["featureReasons"][f"relativeReturn{horizon}"] = ["nonfiniteOrUnderflowDerivedRatio"]
        else:
            unavailable([f"relativeReturn{horizon}"], stock_cov, bench_cov)

    path_window = raw[-21:]
    path_cov = coverage["path20"] = _coverage(path_window, 21)
    path_fields = ("positiveCloseReturnSessions20", "closePathMaxDrawdown20", "signedPathEfficiency20")
    if path_cov["complete"]:
        closes = [r["bar"]["close"] for r in path_window]
        differences = [b - a for a, b in zip(closes, closes[1:])]
        row["positiveCloseReturnSessions20"] = sum(d > 0 for d in differences)
        peak, drawdown = closes[0], 0.
        for value in closes:
            peak = max(peak, value)
            drawdown = max(drawdown, (peak - value) / peak)
        assign("closePathMaxDrawdown20", drawdown)
        try:
            distance = math.fsum(abs(d) for d in differences)
        except OverflowError:
            distance = None
        if distance == 0:
            row["featureReasons"]["signedPathEfficiency20"] = ["zeroPathDistance"]
        elif _positive(distance):
            assign("signedPathEfficiency20", (closes[-1] - closes[0]) / distance)
        else:
            row["featureReasons"]["signedPathEfficiency20"] = ["nonfiniteDerivedFeature"]
    else:
        unavailable(path_fields, path_cov)

    ma_window = raw[-39:]
    ma_cov = coverage["ma20"] = _coverage(ma_window, 39)
    if ma_cov["complete"]:
        closes = [r["bar"]["close"] for r in ma_window]
        averages = [mean(closes[i-19:i+1]) for i in range(19, 39)]
        if all(_finite(value) for value in averages):
            row["closeAboveMA20Sessions20"] = sum(closes[i] > averages[i-19] for i in range(19, 39))
        else:
            row["featureReasons"]["closeAboveMA20Sessions20"] = ["nonfiniteDerivedFeature"]
    else:
        unavailable(["closeAboveMA20Sessions20"], ma_cov)

    prior_amount, today_amount = raw[-21:-1], raw[-1:]
    amount_cov = coverage["amountPrior20"] = _coverage(prior_amount, 20, amount=True)
    today_cov = coverage["amountToday"] = _coverage(today_amount, 1, amount=True)
    row["knownAmountSessions20"] = amount_cov["validSessions"]
    if today_cov["complete"]:
        assign("amountToday", today_amount[0]["bar"]["amount"])
    else:
        unavailable(["amountToday"], today_cov)
    if amount_cov["complete"]:
        assign("prior20MeanAmount", mean(r["bar"]["amount"] for r in prior_amount))
    else:
        unavailable(["prior20MeanAmount"], amount_cov)
    if row["amountToday"] is not None and _positive(row["prior20MeanAmount"]):
        ratio = row["amountToday"] / row["prior20MeanAmount"]
        if _positive(ratio):
            assign("amountRelativeToOwnPrior20", ratio)
        else:
            row["featureReasons"]["amountRelativeToOwnPrior20"] = ["nonfiniteOrUnderflowDerivedRatio"]
    else:
        unavailable(["amountRelativeToOwnPrior20"], amount_cov, today_cov)
    return row


def _validate_rows(rows, day=None):
    if not isinstance(rows, list):
        raise ValueError("Rows must be a list; ranks and persistence are added in place")
    symbols, dates = set(), set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("symbol"), str) or not row["symbol"] or row["symbol"] in symbols:
            raise ValueError("Rows require unique nonempty symbols")
        symbols.add(row["symbol"])
        dates.add(_day(row.get("asOfDate")))
    if len(dates) > 1 or day is not None and dates and dates != {day}:
        raise ValueError("Rows must belong to one supplied market session")
    return symbols


def add_cross_section_ranks(rows):
    """Mutate rows with separate evaluable covered-sample midrank percentiles.

    Ascending average 1-based rank r has percentile (r-.5)/N. Exact numeric
    ties share that value. Eligibility/current identity are never consulted.
    """
    _validate_rows(rows)
    for horizon in (20, 60):
        key = f"relativeReturn{horizon}"
        for row in rows:
            value = row.get(key)
            if value is not None and not _finite(value):
                raise ValueError("Rank values must be finite numbers or explicit null")
    for horizon in (20, 60):
        key = f"relativeReturn{horizon}"
        ranked = sorted((r for r in rows if r.get(key) is not None), key=lambda r: (r[key], r["symbol"]))
        size = len(ranked)
        for row in rows:
            row[key + "Percentile"] = None
            row[key + "CrossSectionCount"] = size
        index = 0
        while index < size:
            stop = index + 1
            while stop < size and ranked[stop][key] == ranked[index][key]:
                stop += 1
            percentile = (index + stop) / (2 * size)
            for row in ranked[index:stop]:
                row[key + "Percentile"] = percentile
            index = stop
    return rows


class PersistenceTracker:
    """Archive actual daily RS20 ranks over twenty caller-supplied sessions.

    The caller must update once per exchange session, including warmup, with
    the entire fixed symbol set. Dates must increase and symbol coverage cannot
    silently change. No old rank is recomputed using today's cross-section.
    """
    def __init__(self):
        self._history = {}
        self._counts = {}
        self._days = deque(maxlen=20)
        self._symbols = None

    def update(self, day, rows):
        day = _day(day)
        symbols = _validate_rows(rows, day)
        if self._days and day <= self._days[-1]:
            raise ValueError("Update each market session once in increasing order")
        if self._symbols is not None and symbols != self._symbols:
            raise ValueError("Every session must retain the fixed symbol population")
        for row in rows:
            if "relativeReturn20Percentile" not in row:
                raise ValueError("Rank rows before updating persistence")
            value = row["relativeReturn20Percentile"]
            if value is not None and (not _finite(value) or not 0 <= value <= 1):
                raise ValueError("Strength percentile must be in [0,1] or null")
            count = row.get("relativeReturn20CrossSectionCount")
            if type(count) is not int or count < 0 or value is not None and count == 0:
                raise ValueError("Keep each daily strength cross-section denominator")
        if len({r["relativeReturn20CrossSectionCount"] for r in rows}) > 1:
            raise ValueError("A daily strength cross-section has one denominator")
        if self._symbols is None:
            self._symbols = symbols
            self._history = {symbol: deque(maxlen=20) for symbol in symbols}
            self._counts = {symbol: deque(maxlen=20) for symbol in symbols}
        self._days.append(day)
        for row in rows:
            history = self._history[row["symbol"]]
            history.append(row["relativeReturn20Percentile"])
            counts = self._counts[row["symbol"]]
            counts.append(row["relativeReturn20CrossSectionCount"])
            values = [None] * (20 - len(history)) + list(history)
            known = [value for value in values if value is not None]
            row.update(trailing20DailyStrengthPercentiles=values,
                trailing20StrengthCrossSectionCounts=[None] * (20 - len(counts)) + list(counts),
                strengthRankSessionDates20=[None] * (20 - len(self._days)) + list(self._days),
                knownStrengthRankSessions20=len(known), availableStrengthRankSessions20=len(self._days),
                meanStrengthPercentile20=mean(known) if len(known) == 20 else None,
                minimumStrengthPercentile20=min(known) if len(known) == 20 else None,
                topQuantileSessionCount20=None)
        return rows
