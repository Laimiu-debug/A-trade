"""Descriptive accounting of a completed or partial research account.

No market access, execution, selection thresholds, annualization, or forced
liquidation. Unknown economic values remain unknown. A supplied calendar is not
reconstructed from dates: period coverage describes only the supplied curve.
"""
from copy import deepcopy
from datetime import date
import hashlib
import json
import math
from statistics import median

VERSION = "research-account-outcomes-v1"
QUANTILES = (0., .05, .25, .5, .75, .95, 1.)


def _copy(value):
    # Reject NaN/Infinity even in sidecars; never silently serialize them as JSON.
    json.dumps(value, allow_nan=False)
    return deepcopy(value)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Expected canonical ISO date")
    return value


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _safe(value):
    return value if _finite(value) else None


def _sum(values):
    if any(value is None for value in values):
        return None
    try:
        return _safe(math.fsum(values))
    except OverflowError:
        return None


def _ratio(a, b, scale=1.):
    return _safe(a / b * scale) if a is not None and b is not None and b > 0 else None


def _mean(values):
    return _ratio(_sum(values), len(values))


def _quantiles(values):
    values = sorted(values)
    output = {}
    for q in QUANTILES:
        key = f"p{round(q * 100):02d}"
        if not values:
            output[key] = None
            continue
        pos = (len(values) - 1) * q
        lower, upper = math.floor(pos), math.ceil(pos)
        fraction = pos - lower
        output[key] = _sum([values[lower] * (1 - fraction), values[upper] * fraction])
    return output


def _stats(values):
    known = [value for value in values if value is not None]
    return {"count": len(values), "knownCount": len(known), "unknownCount": len(values) - len(known),
            "mean": _mean(known), "median": _safe(median(known)) if known else None,
            "minimum": min(known) if known else None, "maximum": max(known) if known else None,
            "status": "empty" if not values else "complete" if len(known) == len(values) else "partialKnownValuesOnly"}


class _Reader:
    def __init__(self):
        self.unknown = set()
        self.issues = []

    def number(self, value, path, *, minimum=None):
        if not _finite(value) or (minimum is not None and value < minimum):
            self.unknown.add(path)
            return None
        return value

    def field(self, row, name, path, **kwargs):
        return self.number(row.get(name), f"{path}.{name}", **kwargs)

    def reconcile(self, observed, expected, label):
        residual = _sum([observed, -expected]) if observed is not None and expected is not None else None
        # Floating ledger arithmetic only; this is not a profitability threshold.
        matches = (math.isclose(observed, expected, rel_tol=1e-10, abs_tol=1e-7)
                   if observed is not None and expected is not None else None)
        if matches is False:
            self.issues.append(label)
        return {"observed": observed, "expected": expected, "residual": residual,
                "status": "unknown" if matches is None else "reconciled" if matches else "mismatch"}


