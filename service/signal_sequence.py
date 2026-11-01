"""Strict, causal B -> R -> T entry hypothesis; no execution or market fetching.

One ``SignalSequence(symbol)`` consumes successive exchange-session prefixes.
``step(day, calendar_prefix, history_through_day, evidence)`` accepts only bars
and evidence known through ``day``. Evidence contract (all are required):

* calendarEvidence: independent=True, asOf, sourceRefs.
* historicalState: date=day, eligible and suspended booleans, asOf, sourceRefs.
* actionToday: status=noEventsProven/effective/unknown, start=end=day,
  asOf, sourceRefs, events. Effective events identify effectiveDate.
* actionWindow (while idle): start/end/asOf/sourceRefs/priceBasisId/events;
  status=noEventsProven or bridged. Every bridged event explicitly identifies
  effectiveDate, knownByDate, bridgeVerified=True and bridgeSourceRefs.

Action proofs are caller-supplied evidence, never inferred from adjustment
factors or unchanged preclose. Every input bar has a priceBasisId matching the
verified window; effective actions invalidate an already waiting sequence.
Unknown idle input is unevaluable, not a failed complete pattern. A known halt
consumes a session without supplying an invented bar or close.

Pattern detection owns no orders or positions. A completed pattern emits one
raw signal and resets to idle; it may start a new pattern next session even if
the symbol is held. Rank every raw signal first, then apply the caller's active
event exclusions without backfilling. A target exit at today's open therefore
cannot prevent today's raw close signal from entering that ranking.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import math
from statistics import mean


RULES_VERSION = "sequence-breakout-retest-renewal-v1"
BREAKOUT_LOOKBACK = 40
TREND_WINDOW = 80
BREAKOUT_VOLUME_RATIO = 1.2
SEQUENCE_DEADLINE = 10
RETEST_ANCHOR_BAND = .02
RETEST_VOLUME_FRACTION = .8
RENEWAL_VOLUME_FRACTION = 1.
DAILY_TOP_N = 5
WAITING = {"waitingForRetest", "waitingForRenewal"}


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Dates must be YYYY-MM-DD")
    return value


def _number(value, *, positive=False):
    try:
        return type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0)
    except OverflowError:
        return False


def _sources(values):
    return isinstance(values, list) and bool(values) and all(isinstance(value, str) and value.strip() for value in values)


def _proof(proof, day):
    if not isinstance(proof, dict) or not _sources(proof.get("sourceRefs")):
        return False
    try:
        return _day(proof.get("asOf")) <= day
    except (ValueError, TypeError):
        return False


def _action_proof(proof, first, day):
    if not _proof(proof, day):
        return False
    try:
        return _day(proof.get("start")) <= first and _day(proof.get("end")) == day
    except (ValueError, TypeError):
        return False


def _valid_bar(bar):
    return (isinstance(bar, dict) and all(_number(bar.get(key), positive=True) for key in ("open", "high", "low", "close"))
            and _number(bar.get("volume")) and isinstance(bar.get("priceBasisId"), str) and bool(bar["priceBasisId"])
            and max(bar[key] for key in ("open", "high", "low", "close")) <= 1e8 and bar["volume"] <= 1e15
            and bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"])


class SignalSequence:
    def __init__(self, symbol):
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("A symbol is required")
        self.symbol = symbol
        self._state = {"name": "idle"}
        self._calendar = ()
        self._events, self._signals = [], []

    @property
    def state(self):
        return deepcopy(self._state)

    @property
    def events(self):
        return deepcopy(self._events)

    @property
    def signals(self):
        return deepcopy(self._signals)

    def step(self, day, calendar_prefix, history_through_day, evidence):
        day = _day(day)
        calendar = tuple(_day(value) for value in calendar_prefix)
        if not calendar or calendar[-1] != day or list(calendar) != sorted(set(calendar)):
            raise ValueError("Calendar must be an ordered unique prefix ending at today's session")
        if self._calendar and calendar[:-1] != self._calendar:
            raise ValueError("Supply exactly the next exchange session with an unchanged calendar prefix")
        if not isinstance(history_through_day, (list, tuple)) or not isinstance(evidence, dict):
            raise ValueError("History must be a list and evidence an object")
        history, previous = {}, ""
        for bar in history_through_day:
            if not isinstance(bar, dict):
                raise ValueError("Each historical bar must be an object")
            observed_day = _day(bar.get("date"))
            if observed_day <= previous or observed_day > day or observed_day not in calendar:
                raise ValueError("History must be strictly ordered, on the supplied calendar and never future dated")
            history[observed_day], previous = bar, observed_day
        before = self.state
        self._calendar = calendar
        elapsed = len(calendar) - 1 - before["anchorIndex"] if before["name"] in WAITING else None

        def finish(kind, reason, facts=None, signal=None):
            event = {"rulesVersion": RULES_VERSION, "symbol": self.symbol, "date": day,
                     "kind": kind, "reason": reason, "elapsedSessions": elapsed,
                     "before": before, "after": self.state, "facts": deepcopy(facts or {}),
                     "inputEvidence": deepcopy(evidence)}
            self._events.append(deepcopy(event))
            if signal is not None:
                self._signals.append(deepcopy(signal))
            return {"symbol": self.symbol, "date": day, "state": self.state,
                    "transitions": [event], "signal": deepcopy(signal)}

        def unknown(reason, facts=None):
            waiting = before["name"] in WAITING
            if waiting:
                self._state = {"name": "idle"}
            return finish("abortedUnknown" if waiting else "unevaluable", reason, facts)

        def invalidate(reason, facts=None):
            self._state = {"name": "idle"}
            return finish("invalidated" if before["name"] in WAITING else "ineligible", reason, facts)

        calendar_proof = evidence.get("calendarEvidence")
        if not _proof(calendar_proof, day) or calendar_proof.get("independent") is not True:
            return unknown("independentCalendarUnknown")
        state_proof = evidence.get("historicalState")
        if (not _proof(state_proof, day) or state_proof.get("date") != day or type(state_proof.get("eligible")) is not bool
                or type(state_proof.get("suspended")) is not bool):
            return unknown("historicalStateUnknown")
        action = evidence.get("actionToday")
        if not _action_proof(action, day, day) or action.get("start") != day:
            return unknown("currentActionEvidenceUnknown")
        action_status = action.get("status")
        if action_status == "effective":
            events = action.get("events")
            if not isinstance(events, list) or not any(row.get("effectiveDate") == day for row in events if isinstance(row, dict)):
                return unknown("effectiveActionDateUnknown")
            return invalidate("effectiveCorporateAction")
        if action_status != "noEventsProven" or action.get("events") != []:
            return unknown("currentActionCompletenessUnknown")
        if not state_proof["eligible"]:
            return invalidate("historicalSecurityIneligible")
        bar = history.get(day)
        if state_proof["suspended"]:
            if isinstance(bar, dict) and _number(bar.get("volume"), positive=True):
                return unknown("suspensionContradictsPositiveVolume")
            if before["name"] in WAITING:
                # If a verified zero-volume close exists, keep that known
                # session close. Otherwise no prior close is invented.
                self._state["previousClose"] = (bar["close"] if _valid_bar(bar) and bar["volume"] == 0
                                               and bar["priceBasisId"] == before["priceBasisId"] else None)
                if elapsed >= SEQUENCE_DEADLINE:
                    self._state = {"name": "idle"}
                    return finish("expired", "deadlineDuringKnownSuspension")
            return finish("held" if before["name"] in WAITING else "notTriggered", "knownSuspensionConsumesSession")
        if not _valid_bar(bar) or bar["volume"] <= 0:
            return unknown("currentTradingBarUnknown")

        if before["name"] == "idle":
            if len(calendar) < TREND_WINDOW + 1:
                return unknown("insufficient81SessionHistory")
            dates = calendar[-(TREND_WINDOW + 1):]
            window = [history.get(value) for value in dates]
            if any(not _valid_bar(row) or row["volume"] <= 0 for row in window):
                return unknown("incomplete81SessionHistory")
            proof = evidence.get("actionWindow")
            if not _action_proof(proof, dates[0], day):
                return unknown("historicalActionWindowUnknown")
            basis, events = proof.get("priceBasisId"), proof.get("events")
            if not isinstance(events, list) or basis is None or any(row["priceBasisId"] != basis for row in window):
                return unknown("historicalPriceBasisUnknown")
            if proof.get("status") == "noEventsProven":
                if events:
                    return unknown("contradictoryNoActionProof")
            elif proof.get("status") == "bridged":
                if not events:
                    return unknown("emptyActionBridgeProof")
                for event in events:
                    try:
                        valid = (isinstance(event, dict) and dates[0] <= _day(event.get("effectiveDate")) <= day
                                 and _day(event.get("knownByDate")) <= day and event.get("bridgeVerified") is True
                                 and _sources(event.get("bridgeSourceRefs")))
                    except (ValueError, TypeError):
                        valid = False
                    if not valid:
                        return unknown("historicalActionBridgeUnknown")
            else:
                return unknown("historicalActionBridgeUnknown")
            prior = window[-BREAKOUT_LOOKBACK - 1:-1]
            anchor = max(row["high"] for row in prior)
            mean_volume = mean(row["volume"] for row in prior)
            ma = mean(row["close"] for row in window[1:])
            prior_ma = mean(row["close"] for row in window[:-1])
            facts = {"historyStart": dates[0], "historyEnd": day, "observations": len(window),
                     "anchor": anchor, "close": bar["close"], "ma80": ma, "previousMa80": prior_ma,
                     "volumeRatio40": bar["volume"] / mean_volume, "priceBasisId": basis}
            if not (bar["close"] > anchor and bar["close"] > ma > prior_ma
                    and facts["volumeRatio40"] >= BREAKOUT_VOLUME_RATIO):
                return finish("notTriggered", "breakoutConditionsNotMet", facts)
            self._state = {"name": "waitingForRetest", "anchorDate": day, "anchorIndex": len(calendar) - 1,
                           "anchor": anchor, "breakoutClose": bar["close"], "breakoutVolume": bar["volume"],
                           "priceBasisId": basis, "previousClose": bar["close"], "deadlineOffset": SEQUENCE_DEADLINE}
            return finish("anchored", "defaultBreakoutConfirmed", facts)

        if bar["priceBasisId"] != before["priceBasisId"]:
            return unknown("waitingPriceBasisChanged")
        facts = {key: bar[key] for key in ("open", "high", "low", "close", "volume")}
        if bar["low"] < before["anchor"]:
            return invalidate("anchorBrokenBeforeEntry", facts)
        self._state["previousClose"] = bar["close"]
        if before["name"] == "waitingForRetest" and elapsed < SEQUENCE_DEADLINE:
            if (bar["low"] <= before["anchor"] * (1 + RETEST_ANCHOR_BAND)
                    and bar["close"] < before["breakoutClose"]
                    and bar["volume"] <= before["breakoutVolume"] * RETEST_VOLUME_FRACTION):
                self._state.update(name="waitingForRenewal", retestDate=day, retestHigh=bar["high"])
                return finish("retested", "firstQuietRetestFrozen", facts)
        elif before["name"] == "waitingForRenewal":
            if before.get("previousClose") is None:
                return unknown("previousSessionCloseUnknown", facts)
            if (bar["close"] > before["retestHigh"] and bar["close"] > before["previousClose"]
                    and bar["volume"] >= before["breakoutVolume"] * RENEWAL_VOLUME_FRACTION):
                signal = {"rulesVersion": RULES_VERSION, "symbol": self.symbol, "signalDate": day,
                          "eligible": True, "anchorDate": before["anchorDate"], "retestDate": before["retestDate"],
                          "anchor": before["anchor"], "confirmedClose": bar["close"],
                          "distanceFromAnchor": bar["close"] / before["anchor"] - 1,
                          "entryRule": "oneAttemptNextExchangeSessionOpen;TPlusOneSellLock", "targetAchieved": False}
                self._state = {"name": "idle"}
                return finish("signaled", "renewedStrengthAfterRetest", facts, signal)
        if elapsed >= SEQUENCE_DEADLINE:
            self._state = {"name": "idle"}
            return finish("expired", "sequenceDeadlineReached", facts)
        return finish("held", "waitingForNextStage", facts)

def rank_signals(signals):
    """Rank raw same-day signals; active-event exclusions happen AFTER Top5.

    No portfolio state is accepted here, so a held symbol cannot silently free
    its rank for a lower-ranked candidate. Return all rows for auditability.
    """
    rows = [deepcopy(row) for row in signals if isinstance(row, dict) and row.get("eligible") is True]
    seen, days = set(), set()
    for row in rows:
        if (not isinstance(row.get("symbol"), str) or not row["symbol"] or row["symbol"] in seen
                or not _number(row.get("anchor"), positive=True) or not _number(row.get("confirmedClose"), positive=True)):
            raise ValueError("Qualified signals require unique symbols and finite positive prices")
        seen.add(row["symbol"])
        days.add(_day(row.get("signalDate")))
        row["distanceFromAnchor"] = row["confirmedClose"] / row["anchor"] - 1
        if not math.isfinite(row["distanceFromAnchor"]):
            raise ValueError("Signal distance overflow")
    if len(days) > 1:
        raise ValueError("Rank signals from a single exchange session")
    rows.sort(key=lambda row: (row["distanceFromAnchor"], row["symbol"]))
    for rank, row in enumerate(rows, 1):
        row.update(rank=rank, selectedTop5=rank <= DAILY_TOP_N)
    return rows
