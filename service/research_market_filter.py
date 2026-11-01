"""Fixed CSI300 close-above-MA60 signal filter; no execution or ranking changes.

The 60 slots come from the supplied market calendar, including signal day.
Missing/invalid prices never shorten that window. Unknown market evidence does
not admit a signal. Benchmark identity, when supplied, must be ``sh000300``.
"""

from copy import deepcopy
from datetime import date
import math
from statistics import mean


VERSION = "csi300-close-above-ma60-v1"
BENCHMARK_SYMBOL = "sh000300"
WINDOW_SESSIONS = 60


def _iso_day(value):
    if not isinstance(value, str):
        raise ValueError("date must be an ISO calendar date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("date must be an ISO calendar date") from None
    if parsed.isoformat() != value:
        raise ValueError("date must be an ISO calendar date")
    return value


def _positive_close(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def filter_by_benchmark_ma60(dataset, signals):
    """Return selected signals and one evidence row per original input signal.

    Original signal fields, input order and rank gaps are preserved. Neither
    input is modified. Calendar/identity contradictions raise ``ValueError``;
    absent, duplicate or invalid required benchmark closes yield ``unknown``.
    Numeric prices outside a signal's 60-session window are never inspected.
    An omitted benchmark symbol uses the caller's designated benchmark slot;
    an explicitly different symbol is rejected instead of relabelled CSI300.
    """
    if not isinstance(dataset, dict) or not isinstance(signals, list):
        raise ValueError("dataset must be a dict and signals a list")
    calendar = dataset.get("calendar")
    if not isinstance(calendar, list):
        raise ValueError("dataset.calendar must be a list")
    if any(_iso_day(day) != day for day in calendar) or calendar != sorted(set(calendar)):
        raise ValueError("calendar must be ordered and unique")
    calendar_index = {day: index for index, day in enumerate(calendar)}
    benchmark = dataset.get("benchmark")
    bars = []
    if benchmark is not None:
        if not isinstance(benchmark, dict) or not isinstance(benchmark.get("bars"), list):
            raise ValueError("benchmark must contain a bars list")
        if benchmark.get("symbol") not in (None, BENCHMARK_SYMBOL):
            raise ValueError("benchmark must be CSI300 (sh000300)")
        bars = benchmark["bars"]
    by_day = {}
    for bar in bars:
        if not isinstance(bar, dict):
            raise ValueError("benchmark bars must be dicts")
        day = _iso_day(bar.get("date"))
        by_day.setdefault(day, []).append(bar)

    selected, evidence, cache = [], [], {}
    counts = {"included": 0, "excluded": 0, "unknown": 0}
    for signal in signals:
        if not isinstance(signal, dict):
            raise ValueError("signals must contain dicts")
        day = _iso_day(signal.get("signalDate"))
        if day not in cache:
            result = {"status": "unknown", "benchmarkClose": None,
                      "benchmarkMA60": None, "reason": None}
            current = by_day.get(day, [])
            if len(current) == 1 and _positive_close(current[0].get("close")):
                result["benchmarkClose"] = float(current[0]["close"])
            index = calendar_index.get(day)
            if index is None:
                result["reason"] = "signalDateOutsideCalendar"
            elif index + 1 < WINDOW_SESSIONS:
                result["reason"] = "insufficientMarketHistory"
            elif benchmark is None:
                result["reason"] = "benchmarkUnavailable"
            else:
                window = calendar[index + 1 - WINDOW_SESSIONS:index + 1]
                missing = [d for d in window if not by_day.get(d)]
                duplicate = [d for d in window if len(by_day.get(d, [])) > 1]
                invalid = [d for d in window if len(by_day.get(d, [])) == 1
                           and not _positive_close(by_day[d][0].get("close"))]
                result.update(windowStart=window[0], windowEnd=day,
                              missingDates=missing, duplicateDates=duplicate,
                              invalidCloseDates=invalid)
                if missing:
                    result["reason"] = "missingBenchmarkClose"
                elif duplicate:
                    result["reason"] = "duplicateBenchmarkDate"
                elif invalid:
                    result["reason"] = "invalidBenchmarkClose"
                else:
                    average = mean(float(by_day[d][0]["close"]) for d in window)
                    result["benchmarkMA60"] = average
                    above = result["benchmarkClose"] > average
                    result["status"] = "included" if above else "excluded"
                    result["reason"] = "closeAboveMA60" if above else "closeNotAboveMA60"
            cache[day] = result
        row = {"signalId": deepcopy(signal.get("signalId")), "signalDate": day,
               **deepcopy(cache[day])}
        for key in ("symbol", "rank", "originalRank"):
            if key in signal:
                row[key] = deepcopy(signal[key])
        evidence.append(row)
        counts[row["status"]] += 1
        if row["status"] == "included":
            selected.append(deepcopy(signal))
    return {"version": VERSION, "benchmarkSymbol": BENCHMARK_SYMBOL,
            "windowSessions": WINDOW_SESSIONS, "selected": selected,
            "evidence": evidence, "counts": counts}