def _closed(rows, reader):
    decorated, keys = [], set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("symbol"), str) or not row["symbol"]:
            raise ValueError("Malformed closed episode")
        entry, exit_day = _day(row["entryDate"]), _day(row["exitDate"])
        if entry > exit_day:
            raise ValueError("Closed episode exits before entry")
        key = (exit_day, entry, row["symbol"])
        if key in keys:
            raise ValueError("Duplicate closed episode identity")
        keys.add(key)
        path = f"closedTrades[{index}]"
        pnl = reader.field(row, "pnlAmount", path)
        invested = reader.field(row, "investedAmount", path, minimum=0)
        net_return = _ratio(pnl, invested, 100)
        if net_return is None:
            reader.unknown.add(path + ".netReturnPct")
        reported = row.get("returnPct")
        if reported is not None and net_return is not None:
            reader.reconcile(reader.number(reported, path + ".returnPct"), net_return, path + ".reportedReturnMismatch")
        holding = reader.field(row, "holdingDays", path, minimum=1)
        if holding is not None and int(holding) != holding:
            raise ValueError("Episode holdingDays must count integer sessions")
        decorated.append({"inputIndex": index, "record": _copy(row), "netReturnPct": net_return,
                          "pnlState": "unknown" if pnl is None else "win" if pnl > 0 else "loss" if pnl < 0 else "zero",
                          "pnlAmount": pnl, "holdingSessions": holding})
    decorated.sort(key=lambda row: (row["record"]["exitDate"], row["record"]["entryDate"], row["record"]["symbol"], row["inputIndex"]))
    pnls = [row["pnlAmount"] for row in decorated]
    returns = [row["netReturnPct"] for row in decorated]
    known_returns = [value for value in returns if value is not None]
    wins = [value for value in known_returns if value > 0]
    losses = [value for value in known_returns if value < 0]
    gross_profit = _sum([value for value in pnls if value is not None and value > 0])
    gross_loss = _sum([-value for value in pnls if value is not None and value < 0])
    complete_pnl, complete_return = all(value is not None for value in pnls), len(known_returns) == len(returns)
    counts = {state: sum(row["pnlState"] == state for row in decorated) for state in ("win", "loss", "zero", "unknown")}
    streak = maximum = 0
    for row in decorated:
        streak = streak + 1 if row["pnlState"] == "loss" else 0
        maximum = max(streak, maximum)
    payoff = _ratio(_mean(wins), -_mean(losses)) if wins and losses and complete_return else None
    factor = _ratio(gross_profit, gross_loss) if complete_pnl else None
    mean_return = _mean(known_returns) if complete_return else None
    status = "noClosedEpisodes" if not rows else "complete" if complete_pnl and complete_return else "unknownValuesRetained"
    return {"count": len(rows), "wins": counts["win"], "losses": counts["loss"], "zeros": counts["zero"],
            "unknownPnlCount": counts["unknown"], "knownReturnCount": len(known_returns),
            "unknownReturnCount": len(returns) - len(known_returns), "status": status,
            "netPnlAmount": _sum(pnls), "grossProfitAmount": gross_profit if complete_pnl else None,
            "grossLossAmount": gross_loss if complete_pnl else None,
            "winRatePct": _ratio(counts["win"], len(rows), 100) if complete_pnl else None,
            "meanReturnPct": mean_return, "expectancyReturnPct": mean_return,
            "expectancyPnlAmount": _mean(pnls),
            "medianReturnPct": _safe(median(known_returns)) if known_returns and complete_return else None,
            "meanWinReturnPct": _mean(wins) if complete_return else None,
            "medianWinReturnPct": _safe(median(wins)) if wins and complete_return else None,
            "meanLossReturnPct": _mean(losses) if complete_return else None,
            "medianLossReturnPct": _safe(median(losses)) if losses and complete_return else None,
            "meanWinPnlAmount": _mean([p for p in pnls if p is not None and p > 0]) if complete_pnl else None,
            "meanLossPnlAmount": _mean([p for p in pnls if p is not None and p < 0]) if complete_pnl else None,
            "medianWinPnlAmount": _safe(median([p for p in pnls if p is not None and p > 0])) if counts["win"] and complete_pnl else None,
            "medianLossPnlAmount": _safe(median([p for p in pnls if p is not None and p < 0])) if counts["loss"] and complete_pnl else None,
            "payoffRatio": payoff, "profitFactor": factor,
            "returnQuantilesPct": _quantiles(known_returns if complete_return else []),
            "quantileDefinition": "linearInterpolationAt(n-1)*q;allClosedIncludingZero;unknownMakesFullDistributionNull",
            "maxConsecutiveLosses": maximum if complete_pnl and rows else None,
            "observedConsecutiveLossesLowerBound": maximum if rows else None,
            "ordering": "exitDate,entryDate,symbol,inputIndex;zeroAndUnknownBreakObservedLossRun",
            "holdingSessions": _stats([row["holdingSessions"] for row in decorated]),
            "metricStatus": {"payoffRatio": "known" if payoff is not None else "unknownReturns" if not complete_return else "noWinsOrLosses",
                             "profitFactor": "known" if factor is not None else "unknownPnl" if not complete_pnl else "noLossDenominator",
                             "winRatePct": "noClosedEpisodes" if not rows else "known" if complete_pnl else "unknownPnl"},
            "secondaryClosedNet30": {"hits": sum(value >= 30 for value in known_returns), "denominator": len(rows),
                                     "unknownCount": len(rows) - len(known_returns),
                                     "ratePct": _ratio(sum(value >= 30 for value in known_returns), len(rows), 100) if complete_return else None,
                                     "meaning": "secondaryClosedEpisodeDiagnostic;notAnObjectiveOrProbabilityForecast"},
            "episodes": decorated}


