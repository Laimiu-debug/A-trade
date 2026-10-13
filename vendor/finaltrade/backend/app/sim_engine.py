from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Callable, Literal
from uuid import uuid4

from .utils.persistence import read_state, write_state, StateLoadError, StatePersistenceError

from .models import (
    CandlePoint,
    CreateOrderRequest,
    CreateOrderResponse,
    DrawdownPoint,
    EquityPoint,
    MonthlyReturnPoint,
    PortfolioPosition,
    PortfolioSnapshot,
    ReviewRange,
    ReviewResponse,
    ReviewStats,
    SimFillsResponse,
    SimOrdersResponse,
    SimResetResponse,
    SimSettleResponse,
    SimTradeFill,
    SimTradeOrder,
    SimTradingConfig,
    TradeRecord,
)

OrderStatus = Literal["pending", "filled", "cancelled", "rejected"]
OrderSide = Literal["buy", "sell"]


class SimEngineError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SimAccountEngine:
    _SCHEMA_VERSION = 1

    def __init__(
        self,
        get_candles: Callable[[str], list[CandlePoint]],
        resolve_symbol_name: Callable[[str], str],
        now_date: Callable[[], str],
        now_datetime: Callable[[], str],
        state_path: str | None = None,
    ) -> None:
        self._get_candles = get_candles
        self._resolve_symbol_name = resolve_symbol_name
        self._now_date = now_date
        self._now_datetime = now_datetime
        self._lock = RLock()
        self._state_path = Path(state_path) if state_path else self._default_state_path()
        self._state = self._load_or_init_state()

    @staticmethod
    def _default_state_path() -> Path:
        return Path.home() / ".tdx-trend" / "sim_state.json"

    def _load_or_init_state(self) -> dict[str, object]:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._state_path.exists():
            state = self._default_state()
            self._write_state(state)
            return state
        raw = read_state(self._state_path)
        try:
            return self._migrate_state(raw)
        except (ValueError, TypeError, KeyError) as exc:
            raise StateLoadError(f"模拟账户状态无效，原文件已保留：{self._state_path}") from exc

    def _default_state(self) -> dict[str, object]:
        today = self._now_date()
        now_ts = self._now_datetime()
        config = SimTradingConfig().model_dump()
        capital = float(config["initial_capital"])
        return {
            "schema_version": self._SCHEMA_VERSION,
            "account": {
                "initial_capital": capital,
                "cash": capital,
                "as_of_date": today,
            },
            "config": config,
            "orders": [],
            "fills": [],
            "lots": [],
            "closed_trades": [],
            "audit": {
                "updated_at": now_ts,
                "last_settle_at": now_ts,
            },
        }

    def _migrate_state(self, raw: dict[str, object]) -> dict[str, object]:
        if not isinstance(raw.get('account'), dict) or 'cash' not in raw['account']:
            raise ValueError('missing or invalid account')
        for key in ('orders', 'fills', 'lots', 'closed_trades'):
            if key in raw and (not isinstance(raw[key], list) or any(not isinstance(item, dict) for item in raw[key])):
                raise ValueError(f'invalid {key}')
        base = self._default_state()
        base["schema_version"] = self._SCHEMA_VERSION

        account = raw.get("account")
        if isinstance(account, dict):
            base_account = base["account"] if isinstance(base["account"], dict) else {}
            base["account"] = {
                "initial_capital": float(account.get("initial_capital", base_account.get("initial_capital", 1_000_000))),
                "cash": float(account.get("cash", base_account.get("cash", 1_000_000))),
                "as_of_date": str(account.get("as_of_date", base_account.get("as_of_date", self._now_date()))),
            }
        config = raw.get("config")
        if isinstance(config, dict):
            merged = SimTradingConfig(**{**SimTradingConfig().model_dump(), **config})
            base["config"] = merged.model_dump()
        for key in ("orders", "fills", "lots", "closed_trades"):
            value = raw.get(key)
            if isinstance(value, list):
                base[key] = value
        audit = raw.get("audit")
        if isinstance(audit, dict):
            base_audit = base["audit"] if isinstance(base["audit"], dict) else {}
            base["audit"] = {
                "updated_at": str(audit.get("updated_at", base_audit.get("updated_at", self._now_datetime()))),
                "last_settle_at": str(audit.get("last_settle_at", base_audit.get("last_settle_at", self._now_datetime()))),
            }
        return base

    def _write_state(self, state: dict[str, object]) -> None:
        write_state(self._state_path, state)

    def _persist(self) -> None:
        audit = self._state.get("audit")
        if isinstance(audit, dict):
            audit["updated_at"] = self._now_datetime()
        try:
            self._write_state(self._state)
        except StatePersistenceError:
            self._state = self._migrate_state(read_state(self._state_path))
            raise

    @staticmethod
    def _parse_date(date_text: str) -> datetime | None:
        try:
            return datetime.strptime(date_text, "%Y-%m-%d")
        except Exception:
            return None

    @staticmethod
    def _days_between(start: str, end: str) -> int:
        a = SimAccountEngine._parse_date(start)
        b = SimAccountEngine._parse_date(end)
        if not a or not b:
            return 0
        return max(0, (b - a).days)

    def _account(self) -> dict[str, object]:
        account = self._state.get("account")
        if not isinstance(account, dict):
            raise SimEngineError("SIM_STATE_CORRUPTED", "模拟账户状态损坏，请重置账户。")
        return account

    def _config(self) -> SimTradingConfig:
        raw = self._state.get("config")
        if not isinstance(raw, dict):
            raise SimEngineError("SIM_STATE_CORRUPTED", "模拟交易配置损坏，请重置账户。")
        return SimTradingConfig(**raw)

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return symbol.strip().lower()

    def _get_symbol_candles_or_raise(self, symbol: str) -> list[CandlePoint]:
        candles = self._get_candles(symbol)
        if not candles:
            raise SimEngineError("SIM_PRICE_NOT_FOUND", f"未找到 {symbol} 的行情数据")
        return candles

    def _align_index_to_submit_date(self, candles: list[CandlePoint], submit_date: str) -> int:
        target = self._parse_date(submit_date)
        if target is None:
            raise SimEngineError("VALIDATION_ERROR", "submit_date 格式需为 YYYY-MM-DD")
        aligned_idx: int | None = None
        for idx, candle in enumerate(candles):
            candle_dt = self._parse_date(candle.time)
            if candle_dt is None:
                continue
            if candle_dt <= target:
                aligned_idx = idx
            else:
                break
        if aligned_idx is None:
            raise SimEngineError("SIM_PRICE_NOT_FOUND", "提交日期早于可用行情起始日")
        return aligned_idx

    def _next_trading_date(self, candles: list[CandlePoint], index: int) -> str | None:
        next_idx = index + 1
        if 0 <= next_idx < len(candles):
            return candles[next_idx].time
        return None

    def _calc_fees(self, side: OrderSide, amount: float, config: SimTradingConfig) -> tuple[float, float, float, float]:
        commission = max(amount * config.commission_rate, config.min_commission)
        stamp_tax = amount * config.stamp_tax_rate if side == "sell" else 0.0
        transfer_fee = amount * config.transfer_fee_rate
        total = commission + stamp_tax + transfer_fee
        return commission, stamp_tax, transfer_fee, total

    def _list_orders_raw(self) -> list[dict[str, object]]:
        orders = self._state.get("orders")
        if not isinstance(orders, list):
            raise SimEngineError("SIM_STATE_CORRUPTED", "订单状态损坏，请重置账户。")
        return orders  # type: ignore[return-value]

    def _list_fills_raw(self) -> list[dict[str, object]]:
        fills = self._state.get("fills")
        if not isinstance(fills, list):
            raise SimEngineError("SIM_STATE_CORRUPTED", "成交状态损坏，请重置账户。")
        return fills  # type: ignore[return-value]

    def _list_lots_raw(self) -> list[dict[str, object]]:
        lots = self._state.get("lots")
        if not isinstance(lots, list):
            raise SimEngineError("SIM_STATE_CORRUPTED", "持仓批次状态损坏，请重置账户。")
        return lots  # type: ignore[return-value]

    def _list_closed_raw(self) -> list[dict[str, object]]:
        rows = self._state.get("closed_trades")
        if not isinstance(rows, list):
            raise SimEngineError("SIM_STATE_CORRUPTED", "复盘数据损坏，请重置账户。")
        return rows  # type: ignore[return-value]

    def _current_available_quantity(self, symbol: str, on_date: str) -> int:
        lots = self._list_lots_raw()
        total = 0
        for lot in lots:
            if str(lot.get("symbol")) != symbol:
                continue
            remaining = int(lot.get("remaining_quantity", 0))
            if remaining <= 0:
                continue
            if self._lot_available(lot, on_date):
                total += remaining
        return total

    @staticmethod
    def _lot_available(lot: dict[str, object], on_date: str) -> bool:
        available = lot.get("available_date")
        return on_date > str(lot.get("buy_date", "")) and (not available or str(available) <= on_date)

    def _current_target_settle_date(self) -> str:
        pending_symbols = {
            str(order.get("symbol"))
            for order in self._list_orders_raw()
            if str(order.get("status")) == "pending"
        }
        pending_symbols.update(str(lot.get("symbol")) for lot in self._list_lots_raw() if int(lot.get("remaining_quantity", 0)) > 0)
        if not pending_symbols:
            return self._now_date()
        last_dates: list[str] = []
        for symbol in pending_symbols:
            candles = self._get_symbol_candles_or_raise(symbol)
            last_dates.append(candles[-1].time)
        return min(max(last_dates), self._now_date()) if last_dates else self._now_date()

    def _estimate_fill_plan(self, symbol: str, submit_date: str) -> tuple[str, str | None, float, str | None]:
        candles = self._get_symbol_candles_or_raise(symbol)
        submit_idx = self._align_index_to_submit_date(candles, submit_date)
        aligned_submit_date = candles[submit_idx].time
        fallback_warning: str | None = None
        expected_fill_date = self._next_trading_date(candles, submit_idx)
        if expected_fill_date:
            estimated_price = float(candles[submit_idx + 1].open)
        else:
            estimated_price = float(candles[submit_idx].close)
            fallback_warning = "WAITING_NEXT_TRADING_DAY"
        return aligned_submit_date, expected_fill_date, estimated_price, fallback_warning

    @staticmethod
    def _price_with_slippage(side: OrderSide, base_price: float, slippage_rate: float) -> float:
        if side == "buy":
            return base_price * (1 + slippage_rate)
        return base_price * (1 - slippage_rate)

    def _resolve_fill_price(
        self,
        symbol: str,
        side: OrderSide,
        fill_date: str,
        submit_date: str,
        fallback_warning: str | None,
        config: SimTradingConfig,
    ) -> tuple[float, str]:
        if fill_date <= submit_date:
            raise SimEngineError('SIM_PRICE_NOT_FOUND', '订单需要下一交易日的真实开盘价。')
        candles = self._get_symbol_candles_or_raise(symbol)
        fill_idx = self._align_index_to_submit_date(candles, fill_date)
        candle = candles[fill_idx]
        if candle.time != fill_date and fill_date > candle.time:
            raise SimEngineError("SIM_PRICE_NOT_FOUND", f"无法定位 {symbol} 在 {fill_date} 的成交价格")
        price_source = "vwap"
        base_price = float(candle.open)
        fill_price = self._price_with_slippage(side, base_price, config.slippage_rate)
        return round(fill_price, 4), price_source

    def _reject_order(self, order: dict[str, object], reason: str, *, status_reason: str | None = None) -> None:
        order["status"] = "rejected"
        order["reject_reason"] = reason
        order["status_reason"] = status_reason or reason
        order["filled_date"] = None
        order["cash_impact"] = 0.0

    def _fill_pending_order(self, order: dict[str, object]) -> tuple[bool, SimTradeFill | None]:
        symbol = str(order.get("symbol"))
        side = str(order.get("side"))
        quantity = int(order.get("quantity", 0))
        submit_date = str(order.get("submit_date"))
        fill_date = str(order.get("expected_fill_date") or submit_date)
        fallback_warning = str(order.get("status_reason") or "") or None

        config = self._config()
        account = self._account()
        cash = float(account.get("cash", 0.0))

        try:
            fill_price, price_source = self._resolve_fill_price(
                symbol=symbol,
                side=side if side in ("buy", "sell") else "buy",
                fill_date=fill_date,
                submit_date=submit_date,
                fallback_warning=fallback_warning,
                config=config,
            )
        except SimEngineError as exc:
            self._reject_order(order, exc.message, status_reason=exc.code)
            return False, None

        gross_amount = float(quantity) * fill_price
        commission, stamp_tax, transfer_fee, total_fee = self._calc_fees(
            side if side in ("buy", "sell") else "buy",
            gross_amount,
            config,
        )
        lots = self._list_lots_raw()
        closed_trades = self._list_closed_raw()
        fills = self._list_fills_raw()

        if side == "buy":
            total_cost = gross_amount + total_fee
            if cash + 1e-9 < total_cost:
                self._reject_order(order, "现金不足，无法成交。", status_reason="SIM_INSUFFICIENT_CASH")
                return False, None
            account["cash"] = round(cash - total_cost, 4)
            candles = self._get_symbol_candles_or_raise(symbol)
            fill_idx = self._align_index_to_submit_date(candles, fill_date)
            next_date = self._next_trading_date(candles, fill_idx)
            lot = {
                "lot_id": f"lot-{uuid4().hex[:12]}",
                "symbol": symbol,
                "buy_date": fill_date,
                "available_date": next_date,
                "quantity": quantity,
                "remaining_quantity": quantity,
                "buy_price": round(fill_price, 4),
                "unit_cost": round(total_cost / max(quantity, 1), 8),
                "fee_total": round(total_fee, 4),
            }
            lots.append(lot)
            net_amount = -total_cost
            cash_impact = -total_cost
        else:
            available = self._current_available_quantity(symbol, fill_date)
            if available < quantity:
                self._reject_order(order, "可卖数量不足，无法成交。", status_reason="SIM_INSUFFICIENT_POSITION")
                return False, None
            remaining = quantity
            matched: list[tuple[dict[str, object], int]] = []
            for lot in lots:
                if str(lot.get("symbol")) != symbol:
                    continue
                lot_remaining = int(lot.get("remaining_quantity", 0))
                if lot_remaining <= 0:
                    continue
                if not self._lot_available(lot, fill_date):
                    continue
                take = min(lot_remaining, remaining)
                if take <= 0:
                    continue
                matched.append((lot, take))
                remaining -= take
                if remaining <= 0:
                    break
            if remaining > 0:
                self._reject_order(order, "可卖数量不足，无法成交。", status_reason="SIM_INSUFFICIENT_POSITION")
                return False, None

            proceeds = gross_amount - total_fee
            account["cash"] = round(cash + proceeds, 4)
            net_amount = proceeds
            cash_impact = proceeds

            allocated_sell_fee = total_fee
            for lot, take in matched:
                lot_remaining = int(lot.get("remaining_quantity", 0))
                lot["remaining_quantity"] = max(0, lot_remaining - take)

                buy_cost = float(lot.get("unit_cost", 0.0)) * take
                sell_fee_part = allocated_sell_fee * (take / max(quantity, 1))
                sell_net = fill_price * take - sell_fee_part
                pnl_amount = sell_net - buy_cost
                pnl_ratio = pnl_amount / buy_cost if buy_cost > 0 else 0.0
                closed_trades.append(
                    {
                        "symbol": symbol,
                        "buy_date": str(lot.get("buy_date", fill_date)),
                        "buy_price": float(lot.get("buy_price", 0.0)),
                        "sell_date": fill_date,
                        "sell_price": round(fill_price, 4),
                        "quantity": take,
                        "holding_days": self._days_between(str(lot.get("buy_date", fill_date)), fill_date),
                        "pnl_amount": round(pnl_amount, 4),
                        "pnl_ratio": round(pnl_ratio, 6),
                    }
                )
            self._state["lots"] = [lot for lot in lots if int(lot.get("remaining_quantity", 0)) > 0]

        order["status"] = "filled"
        order["filled_date"] = fill_date
        order["cash_impact"] = round(cash_impact, 4)
        order["reject_reason"] = None
        order["status_reason"] = fallback_warning if fallback_warning == "NO_NEXT_DAY_FALLBACK_CLOSE" else None

        fill = SimTradeFill(
            order_id=str(order.get("order_id")),
            symbol=symbol,
            side=side if side in ("buy", "sell") else "buy",
            quantity=quantity,
            fill_date=fill_date,
            fill_price=round(fill_price, 4),
            price_source="approx" if price_source == "approx" else "vwap",
            gross_amount=round(gross_amount, 4),
            net_amount=round(net_amount, 4),
            fee_commission=round(commission, 4),
            fee_stamp_tax=round(stamp_tax, 4),
            fee_transfer=round(transfer_fee, 4),
            warning=fallback_warning,
        )
        fills.append(fill.model_dump())
        return True, fill

    def _settle_locked(self, target_date: str | None = None) -> SimSettleResponse:
        orders = self._list_orders_raw()
        account = self._account()
        if target_date is None:
            target_date = self._current_target_settle_date()
        settled_count = 0
        filled_count = 0
        changed = False

        for order in sorted(
            orders,
            key=lambda row: (str(row.get("submit_date", "")), str(row.get("order_id", ""))),
        ):
            if str(order.get("status")) != "pending":
                continue
            expected_fill_date = str(order.get("expected_fill_date") or "")
            if not expected_fill_date or expected_fill_date <= str(order.get("submit_date", "")):
                _, next_day, _, reason = self._estimate_fill_plan(str(order["symbol"]), str(order["submit_date"]))
                order["expected_fill_date"] = next_day
                order["status_reason"] = reason
                expected_fill_date = next_day or ""
            if not expected_fill_date:
                continue
            if expected_fill_date > target_date:
                continue
            settled_count += 1
            success, _ = self._fill_pending_order(order)
            changed = True
            if success:
                filled_count += 1

        as_of_date = str(account.get("as_of_date", self._now_date()))
        if target_date and target_date > as_of_date:
            account["as_of_date"] = target_date
            as_of_date = target_date
            changed = True

        audit = self._state.get("audit")
        if isinstance(audit, dict):
            audit["last_settle_at"] = self._now_datetime()
            changed = True

        if changed:
            self._persist()

        pending_after = sum(1 for order in orders if str(order.get("status")) == "pending")
        return SimSettleResponse(
            settled_count=settled_count,
            filled_count=filled_count,
            pending_count=pending_after,
            as_of_date=as_of_date,
            last_settle_at=str((audit or {}).get("last_settle_at", self._now_datetime())),
        )

    def _auto_settle_locked(self) -> SimSettleResponse:
        return self._settle_locked()

    def create_order(self, payload: CreateOrderRequest) -> CreateOrderResponse:
        with self._lock:
            self._auto_settle_locked()

            symbol = self._normalize_symbol(payload.symbol)
            if payload.quantity % 100 != 0:
                raise SimEngineError("SIM_INVALID_LOT_SIZE", "A股模拟交易仅支持100股整数倍。")
            if payload.quantity <= 0:
                raise SimEngineError("VALIDATION_ERROR", "quantity 必须大于0")

            aligned_submit_date, expected_fill_date, estimated_price, warning = self._estimate_fill_plan(
                symbol,
                payload.submit_date,
            )
            config = self._config()
            estimated_slippage_price = self._price_with_slippage(payload.side, estimated_price, config.slippage_rate)
            gross_amount = estimated_slippage_price * payload.quantity
            _, _, _, fee_total = self._calc_fees(payload.side, gross_amount, config)
            account = self._account()
            cash = float(account.get("cash", 0.0))

            if payload.side == "buy":
                est_cost = gross_amount + fee_total
                # 预留开盘波动缓冲(0.5%):提交时按估算价校验,实际成交可能高开,
                # 不留缓冲会导致次日 _fill_pending_order 静默拒单(rejected)。
                buffered_cost = gross_amount * 1.005 + fee_total
                reserved = 0.0
                for pending in self._list_orders_raw():
                    if pending.get("status") == "pending" and pending.get("side") == "buy":
                        amount = float(pending.get("estimated_price") or 0) * int(pending.get("quantity", 0))
                        reserved += amount * 1.005 + self._calc_fees("buy", amount, config)[3]
                if cash - reserved + 1e-9 < buffered_cost:
                    raise SimEngineError(
                        "SIM_INSUFFICIENT_CASH",
                        "现金不足（已预留开盘波动缓冲），无法提交买入订单。",
                    )
                cash_impact = -est_cost
            else:
                reserved = sum(int(item.get("quantity", 0)) for item in self._list_orders_raw() if item.get("status") == "pending" and item.get("side") == "sell" and item.get("symbol") == symbol)
                available = self._current_available_quantity(symbol, aligned_submit_date) - reserved
                if available < payload.quantity:
                    raise SimEngineError("SIM_INSUFFICIENT_POSITION", "可卖数量不足，无法提交卖出订单。")
                cash_impact = gross_amount - fee_total

            order_id = f"ord-{int(datetime.now().timestamp() * 1000)}-{uuid4().hex[:6]}"
            status_reason = warning
            if aligned_submit_date != payload.submit_date and not warning:
                status_reason = "SUBMIT_DATE_ALIGNED_TO_PREV_TRADING_DAY"

            order = SimTradeOrder(
                order_id=order_id,
                symbol=symbol,
                side=payload.side,
                quantity=payload.quantity,
                signal_date=payload.signal_date,
                submit_date=aligned_submit_date,
                status="pending",
                expected_fill_date=expected_fill_date,
                filled_date=None,
                estimated_price=round(estimated_slippage_price, 4),
                cash_impact=round(cash_impact, 4),
                status_reason=status_reason,
            )

            self._list_orders_raw().append(order.model_dump())
            self._persist()
            return CreateOrderResponse(order=order, fill=None)

    def list_orders(
        self,
        *,
        status: OrderStatus | None,
        symbol: str | None,
        side: OrderSide | None,
        date_from: str | None,
        date_to: str | None,
        page: int,
        page_size: int,
    ) -> SimOrdersResponse:
        with self._lock:
            rows = [SimTradeOrder(**item) for item in self._list_orders_raw()]
            if status:
                rows = [row for row in rows if row.status == status]
            if symbol:
                normalized = self._normalize_symbol(symbol)
                rows = [row for row in rows if row.symbol == normalized]
            if side:
                rows = [row for row in rows if row.side == side]
            if date_from:
                rows = [row for row in rows if row.submit_date >= date_from]
            if date_to:
                rows = [row for row in rows if row.submit_date <= date_to]
            rows.sort(key=lambda row: (row.submit_date, row.order_id), reverse=True)
            total = len(rows)
            start = max(0, (page - 1) * page_size)
            end = start + page_size
            return SimOrdersResponse(items=rows[start:end], total=total, page=page, page_size=page_size)

    def list_fills(
        self,
        *,
        symbol: str | None,
        side: OrderSide | None,
        date_from: str | None,
        date_to: str | None,
        page: int,
        page_size: int,
    ) -> SimFillsResponse:
        with self._lock:
            rows = [SimTradeFill(**item) for item in self._list_fills_raw()]
            if symbol:
                normalized = self._normalize_symbol(symbol)
                rows = [row for row in rows if row.symbol == normalized]
            if side:
                rows = [row for row in rows if row.side == side]
            if date_from:
                rows = [row for row in rows if row.fill_date >= date_from]
            if date_to:
                rows = [row for row in rows if row.fill_date <= date_to]
            rows.sort(key=lambda row: (row.fill_date, row.order_id), reverse=True)
            total = len(rows)
            start = max(0, (page - 1) * page_size)
            end = start + page_size
            return SimFillsResponse(items=rows[start:end], total=total, page=page, page_size=page_size)

    def cancel_order(self, order_id: str) -> CreateOrderResponse:
        with self._lock:
            target = next(
                (item for item in self._list_orders_raw() if str(item.get("order_id")) == order_id),
                None,
            )
            if target is None:
                raise SimEngineError("SIM_ORDER_NOT_FOUND", "订单不存在。")
            if str(target.get("status")) != "pending":
                raise SimEngineError("SIM_ORDER_NOT_CANCELABLE", "当前订单状态不可撤单。")
            target["status"] = "cancelled"
            target["status_reason"] = "USER_CANCELLED"
            self._persist()
            return CreateOrderResponse(order=SimTradeOrder(**target), fill=None)

    def settle(self) -> SimSettleResponse:
        with self._lock:
            return self._settle_locked()

    def reset(self) -> SimResetResponse:
        with self._lock:
            config = self._config()
            capital = float(config.initial_capital)
            now_date = self._now_date()
            now_ts = self._now_datetime()
            self._state = {
                "schema_version": self._SCHEMA_VERSION,
                "account": {
                    "initial_capital": capital,
                    "cash": capital,
                    "as_of_date": now_date,
                },
                "config": config.model_dump(),
                "orders": [],
                "fills": [],
                "lots": [],
                "closed_trades": [],
                "audit": {
                    "updated_at": now_ts,
                    "last_settle_at": now_ts,
                },
            }
            self._persist()
            return SimResetResponse(success=True, as_of_date=now_date, cash=capital)

    def get_config(self) -> SimTradingConfig:
        with self._lock:
            return self._config()

    def set_config(self, payload: SimTradingConfig) -> SimTradingConfig:
        with self._lock:
            account = self._account()
            has_history = bool(self._list_orders_raw() or self._list_lots_raw() or self._list_fills_raw())
            self._state["config"] = payload.model_dump()
            account["initial_capital"] = float(payload.initial_capital)
            if not has_history:
                account["cash"] = float(payload.initial_capital)
            self._persist()
            return payload

    def _resolve_price_on_date(self, symbol: str, on_date: str) -> float:
        candles = self._get_symbol_candles_or_raise(symbol)
        idx = self._align_index_to_submit_date(candles, on_date)
        return float(candles[idx].close)

    def _portfolio_locked(self) -> PortfolioSnapshot:
        self._auto_settle_locked()
        account = self._account()
        as_of_date = str(account.get("as_of_date", self._now_date()))
        lots = self._list_lots_raw()
        grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
        for lot in lots:
            remaining = int(lot.get("remaining_quantity", 0))
            if remaining <= 0:
                continue
            symbol = str(lot.get("symbol", ""))
            grouped[symbol].append(lot)

        positions: list[PortfolioPosition] = []
        unrealized_total = 0.0
        for symbol, symbol_lots in grouped.items():
            quantity = sum(int(item.get("remaining_quantity", 0)) for item in symbol_lots)
            available_quantity = sum(
                int(item.get("remaining_quantity", 0))
                for item in symbol_lots
                if self._lot_available(item, as_of_date)
            )
            cost_basis = sum(
                int(item.get("remaining_quantity", 0)) * float(item.get("unit_cost", 0.0))
                for item in symbol_lots
            )
            avg_cost = cost_basis / quantity if quantity > 0 else 0.0
            current_price = self._resolve_price_on_date(symbol, as_of_date)
            market_value = quantity * current_price
            pnl_amount = market_value - cost_basis
            pnl_ratio = pnl_amount / cost_basis if cost_basis > 0 else 0.0
            buy_dates = [str(item.get("buy_date", as_of_date)) for item in symbol_lots]
            holding_days = self._days_between(min(buy_dates), as_of_date) if buy_dates else 0
            unrealized_total += pnl_amount
            positions.append(
                PortfolioPosition(
                    symbol=symbol,
                    name=self._resolve_symbol_name(symbol),
                    quantity=quantity,
                    available_quantity=available_quantity,
                    avg_cost=round(avg_cost, 4),
                    current_price=round(current_price, 4),
                    market_value=round(market_value, 4),
                    pnl_amount=round(pnl_amount, 4),
                    pnl_ratio=round(pnl_ratio, 6),
                    holding_days=holding_days,
                )
            )
        positions.sort(key=lambda row: row.market_value, reverse=True)

        cash = float(account.get("cash", 0.0))
        position_value = sum(row.market_value for row in positions)
        total_asset = cash + position_value
        closed = [TradeRecord(**item) for item in self._list_closed_raw()]
        realized_pnl = sum(item.pnl_amount for item in closed)
        pending_order_count = sum(
            1 for item in self._list_orders_raw() if str(item.get("status")) == "pending"
        )
        return PortfolioSnapshot(
            as_of_date=as_of_date,
            total_asset=round(total_asset, 4),
            cash=round(cash, 4),
            position_value=round(position_value, 4),
            realized_pnl=round(realized_pnl, 4),
            unrealized_pnl=round(unrealized_total, 4),
            pending_order_count=pending_order_count,
            positions=positions,
        )

    def get_portfolio(self) -> PortfolioSnapshot:
        with self._lock:
            return self._portfolio_locked()

    def get_review(
        self,
        *,
        date_from: str | None,
        date_to: str | None,
        date_axis: Literal["sell", "buy"] = "sell",
    ) -> ReviewResponse:
        with self._lock:
            self._auto_settle_locked()
            account = self._account()
            initial_capital = float(account.get("initial_capital", 1_000_000.0))
            today = self._now_date()
            start = date_from or (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=90)).strftime("%Y-%m-%d")
            end = date_to or today
            if start > end:
                start, end = end, start

            axis = "buy" if date_axis == "buy" else "sell"

            def trade_axis_date(row: TradeRecord) -> str:
                return row.buy_date if axis == "buy" else row.sell_date

            all_trades = [TradeRecord(**item) for item in self._list_closed_raw()]
            all_trades.sort(key=lambda row: (trade_axis_date(row), row.symbol))

            filtered = [trade for trade in all_trades if start <= trade_axis_date(trade) <= end]
            trade_count = len(filtered)
            win_count = sum(1 for row in filtered if row.pnl_amount > 0)
            loss_count = sum(1 for row in filtered if row.pnl_amount < 0)
            gross_profit = sum(row.pnl_amount for row in filtered if row.pnl_amount > 0)
            gross_loss = sum(row.pnl_amount for row in filtered if row.pnl_amount < 0)
            avg_pnl_ratio = sum(row.pnl_ratio for row in filtered) / trade_count if trade_count else 0.0
            win_rate = win_count / trade_count if trade_count else 0.0
            total_pnl = sum(row.pnl_amount for row in filtered)
            total_return = total_pnl / initial_capital if initial_capital > 0 else 0.0
            if gross_loss < 0:
                profit_factor = gross_profit / abs(gross_loss)
            elif gross_profit > 0:
                profit_factor = math.inf
            else:
                profit_factor = 0.0

            cumulative_before_start = sum(row.pnl_amount for row in all_trades if trade_axis_date(row) < start)
            pnl_by_date: dict[str, float] = defaultdict(float)
            for row in filtered:
                pnl_by_date[trade_axis_date(row)] += row.pnl_amount

            equity_curve: list[EquityPoint] = []
            running_realized = cumulative_before_start
            equity_curve.append(
                EquityPoint(
                    date=start,
                    equity=round(initial_capital + running_realized, 4),
                    realized_pnl=round(running_realized, 4),
                )
            )
            for date_key in sorted(pnl_by_date.keys()):
                running_realized += pnl_by_date[date_key]
                equity_curve.append(
                    EquityPoint(
                        date=date_key,
                        equity=round(initial_capital + running_realized, 4),
                        realized_pnl=round(running_realized, 4),
                    )
                )
            if equity_curve[-1].date != end:
                equity_curve.append(
                    EquityPoint(
                        date=end,
                        equity=round(initial_capital + running_realized, 4),
                        realized_pnl=round(running_realized, 4),
                    )
                )

            drawdown_curve: list[DrawdownPoint] = []
            peak = -float("inf")
            max_drawdown = 0.0
            for point in equity_curve:
                peak = max(peak, point.equity)
                drawdown = (point.equity - peak) / peak if peak > 0 else 0.0
                max_drawdown = min(max_drawdown, drawdown)
                drawdown_curve.append(DrawdownPoint(date=point.date, drawdown=round(drawdown, 6)))

            month_agg: dict[str, dict[str, float]] = defaultdict(lambda: {"pnl": 0.0, "count": 0.0})
            for row in filtered:
                month = trade_axis_date(row)[:7]
                month_agg[month]["pnl"] += row.pnl_amount
                month_agg[month]["count"] += 1
            monthly_returns = [
                MonthlyReturnPoint(
                    month=month,
                    return_ratio=round(data["pnl"] / initial_capital, 6) if initial_capital > 0 else 0.0,
                    pnl_amount=round(data["pnl"], 4),
                    trade_count=int(data["count"]),
                )
                for month, data in sorted(month_agg.items())
            ]

            top_trades = sorted(filtered, key=lambda row: row.pnl_amount, reverse=True)[:10]
            bottom_trades = sorted(filtered, key=lambda row: row.pnl_amount)[:10]

            stats = ReviewStats(
                win_rate=round(win_rate, 6),
                total_return=round(total_return, 6),
                max_drawdown=round(abs(max_drawdown), 6),
                avg_pnl_ratio=round(avg_pnl_ratio, 6),
                trade_count=trade_count,
                win_count=win_count,
                loss_count=loss_count,
                profit_factor=round(profit_factor, 6) if math.isfinite(profit_factor) else 999.0,
            )
            return ReviewResponse(
                stats=stats,
                trades=filtered,
                equity_curve=equity_curve,
                drawdown_curve=drawdown_curve,
                monthly_returns=monthly_returns,
                top_trades=top_trades,
                bottom_trades=bottom_trades,
                cost_snapshot=self._config(),
                range=ReviewRange(date_from=start, date_to=end, date_axis=date_axis),
            )
