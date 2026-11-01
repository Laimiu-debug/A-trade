"""Causal finite-window price/volume statistics, without investor identities.

Shares and lots give identical dimensionless results when the input unit is
consistent. No amount, trade direction, participant identity or future outcome
is inferred. With no calendar, windows mean observed bars, not market sessions.
"""
from __future__ import annotations

from datetime import date
from math import fsum, isfinite

VERSION = "force-statistics-v1"
FIELDS = ("close", "prevClose", "TR", "pressure", "closeLocation", "volumeRatio20", "weight",
          "pressure5", "pressure20", "direction5", "direction20", "support5", "support20",
          "opposition5", "opposition20", "positiveShare20", "prior10High", "ma20", "previousMa20",
          "pullback3Return", "pullback3VolumeRatio", "previousHigh")
WARMUP = {"close": 1, "prevClose": 2, "TR": 2, "pressure": 2, "closeLocation": 1,
          "volumeRatio20": 21, "weight": 21, "pressure5": 25, "pressure20": 40,
          "direction5": 25, "direction20": 40, "support5": 25, "support20": 40,
          "opposition5": 25, "opposition20": 40, "positiveShare20": 21,
          "prior10High": 11, "ma20": 20, "previousMa20": 21, "pullback3Return": 5,
          "pullback3VolumeRatio": 14, "previousHigh": 2}


def _day(value):
    if not isinstance(value, str):
        raise ValueError("Dates must be canonical YYYY-MM-DD strings")
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError:
        raise ValueError("Dates must be canonical YYYY-MM-DD strings") from None
    return value


def _positive(value):
    return type(value) in (int, float) and isfinite(value) and value > 0


def _safe(value):
    return value if isfinite(value) else None


def _mean(values):
    # Scaling also avoids overflow when individually finite inputs are large.
    scale = max(values)
    return scale * (fsum(v / scale for v in values) / len(values))


