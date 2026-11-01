"""Integrated system sizing/risk seams on the frozen costed account engine."""
from copy import deepcopy
import hashlib
from pathlib import Path

from .portfolio_engine import _digest, _quantity, lot_rule
from .research_volatility_trail import VolatilityTrailAccount, _bar_issue, _positive
from .research_market_sector_stock import POLICIES

VERSION = "market-sector-stock-account-v1"
ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = ("docs/market-sector-stock-account-v1.json", "service/research_market_sector_stock.py", "service/research_market_sector_account.py")
PARAMETERS = (.2, .4, .01, .05)


def source_pins():
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in SOURCE_FILES}


class MarketSectorStockAccount(VolatilityTrailAccount):
    def __init__(self, dataset, signals, config, *, system_inputs, policy, fee_policy):
        self.system_inputs = deepcopy(system_inputs)
        self.policy = policy
        self.system_identity = {"version": VERSION, "policy": policy, "inputDigest": _digest(self.system_inputs), "sourcePins": source_pins(), "parameters": PARAMETERS}
        self._system_daily = {row["date"]: row for row in self.system_inputs["daily"]}
        self._system_meta = self.system_inputs["metadata"]
        self._system_maps = self.system_inputs["sectorMaps"]
        self._system_live, self._system_bindings = {}, []
        self._system_plans, self._system_opening, self._system_risk = [], [], []
        if policy not in POLICIES:
            raise ValueError("A registered system policy is required")
        if len(self._system_daily) != len(self.system_inputs["daily"]):
            raise ValueError("Duplicate system decision date")
        seen_sector_days = set()
        for signal in signals:
            meta = self._system_meta.get(signal["signalId"])
            if meta is None or any(meta[k] != signal[k] for k in ("signalId", "signalDate", "symbol", "originalRank")):
                raise ValueError("Exact frozen signal/system metadata linkage required")
            if not all(_positive(meta[k]) for k in ("signalClose", "failurePrice")) or not 0 <= meta["meanNtr20"] < 1 or meta["failurePrice"] >= meta["signalClose"]:
                raise ValueError("Invalid pre-entry structural/volatility evidence")
            if meta["signalDate"] not in self._system_daily:
                raise ValueError("Signal has no same-close system context")
            context = self._system_daily[meta["signalDate"]]
            if context["snapshotId"] != meta["snapshotId"] or self._system_maps.get(meta["snapshotId"], {}).get(meta["symbol"]) != meta["sectorCode"]:
                raise ValueError("Candidate industry does not match its known snapshot")
            if policy != "withoutMarketAdmission" and context["market"]["admission"] is not True:
                raise ValueError("Candidate bypassed its market admission")
            if policy != "withoutSectorAdmission" and meta["sectorCode"] not in context["selectedSectors"]:
                raise ValueError("Candidate bypassed its industry admission")
            sector_day = meta["signalDate"], meta["sectorCode"]
            if sector_day in seen_sector_days:
                raise ValueError("Only one frozen candidate per industry and close is allowed")
            seen_sector_days.add(sector_day)
            if policy == "withoutCatchupEntry" and meta["pattern"] == "catchup":
                raise ValueError("Catchup appeared in its disabled branch control")
        super().__init__(dataset, signals, config, entry_weight=.2, fee_policy=fee_policy)
        for signal in signals:
            bar, _ = self._bar(signal["symbol"], signal["signalDate"])
            meta = self._system_meta[signal["signalId"]]
            if bar is None or bar["close"] != meta["signalClose"] or meta["failurePrice"] < .95 * bar["close"]:
                raise ValueError("Signal reserve close/failure floor conflicts with source price")

    def _identity_stable(self):
        super()._identity_stable()
        if (self.system_identity["inputDigest"] != _digest(self.system_inputs) or self.system_identity["sourcePins"] != source_pins()
                or self.system_identity["parameters"] != PARAMETERS or self.policy != self.system_identity["policy"]):
            raise ValueError("Registered system inputs, rules or source changed")

    def _transaction_snapshot(self):
        return (super()._transaction_snapshot(), deepcopy(self._system_live), len(self._system_bindings),
            len(self._system_plans), len(self._system_opening), len(self._system_risk))

    def _transaction_restore(self, snapshot):
        base, live, bindings, plans, opening, risk = snapshot
        super()._transaction_restore(base)
        self._system_live = live
        del self._system_bindings[bindings:]
        del self._system_plans[plans:]
        del self._system_opening[opening:]
        del self._system_risk[risk:]

    def _system_day(self, day):
        if day not in self._system_daily:
            raise ValueError("Frozen system context missing for account date")
        return self._system_daily[day]

    def _sector_for(self, symbol, day):
        context = self._system_day(day)
        known = self._system_maps.get(context["snapshotId"], {}).get(symbol)
        return known if known is not None else self._system_live.get(symbol, {}).get("lastKnownSector")

    def _industry_values(self, day, *, opening=False):
        values = {}
        for symbol, position in self.state["positions"].items():
            sector = self._sector_for(symbol, day)
            if sector is None:
                raise ValueError("Held risk lost both current and entry industry reservation")
            bar, _ = self._bar(symbol, self._next_session(day)) if opening and self._next_session(day) else (None, None)
            price = bar["open"] if bar is not None and _positive(bar.get("open")) else self.state["lastPrices"][symbol]
            values[sector] = values.get(sector, 0.) + position["quantity"] * price
        return values

    def _select_targets(self, scored, excluded=None):
        day, equity = scored["date"], self.state["curve"][-1]["equity"]
        free = max(0, self.config["maxPositions"] - sum(s not in self.state["forcedExits"] for s in self.state["positions"]))
        industry_values = self._industry_values(day)
        cash_left = max(0., self.state["cash"] - self.state["reservedActionCash"])
        chosen, weights, priorities = [], {}, {}
        self.research_state["closePlan"] = []
        rows = self._opportunities(day)
        for row in rows:
            if row["status"] != "notObserved":
                raise ValueError("A system opportunity close was processed twice")
            meta = self._system_meta[row["signalId"]]
            row["nextSession"] = self._next_session(day)
            reason = self._close_exclusion(row["symbol"], day, excluded or set())
            if reason is None and not free:
                reason = "plannedSlotsOccupied"
            evidence = {"date": day, "signalId": row["signalId"], "equity": equity, "cashBeforeReservation": cash_left,
                "sectorCode": meta["sectorCode"], "industryValueBefore": industry_values.get(meta["sectorCode"], 0.),
                "riskBudget": .01 * equity, "failurePrice": meta["failurePrice"]}
            if reason is not None:
                row.update(status="notPlanned", reason=reason)
                self._system_plans.append({**evidence, "status": "notPlanned", "reason": reason})
                continue
            free -= 1
            minimum, increment = lot_rule(row["symbol"])
            price = meta["signalClose"] * (1 + self.config["slippageBps"] / 10000)
            sector_room = max(0., .4 * equity - evidence["industryValueBefore"])
            risk_quantity = _quantity(.01 * equity / (price - meta["failurePrice"]), minimum, increment)
            budget = min(.2 * equity, sector_room, cash_left, risk_quantity * price)
            context = self._context(row["symbol"], day, day, "signalReserve")
            ceiling = min(risk_quantity, _quantity(budget / price, minimum, increment))
            quantity = self.fee_ledger.affordable_quantity(ceiling, minimum, increment, price, budget, context)
            quote = self.fee_ledger.quote(quantity * price, "buy", context)
            row.update(budget=budget, reservePrice=price, plannedQuantity=quantity, reserveQuoteId=quote["quoteId"], unfilledQuantity=quantity,
                systemEquity=equity, systemRiskBudget=.01 * equity, systemSectorCode=meta["sectorCode"], systemFailurePrice=meta["failurePrice"])
            if row["nextSession"] is None or row["nextSession"] > self.dates[-1]:
                row.update(status="censored", reason="nextSessionOutsideStudy")
            elif not quantity:
                row.update(status="notPlanned", reason="systemBudgetRiskOrSectorBelowLotMinimum")
            else:
                row.update(status="planned", reason="nextSessionOnly")
                chosen.append(row["symbol"])
                self.research_state["closePlan"].append(row["signalId"])
                weights[row["symbol"]] = budget / equity
                priorities[row["symbol"]] = {"budgetWeight": budget / equity, "weightedScore": (6 - row["originalRank"]) / 5,
                    "originalRank": row["originalRank"], "signalId": row["signalId"]}
                cash_left -= quantity * price + quote["breakdown"]["total"]
                industry_values[meta["sectorCode"]] = evidence["industryValueBefore"] + quantity * price
            self._system_plans.append({**evidence, "status": row["status"], "reason": row["reason"], "sectorRoom": sector_room,
                "reservePrice": price, "budget": budget, "riskQuantity": risk_quantity, "plannedQuantity": quantity,
                "reserveFee": quote["breakdown"]["total"], "cashAfterReservation": cash_left})
        details = [deepcopy(row) for row in rows if row["symbol"] in chosen]
        return {self.strategy_id: chosen}, weights, {self.strategy_id: 1.}, {self.strategy_id: details}, [], priorities

    def _execute_order(self, day, symbol, side, requested, pending):
        before = len(self.state["trades"])
        if side == "buy":
            meta = self._system_meta[pending["signalId"]]
            opportunity = next(r for r in self._opportunities(pending["signalDate"]) if r["signalId"] == pending["signalId"])
            bar, _ = self._bar(symbol, day)
            if bar is not None and _positive(bar.get("open")):
                opening = bar["open"]
                evidence = {"date": day, "signalId": pending["signalId"], "symbol": symbol, "signalDate": pending["signalDate"],
                    "opening": opening, "signalClose": meta["signalClose"], "failurePrice": meta["failurePrice"],
                    "openingCeiling": meta["signalClose"] * (1 + meta["meanNtr20"]), "requestedQuantity": requested,
                    "riskBudget": opportunity["systemRiskBudget"], "equityAtSignalClose": opportunity["systemEquity"]}
                reason = "systemOpeningAtOrBelowFailure" if opening <= meta["failurePrice"] else "systemOpeningAboveVolatilityCeiling" if opening > evidence["openingCeiling"] else None
                if reason:
                    self._system_opening.append({**evidence, "status": "rejected", "reason": reason})
                    self._decision(day, symbol, "buy", "deferred", reason, signalDate=pending["signalDate"])
                    return
                price = self._fill_price(symbol, day, side)
                if price is not None:
                    minimum, increment = lot_rule(symbol)
                    floor = max(meta["failurePrice"], .95 * price)
                    risk_quantity = _quantity(opportunity["systemRiskBudget"] / (price - floor), minimum, increment)
                    held_value = self._industry_values(pending["signalDate"], opening=True).get(meta["sectorCode"], 0.)
                    room = max(0., .4 * opportunity["systemEquity"] - held_value)
                    sector_quantity = _quantity(room / price, minimum, increment)
                    allowed = min(requested, risk_quantity, sector_quantity)
                    evidence.update(modeledPrice=price, effectiveFailureFloor=floor, industryOpeningValue=held_value,
                        industryOpeningRoom=room, riskQuantity=risk_quantity, sectorQuantity=sector_quantity, allowedQuantity=allowed)
                    if allowed < minimum:
                        self._system_opening.append({**evidence, "status": "rejected", "reason": "systemOpeningRiskOrSectorBelowLotMinimum"})
                        self._decision(day, symbol, "buy", "deferred", "systemOpeningRiskOrSectorBelowLotMinimum", signalDate=pending["signalDate"])
                        return
                    # Keep original requested quantity for unfilled conservation;
                    # a tighter affordable budget limits the inherited fill size.
                    quote = self.fee_ledger.quote(allowed * price, "buy", self._context(symbol, pending["signalDate"], day, "executionSizing"))
                    pending = deepcopy(pending)
                    pending["budgets"][symbol] = min(pending["budgets"][symbol], allowed * price + quote["breakdown"]["total"])
                    self._system_opening.append({**evidence, "status": "submittedToInheritedGate", "budgetCap": pending["budgets"][symbol]})
        super()._execute_order(day, symbol, side, requested, pending)
        fills = self.state["trades"][before:]
        if not fills:
            return
        if side == "sell":
            if symbol not in self.state["positions"]:
                self._system_live.pop(symbol, None)
            return
        fill = fills[0]
        meta = self._system_meta[fill["signalId"]]
        binding = {"symbol": symbol, "signalId": fill["signalId"], "signalDate": fill["signalDate"], "entryDate": day,
            "entryFillId": fill["fillId"], "entryFillPrice": fill["price"], "failurePriceAtSignal": meta["failurePrice"],
            "failureRatio": meta["failurePrice"] / fill["price"], "entrySector": meta["sectorCode"], "lastKnownSector": meta["sectorCode"]}
        self._system_live[symbol] = binding
        self._system_bindings.append(deepcopy(binding))

    def _queue_system_exit(self, day, symbol, reason, evidence):
        self.state["forcedExits"][symbol] = {"signalDate": day, "reason": reason, "riskEvidence": deepcopy(evidence)}
        pending = self.state["pendingTarget"]
        if pending is not None:
            for key in ("weights", "budgets", "priorityBySymbol", "signalDates"):
                pending.get(key, {}).pop(symbol, None)
            pending["quantities"][symbol] = 0
        self._decision(day, symbol, "sell", "queued", reason, signalDate=day, riskEvidence=deepcopy(evidence))

    def _risk_exits(self, day, eligibility):
        super()._risk_exits(day, eligibility)
        context = self._system_day(day)
        sectors = {r["sectorCode"]: r for r in context["sectors"]}
        mapping = self._system_maps.get(context["snapshotId"], {})
        for symbol, position in sorted(self.state["positions"].items()):
            binding = self._system_live[symbol]
            if symbol in mapping:
                binding["lastKnownSector"] = mapping[symbol]
            sector = sectors.get(mapping.get(symbol))
            bar, _ = self._bar(symbol, day)
            valid = _bar_issue(bar, day) is None
            threshold = binding["failureRatio"] * position["riskEntryPrice"]
            structural = bool(valid and bar["close"] <= threshold)
            market = context["market"]["riskExit"] is True
            industry = sector is not None and sector["riskExit"] is True
            earlier = self.state["forcedExits"].get(symbol)
            reason = earlier["reason"] if earlier else "systemStructuralFailure" if structural else "systemMarketRisk" if market else "systemSectorRisk" if industry else None
            evidence = {"date": day, "symbol": symbol, "signalId": binding["signalId"], "entryDate": position["entryDate"],
                "riskEntryPrice": position["riskEntryPrice"], "failureRatio": binding["failureRatio"], "failureThreshold": threshold,
                "observedClose": bar["close"] if bar else None, "currentPriceValid": valid, "structuralTriggered": structural,
                "marketRiskTriggered": market, "marketCrisisCount": context["market"]["crisisCount"], "knownSector": mapping.get(symbol),
                "sectorFailCount": sector["failCount"] if sector else None, "sectorRiskTriggered": industry,
                "earlierRiskRetained": earlier is not None, "reason": reason}
            self._system_risk.append(evidence)
            if reason and earlier is None:
                self._queue_system_exit(day, symbol, reason, evidence)

    def _system_result(self):
        return {"identity": deepcopy(self.system_identity), "entryBindings": deepcopy(self._system_bindings),
            "closePlans": deepcopy(self._system_plans), "openingGuards": deepcopy(self._system_opening), "riskObservations": deepcopy(self._system_risk),
            "riskPriority": "existingForced;inheritedEligibilityStop5;volatilityRatchet;structural;market;sector",
            "checkpointRestoreSupported": False, "certified": False, "targetAchieved": False}

    def result(self):
        value = super().result()
        value["versions"]["integratedSystem"] = VERSION
        value["research"]["system"] = self._system_result()
        return value

    def checkpoint(self):
        value = super().checkpoint()
        value.update(schema="market-sector-stock-descriptive-snapshot-v1", system=self._system_result(), currentSystemEntries=deepcopy(self._system_live))
        value.pop("digest")
        value["digest"] = _digest(value)
        return value
