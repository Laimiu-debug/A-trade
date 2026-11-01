"""Two fixed close-time research exits; inherited fills, costs and lots are unchanged.

These are raw-price rules, not net-profit guarantees. Snapshots are descriptive
only: this small adapter deliberately does not implement checkpoint restoration.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import math

from .portfolio_engine import _digest
from .research_fixed_signal_account import FixedSignalAccount

VERSION = "research-structural-exits-v1"
RULES = ("structure10", "noProgress5")


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _structure_check(day, prior_days, current, prior_bars):
    """Use exact preceding calendar slots; never compress missing sessions."""
    unknown = []
    if current is None or current.get("date") != day or not _positive(current.get("close")) or not _positive(current.get("volume")):
        unknown.append({"date": day, "reason": "missingOrInvalidCurrentCloseOrVolume"})
    if len(prior_days) != 10:
        unknown.append({"date": day, "reason": "insufficientPriorCalendarSessions"})
    lows = []
    for prior_day, bar in zip(prior_days, prior_bars):
        if bar is None or bar.get("date") != prior_day:
            unknown.append({"date": prior_day, "reason": "missingDailyBar"})
        elif not _positive(bar.get("volume")):
            unknown.append({"date": prior_day, "reason": "nonPositiveOrInvalidVolume"})
        elif not _positive(bar.get("low")):
            unknown.append({"date": prior_day, "reason": "invalidLow"})
        else:
            lows.append({"date": prior_day, "low": bar["low"]})
    if len(prior_bars) != len(prior_days):
        unknown.append({"date": day, "reason": "missingWindowSlots"})
    threshold = min(row["low"] for row in lows) if not unknown else None
    return {"status": "unknown" if unknown else "triggered" if current["close"] < threshold else "notTriggered",
            "priorDates": list(prior_days), "priorLows": lows, "prior10Low": threshold,
            "comparison": "closeStrictlyLessThanPrior10Low", "unknown": unknown}


class ResearchExitAccount(FixedSignalAccount):
    def __init__(self, dataset, signals, config, *, exit_rule, entry_weight, fee_policy, state=None):
        if state is not None:
            raise ValueError("ResearchExitAccount checkpoint restore is not supported")
        if exit_rule not in RULES:
            raise ValueError("Unsupported research exit rule")
        expected = {"stopLossPct": .05, "trailingStopPct": 0 if exit_rule == "structure10" else .1,
                    "takeProfitPct": 0, "maxHoldingDays": 0}
        if not isinstance(config, dict) or any(type(config.get(k)) not in (int, float) or config[k] != v for k, v in expected.items()):
            raise ValueError("Explicit exit configuration differs from the research rule")
        self.exit_rule = self._registered_exit_rule = exit_rule
        self._extra_risk_observations = []
        super().__init__(dataset, signals, config, entry_weight=entry_weight, fee_policy=fee_policy)

    def _identity_stable(self):
        super()._identity_stable()
        if self.exit_rule != self._registered_exit_rule:
            raise ValueError("Research exit rule changed")

    def _transaction_snapshot(self):
        return super()._transaction_snapshot(), len(self._extra_risk_observations)

    def _transaction_restore(self, snapshot):
        base, length = snapshot
        super()._transaction_restore(base)
        del self._extra_risk_observations[length:]

    def _queue_extra_exit(self, day, symbol, evidence):
        # Match the inherited close-to-next-open forced-exit seam; never fill here.
        self.state["forcedExits"][symbol] = {"signalDate": day, "reason": self.exit_rule,
                                               "riskEvidence": deepcopy(evidence)}
        pending = self.state["pendingTarget"]
        if pending is not None:
            for key in ("weights", "budgets", "priorityBySymbol", "signalDates"):
                pending.get(key, {}).pop(symbol, None)
            pending["quantities"][symbol] = 0
        self._decision(day, symbol, "sell", "queued", self.exit_rule, signalDate=day,
                       observedClose=evidence["observedClose"], riskEvidence=deepcopy(evidence))

    def _risk_exits(self, day, eligibility):
        super()._risk_exits(day, eligibility)
        index = self.calendar_index[day]
        for symbol, position in sorted(self.state["positions"].items()):
            holding_days = index - self.calendar_index[position["entryDate"]] + 1
            if self.exit_rule == "noProgress5" and holding_days != 5:
                continue
            bar, _ = self._bar(symbol, day)
            evidence = {"date": day, "symbol": symbol, "rule": self.exit_rule,
                        "entryDate": position["entryDate"], "holdingDays": holding_days,
                        "observedClose": bar["close"] if bar else None,
                        "riskEntryPrice": position["riskEntryPrice"],
                        "priceReferenceMeaning": "entryFillPriceIncludingSlippageExcludingFees"}
            existing = self.state["forcedExits"].get(symbol)
            if existing is not None:
                evidence.update(status="skippedExistingExit", existingReason=existing["reason"],
                                existingSignalDate=existing["signalDate"])
            elif self.exit_rule == "structure10":
                days = self.all_dates[max(0, index - 10):index]
                evidence.update(_structure_check(day, days, bar, [self._bar(symbol, d)[0] for d in days]))
            else:
                known = bar is not None and _positive(bar.get("close")) and _positive(bar.get("volume")) and _positive(position["riskEntryPrice"])
                evidence.update(status="unknown" if not known else "triggered" if bar["close"] <= position["riskEntryPrice"] else "notTriggered",
                                comparison="fifthSessionCloseLessThanOrEqualRiskEntryPrice",
                                unknown=[] if known else [{"date": day, "reason": "missingOrInvalidFifthSessionPriceOrVolume"}],
                                retryOnLaterSession=False)
            self._extra_risk_observations.append(evidence)
            if evidence["status"] == "triggered":
                self._queue_extra_exit(day, symbol, evidence)

    def _extra_result(self):
        counts = Counter(row["status"] for row in self._extra_risk_observations)
        return {"version": VERSION, "rule": self.exit_rule, "checkpointRestoreSupported": False,
                "standardRiskHasPriority": True, "signalTiming": "close;earliestSubsequentExecutableOpen",
                "holdingClock": "marketCalendarSessionsIncludingEntry;missingSessionsConsumeTime",
                "window": "prior10CalendarSessionsExcludingToday" if self.exit_rule == "structure10" else "entrySessionPlusFour;noDelayedRetry",
                "observationCounts": {key: counts[key] for key in ("triggered", "notTriggered", "unknown", "skippedExistingExit")},
                "observations": deepcopy(self._extra_risk_observations),
                "certified": False, "targetAchieved": False}

    def result(self):
        value = super().result()
        value["versions"]["researchStructuralExits"] = VERSION
        value["research"]["structuralExits"] = self._extra_result()
        return value

    def checkpoint(self):
        value = super().checkpoint()
        value.update(schema="research-structural-exits-descriptive-snapshot-v1",
                     checkpointRestoreSupported=False, structuralExits=self._extra_result())
        value.pop("digest")
        value["digest"] = _digest(value)
        return value
