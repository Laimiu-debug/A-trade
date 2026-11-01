"""Daily-bar forward signal diagnostics, never a claim of executable profit.

Each event owns a fixed, isolated budget. Entry quantities are fixed at the
signal close using a price-limit reserve, then attempted at the next session's
open only. The main label is a full-position, next-open net-return proxy.
Intraday highs/lows establish observations, never fills or intraday sequencing.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import math
from statistics import median

from .portfolio_config import normalize_config
from .portfolio_engine import PortfolioEngine, _cent, _quantity, lot_rule, nominal_limit
from .execution_evidence import opening_capacity, audit_opening_fills


VERSION = "signal-target-daily-proxy-v1"
DEFAULT_HORIZONS = (5, 10, 20, 40, 60, 120, 250)
PROXIES = ("rawHigh30", "netHigh30", "nextOpenNet30")
HALTED = (False, 0, "0")


class _Execution:
    """Use the portfolio's actual rule/fee implementations without a scorer."""
    _bar = PortfolioEngine._bar
    _reference = PortfolioEngine._reference
    _member = PortfolioEngine._member
    _gate = PortfolioEngine._gate
    _fees = PortfolioEngine._fees
    _fill_price = PortfolioEngine._fill_price
    _affordable_quantity = PortfolioEngine._affordable_quantity

    def __init__(self, owner):
        for name in ("dataset", "config", "securities", "bars", "by_date", "calendar_index"):
            setattr(self, name, getattr(owner, name))
        self.state = {"quarantined": {}}


def _finite(value, label, minimum=0):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= minimum
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{label} must be finite and >= {minimum}")
    return float(value)


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Signal date must be YYYY-MM-DD")
    return value


def _wilson(successes, count):
    if not count:
        return None
    z = 1.959963984540054
    probability = successes / count
    scale = 1 + z * z / count
    center = (probability + z * z / (2 * count)) / scale
    radius = z * math.sqrt(probability * (1 - probability) / count + z * z / (4 * count * count)) / scale
    return [max(0., center - radius), min(1., center + radius)]


