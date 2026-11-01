"""Same-session activity/relative-price alignment of a frozen peer group.

Pure evidence arithmetic: no I/O, group formation, future prices, or account.
The caller binds the previously audited thirty-slot amount evidence. This
module does not rebuild its market universe or reinterpret amount as flow.
"""
from __future__ import annotations

from datetime import date
import math

from service.signal_leader_peer_expansion import _logs, _window

VERSION = "research-peer-activity-alignment-v1"
AMOUNT_VERSION = "research-peer-amount-share-v1"
GROUP_SIZE, PRICE_SLOTS, RETURN_SLOTS = 20, 11, 10


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _dates(values, count):
    _require(isinstance(values, (list, tuple)) and len(values) == count,
             f"Exactly {count} aligned dates are required")
    result = list(values)
    for day in result:
        _require(type(day) is str and date.fromisoformat(day).isoformat() == day,
                 "Dates must be canonical YYYY-MM-DD")
    _require(result == sorted(set(result)), "Dates must be strictly increasing and unique")
    return result


def _symbol(value):
    _require(type(value) is str and value and value == value.strip(), "Invalid symbol identity")
    return value


def _sum(values):
    try:
        result = math.fsum(values)
    except (OverflowError, ValueError):
        return None
    return result if _finite(result) else None


def _amount(anchor, members, days, evidence):
    """Check identity/alignment only; source arithmetic remains caller-bound."""
    if evidence is None:
        return None, [None] * RETURN_SLOTS, None, ["missingAmountEvidence"]
    _require(isinstance(evidence, dict), "Amount evidence must be an object or null")
    _require(evidence.get("version") == AMOUNT_VERSION and evidence.get("anchor") == anchor,
             "Amount evidence version/anchor differs")
    prior_members = evidence.get("members")
    _require(isinstance(prior_members, list) and len(prior_members) == len(set(prior_members))
             and set(prior_members) == set(members), "Amount evidence frozen membership differs")
    all_days = _dates(evidence.get("windowDates"), 30)
    _require(all_days[19:] == days and evidence.get("formationDate") == days[0]
             and evidence.get("signalDate") == days[-1], "Amount and price dates differ from exact F through T")
    daily = evidence.get("dailyRows")
    _require(isinstance(daily, list) and len(daily) == 30
             and all(isinstance(row, dict) and row.get("date") == day for row, day in zip(daily, all_days)),
             "Amount evidence must retain all thirty ordered daily rows")
    _require(evidence.get("observationDates") == days[1:], "Amount observation dates differ")
    control = evidence.get("control")
    _require(control is None or type(control) is bool, "Amount control must be bool or null")
    _require(type(evidence.get("complete")) is bool, "Amount completeness must be explicit")
    shares = [row.get("share") for row in daily[-RETURN_SLOTS:]]
    reasons = []
    if control is None or evidence["complete"] is not True:
        reasons.append("originalAmountUnknown")
    if any(not _finite(value) or not 0 <= value <= 1 for value in shares):
        reasons.append("invalidActivityShare")
    basis = {key: evidence.get(key) for key in ("version", "marketDigest", "universeSha256")}
    return control, [v if _finite(v) and 0 <= v <= 1 else None for v in shares], basis, reasons