def prepare_force_statistics(bars, *, calendar=None, volume_unit="shares"):
    """Return one row per supplied calendar date, including missing-bar slots.

    Date/identity errors raise. Missing or invalid numerical evidence yields
    None only for the dependent fields. All required rows must have positive
    volume and valid OHLC; zero activity is not a zero-pressure observation.
    Prior three-volume comparison uses T-3..T-1 versus T-13..T-4.
    """
    units = {"shares": "shares", "lots": "lots", "股": "shares", "手": "lots"}
    if not isinstance(volume_unit, str) or volume_unit not in units:
        raise ValueError("volume_unit must declare shares or lots")
    if not isinstance(bars, list):
        raise ValueError("bars must be a list")
    by_day = {}
    previous = ""
    for bar in bars:
        if not isinstance(bar, dict):
            raise ValueError("Each bar must be an object with a date")
        day = _day(bar.get("date"))
        if day <= previous:
            raise ValueError("Bar dates must be strictly increasing and unique")
        by_day[day] = bar
        previous = day
    if calendar is None:
        days = list(by_day)
    else:
        if not isinstance(calendar, list):
            raise ValueError("calendar must be a list")
        days = [_day(day) for day in calendar]
        day_set = set(days)
        if days != sorted(day_set) or any(day not in day_set for day in by_day):
            raise ValueError("Calendar must be ordered, unique and contain every bar date")

    raw, valid, issues, invalid_prefix = [], [], [], [0]
    for day in days:
        bar = by_day.get(day)
        bad = [] if bar is not None else ["missingBar"]
        if bar is not None:
            bad = [key for key in ("open", "high", "low", "close", "volume") if not _positive(bar.get(key))]
            if not any(key in bad for key in ("open", "high", "low", "close")) and not (
                    bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]):
                bad.append("ohlcOrder")
        raw.append(bar)
        valid.append(not bad)
        issues.append(bad)
        invalid_prefix.append(invalid_prefix[-1] + bool(bad))

    def complete(start, stop):
        return start >= 0 and stop > start and invalid_prefix[stop] == invalid_prefix[start]

    rows = []
    for i, day in enumerate(days):
        row = {"date": day, **dict.fromkeys(FIELDS)}
        bar = raw[i]
        if valid[i]:
            row["close"] = bar["close"]
            spread = bar["high"] - bar["low"]
            row["closeLocation"] = ((bar["close"] - bar["low"]) - (bar["high"] - bar["close"])) / spread if spread else 0.
        if complete(i - 1, i + 1):
            prior = raw[i - 1]
            row["prevClose"], row["previousHigh"] = prior["close"], prior["high"]
            tr = max(bar["high"], prior["close"]) - min(bar["low"], prior["close"])
            row["TR"] = tr
            row["pressure"] = (bar["close"] - prior["close"]) / tr if tr else 0.
        if complete(i - 20, i + 1):
            average = _mean([raw[j]["volume"] for j in range(i - 20, i)])
            ratio = bar["volume"] / average
            row["volumeRatio20"] = _safe(ratio)
            row["weight"] = min(ratio, 3.)
        if complete(i - 10, i + 1):
            row["prior10High"] = max(raw[j]["high"] for j in range(i - 10, i))
        if complete(i - 19, i + 1):
            row["ma20"] = _mean([raw[j]["close"] for j in range(i - 19, i + 1)])
        if complete(i - 20, i):
            row["previousMa20"] = _mean([raw[j]["close"] for j in range(i - 20, i)])
        if complete(i - 4, i):
            row["pullback3Return"] = _safe(raw[i - 1]["close"] / raw[i - 4]["close"] - 1)
        if complete(i - 13, i):
            row["pullback3VolumeRatio"] = _safe(_mean([raw[j]["volume"] for j in range(i - 3, i)]) /
                                                _mean([raw[j]["volume"] for j in range(i - 13, i - 3)]))
        recent_pressure = [r["pressure"] for r in rows[max(0, i - 19):]] + [row["pressure"]]
        if len(recent_pressure) == 20 and all(p is not None for p in recent_pressure):
            row["positiveShare20"] = sum(p > 0 for p in recent_pressure) / 20
        for n in (5, 20):
            window = rows[max(0, i - n + 1):] + [row]
            if len(window) != n or any(r["weight"] is None or r["pressure"] is None for r in window):
                continue
            denominator = fsum(r["weight"] for r in window)
            if denominator <= 0:
                continue
            row[f"pressure{n}"] = fsum(r["weight"] * r["pressure"] for r in window) / denominator
            row[f"direction{n}"] = fsum(r["weight"] * ((r["close"] > r["prevClose"]) - (r["close"] < r["prevClose"])) for r in window) / denominator
            row[f"support{n}"] = (1 + row[f"pressure{n}"]) / 2
            row[f"opposition{n}"] = (1 - row[f"pressure{n}"]) / 2
        unknown = [key for key in FIELDS if row[key] is None]
        diagnostics = []
        if row["pressure20"] is not None and complete(i - 20, i + 1):
            change = bar["close"] - raw[i - 20]["close"]
            if change > 0 and row["pressure20"] < 0:
                diagnostics.append("priceUp20PressureNegative")
            elif change < 0 and row["pressure20"] > 0:
                diagnostics.append("priceDown20PressurePositive")
        row.update(status="complete" if not unknown else "unknown" if len(unknown) == len(FIELDS) else "partial",
                   barStatus="valid" if valid[i] else "missing" if bar is None else "invalid",
                   invalidFields=issues[i], unknownFields=unknown, diagnostics=diagnostics)
        rows.append(row)
    return {"version": VERSION, "metadata": {
                "calendarBasis": "providedCalendar" if calendar is not None else "observedBars",
                "inputVolumeUnit": units[volume_unit], "statisticUnits": "dimensionlessExceptPricesAndTR",
                "inputBarCount": len(bars), "outputRowCount": len(rows),
                "validBarCount": sum(valid), "missingBarCount": sum(bar is None for bar in raw),
                "invalidBarCount": sum(bar is not None and not ok for bar, ok in zip(raw, valid)),
                "firstDate": days[0] if days else None, "lastDate": days[-1] if days else None,
                "requiredBars": dict(WARMUP), "weightCap": 3, "volumeBaseline": "prior20ExcludingToday",
                "pressureDefinition": "(close-prevClose)/(max(high,prevClose)-min(low,prevClose));zeroTR=0",
                "pressureAggregation": "sum(weight*pressure)/sum(weight)",
                "directionAggregation": "sum(weight*sign(close-prevClose))/sum(weight)",
                "shareMeaning": "support/opposition are price-volume proxy shares, not participant money flows",
                "amountUsed": False, "forwardDataUsed": False, "certified": False},
            "rows": rows, "current": dict(rows[-1]) if rows else None}
