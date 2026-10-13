"""Deterministic, resumable, daily-close / next-open A-share research simulator.

No intraday ordering is inferred from OHLC. This module deliberately keeps cash,
integer shares, pending orders and every fill in one chronological ledger.
Unadjusted local files cannot establish total returns or a point-in-time universe.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
import re
from statistics import mean, stdev

from .strategies import (STRATEGY_VERSION, FORCE_STRATEGIES, evaluate, normalize_config,
                         prepare_bars, required_history, strategy_conflicts)
from .execution_evidence import opening_capacity, audit_opening_fills

ENGINE_VERSION = "daily-next-open-v4-execution-evidence"
RULES_VERSION = "ashare-conservative-2026-07-06"


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _cent(value):
    return float(Decimal(str(value)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))


def _finite(value, label, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= 1e20 or not math.isfinite(value):
        raise ValueError(f"{label} 必须是有效有限数值")
    return float(value)


def _day(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("日线包含无效日期") from None
    return value


def board_for(symbol):
    if symbol.startswith("sh68"):
        return "star"
    if symbol.startswith("sz30"):
        return "chinext"
    if symbol.startswith("bj"):
        return "bse"
    return "main"


def lot_rule(symbol):
    """Minimum initial buy, then increment. STAR/BSE can add single shares."""
    return (200, 1) if board_for(symbol) == "star" else ((100, 1) if board_for(symbol) == "bse" else (100, 100))


def _quantity(capacity, minimum, increment):
    value = max(0, math.floor(capacity / increment) * increment)
    return value if value >= minimum else 0


def nominal_limit(symbol, trading_date):
    board = board_for(symbol)
    if board == "bse":
        return .3
    if board == "star" or board == "chinext" and trading_date >= "2020-08-24":
        return .2
    return .1


def execution_limit(security, trading_date):
    band = nominal_limit(security["symbol"], trading_date)
    if band != .1:
        return band
    # Current name is NOT historical ST status. Unknown status uses the tighter
    # historical band. From 2026-07-06 mainland main-board ST bands equal 10%.
    if board_for(security["symbol"]) == "main" and trading_date >= "2026-07-06":
        return .1
    historical = security.get("stByDate", {}).get(trading_date)
    return .1 if historical is False else .05


def _quality_object(value, label):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{label} 须为对象")
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError):
        raise ValueError(f"{label} 含有非法数据") from None
    return deepcopy(value)


def _date_price_map(value, label):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{label} 须为日期到正数的映射")
    result = {}
    for key, raw in value.items():
        _day(key)
        parsed = _finite(raw, label, .000001)
        if parsed > 1e8:
            raise ValueError(f"{label} 超过有效范围")
        result[key] = parsed
    return result


class BacktestEngine:
    def __init__(self, dataset, config=None, state=None):
        self.config = normalize_config(config)
        if not isinstance(dataset, dict) or not isinstance(dataset.get("securities"), list) or not dataset["securities"]:
            raise ValueError("请选择非空股票池及本地日线数据")
        if len(dataset["securities"]) > 100:
            raise ValueError("首版研究股票池最多100只，以便完整审计和恢复")
        if dataset.get("adjustment", "raw") not in ("raw", "unadjusted", "none"):
            raise ValueError("首版仅接受原始未复权日线；复权价格不能用于真实开盘成交与涨跌停判断")
        self.supplied_quality = _quality_object(dataset.get("dataQuality"), "dataQuality")
        self.securities, self.bars, self.by_date = {}, {}, {}
        for original in dataset["securities"]:
            if not isinstance(original, dict):
                raise ValueError("证券数据格式错误")
            symbol = original.get("symbol", "")
            if not isinstance(symbol, str) or not re.fullmatch(r"(?:sh(?:60|68)\d{4}|sz(?:00|30)\d{4}|bj(?:43|83|87|88|92)\d{4})", symbol) or original.get("kind", "stock") != "stock":
                raise ValueError("回测仅支持已识别的沪深北A股代码")
            if symbol in self.securities:
                raise ValueError("股票池包含重复证券")
            bars = original.get("bars")
            if not isinstance(bars, list) or not bars or len(bars) > 20000:
                raise ValueError("每只股票需1..20000条日线")
            clean, previous = [], ""
            for item in bars:
                if not isinstance(item, dict):
                    raise ValueError("日线格式错误")
                trading_date = _day(item.get("date"))
                if trading_date <= previous:
                    raise ValueError("日线日期必须严格递增，不可重复")
                row = {"date": trading_date, **{key: _finite(item.get(key), f"{symbol}.{key}", .000001 if key in ("open", "high", "low", "close") else 0) for key in ("open", "high", "low", "close", "volume")}}
                row["amount"] = _finite(item.get("amount", 0), f"{symbol}.amount")
                if any(row[key] > 1e8 for key in ("open", "high", "low", "close")) or row["volume"] > 1e15 or row["amount"] > 1e20:
                    raise ValueError("日线数值超过可审计的数值范围")
                if not row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]:
                    raise ValueError("日线开高低收关系无效")
                clean.append(row)
                previous = trading_date
            st_history = original.get("stByDate", {})
            if not isinstance(st_history, dict) or any(type(value) is not bool for value in st_history.values()):
                raise ValueError("历史ST状态须为日期到布尔值的映射")
            for trading_date in st_history:
                _day(trading_date)
            self.securities[symbol] = {"symbol": symbol, "name": str(original.get("name", symbol)), "stByDate": deepcopy(st_history),
                                      "precloseByDate": _date_price_map(original.get("precloseByDate"), "precloseByDate"),
                                      "factorByDate": _date_price_map(original.get("factorByDate"), "factorByDate"),
                                      "dataQuality": _quality_object(original.get("dataQuality"), "security.dataQuality")}
            opening = original.get("openingVolumeByDate", {})
            if not isinstance(opening, dict):
                raise ValueError("openingVolumeByDate 须为日期到开盘成交股数的映射")
            self.securities[symbol]["openingVolumeByDate"] = {_day(day): _finite(value, "opening volume") for day, value in opening.items()}
            self.bars[symbol], self.by_date[symbol] = clean, {bar["date"]: index for index, bar in enumerate(clean)}
        self.all_dates = sorted({bar["date"] for bars in self.bars.values() for bar in bars})
        self.calendar_index = {day: i for i, day in enumerate(self.all_dates)}
        self.dates = [day for day in self.all_dates if (not self.config["startDate"] or day >= self.config["startDate"]) and (not self.config["endDate"] or day <= self.config["endDate"])]
        if not self.dates:
            raise ValueError("所选时间范围没有日线数据")
        self.data_digest = _digest({"securities": self.securities, "bars": self.bars, "dataQuality": self.supplied_quality})
        self.prepared = {symbol: prepare_bars(bars, preclose_by_date=self.securities[symbol]["precloseByDate"]) for symbol, bars in self.bars.items()} if any(
            key in FORCE_STRATEGIES for key in self.config["strategyIds"]) else {}
        self.fingerprint = str(dataset.get("fingerprint") or self.data_digest)
        warnings = [
            "候选策略属于研究假设，不是已证明盈利的投资策略；多策略采用AND交集。",
            "真实成交与账本使用原始价格。参考昨收可识别部分除权边界，但未实现分红/送转/配股权益入账，收益仍是价格回报诊断，不是总回报。",
            "股票池由当前显式选择，缺乏历史成分、退市样本和完整历史ST/上市状态，存在生存者偏差与选择偏差。",
            "仅收盘形成信号，下一市场观测日开盘执行；不使用日内高低价推断成交，涨跌停采用保守拒单。",
            "历史ST状态未知时采用较严格价格边界；前5条观测、停牌、缺失后首日及无法解释价格断点拒绝新增成交。",
            "有开盘量证据时约束成交容量，缺失时采用昨日量近似并标记未验证；超出当日总量的成交阻止平原验收。未模拟竞价队列和真实冲击。",
            "基准为所选股票池首个回测日可交易证券等权买入并持有的原始价格理论组合，允许碎股且不计费用；不是市场指数。",
            "佣金、最低佣金、过户费和滑点使用配置中的统一假设；仅自动印花税按已支持历史日期切换，未完整还原历史费率。",
        ]
        if self.config["stampDutyRate"] is None and self.dates[0] < "2008-09-19":
            raise ValueError("自动印花税仅支持2008-09-19起卖方单边征收期间；更早研究须明确指定税率且自行核验")
        supplied_warnings = dataset.get("warnings", [])
        warnings += strategy_conflicts(self.config)
        if isinstance(supplied_warnings, list):
            warnings += [str(item) for item in supplied_warnings]
        self.state = {"engineVersion": ENGINE_VERSION, "rulesVersion": RULES_VERSION, "strategyVersion": STRATEGY_VERSION,
                      "datasetFingerprint": self.fingerprint, "dataDigest": self.data_digest, "config": deepcopy(self.config),
                      "cursor": 0, "cash": self.config["initialCapital"], "positions": {}, "pendingBuys": [], "pendingSells": {},
                      "lastPrices": {}, "lastPriceDates": {}, "quarantined": {}, "curve": [], "trades": [], "decisions": [],
                      "warnings": list(dict.fromkeys(warnings)), "peakEquity": self.config["initialCapital"], "totalFees": 0., "totalSlippage": 0.,
                      "benchmarkUnits": {}, "benchmarkCash": self.config["initialCapital"], "closedPnls": [], "warmupSkips": 0,
                      "corporateActionDates": [], "actionExposures": [],
                      "executionCapacityIssues": [], "unverifiedOpeningFills": []}
        if state is not None:
            if not isinstance(state, dict):
                raise ValueError("回测恢复状态格式错误")
            try:
                json.dumps(state, allow_nan=False)
            except (TypeError, ValueError):
                raise ValueError("回测恢复状态含非法数值") from None
            for key in ("engineVersion", "rulesVersion", "strategyVersion", "datasetFingerprint", "dataDigest", "config"):
                if state.get(key) != self.state[key]:
                    raise ValueError("回测配置、数据快照或版本已变化，不能恢复旧状态")
            if isinstance(state.get("cursor"), bool) or not isinstance(state.get("cursor"), int) or not 0 <= state["cursor"] <= len(self.dates):
                raise ValueError("恢复进度无效")
            if set(state) != set(self.state) or len(state["curve"]) != state["cursor"]:
                raise ValueError("恢复状态不完整")
            self.state = deepcopy(state)

    @property
    def progress(self):
        return {"progress": self.state["cursor"] / len(self.dates), "phase": "completed" if self.state["cursor"] == len(self.dates) else "backtesting", "completedDays": self.state["cursor"], "totalDays": len(self.dates)}

    def checkpoint(self):
        return deepcopy(self.state)

    def step(self, days=5):
        if isinstance(days, bool) or not isinstance(days, int) or days < 1 or days > 20000:
            raise ValueError("推进天数必须是1..20000的整数")
        for _ in range(min(days, len(self.dates) - self.state["cursor"])):
            self._step_day(self.dates[self.state["cursor"]])
            self.state["cursor"] += 1
        return self.state["cursor"] == len(self.dates)

    def _decision(self, day, symbol, action, status, reason, **extra):
        self.state["decisions"].append({"date": day, "symbol": symbol, "name": self.securities[symbol]["name"], "action": action, "status": status, "reason": reason, **extra})

    def _warning(self, text):
        if text not in self.state["warnings"]:
            self.state["warnings"].append(text)

    def _bar(self, symbol, day):
        index = self.by_date[symbol].get(day)
        return (self.bars[symbol][index], index) if index is not None else (None, None)

    def _reference_close(self, symbol, index):
        row = self.bars[symbol][index]
        supplied = self.securities[symbol]["precloseByDate"].get(row["date"])
        if supplied is not None:
            return supplied
        return self.bars[symbol][index-1]["close"] if index else None

    def _reference_changed(self, symbol, index):
        if not index:
            return False
        previous = self.bars[symbol][index-1]["close"]
        reference = self.securities[symbol]["precloseByDate"].get(self.bars[symbol][index]["date"])
        return reference is not None and abs(reference-previous) > max(.005, previous*1e-7)

    def _observe_actions_before_open(self, day):
        """Record reference changes before sales erase overnight exposure.

        A changed ex-date reference is not evidence of the type, quantity or
        payment date of a corporate action. It never credits cash or shares.
        """
        s = self.state
        for symbol in sorted(self.securities):
            bar, index = self._bar(symbol, day)
            if bar is None or not self._reference_changed(symbol, index):
                continue
            event = {"symbol": symbol, "date": day, "previousRawClose": self.bars[symbol][index-1]["close"],
                     "referencePreclose": self._reference_close(symbol, index), "evidence": "referencePriceDifference"}
            s["corporateActionDates"].append(event)
            quantity = s["positions"].get(symbol, {}).get("quantity", 0)
            benchmark_units = s["benchmarkUnits"].get(symbol, 0.)
            self._decision(day, symbol, "corporateAction", "observed", "referencePriceChanged", **{key: value for key, value in event.items() if key not in ("symbol", "date")},
                           strategyQuantity=quantity, benchmarkUnits=benchmark_units)
            if quantity or benchmark_units:
                exposure = {**event, "strategyQuantity": quantity, "benchmarkUnits": benchmark_units}
                s["actionExposures"].append(exposure)
                self._warning(f"{symbol} {day} 参考昨收变化时存在隔夜持仓或基准暴露；公司行动类型/权益数量/到账日未核验，现金与股数未调整，相关收益与超额收益不可作总回报结论。")

    def _gate(self, symbol, day, side):
        bar, index = self._bar(symbol, day)
        if symbol in self.state["quarantined"]:
            return "rawPriceDiscontinuity"
        if bar is None:
            return "missingBar"
        if bar["volume"] <= 0:
            return "suspended"
        if index < 5:
            return "listingHistoryUnknown"
        previous = self.bars[symbol][index-1]
        if self.calendar_index[day] - self.calendar_index[previous["date"]] != 1 or previous["volume"] <= 0:
            return "missingPreviousTradingBar"
        if day < "1996-12-16" or board_for(symbol) == "bse" and day < "2021-11-15":
            return "unsupportedHistoricalRules"
        reference = self._reference_close(symbol, index)
        if abs(bar["open"] / reference - 1) > nominal_limit(symbol, day) + .005:
            self.state["quarantined"][symbol] = day
            self._warning(f"{symbol} {day} 开盘出现无法解释的原始价格断点，后续成交隔离；如有持仓，估值与收益不可作为有效结论。")
            return "rawPriceDiscontinuity"
        band = execution_limit(self.securities[symbol], day)
        upper, lower = _cent(reference * (1 + band)), _cent(reference * (1 - band))
        if side == "buy" and bar["open"] >= upper - .000001:
            return "openAtUpperLimitOrUncertainST"
        if side == "sell" and bar["open"] <= lower + .000001:
            return "openAtLowerLimitOrUncertainST"
        return None

    def _fees(self, amount, side, day):
        c = self.config
        tax = c["stampDutyRate"]
        if tax is None:
            tax = .0005 if day >= "2023-08-28" else .001
        return round((max(c["minCommission"], amount * c["commissionRate"]) + amount * c["transferFeeRate"] + (amount * tax if side == "sell" else 0)) * c["costMultiplier"], 8)

    def _price(self, opening, side):
        slip = self.config["slippageBps"] * self.config["costMultiplier"] / 10000
        return opening * (1 + slip if side == "buy" else 1 - slip)

    def _slippage_rejection(self, symbol, day, side, price):
        _, index = self._bar(symbol, day)
        previous_close = self._reference_close(symbol, index)
        # Unknown historical ST remains a conservative bound, explicitly NOT
        # a claim that the normal stock's legal limit was five percent.
        band = execution_limit(self.securities[symbol], day)
        upper, lower = _cent(previous_close * (1 + band)), _cent(previous_close * (1 - band))
        if side == "buy" and price > upper + 1e-8 or side == "sell" and price < lower - 1e-8:
            return "slippageBeyondLimitOrUncertainST"
        return None

    def _record_fill(self, day, order, side, quantity, opening, price, fee, realized=None):
        amount, slippage = quantity * price, quantity * abs(price - opening)
        self.state["totalFees"] += fee
        self.state["totalSlippage"] += slippage
        fill = {"date": day, "signalDate": order["signalDate"], "symbol": order["symbol"], "name": self.securities[order["symbol"]]["name"],
                "side": side, "quantity": quantity, "price": round(price, 8), "amount": round(amount, 8), "fee": fee, "slippage": round(slippage, 8),
                "cashAfter": round(self.state["cash"], 8), "reason": order["reason"], "realizedPnl": round(realized, 8) if realized is not None else None}
        self.state["trades"].append(fill)
        self._decision(day, order["symbol"], side, "filled", order["reason"], signalDate=order["signalDate"], quantity=quantity, price=round(price, 8))

    def _sell(self, day, symbol, order):
        position = self.state["positions"].get(symbol)
        if position is None:
            self.state["pendingSells"].pop(symbol, None)
            return False
        reason = "tPlusOne" if day <= position["entryDate"] else self._gate(symbol, day, "sell")
        if reason:
            self._decision(day, symbol, "sell", "deferred", reason, signalDate=order["signalDate"])
            return False
        bar, index = self._bar(symbol, day)
        price = self._price(bar["open"], "sell")
        rejection = self._slippage_rejection(symbol, day, "sell", price)
        if rejection:
            self._decision(day, symbol, "sell", "deferred", rejection, signalDate=order["signalDate"])
            return False
        minimum, increment = lot_rule(symbol)
        remaining = order.get("remainingQuantity")
        if remaining is None:
            if order["reason"] == "partialTakeProfit":
                remaining = math.floor(position["quantity"] * self.config["partialSellFraction"] / increment) * increment
                # A sub-lot remainder can only be liquidated completely; do not
                # invent a partial fill or lose the retained lot in accounting.
                if remaining < minimum or position["quantity"] - remaining < minimum:
                    self._decision(day, symbol, "sell", "rejected", "partialBelowBoardMinimum", signalDate=order["signalDate"])
                    self.state["pendingSells"].pop(symbol, None)
                    return False
            else:
                remaining = position["quantity"]
            order["remainingQuantity"] = remaining
        capacity = opening_capacity(self.securities[symbol], day, self.bars[symbol][index-1]["volume"], self.config["maxParticipationRate"])
        requested = min(remaining, position["quantity"])
        if requested <= capacity and requested == position["quantity"]:
            quantity = requested  # final odd-lot liquidation is allowed
        else:
            quantity = _quantity(min(requested, capacity), minimum, increment)
        if quantity <= 0:
            self._decision(day, symbol, "sell", "deferred", "previousVolumeCapacity", signalDate=order["signalDate"])
            return False
        amount = quantity * price
        fee = self._fees(amount, "sell", day)
        if self.state["cash"] + amount - fee < -1e-8:
            self._decision(day, symbol, "sell", "deferred", "insufficientCashForSellFees", signalDate=order["signalDate"])
            return False
        allocated_cost = position["costBasis"] * quantity / position["quantity"]
        pnl = amount - fee - allocated_cost
        self.state["cash"] += amount - fee
        position["costBasis"] -= allocated_cost
        position["quantity"] -= quantity
        position["realizedPnl"] += pnl
        order["remainingQuantity"] -= quantity
        if order["reason"] == "partialTakeProfit":
            position["partialStage"] = True  # only after an actual confirmed fill
        self._record_fill(day, order, "sell", quantity, bar["open"], price, fee, pnl)
        if not position["quantity"]:
            self.state["closedPnls"].append(position["realizedPnl"])
            del self.state["positions"][symbol]
        if order["remainingQuantity"] <= 0 or symbol not in self.state["positions"]:
            self.state["pendingSells"].pop(symbol, None)
        elif order["reason"] == "partialTakeProfit" and order["remainingQuantity"] < minimum:
            self._decision(day, symbol, "sell", "cancelled", "partialRemainingBelowMinimum", signalDate=order["signalDate"], unfilledQuantity=order["remainingQuantity"])
            self.state["pendingSells"].pop(symbol, None)
        return True

    def _buy(self, day, order, sold_today):
        symbol = order["symbol"]
        reason = None
        if symbol in self.state["positions"] or symbol in sold_today:
            reason = "alreadyHeldOrSoldToday"
        elif len(self.state["positions"]) >= self.config["maxPositions"]:
            reason = "positionLimit"
        else:
            reason = self._gate(symbol, day, "buy")
        if reason:
            self._decision(day, symbol, "buy", "rejected", reason, signalDate=order["signalDate"])
            return
        bar, index = self._bar(symbol, day)
        minimum, increment = lot_rule(symbol)
        price = self._price(bar["open"], "buy")
        rejection = self._slippage_rejection(symbol, day, "buy", price)
        if rejection:
            self._decision(day, symbol, "buy", "rejected", rejection, signalDate=order["signalDate"])
            return
        budget = min(order["budget"], self.state["cash"])
        capacity = opening_capacity(self.securities[symbol], day, self.bars[symbol][index-1]["volume"], self.config["maxParticipationRate"])
        quantity = _quantity(min(budget / price, capacity), minimum, increment)
        # Binary search avoids a linear scan for tiny prices / very large cash.
        low, high, best = 0, quantity // increment, 0
        while low <= high:
            lots = (low + high) // 2
            candidate = lots * increment
            cost = candidate * price + self._fees(candidate * price, "buy", day)
            if cost <= budget + 1e-8:
                best, low = candidate, lots + 1
            else:
                high = lots - 1
        quantity = best if best >= minimum else 0
        if not quantity:
            self._decision(day, symbol, "buy", "rejected", "cashOrPreviousVolumeBelowMinimum", signalDate=order["signalDate"])
            return
        fee = self._fees(quantity * price, "buy", day)
        cost = quantity * price + fee
        self.state["cash"] -= cost
        if -1e-7 < self.state["cash"] < 0:
            self.state["cash"] = 0.
        self.state["positions"][symbol] = {"symbol": symbol, "name": self.securities[symbol]["name"], "quantity": quantity,
                                             "entryDate": day, "entryCursor": self.state["cursor"], "entryPrice": price, "costBasis": cost,
                                             "closePeak": price, "partialStage": False, "realizedPnl": 0.}
        self.state["lastPrices"][symbol] = price
        self.state["lastPriceDates"][symbol] = day
        self._record_fill(day, order, "buy", quantity, bar["open"], price, fee)

    def _history_ready(self, symbol, index):
        max_window = required_history(self.config)
        if index < max(max_window, 5):
            return False
        bars = self.bars[symbol]
        if self.calendar_index[bars[index]["date"]] - self.calendar_index[bars[index-max_window]["date"]] != max_window:
            return False
        for i in range(index-max_window+1, index+1):
            if abs(bars[i]["close"] / self._reference_close(symbol, i) - 1) > nominal_limit(symbol, bars[i]["date"]) + .005:
                return False
            if self._reference_changed(symbol, i) and any(key not in FORCE_STRATEGIES for key in self.config["strategyIds"]):
                # Until the factor semantics are independently verified, raw
                # trend windows spanning known reference changes are unusable.
                return False
        return True

    def _exit_signal(self, day, symbol, position):
        bar, _ = self._bar(symbol, day)
        if not bar or bar["volume"] <= 0:
            return
        c = self.config
        close = bar["close"]
        position["closePeak"] = max(position["closePeak"], close)
        gain = close / position["entryPrice"] - 1
        reason = None
        if c["stopLossPct"] and gain <= -c["stopLossPct"]:
            reason = "stopLoss"
        elif c["trailingStopPct"] and close / position["closePeak"] - 1 <= -c["trailingStopPct"]:
            reason = "trailingStop"
        elif c["takeProfitPct"] and gain >= c["takeProfitPct"]:
            reason = "takeProfit"
        elif self.state["cursor"] - position["entryCursor"] + 1 >= c["maxHoldingDays"]:
            reason = "maxHoldingDays"
        elif c["partialTakeProfitPct"] and gain >= c["partialTakeProfitPct"] and not position["partialStage"]:
            reason = "partialTakeProfit"
        pending = self.state["pendingSells"].get(symbol)
        if reason and (pending is None or pending["reason"] == "partialTakeProfit" and reason != "partialTakeProfit"):
            self.state["pendingSells"][symbol] = {"symbol": symbol, "signalDate": day, "reason": reason}
            self._decision(day, symbol, "sell", "queued", reason, signalDate=day, observedClose=close)
        else:
            self._decision(day, symbol, "hold", "held", "exitPending" if pending else "riskRulesNotTriggered", quantity=position["quantity"], observedClose=close)

    def _step_day(self, day):
        s = self.state
        fill_start = len(s["trades"])
        self._observe_actions_before_open(day)
        sold_today = set()
        for symbol, order in list(sorted(s["pendingSells"].items())):
            if self._sell(day, symbol, order):
                sold_today.add(symbol)
        pending = s["pendingBuys"]
        s["pendingBuys"] = []
        for order in pending:
            self._buy(day, order, sold_today)
        fills = s["trades"][fill_start:]
        issues, unverified = audit_opening_fills(fills, self.securities,
            {fill["symbol"]: self._bar(fill["symbol"], day)[0] for fill in fills})
        s["executionCapacityIssues"].extend(issues)
        s["unverifiedOpeningFills"].extend(unverified)
        if issues:
            self._warning("模拟成交超过当日总量或开盘量证据矛盾；本账户路径无效，不参与平原选择或验收。")
        if s["cursor"] == 0:
            allocation = self.config["initialCapital"] / len(self.securities)
            for symbol in sorted(self.securities):
                bar, _ = self._bar(symbol, day)
                if bar and bar["volume"] > 0:
                    s["benchmarkUnits"][symbol] = allocation / bar["open"]
                    s["benchmarkCash"] -= allocation
        stale = []
        for symbol in sorted(self.securities):
            bar, index = self._bar(symbol, day)
            if bar and bar["volume"] > 0:
                s["lastPrices"][symbol], s["lastPriceDates"][symbol] = bar["close"], day
                if index and abs(bar["close"] / self._reference_close(symbol, index) - 1) > nominal_limit(symbol, day) + .005:
                    s["quarantined"][symbol] = day
                    self._warning(f"{symbol} {day} 原始收盘出现价格断点，停止后续成交；持仓/基准估值及收益有效性须人工核验。")
            if symbol in s["positions"] and (bar is None or bar["volume"] <= 0):
                stale.append(symbol)
                self._decision(day, symbol, "valuation", "stale", "missingBar" if bar is None else "suspended", priceDate=s["lastPriceDates"].get(symbol), markPrice=s["lastPrices"].get(symbol))
        market_value = sum(p["quantity"] * s["lastPrices"][symbol] for symbol, p in s["positions"].items())
        equity = s["cash"] + market_value
        s["peakEquity"] = max(s["peakEquity"], equity)
        benchmark = s["benchmarkCash"] + sum(units * s["lastPrices"][symbol] for symbol, units in s["benchmarkUnits"].items())
        benchmark_stale = [symbol for symbol in s["benchmarkUnits"] if s["lastPriceDates"].get(symbol) != day]
        s["curve"].append({"date": day, "equity": round(equity, 8), "cash": round(s["cash"], 8), "marketValue": round(market_value, 8),
                           "drawdownPct": round((1-equity/s["peakEquity"]) * 100, 8), "benchmarkEquity": round(benchmark, 8),
                           "staleSymbols": stale, "benchmarkStaleSymbols": benchmark_stale,
                           "valuationReliable": not stale and not s["executionCapacityIssues"] and not any(symbol in s["quarantined"] for symbol in s["positions"]) and not any(event["strategyQuantity"] for event in s["actionExposures"]),
                           "benchmarkValuationReliable": not benchmark_stale and not any(symbol in s["quarantined"] for symbol in s["benchmarkUnits"]) and not any(event["benchmarkUnits"] for event in s["actionExposures"])})
        for symbol, position in sorted(s["positions"].items()):
            self._exit_signal(day, symbol, position)
        candidates = []
        for symbol in sorted(self.securities):
            bar, index = self._bar(symbol, day)
            if symbol in s["positions"] or symbol in s["quarantined"] or not bar or bar["volume"] <= 0:
                continue
            if not self._history_ready(symbol, index):
                s["warmupSkips"] += 1
                continue
            signal = evaluate(self.bars[symbol], index, self.config, prepared=self.prepared.get(symbol))
            if signal:
                candidates.append({"symbol": symbol, "signalDate": day, "reason": "strategyIntersection", "budget": equity * self.config["positionFraction"], **signal})
        candidates.sort(key=lambda item: (-item["score"], item["symbol"]))
        # Queue all ranked candidates: next-open rejection of a higher-ranked
        # security can free capacity for the next one without looking ahead.
        s["pendingBuys"] = candidates
        for candidate in candidates:
            self._decision(day, candidate["symbol"], "buy", "queued", candidate["reason"], signalDate=day, score=candidate["score"], budget=candidate["budget"], strategies=candidate["strategies"])

    def result(self):
        s, c = self.state, self.config
        curve = s["curve"]
        initial = c["initialCapital"]
        equity = curve[-1]["equity"] if curve else initial
        total = equity / initial - 1
        annual = math.expm1(math.log(equity / initial) * 252 / len(curve)) if curve and equity > 0 and abs(math.log(equity / initial) * 252 / len(curve)) < 700 else None
        previous, returns = initial, []
        for point in curve:
            returns.append(point["equity"] / previous - 1 if previous > 0 else 0.)
            previous = point["equity"]
        deviation = stdev(returns) if len(returns) > 1 else 0.
        sharpe = mean(returns) / deviation * math.sqrt(252) if deviation > 1e-12 else None
        drawdown = max((point["drawdownPct"] for point in curve), default=0.)
        benchmark_return = ((curve[-1]["benchmarkEquity"] / initial - 1) * 100) if curve else 0.
        closed = s["closedPnls"]
        metrics = {"initialCapital": initial, "finalEquity": round(equity, 8), "totalReturnPct": total * 100,
                   "annualizedReturnPct": annual * 100 if annual is not None else None, "maxDrawdownPct": drawdown,
                   "sharpe": sharpe, "calmar": annual * 100 / drawdown if annual is not None and drawdown > 1e-12 else None,
                   "tradeCount": len(s["trades"]), "sellCount": sum(t["side"] == "sell" for t in s["trades"]),
                   "closedTradeCount": len(closed), "winRatePct": sum(pnl > 0 for pnl in closed) / len(closed) * 100 if closed else None,
                   "benchmarkReturnPct": benchmark_return, "excessReturnPct": total * 100 - benchmark_return,
                   "totalFees": round(s["totalFees"], 8), "totalSlippage": round(s["totalSlippage"], 8),
                   "tradingDays": len(curve), "exposurePct": mean(point["marketValue"] / point["equity"] * 100 if point["equity"] > 0 else 0 for point in curve) if curve else 0.,
                   "investedDays": sum(point["marketValue"] > 0 for point in curve),
                   "staleValuationDays": sum(bool(point["staleSymbols"]) for point in curve), "warmupSkips": s["warmupSkips"]}
        positions = [{**deepcopy(position), "markPrice": s["lastPrices"][symbol], "markDate": s["lastPriceDates"][symbol],
                      "marketValue": position["quantity"] * s["lastPrices"][symbol], "unrealizedPnl": position["quantity"] * s["lastPrices"][symbol] - position["costBasis"]} for symbol, position in sorted(s["positions"].items())]
        warnings = list(s["warnings"])
        if s["pendingBuys"] or s["pendingSells"]:
            warnings.append("区间末尾未执行信号保留为待执行；未强制平仓，最终权益包含尚未退出持仓的市值。")
        if any(point["staleSymbols"] for point in curve):
            warnings.append("部分持仓日缺少有效行情，沿用最后可见收盘估值并逐日标记；缺失不视为已卖出。")
        if not closed:
            warnings.append("没有完整平仓样本；胜率不可计算，不能据此评价策略稳定性。")
        return {"status": "completed" if s["cursor"] == len(self.dates) else "running", "progress": self.progress,
                "metrics": metrics, "curve": deepcopy(curve), "trades": deepcopy(s["trades"]), "decisions": deepcopy(s["decisions"]),
                "positions": positions, "pendingOrders": {"buys": deepcopy(s["pendingBuys"]), "sells": deepcopy(list(s["pendingSells"].values()))},
                "warnings": warnings, "config": deepcopy(c), "datasetFingerprint": self.fingerprint,
                "versions": {"engine": ENGINE_VERSION, "strategies": STRATEGY_VERSION, "rules": RULES_VERSION},
                "quality": {"adjustment": "raw", "pointInTimeUniverse": False, "historicalStComplete": False,
                            "strategyConflicts": strategy_conflicts(c),
                            "quarantinedSymbols": deepcopy(s["quarantined"]), "validForPerformanceClaims": False,
                            "executionCapacityIssues": deepcopy(s["executionCapacityIssues"]),
                            "unverifiedOpeningFills": deepcopy(s["unverifiedOpeningFills"]),
                            "openingCapacityVerified": not s["executionCapacityIssues"] and not s["unverifiedOpeningFills"],
                            "unaccountedActionExposure": [deepcopy(event) for event in s["actionExposures"] if event["strategyQuantity"]],
                            "signalTiming": "closeThenNextSessionOpen", "combination": "intersection",
                            "executionAssumption": "openPriceWithConservativeLimitsAndPriorVolumeCap"}}
