"""Independent direct-window macro oracle; never imports production gate code."""
import math


def oracle(calendar, original, amounts, closes, start, end):
    days = [d for d in calendar if start <= d <= end]
    gates = {}
    daily = []
    for d in days:
        t = calendar.index(d)
        v = [amounts.get(x) for x in calendar[max(0, t - 20):t + 1]]
        numeric = lambda x: type(x) in (int, float) and math.isfinite(x) and x > 0
        known = len(v) == 21 and all(numeric(x) for x in v)
        amount_ok = known and v[-1] >= 1e12 and math.fsum(v[-5:]) / 5 >= .8 * math.fsum(v[:20]) / 20
        liquid_status = "passed" if amount_ok else "failed" if known else "unknown"
        states = []
        terms = {}
        for code in ("sh000001", "sz399106"):
            raw = [closes.get(code, {}).get(x) for x in calendar[max(0, t - 59):t + 1]]
            ready = len(raw) == 60 and all(numeric(x) for x in raw)
            small = math.fsum(raw[40:]) / 20 if ready else None
            large = math.fsum(raw) / 60 if ready else None
            passed = ready and raw[-1] > large and small > large
            states.append("passed" if passed else "failed" if ready else "unknown")
            terms[code] = {"status": states[-1], "close": raw[-1] if raw else None, "mean20": small, "mean60": large}
        combined_states = [liquid_status, *states]
        combined = "failed" if "failed" in combined_states else "unknown" if "unknown" in combined_states else "passed"
        gates[d] = (liquid_status, combined)
        daily.append({"date": d, "liquidityStatus": liquid_status, "combinedStatus": combined, "indexTerms": terms,
            "priorMean20Cny": math.fsum(v[:20]) / 20 if known else None,
            "currentMean5Cny": math.fsum(v[-5:]) / 5 if known else None, "totalAmountCny": v[-1] if v else None})
    return {"original": [dict(r) for r in original],
        "liquid": [dict(r) for r in original if gates[r["signalDate"]][0] == "passed"],
        "liquidTrend": [dict(r) for r in original if gates[r["signalDate"]][1] == "passed"]}, daily
