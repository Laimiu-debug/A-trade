"""Causal target-weight portfolios with one shared, resumable cash ledger.

Signals and budgets are observed at close. Orders use a later session's open,
previous-session volume, and available cash. OHLC extrema never imply a fill.
Corporate actions are booked from dated source evidence under a declared net-cash model.
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
from .portfolio_rotation import ROTATION_MODES, decide_rotation, regime_label
from .portfolio_actions import build_schedule, apply_actions, capture_rights, asset_adjustment
from .execution_evidence import opening_capacity, audit_opening_fills

ENGINE_VERSION = "portfolio-shared-ledger-v6-execution-evidence"
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
    def __init__(self, dataset, config=None, state=None, *, _shared=None):
        self.config = normalize_config(config)
        self.dataset = dataset
        if _shared is None:
            self._validate_dataset(dataset)
            self.data_digest = _digest(dataset)
            self.fingerprint = str(dataset.get("fingerprint") or self.data_digest)
            self.scorer = DailyScorer(dataset, self.config)
        else:
            # A rotation's single-strategy shadow ledgers differ only in dates
            # and allocation. Bars, validation and full-pool scores are shared.
            for key in ("securities", "bars", "by_date", "calendar_supplied", "all_dates", "calendar_index",
                        "benchmark_by_date", "data_digest", "fingerprint", "scorer", "action_schedule", "history_counts", "st_counts"):
                setattr(self, key, getattr(_shared, key))
            self.dates = [day for day in self.all_dates if self.config["startDate"] <= day <= self.config["endDate"]]
            score_keys = ("strategies", "combinations", "universe", "poolSettings")
            if any(self.config[key] != _shared.config[key] for key in score_keys):
                self.scorer = (DailyScorer(dataset, self.config, _prepared=_shared.scorer)
                               if hasattr(_shared.scorer, "series") else DailyScorer(dataset, self.config))
        self.rotation_enabled = self.config["allocation"]["mode"] in ROTATION_MODES
        identifiers = self.config["allocation"]["strategyIds"]
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
            "actionEntitlements": {}, "processedActions": [], "actionLedger": [], "cashReceivables": [],
            "actionPayables": [], "reservedActionCash": 0., "observedBarCount": 0, "observedStCount": 0,
            "carriedHistoryDays": 0,
            "executionCapacityIssues": [], "unverifiedOpeningFills": [],
            "preExRights": {},
            "rotationWeights": {key: 1. / len(identifiers) for key in identifiers},
            "rotationLastAdjustment": None, "rotationLog": [], "shadowStates": {},
            "warnings": [
                "研究假设：收盘形成目标，下一交易日开盘先卖后买，共用现金；日内高低价不构成成交依据。",
                "未模拟竞价队列；有开盘量证据时限制开盘容量，缺少证据时按昨日量近似并标记未验证；超当日总量的成交阻止认证。",
                "现金权益采用数据包声明的税后金额，不自动推断个人持股期限补税；缺失权益证据不能认证总回报。",
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
        self._init_shadows()

    def _init_shadows(self):
        self.shadows = {}
        if not self.rotation_enabled:
            return
        first = self.calendar_index[self.dates[0]]
        warmup_start = self.all_dates[max(0, first - self.config["rotation"]["warmupDays"])]
        for identifier in self.config["allocation"]["strategyIds"]:
            config = deepcopy(self.config)
            config.update({"startDate": warmup_start, "endDate": self.dates[-1],
                           "allocation": {"mode": "single", "strategyIds": [identifier]}})
            shadow = PortfolioEngine(self.dataset, config, self.state["shadowStates"].get(identifier), _shared=self)
            self.shadows[identifier] = shadow
            self.state["shadowStates"][identifier] = shadow.state
        self._warning("轮动预算来自扣除完整交易成本的单策略 Top N 影子组合；独立回测仅作诊断，持续性门控和样本外价值结论须由研究任务提供。")

    def _advance_shadows(self, day, limit=20000):
        # Advance in calendar order across strategies. DailyScorer caches the
        # current day, so each full-universe cross-section is computed once.
        advanced = 0
        while self.shadows and advanced < limit:
            pending = [shadow.dates[shadow.state["cursor"]] for shadow in self.shadows.values()
                       if shadow.state["cursor"] < len(shadow.dates)]
            if not pending or min(pending) > day:
                break
            next_day = min(pending)
            snapshots = {key: shadow._transaction_snapshot() for key, shadow in self.shadows.items()}
            try:
                for shadow in self.shadows.values():
                    if shadow.state["cursor"] < len(shadow.dates) and shadow.dates[shadow.state["cursor"]] == next_day:
                        shadow.step(1)
            except Exception:
                for key, shadow in self.shadows.items():
                    shadow._transaction_restore(snapshots[key])
                raise
            advanced += 1
        return advanced

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
            for field in ("stByDate", "precloseByDate", "factorByDate", "openingVolumeByDate", "tradeStatusByDate", "memberHistory", "memberByDate"):
                mapping = security.get(field, {})
                if not isinstance(mapping, dict):
                    raise ValueError(f"{field} 必须是日期映射")
                for day, value in mapping.items():
                    _day(day)
                    if field in ("stByDate", "memberHistory", "memberByDate") and type(value) is not bool:
                        raise ValueError(f"{field} 需要布尔值")
                    if field in ("precloseByDate", "factorByDate"):
                        _finite(value, field, .000001, 1e8)
                    if field == "openingVolumeByDate":
                        _finite(value, field, 0, 1e15)
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
        self.action_schedule = build_schedule(self.securities)
        self.history_counts, self.st_counts = {}, {}
        for symbol, rows in self.bars.items():
            for row in rows:
                day = row["date"]
                self.history_counts[day] = self.history_counts.get(day, 0) + 1
                if day in self.securities[symbol].get("stByDate", {}):
                    self.st_counts[day] = self.st_counts.get(day, 0) + 1

    def _restore(self, state):
        if not isinstance(state, dict) or set(state) != set(self.state):
            raise ValueError("恢复状态字段不完整")
        try:
            json.dumps(state, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("恢复状态含非法值") from None
        # Old snapshots may omit newly introduced defaults. Compare their
        # effective configuration, without relaxing any version/data checks.
        if not isinstance(state["config"], dict):
            raise ValueError("恢复配置无效")
        restored_config = normalize_config(state["config"])
        if restored_config != self.config:
            raise ValueError("配置、数据或引擎版本已改变，不能恢复")
        for key in ("engineVersion", "rulesVersion", "scorerVersion", "dataDigest", "datasetFingerprint"):
            if state[key] != self.state[key]:
                raise ValueError("配置、数据或引擎版本已改变，不能恢复")
        cursor = state["cursor"]
        carried = state["carriedHistoryDays"]
        if type(carried) is not int or carried < 0 or type(cursor) is not int or not 0 <= cursor <= len(self.dates) or len(state["curve"]) != cursor + carried or len(state["daily"]) != cursor + carried:
            raise ValueError("恢复进度不一致")
        _finite(state["cash"], "cash")
        weights = state["rotationWeights"]
        if not isinstance(weights, dict) or set(weights) != set(self.config["allocation"]["strategyIds"]):
            raise ValueError("恢复轮动策略权重无效")
        for value in weights.values():
            _finite(value, "rotationWeights", 0, 1)
        if abs(sum(weights.values()) - 1) > 1e-8:
            raise ValueError("恢复轮动权重之和须为 1")
        adjustment = state["rotationLastAdjustment"]
        latest_index = self.calendar_index[state["curve"][-1]["date"]] if state["curve"] else -1
        if adjustment is not None and (type(adjustment) is not int or not 0 <= adjustment <= latest_index):
            raise ValueError("恢复轮动冷却进度无效")
        expected_shadows = set(self.config["allocation"]["strategyIds"]) if self.rotation_enabled else set()
        if not isinstance(state["shadowStates"], dict) or set(state["shadowStates"]) not in (set(), expected_shadows):
            raise ValueError("恢复影子账户不完整")
        if cursor and set(state["shadowStates"]) != expected_shadows:
            raise ValueError("恢复影子账户进度缺失")
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
        self.state["config"] = restored_config

    @property
    def progress(self):
        cursor = self.state["cursor"]
        warmup_total = warmup_done = 0
        if self.shadows:
            shadow = next(iter(self.shadows.values()))
            warmup_total = sum(day < self.dates[0] for day in shadow.dates)
            warmup_done = min(shadow.state["cursor"], warmup_total)
        return {"progress": (warmup_done + cursor) / (warmup_total + len(self.dates)),
                "phase": "completed" if cursor == len(self.dates) else "warmingShadowAccounts" if warmup_done < warmup_total else "backtesting",
                "completedDays": cursor, "totalDays": len(self.dates), "warmupDays": warmup_total, "completedWarmupDays": warmup_done}

    def checkpoint(self):
        return deepcopy(self.state)

    def _transaction_snapshot(self):
        append_only = ("curve", "trades", "closedTrades", "decisions", "daily", "warnings", "corporateActionDates", "unaccountedActionExposure", "rotationLog", "actionLedger", "processedActions")
        immutable = ("config", "engineVersion", "rulesVersion", "scorerVersion", "dataDigest", "datasetFingerprint", "shadowStates")
        return ({key: len(self.state[key]) for key in append_only},
                {key: deepcopy(value) for key, value in self.state.items() if key not in append_only + immutable})

    def _transaction_restore(self, snapshot):
        lengths, values = snapshot
        self.state.update(values)
        for key, length in lengths.items():
            del self.state[key][length:]

    def step(self, days=5):
        if type(days) is not int or not 1 <= days <= 20000:
            raise ValueError("推进天数必须为 1..20000 整数")
        if self.shadows and self.state["cursor"] == 0:
            before_first = self.calendar_index[self.dates[0]] - 1
            if before_first >= 0:
                days -= self._advance_shadows(self.all_dates[before_first], limit=days)
        for _ in range(min(days, len(self.dates) - self.state["cursor"])):
            rollback = self._transaction_snapshot()
            shadow_rollbacks = {key: shadow._transaction_snapshot() for key, shadow in self.shadows.items()}
            try:
                self._step_day(self.dates[self.state["cursor"]])
            except Exception:
                self._transaction_restore(rollback)
                for key, shadow in self.shadows.items():
                    shadow._transaction_restore(shadow_rollbacks[key])
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
                if any(stage == "ex" and row["symbol"] == symbol and row["eventKey"] in self.state["processedActions"]
                       for stage, row in self.action_schedule.get(day, [])):
                    continue
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
        excluded_entries = self._excluded_today(day)
        for side in ("sell", "buy"):
            for symbol in self._priority_order(pending["quantities"], pending["priorityBySymbol"]):
                if symbol in s["forcedExits"]:
                    continue  # independent liquidation owns this security
                if side == "buy" and symbol in excluded_entries:
                    continue  # a completed risk exit must not revive an older entry
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
        capacity = opening_capacity(self.securities[symbol], day, self.bars[symbol][index - 1]["volume"], self.config["maxParticipationRate"])
        position = s["positions"].get(symbol)
        quantity_before = position["quantity"] if position else 0
        pnl = None
        if side == "sell":
            sellable = sum(lot["quantity"] for lot in position["lots"] if lot["date"] < day and lot.get("unlockDate", lot["date"]) <= day)
            if sellable == 0:
                self._decision(day, symbol, side, "deferred", "tPlusOne", signalDate=pending["signalDate"])
                return
            desired = min(requested, capacity, sellable)
            quantity = desired if desired == position["quantity"] else _quantity(desired, minimum, increment)
            if not quantity:
                self._decision(day, symbol, side, "deferred", "previousVolumeOrLotMinimum", signalDate=pending["signalDate"], requestedQuantity=requested)
                return
            fee = self._fees(quantity * price, side, day)
            if s["cash"] - s["reservedActionCash"] + quantity * price < fee:
                self._decision(day, symbol, side, "deferred", "insufficientSellFeeCash", signalDate=pending["signalDate"])
                return
            left, cost, retained = quantity, 0., []
            for lot in position["lots"]:
                sold = min(left, lot["quantity"]) if lot["date"] < day and lot.get("unlockDate", lot["date"]) <= day else 0
                allocated = lot["cost"] * sold / lot["quantity"]
                left -= sold
                cost += allocated
                if lot["quantity"] > sold:
                    retained.append({**lot, "quantity": lot["quantity"] - sold, "cost": lot["cost"] - allocated})
            position["lots"] = retained
            position["quantity"] -= quantity
            position["costBasis"] -= cost
            s["cash"] += quantity * price - fee
            pnl = quantity * price - fee - cost
            position["realizedPnl"] += pnl
            if not position["quantity"]:
                s["closedTrades"].append({"symbol": symbol, "entryDate": position["entryDate"], "exitDate": day,
                                           "pnlAmount": position["realizedPnl"], "investedAmount": position["investedAmount"],
                                           "returnPct": position["realizedPnl"] / position["investedAmount"] * 100 if position["investedAmount"] else None,
                                           "holdingDays": self.calendar_index[day] - self.calendar_index[position["entryDate"]] + 1})
                del s["positions"][symbol]
        else:
            if position is None and len(s["positions"]) >= self.config["maxPositions"]:
                self._decision(day, symbol, side, "deferred", "positionSlotsOccupied", signalDate=pending["signalDate"], requestedQuantity=requested)
                return
            ceiling = _quantity(min(requested, capacity), minimum, increment)
            target_budget = max(0., pending["budgets"][symbol] - (position["quantity"] * price if position else 0.))
            quantity = self._affordable_quantity(ceiling, minimum, increment, price, min(max(0., s["cash"] - s["reservedActionCash"]), target_budget), day)
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
                "reason": pending.get("reason", "targetRebalance"), "targetWeight": pending["weights"].get(symbol, 0.),
                "quantityBefore": quantity_before, "quantityAfter": s["positions"].get(symbol, {}).get("quantity", 0),
                "positionAction": ("open" if not quantity_before else "add") if side == "buy" else ("exit" if quantity == quantity_before else "reduce"),
                "unfilledQuantity": max(0, requested - quantity)}
        risk_evidence = {"riskEvidence": deepcopy(pending["riskEvidence"])} if pending.get("riskEvidence") else {}
        fill.update(risk_evidence)
        s["trades"].append(fill)
        self._decision(day, symbol, side, "filled", pending.get("reason", "targetRebalance"), signalDate=pending["signalDate"], quantity=quantity, price=price,
                       unfilledQuantity=max(0, requested - quantity), **risk_evidence)

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
                elif c["maxHoldingDays"] and self.calendar_index[day] - self.calendar_index[position["entryDate"]] + 1 >= c["maxHoldingDays"]:
                    reason = "maxHoldingDays"
            if reason and symbol not in s["forcedExits"]:
                observed_close = bar["close"] if bar else None
                risk_evidence = {"observedDate": day, "observedClose": observed_close,
                                 "riskEntryPrice": position["riskEntryPrice"], "closePeak": position["closePeak"],
                                 "gainPct": observed_close / position["riskEntryPrice"] - 1 if observed_close is not None else None,
                                 "drawdownFromPeakPct": observed_close / position["closePeak"] - 1 if observed_close is not None else None,
                                 "holdingDays": self.calendar_index[day] - self.calendar_index[position["entryDate"]] + 1,
                                 "stopLossPct": c["stopLossPct"], "takeProfitPct": c["takeProfitPct"],
                                 "trailingStopPct": c["trailingStopPct"], "maxHoldingDays": c["maxHoldingDays"],
                                 "percentageUnit": "ratio"}
                s["forcedExits"][symbol] = {"signalDate": day, "reason": reason, "riskEvidence": risk_evidence}
                pending = s["pendingTarget"]
                if pending is not None:
                    # A held rotation target may survive several closes. Drop
                    # the risk-exited name permanently from that old intention,
                    # including after its forced sell has finished.
                    for key in ("weights", "budgets", "priorityBySymbol", "signalDates"):
                        pending.get(key, {}).pop(symbol, None)
                    pending["quantities"][symbol] = 0
                self._decision(day, symbol, "sell", "queued", reason, signalDate=day,
                               observedClose=observed_close, riskEvidence=deepcopy(risk_evidence))
            elif eligibility.get(symbol, {}).get("reason") == "amount20BelowMinimum":
                self._decision(day, symbol, "hold", "held", "liquidityFilterFailedButRiskHoldingRetained")

    def _excluded_today(self, day):
        # A liquidation is not cancelled by another strategy selecting the same
        # stock; do not immediately rebuy a risk exit on its completion date.
        return set(self.state["forcedExits"]) | {trade["symbol"] for trade in self.state["trades"]
                                               if trade["date"] == day and trade["side"] == "sell" and trade["reason"] not in ("targetRebalance", "strategyRotation")}

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
            strategy_weights[identifier] = self.state["rotationWeights"][identifier] if self.rotation_enabled else 1. / len(identifiers)
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
            if strategy_weights[identifier] <= 1e-15:
                continue
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

    def _next_session(self, day):
        index = self.calendar_index[day] + 1
        return self.all_dates[index] if index < len(self.all_dates) else None

    def _daily_holdings(self, day, equity):
        """Close-time facts; future sellability uses only already recorded lots."""
        s, next_day = self.state, self._next_session(day)
        result = []
        for symbol, position in sorted(s["positions"].items()):
            quantity, cost = position["quantity"], position["costBasis"]
            mark, price_date = s["lastPrices"][symbol], s["lastPriceDates"][symbol]
            market_value = quantity * mark
            row = {"symbol": symbol, "quantity": quantity, "costBasis": cost,
                   "marketValue": market_value, "weight": market_value / equity if equity else 0.,
                   "markPrice": mark, "priceDate": price_date, "costPrice": cost / quantity if quantity else None,
                   "unrealizedPnl": market_value - cost, "unrealizedPnlPct": (market_value / cost - 1) * 100 if cost else None,
                   "holdingDays": self.calendar_index[day] - self.calendar_index[position["entryDate"]] + 1}
            if next_day:
                row["nextSessionSellableQuantity"] = sum(lot["quantity"] for lot in position["lots"]
                    if lot["date"] < next_day and lot.get("unlockDate", lot["date"]) <= next_day)
            result.append(row)
        return result

    def _position_control(self, day, curve, rebalance):
        s, c = self.state, self.config
        next_day = self._next_session(day)
        # A continuous handoff also snapshots the preceding close, before this
        # engine's first date (relative day -1), while its cursor is already 0.
        relative_day = self.calendar_index[day] - self.calendar_index[self.dates[0]]
        next_rebalance = relative_day + c["rebalanceEvery"] - relative_day % c["rebalanceEvery"]
        return {"equity": curve["equity"], "cash": s["cash"],
                "availableCash": max(0., s["cash"] - s["reservedActionCash"]),
                "reservedActionCash": s["reservedActionCash"],
                "receivables": sum(row["amount"] for row in s["cashReceivables"]),
                "payables": sum(row["amount"] for row in s["actionPayables"]),
                "preExRightsValue": sum(row["amount"] for row in s["preExRights"].values()),
                "stockMarketValue": curve["marketValue"],
                "stockWeight": curve["marketValue"] / curve["equity"] if curve["equity"] else 0.,
                "holdCount": len(s["positions"]), "maxPositions": c["maxPositions"],
                "maxStockWeight": c["maxStockWeight"], "maxStockWeightMeaning": "newTargetWeightCapNotHardHoldingLimit",
                "rebalanceMode": c["rebalanceMode"], "rebalanceEvery": c["rebalanceEvery"],
                "rebalanceDueToday": rebalance,
                "nextScheduledRebalanceDate": self.dates[next_rebalance] if next_rebalance < len(self.dates) else None,
                "nextSessionDate": next_day,
                "nextSessionInBacktest": bool(next_day and next_day <= self.dates[-1]),
                "valuationReliable": curve["valuationReliable"]}

    def _next_session_plan(self, day, holdings):
        """Report the live close-time intention, without reading a future quote.

        A frozen quantity wins over budget/weight drift, notably in risk mode.
        Before the first opening, a budget determines only an estimated direction.
        """
        s, pending = self.state, self.state["pendingTarget"]
        weights, quantities = (pending or {}).get("weights", {}), (pending or {}).get("quantities", {})
        held = {row["symbol"]: row for row in holdings}
        symbols = set(held) | set(weights) | set(quantities) | set(s["forcedExits"])
        # Only live signal dates matter; do not rescan the entire completed ledger.
        wanted = {(symbol, s["forcedExits"][symbol]["signalDate"] if symbol in s["forcedExits"] else
                   pending.get("signalDates", {}).get(symbol, pending["signalDate"])) for symbol in symbols} if pending else {
                       (symbol, order["signalDate"]) for symbol, order in s["forcedExits"].items()}
        earliest = min((signal for _, signal in wanted), default=day)
        attempts = {}
        for record in reversed(s["decisions"]):
            if record["date"] < earliest or not wanted:
                break
            key = (record["symbol"], record.get("signalDate"))
            if key not in wanted or record["action"] not in ("buy", "sell", "rebalance") or record["status"] not in ("filled", "deferred"):
                continue
            attempts[key] = record
            wanted.remove(key)
        rows = []
        for symbol in sorted(symbols):
            current = held.get(symbol, {})
            quantity = current.get("quantity", 0)
            value = current.get("marketValue", 0.)
            mark, price_date = current.get("markPrice"), current.get("priceDate")
            if mark is None:
                latest = next((bar for bar in reversed(self.bars[symbol]) if bar["date"] <= day and bar["volume"] > 0), None)
                if latest:
                    mark, price_date = latest["close"], latest["date"]
            forced = s["forcedExits"].get(symbol)
            if forced:
                target, weight, budget = 0, 0., 0.
                signal_date, reason, basis = forced["signalDate"], forced["reason"], "frozen"
            elif pending:
                weight, budget = weights.get(symbol, 0.), pending["budgets"].get(symbol, 0.)
                target = quantities.get(symbol, 0 if weight == 0 else None)
                signal_date = pending.get("signalDates", {}).get(symbol, pending["signalDate"])
                reason, basis = pending["reason"], "frozen" if target is not None else "nextOpen"
            else:
                target, weight, budget = None, None, None
                signal_date, reason, basis = None, "noPendingTarget", "holding"
            estimated = target is None and pending is not None
            delta = target - quantity if target is not None else None
            if basis == "holding" or delta == 0:
                action, outstanding = "hold", False
            elif target is not None:
                action = ("open" if not quantity else "add") if delta > 0 else ("exit" if target == 0 else "reduce")
                outstanding = True
            elif not quantity:
                action, outstanding = "open", True
            else:
                # Equality at the close cannot prove tomorrow's opening lot count.
                gap = budget - value
                action = "rebalance" if math.isclose(budget, value, rel_tol=1e-12, abs_tol=1e-8) else "add" if gap > 0 else "reduce"
                outstanding = True
            attempt = attempts.get((symbol, signal_date)) if signal_date else None
            execution = "completed" if not outstanding else "notAttempted" if attempt is None else (
                "deferred" if attempt["status"] == "deferred" else "partial" if attempt.get("unfilledQuantity", 0) > 0 else "pending")
            rows.append({"symbol": symbol, "action": action,
                         "status": ("new" if signal_date == day else "carried") if outstanding else "holding" if basis == "holding" else "completed",
                         "pending": outstanding, "currentQuantity": quantity,
                         "currentWeight": current.get("weight", 0.), "currentMarketValue": value,
                         "targetQuantity": target, "quantityDelta": delta,
                         "targetWeight": weight, "targetBudget": budget, "signalDate": signal_date, "reason": reason,
                         "lastBlockReason": attempt["reason"] if outstanding and attempt and attempt["status"] == "deferred" else None,
                         "lastAttemptDate": attempt["date"] if attempt else None, "executionStatus": execution,
                         "directionEstimated": estimated, "quantityBasis": basis,
                         "valuationBasis": "close" if price_date == day else "lastAvailable",
                         "markPrice": mark, "priceDate": price_date})
            if forced and forced.get("riskEvidence"):
                rows[-1]["riskEvidence"] = deepcopy(forced["riskEvidence"])
        next_day = self._next_session(day)
        return {"asOfDate": day, "nextSessionDate": next_day,
                "nextSessionInBacktest": bool(next_day and next_day <= self.dates[-1]),
                "hasPendingOrders": any(row["pending"] for row in rows), "rows": rows}

    def _step_day(self, day):
        s = self.state
        decision_start, fill_start = len(s["decisions"]), len(s["trades"])
        action_start = len(s["actionLedger"])
        apply_actions(self, day)
        self._observe_actions(day)
        self._execute_target(day)
        # Whole-day volume is only an ex-post consistency check. It never
        # participates in opening quantities, priority or available cash.
        fills = s["trades"][fill_start:]
        issues, unverified = audit_opening_fills(fills, self.securities,
            {fill["symbol"]: self._bar(fill["symbol"], day)[0] for fill in fills})
        s["executionCapacityIssues"].extend(issues)
        s["unverifiedOpeningFills"].extend(unverified)
        if issues:
            self._warning("存在超过当日成交总量的模拟成交或矛盾的开盘量证据；本次账户路径无效，不参与平原选择或认证。")
        stale = []
        known_halts = []
        for symbol in sorted(self.securities):
            bar, index = self._bar(symbol, day)
            if bar is not None and index and bar["volume"] > 0 and abs(bar["close"] / self._reference(symbol, index) - 1) > nominal_limit(symbol, day) + .005:
                s["quarantined"].setdefault(symbol, day)
                self._decision(day, symbol, "dataQuality", "quarantined", "rawCloseDiscontinuity")
                self._warning(f"{symbol} 存在无法解释的原始价格断点，后续成交隔离；相关净值不可用于有效性结论。")
        for symbol in sorted(s["positions"]):
            bar, _ = self._bar(symbol, day)
            known_suspension = (bar is not None and self.dataset.get("dataQuality", {}).get("certified") is True
                                and self.securities[symbol].get("tradeStatusByDate", {}).get(day) in (False, 0, "0"))
            if bar is not None and (bar["volume"] > 0 or known_suspension):
                s["lastPrices"][symbol], s["lastPriceDates"][symbol] = bar["close"], day
                if known_suspension and bar["volume"] == 0:
                    known_halts.append(symbol)
                    self._decision(day, symbol, "valuation", "held", "declaredHistoricalSuspensionMark")
            else:
                stale.append(symbol)
                self._decision(day, symbol, "valuation", "stale", "missingOrSuspendedBar", priceDate=s["lastPriceDates"].get(symbol))
        market_value = sum(position["quantity"] * s["lastPrices"][symbol] for symbol, position in s["positions"].items())
        equity = s["cash"] + market_value + asset_adjustment(s)
        s["observedBarCount"] += self.history_counts.get(day, 0)
        s["observedStCount"] += self.st_counts.get(day, 0)
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
                 "staleSymbols": stale, "valuationReliable": not stale and not s["unaccountedActionExposure"] and not s["executionCapacityIssues"] and not any(symbol in s["quarantined"] for symbol in s["positions"]),
                 "knownSuspendedSymbols": known_halts,
                 "benchmarkEquity": benchmark_equity, "benchmarkStale": bool(self.benchmark_by_date) and s["benchmarkLastDate"] != day,
                 "regimeForReturn": deepcopy(s["daily"][-1]["regime"]) if s["daily"] else {"status": "unknown", "reason": "noPriorCloseObservation"}}
        s["curve"].append(curve)
        self._advance_shadows(day)
        scored = self.scorer.daily(day)
        if not isinstance(scored, dict) or not isinstance(scored.get("strategies"), dict):
            raise ValueError("DailyScorer 返回结构无效")
        self._risk_exits(day, scored.get("eligibilityBySymbol", {}))
        rotation_decision = None
        rotation_changed = False
        if self.rotation_enabled:
            rotation_decision = decide_rotation(day, self.calendar_index[day], self.config["allocation"]["mode"], self.config["rotation"],
                                                s["rotationWeights"], s["rotationLastAdjustment"],
                                                {key: shadow.state["curve"] for key, shadow in self.shadows.items()}, scored.get("regime"))
            rotation_changed = rotation_decision["status"] == "adjusted"
            if isinstance(scored.get("regime"), dict):
                used = any(item["regimeBlendApplied"] > 0 for item in rotation_decision["scores"].values())
                scored["regime"].update(eligibleForRotation=self.config["rotation"]["regimeBlend"] > 0,
                                        usedForRotationScoring=used, affectsWeights=used and rotation_changed)
                rotation_decision["regime"] = deepcopy(scored["regime"])
            if rotation_changed:
                s["rotationWeights"] = deepcopy(rotation_decision["after"])
                s["rotationLastAdjustment"] = self.calendar_index[day]
                next_index = self.calendar_index[day] + 1
                rotation_decision["earliestExecutionDate"] = self.all_dates[next_index] if next_index < len(self.all_dates) else None
                s["rotationLog"].append(deepcopy(rotation_decision))
        rebalance = s["cursor"] % self.config["rebalanceEvery"] == 0
        strategy_weights = {identifier: 1. / len(self.config["allocation"]["strategyIds"]) for identifier in self.config["allocation"]["strategyIds"]}
        selected, weights, strategy_weights, details, clipped, priorities = self._select_targets(scored, self._excluded_today(day) | set(s["quarantined"]))
        raw_top_n = {identifier: [{**deepcopy(item), "rank": rank + 1} for rank, item in enumerate(
            sorted([row for row in rows if row.get("eligible", True)], key=lambda row: (-row["score"], row["symbol"]))[:self.config["topN"]])]
                     for identifier, rows in scored["strategies"].items()}
        prior = s["pendingTarget"]
        outstanding_rotation = bool(prior and prior.get("reason") == "strategyRotation" and any(
            symbol not in s["forcedExits"] and (symbol not in prior["quantities"] or prior["quantities"][symbol] != s["positions"].get(symbol, {}).get("quantity", 0))
            for symbol in set(prior["weights"]) | set(s["positions"])))
        queue_target = rebalance or bool(s["forcedExits"]) or rotation_changed
        if self.config["rebalanceMode"] == "risk" and outstanding_rotation and not rotation_changed:
            # Retain blocked/partial rotation orders until executable. Risk
            # events still liquidate independently through forcedExits.
            queue_target = False
        if queue_target:
            quantities = {}
            carry_budgets, carry_dates = {}, {}
            if self.config["rebalanceMode"] == "risk" and not rotation_changed:
                weights, quantities, carry_budgets, carry_dates, priorities = self._risk_targets(weights, priorities, equity, day)
            elif not rebalance and not rotation_changed and s["pendingTarget"]:
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
                                  "reason": "strategyRotation" if rotation_changed else "targetRebalance",
                                  "priorityBySymbol": priorities,
                                  "budgets": {**{symbol: weight * equity for symbol, weight in weights.items()}, **carry_budgets},
                                  "signalDates": {**{symbol: day for symbol in weights}, **carry_dates}}
            for symbol in sorted(set(weights) | set(s["positions"])):
                self._decision(day, symbol, "target", "queued", "strategyRotation" if rotation_changed else "scheduledRebalance", targetWeight=weights.get(symbol, 0.), signalEquity=equity)
        pending = s["pendingTarget"]
        holdings = self._daily_holdings(day, equity)
        s["daily"].append({"date": day, "rebalance": rebalance, "topNByStrategy": raw_top_n, "selectedCandidatesByStrategy": details,
                           "rotationDecision": rotation_decision,
                           "selectedByStrategy": deepcopy(s["selectedByStrategy"]), "strategyWeights": strategy_weights,
                           "strategyBudgetWeights": strategy_weights, "strategyWeightMeaning": "nextOpenTargetBudgetNotRealizedHoldingAttribution" if self.rotation_enabled else "fixedEntryBudgetNotRealizedHoldingAttribution",
                           "targetWeights": deepcopy(pending["weights"] if pending else {}), "targetSignalDate": pending["signalDate"] if pending else None,
                           "targetPriority": self._priority_order(pending["weights"], pending["priorityBySymbol"]) if pending else [],
                           "targetPriorityBySymbol": deepcopy(pending["priorityBySymbol"] if pending else {}),
                           "holdings": holdings, "actualStockWeights": {row["symbol"]: row["weight"] for row in holdings},
                           "positionControl": self._position_control(day, curve, rebalance),
                           "nextSessionPlan": self._next_session_plan(day, holdings),
                           "coverage": deepcopy(scored.get("coverage", {})),
                           "poolCounts": {key: len(values) for key, values in scored.get("pools", {}).items()},
                           "regime": deepcopy(scored.get("regime")), "clippedTargets": clipped,
                           "activities": deepcopy(s["decisions"][decision_start:]), "fills": deepcopy(s["trades"][fill_start:]),
                           "targetSelectionPolicy": "aggregateBudgetBeforeCap;strategyBudgetWeightedScore;symbolTieBreak;maxPositionsTruncation;noRedistribution",
                           "cash": s["cash"], "equity": equity})
        capture_rights(self, day)
        s["daily"][-1]["corporateActions"] = deepcopy(s["actionLedger"][action_start:])

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
        history_count, st_count = s["observedBarCount"], s["observedStCount"]
        quality = {"certified": False, "returnType": "rawPriceReturnWithoutCorporateActionLedger",
                   "calendarSupplied": self.calendar_supplied, "historicalStCoverage": st_count / history_count if history_count else None,
                   "calendarIndependent": self.calendar_supplied and not self.dataset.get("calendarSource", "").startswith("observed"),
                   "pointInTimeComplete": bool(s["daily"]) and all(row["coverage"].get("pointInTimeComplete") is True for row in s["daily"]),
                   "supplied": deepcopy(self.dataset.get("dataQuality", {})),
                   "staleValuationDays": stale_days, "quarantinedSymbols": sorted(s["quarantined"]),
                   "corporateActionDates": deepcopy(s["corporateActionDates"]),
                   "unaccountedActionExposure": deepcopy(s["unaccountedActionExposure"]),
                   "executionCapacityIssues": deepcopy(s["executionCapacityIssues"]),
                   "unverifiedOpeningFills": deepcopy(s["unverifiedOpeningFills"]),
                   "openingCapacityVerified": not s["executionCapacityIssues"] and not s["unverifiedOpeningFills"],
                   "coverage": deepcopy(s["daily"][-1]["coverage"]) if s["daily"] else {}}
        quality["knownSuspensionValuationDays"] = sum(bool(row.get("knownSuspendedSymbols")) for row in s["curve"])
        quality["benchmark"] = {"available": bool(self.benchmark_by_date),
                                 "symbol": (self.dataset.get("benchmark") or {}).get("symbol"),
                                 "firstDate": s["benchmarkFirstDate"], "lastPriceDate": s["benchmarkLastDate"],
                                 "returnType": "suppliedIndexClosePriceReturnWithoutFees",
                                 "fullIntervalComparable": bool(s["curve"]) and all(row["date"] in self.benchmark_by_date for row in s["curve"])}
        quality["corporateActionsComplete"] = all(security.get("corporateActionsComplete") is True for security in self.securities.values())
        coverage = self.dataset.get("historyCoverage", {})
        quality["certified"] = bool(s["daily"]) and self.dataset.get("dataQuality", {}).get("certified") is True and (
            coverage.get("startDate", "9999") <= self.dates[0] <= self.dates[-1] <= coverage.get("endDate", "0000")) and (
            quality["calendarIndependent"] and quality["pointInTimeComplete"] and quality["corporateActionsComplete"]
            and not stale_days and not s["quarantined"] and not s["unaccountedActionExposure"] and quality["openingCapacityVerified"])
        quality["returnType"] = "declaredNetCashAndShareRightsTotalReturn" if quality["certified"] else "uncertifiedPriceAndAvailableRightsReturn"
        quality["certificateScope"] = "internallyValidatedDeclaredUniverseAndNetCashModel"
        metrics = {"initialCapital": self.config["initialCapital"], "finalEquity": equity,
                   "totalReturnPct": (equity / self.config["initialCapital"] - 1) * 100,
                   "annualizedReturnPct": annualized, "maxDrawdownPct": max((row["drawdownPct"] for row in s["curve"]), default=0.),
                   "sharpe": mean(returns) / dispersion * math.sqrt(252) if dispersion > 1e-15 else None,
                   "tradeCount": len(s["trades"]), "buyCount": sum(trade["side"] == "buy" for trade in s["trades"]),
                   "sellCount": sum(trade["side"] == "sell" for trade in s["trades"]),
                   "totalFees": s["totalFees"], "totalSlippage": s["totalSlippage"],
                   "turnoverPct": s["grossTradedAmount"] / self.config["initialCapital"] * 100,
                   "staleValuationDays": stale_days, "observedDays": len(s["curve"]),
                   "investedDays": sum(row["marketValue"] > 0 for row in s["curve"]),
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
        benchmark_metrics = None
        if comparable:
            closes = [self.benchmark_by_date[row["date"]] for row in s["curve"]]
            benchmark_daily = [0.] + [closes[index] / closes[index - 1] - 1 for index in range(1, len(closes))]
            benchmark_deviation = stdev(benchmark_daily) if len(benchmark_daily) > 1 else 0.
            benchmark_peak, benchmark_drawdown = closes[0], 0.
            for close in closes:
                benchmark_peak = max(benchmark_peak, close)
                benchmark_drawdown = max(benchmark_drawdown, 1 - close / benchmark_peak)
            benchmark_annualized = None
            try:
                benchmark_annualized = ((closes[-1] / closes[0]) ** (252 / len(closes)) - 1) * 100
                if not math.isfinite(benchmark_annualized):
                    benchmark_annualized = None
            except OverflowError:
                pass
            benchmark_metrics = {"symbol": quality["benchmark"]["symbol"], "observedDays": len(closes),
                                 "totalReturnPct": metrics["benchmarkReturnPct"], "annualizedReturnPct": benchmark_annualized,
                                 "maxDrawdownPct": benchmark_drawdown * 100,
                                 "sharpe": mean(benchmark_daily) / benchmark_deviation * math.sqrt(252) if benchmark_deviation > 1e-15 else None,
                                 "dailyWinRatePct": sum(value > 0 for value in benchmark_daily[1:]) / (len(closes) - 1) * 100 if len(closes) > 1 else None,
                                 "winRatePct": None, "turnoverPct": None, "costRatioPct": None,
                                 "totalFees": None, "totalSlippage": None,
                                 "definition": "suppliedIndexClosePriceReturn;firstCloseBase;noTradableLedgerOrCosts;dailyWinRateExcludesInitialBase"}
        regime_returns, regime_dates, date_regimes, benchmark_returns = {}, {}, {}, {}
        previous_benchmark = None
        for index, row in enumerate(s["curve"]):
            label = regime_label(row["regimeForReturn"])
            date_regimes[row["date"]] = label
            current_benchmark = self.benchmark_by_date.get(row["date"])
            benchmark_returns[row["date"]] = (current_benchmark / previous_benchmark - 1 if previous_benchmark else 0. if index == 0 else None) if current_benchmark else None
            previous_benchmark = current_benchmark
            if row["dailyReturn"] is not None:
                regime_returns.setdefault(label, []).append(row["dailyReturn"])
                regime_dates.setdefault(label, []).append(row["date"])
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
            path, peak, drawdown = 1., 1., 0.
            for value in sample:
                path *= 1 + value
                peak = max(peak, path)
                drawdown = max(drawdown, 1 - path / peak if peak else 0.)
            deviation = stdev(sample) if len(sample) > 1 else 0.
            trades = [trade for trade in s["trades"] if date_regimes.get(trade["date"]) == label]
            episodes = [episode for episode in s["closedTrades"] if date_regimes.get(episode["exitDate"]) == label]
            fees, slippage = sum(t["fee"] for t in trades), sum(t["slippage"] for t in trades)
            benchmarks = [benchmark_returns[day] for day in regime_dates[label]]
            benchmark_return = (math.prod(1 + value for value in benchmarks) - 1) * 100 if all(value is not None for value in benchmarks) else None
            regime_metrics.append({"regime": label, "sampleDays": len(sample), "observedDays": len(sample),
                                   "subsampleReturnPct": (growth - 1) * 100, "totalReturnPct": (growth - 1) * 100,
                                   "conditionalAnnualizedReturnPct": conditional_annualized,
                                   "annualizedReturnPct": conditional_annualized,
                                   "maxDrawdownPct": drawdown * 100,
                                   "sharpe": mean(sample) / deviation * math.sqrt(252) if deviation > 1e-15 else None,
                                   "winRatePct": sum(episode["pnlAmount"] > 0 for episode in episodes) / len(episodes) * 100 if episodes else None,
                                   "closedTradeCount": len(episodes), "tradeCount": len(trades),
                                   "turnoverPct": sum(trade["amount"] for trade in trades) / self.config["initialCapital"] * 100,
                                   "totalFees": fees, "totalSlippage": slippage,
                                   "costRatioPct": (fees + slippage) / self.config["initialCapital"] * 100,
                                   "benchmarkReturnPct": benchmark_return,
                                   "excessReturnPct": (growth - 1) * 100 - benchmark_return if benchmark_return is not None else None,
                                   "meanDailyReturnPct": mean(sample) * 100,
                                   "definition": "previousCloseBenchmarkState;noncontiguousConditionalCompoundedPathNotContinuousCAGR;episodeWinsAssignedByExitDate;costAndTurnoverDenominatorInitialCapital"})
        metrics["regimeMetrics"] = regime_metrics
        return {"status": "completed" if s["cursor"] == len(self.dates) else "running", "config": deepcopy(self.config),
                "versions": {"engine": ENGINE_VERSION, "rules": RULES_VERSION, "scorer": SCORER_VERSION}, "fingerprint": self.fingerprint,
                "dataDigest": self.data_digest, "metrics": metrics, "curve": deepcopy(s["curve"]),
                "trades": deepcopy(s["trades"]), "closedTrades": deepcopy(s["closedTrades"]), "decisions": deepcopy(s["decisions"]), "daily": deepcopy(s["daily"]),
                "regimeMetrics": regime_metrics,
                "benchmarkMetrics": benchmark_metrics,
                "rotationLog": deepcopy(s["rotationLog"]),
                "rotation": {"enabled": self.rotation_enabled, "mode": self.config["allocation"]["mode"],
                             "diagnosticOnly": self.rotation_enabled, "gateStatus": "notEvaluatedByEngine" if self.rotation_enabled else "notApplicable",
                             "settings": deepcopy(self.config["rotation"]), "adjustmentCount": len(s["rotationLog"]),
                             "actualWarmupDays": self.progress["completedWarmupDays"],
                             "currentWeights": deepcopy(s["rotationWeights"]),
                             "shadowAccountCount": len(self.shadows),
                             "shadowReturnDefinition": "completeSingleStrategyTopNSharedCashLedgerNetOfAllCosts;historicalWarmupOnly;actualAccountStartsCash",
                             "shadowThroughDate": {key: shadow.state["curve"][-1]["date"] if shadow.state["curve"] else None for key, shadow in self.shadows.items()}},
                "positions": positions, "pendingTarget": deepcopy(s["pendingTarget"]), "forcedExits": deepcopy(s["forcedExits"]),
                "actionLedger": deepcopy(s["actionLedger"]), "cashReceivables": deepcopy(s["cashReceivables"]),
                "actionPayables": deepcopy(s["actionPayables"]), "reservedActionCash": s["reservedActionCash"],
                "preExRights": deepcopy(s["preExRights"]),
                "quality": quality, "warnings": list(s["warnings"])}
