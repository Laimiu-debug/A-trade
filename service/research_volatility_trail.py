"""Past normalized true range sets a ratcheting close-time exit, research only.

The five-percent initial stop and all order/fee/lot behavior remain inherited.
Only the additional trail is new. Its floor is stored in risk-entry units so
the engine's inherited reference conversion scales price levels consistently.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date
import math

from .portfolio_engine import _digest
from .research_fixed_signal_account import FixedSignalAccount

VERSION = "research-volatility-trail-v1"
WINDOW_SLOTS = 21
TR_COUNT = 20
MULTIPLIER = 3
DECISION_STATUSES = ("triggered", "notTriggered", "unknown", "skippedExistingExit", "skippedStandardExit")
WINDOW_STATUSES = ("known", "unknown", "invalidDistance")


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _bar_issue(bar, day):
    if not isinstance(bar, dict):
        return "missingBar"
    if bar.get("date") != day:
        return "barDateMismatch"
    if any(not _positive(bar.get(key)) for key in ("open", "high", "low", "close", "volume")):
        return "nonPositiveOrInvalidOHLCV"
    if not bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]:
        return "invalidOHLCEnvelope"
    return None


def prior_mean_tr_pct20(day, prior_dates, prior_bars, preclose_by_date):
    """Evaluate exactly aligned prior slots; no I/O, compression, or repairs.

    The caller supplies the source calendar slice strictly before ``day``.
    A shorter initial slice is unknown. Dates/order mismatch is rejected;
    missing or invalid observations remain explicit window evidence.
    """
    if (not isinstance(prior_dates, list) or not isinstance(prior_bars, list)
            or not isinstance(preclose_by_date, dict) or len(prior_dates) != len(prior_bars)
            or len(prior_dates) > WINDOW_SLOTS):
        raise ValueError("Aligned prior calendar slots and preclose mapping required")
    for value in [day, *prior_dates]:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError("Canonical calendar dates required")
    if prior_dates != sorted(set(prior_dates)) or any(value >= day for value in prior_dates):
        raise ValueError("Prior dates must be unique, ordered and strictly before close day")
    bar_issues = [{"date": d, "reason": issue} for d, bar in zip(prior_dates, prior_bars)
                  if (issue := _bar_issue(bar, d)) is not None]
    issues = deepcopy(bar_issues)
    if len(prior_dates) != WINDOW_SLOTS:
        issues.insert(0, {"date": day, "reason": "insufficientPriorCalendarSlots"})
    terms = []
    for index in range(1, len(prior_dates)):
        d, previous_day = prior_dates[index], prior_dates[index - 1]
        bar, previous = prior_bars[index], prior_bars[index - 1]
        explicit = d in preclose_by_date
        reference = preclose_by_date[d] if explicit else previous.get("close") if isinstance(previous, dict) else None
        reason = "invalidCurrentBar" if _bar_issue(bar, d) else None
        if not _positive(reference):
            reference_reason = "invalidExplicitPreclose" if explicit else "invalidPreviousCalendarClose"
            issues.append({"date": d, "reason": reference_reason})
            reason = reason or reference_reason
        true_range = tr_pct = None
        if reason is None:
            true_range = max(bar["high"] - bar["low"], abs(bar["high"] - reference), abs(bar["low"] - reference))
            tr_pct = true_range / reference
            if not math.isfinite(true_range) or not math.isfinite(tr_pct):
                true_range = tr_pct = None
                reason = "nonFiniteTrueRange"
                issues.append({"date": d, "reason": reason})
        terms.append({"date": d, "previousDate": previous_day,
            "referenceSource": "precloseByDate" if explicit else "previousCalendarClose",
            "referenceValue": reference if _positive(reference) else None,
            "high": bar.get("high") if isinstance(bar, dict) and _positive(bar.get("high")) else None,
            "low": bar.get("low") if isinstance(bar, dict) and _positive(bar.get("low")) else None,
            "trueRange": true_range, "trPct": tr_pct,
            "status": "unknown" if reason else "known", "reason": reason})
    mean = width = None
    status = "unknown"
    if not issues:
        try:
            mean = math.fsum(row["trPct"] for row in terms) / TR_COUNT
            width = MULTIPLIER * mean
        except OverflowError:
            mean = width = None
        if mean is None or not math.isfinite(mean) or not math.isfinite(width):
            mean = width = None
            issues.append({"date": day, "reason": "nonFiniteMeanOrDistance"})
        elif not 0 < width < 1:
            status = "invalidDistance"
            issues.append({"date": day, "reason": "distanceNotStrictlyBetweenZeroAndOne"})
        else:
            status = "known"
    return {"asOfDate": prior_dates[-1] if prior_dates else None, "windowDates": list(prior_dates),
        "barIssues": bar_issues, "trTerms": terms, "meanTRPct20": mean,
        "scaledDistancePct": width, "windowStatus": status, "issues": issues}


class VolatilityTrailAccount(FixedSignalAccount):
    def __init__(self, dataset, signals, config, *, entry_weight, fee_policy, state=None):
        if state is not None:
            raise ValueError("VolatilityTrailAccount checkpoint restore is not supported")
        expected = {"stopLossPct": .05, "trailingStopPct": 0, "takeProfitPct": 0, "maxHoldingDays": 0,
                    "maxStockWeight": .2}
        if (not isinstance(config, dict) or any(type(config.get(k)) not in (int, float) or config[k] != v for k, v in expected.items())
                or type(entry_weight) not in (int, float) or entry_weight != .2):
            raise ValueError("Volatility trail requires original stop5, no other price/time exit and20percent entry weight")
        if isinstance(dataset, dict) and (dataset.get("corporateActions") or any(
                s.get("corporateActions") for s in dataset.get("securities", []) if isinstance(s, dict))):
            raise ValueError("Declared corporateActions are unsupported by volatilityTrail")
        self._trail_positions = {}
        self._entry_bindings = []
        self._trail_observations = []
        self._registered_parameters = (WINDOW_SLOTS, TR_COUNT, MULTIPLIER)
        super().__init__(dataset, signals, config, entry_weight=entry_weight, fee_policy=fee_policy)

    def _identity_stable(self):
        super()._identity_stable()
        if (WINDOW_SLOTS, TR_COUNT, MULTIPLIER) != self._registered_parameters:
            raise ValueError("Registered volatility-trail parameters changed")

    def _transaction_snapshot(self):
        # Position floors mutate; evidence is append-only, so truncating its
        # suffix restores logs without quadratic copies of the whole history.
        return (super()._transaction_snapshot(), deepcopy(self._trail_positions),
                len(self._entry_bindings), len(self._trail_observations))

    def _transaction_restore(self, snapshot):
        base, positions, entries, observations = snapshot
        super()._transaction_restore(base)
        self._trail_positions = positions
        del self._entry_bindings[entries:]
        del self._trail_observations[observations:]

    def _execute_order(self, day, symbol, side, requested, pending):
        start = len(self.state["trades"])
        super()._execute_order(day, symbol, side, requested, pending)
        fills = self.state["trades"][start:]
        if not fills:
            return
        if len(fills) != 1:
            raise ValueError("Unexpected multiple fills for one frozen order")
        fill = fills[0]
        if side == "sell":
            if symbol not in self.state["positions"]:
                self._trail_positions.pop(symbol, None)
            return
        if fill["positionAction"] != "open" or symbol in self._trail_positions:
            raise ValueError("Volatility trail requires one initial fill and no additions")
        binding = {"signalId": fill["signalId"], "signalDate": fill["signalDate"], "symbol": symbol,
            "entryDate": day, "entryFillId": fill["fillId"], "entryQuantity": fill["quantity"],
            "entryFillPrice": fill["price"]}
        self._entry_bindings.append(deepcopy(binding))
        self._trail_positions[symbol] = {**binding, "ratchetRatio": None}

    def _queue_trail(self, day, symbol, evidence):
        # Inherited forced-exit queue seam; fills/blocked retries remain in the
        # frozen account. A previous order is never replaced by this adapter.
        self.state["forcedExits"][symbol] = {"signalDate": day, "reason": "volatilityTrail",
                                             "riskEvidence": deepcopy(evidence)}
        pending = self.state["pendingTarget"]
        if pending is not None:
            for key in ("weights", "budgets", "priorityBySymbol", "signalDates"):
                pending.get(key, {}).pop(symbol, None)
            pending["quantities"][symbol] = 0
        self._decision(day, symbol, "sell", "queued", "volatilityTrail", signalDate=day,
                       observedClose=evidence["observedClose"], riskEvidence=deepcopy(evidence))

    def _risk_exits(self, day, eligibility):
        existing = set(self.state["forcedExits"])
        super()._risk_exits(day, eligibility)
        index = self.calendar_index[day]
        dates = self.all_dates[max(0, index - WINDOW_SLOTS):index]
        for symbol, position in sorted(self.state["positions"].items()):
            binding = self._trail_positions.get(symbol)
            if binding is None or binding["entryDate"] != position["entryDate"]:
                raise ValueError("Held position lacks current-entry trail binding")
            bar, _ = self._bar(symbol, day)
            current_valid = bar is not None and bar.get("date") == day and _positive(bar.get("close")) and _positive(bar.get("volume"))
            window = prior_mean_tr_pct20(day, dates, [self._bar(symbol, d)[0] for d in dates],
                                         self.securities[symbol].get("precloseByDate", {}))
            previous = binding["ratchetRatio"]
            candidate_price = candidate = None
            ratio = previous
            order = self.state["forcedExits"].get(symbol)
            if order is not None:
                status = "skippedExistingExit" if symbol in existing else "skippedStandardExit"
                decision_reason = "earlierForcedExitRetained" if symbol in existing else "standardRiskHasPriority"
            elif not current_valid:
                status, decision_reason = "unknown", "missingOrInvalidCurrentCloseOrVolume"
            else:
                if window["windowStatus"] == "known":
                    candidate_price = position["closePeak"] * (1 - window["scaledDistancePct"])
                    candidate = candidate_price / position["riskEntryPrice"]
                    if not _positive(candidate_price) or not _positive(candidate):
                        raise ValueError("Finite positive registered candidate expected")
                    ratio = candidate if previous is None else max(previous, candidate)
                    binding["ratchetRatio"] = ratio
                status = "unknown" if ratio is None else "triggered" if bar["close"] <= ratio * position["riskEntryPrice"] else "notTriggered"
                decision_reason = "noKnownThreshold" if ratio is None else "knownWindow" if window["windowStatus"] == "known" else "carriedThresholdDespiteUnavailableWindow"
            threshold = ratio * position["riskEntryPrice"] if ratio is not None else None
            evidence = {"date": day, "symbol": symbol, "signalId": binding["signalId"], "signalDate": binding["signalDate"],
                "entryDate": position["entryDate"], "holdingDays": index - self.calendar_index[position["entryDate"]] + 1,
                "quantity": position["quantity"], "observedClose": bar["close"] if bar else None,
                "observedVolume": bar["volume"] if bar else None, "currentPriceValid": current_valid,
                "riskEntryPrice": position["riskEntryPrice"], "closePeak": position["closePeak"], "window": window,
                "previousRatio": previous, "candidatePrice": candidate_price, "candidateRatio": candidate,
                "ratchetRatio": ratio, "threshold": threshold, "thresholdKnown": ratio is not None,
                "usedCarriedThreshold": previous is not None and current_valid and order is None and window["windowStatus"] != "known",
                "decisionStatus": status, "decisionReason": decision_reason,
                "exitReason": order["reason"] if order else "volatilityTrail" if status == "triggered" else None,
                "exitSignalDate": order["signalDate"] if order else day if status == "triggered" else None}
            self._trail_observations.append(evidence)
            if status == "triggered":
                self._queue_trail(day, symbol, evidence)

    def _trail_result(self):
        decisions = Counter(row["decisionStatus"] for row in self._trail_observations)
        windows = Counter(row["window"]["windowStatus"] for row in self._trail_observations)
        return {"version": VERSION, "parameters": {"windowSlots": WINDOW_SLOTS, "trueRangeCount": TR_COUNT,
            "multiplier": MULTIPLIER, "windowEnd": "heldCloseT-1"}, "checkpointRestoreSupported": False,
            "entryBindings": deepcopy(self._entry_bindings), "observations": deepcopy(self._trail_observations),
            "observationCounts": {key: decisions[key] for key in DECISION_STATUSES},
            "windowCounts": {key: windows[key] for key in WINDOW_STATUSES},
            "formula": "mean(TR/reference) over20 exact prior sessions; candidate=closePeak*(1-3*mean)/riskEntryPrice; ratchetRatio=max(previous,candidate)",
            "priority": "existingForcedExit; inheritedEligibilityDelistSTAndStop5; volatilityTrail; noDay5/noPercentageTrail",
            "unknownPolicy": "Unavailable window creates no new candidate; existing ratio remains usable at a valid current close. Invalid current close/volume neither updates nor triggers.",
            "referencePolicy": "Ratio is unchanged by inherited simultaneous riskEntryPrice/closePeak conversion; raw historical rights uncertainty remains.",
            "certified": False, "targetAchieved": False}

    def result(self):
        value = super().result()
        value["versions"]["researchVolatilityTrail"] = VERSION
        value["research"]["volatilityTrail"] = self._trail_result()
        return value

    def checkpoint(self):
        value = super().checkpoint()
        value.update(schema="research-volatility-trail-descriptive-snapshot-v1", checkpointRestoreSupported=False,
                     volatilityTrail=self._trail_result(), currentPositionTrails=deepcopy(self._trail_positions))
        value.pop("digest")
        value["digest"] = _digest(value)
        return value
