"""Static daily-flow copy; generated substitutions independently checked in tests."""
from copy import deepcopy
from .portfolio_engine import nominal_limit
from .execution_evidence import audit_opening_fills
from .portfolio_rotation import decide_rotation

def action_step_day(self, day):
    s = self.state
    decision_start, fill_start = len(s["decisions"]), len(s["trades"])
    action_start = len(s["actionLedger"])
    self._apply_before_open(day)
    self._observe_actions(day)
    self._execute_target(day)
    # Whole-day volume is only an ex-post consistency check. It never
    # participates in opening quantities, priority or available cash.
    fills = s["trades"][fill_start:]
    issues, unverified = audit_opening_fills(fills, self.securities,
        {fill["symbol"]: self._bar(fill["symbol"], day)[0] for fill in fills})
    s["executionCapacityIssues"].extend(issues)
    s["unverifiedOpeningFills"].extend(unverified)
    if issues:
        self._warning("存在超过当日成交总量的模拟成交或矛盾的开盘量证据；本次账户路径无效，不参与平原选择或认证。")
    stale = []
    known_halts = []
    for symbol in sorted(self.securities):
        bar, index = self._bar(symbol, day)
        if bar is not None and index and bar["volume"] > 0 and abs(bar["close"] / self._continuity_reference(symbol, index) - 1) > nominal_limit(symbol, day) + .005:
            s["quarantined"].setdefault(symbol, day)
            self._decision(day, symbol, "dataQuality", "quarantined", "rawCloseDiscontinuity")
            self._warning(f"{symbol} 存在无法解释的原始价格断点，后续成交隔离；相关净值不可用于有效性结论。")
    for symbol in sorted(s["positions"]):
        bar, _ = self._bar(symbol, day)
        known_suspension = (bar is not None and self.dataset.get("dataQuality", {}).get("certified") is True
                            and self.securities[symbol].get("tradeStatusByDate", {}).get(day) in (False, 0, "0"))
        if bar is not None and (bar["volume"] > 0 or known_suspension):
            s["lastPrices"][symbol], s["lastPriceDates"][symbol] = bar["close"], day
            if known_suspension and bar["volume"] == 0:
                known_halts.append(symbol)
                self._decision(day, symbol, "valuation", "held", "declaredHistoricalSuspensionMark")
        else:
            stale.append(symbol)
            self._decision(day, symbol, "valuation", "stale", "missingOrSuspendedBar", priceDate=s["lastPriceDates"].get(symbol))
    market_value = sum(position["quantity"] * s["lastPrices"][symbol] for symbol, position in s["positions"].items())
    equity = s["cash"] + market_value + self._action_asset_adjustment()
    s["observedBarCount"] += self.history_counts.get(day, 0)
    s["observedStCount"] += self.st_counts.get(day, 0)
    previous_equity = s["curve"][-1]["equity"] if s["curve"] else self.config["initialCapital"]
    s["peakEquity"] = max(s["peakEquity"], equity)
    benchmark_close = self.benchmark_by_date.get(day)
    if benchmark_close is not None:
        if s["benchmarkFirstClose"] is None:
            s["benchmarkFirstClose"], s["benchmarkFirstDate"] = benchmark_close, day
        s["benchmarkLastClose"], s["benchmarkLastDate"] = benchmark_close, day
    benchmark_equity = self.config["initialCapital"] * s["benchmarkLastClose"] / s["benchmarkFirstClose"] if s["benchmarkFirstClose"] else None
    curve = {"date": day, "equity": equity, "cash": s["cash"], "marketValue": market_value,
             "dailyReturn": equity / previous_equity - 1 if previous_equity else None,
             "drawdownPct": (1 - equity / s["peakEquity"]) * 100 if s["peakEquity"] else 0.,
             "staleSymbols": stale, "valuationReliable": not stale and not s["unaccountedActionExposure"] and not s["executionCapacityIssues"] and not any(symbol in s["quarantined"] for symbol in s["positions"]),
             "knownSuspendedSymbols": known_halts,
             "benchmarkEquity": benchmark_equity, "benchmarkStale": bool(self.benchmark_by_date) and s["benchmarkLastDate"] != day,
             "regimeForReturn": deepcopy(s["daily"][-1]["regime"]) if s["daily"] else {"status": "unknown", "reason": "noPriorCloseObservation"}}
    s["curve"].append(curve)
    self._advance_shadows(day)
    scored = self.scorer.daily(day)
    if not isinstance(scored, dict) or not isinstance(scored.get("strategies"), dict):
        raise ValueError("DailyScorer 返回结构无效")
    self._risk_exits(day, scored.get("eligibilityBySymbol", {}))
    rotation_decision = None
    rotation_changed = False
    if self.rotation_enabled:
        rotation_decision = decide_rotation(day, self.calendar_index[day], self.config["allocation"]["mode"], self.config["rotation"],
                                            s["rotationWeights"], s["rotationLastAdjustment"],
                                            {key: shadow.state["curve"] for key, shadow in self.shadows.items()}, scored.get("regime"))
        rotation_changed = rotation_decision["status"] == "adjusted"
        if isinstance(scored.get("regime"), dict):
            used = any(item["regimeBlendApplied"] > 0 for item in rotation_decision["scores"].values())
            scored["regime"].update(eligibleForRotation=self.config["rotation"]["regimeBlend"] > 0,
                                    usedForRotationScoring=used, affectsWeights=used and rotation_changed)
            rotation_decision["regime"] = deepcopy(scored["regime"])
        if rotation_changed:
            s["rotationWeights"] = deepcopy(rotation_decision["after"])
            s["rotationLastAdjustment"] = self.calendar_index[day]
            next_index = self.calendar_index[day] + 1
            rotation_decision["earliestExecutionDate"] = self.all_dates[next_index] if next_index < len(self.all_dates) else None
            s["rotationLog"].append(deepcopy(rotation_decision))
    rebalance = s["cursor"] % self.config["rebalanceEvery"] == 0
    strategy_weights = {identifier: 1. / len(self.config["allocation"]["strategyIds"]) for identifier in self.config["allocation"]["strategyIds"]}
    selected, weights, strategy_weights, details, clipped, priorities = self._select_targets(scored, self._excluded_today(day) | set(s["quarantined"]))
    raw_top_n = {identifier: [{**deepcopy(item), "rank": rank + 1} for rank, item in enumerate(
        sorted([row for row in rows if row.get("eligible", True)], key=lambda row: (-row["score"], row["symbol"]))[:self.config["topN"]])]
                 for identifier, rows in scored["strategies"].items()}
    prior = s["pendingTarget"]
    outstanding_rotation = bool(prior and prior.get("reason") == "strategyRotation" and any(
        symbol not in s["forcedExits"] and (symbol not in prior["quantities"] or prior["quantities"][symbol] != s["positions"].get(symbol, {}).get("quantity", 0))
        for symbol in set(prior["weights"]) | set(s["positions"])))
    queue_target = rebalance or bool(s["forcedExits"]) or rotation_changed
    if self.config["rebalanceMode"] == "risk" and outstanding_rotation and not rotation_changed:
        # Retain blocked/partial rotation orders until executable. Risk
        # events still liquidate independently through forcedExits.
        queue_target = False
    if queue_target:
        quantities = {}
        carry_budgets, carry_dates = {}, {}
        if self.config["rebalanceMode"] == "risk" and not rotation_changed:
            weights, quantities, carry_budgets, carry_dates, priorities = self._risk_targets(weights, priorities, equity, day)
        elif not rebalance and not rotation_changed and s["pendingTarget"]:
            # Between rank dates, risk can remove names but cannot trigger
            # an unrequested fresh cross-sectional rebalance.
            weights = {symbol: weight for symbol, weight in s["pendingTarget"]["weights"].items() if symbol not in s["forcedExits"]}
            quantities = {symbol: count for symbol, count in s["pendingTarget"]["quantities"].items() if symbol not in s["forcedExits"]}
            priorities = {symbol: deepcopy(s["pendingTarget"]["priorityBySymbol"][symbol]) for symbol in weights}
        if s["pendingTarget"]:
            for symbol, quantity in s["pendingTarget"]["quantities"].items():
                remaining = quantity - s["positions"].get(symbol, {}).get("quantity", 0)
                carried = symbol in carry_dates and quantities.get(symbol) == quantity
                if remaining and not carried:
                    self._decision(day, symbol, "rebalance", "superseded", "newCloseTarget", unfilledQuantity=abs(remaining))
        s["selectedByStrategy"] = selected
        s["pendingTarget"] = {"signalDate": day, "signalEquity": equity, "weights": weights, "quantities": quantities,
                              "reason": "strategyRotation" if rotation_changed else "targetRebalance",
                              "priorityBySymbol": priorities,
                              "budgets": {**{symbol: weight * equity for symbol, weight in weights.items()}, **carry_budgets},
                              "signalDates": {**{symbol: day for symbol in weights}, **carry_dates}}
        for symbol in sorted(set(weights) | set(s["positions"])):
            self._decision(day, symbol, "target", "queued", "strategyRotation" if rotation_changed else "scheduledRebalance", targetWeight=weights.get(symbol, 0.), signalEquity=equity)
    pending = s["pendingTarget"]
    holdings = self._daily_holdings(day, equity)
    s["daily"].append({"date": day, "rebalance": rebalance, "topNByStrategy": raw_top_n, "selectedCandidatesByStrategy": details,
                       "rotationDecision": rotation_decision,
                       "selectedByStrategy": deepcopy(s["selectedByStrategy"]), "strategyWeights": strategy_weights,
                       "strategyBudgetWeights": strategy_weights, "strategyWeightMeaning": "nextOpenTargetBudgetNotRealizedHoldingAttribution" if self.rotation_enabled else "fixedEntryBudgetNotRealizedHoldingAttribution",
                       "targetWeights": deepcopy(pending["weights"] if pending else {}), "targetSignalDate": pending["signalDate"] if pending else None,
                       "targetPriority": self._priority_order(pending["weights"], pending["priorityBySymbol"]) if pending else [],
                       "targetPriorityBySymbol": deepcopy(pending["priorityBySymbol"] if pending else {}),
                       "holdings": holdings, "actualStockWeights": {row["symbol"]: row["weight"] for row in holdings},
                       "positionControl": self._position_control(day, curve, rebalance),
                       "nextSessionPlan": self._next_session_plan(day, holdings),
                       "coverage": deepcopy(scored.get("coverage", {})),
                       "poolCounts": {key: len(values) for key, values in scored.get("pools", {}).items()},
                       "regime": deepcopy(scored.get("regime")), "clippedTargets": clipped,
                       "activities": deepcopy(s["decisions"][decision_start:]), "fills": deepcopy(s["trades"][fill_start:]),
                       "targetSelectionPolicy": "aggregateBudgetBeforeCap;strategyBudgetWeightedScore;symbolTieBreak;maxPositionsTruncation;noRedistribution",
                       "cash": s["cash"], "equity": equity})
    self._capture_record_close(day)
    s["daily"][-1]["corporateActions"] = deepcopy(s["actionLedger"][action_start:])
