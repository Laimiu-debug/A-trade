"""Isolated fee-policy event prototype; no strategy runner or actual fee claim.

Only the frozen evaluate body is copied below. Indexes, observations, order
gates, lots and opening-price rules remain inherited. Every event owns its own
fee context. Nothing changes PortfolioEngine, the old study, or their globals.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re

from . import research_fee_profile as profile
from .signal_target_study import (SignalTargetStudy, _Execution, _day, VERSION,
    PROXIES, HALTED, _cent, _quantity, lot_rule, nominal_limit,
    opening_capacity, audit_opening_fills)
from .portfolio_engine import PortfolioEngine

ADAPTER_VERSION = 'signal-target-fee-policy-prototype-v1'
LEGACY_DELEGATED = 'legacy-engine-delegate-v1'
DATED_TRANSFER = profile.DATED_TRANSFER
FROZEN_DEPENDENCIES = {
    'signal_target_study.py': '821c34e58cd9e226a0dc0ab2b7dc4b241eade9364447af95a6b0da362d25c6c4',
    'portfolio_engine.py': 'e00db3e5c9ccd67d808b3ac40bbbcd40bf8272db88f87f16bff2594d893a39c1',
    'portfolio_config.py': '49873041d268f4fba606959e9c794df04a40bd8320af3d8511271800b0221f37',
    'execution_evidence.py': 'e85c3ee91510bf9643ab6c5af1be91f2ff9bf6d70747a28ef5b6bf9bf0b87826',
    'research_fee_profile.py': '4bb9a3f8716cd290a320802d3f9af48cb95ba6a0ff28c111e5fc714663efda72',
}
PURPOSES = {'reserve', 'entryFill', 'targetProbe', 'highObservation', 'exitFill', 'hypotheticalLiquidation'}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def verify_frozen_dependencies():
    for name, expected in FROZEN_DEPENDENCIES.items():
        if hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() != expected:
            raise ValueError('Frozen event dependency changed: '+name)


@dataclass(frozen=True)
class LegacyFeePolicy:
    """Exact old fees, including arbitrary legal old transfer/stamp overrides.

    There is deliberately no commission-inclusion switch: old fees always add
    their configured transfer amount separately. This is a parity reference,
    not the narrower research_fee_profile legacy-static-v1 schedule.
    """
    def settings(self, config):
        return {'profileId': LEGACY_DELEGATED, 'commissionRate': config['commissionRate'],
            'minCommission': config['minCommission'], 'transferFeeRate': config['transferFeeRate'],
            'stampDutyOverride': config['stampDutyRate'], 'commissionIncludesTransfer': False,
            'reservePolicy': 'unchanged-old-next-entry-date', 'legacyFeeDelegation': True,
            'supportedDates': None, 'scopeIndependentlyVerified': False}

    def validate_event(self, config, symbol, signal_day, cutoff):
        return None

    def calculation(self, context, amount, side, effective_date):
        config = context.config
        total = PortfolioEngine._fees(context, amount, side, effective_date)
        stamp = config['stampDutyRate']
        if stamp is None:
            stamp = .0005 if effective_date >= '2023-08-28' else .001
        rates = {'commission': config['commissionRate'], 'transfer': config['transferFeeRate'],
            'chargedStampDuty': stamp if side == 'sell' else 0.}
        return {'breakdown': {'commission': max(config['minCommission'], amount*config['commissionRate']),
            'transfer': amount*config['transferFeeRate'], 'stampDuty': amount*stamp if side == 'sell' else 0,
            'total': total}, 'rates': rates, 'profileSettingsSha256': _digest(self.settings(config)),
            'evidence': {'legacyFeeDelegation': True, 'fullHistoricalCostScheduleVerified': False,
                         'brokerSpecificFeesVerified': False}}


@dataclass(frozen=True, kw_only=True)
class DatedFeePolicy:
    """Explicit unverified 2022-23 ordinary-A share/exchange-auction assumptions.

    The config's old transferFeeRate must be exactly 1e-5 and is a compatibility
    reference only: the dated policy replaces it, never adds to it. Other
    explicit fee assumptions must equal config. The entire signal-to-cutoff
    interval must be supported, even if an early exit might happen before 2024.
    """
    commission_rate: float
    min_commission: float
    commission_includes_transfer: bool
    stamp_duty_override: float | None = None
    security_type: str = 'ordinary-a-share'
    trading_mode: str = 'exchange-auction'

    def __post_init__(self):
        self._breakdown(0., 'buy', '2022-01-01', 'SH')

    def _breakdown(self, amount, side, effective_date, market):
        return profile.fee_breakdown(amount=amount, side=side, execution_date=effective_date,
            market=market, security_type=self.security_type, trading_mode=self.trading_mode,
            profile_id=DATED_TRANSFER, commission_rate=self.commission_rate, min_commission=self.min_commission,
            commission_includes_transfer=self.commission_includes_transfer,
            stamp_duty_override=self.stamp_duty_override)

    def settings(self, config):
        for name, value in (('commissionRate', self.commission_rate), ('minCommission', self.min_commission),
                            ('stampDutyRate', self.stamp_duty_override)):
            if config[name] != value:
                raise ValueError('Config/profile conflict: '+name)
        if config['transferFeeRate'] != .00001:
            raise ValueError('Dated profile requires legacy transfer reference 1e-5; arbitrary override conflicts')
        return {'profileId': DATED_TRANSFER, 'commissionRate': self.commission_rate,
            'minCommission': self.min_commission, 'commissionIncludesTransfer': self.commission_includes_transfer,
            'stampDutyOverride': self.stamp_duty_override, 'securityType': self.security_type, 'tradingMode': self.trading_mode,
            'legacyTransferConfigReference': config['transferFeeRate'], 'legacyTransferConfigReplaced': True,
            'reservePolicy': 'signal-date-effective-buy-rate;no-assumed-next-day-announcement-knowledge',
            'supportedDates': [profile.START_DATE, profile.END_DATE], 'scopeIndependentlyVerified': False,
            'exact2022IssuerNoticeRetrieved': False, 'evidenceReviewSha256': profile.REVIEW_SHA256}

    def validate_event(self, config, symbol, signal_day, cutoff):
        self.settings(config)
        if not isinstance(symbol, str) or re.fullmatch(r'(sh|sz)\d{6}', symbol) is None:
            raise ValueError('Dated policy supports declared SH/SZ securities only')
        if not profile.START_DATE <= signal_day <= cutoff <= profile.END_DATE:
            raise ValueError('Dated policy requires signal-through-study-cutoff within 2022-2023')

    def calculation(self, context, amount, side, effective_date):
        return self._breakdown(amount, side, effective_date, context.market)


class FeeExecutionContext(_Execution):
    """Event-local quote and simulated-payment accounting; no shared hooks."""
    def __init__(self, owner, symbol, signal_day, policy):
        super().__init__(owner)
        self.symbol, self.market, self.signal_day, self.policy = symbol, symbol[:2].upper(), signal_day, policy
        self._sizing = False
        settings = policy.settings(self.config)
        self.evidence = {'version': ADAPTER_VERSION, 'policyId': settings['profileId'], 'settings': settings,
            'settingsSha256': _digest(settings), 'symbol': symbol, 'market': self.market,
            'sourcePins': deepcopy(FROZEN_DEPENDENCIES), 'quotes': [], 'paidLedger': [], 'paidFeeTotal': 0.,
            'meaning': 'registered-cost simulation only; quote probes are not paid fees',
            'actualBrokerChargesVerified': False, 'actualOpeningExecutionVerified': False,
            'certified': False, 'targetAchieved': False}

    def quote(self, amount, side, day, *, purpose):
        if purpose not in PURPOSES or side not in ('buy', 'sell'):
            raise ValueError('Explicit fee purpose and side required')
        if (purpose in ('reserve','entryFill')) != (side == 'buy'):
            raise ValueError('Fee purpose/side contradiction')
        if type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0:
            raise ValueError('Fee notional must be finite and nonnegative')
        effective = self.signal_day if purpose == 'reserve' and isinstance(self.policy, DatedFeePolicy) else day
        result = self.policy.calculation(self, amount, side, effective)
        quote = {'quoteId': len(self.evidence['quotes']), 'purpose': purpose, 'side': side,
            'symbol': self.symbol, 'market': self.market, 'amount': amount,
            'quoteEffectiveDate': effective, 'decisionDate': self.signal_day if purpose == 'reserve' else day,
            'executionDate': day if purpose in ('entryFill','exitFill') else None,
            'breakdown': deepcopy(result['breakdown']), 'rates': deepcopy(result['rates']),
            'profileSettingsSha256': result['profileSettingsSha256'], 'evidence': deepcopy(result['evidence']),
            'simulatedFillQuote': purpose in ('entryFill','exitFill')}
        if 'transferDisclosure' in result:
            quote['transferDisclosure'] = deepcopy(result['transferDisclosure'])
        self.evidence['quotes'].append(quote)
        return quote['breakdown']['total']

    def reserve_quantity(self, ceiling, minimum, increment, price, budget, entry_day):
        self._sizing = True
        try:
            return super()._affordable_quantity(ceiling, minimum, increment, price, budget, entry_day)
        finally:
            self._sizing = False

    def _fees(self, amount, side, day):
        if not self._sizing or side != 'buy':
            raise ValueError('Unclassified fee call outside the sizing probe')
        return self.quote(amount, side, day, purpose='reserve')

    def book_last(self, *, quantity, price, purpose):
        if not self.evidence['quotes'] or purpose not in ('entryFill','exitFill'):
            raise ValueError('Only an explicit simulated fill can book fees')
        quote = self.evidence['quotes'][-1]
        expected = 'entryFill' if not self.evidence['paidLedger'] else 'exitFill'
        if (quote['purpose'] != purpose or purpose != expected or len(self.evidence['paidLedger']) >= 2
                or quantity <= 0 or quantity*price != quote['amount']):
            raise ValueError('Duplicate, nonfill or inconsistent fee ledger entry')
        self.evidence['paidLedger'].append({**deepcopy(quote), 'quantity': quantity, 'price': price,
                                           'meaning': 'simulated-paid-fee;not-actual-broker-payment'})
        self.evidence['paidFeeTotal'] = sum(row['breakdown']['total'] for row in self.evidence['paidLedger'])


class FeeProfileSignalTargetStudy(SignalTargetStudy):
    def __init__(self, dataset, config=None, *, fee_policy, budget=100000, target_pct=30,
                 execution_factory=FeeExecutionContext):
        verify_frozen_dependencies()
        if type(fee_policy) not in (LegacyFeePolicy, DatedFeePolicy) or not callable(execution_factory):
            raise ValueError('Explicit supported immutable fee policy and execution factory required')
        super().__init__(dataset, config, budget=budget, target_pct=target_pct)
        self._fee_settings_identity = _digest(fee_policy.settings(self.config))
        self.fee_policy, self.execution_factory = fee_policy, execution_factory
        self._fee_policy_identity = fee_policy
        self._config_identity = _digest(self.config)

    def _make_execution(self, symbol, signal_day):
        if _digest(self.config) != self._config_identity:
            raise ValueError('Normalized event config changed after construction')
        if (self.fee_policy is not self._fee_policy_identity
                or _digest(self.fee_policy.settings(self.config)) != self._fee_settings_identity):
            raise ValueError('Event fee policy changed after construction')
        self.fee_policy.validate_event(self.config, symbol, signal_day, self.end_date)
        context = self.execution_factory(self, symbol, signal_day, self.fee_policy)
        if (not isinstance(context, FeeExecutionContext) or context.symbol != symbol
                or context.signal_day != signal_day or context.market != symbol[:2].upper()
                or context.policy is not self.fee_policy
                or any(getattr(context, name) is not getattr(self, name) for name in
                       ('dataset', 'config', 'securities', 'bars', 'by_date', 'calendar_index'))):
            raise ValueError('Execution factory must return this event fee context')
        return context

    # Copy of frozen evaluate; changes are limited to the explicit fee seams.
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
        context = self._make_execution(symbol, signal_day)
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
        event["feeEvidence"] = context.evidence
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
        planned = context.reserve_quantity(ceiling, minimum, increment, reserve_price, self.budget, entry_day)
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
        fee = context.quote(quantity * price, "buy", entry_day, purpose="entryFill")
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
        context.book_last(quantity=quantity, price=price, purpose="entryFill")
        event["activeUntil"] = self.end_date
        event["quality"]["dataMissing"] = bool(event["observation"]["missingDates"])
        mae = mfe = 0.
        last_close, last_mark = price, entry_day
        observed = 0
        exit_data = None

        def net_return(sell_price, day, purpose):
            proceeds = quantity * sell_price - context.quote(quantity * sell_price, "sell", day, purpose=purpose)
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
                candidate_return, proceeds = net_return(exit_price, day, "targetProbe") if exit_price is not None else (-math.inf, 0)
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
                        exit_data = {"date": day, "price": exit_price, "fee": context.quote(quantity * exit_price, "sell", day, purpose="exitFill"),
                                     "proceeds": proceeds, "netReturnPct": candidate_return,
                                     "openingCapacityVerified": sale["openingCapacityVerified"]}
                        context.book_last(quantity=quantity, price=exit_price, purpose="exitFill")
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
                    high_return, _ = net_return(bar["high"] * (1 - self.config["slippageBps"] / 10000), day, "highObservation")
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
            mark_net, liquidation = net_return(last_close * (1 - self.config["slippageBps"] / 10000), self.end_date, "hypotheticalLiquidation")
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
