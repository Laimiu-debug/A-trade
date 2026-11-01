"""Fixed trailing protection, then a causal breakout-activated volatility trail.

This isolated research adapter never changes entries, fills, fees or the initial
five-percent stop. A formation anchor is frozen at the first held close, before
any activation decision; no entry-day closing evidence controls opening buys.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date
import math

from .portfolio_engine import _digest
from .research_fixed_signal_account import FixedSignalAccount
from .research_volatility_trail import (
    DECISION_STATUSES, WINDOW_STATUSES, _bar_issue, _positive, prior_mean_tr_pct20,
)

VERSION = "research-breakout-volatility-trail-v1"
FORMATION_SLOTS = 20
WINDOW_SLOTS = 21
TR_COUNT = 20
MULTIPLIER = 3
FIXED_TRAIL_PCT = .1
PHASES = ("fixed", "volatility")
ACTIVATION_STATUSES = ("activated", "notTriggered", "unknown", "alreadyActive", "skipped")


def _dates(values):
    if not isinstance(values, list) or values != sorted(set(values)):
        raise ValueError("Unique ascending source-calendar dates required")
    for value in values:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError("Canonical YYYY-MM-DD dates required")


def _reference_check(day, previous_day, previous, preclose):
    explicit = day in preclose
    reference = preclose.get(day) if explicit else None
    reason = None
    if not _positive(previous):
        reason = "invalidPreviousCalendarClose"
    elif explicit and not _positive(reference):
        reason = "invalidExplicitPreclose"
    tolerance = max(.005, previous * 1e-7) if _positive(previous) else None
    changed = explicit and abs(reference - previous) > tolerance if reason is None else None
    factor = reference / previous if changed else 1. if reason is None else None
    if factor is not None and not _positive(factor):
        reason, factor, changed = "nonFiniteReferenceScale", None, None
    return {"date": day, "previousDate": previous_day,
            "previousClose": previous if _positive(previous) else None,
            "referenceSource": "precloseByDate" if explicit else "noExplicitPreclose",
            "referenceValue": reference if explicit and _positive(reference) else None,
            "changeThreshold": tolerance, "referenceChanged": changed, "scaleFactor": factor,
            "status": "unknown" if reason else "known", "reason": reason}


def build_formation_anchor(formation_date, entry_date, calendar, bars_by_date, preclose_by_date):
    """Freeze the exact pre-formation raw maximum, transported to entry units.

    Known reference changes inside the twenty-slot maximum make it unknown; we
    do not reconstruct a historical adjusted maximum. From formation through
    entry every calendar slot must exist, so the engine's previous actual bar
    equals the exact preceding slot. No gap can be bridged by an older bar.
    """
    _dates(calendar)
    if not isinstance(bars_by_date, dict) or not isinstance(preclose_by_date, dict):
        raise ValueError("Bar and reference mappings required")
    if formation_date not in calendar or entry_date not in calendar or entry_date < formation_date:
        raise ValueError("Formation and entry must be ordered source-calendar dates")
    fi, ei = calendar.index(formation_date), calendar.index(entry_date)
    prior = calendar[max(0, fi - FORMATION_SLOTS):fi]
    issues, closes, internal = [], [], []
    if len(prior) != FORMATION_SLOTS:
        issues.append({"date": formation_date, "reason": "insufficientPriorCalendarSlots"})
    for day in prior:
        bar = bars_by_date.get(day)
        issue = _bar_issue(bar, day)
        if issue:
            issues.append({"date": day, "reason": issue})
        closes.append({"date": day, "close": bar.get("close") if isinstance(bar, dict) and _positive(bar.get("close")) else None,
                       "barIssue": issue})
    # The first slot needs no earlier reference; only comparisons within the
    # registered maximum window are relevant here.
    for previous_day, day in zip(prior, prior[1:]):
        previous = bars_by_date.get(previous_day)
        checked = _reference_check(day, previous_day,
                                   previous.get("close") if isinstance(previous, dict) else None, preclose_by_date)
        internal.append(checked)
        if checked["reason"] or checked["referenceChanged"]:
            issues.append({"date": day, "reason": checked["reason"] or "internalReferenceChange"})
    raw_max = max(row["close"] for row in closes) if len(prior) == FORMATION_SLOTS and all(row["barIssue"] is None for row in closes) else None
    transported = raw_max if not issues else None
    steps = []
    for index in range(fi, ei + 1):
        day = calendar[index]
        previous_day = calendar[index - 1] if index else None
        bar = bars_by_date.get(day)
        previous = bars_by_date.get(previous_day)
        checked = _reference_check(day, previous_day,
                                   previous.get("close") if isinstance(previous, dict) else None, preclose_by_date)
        issue = _bar_issue(bar, day)
        previous_issue = _bar_issue(previous, previous_day) if previous_day else "missingPreviousCalendarSlot"
        reason = issue or previous_issue or checked["reason"]
        before = transported
        if reason:
            issues.append({"date": day, "reason": "conversion:" + reason})
            transported = None
        elif transported is not None:
            transported *= checked["scaleFactor"]
            if not _positive(transported):
                reason = "nonFiniteTransportedAnchor"
                issues.append({"date": day, "reason": reason})
                transported = None
        steps.append({**checked, "status": "unknown" if reason else "known", "reason": reason,
                      "anchorPriceBefore": before, "anchorPriceAfter": transported})
    return {"formationDate": formation_date, "entryDate": entry_date,
            "asOfDate": prior[-1] if prior else None, "priorDates": list(prior), "priorCloses": closes,
            "rawPrior20MaxClose": raw_max, "internalReferenceChecks": internal,
            "conversionDates": calendar[fi:ei + 1], "conversionSteps": steps,
            "entryAnchorPrice": transported if not issues else None,
            "status": "unknown" if issues else "known", "issues": issues}


class BreakoutVolatilityTrailAccount(FixedSignalAccount):
    def __init__(self, dataset, signals, config, *, entry_weight, fee_policy, state=None):
        if state is not None:
            raise ValueError("BreakoutVolatilityTrailAccount checkpoint restore is not supported")
        expected = {"stopLossPct": .05, "trailingStopPct": 0, "takeProfitPct": 0,
                    "maxHoldingDays": 0, "maxStockWeight": .2}
        if (not isinstance(config, dict) or any(type(config.get(k)) not in (int, float) or config[k] != v for k, v in expected.items())
                or type(entry_weight) not in (int, float) or entry_weight != .2):
            raise ValueError("Breakout trail requires stop5, adapter-owned trailing, no TP/expiry and20percent entry weight")
        if isinstance(dataset, dict) and (dataset.get("corporateActions") or any(
                security.get("corporateActions") for security in dataset.get("securities", []) if isinstance(security, dict))):
            raise ValueError("Declared corporateActions are unsupported by breakoutVolatilityTrail")
        self._trail_positions, self._entry_bindings, self._trail_observations = {}, [], []
        self._registered_parameters = (FORMATION_SLOTS, WINDOW_SLOTS, TR_COUNT, MULTIPLIER, FIXED_TRAIL_PCT)
        super().__init__(dataset, signals, config, entry_weight=entry_weight, fee_policy=fee_policy)

    def _identity_stable(self):
        super()._identity_stable()
        if (FORMATION_SLOTS, WINDOW_SLOTS, TR_COUNT, MULTIPLIER, FIXED_TRAIL_PCT) != self._registered_parameters:
            raise ValueError("Registered breakout-trail parameters changed")

    def _transaction_snapshot(self):
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
        if side == "sell":
            if symbol not in self.state["positions"]:
                self._trail_positions.pop(symbol, None)
            return
        fill = fills[0]
        if fill["positionAction"] != "open" or symbol in self._trail_positions:
            raise ValueError("Breakout trail requires one initial fill and no additions")
        self._trail_positions[symbol] = {"signalId": fill["signalId"], "signalDate": fill["signalDate"],
            "symbol": symbol, "entryDate": day, "entryFillId": fill["fillId"],
            "entryQuantity": fill["quantity"], "entryFillPrice": fill["price"],
            "anchor": None, "anchorRatio": None, "activationDate": None, "ratchetRatio": None}

    def _bind_anchor_at_close(self, binding):
        if binding["anchor"] is not None:
            return
        symbol = binding["symbol"]
        anchor = build_formation_anchor(binding["signalDate"], binding["entryDate"], self.all_dates,
                    {row["date"]: row for row in self.bars[symbol]},
                    self.securities[symbol].get("precloseByDate", {}))
        ratio = anchor["entryAnchorPrice"] / binding["entryFillPrice"] if anchor["status"] == "known" else None
        if ratio is not None and not _positive(ratio):
            anchor["issues"].append({"date": binding["entryDate"], "reason": "nonFiniteAnchorRatio"})
            anchor["status"], anchor["entryAnchorPrice"], ratio = "unknown", None, None
        binding["anchor"], binding["anchorRatio"] = anchor, ratio
        self._entry_bindings.append(deepcopy({key: value for key, value in binding.items()
                                             if key not in ("activationDate", "ratchetRatio")}))

    def _queue_exit(self, day, symbol, reason, evidence):
        self.state["forcedExits"][symbol] = {"signalDate": day, "reason": reason, "riskEvidence": deepcopy(evidence)}
        pending = self.state["pendingTarget"]
        if pending is not None:
            for key in ("weights", "budgets", "priorityBySymbol", "signalDates"):
                pending.get(key, {}).pop(symbol, None)
            pending["quantities"][symbol] = 0
        self._decision(day, symbol, "sell", "queued", reason, signalDate=day,
                       observedClose=evidence.get("observedClose"), riskEvidence=deepcopy(evidence))

    def _fixed_evidence(self, day, position, close):
        # Copy only the frozen engine's percentage-trail evidence shape. The
        # configured engine trail remains disabled; no global config mutation.
        return {"observedDate": day, "observedClose": close,
                "riskEntryPrice": position["riskEntryPrice"], "closePeak": position["closePeak"],
                "gainPct": close / position["riskEntryPrice"] - 1,
                "drawdownFromPeakPct": close / position["closePeak"] - 1,
                "holdingDays": self.calendar_index[day] - self.calendar_index[position["entryDate"]] + 1,
                "stopLossPct": .05, "takeProfitPct": 0, "trailingStopPct": FIXED_TRAIL_PCT,
                "maxHoldingDays": 0, "percentageUnit": "ratio"}

    def _risk_exits(self, day, eligibility):
        existing = set(self.state["forcedExits"])
        super()._risk_exits(day, eligibility)
        index = self.calendar_index[day]
        dates = self.all_dates[max(0, index - WINDOW_SLOTS):index]
        for symbol, position in sorted(self.state["positions"].items()):
            binding = self._trail_positions.get(symbol)
            if binding is None or binding["entryDate"] != position["entryDate"]:
                raise ValueError("Held position lacks its current-entry breakout binding")
            self._bind_anchor_at_close(binding)
            bar, _ = self._bar(symbol, day)
            current_valid = bar is not None and bar.get("date") == day and _positive(bar.get("close")) and _positive(bar.get("volume"))
            window = prior_mean_tr_pct20(day, dates, [self._bar(symbol, d)[0] for d in dates],
                                         self.securities[symbol].get("precloseByDate", {}))
            phase = "volatility" if binding["activationDate"] is not None else "fixed"
            previous = ratio = binding["ratchetRatio"]
            anchor_price = binding["anchorRatio"] * position["riskEntryPrice"] if binding["anchorRatio"] is not None else None
            if anchor_price is not None and not _positive(anchor_price):
                raise ValueError("Finite positive converted anchor expected")
            fixed = candidate_price = candidate = None
            activation = "alreadyActive" if phase == "volatility" else "skipped"
            activated = evaluated = False
            reason_to_queue = None
            order = self.state["forcedExits"].get(symbol)
            if order is not None:
                status = "skippedExistingExit" if symbol in existing else "skippedStandardExit"
                decision_reason = "earlierForcedExitRetained" if symbol in existing else "standardRiskHasPriority"
                activation = "skipped"
            elif not current_valid:
                status, decision_reason = "unknown", "missingOrInvalidCurrentCloseOrVolume"
                activation = "unknown" if phase == "fixed" else "alreadyActive"
            elif phase == "fixed":
                fixed = position["closePeak"] * (1 - FIXED_TRAIL_PCT) / position["riskEntryPrice"]
                if not _positive(fixed):
                    raise ValueError("Finite positive fixed floor expected")
                ratio = fixed if previous is None else max(previous, fixed)
                binding["ratchetRatio"] = ratio
                # Preserve the inherited floating-point comparison exactly.
                if bar["close"] / position["closePeak"] - 1 <= -FIXED_TRAIL_PCT:
                    status, decision_reason, reason_to_queue = "triggered", "fixedTrail10", "trailingStop"
                else:
                    status = "notTriggered"
                    if anchor_price is None:
                        activation, decision_reason = "unknown", "anchorUnknownFixedTrailRetained"
                    elif bar["close"] > anchor_price:
                        binding["activationDate"] = day
                        activated = True
                        activation, decision_reason = "activated", "breakoutActivatedFixedFloorCarried"
                    else:
                        activation, decision_reason = "notTriggered", "fixedTrailBeforeBreakout"
            else:
                if ratio is None:
                    raise ValueError("Activated position lacks its fixed-floor seed")
                if window["windowStatus"] == "known":
                    evaluated = True
                    candidate_price = position["closePeak"] * (1 - window["scaledDistancePct"])
                    candidate = candidate_price / position["riskEntryPrice"]
                    if not _positive(candidate_price) or not _positive(candidate):
                        raise ValueError("Finite positive volatility floor expected")
                    ratio = max(previous, candidate)
                    binding["ratchetRatio"] = ratio
                status = "triggered" if bar["close"] <= ratio * position["riskEntryPrice"] else "notTriggered"
                decision_reason = "knownVolatilityWindow" if evaluated else "carriedThresholdDespiteUnavailableWindow"
                if status == "triggered":
                    reason_to_queue = "breakoutVolatilityTrail"
            threshold = ratio * position["riskEntryPrice"] if ratio is not None else None
            evidence = {"date": day, "symbol": symbol, "signalId": binding["signalId"], "signalDate": binding["signalDate"],
                "entryDate": position["entryDate"], "holdingDays": index - self.calendar_index[position["entryDate"]] + 1,
                "quantity": position["quantity"], "observedClose": bar["close"] if bar else None,
                "observedVolume": bar["volume"] if bar else None, "currentPriceValid": current_valid,
                "riskEntryPrice": position["riskEntryPrice"], "closePeak": position["closePeak"],
                "phaseBefore": phase, "phaseAfter": "volatility" if binding["activationDate"] is not None else "fixed",
                "activationStatus": activation, "activatedToday": activated, "activationDate": binding["activationDate"],
                "anchorStatus": binding["anchor"]["status"], "anchorRatio": binding["anchorRatio"], "anchorPrice": anchor_price,
                "window": window, "volatilityCandidateEvaluated": evaluated,
                "previousRatio": previous, "fixedCandidateRatio": fixed,
                "volatilityCandidatePrice": candidate_price, "volatilityCandidateRatio": candidate,
                "ratchetRatio": ratio, "threshold": threshold, "thresholdKnown": ratio is not None,
                "usedCarriedThreshold": phase == "volatility" and current_valid and order is None and not evaluated,
                "decisionStatus": status, "decisionReason": decision_reason,
                "exitReason": order["reason"] if order else reason_to_queue,
                "exitSignalDate": order["signalDate"] if order else day if reason_to_queue else None}
            self._trail_observations.append(evidence)
            if reason_to_queue:
                self._queue_exit(day, symbol, reason_to_queue,
                                 self._fixed_evidence(day, position, bar["close"]) if reason_to_queue == "trailingStop" else evidence)

    def _trail_result(self):
        decisions = Counter(row["decisionStatus"] for row in self._trail_observations)
        windows = Counter(row["window"]["windowStatus"] for row in self._trail_observations)
        phases = Counter(row["phaseBefore"] for row in self._trail_observations)
        activations = Counter(row["activationStatus"] for row in self._trail_observations)
        return {"version": VERSION, "parameters": {"formationSlots": FORMATION_SLOTS,
            "windowSlots": WINDOW_SLOTS, "trueRangeCount": TR_COUNT, "multiplier": MULTIPLIER,
            "fixedTrailingPct": FIXED_TRAIL_PCT, "windowEnd": "heldCloseT-1"},
            "checkpointRestoreSupported": False, "entryBindings": deepcopy(self._entry_bindings),
            "observations": deepcopy(self._trail_observations), "entryCount": len(self._entry_bindings),
            "activatedEntryCount": sum(row["activatedToday"] for row in self._trail_observations),
            "unknownAnchorEntryCount": sum(row["anchor"]["status"] == "unknown" for row in self._entry_bindings),
            "phaseObservationCounts": {key: phases[key] for key in PHASES},
            "activationObservationCounts": {key: activations[key] for key in ACTIVATION_STATUSES},
            "observationCounts": {key: decisions[key] for key in DECISION_STATUSES},
            "windowCounts": {key: windows[key] for key in WINDOW_STATUSES},
            "priority": "existingForcedExit; inheritedEligibilityDelistSTAndStop5; fixedTrail10ThenActivationOrActiveVolatilityTrail",
            "activationTiming": "firstHeldCloseStrictlyAboveFrozenConvertedAnchorAfterFixedRisk; volatilityCandidatesStartNextHeldClose",
            "windowMeaning": "Prior windows are logged on every held close; used only when phaseBefore=volatility, no existing/standard exit, and valid current close/volume.",
            "referencePolicy": "Anchor and floor ratios remain fixed in inherited riskEntryPrice units; no historical rights reconstruction.",
            "certified": False, "targetAchieved": False}

    def result(self):
        value = super().result()
        value["versions"]["researchBreakoutVolatilityTrail"] = VERSION
        value["research"]["breakoutVolatilityTrail"] = self._trail_result()
        return value

    def checkpoint(self):
        value = super().checkpoint()
        value.update(schema="research-breakout-volatility-trail-descriptive-snapshot-v1", checkpointRestoreSupported=False,
                     breakoutVolatilityTrail=self._trail_result(), currentPositionTrails=deepcopy(self._trail_positions))
        value.pop("digest")
        value["digest"] = _digest(value)
        return value