def evaluate_peer_activity_alignment(anchor, members, window_dates11, raw_by_symbol,
                                     benchmark11, amount_evidence):
    """Return three nested policies on one common complete observation set.

    A malformed identity/date join raises. Missing/invalid price observations,
    incomplete frozen membership or unknown original amount remain unknown in
    all three policies, even when the original amount control was known false.
    Price quality and log arithmetic use the unchanged peer-expansion helpers.
    """
    anchor = _symbol(anchor)
    _require(isinstance(members, (list, tuple)), "Frozen members must be a list or tuple")
    members = [_symbol(symbol) for symbol in members]
    _require(len(members) <= GROUP_SIZE and len(members) == len(set(members)) and anchor not in members,
             "At most twenty unique fixed peers excluding anchor are required")
    days = _dates(window_dates11, PRICE_SLOTS)
    _require(isinstance(raw_by_symbol, dict), "Raw price inputs must map symbols to aligned bars")
    control, shares, amount_basis, reasons = _amount(anchor, members, days, amount_evidence)
    if len(members) != GROUP_SIZE:
        reasons.append("incompleteFrozenPeerGroup")

    # Deliberately reuse the frozen raw-basis, suspension, positive OHLCV,
    # envelope and exact-date checks rather than admitting close-only paths.
    price_quality, peer_logs = [], {}
    for symbol in sorted(members):
        raw = raw_by_symbol.get(symbol)
        reason, slot = _window(raw, days)
        logs = _logs(raw) if reason is None else None
        if reason is None and logs is None:
            reason = "nonfiniteLogReturn"
        price_quality.append({"symbol": symbol, "reason": reason, "invalidSlot": slot})
        if reason is None:
            peer_logs[symbol] = logs
    benchmark_reason, benchmark_slot = _window(benchmark11, days)
    benchmark_logs = _logs(benchmark11) if benchmark_reason is None else None
    if benchmark_reason is None and benchmark_logs is None:
        benchmark_reason = "nonfiniteLogReturn"
    if any(row["reason"] is not None for row in price_quality):
        reasons.append("incompletePeerPriceWindow")
    if benchmark_reason is not None:
        reasons.append("incompleteBenchmarkPriceWindow")

    daily = [{"date": day, "previousDate": days[i], "activityShare": shares[i],
              "peerMeanLogReturn": None, "benchmarkLogReturn": None,
              "relativeLogReturn": None, "centeredProduct": None, "status": "unknown"}
             for i, day in enumerate(days[1:])]
    mean_a = mean_r = net_r = covariance = products = None
    if not reasons:
        peer_means, relative_returns = [], []
        for index in range(RETURN_SLOTS):
            total = _sum(peer_logs[symbol][index] for symbol in sorted(members))
            average = total / GROUP_SIZE if total is not None else None
            relative = average - benchmark_logs[index] if average is not None else None
            if not _finite(relative):
                reasons.append("nonfiniteRelativeLogReturn")
                break
            peer_means.append(average)
            relative_returns.append(relative)
        if not reasons:
            sum_a, net_r = _sum(shares), _sum(relative_returns)
            if sum_a is None or net_r is None:
                reasons.append("nonfiniteAggregate")
            else:
                mean_a, mean_r = sum_a / RETURN_SLOTS, net_r / RETURN_SLOTS
                # Exact constant sequences have mathematical zero variance.
                # Use exact equality, never an epsilon, to avoid a centering
                # roundoff residue manufacturing a nonzero covariance.
                constant_a = all(v == shares[0] for v in shares)
                constant_r = all(v == relative_returns[0] for v in relative_returns)
                products = [(0. if constant_a else a - mean_a) * (0. if constant_r else r - mean_r)
                            for a, r in zip(shares, relative_returns)]
                total = _sum(products)
                covariance = total / RETURN_SLOTS if total is not None else None
                if covariance is None:
                    reasons.append("nonfiniteCovariance")
                else:
                    for i, row in enumerate(daily):
                        row.update(peerMeanLogReturn=peer_means[i], benchmarkLogReturn=benchmark_logs[i],
                                   relativeLogReturn=relative_returns[i], centeredProduct=products[i], status="known")
    complete = not reasons
    if not complete:
        # No partial-window total can accidentally be interpreted as a policy
        # decision; individual input shares and price-quality evidence remain.
        mean_a = mean_r = net_r = covariance = products = None
    current = control if complete else None
    progress = current and net_r > 0 if complete else None
    aligned = progress and covariance > 0 if complete else None
    return {"version": VERSION, "anchor": anchor, "members": members,
        "windowDates": days, "formationDate": days[0], "signalDate": days[-1],
        "observationDates": days[1:], "amountBasis": amount_basis,
        "originalAmountControl": control, "dailyRows": daily, "meanA": mean_a, "meanR": mean_r,
        "netR": net_r, "cov": covariance, "centeredProducts": products,
        "currentActivity": current, "priceProgress": progress, "activityAligned": aligned,
        "complete": complete, "kind": "unknown" if not complete else "notActive" if not current else
            "noPositiveProgress" if not progress else "nonPositiveAlignment" if not aligned else "aligned",
        "unknownReasons": reasons,
        "priceQuality": {"members": price_quality,
            "benchmark": {"reason": benchmark_reason, "invalidSlot": benchmark_slot},
            "helper": "service.signal_leader_peer_expansion._window/_logs; unchanged"},
        "basis": {"activity": "Prior audited daily fixed-peer amount share; same U(T); no market rebuild here.",
            "return": "Mean of twenty peer close log returns minus benchmark close log return, each F+1..T session.",
            "netR": "fsum(ten daily relative log returns); descriptive price progression, not portfolio PnL.",
            "covariance": "fsum((a-meanA)*(r-meanR))/10; population covariance, no variance normalization.",
            "constantSeries": "Exact constant input series center to zero; no epsilon or near-zero snapping.",
            "policies": "currentActivity=original control; priceProgress=currentActivity and netR>0; activityAligned=priceProgress and cov>0.",
            "completeness": "All three policies require the same full original amount and eleven-slot price evidence.",
            "interpretation": "Same-day co-movement, not investor identity, net inflow, or causal price impact.",
            "timing": "Computed after T close using F..T only; no future or intraday execution evidence."},
        "certified": False, "targetAchieved": False}