def _concentration(closed):
    episodes = closed["episodes"]
    positive = sorted((row for row in episodes if row["pnlAmount"] is not None and row["pnlAmount"] > 0),
                      key=lambda row: (-row["pnlAmount"], row["record"]["exitDate"], row["record"]["entryDate"], row["record"]["symbol"]))
    removed = positive[:5]
    removed_ids = {row["inputIndex"] for row in removed}
    remaining = [row for row in episodes if row["inputIndex"] not in removed_ids]
    known = closed["unknownPnlCount"] == 0
    return {"unit": "fullyClosedPositionEpisode;positiveNetPnlRank",
            "grossProfitAmount": closed["grossProfitAmount"], "positiveEpisodeCount": len(positive),
            "top1ProfitSharePct": _ratio(_sum([r["pnlAmount"] for r in positive[:1]]), closed["grossProfitAmount"], 100) if known else None,
            "top5ProfitSharePct": _ratio(_sum([r["pnlAmount"] for r in removed]), closed["grossProfitAmount"], 100) if known else None,
            "status": "unknownPnl" if not known else "noPositiveProfitDenominator" if not positive else "known",
            "top5": [{"symbol": row["record"]["symbol"], "entryDate": row["record"]["entryDate"],
                      "exitDate": row["record"]["exitDate"], "pnlAmount": row["pnlAmount"], "netReturnPct": row["netReturnPct"]} for row in removed],
            "excludingTop5PositiveEpisodes": {"removedCount": len(removed), "remainingCount": len(remaining),
                "netPnlAmount": _sum([r["pnlAmount"] for r in remaining]) if known else None,
                "meanReturnPct": _mean([r["netReturnPct"] for r in remaining]) if closed["unknownReturnCount"] == 0 else None,
                "meaning": "arithmeticClosedEpisodeDiagnostic;notRerunAccountOrRightTailRejectionRule"}}


def _intervals(start, end, supplied):
    result = [{"id": "overall", "start": start, "end": end, "kind": "overall"}]
    if supplied is None:
        for year in range(int(start[:4]), int(end[:4]) + 1):
            result.append({"id": str(year), "start": f"{year}-01-01", "end": f"{year}-12-31", "kind": "calendarYear"})
            result.extend(({"id": f"{year}-H1", "start": f"{year}-01-01", "end": f"{year}-06-30", "kind": "calendarHalfYear"},
                           {"id": f"{year}-H2", "start": f"{year}-07-01", "end": f"{year}-12-31", "kind": "calendarHalfYear"}))
    else:
        if not isinstance(supplied, list):
            raise ValueError("intervals must be a list")
        result.extend(_copy(supplied))
    seen = set()
    for row in result:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] or row["id"] in seen:
            raise ValueError("Interval IDs must be unique strings")
        if _day(row["start"]) > _day(row["end"]):
            raise ValueError("Reversed interval")
        seen.add(row["id"])
    return result


