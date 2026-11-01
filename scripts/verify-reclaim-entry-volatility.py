"""Separate vector raw-window oracle; does not import the production builder."""
import math
import numpy as np


def oracle(dataset, original, bases):
    calendar = dataset["calendar"]
    symbols = {r["symbol"] for r in original}
    securities = {s["symbol"]: s for s in dataset["securities"] if s["symbol"] in symbols}
    bars = {s: {b["date"]: b for b in security["bars"]} for s, security in securities.items()}
    evidence = []
    for signal in original:
        t = calendar.index(signal["signalDate"])
        days = calendar[max(0, t - 20):t + 1]
        rows = [bars[signal["symbol"]].get(d) for d in days]
        ready = len(days) == 21
        for d, row in zip(days, rows):
            valid = isinstance(row, dict) and row.get("date") == d and row.get("priceBasisId", "raw-unadjusted") == "raw-unadjusted"
            if valid:
                valid = all(type(row.get(k)) in (int, float) and math.isfinite(row[k]) and row[k] > 0 for k in ("open", "high", "low", "close", "volume"))
            if valid:
                valid = row["low"] <= row["open"] <= row["high"] and row["low"] <= row["close"] <= row["high"]
            ready = ready and valid
        mean = None
        references, ranges, normalized = [], [], []
        if ready:
            refmap = securities[signal["symbol"]].get("precloseByDate", {})
            refs = [refmap[d] if d in refmap else rows[i - 1]["close"] for i, d in enumerate(days) if i]
            ready = all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in refs)
            if ready:
                highs = np.asarray([r["high"] for r in rows[1:]], dtype=float)
                lows = np.asarray([r["low"] for r in rows[1:]], dtype=float)
                r = np.asarray(refs, dtype=float)
                with np.errstate(over="ignore", invalid="ignore"):
                    raw = np.maximum.reduce((highs - lows, np.abs(highs - r), np.abs(lows - r)))
                    ratios = raw / r
                if np.isfinite(ratios).all() and np.isfinite(raw).all():
                    try:
                        mean = math.fsum(float(x) for x in ratios) / 20
                    except OverflowError:
                        mean = None
                    if mean is not None and not math.isfinite(mean):
                        mean = None
                    references, ranges, normalized = refs, raw.tolist(), ratios.tolist()
        evidence.append({"signalId": signal["signalId"], "asOfDate": signal["signalDate"], "windowDates": days,
            "meanTRPct20": mean, "windowStatus": "known" if mean is not None else "unknown",
            "admissionStatus": "unknown" if mean is None else "passed" if mean <= .05 else "failed",
            "references": references, "ranges": ranges, "normalized": normalized})
    by_id = {r["signalId"]: r for r in evidence}
    schedules = {new: [dict(r) for r in bases[old] if by_id[r["signalId"]]["admissionStatus"] == "passed"]
                 for new, old in (("liquidNtr5", "liquid"), ("liquidTrendNtr5", "liquidTrend"))}
    return evidence, schedules