class SignalTargetStudy:
    """Prepare immutable dataset indexes once, then evaluate frozen signals.

    evaluate() accepts strategyId/symbol/signalDate plus optional signal metadata.
    The caller owns overlapping-event exclusion and chronological sample splits.
    """
    def __init__(self, dataset, config=None, *, budget=100000, target_pct=30):
        self.budget = _finite(budget, "Event budget", 1000)
        self.target_pct = _finite(target_pct, "Target percentage", .000001)
        self.config = normalize_config(config)
        self.dataset = dataset
        PortfolioEngine._validate_dataset(self, dataset)
        # Keep pre-signal sessions for execution references, but never label
        # outcomes beyond an explicitly frozen research cutoff.
        self.calendar = [day for day in self.all_dates if not self.config["endDate"] or day <= self.config["endDate"]]
        self.end_date = self.calendar[-1]
        self.calendar_independent = (self.calendar_supplied and dataset.get("calendarIndependent") is True
                                     and not dataset.get("calendarSource", "").startswith("observed"))

    def _observation(self, security, entry_date):
        dates = self.calendar[self.calendar_index[entry_date]:]
        bars = self.by_date[security["symbol"]]
        gaps, halts = [], []
        for index, day in enumerate(dates, 1):
            halted = security.get("tradeStatusByDate", {}).get(day) in HALTED
            if halted:
                halts.append(day)
            elif day not in bars:
                gaps.append({"date": day, "tradingDay": index})
        return {"availableTradingDays": len(dates), "firstDate": entry_date, "lastDate": self.end_date,
                "missingDates": gaps, "knownSuspensionDays": len(halts), "knownSuspensionDates": halts,
                "calendarIndependent": self.calendar_independent,
                "cohortDefinition": "allCalendarSessionsFromEntryThroughDatasetEndIncludingEntryAndSuspensions;independentOfOutcome"}

    def evaluate(self, signal):
        if not isinstance(signal, dict):
            raise ValueError("Signal must be an object")
        symbol, signal_day = signal.get("symbol"), _day(signal.get("signalDate"))
        if symbol not in self.securities or signal_day not in self.calendar_index:
            raise ValueError("Signal security/date is absent from the frozen dataset")
        if ((self.config["startDate"] and signal_day < self.config["startDate"])
                or signal_day > self.end_date):
            raise ValueError("Signal date is outside the configured study interval")
        if not isinstance(signal.get("strategyId"), str) or not signal["strategyId"]:
            raise ValueError("Signal must identify its frozen strategy")
        security = self.securities[symbol]
        context = _Execution(self)
        signal_bar, _ = context._bar(symbol, signal_day)
        event = {"version": VERSION, "signal": deepcopy(signal), "budget": self.budget,
                 "targetPct": self.target_pct, "entry": {"status": "invalid", "date": None},
                 "outcomes": {key: {"hitDate": None, "tradingDays": None, "calendarDays": None} for key in PROXIES},
                 "activeUntil": self.end_date, "observation": {"availableTradingDays": 0, "missingDates": []},
                 "excursions": None, "terminal": None,
                 "quality": {"targetAchieved": False, "executionVerified": False, "historicalIdentityVerified": False,
                             "corporateActionsVerified": False, "executionCapacityIssues": [], "unverifiedOpeningFills": [],
                             "rightsChangeDates": [], "blockedExitReasons": {}, "dataMissing": False,
                             "interpretation": "daily-bar development diagnostics; net price proxy without a corporate-action ledger"}}
        if signal_bar is None or signal_bar["volume"] <= 0:
            event["entry"]["reason"] = "missingOrNonTradingSignalBar"
            event["quality"]["dataMissing"] = signal_bar is None
            event["activeUntil"] = signal_day
            return event
        next_index = self.calendar_index[signal_day] + 1
        if next_index >= len(self.calendar):
            event["entry"].update(status="censored", reason="datasetEndsBeforeNextEntrySession")
            return event
        entry_day = self.calendar[next_index]
        event["entry"]["date"] = entry_day
        event["activeUntil"] = entry_day
        event["observation"] = self._observation(security, entry_day)
        minimum, increment = lot_rule(symbol)
        # The share count sees T's close/volume and scheduled rules only; no T+1
        # price or volume is allowed to enlarge the entry order.
        reserve_price = _cent(signal_bar["close"] * (1 + nominal_limit(symbol, entry_day))) * (1 + self.config["slippageBps"] / 10000)
        ceiling = _quantity(min(self.budget / reserve_price, signal_bar["volume"] * self.config["maxParticipationRate"]), minimum, increment)
        planned = context._affordable_quantity(ceiling, minimum, increment, reserve_price, self.budget, entry_day)
        event["entry"].update(plannedQuantity=planned, reservedPrice=reserve_price, maximumBudget=self.budget,
                              sizingDate=signal_day, sizingDefinition="signalCloseUpperLimitReservePlusSlippageAndBuyFees;signalDayVolumeCap")
        reason = context._gate(symbol, entry_day, "buy")
        price = context._fill_price(symbol, entry_day, "buy") if reason is None else None
        entry_bar, entry_index = context._bar(symbol, entry_day)
        if reason is None and price is None:
            reason = "slippageBeyondLimitOrUncertainST"
        if reason is None and price > reserve_price + 1e-8:
            reason = "openingPriceExceedsSignalReserve"
        quantity = _quantity(min(planned, opening_capacity(security, entry_day, signal_bar["volume"], self.config["maxParticipationRate"])), minimum, increment)
        if reason is None and not quantity:
            reason = "budgetOrOpeningCapacityBelowLotMinimum"
        if reason is not None:
            event["entry"].update(status="unfilled", reason=reason, quantity=0, retryPolicy="noDeferredEntry")
            event["quality"]["dataMissing"] = reason in ("missingBar", "missingPreviousTradingBar", "executionHistoryUnknown")
            return event
        fee = context._fees(quantity * price, "buy", entry_day)
        cost = quantity * price + fee
        if cost > self.budget + 1e-8:
            raise ValueError("Frozen entry order exceeds its reserved event budget")
        fill = {"date": entry_day, "symbol": symbol, "quantity": quantity}
        issues, unverified = audit_opening_fills([fill], {symbol: security}, {symbol: entry_bar})
        for issue in issues:
            issue["tradingDay"] = 1
        event["quality"]["executionCapacityIssues"].extend(issues)
        event["quality"]["unverifiedOpeningFills"].extend(unverified)
        event["entry"].update(status="filled", quantity=quantity, price=price, amount=quantity * price,
                              fee=fee, investedCapital=cost, remainingCash=self.budget - cost,
                              partial=quantity < planned, openingCapacityVerified=fill["openingCapacityVerified"],
                              executionValid=fill["executionValid"], retryPolicy="noDeferredEntry")
        event["activeUntil"] = self.end_date
        event["quality"]["dataMissing"] = bool(event["observation"]["missingDates"])
        mae = mfe = 0.
        last_close, last_mark = price, entry_day
        observed = 0
        exit_data = None

        def net_return(sell_price, day):
            proceeds = quantity * sell_price - context._fees(quantity * sell_price, "sell", day)
            return (proceeds / cost - 1) * 100, proceeds

        def touch(key, day, offset):
            if event["outcomes"][key]["hitDate"] is None:
                event["outcomes"][key].update(hitDate=day, tradingDays=offset,
                    calendarDays=(date.fromisoformat(day) - date.fromisoformat(entry_day)).days + 1)

        for offset, day in enumerate(self.calendar[next_index:], 1):
            bar, index = context._bar(symbol, day)
            if bar is None:
                continue
            observed += 1
            reference = context._reference(symbol, index)
            previous_close = self.bars[symbol][index - 1]["close"] if index else None
            if reference is not None and previous_close is not None and abs(reference - previous_close) > max(.005, previous_close * 1e-7):
                event["quality"]["rightsChangeDates"].append(day)
            halted = security.get("tradeStatusByDate", {}).get(day) in HALTED or bar["volume"] <= 0
            exit_reason = context._gate(symbol, day, "sell") if offset > 1 else "tPlusOne"
            if not halted and offset > 1 and exit_reason is None:
                exit_price = context._fill_price(symbol, day, "sell")
                candidate_return, proceeds = net_return(exit_price, day) if exit_price is not None else (-math.inf, 0)
                if candidate_return >= self.target_pct - 1e-10:
                    capacity = opening_capacity(security, day, self.bars[symbol][index - 1]["volume"], self.config["maxParticipationRate"])
                    if capacity >= quantity:
                        sale = {"date": day, "symbol": symbol, "quantity": quantity}
                        sale_issues, sale_unverified = audit_opening_fills([sale], {symbol: security}, {symbol: bar})
                        for issue in sale_issues:
                            issue["tradingDay"] = offset
                        event["quality"]["executionCapacityIssues"].extend(sale_issues)
                        event["quality"]["unverifiedOpeningFills"].extend(sale_unverified)
                        # The EOD audit annotates the causal opening proxy. It
                        # must not use later volume to change the exit date.
                        touch("nextOpenNet30", day, offset)
                        # A qualifying open also establishes both observations,
                        # without looking at any later high/low of the exit day.
                        if bar["open"] / price - 1 >= self.target_pct / 100 - 1e-12:
                            touch("rawHigh30", day, offset)
                        touch("netHigh30", day, offset)
                        excursion = (exit_price / price - 1) * 100
                        mae, mfe = min(mae, excursion), max(mfe, excursion)
                        exit_data = {"date": day, "price": exit_price, "fee": context._fees(quantity * exit_price, "sell", day),
                                     "proceeds": proceeds, "netReturnPct": candidate_return,
                                     "openingCapacityVerified": sale["openingCapacityVerified"]}
                        event["activeUntil"] = day
                        break
                    else:
                        exit_reason = "openingCapacityBelowFullPosition"
            if exit_reason and exit_reason != "tPlusOne":
                reasons = event["quality"]["blockedExitReasons"]
                reasons[exit_reason] = reasons.get(exit_reason, 0) + 1
            if not halted:
                mae = min(mae, (bar["low"] / price - 1) * 100)
                mfe = max(mfe, (bar["high"] / price - 1) * 100)
                if bar["high"] / price - 1 >= self.target_pct / 100 - 1e-12:
                    touch("rawHigh30", day, offset)
                if offset > 1:
                    high_return, _ = net_return(bar["high"] * (1 - self.config["slippageBps"] / 10000), day)
                    if high_return >= self.target_pct - 1e-10:
                        touch("netHigh30", day, offset)
                if index and abs(bar["close"] / reference - 1) > nominal_limit(symbol, day) + .005:
                    context.state["quarantined"].setdefault(symbol, day)
                last_close, last_mark = bar["close"], day
            elif security.get("tradeStatusByDate", {}).get(day) in HALTED:
                last_close, last_mark = bar["close"], day
        event["excursions"] = {"maePct": mae, "mfePct": mfe, "throughDate": event["activeUntil"],
            "observedBarDays": observed, "definition": "grossPriceRangeRelativeToEntryFill;entryDayIncluded;exitDayOpeningFillOnly;lowIsNotExecutableExit"}
        if exit_data:
            terminal_value = self.budget - cost + exit_data["proceeds"]
            event["terminal"] = {"status": "proxyExited", **exit_data, "eventEquity": terminal_value,
                                  "budgetReturnPct": (terminal_value / self.budget - 1) * 100}
        else:
            mark_net, liquidation = net_return(last_close * (1 - self.config["slippageBps"] / 10000), self.end_date)
            terminal_value = self.budget - cost + quantity * last_close
            event["terminal"] = {"status": "rightCensoredAtDatasetEnd", "date": self.end_date, "priceDate": last_mark,
                "markPrice": last_close, "eventEquity": terminal_value, "netMarkedReturnPct": (quantity * last_close / cost - 1) * 100,
                "hypotheticalLiquidationNetReturnPct": mark_net, "hypotheticalLiquidationProceeds": liquidation,
                "hypotheticalLiquidationIsExecutable": False, "budgetReturnPct": (terminal_value / self.budget - 1) * 100}
        event["quality"].update(quarantinedSymbols=sorted(context.state["quarantined"]),
            rightCensored=exit_data is None, firstTouchDatesAreOnlyObserved=event["quality"]["dataMissing"],
            identityEvidenceDeclared=bool(security.get("listingDate") and security.get("stByDate") and
                                          (security.get("memberHistory") or security.get("memberByDate") or self.dataset.get("membershipByDate"))),
            corporateActionsCompleteDeclared=security.get("corporateActionsComplete") is True,
            returnDefinition="priceOnlyNetOfConfiguredTradingCosts;NoCorporateActionCashOrShareLedger;UnusedCashExcludedFromPositionReturn")
        event["quality"]["dataMissing"] = any(row["date"] <= event["activeUntil"] for row in event["observation"]["missingDates"])
        event["quality"]["firstTouchDatesAreOnlyObserved"] = event["quality"]["dataMissing"]
        for key in PROXIES:
            event["outcomes"][key]["status"] = "observedProxyHit" if event["outcomes"][key]["hitDate"] else "rightCensored"
        return event


