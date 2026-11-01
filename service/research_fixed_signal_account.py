"""Isolated, uncertified shared account for a frozen close-time opportunity stream.

No strategy is scored here. The inherited engine owns dated rights, valuation,
close-triggered risk exits and order arithmetic. Only entry scheduling/sizing and
the explicit fee seams differ. Percentage exits are price triggers, not promised
net returns; maxHoldingDays uses calendar sessions (entry session is one), with
the inherited trigger deferred on missing/nonpositive-volume bars.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .portfolio_config import normalize_config
from .portfolio_engine import PortfolioEngine, _day, _digest, _finite, _quantity, lot_rule
from .execution_evidence import opening_capacity
from .research_account_fees import AccountFeeLedger, OrderFeeContext, LegacyFeePolicy, DatedFeePolicy

VERSION = "fixed-signal-account-v1"
CHECKPOINT_SCHEMA = "fixed-signal-account-checkpoint-v1"
ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "docs/fixed-signal-account-contract-v1.json"
SIGNAL_FIELDS = {"signalId", "strategyId", "symbol", "signalDate", "originalRank"}
SOURCE_FILES = (CONTRACT, "service/research_fixed_signal_account.py", "service/research_account_fees.py",
                "service/research_fee_profile.py", "service/portfolio_engine.py", "service/portfolio_config.py",
                "service/portfolio_signals.py", "service/portfolio_actions.py", "service/portfolio_rotation.py",
                "service/execution_evidence.py")


def _source_pins():
    pins = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES}
    frozen = json.loads((ROOT / CONTRACT).read_text(encoding="utf-8"))["isolation"]
    for name, key in (("service/portfolio_engine.py", "existingEngineSha256"),
                      ("service/portfolio_config.py", "existingConfigSha256"),
                      ("service/research_fee_profile.py", "existingResearchFeeSha256")):
        if pins[name] != frozen[key]:
            raise ValueError("Frozen inherited source changed: " + name)
    return pins


class FrozenSignalScorer:
    """Only exact-date input rows; the score token preserves rank for old reports."""

    def __init__(self, signals, strategy_id):
        self.strategy_id = strategy_id
        self.by_day = {}
        for row in signals:
            self.by_day.setdefault(row["signalDate"], []).append(deepcopy(row))

    def daily(self, day):
        rows = [{**deepcopy(row), "rank": row["originalRank"],
                 "score": (6 - row["originalRank"]) / 5, "rawScore": None,
                 "eligible": True, "scoreMeaning": "originalRankOrderingTokenOnly"}
                for row in self.by_day.get(day, [])]
        return {"date": day, "version": VERSION, "strategies": {self.strategy_id: rows},
                "eligibilityBySymbol": {}, "pools": {}, "regime": None,
                "coverage": {"source": "frozenOpportunitySchedule", "signalCount": len(rows),
                             "pointInTimeComplete": False, "certified": False}}


class FixedSignalAccount(PortfolioEngine):
    def __init__(self, dataset, signals, config, *, entry_weight, fee_policy, state=None):
        if not isinstance(config, dict) or not config.get("startDate") or not config.get("endDate"):
            raise ValueError("Explicit startDate/endDate are required")
        explicit = {"stopLossPct", "trailingStopPct", "takeProfitPct", "maxHoldingDays", "maxStockWeight", "initialCapital", "maxPositions"}
        if not explicit.issubset(config):
            raise ValueError("Explicit exits and account limits required; zero disables an exit")
        settings = normalize_config(config)
        if (len(settings["strategies"]) != 1 or settings["combinations"] or
                settings["allocation"]["mode"] != "single" or
                settings["allocation"]["strategyIds"] != [settings["strategies"][0]["id"]] or
                settings["rebalanceMode"] != "risk" or settings["rebalanceEvery"] != 1):
            raise ValueError("One frozen strategy, risk mode and daily entry processing required; rotation disabled")
        if type(fee_policy) not in (LegacyFeePolicy, DatedFeePolicy):
            raise ValueError("An explicit supported research fee policy is required")
        self.entry_weight = _finite(entry_weight, "entry_weight", 0, 1)
        if not self.entry_weight:
            raise ValueError("entry_weight must be positive")
        if (not isinstance(dataset, dict) or not isinstance(dataset.get("calendar"), list) or
                not dataset["calendar"] or dataset.get("calendarSource", "").startswith("observed")):
            raise ValueError("An explicit independent calendar is required")
        prepared = object.__new__(PortfolioEngine)
        prepared.config, prepared.dataset = settings, dataset
        PortfolioEngine._validate_dataset(prepared, dataset)
        if not prepared.dates:
            raise ValueError("No calendar sessions in explicit interval")
        prepared.data_digest = _digest(dataset)
        prepared.fingerprint = str(dataset.get("fingerprint") or prepared.data_digest)
        self.strategy_id = settings["strategies"][0]["id"]
        schedule = self._validate_signals(signals, prepared)
        prepared.scorer = FrozenSignalScorer(schedule, self.strategy_id)
        # The score-related configuration is identical, so the shared path never
        # constructs DailyScorer (nor any rotation/shadow account).
        super().__init__(dataset, settings, _shared=prepared)
        self._schedule = schedule
        for symbol in self.securities:
            fee_policy.validate_event(self.config, symbol, self.config["startDate"], self.config["endDate"])
        self.research_state = {"opportunities": [{**deepcopy(row), "status": "notObserved",
                                "reason": None, "nextSession": None, "budget": None,
                                "plannedQuantity": None, "reservePrice": None, "reserveQuoteId": None,
                                "attemptDate": None, "filledQuantity": 0, "unfilledQuantity": None,
                                "fillId": None} for row in schedule], "closePlan": []}
        self.fee_ledger = AccountFeeLedger(self, fee_policy)
        self.identity = {"adapterVersion": VERSION, "config": deepcopy(self.config),
                         "signalSchedule": deepcopy(schedule), "signalScheduleSha256": _digest(schedule),
                         "entryWeight": self.entry_weight,
                         "entryPolicy": "TcloseBudgetAndQuantity;nextIndependentSessionOnly;originalRank;noAdd;noBackfill",
                         "feePolicy": deepcopy(self.fee_ledger.checkpoint()["identity"]),
                         "datasetDigest": self.data_digest, "datasetFingerprint": self.fingerprint,
                         "validatedIndexesDigest": self._indexes_digest(),
                         "sourcePins": _source_pins()}
        if state is not None:
            self._restore_envelope(state)

    def _validate_signals(self, signals, prepared):
        if not isinstance(signals, list):
            raise ValueError("signals must be a complete list")
        ids, dates_symbols, dates_ranks = set(), set(), set()
        for row in signals:
            if not isinstance(row, dict) or set(row) != SIGNAL_FIELDS:
                raise ValueError("Unsupported or incomplete frozen signal fields")
            if not isinstance(row["signalId"], str) or not row["signalId"].strip() or row["signalId"] in ids:
                raise ValueError("Signal IDs must be nonempty and unique")
            if row["strategyId"] != self.strategy_id or row["symbol"] not in prepared.securities:
                raise ValueError("Signal strategy/symbol differs from the declared account")
            day = _day(row["signalDate"])
            rank = row["originalRank"]
            if day not in prepared.dates or type(rank) is not int or not 1 <= rank <= 5:
                raise ValueError("Signal date/rank outside the explicit interval or Top5")
            if (day, row["symbol"]) in dates_symbols or (day, rank) in dates_ranks:
                raise ValueError("Duplicate signal date/symbol or date/rank")
            ids.add(row["signalId"])
            dates_symbols.add((day, row["symbol"]))
            dates_ranks.add((day, rank))
        return sorted(deepcopy(signals), key=lambda row: (row["signalDate"], row["originalRank"], row["symbol"]))

    def _indexes_digest(self):
        return _digest({key: getattr(self, key) for key in (
            "by_date", "calendar_index", "dates", "benchmark_by_date", "action_schedule", "history_counts", "st_counts")})

    def _identity_stable(self):
        observed_schedule = [{key: row.get(key) for key in SIGNAL_FIELDS}
                             for row in self.research_state["opportunities"]]
        expected_scorer = FrozenSignalScorer(self.identity["signalSchedule"], self.strategy_id)
        if (self.config != self.identity["config"] or _source_pins() != self.identity["sourcePins"] or
                self.entry_weight != self.identity["entryWeight"] or
                _digest(self._schedule) != self.identity["signalScheduleSha256"] or
                observed_schedule != self.identity["signalSchedule"] or
                self.scorer.by_day != expected_scorer.by_day or self.scorer.strategy_id != self.strategy_id or
                self.strategy_id != self.identity["config"]["strategies"][0]["id"] or
                _digest(self.dataset) != self.identity["datasetDigest"] or
                self.data_digest != self.identity["datasetDigest"] or self.fingerprint != self.identity["datasetFingerprint"] or
                self._indexes_digest() != self.identity["validatedIndexesDigest"] or
                self.all_dates is not self.dataset["calendar"] or
                set(self.securities) != {security["symbol"] for security in self.dataset["securities"]} or
                set(self.bars) != set(self.securities) or
                any(self.securities.get(security["symbol"]) is not security or
                    self.bars.get(security["symbol"]) is not security["bars"] for security in self.dataset["securities"]) or
                self.fee_ledger.identity != self.identity["feePolicy"]):
            raise ValueError("Account config, source or fee policy changed")

    def checkpoint(self):
        self._identity_stable()
        self._validate_research_state()
        envelope = {"schema": CHECKPOINT_SCHEMA, "adapterVersion": VERSION,
                    "identity": deepcopy(self.identity), "engineState": super().checkpoint(),
                    "researchState": deepcopy(self.research_state), "feeState": self.fee_ledger.checkpoint()}
        envelope["digest"] = _digest(envelope)
        return envelope

    def _restore_envelope(self, envelope):
        keys = {"schema", "adapterVersion", "identity", "engineState", "researchState", "feeState", "digest"}
        if (not isinstance(envelope, dict) or set(envelope) != keys or envelope["schema"] != CHECKPOINT_SCHEMA or
                envelope["adapterVersion"] != VERSION or envelope["identity"] != self.identity or
                _digest({key: value for key, value in envelope.items() if key != "digest"}) != envelope["digest"]):
            raise ValueError("Incompatible or altered fixed-signal checkpoint")
        research = envelope["researchState"]
        if (not isinstance(research, dict) or set(research) != {"opportunities", "closePlan"} or
                [{key: row[key] for key in SIGNAL_FIELDS} for row in research["opportunities"]] != self._schedule):
            raise ValueError("Checkpoint opportunity schedule differs")
        super()._restore(envelope["engineState"])
        self.research_state = deepcopy(research)
        self._validate_research_state()
        self.fee_ledger.restore(envelope["feeState"])

    def _validate_research_state(self):
        """Reject inconsistent portable sidecars, including rehashed mistakes."""
        rows = self.research_state["opportunities"]
        allowed = {"notObserved", "notPlanned", "planned", "filled", "partial", "expired", "censored"}
        last = self.dates[self.state["cursor"] - 1] if self.state["cursor"] else None
        linked = set()
        for row in rows:
            status = row.get("status")
            if status not in allowed or (status == "notObserved") != (last is None or row["signalDate"] > last):
                raise ValueError("Opportunity status/cursor inconsistent")
            if type(row.get("filledQuantity")) is not int or row["filledQuantity"] < 0:
                raise ValueError("Invalid opportunity fill quantity")
            for field in ("budget", "reservePrice"):
                if row.get(field) is not None:
                    _finite(row[field], field)
            planned, unfilled, filled = row.get("plannedQuantity"), row.get("unfilledQuantity"), row["filledQuantity"]
            if planned is None:
                if unfilled is not None or filled or row.get("fillId") is not None:
                    raise ValueError("Unplanned opportunity has fills")
            elif (type(planned) is not int or type(unfilled) is not int or min(planned, unfilled) < 0 or
                  filled + unfilled != planned):
                raise ValueError("Opportunity quantity conservation failed")
            if status != "notObserved" and row.get("nextSession") != self._next_session(row["signalDate"]):
                raise ValueError("Opportunity next session changed")
            attempted = status in ("filled", "partial", "expired")
            if attempted:
                if row.get("attemptDate") != row.get("nextSession") or row["attemptDate"] > last or not planned:
                    raise ValueError("Opportunity attempt timing invalid")
            elif row.get("attemptDate") is not None or filled or row.get("fillId") is not None:
                raise ValueError("Unattempted opportunity has execution state")
            if status == "filled" and (not filled or unfilled != 0) or status == "partial" and (not filled or not unfilled):
                raise ValueError("Opportunity terminal quantity inconsistent")
            if status == "expired" and filled:
                raise ValueError("Expired opportunity has a fill")
            if status == "planned" and (not planned or row["nextSession"] is None or row["nextSession"] <= last or row["nextSession"] > self.dates[-1]):
                raise ValueError("Stale or out-of-window pending opportunity")
            if status == "censored" and row["nextSession"] is not None and row["nextSession"] <= self.dates[-1]:
                raise ValueError("Censored opportunity had an in-study next session")
            if filled:
                trades = [trade for trade in self.state["trades"] if trade.get("fillId") == row.get("fillId")]
                if len(trades) != 1:
                    raise ValueError("Opportunity fill linkage missing")
                trade = trades[0]
                expected = {"side": "buy", "signalId": row["signalId"], "symbol": row["symbol"],
                            "signalDate": row["signalDate"], "date": row["attemptDate"],
                            "originalRank": row["originalRank"], "quantity": filled}
                if any(trade.get(key) != value for key, value in expected.items()) or row["fillId"] in linked:
                    raise ValueError("Opportunity fill linkage differs")
                linked.add(row["fillId"])
        if {trade.get("fillId") for trade in self.state["trades"] if trade["side"] == "buy"} != linked:
            raise ValueError("Buy fill lacks a frozen opportunity")
        if self.research_state["closePlan"] != [row["signalId"] for row in rows if row["status"] == "planned"]:
            raise ValueError("Close plan differs from pending opportunities")
        pending = self.state["pendingTarget"]
        for row in rows:
            if row["status"] == "planned":
                symbol = row["symbol"]
                if (pending is None or pending["signalDate"] != row["signalDate"] or
                        pending["quantities"].get(symbol) != row["plannedQuantity"] or
                        pending["budgets"].get(symbol) != row["budget"] or
                        pending["priorityBySymbol"].get(symbol, {}).get("originalRank") != row["originalRank"]):
                    raise ValueError("Pending target differs from the frozen close opportunity")

    def _transaction_snapshot(self):
        return (super()._transaction_snapshot(), deepcopy(self.research_state), self.fee_ledger.transaction_snapshot())

    def _transaction_restore(self, snapshot):
        engine, research, fee = snapshot
        super()._transaction_restore(engine)
        self.research_state = research
        self.fee_ledger.transaction_restore(fee)

    def step(self, days=5):
        self._identity_stable()
        self._validate_research_state()
        return super().step(days)

    def _fees(self, amount, side, day):
        raise RuntimeError("Research fees require an explicit OrderFeeContext")

    @staticmethod
    def _context(symbol, signal_day, day, purpose):
        return OrderFeeContext(symbol, signal_day, day, day, day if purpose == "fillQuote" else None, purpose)

    def _opportunities(self, day):
        return [row for row in self.research_state["opportunities"] if row["signalDate"] == day]

    def _close_exclusion(self, symbol, day, excluded):
        if symbol in self.state["forcedExits"]:
            return "pendingForcedExit"
        if symbol in self.state["positions"]:
            return "alreadyHeldNoAdd"
        if symbol in excluded:
            return "soldTodayOrQuarantined"
        security = self.securities[symbol]
        if self._member(symbol, day) is False:
            return "notHistoricalMember"
        if security.get("delistDate") and day >= security["delistDate"]:
            return "delisted"
        if security.get("listingDate") and day < security["listingDate"]:
            return "notListed"
        if self.config["universe"]["excludeSt"] and security.get("stByDate", {}).get(day) is True:
            return "historicalST"
        bar, _ = self._bar(symbol, day)
        status = security.get("tradeStatusByDate", {}).get(day)
        if bar is None or bar["volume"] <= 0 or status in (False, 0, "0"):
            return "missingOrSuspendedSignalClose"
        if self.config["universe"]["strictHistory"] and (status is None or day not in security.get("stByDate", {}) or
                self._member(symbol, day) is None or not security.get("listingDate")):
            return "signalHistoryUnknown"
        return None

    def _select_targets(self, scored, excluded=None):
        day = scored["date"]
        equity = self.state["curve"][-1]["equity"]
        weight = min(self.entry_weight, self.config["maxStockWeight"])
        free = max(0, self.config["maxPositions"] - sum(symbol not in self.state["forcedExits"] for symbol in self.state["positions"]))
        rows, chosen, weights, priorities = self._opportunities(day), [], {}, {}
        self.research_state["closePlan"] = []
        for row in rows:
            if row["status"] != "notObserved":
                raise ValueError("Signal close processed twice")
            symbol = row["symbol"]
            row["nextSession"] = self._next_session(day)
            reason = self._close_exclusion(symbol, day, excluded or set())
            if reason is None and not free:
                reason = "plannedSlotsOccupied"
            if reason is not None:
                row.update(status="notPlanned", reason=reason)
                continue
            # A selected slot is consumed even if its budget cannot buy one lot.
            free -= 1
            bar, _ = self._bar(symbol, day)
            price = bar["close"] * (1 + self.config["slippageBps"] / 10000)
            budget = weight * equity
            minimum, increment = lot_rule(symbol)
            context = self._context(symbol, day, day, "signalReserve")
            ceiling = _quantity(budget / price, minimum, increment)
            quantity = self.fee_ledger.affordable_quantity(ceiling, minimum, increment, price, budget, context)
            quote = self.fee_ledger.quote(quantity * price, "buy", context)
            row.update(budget=budget, reservePrice=price, plannedQuantity=quantity,
                       reserveQuoteId=quote["quoteId"], unfilledQuantity=quantity)
            if row["nextSession"] is None or row["nextSession"] > self.dates[-1]:
                row.update(status="censored", reason="nextSessionOutsideStudy")
            elif not quantity:
                row.update(status="notPlanned", reason="budgetBelowLotMinimum")
            else:
                row.update(status="planned", reason="nextSessionOnly")
                chosen.append(symbol)
                self.research_state["closePlan"].append(row["signalId"])
                weights[symbol] = weight
                priorities[symbol] = {"budgetWeight": weight, "weightedScore": (6 - row["originalRank"]) / 5,
                                      "originalRank": row["originalRank"], "signalId": row["signalId"]}
        details = [deepcopy(row) for row in rows if row["symbol"] in chosen]
        return {self.strategy_id: chosen}, weights, {self.strategy_id: 1.}, {self.strategy_id: details}, [], priorities

    def _risk_targets(self, weights, priorities, equity, day):
        retained = {symbol: position["quantity"] for symbol, position in self.state["positions"].items()
                    if symbol not in self.state["forcedExits"]}
        target_weights = {symbol: quantity * self.state["lastPrices"][symbol] / equity if equity else 0.
                          for symbol, quantity in retained.items()}
        quantities, budgets, dates = dict(retained), {}, {}
        for symbol in retained:
            priorities[symbol] = {"budgetWeight": min(1., max(0., target_weights[symbol])), "weightedScore": 0.}
        for row in self._opportunities(day):
            if row["status"] == "planned":
                symbol = row["symbol"]
                target_weights[symbol] = weights[symbol]
                quantities[symbol], budgets[symbol], dates[symbol] = row["plannedQuantity"], row["budget"], day
        return target_weights, quantities, budgets, dates, priorities

    @staticmethod
    def _priority_order(symbols, priorities):
        return sorted(symbols, key=lambda symbol: (priorities.get(symbol, {}).get("originalRank", 999), symbol))

    def _execute_target(self, day):
        pending = self.state["pendingTarget"]
        self.state["pendingTarget"] = None
        # No generic pending buy/target exists during this call: only persistent
        # forced exits use the inherited deterministic sell loop.
        super()._execute_target(day)
        if pending is None:
            return
        for row in self._opportunities(pending["signalDate"]):
            if row["status"] != "planned":
                continue
            symbol = row["symbol"]
            row["attemptDate"] = day
            reason = None
            if day != row["nextSession"]:
                reason = "immediateNextSessionMissed"
            elif symbol in self.state["positions"]:
                reason = "alreadyHeldNoAdd"
            elif symbol in self._excluded_today(day):
                reason = "pendingExitOrSoldToday"
            before, decision_start = len(self.state["trades"]), len(self.state["decisions"])
            if reason is None:
                self._execute_order(day, symbol, "buy", row["plannedQuantity"],
                                    {**pending, "signalDate": row["signalDate"], "signalId": row["signalId"],
                                     "originalRank": row["originalRank"], "reason": "fixedSignalEntry"})
            fills = self.state["trades"][before:]
            if fills:
                fill = fills[0]
                row.update(status="filled" if fill["quantity"] == row["plannedQuantity"] else "partial",
                           reason="filled" if fill["quantity"] == row["plannedQuantity"] else "partialRemainderExpired",
                           filledQuantity=fill["quantity"], unfilledQuantity=row["plannedQuantity"] - fill["quantity"],
                           fillId=fill["fillId"])
            else:
                decisions = self.state["decisions"][decision_start:]
                row.update(status="expired", reason=reason or (decisions[-1]["reason"] if decisions else "noFill"))
            self._decision(day, symbol, "frozenOpportunity", row["status"], row["reason"],
                           signalDate=row["signalDate"], signalId=row["signalId"], originalRank=row["originalRank"],
                           unfilledQuantity=row["unfilledQuantity"])
        # No failed/partial buy remainder remains for tomorrow. Holdings are
        # reintroduced as exact retained quantities at this session's close.

    def _step_day(self, day):
        super()._step_day(day)
        daily = self.state["daily"][-1]
        daily["topNByStrategy"] = self.scorer.daily(day)["strategies"]
        daily["targetSelectionPolicy"] = "frozenOriginalRank;fixedCloseBudgetAndQuantity;nextSessionOnly;noBackfill;noAdd"
        daily["fixedSignalOpportunities"] = deepcopy(self._opportunities(day))
        daily["buyAttemptOrder"] = [row["signalId"] for row in self.research_state["opportunities"] if row["attemptDate"] == day]

    def result(self):
        self._identity_stable()
        self._validate_research_state()
        result = super().result()
        result["quality"].update(certified=False, riskKnown=False, certificateScope="none;researchAdapterOnly",
                                 returnType="uncertifiedPriceAndAvailableRightsReturn")
        result["certified"], result["targetAchieved"] = False, False
        result["versions"]["fixedSignalAdapter"] = VERSION
        result["research"] = {"version": VERSION, "identity": deepcopy(self.identity),
                              "opportunities": deepcopy(self.research_state["opportunities"]),
                              "opportunityCounts": dict(Counter(row["status"] for row in self.research_state["opportunities"])),
                              "feeLedger": self.fee_ledger.checkpoint(), "certified": False, "targetAchieved": False,
                              "exitMeaning": "closePriceTriggers;calendarHoldingClockIncludingEntry;missingOrHaltCanDefer;notGuaranteedNetReturn",
                              "entryMeaning": "fixedCloseQuantity;oneNextSessionAttempt;noBackfill;noAdd;independentCashAccount"}
        return result

    # Controlled copy: only fee quoting, fee sizing, and committed-fill linkage differ.
    def _execute_order(self, day, symbol, side, requested, pending):
        s = self.state
        reason = self._gate(symbol, day, side)
        if reason:
            self._decision(day, symbol, side, "deferred", reason, signalDate=pending["signalDate"], requestedQuantity=requested)
            return
        price = self._fill_price(symbol, day, side)
        if price is None:
            self._decision(day, symbol, side, "deferred", "slippageBeyondLimitOrUncertainST", signalDate=pending["signalDate"])
            return
        bar, index = self._bar(symbol, day)
        minimum, increment = lot_rule(symbol)
        capacity = opening_capacity(self.securities[symbol], day, self.bars[symbol][index - 1]["volume"], self.config["maxParticipationRate"])
        position = s["positions"].get(symbol)
        quantity_before = position["quantity"] if position else 0
        pnl = None
        if side == "sell":
            sellable = sum(lot["quantity"] for lot in position["lots"] if lot["date"] < day and lot.get("unlockDate", lot["date"]) <= day)
            if sellable == 0:
                self._decision(day, symbol, side, "deferred", "tPlusOne", signalDate=pending["signalDate"])
                return
            desired = min(requested, capacity, sellable)
            quantity = desired if desired == position["quantity"] else _quantity(desired, minimum, increment)
            if not quantity:
                self._decision(day, symbol, side, "deferred", "previousVolumeOrLotMinimum", signalDate=pending["signalDate"], requestedQuantity=requested)
                return
            quote = self.fee_ledger.quote(quantity * price, side, self._context(symbol, pending["signalDate"], day, "fillQuote"))
            fee = quote["breakdown"]["total"]
            if s["cash"] - s["reservedActionCash"] + quantity * price < fee:
                self._decision(day, symbol, side, "deferred", "insufficientSellFeeCash", signalDate=pending["signalDate"])
                return
            left, cost, retained = quantity, 0., []
            for lot in position["lots"]:
                sold = min(left, lot["quantity"]) if lot["date"] < day and lot.get("unlockDate", lot["date"]) <= day else 0
                allocated = lot["cost"] * sold / lot["quantity"]
                left -= sold
                cost += allocated
                if lot["quantity"] > sold:
                    retained.append({**lot, "quantity": lot["quantity"] - sold, "cost": lot["cost"] - allocated})
            position["lots"] = retained
            position["quantity"] -= quantity
            position["costBasis"] -= cost
            s["cash"] += quantity * price - fee
            pnl = quantity * price - fee - cost
            position["realizedPnl"] += pnl
            if not position["quantity"]:
                s["closedTrades"].append({"symbol": symbol, "entryDate": position["entryDate"], "exitDate": day,
                                           "pnlAmount": position["realizedPnl"], "investedAmount": position["investedAmount"],
                                           "returnPct": position["realizedPnl"] / position["investedAmount"] * 100 if position["investedAmount"] else None,
                                           "holdingDays": self.calendar_index[day] - self.calendar_index[position["entryDate"]] + 1})
                del s["positions"][symbol]
        else:
            if position is None and len(s["positions"]) >= self.config["maxPositions"]:
                self._decision(day, symbol, side, "deferred", "positionSlotsOccupied", signalDate=pending["signalDate"], requestedQuantity=requested)
                return
            ceiling = _quantity(min(requested, capacity), minimum, increment)
            target_budget = max(0., pending["budgets"][symbol] - (position["quantity"] * price if position else 0.))
            quantity = self.fee_ledger.affordable_quantity(ceiling, minimum, increment, price, min(max(0., s["cash"] - s["reservedActionCash"]), target_budget), self._context(symbol, pending["signalDate"], day, "executionSizing"))
            if quantity < minimum:
                self._decision(day, symbol, side, "deferred", "cashOrPreviousVolumeBelowMinimum", signalDate=pending["signalDate"], requestedQuantity=requested)
                return
            quote = self.fee_ledger.quote(quantity * price, side, self._context(symbol, pending["signalDate"], day, "fillQuote"))
            fee = quote["breakdown"]["total"]
            cost = quantity * price + fee
            s["cash"] -= cost
            if -1e-7 < s["cash"] < 0:
                s["cash"] = 0.
            if position is None:
                position = {"symbol": symbol, "quantity": 0, "costBasis": 0., "lots": [],
                            "entryDate": day, "entryCursor": s["cursor"], "riskEntryPrice": price, "closePeak": price,
                            "realizedPnl": 0., "investedAmount": 0.}
                s["positions"][symbol] = position
            position["riskEntryPrice"] = (position["riskEntryPrice"] * position["quantity"] + price * quantity) / (position["quantity"] + quantity)
            position["quantity"] += quantity
            position["costBasis"] += cost
            position["investedAmount"] += cost
            position["lots"].append({"date": day, "quantity": quantity, "cost": cost})
            s["lastPrices"][symbol], s["lastPriceDates"][symbol] = price, day
        slippage = quantity * abs(price - bar["open"])
        s["totalFees"] += fee
        s["totalSlippage"] += slippage
        s["grossTradedAmount"] += quantity * price
        fill = {"date": day, "signalDate": pending["signalDate"], "symbol": symbol, "side": side,
                "quantity": quantity, "price": price, "openingPrice": bar["open"], "amount": quantity * price,
                "fee": fee, "slippage": slippage, "cashAfter": s["cash"], "realizedPnl": pnl,
                "reason": pending.get("reason", "targetRebalance"), "targetWeight": pending["weights"].get(symbol, 0.),
                "quantityBefore": quantity_before, "quantityAfter": s["positions"].get(symbol, {}).get("quantity", 0),
                "positionAction": ("open" if not quantity_before else "add") if side == "buy" else ("exit" if quantity == quantity_before else "reduce"),
                "unfilledQuantity": max(0, requested - quantity)}
        risk_evidence = {"riskEvidence": deepcopy(pending["riskEvidence"])} if pending.get("riskEvidence") else {}
        fill.update(risk_evidence)
        s["trades"].append(fill)
        fill_id = f"fill-{len(s['trades']):08d}"
        fill.update(fillId=fill_id, feeQuoteId=quote["quoteId"],
                    signalId=pending.get("signalId"), originalRank=pending.get("originalRank"))
        self.fee_ledger.commit_fill(quote, fill, fill_id)
        self._decision(day, symbol, side, "filled", pending.get("reason", "targetRebalance"), signalDate=pending["signalDate"], quantity=quantity, price=price,
                       unfilledQuantity=max(0, requested - quantity), **risk_evidence)

