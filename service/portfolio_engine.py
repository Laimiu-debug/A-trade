"""Causal target-weight portfolios with one shared, resumable cash ledger.

Signals and budgets are observed at close. Orders use a later session's open,
previous-session volume, and available cash. OHLC extrema never imply a fill.
This research ledger does not book corporate actions or certify total returns.
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

from .portfolio_config import normalize_config
from .portfolio_signals import DailyScorer, SCORER_VERSION

ENGINE_VERSION = "portfolio-shared-ledger-v2"
RULES_VERSION = "ashare-conservative-2026-07-06"


def _digest(value):
    digest = hashlib.sha256()
    for chunk in json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False,
                                  separators=(",", ":")).iterencode(value):
        digest.update(chunk.encode("utf-8"))
    return digest.hexdigest()


def _day(value):
    if not isinstance(value, str):
        raise ValueError("日期必须是 YYYY-MM-DD")
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError:
        raise ValueError("日期必须是有效 YYYY-MM-DD") from None
    return value


def _finite(value, label, minimum=0, maximum=1e20):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum or not math.isfinite(value):
        raise ValueError(f"{label} 必须是范围内的有限数值")
    return float(value)


def _cent(value):
    return float(Decimal(str(value)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))


def board_for(symbol):
    return "star" if symbol.startswith("sh68") else "chinext" if symbol.startswith("sz30") else "bse" if symbol.startswith("bj") else "main"


def lot_rule(symbol):
    return (200, 1) if board_for(symbol) == "star" else (100, 1) if board_for(symbol) == "bse" else (100, 100)


def _quantity(value, minimum, increment):
    count = max(0, math.floor(value / increment + 1e-10) * increment)
    return count if count >= minimum else 0


def nominal_limit(symbol, day):
    board = board_for(symbol)
    return .3 if board == "bse" else .2 if board == "star" or board == "chinext" and day >= "2020-08-24" else .1


def execution_limit(security, day):
    band = nominal_limit(security["symbol"], day)
    if band != .1 or board_for(security["symbol"]) == "main" and day >= "2026-07-06":
        return band
    # Unknown historical ST is a conservative rejection boundary, not a claim
    # that a normal security's legal daily band is five percent.
    return .1 if security.get("stByDate", {}).get(day) is False else .05


class PortfolioEngine:
    def __init__(self, dataset, config=None, state=None):
        self.config = normalize_config(config)
        self._validate_dataset(dataset)
        self.dataset = dataset
        self.data_digest = _digest(dataset)
        self.fingerprint = str(dataset.get("fingerprint") or self.data_digest)
        self.scorer = DailyScorer(dataset, self.config)
        self.state = {
            "engineVersion": ENGINE_VERSION, "rulesVersion": RULES_VERSION, "scorerVersion": SCORER_VERSION,
            "dataDigest": self.data_digest, "datasetFingerprint": self.fingerprint,
            "config": deepcopy(self.config), "cursor": 0, "cash": self.config["initialCapital"],
            "positions": {}, "pendingTarget": None, "selectedByStrategy": {}, "forcedExits": {},
            "lastPrices": {}, "lastPriceDates": {}, "peakEquity": self.config["initialCapital"],
            "benchmarkFirstClose": None, "benchmarkFirstDate": None, "benchmarkLastClose": None, "benchmarkLastDate": None,
            "totalFees": 0., "totalSlippage": 0., "grossTradedAmount": 0.,
            "curve": [], "trades": [], "closedTrades": [], "decisions": [], "daily": [],
            "corporateActionDates": [], "unaccountedActionExposure": [], "quarantined": {},
            "warnings": [
                "研究假设：收盘形成目标，下一交易日开盘先卖后买，共用现金；日内高低价不构成成交依据。",
                "未模拟竞价队列；成交容量取前一交易日成交量。开盘成交与滑点属于假设。",
                "公司行动权益未入账，复权因子不改变现金或真实成交价；本报告不能认证总回报。",
                "非完整历史股票集合不能消除生存者偏差；分批计算不构成投资范围截断。",
                "统一佣金、最低佣金、过户费、滑点是配置假设；自动印花税仅还原已支持的日期切换。",
            ],
        }
        for warning in dataset.get("warnings", []):
            self._warning(str(warning))
        if not self.calendar_supplied or dataset.get("calendarSource", "").startswith("observed"):
            self._warning("未提供独立交易日历，使用全部输入证券日线日期并集；全体缺失的交易日无法识别。")
        if self.config["stampDutyRate"] is None and self.dates[0] < "2008-09-19":
            raise ValueError("自动印花税仅支持 2008-09-19 起的卖方单边期间")
        if state is not None:
            self._restore(state)

    def _validate_dataset(self, dataset):
        if not isinstance(dataset, dict) or not isinstance(dataset.get("securities"), list) or not dataset["securities"]:
            raise ValueError("需要非空 securities 数据集")
        if dataset.get("adjustment", "raw") not in ("raw", "unadjusted", "none"):
            raise ValueError("组合成交仅接受原始未复权价格")
        self.securities, self.bars, self.by_date = {}, {}, {}
        for security in dataset["securities"]:
            if not isinstance(security, dict):
                raise ValueError("证券须为对象")
            symbol = security.get("symbol", "")
            if not isinstance(symbol, str) or not re.fullmatch(r"(?:sh(?:60|68)\d{4}|sz(?:00|30)\d{4}|bj(?:43|83|87|88|92)\d{4})", symbol):
                raise ValueError("仅支持识别的沪深北 A 股代码")
            if security.get("kind", "stock") != "stock" or symbol in self.securities:
                raise ValueError("重复证券或不支持的证券类型")
            bars = security.get("bars")
            if not isinstance(bars, list) or not bars:
                raise ValueError("每只证券需要非空日线")
            previous = ""
            for row in bars:
                if not isinstance(row, dict):
                    raise ValueError("日线须为对象")
                day = _day(row.get("date"))
                if day <= previous:
                    raise ValueError("日线日期必须严格递增且不重复")
                previous = day
                for key in ("open", "high", "low", "close"):
                    _finite(row.get(key), f"{symbol}.{key}", .000001, 1e8)
                _finite(row.get("volume"), f"{symbol}.volume", 0, 1e15)
                _finite(row.get("amount", 0), f"{symbol}.amount")
                if not row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]:
                    raise ValueError("日线开高低收关系无效")
            for field in ("stByDate", "precloseByDate", "factorByDate", "tradeStatusByDate", "memberHistory", "memberByDate"):
                mapping = security.get(field, {})
                if not isinstance(mapping, dict):
                    raise ValueError(f"{field} 必须是日期映射")
                for day, value in mapping.items():
                    _day(day)
                    if field in ("stByDate", "memberHistory", "memberByDate") and type(value) is not bool:
                        raise ValueError(f"{field} 需要布尔值")
                    if field in ("precloseByDate", "factorByDate"):
                        _finite(value, field, .000001, 1e8)
                    if field == "tradeStatusByDate" and value not in (True, False, 1, 0, "1", "0"):
                        raise ValueError("tradeStatusByDate 需要布尔值或 0/1")
            for field in ("listingDate", "delistDate"):
                if security.get(field):
                    _day(security[field])
            self.securities[symbol], self.bars[symbol] = security, bars
            self.by_date[symbol] = {row["date"]: index for index, row in enumerate(bars)}
        membership = dataset.get("membershipByDate", {})
        if not isinstance(membership, dict):
            raise ValueError("membershipByDate 必须为日期到完整股票列表的映射")
        for day, symbols in membership.items():
            _day(day)
            if not isinstance(symbols, list) or any(not isinstance(symbol, str) for symbol in symbols) or len(symbols) != len(set(symbols)):
                raise ValueError("membershipByDate 必须为日期到不重复股票列表的映射")
        observed_dates = sorted({row["date"] for bars in self.bars.values() for row in bars})
        calendar = dataset.get("calendar")
        self.calendar_supplied = calendar is not None
        if calendar is not None:
            if not isinstance(calendar, list) or not calendar or any(_day(day) != day for day in calendar) or calendar != sorted(set(calendar)):
                raise ValueError("calendar 须为严格递增的交易日期列表")
            if not set(observed_dates).issubset(set(calendar)):
                raise ValueError("calendar 必须覆盖全部日线日期（含预热）")
        self.all_dates = calendar if calendar is not None else observed_dates
        self.calendar_index = {day: index for index, day in enumerate(self.all_dates)}
        self.dates = [day for day in self.all_dates if (not self.config["startDate"] or day >= self.config["startDate"]) and (not self.config["endDate"] or day <= self.config["endDate"])]
        if not self.config["startDate"]:
            self.dates = self.dates[-self.config["lookbackDays"]:]
        if not self.dates:
            raise ValueError("所选区间没有交易日")
        self.benchmark_by_date = {}
        benchmark = dataset.get("benchmark")
        if benchmark is not None:
            if not isinstance(benchmark, dict) or not isinstance(benchmark.get("bars"), list):
                raise ValueError("benchmark 需要 bars")
            previous = ""
            for bar in benchmark["bars"]:
                day = _day(bar.get("date"))
                if day <= previous:
                    raise ValueError("基准日期必须严格递增")
                previous = day
                self.benchmark_by_date[day] = _finite(bar.get("close"), "benchmark.close", .000001, 1e8)
        try:
            json.dumps({key: value for key, value in dataset.items() if key != "securities"}, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("数据集元数据包含非法值") from None

    def _restore(self, state):
        if not isinstance(state, dict) or set(state) != set(self.state):
            raise ValueError("恢复状态字段不完整")
        try:
            json.dumps(state, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("恢复状态含非法值") from None
        for key in ("engineVersion", "rulesVersion", "scorerVersion", "dataDigest", "datasetFingerprint", "config"):
            if state[key] != self.state[key]:
                raise ValueError("配置、数据或引擎版本已改变，不能恢复")
        cursor = state["cursor"]
        if type(cursor) is not int or not 0 <= cursor <= len(self.dates) or len(state["curve"]) != cursor or len(state["daily"]) != cursor:
            raise ValueError("恢复进度不一致")
        _finite(state["cash"], "cash")
        for symbol, position in state["positions"].items():
            if symbol not in self.securities or type(position["quantity"]) is not int or position["quantity"] <= 0:
                raise ValueError("恢复持仓无效")
            if sum(lot["quantity"] for lot in position["lots"]) != position["quantity"]:
                raise ValueError("恢复持仓分笔数量不一致")
            for lot in position["lots"]:
                _day(lot["date"])
                if type(lot["quantity"]) is not int or lot["quantity"] <= 0:
                    raise ValueError("恢复分笔数量无效")
                _finite(lot["cost"], "lot.cost")
        pending = state["pendingTarget"]
        if pending is not None:
            priorities = pending.get("priorityBySymbol")
            if not isinstance(priorities, dict) or set(priorities) != set(pending["weights"]):
                raise ValueError("恢复目标优先级不完整")
            for symbol, priority in priorities.items():
                if symbol not in self.securities or not isinstance(priority, dict):
                    raise ValueError("恢复目标优先级无效")
                _finite(priority.get("budgetWeight"), "priority.budgetWeight", 0, 1.000001)
                _finite(priority.get("weightedScore"), "priority.weightedScore", 0, 1.000001)
        self.state = deepcopy(state)

    @property
    def progress(self):
        cursor = self.state["cursor"]
        return {"progress": cursor / len(self.dates), "phase": "completed" if cursor == len(self.dates) else "backtesting", "completedDays": cursor, "totalDays": len(self.dates)}

    def checkpoint(self):
        return deepcopy(self.state)

    def step(self, days=5):
        if type(days) is not int or not 1 <= days <= 20000:
            raise ValueError("推进天数必须为 1..20000 整数")
        for _ in range(min(days, len(self.dates) - self.state["cursor"])):
            append_only = ("curve", "trades", "closedTrades", "decisions", "daily", "warnings", "corporateActionDates", "unaccountedActionExposure")
            immutable = ("config", "engineVersion", "rulesVersion", "scorerVersion", "dataDigest", "datasetFingerprint")
            lengths = {key: len(self.state[key]) for key in append_only}
            rollback = {key: deepcopy(value) for key, value in self.state.items() if key not in append_only + immutable}
            try:
                self._step_day(self.dates[self.state["cursor"]])
            except Exception:
                self.state.update(rollback)
                for key, length in lengths.items():
                    del self.state[key][length:]
                raise
            self.state["cursor"] += 1
        return self.state["cursor"] == len(self.dates)

    def _warning(self, text):
        if text not in self.state["warnings"]:
            self.state["warnings"].append(text)

    def _decision(self, day, symbol, action, status, reason, **extra):
        self.state["decisions"].append({"date": day, "symbol": symbol, "action": action, "status": status, "reason": reason, **extra})

    def _bar(self, symbol, day):
        index = self.by_date[symbol].get(day)
        return (self.bars[symbol][index], index) if index is not None else (None, None)

    def _reference(self, symbol, index):
        rows = self.bars[symbol]
        return self.securities[symbol].get("precloseByDate", {}).get(rows[index]["date"], rows[index - 1]["close"] if index else None)

    def _member(self, symbol, day):
        global_members = self.dataset.get("membershipByDate", {})
        if day in global_members:
            return symbol in global_members[day]
        security = self.securities[symbol]
        local = security.get("memberHistory", security.get("memberByDate", {}))
        if day in local:
            return local[day]
        return None

    def _observe_actions(self, day):
        for symbol in sorted(self.securities):
            bar, index = self._bar(symbol, day)
            if bar is None or not index:
                continue
            reference = self.securities[symbol].get("precloseByDate", {}).get(day)
            previous = self.bars[symbol][index - 1]["close"]
            if reference is not None and abs(reference - previous) > max(.005, previous * 1e-7):
                event = {"symbol": symbol, "date": day, "rawPreviousClose": previous, "preclose": reference}
                self.state["corporateActionDates"].append(event)
                if symbol in self.state["positions"]:
                    self.state["unaccountedActionExposure"].append({**event, "quantity": self.state["positions"][symbol]["quantity"]})
                    self._decision(day, symbol, "corporateAction", "unaccounted", "referenceChangedWithoutRightsLedger", rawPreviousClose=previous, preclose=reference)
                    # Price risk references may follow the ex-right reference;
                    # this does not create shares, dividends or ledger P&L.
                    position = self.state["positions"][symbol]
                    position["riskEntryPrice"] *= reference / previous
                    position["closePeak"] *= reference / previous
                    self._warning(f"{symbol} {day} 隔夜持仓跨参考昨收变化，权益未入账，之后净值仅供价格回报诊断。")

    def _gate(self, symbol, day, side):
        security = self.securities[symbol]
        bar, index = self._bar(symbol, day)
        if symbol in self.state["quarantined"]:
            return "rawPriceDiscontinuity"
        if security.get("delistDate") and day >= security["delistDate"]:
            return "delistedUnsettledPosition"
        if bar is None:
            return "missingBar"
        status = security.get("tradeStatusByDate", {}).get(day)
        if status in (False, 0, "0") or bar["volume"] <= 0:
            return "suspended"
        if index < 5:
            return "insufficientListingObservations"
        previous = self.bars[symbol][index - 1]
        if self.calendar_index[day] - self.calendar_index[previous["date"]] != 1 or previous["volume"] <= 0:
            return "missingPreviousTradingBar"
        if day < "1996-12-16" or board_for(symbol) == "bse" and day < "2021-11-15":
            return "unsupportedHistoricalRules"
        if side == "buy":
            if self._member(symbol, day) is False:
                return "notHistoricalMember"
            if security.get("listingDate") and day < security["listingDate"]:
                return "notListed"
            if self.config["universe"]["excludeSt"] and security.get("stByDate", {}).get(day) is True:
                return "historicalST"
            if self.config["universe"]["strictHistory"] and (status is None or day not in security.get("stByDate", {}) or self._member(symbol, day) is None or not security.get("listingDate")):
                return "executionHistoryUnknown"
        reference = self._reference(symbol, index)
        if abs(bar["open"] / reference - 1) > nominal_limit(symbol, day) + .005:
            self.state["quarantined"][symbol] = day
            return "rawPriceDiscontinuity"
        band = execution_limit(security, day)
        upper, lower = _cent(reference * (1 + band)), _cent(reference * (1 - band))
        if side == "buy" and bar["open"] >= upper - 1e-8:
            return "openAtUpperLimitOrUncertainST"
        if side == "sell" and bar["open"] <= lower + 1e-8:
            return "openAtLowerLimitOrUncertainST"
        return None

    def _fees(self, amount, side, day):
        c = self.config
        tax = c["stampDutyRate"]
        if tax is None:
            tax = .0005 if day >= "2023-08-28" else .001
        return max(c["minCommission"], amount * c["commissionRate"]) + amount * c["transferFeeRate"] + (amount * tax if side == "sell" else 0)

    def _fill_price(self, symbol, day, side):
        bar, index = self._bar(symbol, day)
        price = bar["open"] * (1 + self.config["slippageBps"] / 10000 * (1 if side == "buy" else -1))
        band, reference = execution_limit(self.securities[symbol], day), self._reference(symbol, index)
        if side == "buy" and price > _cent(reference * (1 + band)) + 1e-8 or side == "sell" and price < _cent(reference * (1 - band)) - 1e-8:
            return None
        return price

    def _execute_target(self, day):
        s, pending = self.state, self.state["pendingTarget"]
        for symbol, order in list(sorted(s["forcedExits"].items())):
            position = s["positions"].get(symbol)
            if position is None:
                s["forcedExits"].pop(symbol, None)
            elif day > order["signalDate"]:
                self._execute_order(day, symbol, "sell", position["quantity"], {**order, "weights": {}})
                if symbol not in s["positions"]:
                    s["forcedExits"].pop(symbol, None)
        if pending is None or day <= pending["signalDate"]:
            return
        # Freeze desired quantities at the first observable opening for each
        # security. Partial fills retry these quantities, not a new daily NAV.
        for symbol in sorted(set(pending["weights"]) | set(s["positions"])):
            if symbol in pending["quantities"]:
                continue
            weight = pending["weights"].get(symbol, 0.)
            if weight == 0:
                pending["quantities"][symbol] = 0
            else:
                bar, _ = self._bar(symbol, day)
                if bar is not None:
                    minimum, increment = lot_rule(symbol)
                    buying_price = bar["open"] * (1 + self.config["slippageBps"] / 10000)
                    budget = pending["budgets"][symbol]
                    limit = _quantity(budget / buying_price, minimum, increment)
                    # Account for fees before freezing an initial entry order.
                    if symbol not in s["positions"]:
                        limit = self._affordable_quantity(limit, minimum, increment, buying_price, budget, day)
                    pending["quantities"][symbol] = limit
                else:
                    self._decision(day, symbol, "rebalance", "deferred", "missingBar", signalDate=pending["signalDate"])
        for side in ("sell", "buy"):
            for symbol in self._priority_order(pending["quantities"], pending["priorityBySymbol"]):
                if symbol in s["forcedExits"]:
                    continue  # independent liquidation owns this security
                held = s["positions"].get(symbol, {}).get("quantity", 0)
                delta = pending["quantities"][symbol] - held
                requested = -delta if side == "sell" else delta
                if requested <= 0:
                    continue
                self._execute_order(day, symbol, side, requested,
                                    {**pending, "signalDate": pending.get("signalDates", {}).get(symbol, pending["signalDate"])})

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
        capacity = math.floor(self.bars[symbol][index - 1]["volume"] * self.config["maxParticipationRate"])
        position = s["positions"].get(symbol)
        pnl = None
        if side == "sell":
            sellable = sum(lot["quantity"] for lot in position["lots"] if lot["date"] < day)
            if sellable == 0:
                self._decision(day, symbol, side, "deferred", "tPlusOne", signalDate=pending["signalDate"])
                return
            desired = min(requested, capacity, sellable)
            quantity = desired if desired == position["quantity"] else _quantity(desired, minimum, increment)
            if not quantity:
                self._decision(day, symbol, side, "deferred", "previousVolumeOrLotMinimum", signalDate=pending["signalDate"], requestedQuantity=requested)
                return
            fee = self._fees(quantity * price, side, day)
            if s["cash"] + quantity * price < fee:
                self._decision(day, symbol, side, "deferred", "insufficientSellFeeCash", signalDate=pending["signalDate"])
                return
            left, cost, retained = quantity, 0., []
            for lot in position["lots"]:
                sold = min(left, lot["quantity"]) if lot["date"] < day else 0
                allocated = lot["cost"] * sold / lot["quantity"]
                left -= sold
                cost += allocated
                if lot["quantity"] > sold:
                    retained.append({"date": lot["date"], "quantity": lot["quantity"] - sold, "cost": lot["cost"] - allocated})
            position["lots"] = retained
            position["quantity"] -= quantity
            position["costBasis"] -= cost
            s["cash"] += quantity * price - fee
            pnl = quantity * price - fee - cost
            position["realizedPnl"] += pnl
            if not position["quantity"]:
                s["closedTrades"].append({"symbol": symbol, "entryDate": position["entryDate"], "exitDate": day,
                                           "pnlAmount": position["realizedPnl"], "investedAmount": position["investedAmount"],
                                           "returnPct": position["realizedPnl"] / position["investedAmount"] * 100,
                                           "holdingDays": s["cursor"] - position["entryCursor"] + 1})
                del s["positions"][symbol]
        else:
            if position is None and len(s["positions"]) >= self.config["maxPositions"]:
                self._decision(day, symbol, side, "deferred", "positionSlotsOccupied", signalDate=pending["signalDate"], requestedQuantity=requested)
                return
            ceiling = _quantity(min(requested, capacity), minimum, increment)
            target_budget = max(0., pending["budgets"][symbol] - (position["quantity"] * price if position else 0.))
            quantity = self._affordable_quantity(ceiling, minimum, increment, price, min(s["cash"], target_budget), day)
            if quantity < minimum:
                self._decision(day, symbol, side, "deferred", "cashOrPreviousVolumeBelowMinimum", signalDate=pending["signalDate"], requestedQuantity=requested)
                return
            fee = self._fees(quantity * price, side, day)
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
                "reason": pending.get("reason", "targetRebalance"), "targetWeight": pending["weights"].get(symbol, 0.)}
        s["trades"].append(fill)
        self._decision(day, symbol, side, "filled", pending.get("reason", "targetRebalance"), signalDate=pending["signalDate"], quantity=quantity, price=price,
                       unfilledQuantity=max(0, requested - quantity))

    def _affordable_quantity(self, ceiling, minimum, increment, price, budget, day):
        low, high, quantity = 0, ceiling // increment, 0
        while low <= high:
            mid = (low + high) // 2
            count = mid * increment
            if count * price + self._fees(count * price, "buy", day) <= budget + 1e-8:
                quantity, low = count, mid + 1
            else:
                high = mid - 1
        return quantity if quantity >= minimum else 0

    def _risk_exits(self, day, eligibility):
        s, c = self.state, self.config
        for symbol, position in sorted(s["positions"].items()):
            security = self.securities[symbol]
            bar, _ = self._bar(symbol, day)
            reason = None
            if self._member(symbol, day) is False:
                reason = "baseEligibilityLost"
            elif security.get("delistDate") and day >= security["delistDate"]:
                reason = "delisted"
            elif c["universe"]["excludeSt"] and security.get("stByDate", {}).get(day) is True:
                reason = "historicalST"
            elif c["rebalanceMode"] in ("risk", "hybrid") and bar is not None and bar["volume"] > 0:
                position["closePeak"] = max(position["closePeak"], bar["close"])
                gain = bar["close"] / position["riskEntryPrice"] - 1
                if c["stopLossPct"] and gain <= -c["stopLossPct"]:
                    reason = "stopLoss"
                elif c["trailingStopPct"] and bar["close"] / position["closePeak"] - 1 <= -c["trailingStopPct"]:
                    reason = "trailingStop"
                elif c["takeProfitPct"] and gain >= c["takeProfitPct"]:
                    reason = "takeProfit"
                elif c["maxHoldingDays"] and s["cursor"] - position["entryCursor"] + 1 >= c["maxHoldingDays"]:
                    reason = "maxHoldingDays"
            if reason and symbol not in s["forcedExits"]:
                s["forcedExits"][symbol] = {"signalDate": day, "reason": reason}
                self._decision(day, symbol, "sell", "queued", reason, signalDate=day,
                               observedClose=bar["close"] if bar else None)
            elif eligibility.get(symbol, {}).get("reason") == "amount20BelowMinimum":
                self._decision(day, symbol, "hold", "held", "liquidityFilterFailedButRiskHoldingRetained")

    def _excluded_today(self, day):
        # A liquidation is not cancelled by another strategy selecting the same
        # stock; do not immediately rebuy a risk exit on its completion date.
        return set(self.state["forcedExits"]) | {trade["symbol"] for trade in self.state["trades"]
                                               if trade["date"] == day and trade["side"] == "sell" and trade["reason"] != "targetRebalance"}

    @staticmethod
    def _priority_order(symbols, priorities):
        return sorted(symbols, key=lambda symbol: (
            -priorities.get(symbol, {}).get("budgetWeight", 0.),
            -priorities.get(symbol, {}).get("weightedScore", 0.), symbol))

    def _select_targets(self, scored, excluded=None):
        selected, target_weights, strategy_weights, details = {}, {}, {}, {}
        weighted_scores = {}
        allocation = self.config["allocation"]
        identifiers = allocation["strategyIds"]
        for identifier in identifiers:
            strategy_weights[identifier] = 1. / len(identifiers)
            candidates = scored.get("strategies", {}).get(identifier, [])
            by_symbol = {}
            for item in candidates:
                symbol = item.get("symbol")
                if symbol not in self.securities or symbol in by_symbol:
                    raise ValueError("评分结果有未知或重复证券")
                _finite(item.get("score"), "normalized score", 0, 1)
                if item.get("eligible", True) and symbol not in (excluded or set()):
                    by_symbol[symbol] = item
            ordered = sorted(by_symbol, key=lambda symbol: (-by_symbol[symbol]["score"], symbol))
            incumbents = [symbol for symbol in self.state["selectedByStrategy"].get(identifier, [])
                          if symbol in self.state["positions"] and symbol in by_symbol]
            chosen = sorted(incumbents, key=lambda symbol: (-by_symbol[symbol]["score"], symbol))[:self.config["topN"]]
            for symbol in ordered:
                if symbol in chosen:
                    continue
                if len(chosen) < self.config["topN"]:
                    chosen.append(symbol)
                    continue
                weakest = min(chosen, key=lambda key: (by_symbol[key]["score"], key))
                if by_symbol[symbol]["score"] > by_symbol[weakest]["score"] + self.config["switchThreshold"] + 1e-12:
                    chosen.remove(weakest)
                    chosen.append(symbol)
            chosen.sort(key=lambda symbol: (-by_symbol[symbol]["score"], symbol))
            selected[identifier] = chosen
            details[identifier] = [{**deepcopy(by_symbol[symbol]), "rank": rank + 1} for rank, symbol in enumerate(chosen)]
            scores = [by_symbol[symbol]["score"] for symbol in chosen]
            denominator = sum(scores) if self.config["weighting"] == "rank" else len(chosen)
            for symbol in chosen:
                share = (by_symbol[symbol]["score"] / denominator if denominator else 1. / len(chosen)) if self.config["weighting"] == "rank" else 1. / len(chosen)
                target_weights[symbol] = target_weights.get(symbol, 0.) + strategy_weights[identifier] * share
                weighted_scores[symbol] = weighted_scores.get(symbol, 0.) + strategy_weights[identifier] * by_symbol[symbol]["score"]
        # Capacity selection preserves the aggregate strategy-budget priority.
        # Equal budgets retain normalized score priority, including after JSON
        # serialization. A stock cap must not erase either key into code ties.
        priorities = {symbol: {"budgetWeight": weight, "weightedScore": weighted_scores[symbol]}
                      for symbol, weight in target_weights.items()}
        ordered_targets = self._priority_order(target_weights, priorities)
        clipped = [{"symbol": symbol, "preCapWeight": target_weights[symbol],
                    "weightedScore": weighted_scores[symbol],
                    "targetWeight": min(target_weights[symbol], self.config["maxStockWeight"]), "reason": "maxPositions"}
                   for symbol in ordered_targets[self.config["maxPositions"]:]]
        target_weights = {symbol: min(target_weights[symbol], self.config["maxStockWeight"]) for symbol in ordered_targets[:self.config["maxPositions"]]}
        priorities = {symbol: priorities[symbol] for symbol in target_weights}
        return selected, target_weights, strategy_weights, details, clipped, priorities

    def _risk_targets(self, weights, priorities, equity, day):
        """Keep existing quantities; deploy only empty/closing slots to new names."""
        retained = {symbol: position["quantity"] for symbol, position in self.state["positions"].items()
                    if symbol not in self.state["forcedExits"]}
        carry_budgets, carry_dates = {}, {}
        previous = self.state["pendingTarget"]
        if previous is not None:
            for symbol in previous["weights"]:
                security = self.securities[symbol]
                if symbol in self._excluded_today(day):
                    continue
                if self._member(symbol, day) is False or self.config["universe"]["excludeSt"] and security.get("stByDate", {}).get(day) is True or security.get("delistDate") and day >= security["delistDate"]:
                    self._decision(day, symbol, "buy", "cancelled", "baseEligibilityLost")
                    continue
                held_quantity = self.state["positions"].get(symbol, {}).get("quantity", 0)
                outstanding = previous["quantities"].get(symbol)
                if outstanding is None or outstanding > held_quantity:
                    # No opening quote yet is still a live entry intention.
                    retained[symbol] = outstanding
                    carry_budgets[symbol] = previous["budgets"][symbol]
                    carry_dates[symbol] = previous.get("signalDates", {}).get(symbol, previous["signalDate"])
                    priorities[symbol] = deepcopy(previous["priorityBySymbol"][symbol])
        free = max(0, self.config["maxPositions"] - len(retained))
        fresh = [symbol for symbol in self._priority_order(weights, priorities) if symbol not in retained][:free]
        target_weights = {symbol: quantity * self.state["lastPrices"][symbol] / equity if equity else 0.
                          for symbol, quantity in retained.items() if symbol not in carry_budgets}
        for symbol, budget in carry_budgets.items():
            target_weights[symbol] = budget / equity if equity else 0.
        remaining_weight = max(0., 1. - sum(target_weights.values()))
        for symbol in fresh:
            target_weights[symbol] = min(weights[symbol], self.config["maxStockWeight"], remaining_weight)
            remaining_weight -= target_weights[symbol]
        priorities = {symbol: priorities.get(symbol, (previous or {}).get("priorityBySymbol", {}).get(
            symbol, {"budgetWeight": 0., "weightedScore": 0.})) for symbol in target_weights}
        return target_weights, {symbol: quantity for symbol, quantity in retained.items() if quantity is not None}, carry_budgets, carry_dates, priorities

    def _step_day(self, day):
        s = self.state
        self._observe_actions(day)
        self._execute_target(day)
        stale = []
        for symbol in sorted(self.securities):
            bar, index = self._bar(symbol, day)
            if bar is not None and index and bar["volume"] > 0 and abs(bar["close"] / self._reference(symbol, index) - 1) > nominal_limit(symbol, day) + .005:
                s["quarantined"].setdefault(symbol, day)
                self._decision(day, symbol, "dataQuality", "quarantined", "rawCloseDiscontinuity")
                self._warning(f"{symbol} 存在无法解释的原始价格断点，后续成交隔离；相关净值不可用于有效性结论。")
        for symbol in sorted(s["positions"]):
            bar, _ = self._bar(symbol, day)
            if bar is not None and bar["volume"] > 0:
                s["lastPrices"][symbol], s["lastPriceDates"][symbol] = bar["close"], day
            else:
                stale.append(symbol)
                self._decision(day, symbol, "valuation", "stale", "missingOrSuspendedBar", priceDate=s["lastPriceDates"].get(symbol))
        market_value = sum(position["quantity"] * s["lastPrices"][symbol] for symbol, position in s["positions"].items())
        equity = s["cash"] + market_value
        previous_equity = s["curve"][-1]["equity"] if s["curve"] else self.config["initialCapital"]
        s["peakEquity"] = max(s["peakEquity"], equity)
        benchmark_close = self.benchmark_by_date.get(day)
        if benchmark_close is not None:
            if s["benchmarkFirstClose"] is None:
                s["benchmarkFirstClose"], s["benchmarkFirstDate"] = benchmark_close, day
            s["benchmarkLastClose"], s["benchmarkLastDate"] = benchmark_close, day
        benchmark_equity = self.config["initialCapital"] * s["benchmarkLastClose"] / s["benchmarkFirstClose"] if s["benchmarkFirstClose"] else None
        curve = {"date": day, "equity": equity, "cash": s["cash"], "marketValue": market_value,
                 "dailyReturn": equity / previous_equity - 1 if previous_equity else None,
                 "drawdownPct": (1 - equity / s["peakEquity"]) * 100 if s["peakEquity"] else 0.,
                 "staleSymbols": stale, "valuationReliable": not stale and not s["unaccountedActionExposure"] and not any(symbol in s["quarantined"] for symbol in s["positions"]),
                 "benchmarkEquity": benchmark_equity, "benchmarkStale": bool(self.benchmark_by_date) and s["benchmarkLastDate"] != day,
                 "regimeForReturn": deepcopy(s["daily"][-1]["regime"]) if s["daily"] else {"status": "unknown", "reason": "noPriorCloseObservation"}}
        s["curve"].append(curve)
        scored = self.scorer.daily(day)
        if not isinstance(scored, dict) or not isinstance(scored.get("strategies"), dict):
            raise ValueError("DailyScorer 返回结构无效")
        self._risk_exits(day, scored.get("eligibilityBySymbol", {}))
        rebalance = s["cursor"] % self.config["rebalanceEvery"] == 0
        strategy_weights = {identifier: 1. / len(self.config["allocation"]["strategyIds"]) for identifier in self.config["allocation"]["strategyIds"]}
        selected, weights, strategy_weights, details, clipped, priorities = self._select_targets(scored, self._excluded_today(day) | set(s["quarantined"]))
        raw_top_n = {identifier: [{**deepcopy(item), "rank": rank + 1} for rank, item in enumerate(
            sorted([row for row in rows if row.get("eligible", True)], key=lambda row: (-row["score"], row["symbol"]))[:self.config["topN"]])]
                     for identifier, rows in scored["strategies"].items()}
        if rebalance or s["forcedExits"]:
            quantities = {}
            carry_budgets, carry_dates = {}, {}
            if self.config["rebalanceMode"] == "risk":
                weights, quantities, carry_budgets, carry_dates, priorities = self._risk_targets(weights, priorities, equity, day)
            elif not rebalance and s["pendingTarget"]:
                # Between rank dates, risk can remove names but cannot trigger
                # an unrequested fresh cross-sectional rebalance.
                weights = {symbol: weight for symbol, weight in s["pendingTarget"]["weights"].items() if symbol not in s["forcedExits"]}
                quantities = {symbol: count for symbol, count in s["pendingTarget"]["quantities"].items() if symbol not in s["forcedExits"]}
                priorities = {symbol: deepcopy(s["pendingTarget"]["priorityBySymbol"][symbol]) for symbol in weights}
            if s["pendingTarget"]:
                for symbol, quantity in s["pendingTarget"]["quantities"].items():
                    remaining = quantity - s["positions"].get(symbol, {}).get("quantity", 0)
                    carried = symbol in carry_dates and quantities.get(symbol) == quantity
                    if remaining and not carried:
                        self._decision(day, symbol, "rebalance", "superseded", "newCloseTarget", unfilledQuantity=abs(remaining))
            s["selectedByStrategy"] = selected
            s["pendingTarget"] = {"signalDate": day, "signalEquity": equity, "weights": weights, "quantities": quantities,
                                  "priorityBySymbol": priorities,
                                  "budgets": {**{symbol: weight * equity for symbol, weight in weights.items()}, **carry_budgets},
                                  "signalDates": {**{symbol: day for symbol in weights}, **carry_dates}}
            for symbol in sorted(set(weights) | set(s["positions"])):
                self._decision(day, symbol, "target", "queued", "scheduledRebalance", targetWeight=weights.get(symbol, 0.), signalEquity=equity)
        pending = s["pendingTarget"]
        holdings = [{"symbol": symbol, "quantity": position["quantity"], "costBasis": position["costBasis"],
                     "marketValue": position["quantity"] * s["lastPrices"][symbol],
                     "weight": position["quantity"] * s["lastPrices"][symbol] / equity if equity else 0.}
                    for symbol, position in sorted(s["positions"].items())]
        s["daily"].append({"date": day, "rebalance": rebalance, "topNByStrategy": raw_top_n, "selectedCandidatesByStrategy": details,
                           "selectedByStrategy": deepcopy(s["selectedByStrategy"]), "strategyWeights": strategy_weights,
                           "strategyBudgetWeights": strategy_weights, "strategyWeightMeaning": "fixedEntryBudgetNotRealizedHoldingAttribution",
                           "targetWeights": deepcopy(pending["weights"] if pending else {}), "targetSignalDate": pending["signalDate"] if pending else None,
                           "targetPriority": self._priority_order(pending["weights"], pending["priorityBySymbol"]) if pending else [],
                           "targetPriorityBySymbol": deepcopy(pending["priorityBySymbol"] if pending else {}),
                           "holdings": holdings, "actualStockWeights": {row["symbol"]: row["weight"] for row in holdings},
                           "coverage": deepcopy(scored.get("coverage", {})),
                           "regime": deepcopy(scored.get("regime")), "clippedTargets": clipped,
                           "targetSelectionPolicy": "aggregateBudgetBeforeCap;strategyBudgetWeightedScore;symbolTieBreak;maxPositionsTruncation;noRedistribution",
                           "cash": s["cash"], "equity": equity})

    def result(self):
        s = self.state
        equity = s["curve"][-1]["equity"] if s["curve"] else self.config["initialCapital"]
        returns = [row["dailyReturn"] for row in s["curve"] if row["dailyReturn"] is not None]
        dispersion = stdev(returns) if len(returns) > 1 else 0.
        annualized = None
        if returns and equity > 0:
            try:
                annualized = ((equity / self.config["initialCapital"]) ** (252 / len(returns)) - 1) * 100
                if not math.isfinite(annualized):
                    annualized = None
            except OverflowError:
                pass
        positions = [{**deepcopy(position), "name": self.securities[symbol].get("name", symbol),
                      "markPrice": s["lastPrices"][symbol], "priceDate": s["lastPriceDates"][symbol],
                      "marketValue": position["quantity"] * s["lastPrices"][symbol]}
                     for symbol, position in sorted(s["positions"].items())]
        stale_days = sum(bool(row["staleSymbols"]) for row in s["curve"])
        history_count = sum(1 for day in self.dates[:s["cursor"]] for symbol in self.securities if day in self.by_date[symbol])
        st_count = sum(1 for day in self.dates[:s["cursor"]] for security in self.securities.values() if day in security.get("stByDate", {}) and day in self.by_date[security["symbol"]])
        quality = {"certified": False, "returnType": "rawPriceReturnWithoutCorporateActionLedger",
                   "calendarSupplied": self.calendar_supplied, "historicalStCoverage": st_count / history_count if history_count else None,
                   "calendarIndependent": self.calendar_supplied and not self.dataset.get("calendarSource", "").startswith("observed"),
                   "pointInTimeComplete": bool(s["daily"]) and all(row["coverage"].get("pointInTimeComplete") is True for row in s["daily"]),
                   "supplied": deepcopy(self.dataset.get("dataQuality", {})),
                   "staleValuationDays": stale_days, "quarantinedSymbols": sorted(s["quarantined"]),
                   "corporateActionDates": deepcopy(s["corporateActionDates"]),
                   "unaccountedActionExposure": deepcopy(s["unaccountedActionExposure"]),
                   "coverage": deepcopy(s["daily"][-1]["coverage"]) if s["daily"] else {}}
        quality["benchmark"] = {"available": bool(self.benchmark_by_date),
                                 "symbol": (self.dataset.get("benchmark") or {}).get("symbol"),
                                 "firstDate": s["benchmarkFirstDate"], "lastPriceDate": s["benchmarkLastDate"],
                                 "returnType": "suppliedIndexClosePriceReturnWithoutFees",
                                 "fullIntervalComparable": bool(s["curve"]) and s["benchmarkFirstDate"] == self.dates[0] and s["benchmarkLastDate"] == s["curve"][-1]["date"]}
        metrics = {"initialCapital": self.config["initialCapital"], "finalEquity": equity,
                   "totalReturnPct": (equity / self.config["initialCapital"] - 1) * 100,
                   "annualizedReturnPct": annualized, "maxDrawdownPct": max((row["drawdownPct"] for row in s["curve"]), default=0.),
                   "sharpe": mean(returns) / dispersion * math.sqrt(252) if dispersion > 1e-15 else None,
                   "tradeCount": len(s["trades"]), "buyCount": sum(trade["side"] == "buy" for trade in s["trades"]),
                   "sellCount": sum(trade["side"] == "sell" for trade in s["trades"]),
                   "totalFees": s["totalFees"], "totalSlippage": s["totalSlippage"],
                   "turnoverPct": s["grossTradedAmount"] / self.config["initialCapital"] * 100,
                   "staleValuationDays": stale_days, "observedDays": len(s["curve"]),
                   "costRatioPct": (s["totalFees"] + s["totalSlippage"]) / self.config["initialCapital"] * 100,
                   "costRatioDefinition": "feesPlusSlippageDividedByInitialCapital;slippageAlreadyInFillPrices",
                   "winRateDefinition": "fullyClosedPositionEpisodesIncludingAllPartialSellPnLExcludingUnbookedCorporateActions"}
        sales = [trade for trade in s["trades"] if trade["side"] == "sell"]
        metrics["closedTradeCount"] = len(s["closedTrades"])
        metrics["winRatePct"] = sum(trade["pnlAmount"] > 0 for trade in s["closedTrades"]) / len(s["closedTrades"]) * 100 if s["closedTrades"] else None
        metrics["sellFillWinRatePct"] = sum(trade["realizedPnl"] > 0 for trade in sales) / len(sales) * 100 if sales else None
        comparable = quality["benchmark"]["fullIntervalComparable"]
        metrics["benchmarkReturnPct"] = (s["benchmarkLastClose"] / s["benchmarkFirstClose"] - 1) * 100 if comparable else None
        metrics["excessReturnPct"] = metrics["totalReturnPct"] - metrics["benchmarkReturnPct"] if comparable else None
        regime_returns = {}
        for row in s["curve"]:
            regime = row["regimeForReturn"]
            label = (str(regime.get("trend", "unknown")) + "/" + str(regime.get("volatility", "unknown"))) if isinstance(regime, dict) and regime.get("status") == "observed" else "unknown"
            if row["dailyReturn"] is not None:
                regime_returns.setdefault(label, []).append(row["dailyReturn"])
        regime_metrics = []
        for label, sample in sorted(regime_returns.items()):
            growth = math.prod(1 + value for value in sample)
            conditional_annualized = None
            try:
                conditional_annualized = (growth ** (252 / len(sample)) - 1) * 100 if growth > 0 else None
                if conditional_annualized is not None and not math.isfinite(conditional_annualized):
                    conditional_annualized = None
            except OverflowError:
                pass
            regime_metrics.append({"regime": label, "sampleDays": len(sample), "subsampleReturnPct": (growth - 1) * 100,
                                   "conditionalAnnualizedReturnPct": conditional_annualized,
                                   "meanDailyReturnPct": mean(sample) * 100,
                                   "definition": "previousCloseBenchmarkState;noncontiguousConditionalSamplesNotContinuousCAGR;doesNotChangeWeights"})
        metrics["regimeMetrics"] = regime_metrics
        return {"status": "completed" if s["cursor"] == len(self.dates) else "running", "config": deepcopy(self.config),
                "versions": {"engine": ENGINE_VERSION, "rules": RULES_VERSION, "scorer": SCORER_VERSION}, "fingerprint": self.fingerprint,
                "dataDigest": self.data_digest, "metrics": metrics, "curve": deepcopy(s["curve"]),
                "trades": deepcopy(s["trades"]), "closedTrades": deepcopy(s["closedTrades"]), "decisions": deepcopy(s["decisions"]), "daily": deepcopy(s["daily"]),
                "regimeMetrics": regime_metrics,
                "positions": positions, "pendingTarget": deepcopy(s["pendingTarget"]), "forcedExits": deepcopy(s["forcedExits"]),
                "quality": quality, "warnings": list(s["warnings"])}