def _period(spec, curve, daily, initial, configured_start, configured_end, reader):
    selected = [row for row in curve if spec["start"] <= row["date"] <= spec["end"]]
    previous = [row for row in curve if row["date"] < spec["start"]]
    entering = previous[-1]["equity"] if previous else initial
    complete_values = entering is not None and entering > 0 and all(row["equity"] is not None for row in selected)
    ending = selected[-1]["equity"] if selected else None
    net = _sum([ending, -entering]) if ending is not None and entering is not None else None
    drawdown, peak = 0., entering
    if complete_values:
        for row in selected:
            peak = max(peak, row["equity"])
            drawdown = max(drawdown, (peak - row["equity"]) / peak * 100)
    exposure, holding_age, missing_holdings = [], [], []
    for row in selected:
        exposure.append(_ratio(row["marketValue"], row["equity"], 100))
        item = daily.get(row["date"])
        if item is None or not isinstance(item.get("holdings"), list):
            missing_holdings.append(row["date"])
            continue
        for holding in item["holdings"]:
            holding_age.append(reader.field(holding, "holdingDays", f"daily[{row['date']}].holdings[{holding.get('symbol')} ]", minimum=1))
    unreliable = [row["date"] for row in selected if row["valuationReliable"] is not True]
    return {"id": spec["id"], "kind": spec.get("kind", "explicit"), "requestedStart": spec["start"], "requestedEnd": spec["end"],
            "observedStart": selected[0]["date"] if selected else None, "observedEnd": selected[-1]["date"] if selected else None,
            "observedSessions": len(selected), "enteringEquity": entering,
            "enteringEquityDate": previous[-1]["date"] if previous else None,
            "enteringEquityBasis": "previousObservedClose" if previous else "initialCapital",
            "endingEquity": ending, "netPnlAmount": net,
            "returnPct": _ratio(net, entering, 100) if complete_values else None,
            "maxDrawdownPct": _safe(drawdown) if selected and complete_values else None,
            "status": "noObservations" if not selected else "numericUnknown" if not complete_values else "observedValues;valuationUnverified" if unreliable else "observedValues",
            "valuationUnreliableDates": unreliable,
            "withinConfiguredInterval": configured_start <= spec["start"] and spec["end"] <= configured_end,
            "calendarCompletenessProven": False,
            "coverageMeaning": "suppliedCurveOnly;weekendsHolidaysAndMissingSessionsNotReconstructed",
            "investedSessions": sum(row["marketValue"] is not None and row["marketValue"] > 0 for row in selected),
            "unknownInvestmentSessions": sum(row["marketValue"] is None for row in selected),
            "investedSessionPct": _ratio(sum(row["marketValue"] is not None and row["marketValue"] > 0 for row in selected), len(selected), 100)
                if all(row["marketValue"] is not None for row in selected) else None,
            "exposurePct": _stats(exposure), "exposureMeaning": "equalWeightPerObservedClose;stockMarketValue/equity;notCapitalTimeWeighted",
            "heldSessionAge": _stats(holding_age), "heldSessionAgeMeaning": "oneObservationPerPositionPerClose;includesEntrySession;notClosedTradeDuration",
            "missingDailyHoldingsDates": missing_holdings}


