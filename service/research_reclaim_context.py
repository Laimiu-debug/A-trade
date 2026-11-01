"""Pure formation-time market context and existing-account attribution.

Both breadth axes use the same already audited matched-five-session sample at
formation T-1. This module does not create signals, run accounts, fill missing
calendar slots, infer integer counts, or calculate returns on subgroup capital.
Record amounts describe existing account episodes, grouped by formation date.
"""
from __future__ import annotations

from datetime import date
import math
from statistics import mean


STATES = ("majorityImproving", "majorityNotImproving", "notMajorityImproving",
          "notMajorityNotImproving", "unknown")
LABELS = {
    "majorityImproving": "20日上涨过半、五日广度增加",
    "majorityNotImproving": "20日上涨过半、五日广度未增加",
    "notMajorityImproving": "20日上涨未过半、五日广度增加",
    "notMajorityNotImproving": "20日上涨未过半、五日广度未增加",
    "unknown": "市场环境证据不足",
}
VALUE_FIELDS = ("positiveBreadthToday", "positiveBreadthEarlier", "matchedBreadthChange5",
                "pairedCoverageToday", "pairedCoverageEarlier", "pairedCoverageFixed")
AMOUNT_FIELDS = ("closedPnlAmount", "openUnrealizedPnlAmount", "openRealizedPnlAmount")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Dates must be canonical YYYY-MM-DD")
    return value


def _text(value, field):
    _require(isinstance(value, str) and bool(value.strip()), "Nonempty string required: " + field)
    return value


def _optional_number(value, field, low, high):
    # Absent, nonnumeric and nonfinite evidence stays unknown; a known finite
    # observation outside its definition is an integrity error.
    if not _finite(value):
        return None
    _require(low <= value <= high, "Finite number outside domain: " + field)
    return value


def _optional_count(value, field):
    if value is None or not _finite(value):
        return None
    _require(type(value) is int and value >= 0, "Nonnegative integer required: " + field)
    return value


def build_context_rows(calendar, market_rows, formation_dates):
    """Classify unique formation dates from their exact T-1 matched5 evidence.

    Calendar and all market-row date identities are checked globally. Numerical
    evidence is inspected only for required T-1 rows, so appending future market
    observations cannot change earlier classifications. Coverage is retained as
    evidence and never used as a new filter. Missing coverage alone does not
    erase otherwise complete breadth axes.
    """
    _require(isinstance(calendar, (list, tuple)) and bool(calendar), "A source calendar is required")
    days = list(calendar)
    _require(all(_day(d) == d for d in days) and days == sorted(set(days)), "Calendar must be unique and sorted")
    index = {day: i for i, day in enumerate(days)}
    _require(isinstance(formation_dates, (list, tuple, set)), "Formation dates must be a sequence or set")
    formations = list(formation_dates)
    _require(all(_day(d) in index for d in formations), "Formation date outside source calendar")
    _require(len(formations) == len(set(formations)), "Duplicate formation date")
    _require(isinstance(market_rows, (list, tuple)), "Market rows must be a sequence")
    by_day = {}
    for row in market_rows:
        _require(isinstance(row, dict), "Market rows must be mappings")
        day = _day(row.get("date"))
        _require(day in index and day not in by_day, "Duplicate or off-calendar market date")
        by_day[day] = row
    output = []
    for formation in sorted(formations):
        at = index[formation]
        as_of = days[at - 1] if at >= 1 else None
        earlier = days[at - 6] if at >= 6 else None
        result = {"formationDate": formation, "asOfDate": as_of, "earlierDate": earlier,
                  "state": "unknown", "status": "unknown", "reason": None,
                  "pairedCount": None, **dict.fromkeys(VALUE_FIELDS)}
        if as_of is None:
            result["reason"] = "asOfSessionUnavailable"
        elif as_of not in by_day:
            result["reason"] = "marketRowUnavailable"
        else:
            row = by_day[as_of]
            matched = row.get("matched5")
            _require(matched is None or isinstance(matched, dict), "matched5 must be a mapping or null")
            if matched is None:
                result["reason"] = "matchedEvidenceUnavailable"
            else:
                supplied_earlier = matched.get("earlierDate")
                if supplied_earlier is not None:
                    _require(_day(supplied_earlier) == earlier, "matched5 earlierDate differs from exact source-calendar T-6")
                paired = _optional_count(matched.get("pairedCount"), "pairedCount")
                result["pairedCount"] = paired
                denominators = {"fixedPopulation": row.get("fixedPopulation"),
                                "todayEvaluableCount": matched.get("todayEvaluableCount"),
                                "earlierEvaluableCount": matched.get("earlierEvaluableCount")}
                for key, value in denominators.items():
                    denominator = _optional_count(value, key)
                    if paired is not None and denominator is not None:
                        _require(paired <= denominator, "pairedCount exceeds " + key)
                for field in VALUE_FIELDS:
                    low, high = (-100, 100) if field == "matchedBreadthChange5" else (0, 1)
                    result[field] = _optional_number(matched.get(field), field, low, high)
                today, prior, change = (result[k] for k in VALUE_FIELDS[:3])
                if all(value is not None for value in (today, prior, change)):
                    _require(abs(change - 100 * (today - prior)) <= 1e-9,
                             "Matched breadth change differs from its same-population endpoints")
                if earlier is None:
                    result["reason"] = "earlierSessionUnavailable"
                elif matched.get("available") is not True:
                    result["reason"] = "matchedComparisonUnavailable"
                elif paired is None:
                    result["reason"] = "pairedCountUnavailable"
                elif paired == 0:
                    result["reason"] = "emptyMatchedPopulation"
                elif supplied_earlier is None:
                    result["reason"] = "matchedEarlierDateUnavailable"
                elif any(value is None for value in (today, prior, change)):
                    result["reason"] = "matchedValuesUnavailable"
                else:
                    state = ("majority" if today > .5 else "notMajority") + ("Improving" if change > 0 else "NotImproving")
                    result.update(state=state, status="known", reason="classified")
        output.append(result)
    return output


