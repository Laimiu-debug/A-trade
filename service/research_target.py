"""Independent numerical screening for strategy research, never a profit certificate.

The account curve already includes trading costs. Recomputing from equity avoids
subtracting those costs twice or mistaking 252-session annualization for CAGR.
Passing numerical screens only makes a frozen candidate eligible for review;
independent data, sample-use and execution evidence still require a separate audit.
"""
from __future__ import annotations

from datetime import date
import math
from statistics import mean


VERSION = "strategy-target-screen-v1"
DEFAULT_TARGET = {
    "calendarCagrPct": 30.0,
    "maxDrawdownPct": 20.0,
    "minCalendarDays": 730,
    "minTradingDays": 504,
    "minClosedTrades": 100,
    "minDistinctEntryDates": 50,
    "minPositiveFullYears": 2,
}


def _number(value, label, minimum=0):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and (minimum is None or value >= minimum)
        converted = float(value) if valid else None
    except (OverflowError, ValueError):
        converted = None
    if converted is None or not math.isfinite(converted):
        raise ValueError(f"{label} must be a finite number" + (f" >= {minimum}" if minimum is not None else ""))
    return converted


def _mapping(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _date(value, label):
    try:
        parsed = date.fromisoformat(value) if isinstance(value, str) else None
        if parsed is None or parsed.isoformat() != value:
            raise ValueError()
        return parsed
    except (ValueError, TypeError):
        raise ValueError(f"{label} must be YYYY-MM-DD") from None


def _return_pct(initial, final):
    if initial == 0:
        return None
    value = (final / initial - 1) * 100
    return value if math.isfinite(value) else None


def _growth(initial, final, days):
    if not days:
        return None
    if final == 0:
        return -100.0
    try:
        # Subtract logarithms: final / initial can underflow to zero or overflow
        # even when both supplied equities are finite.
        value = math.expm1((math.log(final) - math.log(initial)) * 365.2425 / days) * 100
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def summarize_result(result, *, stage="development", pilot=False, target=None):
    """Return a compact, independently recomputed account summary.

    `stage` is a research label, not proof of unused data. `targetAchieved` is
    deliberately never inferred from one account, even with certified inputs.
    """
    if stage not in ("development", "validation", "final_candidate"):
        raise ValueError("Unknown research stage")
    if type(pilot) is not bool:
        raise ValueError("pilot must be boolean")
    _mapping(result, "Research result")
    target = dict(DEFAULT_TARGET) if target is None else {**DEFAULT_TARGET, **_mapping(target, "Target")}
    if set(target) != set(DEFAULT_TARGET):
        raise ValueError("Unknown target field")
    for key, value in target.items():
        _number(value, key)
    if any(int(target[key]) != target[key] for key in target if key.startswith("min")):
        raise ValueError("Minimum sample counts must be integers")
    metrics = _mapping(result.get("metrics", {}), "Metrics")
    config = _mapping(result.get("config", {}), "Config")
    initial = _number(metrics.get("initialCapital", config.get("initialCapital")), "initial capital", .000001)
    if config.get("initialCapital") is not None and not math.isclose(
            initial, _number(config["initialCapital"], "configured initial capital", .000001), rel_tol=1e-9, abs_tol=1e-12):
        raise ValueError("Reported initial capital differs from configured capital")
    curve = result.get("curve")
    if not isinstance(curve, list):
        raise ValueError("Account curve must be a list")
    quality = _mapping(result.get("quality", {}), "Quality")
    peak, maximum_drawdown, previous = initial, 0.0, initial
    years, exposures, dates = {}, [], []
    derived_finite = True
    for point in curve:
        _mapping(point, "Account curve point")
        day = _date(point.get("date"), "Account date")
        if dates and day <= dates[-1]:
            raise ValueError("Account dates must be unique and strictly increasing")
        equity = _number(point.get("equity"), "account equity")
        if previous == 0 and equity != 0:
            raise ValueError("A zero-equity account cannot recover without external cash flows")
        peak = max(peak, equity)
        maximum_drawdown = max(maximum_drawdown, (1 - equity / peak) * 100)
        row = years.setdefault(day.year, {"year": day.year, "startingEquity": previous,
                                         "firstDate": day.isoformat(), "tradingDays": 0})
        row.update(finalEquity=equity, lastDate=day.isoformat(), tradingDays=row["tradingDays"] + 1)
        if point.get("marketValue") is not None and equity > 0:
            exposure = _number(point["marketValue"], "market value") / equity * 100
            if math.isfinite(exposure):
                exposures.append(exposure)
            else:
                derived_finite = False
        dates.append(day)
        previous = equity
    final = previous
    if "finalEquity" in metrics and not math.isclose(_number(metrics["finalEquity"], "reported final equity"), final, rel_tol=1e-9, abs_tol=1e-12):
        raise ValueError("Reported final equity differs from the complete account curve")
    start = _date(config["startDate"], "Research start date") if config.get("startDate") else (dates[0] if dates else None)
    end = _date(config["endDate"], "Research end date") if config.get("endDate") else (dates[-1] if dates else None)
    if start and end and start > end:
        raise ValueError("Research start date must not exceed end date")
    if dates and (dates[0] < start or dates[-1] > end):
        raise ValueError("Account curve exceeds declared research dates")
    elapsed = (dates[-1] - dates[0]).days + 1 if dates else 0
    annual = []
    for year, row in sorted(years.items()):
        first_observed = _date(row["firstDate"], "First observed year date")
        last_observed = _date(row["lastDate"], "Last observed year date")
        full = bool(start <= date(year, 1, 1) and end >= date(year, 12, 31)
                    and (first_observed - date(year, 1, 1)).days <= 6
                    and (date(year, 12, 31) - last_observed).days <= 6
                    and row["tradingDays"] >= 200 and quality.get("calendarIndependent") is True)
        annual.append({**row, "returnPct": _return_pct(row["startingEquity"], row["finalEquity"]),
                       "fullCalendarYear": full})
    closed = result.get("closedTrades")
    if not isinstance(closed, list):
        raise ValueError("Closed trades must be a list")
    positions = result.get("positions")
    if not isinstance(positions, list):
        raise ValueError("Terminal positions must be a list")
    date_index = {day: index for index, day in enumerate(dates)}
    episodes = set()
    for trade in closed:
        _mapping(trade, "Closed trade")
        entry = _date(trade.get("entryDate"), "Trade entry date")
        exit_day = _date(trade.get("exitDate"), "Trade exit date")
        symbol = trade.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("Closed trade must identify its security")
        if entry not in date_index or exit_day not in date_index or entry > exit_day:
            raise ValueError("Closed trades must fall within the observed account dates")
        identity = (symbol, entry, exit_day)
        if identity in episodes:
            raise ValueError("Duplicate closed position episode")
        episodes.add(identity)
        _number(trade.get("pnlAmount"), "closed trade net PnL", None)
        holding = _number(trade.get("holdingDays"), "closed trade holding days", 1)
        if holding != date_index[exit_day] - date_index[entry] + 1:
            raise ValueError("Closed trade holding days differ from the observed account calendar")
    distinct_entries = len({trade["entryDate"] for trade in closed})
    positive_full_years = sum(row["fullCalendarYear"] and row["returnPct"] is not None
                              and row["returnPct"] > 0 for row in annual)
    cagr = _growth(initial, final, elapsed)
    total_return = _return_pct(initial, final)
    derived_finite = derived_finite and total_return is not None and cagr is not None
    numerical = cagr is not None and cagr >= target["calendarCagrPct"] and maximum_drawdown <= target["maxDrawdownPct"]
    quality_lists = ("quarantinedSymbols", "unaccountedActionExposure", "executionCapacityIssues", "unverifiedOpeningFills")
    for field in quality_lists:
        if field in quality and not isinstance(quality[field], list):
            raise ValueError(f"Quality {field} must be a list")
    stale_days = quality.get("staleValuationDays")
    if stale_days is not None and (type(stale_days) is not int or not 0 <= stale_days <= len(curve)):
        raise ValueError("Stale valuation days must be an observed-day count")
    cost_values = {key: _number(metrics[key], key) if metrics.get(key) is not None else None
                   for key in ("totalFees", "totalSlippage", "turnoverPct")}
    reported_annualized = (_number(metrics["annualizedReturnPct"], "reported annualization", None)
                          if metrics.get("annualizedReturnPct") is not None else None)
    blockers = []
    checks = [
        (result.get("status") == "completed", "account-not-completed"),
        (not pilot, "pilot-universe-is-not-acceptance-evidence"),
        (stage == "final_candidate", "not-a-frozen-final-candidate"),
        (quality.get("certified") is True, "data-and-execution-not-certified"),
        (quality.get("calendarIndependent") is True, "independent-calendar-missing"),
        (quality.get("pointInTimeComplete") is True, "historical-universe-incomplete"),
        (quality.get("corporateActionsComplete") is True, "corporate-actions-incomplete"),
        (quality.get("openingCapacityVerified") is True, "opening-capacity-unverified"),
        (stale_days is not None and all(key in quality for key in quality_lists), "quality-checks-missing"),
        (stale_days == 0, "stale-valuations"),
        (not quality.get("quarantinedSymbols", []), "quarantined-securities"),
        (not quality.get("unaccountedActionExposure", []), "unaccounted-corporate-action-exposure"),
        (not quality.get("executionCapacityIssues", []), "execution-capacity-violations"),
        (not quality.get("unverifiedOpeningFills", []), "unverified-opening-fills"),
        (all(cost_values[key] is not None for key in ("totalFees", "totalSlippage")), "cost-metrics-missing"),
        (all(isinstance(result.get(key), str) and bool(result[key]) for key in ("fingerprint", "dataDigest")), "source-identity-missing"),
        (derived_finite, "derived-metrics-not-finite"),
        (elapsed >= target["minCalendarDays"], "insufficient-calendar-span"),
        (len(curve) >= target["minTradingDays"], "insufficient-trading-days"),
        (len(closed) >= target["minClosedTrades"], "insufficient-completed-trades"),
        (distinct_entries >= target["minDistinctEntryDates"], "insufficient-distinct-entry-dates"),
        (positive_full_years >= target["minPositiveFullYears"], "insufficient-positive-full-calendar-years"),
        (numerical, "return-or-drawdown-target-not-reached"),
    ]
    blockers.extend(reason for passed, reason in checks if not passed)
    return {
        "version": VERSION, "researchStage": stage, "pilot": pilot,
        "fingerprint": result.get("fingerprint"), "dataDigest": result.get("dataDigest"),
        "independentlyRecomputed": True,
        "period": {"firstDate": dates[0].isoformat() if dates else None,
                   "lastDate": dates[-1].isoformat() if dates else None,
                   "calendarDaysInclusive": elapsed, "tradingDays": len(curve)},
        "initialCapital": initial, "finalEquity": final,
        "totalReturnPct": total_return,
        "calendarCagrPct": cagr,
        "calendarCagrDefinition": "netMarkedEquityGrowthAnnualizedBy365.2425OverInclusiveObservedCalendarDays",
        "engineTradingDayAnnualizedPct": reported_annualized,
        "maxDrawdownPct": maximum_drawdown,
        "avgExposurePct": mean(exposures) if exposures else None,
        "annualReturns": annual,
        "fullCalendarYearScreenDefinition": "declaredWholeYearAndObservedFirstAndLastCalendarWeeksWithAtLeast200Sessions;notIndependentCalendarCompletenessProof",
        "closedTradeCount": len(closed), "distinctEntryDates": distinct_entries,
        "meanClosedTradeNetPnl": mean(t["pnlAmount"] for t in closed) if closed else None,
        "meanHoldingDays": mean(t["holdingDays"] for t in closed) if closed else None,
        "terminalOpenPositions": len(positions),
        "terminalEquityDefinition": "marked-to-close-including-booked-rights-no-hypothetical-liquidation",
        **cost_values,
        "qualityCertified": quality.get("certified") is True,
        "goalAssessment": {
            "target": target, "numericalTargetReached": numerical,
            "eligibleForIndependentReview": not blockers,
            "targetAchieved": False,
            "blockers": blockers,
            "remainingEvidence": ["independently-audited-unused-holdout-and-all-trial-ledger",
                                  "independent-calendar-completeness-review",
                                  "cost-stress-and-parameter-neighborhood-results",
                                  "execution-reconciliation-and-reproduction-review"],
            "interpretation": "A single backtest cannot certify stable future returns or complete the research goal.",
        },
    }