def summarize_events(events, horizons=DEFAULT_HORIZONS, precision_target=.8):
    """Same complete-follow-up entry cohort for every proxy and each horizon."""
    threshold = _finite(precision_target, "Precision target")
    if threshold > 1:
        raise ValueError("Precision target must be <= 1")
    if not isinstance(events, (list, tuple)):
        raise ValueError("Events must be a list")
    horizons = tuple(horizons)
    if any(type(value) is not int or value < 1 for value in horizons) or len(set(horizons)) != len(horizons):
        raise ValueError("Horizons must be distinct positive trading-session counts")
    status_counts, unfilled_reasons = {}, {}
    entered = []
    for event in events:
        status = event["entry"]["status"]
        status_counts[status] = status_counts.get(status, 0) + 1
        if status == "filled":
            entered.append(event)
        else:
            reason = event["entry"].get("reason", "unknown")
            unfilled_reasons[reason] = unfilled_reasons.get(reason, 0) + 1
    rows = []
    def eligible(event, horizon):
        stop = min(horizon, event["outcomes"]["nextOpenNet30"]["tradingDays"] or horizon)
        missing = any(row["tradingDay"] <= stop for row in event["observation"]["missingDates"])
        # Capacity audits are dated; use elapsed exchange-session offsets
        # supplied by each event, never let a later audit alter an earlier H.
        issues = any(row.get("tradingDay", 1) <= stop for row in event["quality"]["executionCapacityIssues"])
        return not missing and not issues and event["entry"].get("executionValid") is True
    for horizon in horizons:
        complete = [event for event in entered if event["observation"]["availableTradingDays"] >= horizon]
        cohort = [event for event in complete if eligible(event, horizon)]
        proxies = {}
        for key in PROXIES:
            hits = [event["outcomes"][key]["tradingDays"] for event in cohort
                    if event["outcomes"][key]["tradingDays"] is not None and event["outcomes"][key]["tradingDays"] <= horizon]
            rate = len(hits) / len(cohort) if cohort else None
            unresolved = len(complete) - len(cohort)
            lower = len(hits) / len(complete) if complete else None
            upper = (len(hits) + unresolved) / len(complete) if complete else None
            proxies[key] = {"hits": len(hits), "denominator": len(cohort), "hitRate": rate,
                            "wilson95": _wilson(len(hits), len(cohort)), "medianHitTradingDays": median(hits) if hits else None,
                            "hitRateDefinition": "conditionalOnValidDataAndExecutionAudits;notAnAcceptanceCriterion",
                            "missingOutcomeBounds": {"denominator": len(complete), "unresolved": unresolved,
                                "lower": lower, "upper": upper, "interpretation": "missingOutcomeBoundsNotConfidenceInterval"},
                            "proxyPrecisionThresholdReached": rate is not None and rate >= threshold,
                            "conservativeMissingOutcomeLowerBoundReachesThreshold": lower is not None and lower >= threshold}
        rows.append({"tradingDays": horizon, "completeCalendarCohort": len(complete), "eligibleEntryCohort": len(cohort),
                     "rightCensoredBeforeHorizon": len(entered) - len(complete), "missingOrInvalidDataExcluded": len(complete) - len(cohort),
                     "proxies": proxies})
    maes = [event["excursions"]["maePct"] for event in entered if event.get("excursions")]
    excursion_summary = {"count": len(maes), "medianMaePct": median(maes) if maes else None,
        "worstMaePct": min(maes) if maes else None,
        "atOrBelow": [{"maePct": cut, "count": sum(value <= cut for value in maes),
                       "fraction": sum(value <= cut for value in maes) / len(maes) if maes else None}
                      for cut in (-5, -10, -20, -30)],
        "definition": "allFilledProxyEvents;grossPriceMAEUntilMainOpenExitOrCutoff;variableFollowup;lowIsNotExecutableExit"}
    return {"version": VERSION, "signalCount": len(events), "entryStatusCounts": status_counts,
            "entryUnfilledReasons": unfilled_reasons, "filledEntryCount": len(entered),
            "entryFillRate": len(entered) / len(events) if events else None, "precisionTarget": threshold,
            "horizons": rows, "excursions": excursion_summary, "targetAchieved": False, "executionVerified": False,
            "rightCensoredEventCount": sum(event["outcomes"]["nextOpenNet30"]["hitDate"] is None for event in entered),
            "dataMissingEventCount": sum(bool(event["quality"]["dataMissing"]) for event in events),
            "definitions": {"horizon": "entryIncludedExchangeSessions;noFixedExpiry;datasetEndRightCensoring",
                "cohort": "sameCompletePotentialFollowupCohortForAllThreeLabels;earlyHitsDoNotAdmitRecentEvents;qualityOnlyThroughMinHorizonAndMainExit;postExitDataDoesNotExcludeEvents",
                "rawHigh30": "rawHighRelativeToEntryFillIncludingEntryDay;observationOnly",
                "netHigh30": "sellableDaysHighLessSlippageAndFees;OHLCProxyNotFillEvidence",
                "nextOpenNet30": "predeclaredProfitTarget;fullPositionOnly;nextSellableOpenPassesPortfolioGatesAndCapacity;priceOnlyNetProxy",
                "probability": "descriptiveEventFraction;WilsonIntervalAssumesIndependentEventsAndIsNotClusterRobust",
                "notCertification": "unknownHistoricalIdentityRightsAndOpeningEvidenceRemainUnverified;callerOwnsSampleSplitAndOverlapControl"}}
