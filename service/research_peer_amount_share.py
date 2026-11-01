"""Calendar-aligned traded-amount shares of an already frozen twenty-peer group.

No membership formation, I/O, price outcome, account, or trading execution is
performed here. The common amount-observation universe is formed as of T over
all thirty supplied sessions; it is deliberately not called frozen at F.
Explicit finite zero amount is observed zero. Missing and invalid are separate.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
import math

VERSION = "research-peer-amount-share-v1"
WINDOW = 30
FORMATION_INDEX = 19
GROUP_SIZE = 20
PERSISTENT_SESSIONS = 8


def _require(test, message):
    if not test:
        raise ValueError(message)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _dates(values):
    _require(isinstance(values, (list, tuple)) and len(values) == WINDOW,
             "Exactly thirty aligned calendar dates are required")
    days = list(values)
    for day in days:
        _require(type(day) is str and date.fromisoformat(day).isoformat() == day,
                 "Dates must be canonical YYYY-MM-DD")
    _require(days == sorted(set(days)), "Calendar dates must be increasing and unique")
    return days


def _symbol(value):
    _require(type(value) is str and bool(value.strip()) and value == value.strip(),
             "Symbols must be nonempty strings without outer whitespace")
    return value


def _cell(value):
    if value is None:
        return {"state": "missing", "value": None, "token": None, "reason": "missingExplicitAmount"}
    if type(value) not in (int, float):
        token = value if type(value) in (str, bool) else {"type": type(value).__name__}
        return {"state": "invalid", "value": None, "token": token, "reason": "nonnumericExplicitAmount"}
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        return {"state": "invalid", "value": None, "token": str(value), "reason": "nonfiniteExplicitAmount"}
    if value < 0:
        return {"state": "invalid", "value": None, "token": value, "reason": "negativeExplicitAmount"}
    return {"state": "valid", "value": value, "token": value, "reason": None}


def _inputs(window_dates, amounts_by_symbol):
    days = _dates(window_dates)
    _require(isinstance(amounts_by_symbol, dict), "Amounts must map every fixed-population symbol to thirty slots")
    symbols = sorted(_symbol(symbol) for symbol in amounts_by_symbol)
    normalized = {}
    for symbol in symbols:
        values = amounts_by_symbol[symbol]
        _require(isinstance(values, (list, tuple)) and len(values) == WINDOW,
                 "Each symbol requires thirty aligned amount slots; missing slots must be explicit nulls")
        normalized[symbol] = [_cell(value) for value in values]
    return days, symbols, normalized


def _sum(values):
    try:
        value = math.fsum(values)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def build_balanced_market(window_dates, amounts_by_symbol):
    """Return U(T), thirty denominators and explicit observation coverage.

    ``amounts_by_symbol[symbol]`` is a thirty-element list/tuple containing
    explicit amounts or None. Real-study population size is bound by the caller;
    this pure routine also accepts smaller synthetic universes. All thirty
    slots must be finite and >=0 for membership in U(T). No IPO/halt inference.
    """
    days, symbols, normalized = _inputs(window_dates, amounts_by_symbol)
    universe, exclusions = [], []
    coverage = [{"date": day, "validPositive": 0, "validZero": 0, "missing": 0, "invalid": 0}
                for day in days]
    for symbol in symbols:
        missing, invalid, zeros = [], [], []
        for index, cell in enumerate(normalized[symbol]):
            if cell["state"] == "missing":
                missing.append(days[index])
                coverage[index]["missing"] += 1
            elif cell["state"] == "invalid":
                invalid.append({"date": days[index], "reason": cell["reason"]})
                coverage[index]["invalid"] += 1
            elif cell["value"] == 0:
                zeros.append(days[index])
                coverage[index]["validZero"] += 1
            else:
                coverage[index]["validPositive"] += 1
        if missing or invalid:
            exclusions.append({"symbol": symbol, "missingDates": missing, "invalidSlots": invalid,
                               "validZeroDates": zeros})
        else:
            universe.append(symbol)
    denominators = [_sum(normalized[symbol][index]["value"] for symbol in universe)
                    for index in range(WINDOW)]
    totals = {key: sum(row[key] for row in coverage) for key in ("validPositive", "validZero", "missing", "invalid")}
    result = {"version": VERSION, "windowDates": days, "formationDate": days[FORMATION_INDEX],
              "signalDate": days[-1], "universeAsOfDate": days[-1],
              "fixedPopulationCount": len(symbols), "fixedPopulationSha256": _digest(symbols),
              "universe": universe, "universeSha256": _digest(universe),
              "denominators": denominators, "exclusions": exclusions, "coverageByDate": coverage,
              "counts": {"includedSymbols": len(universe), "excludedSymbols": len(exclusions),
                         "totalSlots": WINDOW * len(symbols), **totals,
                         "zeroDenominatorDays": sum(value == 0 for value in denominators),
                         "nonfiniteDenominatorDays": sum(value is None for value in denominators)},
              "amountsSha256": _digest(normalized),
              "digestMeaning": "Normalized amount values and missing/invalid classifications; raw file bytes are separately pinned by caller.",
              "denominatorMeaning": "Same U(T) on every one of the thirty sessions; membership observed over F-19..T, not frozen at F.",
              "amountUnit": "same explicit raw currency unit across stocks; caller binds yuan source",
              "zeroPolicy": "Finite explicit zero retained; unknown is neither zero nor an inferred halt/IPO."}
    result["digest"] = _digest(result)
    return result


def evaluate_peer_amount_share(anchor, members, window_dates, amounts_by_symbol, market):
    """Describe the one registered comparison without changing any peer.

    A malformed identity/window raises. An incomplete group or a frozen peer
    outside U(T) remains unknown for both policies. Current and persistent
    policies use identical full-window observation completeness.
    """
    anchor = _symbol(anchor)
    _require(isinstance(members, (list, tuple)), "Frozen peers must be a list or tuple")
    members = list(members)
    for symbol in members:
        _symbol(symbol)
    _require(len(members) <= GROUP_SIZE and len(set(members)) == len(members) and anchor not in members,
             "Peer identity must be unique, at most twenty, and exclude anchor")
    days, symbols, normalized = _inputs(window_dates, amounts_by_symbol)
    _require(anchor in normalized, "Anchor must belong to the caller's fixed source population")
    # Rebuild from these supplied slots to reject stale U/denominator caches,
    # including same-date values cached from a different thirty-session window.
    expected_market = build_balanced_market(days, amounts_by_symbol)
    _require(isinstance(market, dict) and market == expected_market,
             "Market evidence differs from this exact window and amount input")
    universe = set(market["universe"])
    outside = [symbol for symbol in members if symbol not in universe]
    reasons = []
    if len(members) != GROUP_SIZE:
        reasons.append("incompleteFrozenPeerGroup")
    if outside:
        reasons.append("frozenPeerOutsideBalancedUniverse")
    membership_known = not reasons
    daily = []
    for index, day in enumerate(days):
        denominator = market["denominators"][index]
        numerator = _sum(normalized[symbol][index]["value"] for symbol in members) if membership_known else None
        reason = "incompleteFrozenPeerObservation" if not membership_known else (
            "nonfiniteMarketAmount" if denominator is None else "nonpositiveMarketAmount" if denominator <= 0 else
            "nonfinitePeerAmount" if numerator is None else None)
        share = numerator / denominator if reason is None else None
        if share is not None and not math.isfinite(share):
            reason, share = "nonfinitePeerShare", None
        daily.append({"date": day, "peerAmount": numerator, "marketAmount": denominator,
                      "share": share, "status": "known" if share is not None else "unknown", "reason": reason})
    baseline_values = [row["share"] for row in daily[:20]]
    baseline = math.fsum(baseline_values) / 20 if all(value is not None for value in baseline_values) else None
    current = daily[-1]["share"]
    comparison = [row["share"] > baseline if baseline is not None and row["share"] is not None else None
                  for row in daily[20:]]
    complete = membership_known and all(row["share"] is not None for row in daily)
    above_count = sum(comparison) if all(value is not None for value in comparison) else None
    control = current > baseline if complete else None
    candidate = control and above_count >= PERSISTENT_SESSIONS if complete else None
    daily_reasons = Counter(row["reason"] for row in daily if row["reason"] is not None)
    reasons.extend(reason for reason in daily_reasons if reason not in reasons)
    return {"version": VERSION, "anchor": anchor, "members": members,
            "windowDates": days, "formationDate": days[FORMATION_INDEX], "signalDate": days[-1],
            "marketDigest": market["digest"], "universeSha256": market["universeSha256"],
            "universeCount": len(universe), "frozenPeerCount": len(members),
            "peersOutsideUniverse": outside, "dailyRows": daily, "baseline": baseline, "current": current,
            "observationDates": days[20:], "observationsAboveBaseline": comparison,
            "shareAboveBaselineCount": above_count, "requiredAboveBaselineCount": PERSISTENT_SESSIONS,
            "kind": "unknown" if not complete else "persistent" if candidate else "currentOnly" if control else "notElevated",
            "control": control, "candidate": candidate, "complete": complete,
            "unknownReasons": reasons, "dailyUnknownReasonCounts": dict(daily_reasons),
            "rules": {"baseline": "arithmetic mean of daily shares F-19..F (twenty sessions)",
                      "control": "S_T > baseline", "candidate": "control and count(S_d > baseline,F+1..T)>=8",
                      "comparison": "strict >; no epsilon; current day must pass independently",
                      "population": "Fixed twenty peers exclude anchor; anchor may contribute only to market denominator."},
            "certified": False, "targetAchieved": False}
