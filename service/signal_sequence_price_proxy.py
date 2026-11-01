"""Unadjusted-price-only B/R/T diagnostic, separate from evidence-certified rules.

The caller supplies one current raw bar and an exchange-calendar-aligned vector
of 81 raw bars/None, ending at that bar. Each raw bar is explicitly labelled
priceBasisId='raw-unadjusted' from its decoder. This label is NOT proof that
prices are comparable across corporate actions. No strict evidence certificate
is fabricated or imported. The module has no market I/O or portfolio state.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from functools import lru_cache
import math
from statistics import mean


VERSION = "raw-price-sequence-diagnostic-v1"
CANDIDATE_ID = "rawPriceBreakoutRetestRenewalDiagnostic"
BREAKOUT_LOOKBACK, TREND_WINDOW, DEADLINE = 40, 80, 10
BREAKOUT_VOLUME_RATIO, RETEST_BAND, RETEST_VOLUME_FRACTION = 1.2, .02, .8
RENEWAL_VOLUME_FRACTION, TOP_N = 1., 5
UNKNOWN_REASONS = {"missingDailyBar", "notObservedYet", "invalidHistory", "strictHistoryMissing"}
WAITING = {"waitingForRetest", "waitingForRenewal"}


@lru_cache(maxsize=8192)
def _checked_day(value):
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError("Dates must be YYYY-MM-DD")
    return value


def _day(value):
    if not isinstance(value, str):
        raise ValueError("Dates must be YYYY-MM-DD")
    return _checked_day(value)


def _number(value, *, positive=False):
    try:
        return type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0)
    except OverflowError:
        return False


def _raw_bar(bar):
    return (isinstance(bar, dict) and bar.get("priceBasisId") == "raw-unadjusted"
            and all(_number(bar.get(key), positive=True) and bar[key] <= 1e8 for key in ("open", "high", "low", "close"))
            and _number(bar.get("volume")) and bar["volume"] <= 1e15
            and bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"])


class RawPriceSequence:
    """Pure shape detector. A held symbol still completes and emits patterns.

    step(day, calendar_index, bar, prior81, eligibility, known_suspended=False)
    returns {transition, signal}. Rank all raw completed signals before the
    external runner applies active-event exclusions without replacement.
    """
    def __init__(self, symbol, *, keep_history=True):
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("Symbol is required")
        if type(keep_history) is not bool:
            raise ValueError("keep_history must be boolean")
        self.symbol, self._state = symbol, {"name": "idle"}
        self._keep_history = keep_history
        self._last_index, self._last_day = None, None
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

    def step(self, day, calendar_index, bar, prior81, eligibility, *, known_suspended=False):
        day = _day(day)
        if type(calendar_index) is not int or calendar_index < 0:
            raise ValueError("Calendar index must be a nonnegative integer")
        if self._last_index is not None and (calendar_index != self._last_index + 1 or day <= self._last_day):
            raise ValueError("Advance exactly one exchange session with an increasing date")
        if type(known_suspended) is not bool or not isinstance(eligibility, dict):
            raise ValueError("Known suspension must be boolean and eligibility an object")
        if not isinstance(prior81, (list, tuple)) or len(prior81) != 81 or prior81[-1] != bar:
            raise ValueError("Provide exactly 81 aligned slots with the current bar in the last slot")
        previous = ""
        for row in prior81:
            if row is None:
                continue
            if not isinstance(row, dict):
                raise ValueError("History slots must be bars or None")
            observed = _day(row.get("date"))
            if observed <= previous or observed > day:
                raise ValueError("History dates must be strictly increasing and never future dated")
            previous = observed
        if bar is not None and bar.get("date") != day:
            raise ValueError("Current bar must identify today's session")
        before = self.state
        self._last_day, self._last_index = day, calendar_index
        elapsed = calendar_index - before["anchorIndex"] if before["name"] in WAITING else None
        quality = {"certified": False, "targetAchieved": False, "interpretation": "raw-price-only",
                   "actionEvidence": "unverified", "actionUnverified": True,
                   "historicalEligibilityVerified": False, "comparableRightsVerified": False,
                   "unknownHistoricalFields": deepcopy(eligibility.get("unknown", []))}

        def finish(kind, reason, facts=None, signal=None):
            transition = {"version": VERSION, "candidateId": CANDIDATE_ID, "symbol": self.symbol,
                          "date": day, "calendarIndex": calendar_index, "elapsedSessions": elapsed,
                          "kind": kind, "reason": reason, "before": before, "after": self.state,
                          "facts": deepcopy(facts or {}), "eligibility": deepcopy(eligibility),
                          "knownSuspended": known_suspended, "quality": deepcopy(quality)}
            if self._keep_history:
                self._events.append(deepcopy(transition))
                if signal is not None:
                    self._signals.append(deepcopy(signal))
            return {"transition": transition, "signal": deepcopy(signal)}

        def unknown(reason, facts=None):
            waiting = before["name"] in WAITING
            self._state = {"name": "idle"}
            return finish("abortedUnknown" if waiting else "unevaluable", reason, facts)

        def reject_base():
            self._state = {"name": "idle"}
            return finish("invalidated" if before["name"] in WAITING else "ineligible",
                          "baseEligibilityRejected", {"baseReason": eligibility.get("reason")})

        reason = eligibility.get("reason")
        if type(eligibility.get("eligible")) is not bool:
            return unknown("baseEligibilityUnknown")
        # Explicit loss of qualification still invalidates during suspension.
        # A known halt only explains generic missing/zero-volume observations;
        # it cannot establish otherwise unknown eligibility.
        if not eligibility["eligible"] and reason not in UNKNOWN_REASONS | {"suspendedOrZeroVolume"}:
            return reject_base()
        if reason in UNKNOWN_REASONS and not (known_suspended and reason == "missingDailyBar"):
            return unknown(reason)
        if known_suspended:
            if isinstance(bar, dict) and _number(bar.get("volume"), positive=True):
                return unknown("suspensionContradictsPositiveVolume")
            if before["name"] in WAITING:
                self._state["previousClose"] = bar["close"] if _raw_bar(bar) and bar["volume"] == 0 else None
                if elapsed >= DEADLINE:
                    self._state = {"name": "idle"}
                    return finish("expired", "deadlineDuringKnownSuspension")
            return finish("held" if before["name"] in WAITING else "notTriggered", "knownSuspensionConsumesSession")
        if not _raw_bar(bar) or bar["volume"] <= 0:
            return unknown("currentRawTradingBarUnknown")
        if reason == "suspendedOrZeroVolume":
            return unknown("suspensionStatusUnknown")
        if not eligibility["eligible"]:
            return reject_base()

        if before["name"] == "idle":
            if any(not _raw_bar(row) or row["volume"] <= 0 for row in prior81):
                return unknown("incomplete81SessionRawHistory")
            prior = prior81[-BREAKOUT_LOOKBACK - 1:-1]
            anchor = max(row["high"] for row in prior)
            ma, prior_ma = mean(row["close"] for row in prior81[1:]), mean(row["close"] for row in prior81[:-1])
            previous_volume = mean(row["volume"] for row in prior)
            ratio = bar["volume"] / previous_volume
            facts = {"date": day, "historyStart": prior81[0]["date"], "observations": 81,
                     "anchor": anchor, "prior40High": anchor, "ma80": ma, "previousMa80": prior_ma,
                     "prior40MeanVolume": previous_volume, "volumeRatio40": ratio,
                     **{key: bar[key] for key in ("open", "high", "low", "close", "volume")}}
            if not (bar["close"] > anchor and bar["close"] > ma > prior_ma and ratio >= BREAKOUT_VOLUME_RATIO):
                return finish("notTriggered", "breakoutConditionsNotMet", facts)
            self._state = {"name": "waitingForRetest", "anchorDate": day, "anchorIndex": calendar_index,
                           "anchor": anchor, "breakoutClose": bar["close"], "breakoutVolume": bar["volume"],
                           "previousClose": bar["close"], "deadlineOffset": DEADLINE,
                           "breakoutFacts": deepcopy(facts),
                           "anchorUnknownHistoricalFields": deepcopy(quality["unknownHistoricalFields"])}
            return finish("anchored", "rawPriceBreakoutObserved", facts)

        facts = {key: bar[key] for key in ("open", "high", "low", "close", "volume")}
        if bar["low"] < before["anchor"]:
            self._state = {"name": "idle"}
            return finish("invalidated", "rawAnchorBrokenBeforeEntry", facts)
        self._state["previousClose"] = bar["close"]
        if before["name"] == "waitingForRetest" and elapsed < DEADLINE:
            if (bar["low"] <= before["anchor"] * (1 + RETEST_BAND) and bar["close"] < before["breakoutClose"]
                    and bar["volume"] <= before["breakoutVolume"] * RETEST_VOLUME_FRACTION):
                self._state.update(name="waitingForRenewal", retestDate=day, retestHigh=bar["high"],
                                   retestFacts={"date": day, **facts},
                                   retestUnknownHistoricalFields=deepcopy(quality["unknownHistoricalFields"]))
                return finish("retested", "firstRawQuietRetestFrozen", facts)
        elif before["name"] == "waitingForRenewal":
            if before.get("previousClose") is None:
                return unknown("previousSessionCloseUnknown", facts)
            if (bar["close"] > before["retestHigh"] and bar["close"] > before["previousClose"]
                    and bar["volume"] >= before["breakoutVolume"] * RENEWAL_VOLUME_FRACTION):
                signal = {"version": VERSION, "strategyId": CANDIDATE_ID, "symbol": self.symbol, "signalDate": day,
                          "eligible": True, "anchorDate": before["anchorDate"], "retestDate": before["retestDate"],
                          "anchor": before["anchor"], "breakoutClose": before["breakoutClose"],
                          "breakoutVolume": before["breakoutVolume"], "retestHigh": before["retestHigh"],
                          "confirmedClose": bar["close"], "distanceFromAnchor": bar["close"] / before["anchor"] - 1,
                          "entryRule": "oneAttemptNextExchangeSessionOpen;TPlusOneSellLock",
                          "certified": False, "targetAchieved": False, "actionEvidence": "unverified",
                          "quality": deepcopy(quality),
                          "formationFacts": {"B": deepcopy(before["breakoutFacts"]),
                              "R": deepcopy(before["retestFacts"]),
                              "T": {"date": day, **facts, "priorSessionClose": before["previousClose"]}},
                          "stageUnknownHistoricalFields": {"B": before["anchorUnknownHistoricalFields"],
                              "R": before["retestUnknownHistoricalFields"], "T": quality["unknownHistoricalFields"]}}
                self._state = {"name": "idle"}
                return finish("signaled", "rawRenewedStrengthAfterRetest", facts, signal)
        if elapsed >= DEADLINE:
            self._state = {"name": "idle"}
            return finish("expired", "sequenceDeadlineReached", facts)
        return finish("held", "waitingForNextStage", facts)


def rank_price_signals(signals):
    """Return every eligible raw signal; mark Top5 before external active checks."""
    rows = [deepcopy(row) for row in signals if isinstance(row, dict) and row.get("eligible") is True]
    days, symbols = set(), set()
    for row in rows:
        if (not isinstance(row.get("symbol"), str) or not row["symbol"] or row["symbol"] in symbols
                or not _number(row.get("anchor"), positive=True) or not _number(row.get("confirmedClose"), positive=True)):
            raise ValueError("Signals need unique symbols and finite positive anchor/close")
        days.add(_day(row.get("signalDate")))
        symbols.add(row["symbol"])
        row["distanceFromAnchor"] = row["confirmedClose"] / row["anchor"] - 1
        if not math.isfinite(row["distanceFromAnchor"]):
            raise ValueError("Distance overflow")
    if len(days) > 1:
        raise ValueError("Rank one signal session at a time")
    rows.sort(key=lambda row: (row["distanceFromAnchor"], row["symbol"]))
    for rank, row in enumerate(rows, 1):
        row.update(rank=rank, selectedTop5=rank <= TOP_N)
    return rows