def _validate_record(row):
    _require(isinstance(row, dict), "Records must be mappings")
    for key in ("period", "policy", "formationId", "symbol"):
        _text(row.get(key), key)
    formation = _day(row.get("formationDate"))
    _require(row.get("state") in STATES, "Unrecognized context state")
    signal = row.get("signalDate")
    if signal is not None:
        _require(_day(signal) >= formation, "Decision date precedes formation")
    for key in ("entered", "closed", "open", "openMarkStale"):
        _require(type(row.get(key)) is bool, "Boolean required: " + key)
    _require(int(row["closed"]) + int(row["open"]) == int(row["entered"]),
             "Entered episode must be exactly closed or open")
    _require(not row["entered"] or signal is not None, "Entered episode lacks selected signal")
    _require(not row["openMarkStale"] or row["open"], "Stale open mark without an open episode")
    for key in AMOUNT_FIELDS:
        _require(_finite(row.get(key)), "Finite episode amount required: " + key)
    _require(row["closed"] or row["closedPnlAmount"] == 0, "Nonclosed record has closed PnL")
    _require(row["open"] or row["openUnrealizedPnlAmount"] == row["openRealizedPnlAmount"] == 0,
             "Nonopen record has open-episode amounts")
    if row["closed"]:
        _require(_finite(row.get("closedReturnPct")), "Closed episode requires its finite observed return")
    else:
        _require(row.get("closedReturnPct") is None, "Nonclosed record has a closed return")
    sessions = row.get("holdingSessions")
    _require(sessions is None or type(sessions) is int and sessions >= 0, "Invalid holding-session count")
    _require(row.get("reason") is None or isinstance(row["reason"], str), "Reason must be text or null")


def _sum(values):
    try:
        value = math.fsum(values)
    except (OverflowError, ValueError):
        raise ValueError("Episode amount sum is not finite") from None
    _require(_finite(value), "Episode amount sum is not finite")
    return value


def _ratio(numerator, denominator):
    if not denominator:
        return None
    value = numerator / denominator
    _require(_finite(value), "Derived aggregate ratio is not finite")
    return value