def summarize_account(result, state=None, *, policy_id=None, policy_label=None, intervals=None):
    """Describe one result without modifying it or treating open positions as losses.

    ``state`` may be the engine state or an adapter checkpoint with engineState;
    it is used only to cross-check existing facts. Custom intervals are
    ``[{id,start,end}]``. Overall is always included; otherwise years and halves
    use fixed calendar boundaries. No interval is certified calendar-complete.
    Structural date/identity corruption raises ValueError; missing numerical
    facts produce nulls and explicit unknown paths. All result quality is kept.
    """
    result = _copy(result)
    if not isinstance(result, dict):
        raise ValueError("result must be an account result")
    reader = _Reader()
    config = result["config"]
    start, end = _day(config["startDate"]), _day(config["endDate"])
    initial = reader.field(config, "initialCapital", "config", minimum=0)
    if start > end or initial is None or initial <= 0:
        raise ValueError("Positive initial capital and ordered interval required")
    for key in ("curve", "trades", "closedTrades", "positions", "actionLedger", "cashReceivables", "actionPayables"):
        if not isinstance(result.get(key), list) or any(not isinstance(row, dict) for row in result[key]):
            raise ValueError(f"{key} must contain complete rows (possibly empty)")
    for key in ("forcedExits", "preExRights", "quality"):
        if not isinstance(result.get(key), dict):
            raise ValueError(f"{key} must be a dictionary")
    curve, previous = [], None
    for index, row in enumerate(result["curve"]):
        day = _day(row["date"])
        if not start <= day <= end or (previous is not None and day <= previous):
            raise ValueError("Curve dates must be unique, ordered, and inside config")
        curve.append({"date": day, **{key: reader.field(row, key, f"curve[{index}]", minimum=0 if key == "marketValue" else None)
                                     for key in ("equity", "cash", "marketValue")},
                      "valuationReliable": row.get("valuationReliable")})
        previous = day
    cutoff = curve[-1]["date"] if curve else None
    for key, field in (("closedTrades", "exitDate"), ("trades", "date"), ("actionLedger", "date")):
        for row in result[key]:
            if not cutoff or not start <= _day(row[field]) <= cutoff:
                raise ValueError(f"{key} fact lies outside observed account interval")
    closed = _closed(result["closedTrades"], reader)
    daily = {}
    for row in result.get("daily", []):
        day = _day(row["date"])
        if day in daily or day not in {row["date"] for row in curve}:
            raise ValueError("Duplicate or out-of-curve daily holdings")
        daily[day] = row
    periods = []
    for spec in _intervals(start, end, intervals):
        period = _period(spec, curve, daily, initial, start, end, reader)
        period["closedEpisodes"] = _closed([row for row in result["closedTrades"] if spec["start"] <= row["exitDate"] <= spec["end"]], reader)
        period["closedEpisodeAssignment"] = "exitDate;entireEpisodePnlNotPartialFillDate"
        periods.append(period)
    final_equity = curve[-1]["equity"] if curve else initial
    final_cash = curve[-1]["cash"] if curve else initial
    final_market = curve[-1]["marketValue"] if curve else 0.
    open_rows, seen = [], set()
    for row in result["positions"]:
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol or symbol in seen:
            raise ValueError("Duplicate or invalid open position")
        seen.add(symbol)
        entry = _day(row["entryDate"])
        if not cutoff or entry > cutoff:
            raise ValueError("Open position lies after cutoff")
        path = f"positions[{symbol}]"
        market = reader.field(row, "marketValue", path, minimum=0)
        cost = reader.field(row, "costBasis", path, minimum=0)
        realized = reader.field(row, "realizedPnl", path)
        age = len([r for r in curve if entry <= r["date"] <= cutoff]) if entry >= start else None
        open_rows.append({"record": _copy(row), "marketValue": market, "unrealizedPnlAmount": _sum([market, -cost]) if market is not None and cost is not None else None,
                          "realizedPnlWhileOpen": realized, "observedHoldingSessions": age,
                          "holdingAgeStatus": "observedCurveSessions" if age is not None else "entryPrecedesObservedInterval",
                          "markStaleAtCutoff": row.get("priceDate") != cutoff})
    rights = {key: _copy(result[key]) for key in ("cashReceivables", "actionPayables", "preExRights", "actionLedger")}
    rights["reservedActionCash"] = reader.field(result, "reservedActionCash", "result", minimum=0)
    for key in ("cashReceivables", "actionPayables", "preExRights"):
        items = result[key].values() if key == "preExRights" else result[key]
        rights[key + "Amount"] = _sum([reader.field(row, "amount", f"{key}[{index}]", minimum=0) for index, row in enumerate(items)])
    receivable, payable, pre_ex = (rights[key + "Amount"] for key in ("cashReceivables", "actionPayables", "preExRights"))
    rights_adjustment = _sum([receivable, -payable, pre_ex]) if payable is not None else None
    rights["assetAdjustment"] = rights_adjustment
    rights["meaning"] = "receivables-payables+preExRights;reservedActionCashIsLiquidityReservationNotAdditionalLiability"
    gross_amounts, fees, slippages, cashflows = [], [], [], []
    buys = sells = 0
    for index, row in enumerate(result["trades"]):
        side = row.get("side")
        if side not in ("buy", "sell"):
            raise ValueError("Unknown fill side")
        amount = reader.field(row, "amount", f"trades[{index}]", minimum=0)
        fee = reader.field(row, "fee", f"trades[{index}]", minimum=0)
        gross_amounts.append(amount)
        fees.append(fee)
        slippages.append(reader.field(row, "slippage", f"trades[{index}]", minimum=0))
        cashflows.append(_sum([amount if side == "sell" else -amount, -fee]) if amount is not None and fee is not None else None)
        buys += side == "buy"
        sells += side == "sell"
    action_flows, unknown_stages = [], []
    noncash = {"record", "ex", "subscriptionDeclined", "refundReceivable", "unsupported"}
    for index, row in enumerate(result["actionLedger"]):
        stage = row.get("stage")
        if stage in ("cashReceived", "cashPaid", "subscriptionPaid"):
            amount = reader.field(row, "amount", f"actionLedger[{index}]", minimum=0)
            action_flows.append(amount if stage == "cashReceived" else -amount if amount is not None else None)
        elif stage not in noncash:
            unknown_stages.append(_copy(row))
    trade_flow, action_flow = _sum(cashflows), _sum(action_flows) if not unknown_stages else None
    open_realized = _sum([row["realizedPnlWhileOpen"] for row in open_rows])
    floating = _sum([row["unrealizedPnlAmount"] for row in open_rows])
    net_pnl = _sum([final_equity, -initial]) if final_equity is not None else None
    expected_cash = _sum([initial, trade_flow, action_flow])
    balance_equity = _sum([final_cash, final_market, rights_adjustment])
    pnl_components = _sum([closed["netPnlAmount"], open_realized, floating])
    reconciliation = {"cashFlow": reader.reconcile(final_cash, expected_cash, "cashFlowMismatch"),
                      "balanceSheet": reader.reconcile(final_equity, balance_equity, "balanceSheetMismatch"),
                      "positionMarketValue": reader.reconcile(final_market, _sum([r["marketValue"] for r in open_rows]), "positionMarketValueMismatch"),
                      "episodePnlBridge": reader.reconcile(net_pnl, pnl_components, "episodePnlBridgeResidual"),
                      "tradeNetCashFlow": trade_flow, "actionNetCashFlow": action_flow,
                      "closedEpisodePnl": closed["netPnlAmount"], "openPositionRealizedPnl": open_realized,
                      "openPositionUnrealizedPnl": floating, "unknownActionStages": unknown_stages,
                      "bridgeMeaning": "residualIsUnallocatedOrUnreconciledIncludingRights;neverAllocateItToClosedEpisodes",
                      "stateChecks": []}
    reconciliation["rightsAttributionGapAmount"] = reconciliation["episodePnlBridge"]["residual"]
    reconciliation["rightsAttributionMeaning"] = "gapIsNotProvenRightsProfit;mayIncludeUnallocatedRightsOrOtherInconsistency"
    if state is not None:
        state = _copy(state)
        state = state.get("engineState", state)
        if not isinstance(state, dict):
            raise ValueError("state must be an engine state or checkpoint")
        for key in ("curve", "trades", "closedTrades", "forcedExits", "cashReceivables", "actionPayables", "preExRights", "actionLedger", "reservedActionCash"):
            matches = state.get(key) == result.get(key)
            reconciliation["stateChecks"].append({"field": key, "matches": matches})
            if not matches:
                reader.issues.append("stateMismatch:" + key)
        reconciliation["stateCash"] = reader.reconcile(reader.field(state, "cash", "state"), final_cash, "stateCashMismatch")
        state_positions = state.get("positions")
        matches = isinstance(state_positions, dict) and set(state_positions) == seen and all(
            all(result_row.get(key) == value for key, value in state_positions[result_row["symbol"]].items()) for result_row in result["positions"])
        reconciliation["stateChecks"].append({"field": "positions", "matches": matches})
        if not matches:
            reader.issues.append("stateMismatch:positions")
    metrics = result.get("metrics", {})
    for key, value in (("finalEquity", final_equity), ("totalFees", _sum(fees)), ("totalSlippage", _sum(slippages))):
        if key in metrics:
            reconciliation["reported" + key[0].upper() + key[1:]] = reader.reconcile(reader.number(metrics[key], "metrics." + key), value, "reportedMetricMismatch:" + key)
    paid = ((result.get("research") or {}).get("feeLedger") or {}).get("paidLedger")
    if isinstance(paid, list):
        paid_total = _sum([reader.number(((r.get("quote") or {}).get("breakdown") or {}).get("total"), f"paidLedger[{i}].total", minimum=0) for i, r in enumerate(paid)])
        reconciliation["paidFees"] = reader.reconcile(_sum(fees), paid_total, "paidFeesMismatch")
        reconciliation["paidFillCountMatches"] = len(paid) == len(result["trades"])
        if len(paid) != len(result["trades"]):
            reader.issues.append("paidFillCountMismatch")
    else:
        reconciliation["paidFees"] = {"status": "notSupplied", "observed": _sum(fees), "expected": None, "residual": None}
    output = {"schema": VERSION, "policyId": policy_id, "policyLabel": policy_label,
              "inputResultSha256": _digest(result), "certified": False, "targetAchieved": False,
              "meaning": "descriptiveRegisteredCostAccount;noStrategySelectionThreshold;noInferenceOfIndependentFutureProbability",
              "account": {"status": result.get("status"), "configuredStart": start, "configuredEnd": end,
                          "observedStart": curve[0]["date"] if curve else None, "cutoff": cutoff,
                          "initialCapital": initial, "finalEquity": final_equity, "finalCash": final_cash,
                          "finalMarketValue": final_market, "netPnlAmount": net_pnl,
                          "netReturnPct": periods[0]["returnPct"], "maxDrawdownPct": periods[0]["maxDrawdownPct"],
                          "fillCount": len(result["trades"]), "buyFillCount": buys, "sellFillCount": sells,
                          "feesPaid": _sum(fees), "slippageAlreadyInFillPrices": _sum(slippages),
                          "grossTradedAmount": _sum(gross_amounts), "turnoverPct": _ratio(_sum(gross_amounts), initial, 100),
                          "turnoverMeaning": "sumBuyAndSellNotional/initialCapital;notHalfTurnover",
                          "noForcedLiquidation": True},
              "closedEpisodes": closed, "periods": periods, "concentration": _concentration(closed),
              "openPositions": {"count": len(open_rows), "positions": open_rows,
                                "holdingSessions": _stats([r["observedHoldingSessions"] for r in open_rows]),
                                "excludedFromClosedEpisodeDenominator": True},
              "unresolvedExits": {"count": len(result["forcedExits"]), "orders": _copy(result["forcedExits"]),
                                  "pendingTarget": _copy(result.get("pendingTarget"))},
              "opportunityDispositions": _copy({key: (result.get("research") or {}).get(key)
                                                  for key in ("opportunityCounts", "opportunities")}),
              "rights": rights, "reconciliation": reconciliation,
              "sourceQuality": _copy(result["quality"]), "warnings": _copy(result.get("warnings", [])),
              "quality": {"numericUnknownPaths": sorted(reader.unknown), "integrityIssues": sorted(set(reader.issues)),
                          "suppliedQualityPreserved": True, "calendarCompletenessProven": False,
                          "valuationUnreliableDates": periods[0]["valuationUnreliableDates"],
                          "interpretation": "knownValuesAreDescriptive;qualityFlagsNotRemovedByArithmeticReconciliation"}}
    json.dumps(output, allow_nan=False)
    return output
