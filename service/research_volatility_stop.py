"""Isolated entry-frozen mean-TR stop experiment; not a live trading policy.

The upstream noProgress5 account owns order gates, lots, fees and valuation.
Only the initial stop fraction changes. No global configuration or position
risk reference is temporarily rewritten. The registered input mean is a simple
mean of 20 true ranges from 21 calendar-aligned bars ending at signal T-1; its
raw-window calculation belongs to the separately pinned input preparation.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import math

from .portfolio_engine import _digest
from .research_structural_exits import ResearchExitAccount

VERSION = "research-volatility-stop-v1"
MODES = ("fixed5", "meanTR20x2")


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _safe_input(value):
    if type(value) is float and not math.isfinite(value):
        return "NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"
    if value is None or type(value) in (str, int, float, bool):
        return value
    return {"invalidType": type(value).__name__}


class VolatilityStopAccount(ResearchExitAccount):
    def __init__(self, dataset, signals, config, *, volatility_inputs, mode,
                 entry_weight, fee_policy, state=None):
        if state is not None:
            raise ValueError("VolatilityStopAccount checkpoint restore is not supported")
        if mode not in MODES:
            raise ValueError("Unsupported volatility stop mode")
        if type(entry_weight) not in (int, float) or entry_weight != .2:
            raise ValueError("Volatility stop experiment requires 20 percent entry weight")
        if not isinstance(config, dict) or type(config.get("maxStockWeight")) not in (int, float) or config["maxStockWeight"] != .2:
            raise ValueError("Volatility stop experiment requires 20 percent maxStockWeight")
        if not isinstance(volatility_inputs, dict):
            raise ValueError("volatility_inputs must map signalId to input records")
        if mode == "meanTR20x2" and isinstance(dataset, dict) and (
                dataset.get("corporateActions") or any(security.get("corporateActions")
                    for security in dataset.get("securities", []) if isinstance(security, dict))):
            raise ValueError("Declared corporateActions are unsupported by meanTR20x2")
        self.mode = self._registered_mode = mode
        self._position_stops = {}
        self._entry_stop_bindings = []
        self._stop_observations = []
        super().__init__(dataset, signals, config, exit_rule="noProgress5",
                         entry_weight=entry_weight, fee_policy=fee_policy)
        schedule = {row["signalId"]: row for row in self._schedule}
        if set(volatility_inputs) - set(schedule):
            raise ValueError("Volatility input has unknown signalId")
        normalized = {}
        for sid, row in volatility_inputs.items():
            if row is None:
                continue
            if not isinstance(row, dict):
                raise ValueError("Volatility input record must be an object or null")
            signal = schedule[sid]
            for key in ("signalId", "symbol", "signalDate"):
                if row.get(key) != signal[key]:
                    raise ValueError("Volatility input signal identity differs")
            index = self.calendar_index[signal["signalDate"]]
            previous = self.all_dates[index - 1] if index else None
            if row.get("asOfDate") != previous:
                raise ValueError("Volatility asOfDate must be exact source-calendar T-1; future/current input rejected")
            if "windowDates" in row and row["windowDates"] != self.all_dates[max(0, index - 21):index]:
                raise ValueError("Volatility window must be 21 aligned source-calendar slots ending T-1")
            original = row.get("meanTrueRange20")
            normalized[sid] = {"signalId": sid, "symbol": signal["symbol"],
                               "signalDate": signal["signalDate"], "asOfDate": previous,
                               "meanTrueRange20": original if _positive(original) else None,
                               "inputMeanTrueRange20": _safe_input(original),
                               "inputValidity": "insufficientPriorCalendarSessions" if index < 21
                                   else "valid" if _positive(original) else "missingOrInvalidMeanTrueRange20"}
        self.volatility_inputs = normalized
        self._registered_inputs_digest = _digest(normalized)

    def _identity_stable(self):
        super()._identity_stable()
        if self.mode != self._registered_mode or _digest(self.volatility_inputs) != self._registered_inputs_digest:
            raise ValueError("Volatility stop mode or input changed")

    def _transaction_snapshot(self):
        return (super()._transaction_snapshot(), deepcopy(self._position_stops),
                len(self._entry_stop_bindings), len(self._stop_observations))

    def _transaction_restore(self, snapshot):
        original, positions, bindings_length, observations_length = snapshot
        super()._transaction_restore(original)
        self._position_stops = positions
        del self._entry_stop_bindings[bindings_length:]
        del self._stop_observations[observations_length:]

    def _execute_order(self, day, symbol, side, requested, pending):
        start = len(self.state["trades"])
        super()._execute_order(day, symbol, side, requested, pending)
        fills = self.state["trades"][start:]
        if not fills:
            return
        if len(fills) != 1:
            raise ValueError("Unexpected multiple fills in one frozen order")
        fill = fills[0]
        if side == "sell":
            if symbol not in self.state["positions"]:
                self._position_stops.pop(symbol, None)
            return
        if fill["positionAction"] != "open" or symbol in self._position_stops:
            raise ValueError("Volatility stop requires one initial fill, no additions")
        sid = fill["signalId"]
        source = self.volatility_inputs.get(sid)
        initial_price = self.state["positions"][symbol]["riskEntryPrice"]
        fill_price = fill["price"]
        mean_tr = source["meanTrueRange20"] if source else None
        fallback = None
        fraction = .05
        distance = fill_price * fraction
        if self.mode == "meanTR20x2":
            if source is None:
                fallback = "missingVolatilityInput"
            elif source["inputValidity"] != "valid":
                fallback = source["inputValidity"]
            else:
                candidate_distance = 2 * mean_tr
                candidate = candidate_distance / fill_price
                if not math.isfinite(candidate_distance) or not math.isfinite(candidate) or not 0 < candidate < 1:
                    fallback = "distanceNotStrictlyBetweenZeroAndEntryPrice"
                else:
                    fraction = candidate
                    distance = candidate_distance
        binding = {"signalId": sid, "symbol": symbol, "signalDate": fill["signalDate"],
                   "entryDate": day, "entryFillId": fill["fillId"], "entryQuantity": fill["quantity"],
                   "mode": self.mode, "asOfDate": source["asOfDate"] if source else None,
                   "meanTrueRange20": mean_tr,
                   "inputMeanTrueRange20": source["inputMeanTrueRange20"] if source else None,
                   "entryRiskPrice": initial_price, "entryFillPrice": fill_price, "entryDistance": distance,
                   "entryInitialRiskAmount": fill["quantity"] * distance,
                   "effectivePct": fraction, "fallback": fallback is not None, "fallbackReason": fallback,
                   "effectiveMode": "meanTR20x2" if self.mode == "meanTR20x2" and fallback is None else "fixed5"}
        self._position_stops[symbol] = deepcopy(binding)
        self._entry_stop_bindings.append(binding)

    def _stop_terms(self, symbol, position):
        binding = self._position_stops.get(symbol)
        if binding is None or binding["entryDate"] != position["entryDate"]:
            raise ValueError("Held position lacks current-entry volatility binding")
        distance = position["riskEntryPrice"] * binding["effectivePct"]
        return {**deepcopy(binding), "currentRiskEntryPrice": position["riskEntryPrice"],
                "currentRiskDistance": distance,
                "threshold": position["riskEntryPrice"] * (1 - binding["effectivePct"]),
                "referenceConversion": "Inherited riskEntryPrice conversion scales frozen entry fraction; never re-estimates TR."}

    def _queue_standard_exit(self, day, symbol, reason, evidence):
        # Minimal close-to-next-open queue copied from PortfolioEngine._risk_exits.
        # All gate/capacity/T+1/partial-fill retry behavior remains inherited.
        self.state["forcedExits"][symbol] = {"signalDate": day, "reason": reason, "riskEvidence": evidence}
        pending = self.state["pendingTarget"]
        if pending is not None:
            for key in ("weights", "budgets", "priorityBySymbol", "signalDates"):
                pending.get(key, {}).pop(symbol, None)
            pending["quantities"][symbol] = 0
        self._decision(day, symbol, "sell", "queued", reason, signalDate=day,
                       observedClose=evidence["observedClose"], riskEvidence=deepcopy(evidence))

    def _dynamic_risk_exits(self, day, eligibility):
        # Controlled copy of PortfolioEngine._risk_exits standard priority.
        # The sole trigger change is .05 -> entry-frozen effectivePct.
        for symbol, position in sorted(self.state["positions"].items()):
            security, config = self.securities[symbol], self.config
            bar, _ = self._bar(symbol, day)
            terms = self._stop_terms(symbol, position)
            reason = None
            if self._member(symbol, day) is False:
                reason = "baseEligibilityLost"
            elif security.get("delistDate") and day >= security["delistDate"]:
                reason = "delisted"
            elif config["universe"]["excludeSt"] and security.get("stByDate", {}).get(day) is True:
                reason = "historicalST"
            elif bar is not None and bar["volume"] > 0:
                position["closePeak"] = max(position["closePeak"], bar["close"])
                gain = bar["close"] / position["riskEntryPrice"] - 1
                if gain <= -terms["effectivePct"]:
                    reason = "stopLoss"
                elif bar["close"] / position["closePeak"] - 1 <= -config["trailingStopPct"]:
                    reason = "trailingStop"
            if reason and symbol not in self.state["forcedExits"]:
                close = bar["close"] if bar else None
                evidence = {"observedDate": day, "observedClose": close,
                            "riskEntryPrice": position["riskEntryPrice"], "closePeak": position["closePeak"],
                            "gainPct": close / position["riskEntryPrice"] - 1 if close is not None else None,
                            "drawdownFromPeakPct": close / position["closePeak"] - 1 if close is not None else None,
                            "holdingDays": self.calendar_index[day] - self.calendar_index[position["entryDate"]] + 1,
                            "stopLossPct": terms["effectivePct"], "configuredFallbackStopLossPct": .05,
                            "takeProfitPct": 0, "trailingStopPct": config["trailingStopPct"],
                            "maxHoldingDays": 0, "percentageUnit": "ratio", "volatilityStop": terms}
                self._queue_standard_exit(day, symbol, reason, evidence)
            elif eligibility.get(symbol, {}).get("reason") == "amount20BelowMinimum":
                self._decision(day, symbol, "hold", "held", "liquidityFilterFailedButRiskHoldingRetained")
        # Minimal noProgress5 observation copied from ResearchExitAccount._risk_exits.
        # Existing standard/earlier queued orders keep their original evidence.
        index = self.calendar_index[day]
        for symbol, position in sorted(self.state["positions"].items()):
            holding_days = index - self.calendar_index[position["entryDate"]] + 1
            if holding_days != 5:
                continue
            bar, _ = self._bar(symbol, day)
            evidence = {"date": day, "symbol": symbol, "rule": "noProgress5",
                        "entryDate": position["entryDate"], "holdingDays": holding_days,
                        "observedClose": bar["close"] if bar else None,
                        "riskEntryPrice": position["riskEntryPrice"],
                        "priceReferenceMeaning": "entryFillPriceIncludingSlippageExcludingFees",
                        "volatilityStop": self._stop_terms(symbol, position)}
            existing = self.state["forcedExits"].get(symbol)
            if existing is not None:
                evidence.update(status="skippedExistingExit", existingReason=existing["reason"],
                                existingSignalDate=existing["signalDate"])
            else:
                known = bar is not None and _positive(bar.get("close")) and _positive(bar.get("volume")) and _positive(position["riskEntryPrice"])
                evidence.update(status="unknown" if not known else "triggered" if bar["close"] <= position["riskEntryPrice"] else "notTriggered",
                                comparison="fifthSessionCloseLessThanOrEqualRiskEntryPrice",
                                unknown=[] if known else [{"date": day, "reason": "missingOrInvalidFifthSessionPriceOrVolume"}],
                                retryOnLaterSession=False)
            self._extra_risk_observations.append(evidence)
            if evidence["status"] == "triggered":
                self._queue_extra_exit(day, symbol, evidence)

    def _risk_exits(self, day, eligibility):
        existing = deepcopy(self.state["forcedExits"])
        if self.mode == "fixed5":
            super()._risk_exits(day, eligibility)
        else:
            self._dynamic_risk_exits(day, eligibility)
        for symbol, position in sorted(self.state["positions"].items()):
            bar, _ = self._bar(symbol, day)
            order = self.state["forcedExits"].get(symbol)
            known = bar is not None and _positive(bar.get("close")) and _positive(bar.get("volume"))
            status = "skippedExistingExit" if symbol in existing else "triggered" if order else "notTriggered" if known else "unknown"
            self._stop_observations.append({**self._stop_terms(symbol, position), "date": day,
                "holdingDays": self.calendar_index[day] - self.calendar_index[position["entryDate"]] + 1,
                "quantity": position["quantity"], "observedClose": bar["close"] if bar else None,
                "barAvailableForPriceRisk": known, "closePeak": position["closePeak"], "status": status,
                "exitReason": order["reason"] if order else None,
                "exitSignalDate": order["signalDate"] if order else None,
                "existingExitReason": existing[symbol]["reason"] if symbol in existing else None})

    def _volatility_result(self):
        return {"version": VERSION, "mode": self.mode, "checkpointRestoreSupported": False,
                "inputSha256": self._registered_inputs_digest, "inputs": deepcopy(self.volatility_inputs),
                "entryBindings": deepcopy(self._entry_stop_bindings),
                "observationCounts": dict(Counter(row["status"] for row in self._stop_observations)),
                "observations": deepcopy(self._stop_observations),
                "fallbackEntryCount": sum(row["fallback"] for row in self._entry_stop_bindings),
                "formula": "effectivePct=2*simpleMeanTrueRange20(T-1)/actualEntryPrice; fallback=.05; freeze fraction at first buy",
                "riskReferencePolicy": "Inherited preclose reference conversion retained; current distance=riskEntryPrice*effectivePct; no TR re-estimation",
                "priority": "existingQueuedOrderPreserved; eligibility/delist/ST > initialStop > trailing10 > exactFifthSessionNoProgress",
                "limitations": ["Input formula/raw-window audit is external to this adapter.",
                    "Dynamic mode rejects declared corporateActions; undeclared historical actions and reference uncertainty remain inherited.",
                    "Stops are close-time triggers executed at a later eligible open, not loss guarantees."],
                "certified": False, "targetAchieved": False}

    def result(self):
        value = super().result()
        value["versions"]["researchVolatilityStop"] = VERSION
        value["research"]["volatilityStop"] = self._volatility_result()
        return value

    def checkpoint(self):
        value = super().checkpoint()
        value.update(schema="research-volatility-stop-descriptive-snapshot-v1",
                     checkpointRestoreSupported=False, volatilityStop=self._volatility_result(),
                     currentPositionStops=deepcopy(self._position_stops))
        value.pop("digest")
        value["digest"] = _digest(value)
        return value
