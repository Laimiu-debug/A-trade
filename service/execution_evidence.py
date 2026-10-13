"""Opening capacity uses opening evidence, never the future day's total volume."""
import math


def opening_capacity(security, day, previous_volume, participation):
    planned = math.floor(previous_volume * participation)
    observed = security.get("openingVolumeByDate", {}).get(day)
    return planned if observed is None else min(planned, math.floor(observed * participation))


def audit_opening_fills(fills, securities, bars_by_symbol):
    """End-of-day consistency audit. It never resizes an opening order/fill."""
    issues, unverified = [], []
    for symbol in sorted({fill["symbol"] for fill in fills}):
        rows = [fill for fill in fills if fill["symbol"] == symbol]
        day = rows[0]["date"]
        bar = bars_by_symbol[symbol]
        opening = securities[symbol].get("openingVolumeByDate", {}).get(day)
        quantity = sum(fill["quantity"] for fill in rows)
        invalid = quantity > bar["volume"] + 1e-8 or (opening is not None and opening > bar["volume"] + 1e-8)
        if invalid:
            issues.append({"date": day, "symbol": symbol, "filledQuantity": quantity,
                           "observedDailyVolume": bar["volume"], "openingVolume": opening,
                           "reason": "fills_or_opening_evidence_exceed_daily_volume"})
        if opening is None:
            unverified.append({"date": day, "symbol": symbol, "filledQuantity": quantity})
        for fill in rows:
            fill.update(capacityBasis="openingVolumeEvidence" if opening is not None else "previousVolumeApproximation",
                        openingCapacityVerified=opening is not None and not invalid,
                        executionValid=not invalid)
    return issues, unverified