def _aggregate(rows, initial_capital):
    closed = [row for row in rows if row["closed"]]
    wins = [row for row in closed if row["closedPnlAmount"] > 0]
    losses = [row for row in closed if row["closedPnlAmount"] < 0]
    selected = sum(row["signalDate"] is not None for row in rows)
    entered = sum(row["entered"] for row in rows)
    profit = _sum(row["closedPnlAmount"] for row in wins)
    loss = _sum(-row["closedPnlAmount"] for row in losses)
    amounts = {key: _sum(row[key] for row in rows) for key in AMOUNT_FIELDS}
    total_pnl = _sum(amounts.values())
    contribution = _ratio(total_pnl, initial_capital) * 100
    _require(_finite(contribution), "Initial-capital contribution is not finite")
    return {"formationCount": len(rows), "selectedSignals": selected, "entered": entered,
            "closed": len(closed), "open": sum(row["open"] for row in rows),
            "notScheduled": len(rows) - selected, "notEntered": selected - entered,
            "uniqueFormationDays": len({row["formationDate"] for row in rows}),
            "uniqueSymbols": len({row["symbol"] for row in rows}),
            "closedMeanPct": mean(row["closedReturnPct"] for row in closed) if closed else None,
            "winRatePct": len(wins) / len(closed) * 100 if closed else None,
            "meanWinPct": mean(row["closedReturnPct"] for row in wins) if wins else None,
            "meanLossPct": mean(row["closedReturnPct"] for row in losses) if losses else None,
            "profitFactor": _ratio(profit, loss),
            "minClosedPct": min(row["closedReturnPct"] for row in closed) if closed else None,
            "top5ShareOfGrossProfitPct": _ratio(_sum(sorted((row["closedPnlAmount"] for row in wins), reverse=True)[:5]), profit) * 100 if profit else None,
            **amounts, "totalPnlAmount": total_pnl, "contributionPpOfInitialCapital": contribution}


def summarize_records(records, initial_capital=500000):
    """Aggregate complete existing-account records by formation context/year.

    Domains are the observed periods and global observed policies, sorted as
    strings. Every combination receives all five states, even when empty. Each
    period's formation years similarly cross all observed policies and states.
    Relabeling periods supports predeclared epochs without changing this logic.
    Capital contribution is PnL / original capital, not a subgroup return path.
    """
    _require(isinstance(records, (list, tuple)), "Records must be a sequence")
    _require(_finite(initial_capital) and initial_capital > 0, "Initial capital must be finite and positive")
    seen, identities, overall, yearly = set(), {}, {}, {}
    periods, policies, years = set(), set(), {}
    for row in records:
        _validate_record(row)
        period, policy, formation_id, state = (row[k] for k in ("period", "policy", "formationId", "state"))
        key = (period, policy, formation_id)
        _require(key not in seen, "Duplicate period/policy/formation record")
        seen.add(key)
        identity = (row["formationDate"], row["symbol"], state)
        _require((period, formation_id) not in identities or identities[period, formation_id] == identity,
                 "Same formation has contradictory date/symbol/context across policies")
        identities[period, formation_id] = identity
        year = int(row["formationDate"][:4])
        periods.add(period)
        policies.add(policy)
        years.setdefault(period, set()).add(year)
        overall.setdefault((period, policy, state), []).append(row)
        yearly.setdefault((period, year, policy, state), []).append(row)
    overall_rows, year_rows = [], []
    for period in sorted(periods):
        for policy in sorted(policies):
            for state in STATES:
                overall_rows.append({"period": period, "policy": policy, "state": state,
                                     **_aggregate(overall.get((period, policy, state), []), initial_capital)})
        for year in sorted(years[period]):
            for policy in sorted(policies):
                for state in STATES:
                    year_rows.append({"period": period, "formationYear": year, "policy": policy, "state": state,
                                      **_aggregate(yearly.get((period, year, policy, state), []), initial_capital)})
    return {"overallRows": overall_rows, "formationYearRows": year_rows}
