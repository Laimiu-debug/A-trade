from __future__ import annotations

import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

import numpy as np

from ..models import (
    BacktestResponse,
    BacktestOperationPlan,
    BacktestRunRequest,
    BacktestTrade,
    CandlePoint,
    DrawdownPoint,
    EquityPoint,
    MonthlyReturnPoint,
    ReviewRange,
    ReviewStats,
    SimTradingConfig,
)
from .backtest_matrix_engine import MatrixBundle
from .backtest_signal_matrix import BacktestSignalMatrix

ENTRY_EVENT_WEIGHTS: dict[str, float] = {
    "PS": 1.0,
    "SC": 1.2,
    "AR": 1.4,
    "ST": 1.6,
    "TSO": 2.5,
    "Spring": 3.0,
    "SOS": 3.4,
    "JOC": 4.0,
    "LPS": 2.8,
    "UTAD": 1.5,
    "SOW": 1.5,
    "LPSY": 1.3,
}

PHASE_PRIORITY_SCORE: dict[str, float] = {
    "鍚哥A": 1.1,
    "鍚哥B": 1.4,
    "鍚哥C": 2.2,
    "鍚哥D": 2.0,
    "鍚哥E": 0.2,
    "闃舵鏈槑": 0.0,
    "娲惧彂A": -0.8,
    "娲惧彂B": -1.2,
    "娲惧彂C": -2.0,
    "娲惧彂D": -2.2,
    "娲惧彂E": -2.5,
}


DELAY_INVALIDATION_RISK_EVENTS: tuple[str, ...] = ("UTAD", "SOW", "LPSY")
DELAY_SKIP_REASON_NO_ENTRY_DAY = "delay_no_entry_day"
DELAY_SKIP_REASON_RISK_EVENT = "delay_invalidated_by_risk_event"
DELAY_SKIP_REASON_SELL_SIGNAL = "delay_invalidated_by_sell_signal"
EVENT_GRADE_RANK: dict[str, int] = {"C": 1, "B": 2, "A": 3}
MATRIX_SEMANTIC_ALIGNED = "aligned_wyckoff_v2"
SCORE_ONLY_STRATEGY_ID = "score_only_rank_v1"
WULONG_STRATEGY_IDS: frozenset[str] = frozenset({"wulong_cluster_v1"})
TREND_KING_STRATEGY_IDS: frozenset[str] = frozenset({
    "trend_king_v1",
    "trend_king_limitup_v1",
    "trend_king_rally_v1",
    "trend_king_pullback_v1",
})
# 不依赖 Wyckoff 入场事件的策略 — 当天无事件时用伪事件 "SCORE" 兜底
EVENT_INDEPENDENT_STRATEGY_IDS: frozenset[str] = frozenset({
    "score_only_rank_v1",
    "matrix_signal_v1",
    "ths_main_force_flip_v1",
    "ths_main_force_golden_cross_v1",
    "ths_force_rhythm_v1",
    "relative_strength_breakout_v1",
    *WULONG_STRATEGY_IDS,
    "b1_mtf_v1",
    *TREND_KING_STRATEGY_IDS,
    "emotion_limit_up_v1",
    "limit_up_arb_v1",
})
SAME_DAY_CLOSE_ENTRY_STRATEGY_IDS: frozenset[str] = frozenset({"limit_up_arb_v1"})


@dataclass(frozen=True)
class ExitLeg:
    exit_index: int
    exit_price: float
    exit_reason: str
    sell_fraction: float
    exit_date: str = ""
    execution_phase: str = "intraday"


@dataclass
class CandidateTrade:
    symbol: str
    signal_date: str
    entry_date: str
    exit_date: str
    entry_signal: str
    entry_phase: str
    entry_quality_score: float
    candle_quality_score: float
    cost_center_shift_score: float
    weekly_context_score: float
    weekly_context_multiplier: float
    entry_phase_score: float
    entry_events_weight: float
    entry_structure_score: int
    entry_trend_score: float
    entry_volatility_score: float
    health_score: float
    event_score: float
    risk_score: float
    confirmation_status: str
    event_grade: str
    phase_context_score: float
    event_recency_score: float
    delay_entry_days: int
    delay_window_days: int
    final_rank_score: float
    entry_price: float
    exit_price: float
    holding_days: int
    exit_reason: str
    entry_index: int = 0
    exit_legs: list[ExitLeg] = field(default_factory=list)
    exit_execution_phase: str = "intraday"


@dataclass
class MatrixEntryIntent:
    symbol: str
    signal_index: int
    entry_index: int
    signal_date: str
    entry_date: str
    entry_signal: str
    entry_phase: str
    entry_quality_score: float
    candle_quality_score: float
    cost_center_shift_score: float
    weekly_context_score: float
    weekly_context_multiplier: float
    entry_phase_score: float
    entry_events_weight: float
    entry_structure_score: int
    entry_trend_score: float
    entry_volatility_score: float
    health_score: float
    event_score: float
    risk_score: float
    confirmation_status: str
    event_grade: str
    phase_context_score: float
    event_recency_score: float
    delay_entry_days: int
    delay_window_days: int
    final_rank_score: float
    entry_price: float


class BacktestEngine:
    def __init__(
        self,
        get_candles: Callable[[str], list[CandlePoint]],
        build_row: Callable[[str, str | None], Any | None],
        calc_snapshot: Callable[[Any, int, str | None], dict[str, Any]],
        resolve_symbol_name: Callable[[str], str],
        strategy_signal_filter: Callable[[str, Any, dict[str, Any], dict[str, Any]], bool] | None = None,
        strategy_signal_context_builder: Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any] | None] | None = None,
    ) -> None:
        self._get_candles = get_candles
        self._build_row = build_row
        self._calc_snapshot = calc_snapshot
        self._resolve_symbol_name = resolve_symbol_name
        self._strategy_signal_filter = strategy_signal_filter
        self._strategy_signal_context_builder = strategy_signal_context_builder

    @staticmethod
    def _parse_date(date_text: str) -> datetime | None:
        try:
            return datetime.strptime(date_text, "%Y-%m-%d")
        except Exception:
            return None

    @staticmethod
    def _normalize_event_dates(raw: Any) -> dict[str, str]:
        if not isinstance(raw, dict):
            return {}
        out: dict[str, str] = {}
        for event_code, event_date in raw.items():
            code_text = str(event_code).strip()
            date_text = str(event_date).strip()
            if not code_text or not date_text:
                continue
            out[code_text] = date_text
        return out

    @staticmethod
    def _normalize_event_count(snapshot: dict[str, Any]) -> int:
        event_chain = snapshot.get("event_chain")
        if isinstance(event_chain, list):
            valid = 0
            for row in event_chain:
                if isinstance(row, dict) and str(row.get("event", "")).strip():
                    valid += 1
            if valid > 0:
                return valid
        events = snapshot.get("events")
        risk_events = snapshot.get("risk_events")
        event_count = len(events) if isinstance(events, list) else 0
        event_count += len(risk_events) if isinstance(risk_events, list) else 0
        return event_count

    @staticmethod
    def _build_matrix_exit_signal_label(payload: BacktestRunRequest) -> str:
        configured = [str(item).strip() for item in payload.exit_events if str(item).strip()]
        for item in ("UTAD", "SOW", "LPSY"):
            if item in configured:
                return item
        return configured[0] if configured else "SELL"

    @staticmethod
    def _build_delay_skip_counter() -> dict[str, int]:
        return {
            DELAY_SKIP_REASON_NO_ENTRY_DAY: 0,
            DELAY_SKIP_REASON_RISK_EVENT: 0,
            DELAY_SKIP_REASON_SELL_SIGNAL: 0,
        }

    @staticmethod
    def _uses_same_day_close_entry(payload: BacktestRunRequest) -> bool:
        return str(payload.strategy_id).strip() in SAME_DAY_CLOSE_ENTRY_STRATEGY_IDS

    @staticmethod
    def _resolve_entry_index(signal_index: int, payload: BacktestRunRequest) -> int:
        if BacktestEngine._uses_same_day_close_entry(payload):
            return int(signal_index)
        return int(signal_index) + max(1, int(payload.entry_delay_days))

    @staticmethod
    def _resolve_entry_price_from_candle(candle: CandlePoint, payload: BacktestRunRequest) -> float:
        if BacktestEngine._uses_same_day_close_entry(payload):
            return float(candle.close)
        return float(candle.open)

    @staticmethod
    def _advance_trading_day(date_str: str, delay_days: int) -> str:
        dt = datetime.strptime(str(date_str), "%Y-%m-%d")
        remaining = max(1, int(delay_days))
        while remaining > 0:
            dt += timedelta(days=1)
            if dt.weekday() < 5:
                remaining -= 1
        return dt.strftime("%Y-%m-%d")

    @staticmethod
    def _resolve_planned_entry_date_from_dates(
        dates: list[str],
        signal_index: int,
        payload: BacktestRunRequest,
    ) -> str | None:
        entry_index = BacktestEngine._resolve_entry_index(signal_index, payload)
        if 0 <= entry_index < len(dates):
            return dates[entry_index]
        if signal_index < 0 or signal_index >= len(dates):
            return None
        return BacktestEngine._advance_trading_day(
            dates[signal_index],
            max(1, int(payload.entry_delay_days)),
        )

    @staticmethod
    def _resolve_planned_entry_date(
        candles: list[CandlePoint],
        signal_index: int,
        payload: BacktestRunRequest,
    ) -> str | None:
        entry_index = BacktestEngine._resolve_entry_index(signal_index, payload)
        if 0 <= entry_index < len(candles):
            return candles[entry_index].time
        if signal_index < 0 or signal_index >= len(candles):
            return None
        return BacktestEngine._advance_trading_day(
            candles[signal_index].time,
            max(1, int(payload.entry_delay_days)),
        )

    def _collect_last_day_open_signals_matrix(
        self,
        *,
        payload: BacktestRunRequest,
        symbols: list[str],
        start_date: str,
        end_date: str,
        matrix_bundle: MatrixBundle,
        matrix_signals: BacktestSignalMatrix,
        allowed_symbols_by_date: dict[str, set[str]] | None = None,
    ) -> list[BacktestTrade]:
        dates = list(matrix_bundle.dates)
        if not dates:
            return []
        total_shape = (len(dates), len(matrix_bundle.symbols))
        if (
            matrix_bundle.close.shape != total_shape
            or matrix_bundle.valid_mask.shape != total_shape
            or matrix_signals.buy_signal.shape != total_shape
            or matrix_signals.score.shape != total_shape
        ):
            return []
        entry_delay = max(1, int(payload.entry_delay_days))
        last_idx = -1
        for i in range(len(dates) - 1, -1, -1):
            if start_date <= dates[i] <= end_date:
                last_idx = i
                break
        if last_idx < 0:
            return []
        window_indices = [last_idx]
        symbol_to_col = matrix_bundle.symbol_to_index()
        out: list[BacktestTrade] = []
        seen_symbols: set[str] = set()
        for raw_symbol in symbols:
            symbol = str(raw_symbol).strip().lower()
            if symbol in seen_symbols:
                continue
            col = symbol_to_col.get(symbol)
            if col is None:
                continue
            best_trade: BacktestTrade | None = None
            best_score = -1.0
            # Scan from latest to earliest date in the window
            for row_idx in reversed(window_indices):
                sdate = dates[row_idx]
                if not bool(matrix_signals.buy_signal[row_idx, col]):
                    continue
                if not bool(matrix_bundle.valid_mask[row_idx, col]):
                    continue
                if allowed_symbols_by_date is not None:
                    if symbol not in allowed_symbols_by_date.get(sdate, set()):
                        continue
                close_price = float(matrix_bundle.close[row_idx, col])
                if not math.isfinite(close_price) or close_price <= 0:
                    continue
                score = float(matrix_signals.score[row_idx, col])
                if not math.isfinite(score):
                    score = 0.0
                score = max(0.0, min(100.0, score))
                if score < payload.min_score:
                    continue
                entry_tags: list[str] = []
                if bool(matrix_signals.s5[row_idx, col]):
                    entry_tags.append("SOS")
                if bool(matrix_signals.s6[row_idx, col]):
                    entry_tags.append("LPS")
                if not entry_tags:
                    entry_tags.append(payload.entry_events[0] if payload.entry_events else "ENTRY")
                entry_phase = "吸筹D" if bool(matrix_signals.s7[row_idx, col]) else "阶段不明"
                entry_signal_text = " / ".join(entry_tags)
                health_score = score
                event_score = score
                event_grade = "C"
                confirmation_status = "unconfirmed"
                if payload.matrix_event_semantic_version == MATRIX_SEMANTIC_ALIGNED:
                    semantic_meta = self._build_matrix_semantic_meta(
                        symbol=symbol,
                        signal_date=sdate,
                        payload=payload,
                    )
                    if semantic_meta is not None:
                        entry_signal_text = str(semantic_meta["entry_signal"])
                        entry_phase = str(semantic_meta["entry_phase"])
                        health_score = float(semantic_meta["health_score"])
                        event_score = float(semantic_meta["event_score"])
                        event_grade = self._normalize_event_grade(semantic_meta["event_grade"])
                        confirmation_status = self._normalize_confirmation_status(
                            semantic_meta["confirmation_status"]
                        )
                entry_idx = row_idx + entry_delay
                entry_dt = dates[entry_idx] if entry_idx < len(dates) else sdate
                if score > best_score:
                    best_score = score
                    best_trade = BacktestTrade(
                        symbol=symbol,
                        name=self._resolve_symbol_name(symbol),
                        signal_date=sdate,
                        entry_date=entry_dt,
                        exit_date=entry_dt,
                        entry_signal=entry_signal_text,
                        entry_phase=entry_phase,
                        entry_quality_score=round(score, 2),
                        candle_quality_score=round(score, 2),
                        cost_center_shift_score=round(score, 2),
                        weekly_context_score=round(score, 2),
                        weekly_context_multiplier=1.0,
                        health_score=round(max(0.0, min(100.0, health_score)), 2),
                        event_score=round(max(0.0, min(100.0, event_score)), 2),
                        risk_score=0.0,
                        confirmation_status=confirmation_status,
                        event_grade=event_grade,
                        phase_context_score=0.0,
                        event_recency_score=0.0,
                        exit_reason="open",
                        delay_entry_days=entry_delay,
                        delay_window_days=0,
                        quantity=0,
                        entry_price=round(close_price, 4),
                        exit_price=round(close_price, 4),
                        holding_days=0,
                        pnl_amount=0.0,
                        pnl_ratio=0.0,
                    )
                # Once we find a valid signal, no need to check earlier dates
                break
            if best_trade is not None:
                seen_symbols.add(symbol)
                out.append(best_trade)
        out.sort(key=lambda t: -t.entry_quality_score)
        return out

    def _collect_last_day_open_signals_legacy(
        self,
        *,
        payload: BacktestRunRequest,
        symbols: list[str],
        start_date: str,
        end_date: str,
        allowed_symbols_by_date: dict[str, set[str]] | None = None,
    ) -> list[BacktestTrade]:
        score_only_strategy = self._is_score_only_strategy(payload)
        entry_delay = max(1, int(payload.entry_delay_days))
        out: list[BacktestTrade] = []
        diag = {"total": 0, "can_enter": 0, "no_candles": 0, "no_date": 0, "not_allowed": 0, "no_row": 0, "no_signal": 0, "no_event": 0, "low_score": 0, "gated": 0}
        # Collect the last trading day within [start_date, end_date].
        # Candles may extend far beyond end_date (loaded to today), so we must
        # first locate the last candle within range, then scan backwards.
        signal_dates: set[str] = set()
        for raw_symbol in symbols:
            symbol = str(raw_symbol).strip().lower()
            candles = self._get_candles(symbol)
            if len(candles) < 30:
                continue
            last_in_range = -1
            for i in range(len(candles) - 1, -1, -1):
                if candles[i].time <= end_date:
                    last_in_range = i
                    break
            if last_in_range < 0:
                continue
            for i in range(last_in_range, max(last_in_range - 5, -1), -1):
                dt = candles[i].time
                if dt < start_date:
                    break
                signal_dates.add(dt)
        signal_date_list = sorted(signal_dates)
        # Keep only the most recent trading day
        if signal_date_list:
            signal_date_list = [signal_date_list[-1]]
        else:
            self._last_open_signal_diag = {**diag, "info": "no dates in range"}
            return out
        # For each symbol, find the most recent signal date that has a buy signal.
        # We prefer the latest date first (closest to end_date).
        seen_symbols: set[str] = set()
        for raw_symbol in symbols:
            diag["total"] += 1
            symbol = str(raw_symbol).strip().lower()
            if symbol in seen_symbols:
                continue
            candles = self._get_candles(symbol)
            if len(candles) < 30:
                diag["no_candles"] += 1
                continue
            candle_by_date: dict[str, int] = {}
            for ci in range(len(candles)):
                candle_by_date[candles[ci].time] = ci
            best_trade: BacktestTrade | None = None
            best_score = -1.0
            # Scan from latest to earliest signal date
            for sdate in reversed(signal_date_list):
                ci = candle_by_date.get(sdate)
                if ci is None:
                    continue
                # Check if this symbol is allowed on this signal date
                if allowed_symbols_by_date is not None:
                    if symbol not in allowed_symbols_by_date.get(sdate, set()):
                        continue
                row = self._build_row(symbol, sdate)
                if row is None:
                    continue
                snapshot = self._calc_snapshot(row, payload.window_days, sdate)
                if self._strategy_signal_filter is not None:
                    strategy_params = payload.strategy_params if isinstance(payload.strategy_params, dict) else {}
                    if not self._strategy_signal_filter(
                        str(payload.strategy_id).strip(), row, snapshot, strategy_params,
                    ):
                        continue
                event_dates = self._normalize_event_dates(snapshot.get("event_dates"))
                day_entry_events = [
                    event_name
                    for event_name in payload.entry_events
                    if event_dates.get(event_name) == sdate
                ]
                strategy_signal_context = self._resolve_strategy_signal_context(payload=payload, snapshot=snapshot)
                if not day_entry_events:
                    if score_only_strategy:
                        strategy_signal_name = ""
                        if strategy_signal_context is not None:
                            strategy_signal_name = str(strategy_signal_context.get("signal_name") or "").strip()
                        day_entry_events = [strategy_signal_name or self._resolve_event_independent_entry_label(payload)]
                    else:
                        continue
                event_count = self._normalize_event_count(snapshot)
                sequence_ok = bool(snapshot.get("sequence_ok"))
                entry_quality_score = float(snapshot.get("entry_quality_score", 0.0) or 0.0)
                if not score_only_strategy:
                    if event_count < payload.min_event_count:
                        continue
                    if payload.require_sequence and not sequence_ok:
                        continue
                if entry_quality_score < payload.min_score:
                    continue
                health_score = float(snapshot.get("health_score", entry_quality_score) or entry_quality_score)
                event_score = float(
                    snapshot.get("event_score", snapshot.get("event_strength_score", entry_quality_score)) or 0.0
                )
                event_grade = self._normalize_event_grade(snapshot.get("event_grade", "C"))
                confirmation_status = self._normalize_confirmation_status(snapshot.get("confirmation_status", "unconfirmed"))
                entry_phase = str(snapshot.get("phase", "阶段不明"))
                if strategy_signal_context is not None:
                    entry_quality_score = float(strategy_signal_context.get("entry_quality_score", entry_quality_score) or 0.0)
                    health_score = float(strategy_signal_context.get("health_score", health_score) or entry_quality_score)
                    event_score = float(strategy_signal_context.get("event_score", event_score) or entry_quality_score)
                    event_grade = self._normalize_event_grade(strategy_signal_context.get("event_grade", event_grade))
                    confirmation_status = self._normalize_confirmation_status(
                        strategy_signal_context.get("confirmation_status", confirmation_status)
                    )
                    entry_phase = str(strategy_signal_context.get("phase", entry_phase) or entry_phase)
                if not self._passes_semantic_score_gates(
                    payload=payload,
                    health_score=health_score,
                    event_score=event_score,
                    event_grade=event_grade,
                    confirmation_status=confirmation_status,
                ):
                    continue
                close_price = float(candles[ci].close)
                if not math.isfinite(close_price) or close_price <= 0:
                    continue
                # Compute next trading day as entry_date
                entry_ci = ci + entry_delay
                entry_dt = candles[entry_ci].time if entry_ci < len(candles) else sdate
                # Keep the best (highest score) signal for this symbol
                if entry_quality_score > best_score:
                    best_score = entry_quality_score
                    best_trade = BacktestTrade(
                        symbol=symbol,
                        name=self._resolve_symbol_name(symbol),
                        signal_date=sdate,
                        entry_date=entry_dt,
                        exit_date=entry_dt,
                        entry_signal=" / ".join(day_entry_events),
                        entry_phase=entry_phase,
                        entry_quality_score=round(max(0.0, min(100.0, entry_quality_score)), 2),
                        candle_quality_score=round(max(0.0, min(100.0, float(snapshot.get("candle_quality_score", 0.0) or 0.0))), 2),
                        cost_center_shift_score=round(max(0.0, min(100.0, float(snapshot.get("cost_center_shift_score", 0.0) or 0.0))), 2),
                        weekly_context_score=round(max(0.0, min(100.0, float(snapshot.get("weekly_context_score", 50.0) or 50.0))), 2),
                        weekly_context_multiplier=round(max(0.85, min(1.15, float(snapshot.get("weekly_context_multiplier", 1.0) or 1.0))), 4),
                        health_score=round(max(0.0, min(100.0, health_score)), 2),
                        event_score=round(max(0.0, min(100.0, event_score)), 2),
                        risk_score=round(max(0.0, min(100.0, float(snapshot.get("risk_score", 0.0) or 0.0))), 2),
                        confirmation_status=confirmation_status,
                        event_grade=event_grade,
                        phase_context_score=round(max(0.0, min(100.0, float(snapshot.get("phase_context_score", 0.0) or 0.0))), 2),
                        event_recency_score=round(max(0.0, min(100.0, float(snapshot.get("event_recency_score", 0.0) or 0.0))), 2),
                        exit_reason="open",
                        delay_entry_days=entry_delay,
                        delay_window_days=0,
                        quantity=0,
                        entry_price=round(close_price, 4),
                        exit_price=round(close_price, 4),
                        holding_days=0,
                        pnl_amount=0.0,
                        pnl_ratio=0.0,
                    )
                # Once we find a valid signal, no need to check earlier dates for this symbol
                break
            if best_trade is not None:
                seen_symbols.add(symbol)
                out.append(best_trade)
        out.sort(key=lambda t: -t.entry_quality_score)
        self._last_open_signal_diag = diag
        return out

    @staticmethod
    def _is_score_only_strategy(payload: BacktestRunRequest) -> bool:
        return str(payload.strategy_id).strip().lower() in EVENT_INDEPENDENT_STRATEGY_IDS

    @staticmethod
    def _resolve_event_independent_entry_label(payload: BacktestRunRequest) -> str:
        strategy_id = str(payload.strategy_id).strip().lower()
        if strategy_id == "ths_main_force_flip_v1":
            return "主力紫转黄"
        if strategy_id == "ths_main_force_golden_cross_v1":
            return "主力金叉"
        if strategy_id == "ths_force_rhythm_v1":
            return "节奏波谷"
        if strategy_id == "emotion_limit_up_v1":
            return "情绪涨停"
        if strategy_id == "limit_up_arb_v1":
            return "涨停套利"
        if strategy_id in WULONG_STRATEGY_IDS:
            return "五龙聚首"
        if strategy_id == "matrix_signal_v1":
            return "MATRIX"
        if strategy_id == "b1_mtf_v1":
            return "B1_MTF"
        if strategy_id == "relative_strength_breakout_v1":
            return "相对强弱突破"
        if strategy_id in TREND_KING_STRATEGY_IDS:
            if strategy_id == "trend_king_limitup_v1":
                return "涨停精选"
            if strategy_id == "trend_king_rally_v1":
                return "涨势确认"
            if strategy_id == "trend_king_pullback_v1":
                return "涨停跟踪"
            return "趋势为王"
        return "SCORE"

    def _resolve_strategy_signal_context(
        self,
        *,
        payload: BacktestRunRequest,
        snapshot: dict[str, Any],
    ) -> dict[str, Any] | None:
        if self._strategy_signal_context_builder is None:
            return None
        strategy_params = payload.strategy_params if isinstance(payload.strategy_params, dict) else {}
        return self._strategy_signal_context_builder(
            str(payload.strategy_id).strip(),
            snapshot,
            strategy_params,
        )

    @staticmethod
    def _normalize_event_grade(raw: Any) -> str:
        text = str(raw or "C").strip().upper()
        return text if text in EVENT_GRADE_RANK else "C"

    @staticmethod
    def _normalize_confirmation_status(raw: Any) -> str:
        text = str(raw or "").strip().lower()
        if text in {"confirmed", "partial", "unconfirmed", "risk_blocked"}:
            return text
        return "unconfirmed"

    @staticmethod
    def _event_grade_meets_threshold(*, grade: str, minimum: str) -> bool:
        return EVENT_GRADE_RANK.get(BacktestEngine._normalize_event_grade(grade), 1) >= EVENT_GRADE_RANK.get(
            BacktestEngine._normalize_event_grade(minimum),
            1,
        )

    @staticmethod
    def _resolve_rank_weights(payload: BacktestRunRequest) -> tuple[float, float]:
        w_health = max(0.0, float(payload.rank_weight_health))
        w_event = max(0.0, float(payload.rank_weight_event))
        total = w_health + w_event
        if total <= 0:
            return 0.45, 0.55
        return w_health / total, w_event / total

    @staticmethod
    def _compute_final_rank_score(
        *,
        payload: BacktestRunRequest,
        health_score: float,
        event_score: float,
    ) -> float:
        w_health, w_event = BacktestEngine._resolve_rank_weights(payload)
        score = float(health_score) * w_health + float(event_score) * w_event
        return max(0.0, min(100.0, score))

    @staticmethod
    def _passes_semantic_score_gates(
        *,
        payload: BacktestRunRequest,
        health_score: float,
        event_score: float,
        event_grade: str,
        confirmation_status: str = "unconfirmed",
    ) -> bool:
        if float(health_score) < float(payload.health_score_min):
            return False
        if float(event_score) < float(payload.event_score_min):
            return False
        if not BacktestEngine._event_grade_meets_threshold(
            grade=event_grade,
            minimum=payload.event_grade_min,
        ):
            return False
        if bool(payload.require_key_event_confirmation):
            normalized_status = BacktestEngine._normalize_confirmation_status(confirmation_status)
            if normalized_status != "confirmed":
                return False
        return True

    def _build_matrix_semantic_meta(
        self,
        *,
        symbol: str,
        signal_date: str,
        payload: BacktestRunRequest,
    ) -> dict[str, Any] | None:
        row = self._build_row(symbol, signal_date)
        if row is None:
            return None
        snapshot = self._calc_snapshot(row, payload.window_days, signal_date)
        event_dates = self._normalize_event_dates(snapshot.get("event_dates"))
        day_entry_events = [
            event_name
            for event_name in payload.entry_events
            if event_dates.get(event_name) == signal_date
        ]
        if not day_entry_events:
            return None
        event_count = self._normalize_event_count(snapshot)
        sequence_ok = bool(snapshot.get("sequence_ok"))
        entry_quality_score = float(snapshot.get("entry_quality_score", 0.0) or 0.0)
        candle_quality_score = float(snapshot.get("candle_quality_score", 0.0) or 0.0)
        cost_center_shift_score = float(snapshot.get("cost_center_shift_score", 0.0) or 0.0)
        weekly_context_score = float(snapshot.get("weekly_context_score", 50.0) or 50.0)
        weekly_context_multiplier = float(snapshot.get("weekly_context_multiplier", 1.0) or 1.0)
        if event_count < payload.min_event_count:
            return None
        if payload.require_sequence and not sequence_ok:
            return None
        if entry_quality_score < payload.min_score:
            return None

        entry_phase = str(snapshot.get("phase", "闂冭埖顔岄張顏呮"))
        structure_hhh = str(snapshot.get("structure_hhh", "-"))
        health_score = float(snapshot.get("health_score", entry_quality_score) or entry_quality_score)
        event_score = float(
            snapshot.get(
                "event_score",
                snapshot.get("event_strength_score", entry_quality_score),
            )
            or 0.0
        )
        risk_score = float(snapshot.get("risk_score", 0.0) or 0.0)
        confirmation_status = self._normalize_confirmation_status(snapshot.get("confirmation_status", "unconfirmed"))
        phase_context_score = float(snapshot.get("phase_context_score", 0.0) or 0.0)
        event_recency_score = float(snapshot.get("event_recency_score", 0.0) or 0.0)
        event_grade = self._normalize_event_grade(snapshot.get("event_grade", "C"))
        if not self._passes_semantic_score_gates(
            payload=payload,
            health_score=health_score,
            event_score=event_score,
            event_grade=event_grade,
            confirmation_status=confirmation_status,
        ):
            return None
        return {
            "entry_signal": " / ".join(day_entry_events),
            "entry_phase": entry_phase,
            "entry_quality_score": float(entry_quality_score),
            "candle_quality_score": max(0.0, min(100.0, candle_quality_score)),
            "cost_center_shift_score": max(0.0, min(100.0, cost_center_shift_score)),
            "weekly_context_score": max(0.0, min(100.0, weekly_context_score)),
            "weekly_context_multiplier": max(0.85, min(1.15, weekly_context_multiplier)),
            "entry_phase_score": float(PHASE_PRIORITY_SCORE.get(entry_phase, 0.0)),
            "entry_events_weight": float(sum(ENTRY_EVENT_WEIGHTS.get(evt, 1.0) for evt in day_entry_events)),
            "entry_structure_score": self._structure_score(structure_hhh),
            "entry_trend_score": float(snapshot.get("trend_score", 50.0) or 50.0),
            "entry_volatility_score": float(snapshot.get("volatility_score", 50.0) or 50.0),
            "health_score": max(0.0, min(100.0, health_score)),
            "event_score": max(0.0, min(100.0, event_score)),
            "risk_score": max(0.0, min(100.0, risk_score)),
            "confirmation_status": confirmation_status,
            "event_grade": event_grade,
            "phase_context_score": max(0.0, min(100.0, phase_context_score)),
            "event_recency_score": max(0.0, min(100.0, event_recency_score)),
        }

    @staticmethod
    def _resolve_delay_invalidation_reason_legacy(
        *,
        signal_index: int,
        entry_index: int,
        risk_events_by_index: dict[int, list[str]],
        exit_signal_events_by_index: dict[int, list[str]],
    ) -> str | None:
        if entry_index - signal_index <= 1:
            return None
        for probe_index in range(signal_index + 1, entry_index):
            if risk_events_by_index.get(probe_index):
                return DELAY_SKIP_REASON_RISK_EVENT
            if exit_signal_events_by_index.get(probe_index):
                return DELAY_SKIP_REASON_SELL_SIGNAL
        return None

    @staticmethod
    def _resolve_delay_invalidation_reason_matrix(
        *,
        signal_index: int,
        entry_index: int,
        valid_col: np.ndarray,
        sell_col: np.ndarray,
    ) -> str | None:
        if entry_index - signal_index <= 1:
            return None
        for probe_index in range(signal_index + 1, entry_index):
            if probe_index >= int(valid_col.shape[0]):
                break
            if not bool(valid_col[probe_index]):
                continue
            if bool(sell_col[probe_index]):
                return DELAY_SKIP_REASON_SELL_SIGNAL
        return None

    def _resolve_delay_invalidation_reason_matrix_aligned(
        self,
        *,
        symbol: str,
        signal_index: int,
        entry_index: int,
        dates: list[str],
        payload: BacktestRunRequest,
    ) -> str | None:
        if entry_index - signal_index <= 1:
            return None
        for probe_index in range(signal_index + 1, entry_index):
            if probe_index >= len(dates):
                break
            probe_date = str(dates[probe_index]).strip()
            if not probe_date:
                continue
            row = self._build_row(symbol, probe_date)
            if row is None:
                continue
            snapshot = self._calc_snapshot(row, payload.window_days, probe_date)
            event_dates = self._normalize_event_dates(snapshot.get("event_dates"))

            raw_risk_events = snapshot.get("risk_events")
            if isinstance(raw_risk_events, list):
                for raw_event in raw_risk_events:
                    event_text = str(raw_event).strip()
                    if not event_text or event_text not in DELAY_INVALIDATION_RISK_EVENTS:
                        continue
                    event_day = str(event_dates.get(event_text, "")).strip()
                    if event_day == probe_date or (not event_day and event_text in event_dates):
                        return DELAY_SKIP_REASON_RISK_EVENT

            for risk_event in DELAY_INVALIDATION_RISK_EVENTS:
                if str(event_dates.get(risk_event, "")).strip() == probe_date:
                    return DELAY_SKIP_REASON_RISK_EVENT

            for exit_event in payload.exit_events:
                exit_name = str(exit_event).strip()
                if not exit_name:
                    continue
                if str(event_dates.get(exit_name, "")).strip() == probe_date:
                    return DELAY_SKIP_REASON_SELL_SIGNAL
        return None

    @staticmethod
    def _structure_score(structure_hhh: str) -> int:
        parts = [part.strip() for part in str(structure_hhh).split("|")]
        return sum(1 for part in parts if part and part != "-")

    @staticmethod
    def _candidate_sort_key(
        row: CandidateTrade,
        *,
        priority_mode: str,
    ) -> tuple[Any, ...]:
        if priority_mode == "phase_first":
            return (
                row.entry_date,
                -row.entry_phase_score,
                -row.final_rank_score,
                -row.entry_quality_score,
                -row.entry_events_weight,
                -row.entry_structure_score,
                row.symbol,
                row.exit_date,
            )
        if priority_mode == "momentum":
            return (
                row.entry_date,
                -row.entry_trend_score,
                -row.final_rank_score,
                -row.entry_quality_score,
                -row.entry_events_weight,
                -row.entry_structure_score,
                row.symbol,
                row.exit_date,
            )
        return (
            row.entry_date,
            -row.final_rank_score,
            -row.entry_quality_score,
            -row.entry_phase_score,
            -row.entry_events_weight,
            -row.entry_structure_score,
            row.symbol,
            row.exit_date,
        )

    def _build_plan_candidate_from_meta(
        self,
        *,
        symbol: str,
        signal_date: str,
        entry_date: str,
        reference_price: float,
        meta: dict[str, Any],
        payload: BacktestRunRequest,
    ) -> CandidateTrade | None:
        price = float(reference_price)
        if not math.isfinite(price) or price <= 0:
            return None
        final_rank_score = meta.get("final_rank_score")
        if final_rank_score is None:
            final_rank_score = self._compute_final_rank_score(
                payload=payload,
                health_score=float(meta.get("health_score", meta.get("entry_quality_score", 0.0)) or 0.0),
                event_score=float(meta.get("event_score", meta.get("entry_quality_score", 0.0)) or 0.0),
            )
        return CandidateTrade(
            symbol=symbol,
            signal_date=signal_date,
            entry_date=entry_date or signal_date,
            exit_date=entry_date or signal_date,
            entry_signal=str(meta.get("entry_signal", "")),
            entry_phase=str(meta.get("entry_phase", "阶段未明")),
            entry_quality_score=float(meta.get("entry_quality_score", 0.0) or 0.0),
            candle_quality_score=float(meta.get("candle_quality_score", 0.0) or 0.0),
            cost_center_shift_score=float(meta.get("cost_center_shift_score", 0.0) or 0.0),
            weekly_context_score=float(meta.get("weekly_context_score", 50.0) or 50.0),
            weekly_context_multiplier=float(meta.get("weekly_context_multiplier", 1.0) or 1.0),
            entry_phase_score=float(meta.get("entry_phase_score", 0.0) or 0.0),
            entry_events_weight=float(meta.get("entry_events_weight", 0.0) or 0.0),
            entry_structure_score=int(meta.get("entry_structure_score", 0) or 0),
            entry_trend_score=float(meta.get("entry_trend_score", 0.0) or 0.0),
            entry_volatility_score=float(meta.get("entry_volatility_score", 0.0) or 0.0),
            health_score=float(meta.get("health_score", 0.0) or 0.0),
            event_score=float(meta.get("event_score", 0.0) or 0.0),
            risk_score=float(meta.get("risk_score", 0.0) or 0.0),
            confirmation_status=self._normalize_confirmation_status(meta.get("confirmation_status", "unconfirmed")),
            event_grade=self._normalize_event_grade(meta.get("event_grade", "C")),
            phase_context_score=float(meta.get("phase_context_score", 0.0) or 0.0),
            event_recency_score=float(meta.get("event_recency_score", 0.0) or 0.0),
            delay_entry_days=max(1, int(payload.entry_delay_days)),
            delay_window_days=max(0, int(payload.entry_delay_days) - 1),
            final_rank_score=float(final_rank_score or 0.0),
            entry_price=price,
            exit_price=price,
            holding_days=0,
            exit_reason="open",
        )

    def _candidate_to_plan_trade(self, row: CandidateTrade) -> BacktestTrade:
        return BacktestTrade(
            symbol=row.symbol,
            name=self._resolve_symbol_name(row.symbol),
            signal_date=row.signal_date,
            entry_date=row.entry_date,
            exit_date=row.exit_date,
            entry_signal=row.entry_signal,
            entry_phase=row.entry_phase,
            entry_quality_score=round(row.entry_quality_score, 2),
            candle_quality_score=round(row.candle_quality_score, 2),
            cost_center_shift_score=round(row.cost_center_shift_score, 2),
            weekly_context_score=round(row.weekly_context_score, 2),
            weekly_context_multiplier=round(row.weekly_context_multiplier, 4),
            health_score=round(row.health_score, 2),
            event_score=round(row.event_score, 2),
            risk_score=round(row.risk_score, 2),
            confirmation_status=self._normalize_confirmation_status(row.confirmation_status),
            event_grade=self._normalize_event_grade(row.event_grade),  # type: ignore[arg-type]
            phase_context_score=round(row.phase_context_score, 2),
            event_recency_score=round(row.event_recency_score, 2),
            exit_reason="open",
            delay_entry_days=max(1, int(row.delay_entry_days)),
            delay_window_days=max(0, int(row.delay_window_days)),
            quantity=0,
            entry_price=round(row.entry_price, 4),
            exit_price=round(row.exit_price, 4),
            holding_days=0,
            pnl_amount=0.0,
            pnl_ratio=0.0,
        )

    def _finalize_plan_signals(
        self,
        plan_candidates: list[CandidateTrade],
        *,
        payload: BacktestRunRequest,
    ) -> list[BacktestTrade]:
        if not plan_candidates:
            return []
        if payload.prioritize_signals:
            plan_candidates.sort(key=lambda row: self._candidate_sort_key(row, priority_mode=payload.priority_mode))
        else:
            plan_candidates.sort(key=lambda row: (row.signal_date, row.symbol, row.entry_date))

        if payload.prioritize_signals and payload.priority_topk_per_day > 0:
            kept: list[CandidateTrade] = []
            day_counter: dict[str, int] = defaultdict(int)
            for row in plan_candidates:
                if day_counter[row.signal_date] >= payload.priority_topk_per_day:
                    continue
                kept.append(row)
                day_counter[row.signal_date] += 1
            plan_candidates = kept

        return [self._candidate_to_plan_trade(row) for row in plan_candidates]

    @staticmethod
    def _is_backtest_open_holding(exit_reason: str) -> bool:
        return str(exit_reason or "").strip().lower() == "open"

    def _resolve_mark_price_for_symbol(
        self,
        symbol: str,
        mark_date: str,
        *,
        close_map_by_symbol: dict[str, dict[str, float]] | None = None,
        fallback_price: float | None = None,
    ) -> float | None:
        day_close = (close_map_by_symbol or {}).get(symbol, {})
        if day_close:
            direct = day_close.get(mark_date)
            if direct is not None and math.isfinite(direct) and direct > 0:
                return float(direct)
            prior = [
                (day, price)
                for day, price in day_close.items()
                if day <= mark_date and math.isfinite(price) and price > 0
            ]
            if prior:
                prior.sort(key=lambda item: item[0])
                return float(prior[-1][1])

        last_price: float | None = None
        for bar in self._get_candles(symbol):
            if bar.time > mark_date:
                break
            close_price = float(bar.close)
            if math.isfinite(close_price) and close_price > 0:
                last_price = close_price
        if last_price is not None:
            return last_price
        if fallback_price is not None and math.isfinite(fallback_price) and fallback_price > 0:
            return float(fallback_price)
        return None

    def _apply_end_date_mark_to_market(
        self,
        trades: list[BacktestTrade],
        *,
        end_date: str,
        fee_rate: float,
        close_map_by_symbol: dict[str, dict[str, float]] | None = None,
    ) -> list[BacktestTrade]:
        if not trades:
            return trades
        out: list[BacktestTrade] = []
        for trade in trades:
            if not self._is_backtest_open_holding(trade.exit_reason):
                out.append(trade)
                continue
            mark_price = self._resolve_mark_price_for_symbol(
                trade.symbol,
                end_date,
                close_map_by_symbol=close_map_by_symbol,
                fallback_price=float(trade.exit_price or trade.entry_price),
            )
            if mark_price is None or mark_price <= 0:
                out.append(trade)
                continue
            entry_exec = float(trade.entry_price) * (1 + fee_rate)
            exit_exec = mark_price * (1 - fee_rate)
            invested = float(trade.quantity) * entry_exec
            exit_amount = float(trade.quantity) * exit_exec
            pnl_amount = exit_amount - invested if invested > 0 else 0.0
            pnl_ratio = pnl_amount / invested if invested > 0 else 0.0
            out.append(
                trade.model_copy(
                    update={
                        "exit_date": end_date,
                        "exit_price": round(mark_price, 4),
                        "pnl_amount": round(pnl_amount, 4),
                        "pnl_ratio": round(pnl_ratio, 6),
                    }
                )
            )
        return out

    @staticmethod
    def _is_trailing_plan_sell_reason(reason: str) -> bool:
        text = str(reason or "").strip()
        return text in {"trailing_stop", "trailing_stop_intraday", "trailing_stop_daily"} or text.startswith(
            "trailing_stop_intraday:"
        )

    @staticmethod
    def _is_next_day_plan_sell(trade: BacktestTrade) -> bool:
        reason = str(trade.exit_reason or "").strip()
        return BacktestEngine._is_trailing_plan_sell_reason(reason) or reason.startswith("event_exit")

    @staticmethod
    def _resolve_trailing_layer_flags(payload: BacktestRunRequest) -> tuple[float, bool, bool, float, int]:
        trailing_pct = float(getattr(payload, "trailing_stop_pct", 0.0) or 0.0)
        if trailing_pct <= 0:
            return 0.0, False, False, 1.0, 2
        intraday_enabled = bool(getattr(payload, "intraday_trailing_enabled", True))
        daily_enabled = bool(getattr(payload, "daily_trailing_clear_enabled", True))
        if not intraday_enabled and not daily_enabled:
            daily_enabled = True
        reduce_ratio = float(getattr(payload, "intraday_trailing_reduce_ratio", 0.5) or 0.5)
        reduce_ratio = max(0.1, min(1.0, reduce_ratio))
        confirm_days = int(getattr(payload, "daily_trailing_confirm_days", 2) or 2)
        confirm_days = max(1, min(5, confirm_days))
        return trailing_pct, intraday_enabled, daily_enabled, reduce_ratio, confirm_days

    @staticmethod
    def _format_intraday_trailing_reason(reduce_ratio: float) -> str:
        pct = int(round(max(0.1, min(1.0, reduce_ratio)) * 100))
        return f"trailing_stop_intraday:reduce={pct}"

    @staticmethod
    def _resolve_trailing_pending_reason(
        *,
        pending_reason: str,
        trailing_pct: float,
        intraday_enabled: bool,
        daily_enabled: bool,
        reduce_ratio: float,
    ) -> str:
        if pending_reason == "trailing_stop_daily":
            return pending_reason
        if pending_reason == "trailing_stop_intraday" or pending_reason.startswith("trailing_stop_intraday:"):
            return BacktestEngine._format_intraday_trailing_reason(reduce_ratio)
        if pending_reason == "trailing_stop":
            return pending_reason
        if intraday_enabled and not daily_enabled:
            return BacktestEngine._format_intraday_trailing_reason(reduce_ratio)
        if daily_enabled:
            return "trailing_stop_daily"
        return "trailing_stop"

    @staticmethod
    def _is_intraday_reduce_only_exit(exit_reason: str) -> bool:
        text = str(exit_reason or "").strip()
        return text.startswith("trailing_stop_intraday:reduce=")

    @staticmethod
    def _parse_intraday_reduce_ratio(exit_reason: str, *, default: float = 0.5) -> float:
        text = str(exit_reason or "").strip()
        match = re.search(r"reduce=(\d+)", text)
        if not match:
            return max(0.1, min(1.0, float(default)))
        return max(0.1, min(1.0, int(match.group(1)) / 100.0))

    @staticmethod
    def _compute_reduce_sell_shares(remaining_shares: int, reduce_ratio: float) -> int:
        if remaining_shares <= 0:
            return 0
        if remaining_shares < 100:
            return remaining_shares
        target = int(math.floor(remaining_shares * max(0.1, min(1.0, reduce_ratio)) / 100.0)) * 100
        if target <= 0:
            target = 100
        if remaining_shares - target < 100:
            return remaining_shares
        return min(target, remaining_shares)

    @staticmethod
    def _resolve_last_day_held_trades(
        trades: list[BacktestTrade],
        *,
        as_of_date: str,
    ) -> list[BacktestTrade]:
        return [
            trade
            for trade in trades
            if (
                (
                    BacktestEngine._is_backtest_open_holding(trade.exit_reason)
                    and trade.entry_date <= as_of_date
                )
                or (trade.entry_date <= as_of_date < trade.exit_date)
            )
        ]

    @staticmethod
    def _group_held_trades_by_symbol(
        held_trades: list[BacktestTrade],
    ) -> dict[str, list[BacktestTrade]]:
        grouped: dict[str, list[BacktestTrade]] = defaultdict(list)
        for trade in held_trades:
            symbol = str(trade.symbol or "").strip().lower()
            if symbol:
                grouped[symbol].append(trade)
        return grouped

    @classmethod
    def _count_slot_freeing_plan_sell_symbols(
        cls,
        *,
        held_trades: list[BacktestTrade],
        sell_signals: list[BacktestTrade],
    ) -> int:
        if not sell_signals:
            return 0
        held_by_symbol = cls._group_held_trades_by_symbol(held_trades)
        sell_symbols = {str(trade.symbol or "").strip().lower() for trade in sell_signals}
        freeing = 0
        for symbol in sell_symbols:
            if not symbol:
                continue
            legs = held_by_symbol.get(symbol, [])
            if not legs:
                continue
            if any(cls._is_intraday_reduce_only_exit(str(leg.exit_reason or "")) for leg in legs):
                continue
            if all(cls._is_next_day_plan_sell(leg) for leg in legs):
                freeing += 1
        return freeing

    @classmethod
    def _compute_available_buy_slots(
        cls,
        *,
        held_trades: list[BacktestTrade],
        sell_signals: list[BacktestTrade],
        max_positions: int,
    ) -> int:
        max_positions = max(0, int(max_positions))
        occupied_slots = len(cls._group_held_trades_by_symbol(held_trades))
        freeing_slots = cls._count_slot_freeing_plan_sell_symbols(
            held_trades=held_trades,
            sell_signals=sell_signals,
        )
        return max(0, max_positions - occupied_slots + freeing_slots)

    @staticmethod
    def _active_position_symbols(
        active_positions: list[dict[str, float | str | list[dict[str, float | str]]]],
    ) -> set[str]:
        symbols: set[str] = set()
        for item in active_positions:
            symbol = str(item.get("symbol", "")).strip().lower()
            if symbol:
                symbols.add(symbol)
        return symbols

    @classmethod
    def _count_active_position_slots(
        cls,
        active_positions: list[dict[str, float | str | list[dict[str, float | str]]]],
    ) -> int:
        return len(cls._active_position_symbols(active_positions))

    @staticmethod
    def _symbols_with_pending_intraday_reduce(sell_signals: list[BacktestTrade]) -> set[str]:
        return {
            str(trade.symbol or "").strip().lower()
            for trade in sell_signals
            if BacktestEngine._is_intraday_reduce_only_exit(str(trade.exit_reason or ""))
        }

    @staticmethod
    def _filter_plan_buy_signals(
        candidates: list[BacktestTrade],
        *,
        held_symbols: set[str],
        reduce_blocked_symbols: set[str],
        max_slots: int,
    ) -> list[BacktestTrade]:
        filtered = [
            row
            for row in candidates
            if str(row.symbol).strip().lower() not in held_symbols
            and str(row.symbol).strip().lower() not in reduce_blocked_symbols
        ]
        return filtered[: max(0, int(max_slots))]

    @classmethod
    def _entry_blocked_by_pending_intraday_reduce(
        cls,
        active_positions: list[dict[str, float | str | list[dict[str, float | str]]]],
        symbol: str,
        entry_date: str,
    ) -> bool:
        symbol_key = str(symbol or "").strip().lower()
        if not symbol_key:
            return False
        for item in active_positions:
            if str(item.get("symbol", "")).strip().lower() != symbol_key:
                continue
            cash_events = item.get("cash_events")
            if isinstance(cash_events, list):
                for event in cash_events:
                    if not isinstance(event, dict):
                        continue
                    if str(event.get("exit_date", "")).strip() == entry_date:
                        return True
            exit_reason = str(item.get("exit_reason", ""))
            if cls._is_intraday_reduce_only_exit(exit_reason) and str(item.get("exit_date", "")).strip() == entry_date:
                return True
        return False

    def _resolve_last_day_open_signal_slots(
        self,
        *,
        trades: list[BacktestTrade],
        end_date: str,
        next_trade_date: str | None,
        max_positions: int,
    ) -> tuple[int, set[str]]:
        max_positions = max(0, int(max_positions))
        held_trades = self._resolve_last_day_held_trades(trades, as_of_date=end_date)
        pending_sells = [
            trade
            for trade in held_trades
            if next_trade_date
            and trade.exit_date == next_trade_date
            and self._is_next_day_plan_sell(trade)
        ]
        held_by_symbol = self._group_held_trades_by_symbol(held_trades)
        freeing_symbols = {
            symbol
            for symbol, legs in held_by_symbol.items()
            if legs
            and not any(self._is_intraday_reduce_only_exit(str(leg.exit_reason or "")) for leg in legs)
            and all(self._is_next_day_plan_sell(leg) for leg in legs)
        }
        blocking_symbols = set(held_by_symbol.keys()) - freeing_symbols
        available_slots = self._compute_available_buy_slots(
            held_trades=held_trades,
            sell_signals=pending_sells,
            max_positions=max_positions,
        )
        return available_slots, blocking_symbols

    @staticmethod
    def _resolve_next_trade_date_from_candles(
        *,
        end_date: str,
        symbols: list[str],
        get_candles: Callable[[str], list[CandlePoint]],
    ) -> str | None:
        candidates: list[str] = []
        for raw_symbol in symbols:
            symbol = str(raw_symbol).strip().lower()
            if not symbol:
                continue
            candles = get_candles(symbol)
            for candle in candles:
                day = str(candle.time or "").strip()
                if day > end_date:
                    candidates.append(day)
                    break
        if not candidates:
            return BacktestEngine._advance_trading_day(end_date, 1)
        return min(candidates)

    def _build_operation_plans(
        self,
        *,
        trading_dates: list[str],
        trades: list[BacktestTrade],
        plan_signals: list[BacktestTrade],
        payload: BacktestRunRequest,
    ) -> list[BacktestOperationPlan]:
        if not trading_dates:
            return []
        buy_by_date: dict[str, list[BacktestTrade]] = defaultdict(list)
        for row in plan_signals:
            signal_date = str(row.signal_date or "").strip()
            if signal_date:
                buy_by_date[signal_date].append(row)

        out: list[BacktestOperationPlan] = []
        max_positions = max(0, int(payload.max_positions))
        last_date = trading_dates[-1]
        for idx, day in enumerate(trading_dates):
            next_day = trading_dates[idx + 1] if idx + 1 < len(trading_dates) else ""
            is_last_day = day == last_date
            held_trades = [
                trade
                for trade in trades
                if (
                    (
                        BacktestEngine._is_backtest_open_holding(trade.exit_reason)
                        and trade.entry_date <= day
                    )
                    or (trade.entry_date <= day < trade.exit_date)
                )
            ]
            sell_signals = [
                trade
                for trade in held_trades
                if (
                    self._is_next_day_plan_sell(trade)
                    and str(trade.exit_date or "").strip() > day
                )
            ]
            held_symbols = set(self._group_held_trades_by_symbol(held_trades).keys())
            reduce_blocked_symbols = self._symbols_with_pending_intraday_reduce(sell_signals)
            available_buy_slots = self._compute_available_buy_slots(
                held_trades=held_trades,
                sell_signals=sell_signals,
                max_positions=max_positions,
            )
            buy_signals = self._filter_plan_buy_signals(
                buy_by_date.get(day, []),
                held_symbols=held_symbols,
                reduce_blocked_symbols=reduce_blocked_symbols,
                max_slots=available_buy_slots,
            )
            out.append(
                BacktestOperationPlan(
                    date=day,
                    next_trade_date=next_day,
                    buy_signals=buy_signals,
                    sell_signals=sell_signals,
                )
            )
        return out

    @staticmethod
    def _resolve_exit_fallback_reason(
        *,
        entry_index: int,
        fallback_index: int,
        payload: BacktestRunRequest,
        trailing_triggered: bool,
        event_exit_pending: str | None,
    ) -> str:
        if event_exit_pending is not None or trailing_triggered:
            # The next opening execution is outside the available data window.
            return "open"
        if (fallback_index - entry_index + 1) >= payload.max_hold_days:
            return "time_exit"
        return "open"

    @staticmethod
    def _resolve_exit(
        candles: list[CandlePoint],
        entry_index: int,
        exit_signal_events_by_index: dict[int, list[str]],
        payload: BacktestRunRequest,
        *,
        max_bar_index: int | None = None,
        entry_price_override: float | None = None,
    ) -> tuple[int, float, str] | None:
        if entry_index >= len(candles):
            return None
        if entry_price_override is not None and math.isfinite(entry_price_override) and entry_price_override > 0:
            entry_price = float(entry_price_override)
        else:
            entry_price = BacktestEngine._resolve_entry_price_from_candle(candles[entry_index], payload)
        if not math.isfinite(entry_price) or entry_price <= 0:
            return None

        stop_price = entry_price * (1 - payload.stop_loss) if payload.stop_loss > 0 else None
        take_price = entry_price * (1 + payload.take_profit) if payload.take_profit > 0 else None
        trailing_pct, intraday_enabled, daily_enabled, reduce_ratio, confirm_days = (
            BacktestEngine._resolve_trailing_layer_flags(payload)
        )
        intraday_peak = entry_price
        close_peak = entry_price
        trailing_pending = False
        trailing_pending_reason = ""
        trailing_exit_price: float | None = None
        daily_weak_days = 0
        entry_date = candles[entry_index].time
        last_sellable_index: int | None = None

        upper_bound = len(candles) if max_bar_index is None else min(len(candles), max_bar_index + 1)
        event_exit_pending: str | None = None
        for bar_index in range(entry_index, upper_bound):
            bar = candles[bar_index]
            sellable_today = True
            if payload.enforce_t1:
                sellable_today = bar.time > entry_date

            if not sellable_today:
                bar_open = float(bar.open)
                bar_high = float(bar.high)
                bar_close = float(bar.close)
                if intraday_enabled and trailing_pct > 0:
                    open_to_close_dd = (
                        (bar_open - bar_close) / bar_open if bar_open > 0 and bar_close < bar_open else 0.0
                    )
                    high_to_close_dd = (
                        (bar_high - bar_close) / bar_high if bar_high > 0 and bar_close < bar_high else 0.0
                    )
                    if open_to_close_dd >= trailing_pct or high_to_close_dd >= trailing_pct:
                        trailing_pending = True
                        trailing_pending_reason = BacktestEngine._format_intraday_trailing_reason(reduce_ratio)
                        trailing_exit_price = bar_close if bar_close > 0 else None
                intraday_peak = max(intraday_peak, bar_high)
                close_peak = max(close_peak, bar_close)
                continue

            last_sellable_index = bar_index

            if trailing_pending or event_exit_pending is not None:
                reason = (
                    event_exit_pending
                    if event_exit_pending is not None
                    else BacktestEngine._resolve_trailing_pending_reason(
                        pending_reason=trailing_pending_reason,
                        trailing_pct=trailing_pct,
                        intraday_enabled=intraday_enabled,
                        daily_enabled=daily_enabled,
                        reduce_ratio=reduce_ratio,
                    )
                )
                return bar_index, float(bar.open), reason

            # Opening gaps execute at the first available price, before intraday ambiguity.
            if stop_price is not None and float(bar.open) <= stop_price:
                return bar_index, float(bar.open), "stop_loss"
            if take_price is not None and float(bar.open) >= take_price:
                return bar_index, float(bar.open), "take_profit"
            if stop_price is not None and float(bar.low) <= stop_price:
                if (
                    payload.ambiguity_policy == "optimistic"
                    and take_price is not None
                    and float(bar.high) >= take_price
                ):
                    return bar_index, float(take_price), "take_profit"
                return bar_index, float(stop_price), "stop_loss"
            if take_price is not None and float(bar.high) >= take_price:
                return bar_index, float(take_price), "take_profit"

            if intraday_enabled and trailing_pct > 0 and intraday_peak > entry_price:
                intraday_trigger = intraday_peak * (1 - trailing_pct)
                bar_open = float(bar.open)
                bar_close = float(bar.close)
                intraday_breach = float(bar.low) <= intraday_trigger and intraday_trigger > entry_price
                repair_failed = bar_close <= intraday_trigger or (
                    bar_open > 0 and bar_close < bar_open
                )
                if intraday_breach and repair_failed:
                    trailing_pending = True
                    trailing_pending_reason = BacktestEngine._format_intraday_trailing_reason(reduce_ratio)
                    trailing_exit_price = intraday_trigger
                    intraday_peak = max(intraday_peak, float(bar.high))
                    close_peak = max(close_peak, bar_close)
                    continue

            if daily_enabled and trailing_pct > 0 and close_peak > entry_price:
                daily_trigger = close_peak * (1 - trailing_pct)
                if float(bar.close) <= daily_trigger and daily_trigger > entry_price:
                    daily_weak_days += 1
                else:
                    daily_weak_days = 0
                if daily_weak_days >= confirm_days:
                    trailing_pending = True
                    trailing_pending_reason = "trailing_stop_daily"
                    trailing_exit_price = daily_trigger
                    intraday_peak = max(intraday_peak, float(bar.high))
                    close_peak = max(close_peak, float(bar.close))
                    continue

            intraday_peak = max(intraday_peak, float(bar.high))
            close_peak = max(close_peak, float(bar.close))

            if bar_index in exit_signal_events_by_index:
                event_names = [item for item in exit_signal_events_by_index.get(bar_index, []) if item]
                if event_names:
                    event_exit_pending = f"event_exit:{'/'.join(event_names)}"
                else:
                    event_exit_pending = "event_exit"
                continue
            if (bar_index - entry_index + 1) >= payload.max_hold_days:
                return bar_index, float(bar.close), "time_exit"

        if payload.enforce_t1 and last_sellable_index is None:
            if entry_index + 1 >= upper_bound and entry_index < upper_bound:
                close_px = float(candles[entry_index].close)
                if math.isfinite(close_px) and close_px > 0:
                    return entry_index, close_px, "open"
            return None
        fallback_index = last_sellable_index if last_sellable_index is not None else (
            upper_bound - 1 if upper_bound > 0 else len(candles) - 1
        )
        fallback_reason = BacktestEngine._resolve_exit_fallback_reason(
            entry_index=entry_index,
            fallback_index=fallback_index,
            payload=payload,
            trailing_triggered=trailing_pending,
            event_exit_pending=event_exit_pending,
        )
        return fallback_index, float(candles[fallback_index].close), fallback_reason

    @staticmethod
    def _resolve_exit_legs_from_candles(
        candles: list[CandlePoint],
        entry_index: int,
        entry_price: float,
        exit_signal_events_by_index: dict[int, list[str]],
        payload: BacktestRunRequest,
        max_bar_index: int | None = None,
    ) -> list[ExitLeg]:
        total_bars = len(candles)
        if entry_index >= total_bars:
            return []
        dates = [candle.time for candle in candles]
        open_col = np.array([float(candle.open) for candle in candles], dtype=np.float64)
        high_col = np.array([float(candle.high) for candle in candles], dtype=np.float64)
        low_col = np.array([float(candle.low) for candle in candles], dtype=np.float64)
        close_col = np.array([float(candle.close) for candle in candles], dtype=np.float64)
        valid_col = np.array(
            [
                math.isfinite(float(candle.open))
                and float(candle.open) > 0
                and math.isfinite(float(candle.close))
                and float(candle.close) > 0
                for candle in candles
            ],
            dtype=bool,
        )
        sell_col = np.zeros(total_bars, dtype=bool)
        sell_label_col = np.full(total_bars, "", dtype=object)
        for idx, events in exit_signal_events_by_index.items():
            bar_index = int(idx)
            if bar_index < 0 or bar_index >= total_bars:
                continue
            event_names = [str(item).strip() for item in events if str(item).strip()]
            if not event_names:
                continue
            sell_col[bar_index] = True
            sell_label_col[bar_index] = "/".join(event_names)
        return BacktestEngine._resolve_exit_matrix_legs(
            entry_index=entry_index,
            entry_price=entry_price,
            open_col=open_col,
            high_col=high_col,
            low_col=low_col,
            close_col=close_col,
            valid_col=valid_col,
            sell_col=sell_col,
            sell_label_col=sell_label_col,
            payload=payload,
            max_bar_index=max_bar_index,
            dates=dates,
        )

    @staticmethod
    def _resolve_exit_matrix(
        *,
        entry_index: int,
        entry_price: float,
        open_col: np.ndarray,
        high_col: np.ndarray,
        low_col: np.ndarray,
        close_col: np.ndarray,
        valid_col: np.ndarray,
        sell_col: np.ndarray,
        sell_label_col: np.ndarray | None,
        payload: BacktestRunRequest,
        max_bar_index: int | None = None,
    ) -> tuple[int, float, str] | None:
        legs = BacktestEngine._resolve_exit_matrix_legs(
            entry_index=entry_index,
            entry_price=entry_price,
            open_col=open_col,
            high_col=high_col,
            low_col=low_col,
            close_col=close_col,
            valid_col=valid_col,
            sell_col=sell_col,
            sell_label_col=sell_label_col,
            payload=payload,
            max_bar_index=max_bar_index,
        )
        if not legs:
            return None
        last = legs[-1]
        return last.exit_index, last.exit_price, last.exit_reason

    @staticmethod
    def _resolve_exit_matrix_legs(
        *,
        entry_index: int,
        entry_price: float,
        open_col: np.ndarray,
        high_col: np.ndarray,
        low_col: np.ndarray,
        close_col: np.ndarray,
        valid_col: np.ndarray,
        sell_col: np.ndarray,
        sell_label_col: np.ndarray | None,
        payload: BacktestRunRequest,
        max_bar_index: int | None = None,
        dates: list[str] | None = None,
    ) -> list[ExitLeg]:
        total_bars = int(valid_col.shape[0])
        if entry_index >= total_bars:
            return []
        if (not math.isfinite(entry_price)) or entry_price <= 0:
            return []

        stop_price = entry_price * (1 - payload.stop_loss) if payload.stop_loss > 0 else None
        take_price = entry_price * (1 + payload.take_profit) if payload.take_profit > 0 else None
        trailing_pct, intraday_enabled, daily_enabled, reduce_ratio, confirm_days = (
            BacktestEngine._resolve_trailing_layer_flags(payload)
        )
        intraday_peak = entry_price
        close_peak = entry_price
        trailing_pending = False
        trailing_pending_reason = ""
        trailing_exit_price: float | None = None
        daily_weak_days = 0
        last_sellable_index: int | None = None
        remaining_fraction = 1.0
        legs: list[ExitLeg] = []

        def _leg_date(bar_index: int) -> str:
            if dates and 0 <= int(bar_index) < len(dates):
                return str(dates[int(bar_index)])
            return ""

        def _append_full_leg(bar_index: int, price: float, reason: str, phase: str = "intraday") -> None:
            nonlocal remaining_fraction
            if remaining_fraction <= 1e-9:
                return
            legs.append(
                ExitLeg(
                    exit_index=int(bar_index),
                    exit_price=float(price),
                    exit_reason=str(reason),
                    sell_fraction=float(remaining_fraction),
                    exit_date=_leg_date(bar_index),
                    execution_phase=phase,
                )
            )
            remaining_fraction = 0.0

        def _append_reduce_or_full_leg(bar_index: int, price: float, reason: str, phase: str = "open") -> bool:
            nonlocal remaining_fraction, trailing_pending, trailing_pending_reason, trailing_exit_price
            nonlocal event_exit_pending, intraday_peak, close_peak, daily_weak_days
            if remaining_fraction <= 1e-9:
                return True
            if not BacktestEngine._is_intraday_reduce_only_exit(reason):
                _append_full_leg(bar_index, price, reason, phase)
                return True
            sell_fraction = min(float(reduce_ratio), float(remaining_fraction))
            if sell_fraction <= 1e-9:
                trailing_pending = False
                trailing_pending_reason = ""
                trailing_exit_price = None
                return False
            if remaining_fraction - sell_fraction < 1e-9:
                _append_full_leg(bar_index, price, reason, phase)
                return True
            legs.append(
                ExitLeg(
                    exit_index=int(bar_index),
                    exit_price=float(price),
                    exit_reason=str(reason),
                    sell_fraction=float(sell_fraction),
                    exit_date=_leg_date(bar_index),
                    execution_phase=phase,
                )
            )
            remaining_fraction -= sell_fraction
            trailing_pending = False
            trailing_pending_reason = ""
            trailing_exit_price = None
            event_exit_pending = None
            intraday_peak = max(entry_price, float(price))
            close_peak = max(close_peak, float(price))
            daily_weak_days = 0
            return False

        upper_bound = total_bars if max_bar_index is None else min(total_bars, max_bar_index + 1)
        start_index = entry_index + 1 if payload.enforce_t1 else entry_index
        if payload.enforce_t1 and entry_index < total_bars and bool(valid_col[entry_index]):
            entry_open = float(open_col[entry_index])
            entry_high = float(high_col[entry_index])
            entry_close = float(close_col[entry_index])
            if (
                intraday_enabled
                and trailing_pct > 0
                and math.isfinite(entry_open)
                and math.isfinite(entry_high)
                and math.isfinite(entry_close)
                and entry_open > 0
                and entry_high > 0
            ):
                open_to_close_dd = (
                    (entry_open - entry_close) / entry_open if entry_close < entry_open else 0.0
                )
                high_to_close_dd = (
                    (entry_high - entry_close) / entry_high if entry_close < entry_high else 0.0
                )
                if open_to_close_dd >= trailing_pct or high_to_close_dd >= trailing_pct:
                    trailing_pending = True
                    trailing_pending_reason = BacktestEngine._format_intraday_trailing_reason(reduce_ratio)
                    trailing_exit_price = entry_close if entry_close > 0 else None
            if math.isfinite(entry_high) and entry_high > 0:
                intraday_peak = max(intraday_peak, entry_high)
            if math.isfinite(entry_close) and entry_close > 0:
                close_peak = max(close_peak, entry_close)
        event_exit_pending: str | None = None
        for bar_index in range(start_index, upper_bound):
            if not bool(valid_col[bar_index]):
                continue
            last_sellable_index = bar_index

            low_price = float(low_col[bar_index])
            high_price = float(high_col[bar_index])
            close_price = float(close_col[bar_index])
            if not (math.isfinite(low_price) and math.isfinite(high_price) and math.isfinite(close_price)):
                continue
            if low_price <= 0 or high_price <= 0 or close_price <= 0:
                continue

            if trailing_pending or event_exit_pending is not None:
                reason = (
                    event_exit_pending
                    if event_exit_pending is not None
                    else BacktestEngine._resolve_trailing_pending_reason(
                        pending_reason=trailing_pending_reason,
                        trailing_pct=trailing_pct,
                        intraday_enabled=intraday_enabled,
                        daily_enabled=daily_enabled,
                        reduce_ratio=reduce_ratio,
                    )
                )
                open_price = float(open_col[bar_index])
                exit_price = float(open_price) if math.isfinite(open_price) and open_price > 0 else float(close_price)
                if _append_reduce_or_full_leg(bar_index, exit_price, reason):
                    return legs
                continue

            open_price = float(open_col[bar_index])
            if math.isfinite(open_price) and open_price > 0:
                if stop_price is not None and open_price <= stop_price:
                    _append_full_leg(bar_index, open_price, "stop_loss", "open")
                    return legs
                if take_price is not None and open_price >= take_price:
                    _append_full_leg(bar_index, open_price, "take_profit", "open")
                    return legs
            if stop_price is not None and low_price <= stop_price:
                if (
                    payload.ambiguity_policy == "optimistic"
                    and take_price is not None
                    and high_price >= take_price
                ):
                    _append_full_leg(bar_index, float(take_price), "take_profit")
                    return legs
                _append_full_leg(bar_index, float(stop_price), "stop_loss")
                return legs
            if take_price is not None and high_price >= take_price:
                _append_full_leg(bar_index, float(take_price), "take_profit")
                return legs

            if intraday_enabled and trailing_pct > 0 and intraday_peak > entry_price:
                intraday_trigger = intraday_peak * (1 - trailing_pct)
                open_price = float(open_col[bar_index])
                intraday_breach = low_price <= intraday_trigger and intraday_trigger > entry_price
                repair_failed = close_price <= intraday_trigger or (
                    open_price > 0 and close_price < open_price
                )
                if intraday_breach and repair_failed:
                    trailing_pending = True
                    trailing_pending_reason = BacktestEngine._format_intraday_trailing_reason(reduce_ratio)
                    trailing_exit_price = intraday_trigger
                    intraday_peak = max(intraday_peak, high_price)
                    close_peak = max(close_peak, close_price)
                    continue

            if daily_enabled and trailing_pct > 0 and close_peak > entry_price:
                daily_trigger = close_peak * (1 - trailing_pct)
                if close_price <= daily_trigger and daily_trigger > entry_price:
                    daily_weak_days += 1
                else:
                    daily_weak_days = 0
                if daily_weak_days >= confirm_days:
                    trailing_pending = True
                    trailing_pending_reason = "trailing_stop_daily"
                    trailing_exit_price = daily_trigger
                    intraday_peak = max(intraday_peak, high_price)
                    close_peak = max(close_peak, close_price)
                    continue

            intraday_peak = max(intraday_peak, high_price)
            close_peak = max(close_peak, close_price)

            if bool(sell_col[bar_index]):
                exit_label = ""
                if sell_label_col is not None:
                    try:
                        exit_label = str(sell_label_col[bar_index]).strip()
                    except Exception:
                        exit_label = ""
                if not exit_label:
                    exit_label = BacktestEngine._build_matrix_exit_signal_label(payload)
                event_exit_pending = f"event_exit:{exit_label}"
                continue
            if (bar_index - entry_index + 1) >= payload.max_hold_days:
                _append_full_leg(bar_index, float(close_price), "time_exit", "close")
                return legs

        if payload.enforce_t1 and last_sellable_index is None:
            if entry_index + 1 >= upper_bound and entry_index < upper_bound:
                close_px = float(close_col[entry_index])
                if math.isfinite(close_px) and close_px > 0 and remaining_fraction > 1e-9:
                    legs.append(
                        ExitLeg(
                            exit_index=int(entry_index),
                            exit_price=float(close_px),
                            exit_reason="open",
                            sell_fraction=float(remaining_fraction),
                            exit_date=_leg_date(entry_index),
                        )
                    )
            return legs
        fallback_index = last_sellable_index if last_sellable_index is not None else (
            upper_bound - 1 if upper_bound > 0 else total_bars - 1
        )
        fallback_price = float(close_col[fallback_index]) if math.isfinite(float(close_col[fallback_index])) else entry_price
        if fallback_price <= 0:
            fallback_price = entry_price
        fallback_reason = BacktestEngine._resolve_exit_fallback_reason(
            entry_index=entry_index,
            fallback_index=fallback_index,
            payload=payload,
            trailing_triggered=trailing_pending,
            event_exit_pending=event_exit_pending,
        )
        if remaining_fraction > 1e-9:
            legs.append(
                ExitLeg(
                    exit_index=int(fallback_index),
                    exit_price=float(fallback_price),
                    exit_reason=fallback_reason,
                    sell_fraction=float(remaining_fraction),
                    exit_date=_leg_date(fallback_index),
                    execution_phase="close",
                )
            )
        return legs

    def _build_plan_candidates_from_matrix(
        self,
        *,
        payload: BacktestRunRequest,
        symbols: list[str],
        start_date: str,
        end_date: str,
        matrix_bundle: MatrixBundle,
        matrix_signals: BacktestSignalMatrix,
        allowed_symbols_by_date: dict[str, set[str]] | None = None,
        control_callback: Callable[[], None] | None = None,
    ) -> list[CandidateTrade]:
        dates = list(matrix_bundle.dates)
        if not dates:
            return []

        total_shape = (len(dates), len(matrix_bundle.symbols))
        if (
            matrix_bundle.close.shape != total_shape
            or matrix_bundle.valid_mask.shape != total_shape
            or matrix_signals.buy_signal.shape != total_shape
            or matrix_signals.score.shape != total_shape
        ):
            return []

        in_range_mask = np.fromiter(
            (start_date <= day <= end_date for day in dates),
            dtype=bool,
            count=len(dates),
        )
        if int(np.count_nonzero(in_range_mask)) <= 0:
            return []

        symbol_to_col = matrix_bundle.symbol_to_index()
        in_range_valid_buy = matrix_signals.buy_signal & matrix_bundle.valid_mask & in_range_mask[:, np.newaxis]
        buy_any_by_col = np.any(in_range_valid_buy, axis=0)
        out: list[CandidateTrade] = []

        for raw_symbol in symbols:
            if control_callback is not None:
                control_callback()
            symbol = str(raw_symbol).strip().lower()
            col = symbol_to_col.get(symbol)
            if col is None:
                continue
            if not bool(buy_any_by_col[col]):
                continue

            close_col = matrix_bundle.close[:, col]
            score_col = matrix_signals.score[:, col]
            buy_indexes = np.flatnonzero(in_range_valid_buy[:, col])
            if buy_indexes.size <= 0:
                continue
            if allowed_symbols_by_date is not None:
                filtered_indexes = [
                    int(idx)
                    for idx in buy_indexes.tolist()
                    if symbol in allowed_symbols_by_date.get(dates[int(idx)], set())
                ]
                if not filtered_indexes:
                    continue
                buy_indexes = np.asarray(filtered_indexes, dtype=np.int64)

            for signal_index in buy_indexes.tolist():
                if control_callback is not None:
                    control_callback()
                signal_date = dates[int(signal_index)]
                close_price = float(close_col[int(signal_index)])
                if not math.isfinite(close_price) or close_price <= 0:
                    continue
                score = float(score_col[int(signal_index)])
                if not math.isfinite(score):
                    score = 0.0
                score = max(0.0, min(100.0, score))
                if payload.matrix_event_semantic_version != MATRIX_SEMANTIC_ALIGNED and score < payload.min_score:
                    continue

                entry_tags: list[str] = []
                if bool(matrix_signals.s5[int(signal_index), col]):
                    entry_tags.append("SOS")
                if bool(matrix_signals.s6[int(signal_index), col]):
                    entry_tags.append("LPS")
                if not entry_tags:
                    entry_tags.append(payload.entry_events[0] if payload.entry_events else "ENTRY")

                entry_phase = "吸筹D" if bool(matrix_signals.s7[int(signal_index), col]) else "阶段不明"
                meta: dict[str, Any] = {
                    "entry_signal": " / ".join(entry_tags),
                    "entry_phase": entry_phase,
                    "entry_quality_score": score,
                    "candle_quality_score": score,
                    "cost_center_shift_score": score,
                    "weekly_context_score": score,
                    "weekly_context_multiplier": 1.0,
                    "entry_phase_score": float(PHASE_PRIORITY_SCORE.get(entry_phase, 0.0)),
                    "entry_events_weight": float(len(entry_tags)),
                    "entry_structure_score": int(bool(matrix_signals.in_pool[int(signal_index), col])),
                    "entry_trend_score": score,
                    "entry_volatility_score": score,
                    "health_score": score,
                    "event_score": score,
                    "risk_score": 0.0,
                    "confirmation_status": "unconfirmed",
                    "event_grade": "C",
                    "phase_context_score": 0.0,
                    "event_recency_score": 0.0,
                    "final_rank_score": self._compute_final_rank_score(
                        payload=payload,
                        health_score=score,
                        event_score=score,
                    ),
                }

                if payload.matrix_event_semantic_version == MATRIX_SEMANTIC_ALIGNED:
                    semantic_meta = self._build_matrix_semantic_meta(
                        symbol=symbol,
                        signal_date=signal_date,
                        payload=payload,
                    )
                    if semantic_meta is not None:
                        meta.update(semantic_meta)
                        meta["final_rank_score"] = self._compute_final_rank_score(
                            payload=payload,
                            health_score=float(meta.get("health_score", meta.get("entry_quality_score", 0.0)) or 0.0),
                            event_score=float(meta.get("event_score", meta.get("entry_quality_score", 0.0)) or 0.0),
                        )

                entry_date = self._resolve_planned_entry_date_from_dates(dates, int(signal_index), payload)
                if entry_date is None:
                    continue
                plan = self._build_plan_candidate_from_meta(
                    symbol=symbol,
                    signal_date=signal_date,
                    entry_date=entry_date,
                    reference_price=close_price,
                    meta=meta,
                    payload=payload,
                )
                if plan is not None:
                    out.append(plan)

        return out

    def _build_candidates_from_matrix(
        self,
        *,
        payload: BacktestRunRequest,
        symbols: list[str],
        start_date: str,
        end_date: str,
        matrix_bundle: MatrixBundle,
        matrix_signals: BacktestSignalMatrix,
        allowed_symbols_by_date: dict[str, set[str]] | None = None,
        allow_reentry_after_skipped: bool = False,
        control_callback: Callable[[], None] | None = None,
    ) -> tuple[list[CandidateTrade], int, dict[str, int]]:
        dates = list(matrix_bundle.dates)
        if not dates:
            return [], 0, self._build_delay_skip_counter()

        total_shape = (len(dates), len(matrix_bundle.symbols))
        if (
            matrix_bundle.open.shape != total_shape
            or matrix_bundle.high.shape != total_shape
            or matrix_bundle.low.shape != total_shape
            or matrix_bundle.close.shape != total_shape
            or matrix_bundle.valid_mask.shape != total_shape
            or matrix_signals.buy_signal.shape != total_shape
            or matrix_signals.sell_signal.shape != total_shape
            or matrix_signals.score.shape != total_shape
        ):
            raise ValueError("matrix bundle / signals shape mismatch")

        in_range_mask = np.fromiter(
            (start_date <= day <= end_date for day in dates),
            dtype=bool,
            count=len(dates),
        )
        if int(np.count_nonzero(in_range_mask)) < 2:
            return [], 0, self._build_delay_skip_counter()

        in_range_indices = np.flatnonzero(in_range_mask)
        max_exit_bar_index = int(in_range_indices[-1])

        symbol_to_col = matrix_bundle.symbol_to_index()
        out: list[CandidateTrade] = []
        t1_no_sellable_skips = 0
        delay_skip_reasons = self._build_delay_skip_counter()
        in_range_valid_buy = matrix_signals.buy_signal & matrix_bundle.valid_mask & in_range_mask[:, np.newaxis]
        buy_any_by_col = np.any(in_range_valid_buy, axis=0)

        for raw_symbol in symbols:
            if control_callback is not None:
                control_callback()
            symbol = str(raw_symbol).strip().lower()
            col = symbol_to_col.get(symbol)
            if col is None:
                continue
            if not bool(buy_any_by_col[col]):
                continue

            open_col = matrix_bundle.open[:, col]
            high_col = matrix_bundle.high[:, col]
            low_col = matrix_bundle.low[:, col]
            close_col = matrix_bundle.close[:, col]
            valid_col = matrix_bundle.valid_mask[:, col]

            sell_col = matrix_signals.sell_signal[:, col]
            score_col = matrix_signals.score[:, col]

            buy_indexes = np.flatnonzero(in_range_valid_buy[:, col])
            if buy_indexes.size <= 0:
                continue
            if allowed_symbols_by_date is not None:
                filtered_indexes = [
                    int(idx)
                    for idx in buy_indexes.tolist()
                    if symbol in allowed_symbols_by_date.get(dates[int(idx)], set())
                ]
                if not filtered_indexes:
                    continue
                buy_indexes = np.asarray(filtered_indexes, dtype=np.int64)

            blocked_until = -1
            for signal_index in buy_indexes.tolist():
                if control_callback is not None:
                    control_callback()
                if (not allow_reentry_after_skipped) and signal_index <= blocked_until:
                    continue

                entry_index = self._resolve_entry_index(signal_index, payload)
                if entry_index >= len(dates):
                    delay_skip_reasons[DELAY_SKIP_REASON_NO_ENTRY_DAY] += 1
                    continue
                if not bool(valid_col[entry_index]):
                    continue
                if payload.delay_invalidation_enabled:
                    if payload.matrix_event_semantic_version == MATRIX_SEMANTIC_ALIGNED:
                        delay_reason = self._resolve_delay_invalidation_reason_matrix_aligned(
                            symbol=symbol,
                            signal_index=int(signal_index),
                            entry_index=int(entry_index),
                            dates=dates,
                            payload=payload,
                        )
                    else:
                        delay_reason = self._resolve_delay_invalidation_reason_matrix(
                            signal_index=int(signal_index),
                            entry_index=int(entry_index),
                            valid_col=valid_col,
                            sell_col=sell_col,
                        )
                    if delay_reason:
                        delay_skip_reasons[delay_reason] = delay_skip_reasons.get(delay_reason, 0) + 1
                        continue

                entry_price = (
                    float(close_col[entry_index])
                    if self._uses_same_day_close_entry(payload)
                    else float(open_col[entry_index])
                )
                if (not math.isfinite(entry_price)) or entry_price <= 0:
                    continue

                exit_legs = self._resolve_exit_matrix_legs(
                    entry_index=entry_index,
                    entry_price=entry_price,
                    open_col=open_col,
                    high_col=high_col,
                    low_col=low_col,
                    close_col=close_col,
                    valid_col=valid_col,
                    sell_col=sell_col,
                    sell_label_col=matrix_signals.sell_signal_label[:, col] if matrix_signals.sell_signal_label is not None else None,
                    payload=payload,
                    max_bar_index=max_exit_bar_index,
                    dates=dates,
                )
                if not exit_legs:
                    t1_no_sellable_skips += 1
                    continue

                last_leg = exit_legs[-1]
                exit_index, exit_price, exit_reason = last_leg.exit_index, last_leg.exit_price, last_leg.exit_reason
                entry_tags: list[str] = []
                if bool(matrix_signals.s5[signal_index, col]):
                    entry_tags.append("SOS")
                if bool(matrix_signals.s6[signal_index, col]):
                    entry_tags.append("LPS")
                if not entry_tags:
                    entry_tags.append(payload.entry_events[0] if payload.entry_events else "ENTRY")
                entry_phase = "鍚哥D" if bool(matrix_signals.s7[signal_index, col]) else "闃舵鏈槑"
                entry_quality_score = float(score_col[signal_index]) if math.isfinite(float(score_col[signal_index])) else 0.0
                candle_quality_score = max(0.0, min(100.0, entry_quality_score))
                cost_center_shift_score = max(0.0, min(100.0, entry_quality_score))
                weekly_context_score = max(0.0, min(100.0, entry_quality_score))
                weekly_context_multiplier = 1.0
                entry_signal_text = " / ".join(entry_tags)
                entry_phase_score = float(PHASE_PRIORITY_SCORE.get(entry_phase, 0.0))
                entry_events_weight = float(len(entry_tags))
                entry_structure_score = int(bool(matrix_signals.in_pool[signal_index, col]))
                entry_trend_score = max(0.0, min(100.0, entry_quality_score))
                entry_volatility_score = max(0.0, min(100.0, entry_quality_score))
                health_score = max(0.0, min(100.0, entry_quality_score))
                event_score = max(0.0, min(100.0, entry_quality_score))
                risk_score = 0.0
                confirmation_status = "unconfirmed"
                event_grade = "C"
                phase_context_score = 0.0
                event_recency_score = 0.0

                if payload.matrix_event_semantic_version == MATRIX_SEMANTIC_ALIGNED:
                    semantic_meta = self._build_matrix_semantic_meta(
                        symbol=symbol,
                        signal_date=dates[signal_index],
                        payload=payload,
                    )
                    if semantic_meta is None:
                        continue
                    entry_signal_text = str(semantic_meta["entry_signal"])
                    entry_phase = str(semantic_meta["entry_phase"])
                    entry_quality_score = float(semantic_meta["entry_quality_score"])
                    candle_quality_score = float(semantic_meta.get("candle_quality_score", 0.0) or 0.0)
                    cost_center_shift_score = float(semantic_meta.get("cost_center_shift_score", 0.0) or 0.0)
                    weekly_context_score = float(semantic_meta.get("weekly_context_score", 50.0) or 50.0)
                    weekly_context_multiplier = float(semantic_meta.get("weekly_context_multiplier", 1.0) or 1.0)
                    entry_phase_score = float(semantic_meta["entry_phase_score"])
                    entry_events_weight = float(semantic_meta["entry_events_weight"])
                    entry_structure_score = int(semantic_meta["entry_structure_score"])
                    entry_trend_score = float(semantic_meta["entry_trend_score"])
                    entry_volatility_score = float(semantic_meta["entry_volatility_score"])
                    health_score = float(semantic_meta["health_score"])
                    event_score = float(semantic_meta["event_score"])
                    risk_score = float(semantic_meta.get("risk_score", 0.0) or 0.0)
                    confirmation_status = self._normalize_confirmation_status(
                        semantic_meta.get("confirmation_status", "unconfirmed")
                    )
                    event_grade = self._normalize_event_grade(semantic_meta["event_grade"])
                    phase_context_score = float(semantic_meta.get("phase_context_score", 0.0) or 0.0)
                    event_recency_score = float(semantic_meta.get("event_recency_score", 0.0) or 0.0)
                elif not self._passes_semantic_score_gates(
                    payload=payload,
                    health_score=health_score,
                    event_score=event_score,
                    event_grade=event_grade,
                    confirmation_status=confirmation_status,
                ):
                    continue

                final_rank_score = self._compute_final_rank_score(
                    payload=payload,
                    health_score=health_score,
                    event_score=event_score,
                )

                out.append(
                    CandidateTrade(
                        symbol=symbol,
                        signal_date=dates[signal_index],
                        entry_date=dates[entry_index],
                        exit_date=dates[exit_index],
                        entry_signal=entry_signal_text,
                        entry_phase=entry_phase,
                        entry_quality_score=max(0.0, min(100.0, entry_quality_score)),
                        candle_quality_score=max(0.0, min(100.0, candle_quality_score)),
                        cost_center_shift_score=max(0.0, min(100.0, cost_center_shift_score)),
                        weekly_context_score=max(0.0, min(100.0, weekly_context_score)),
                        weekly_context_multiplier=max(0.85, min(1.15, weekly_context_multiplier)),
                        entry_phase_score=float(entry_phase_score),
                        entry_events_weight=float(entry_events_weight),
                        entry_structure_score=int(entry_structure_score),
                        entry_trend_score=float(entry_trend_score),
                        entry_volatility_score=float(entry_volatility_score),
                        health_score=max(0.0, min(100.0, health_score)),
                        event_score=max(0.0, min(100.0, event_score)),
                        risk_score=max(0.0, min(100.0, risk_score)),
                        confirmation_status=confirmation_status,
                        event_grade=event_grade,
                        phase_context_score=max(0.0, min(100.0, phase_context_score)),
                        event_recency_score=max(0.0, min(100.0, event_recency_score)),
                        delay_entry_days=max(1, int(payload.entry_delay_days)),
                        delay_window_days=max(0, int(entry_index) - int(signal_index) - 1),
                        final_rank_score=float(final_rank_score),
                        entry_price=entry_price,
                        exit_price=float(exit_price),
                        holding_days=max(0, exit_index - entry_index + 1),
                        exit_reason=exit_reason,
                        entry_index=int(entry_index),
                        exit_legs=list(exit_legs),
                    )
                )
                if not allow_reentry_after_skipped:
                    blocked_until = max(blocked_until, int(exit_index))

        return out, t1_no_sellable_skips, delay_skip_reasons

    def _build_candidates_for_symbol(
        self,
        symbol: str,
        payload: BacktestRunRequest,
        start_date: str,
        end_date: str,
        allowed_symbols_by_date: dict[str, set[str]] | None = None,
        allow_reentry_after_skipped: bool = False,
        control_callback: Callable[[], None] | None = None,
    ) -> tuple[list[CandidateTrade], int, dict[str, int], list[CandidateTrade]]:
        candles = self._get_candles(symbol)
        if len(candles) < 30:
            return [], 0, self._build_delay_skip_counter(), []

        in_range_indexes = [
            idx
            for idx, candle in enumerate(candles)
            if start_date <= candle.time <= end_date
        ]
        if not in_range_indexes:
            return [], 0, self._build_delay_skip_counter(), []

        max_exit_bar_index = in_range_indexes[-1]

        entry_meta_by_index: dict[int, dict[str, Any]] = {}
        exit_signal_events_by_index: dict[int, list[str]] = {}
        risk_events_by_index: dict[int, list[str]] = {}
        plan_candidates: list[CandidateTrade] = []
        t1_no_sellable_skips = 0
        delay_skip_reasons = self._build_delay_skip_counter()
        score_only_strategy = self._is_score_only_strategy(payload)

        for idx in in_range_indexes:
            if control_callback is not None:
                control_callback()
            as_of_date = candles[idx].time
            if allowed_symbols_by_date is not None:
                allowed_today = allowed_symbols_by_date.get(as_of_date, set())
                if symbol not in allowed_today:
                    continue
            row = self._build_row(symbol, as_of_date)
            if row is None:
                continue
            snapshot = self._calc_snapshot(row, payload.window_days, as_of_date)
            if self._strategy_signal_filter is not None:
                strategy_params = payload.strategy_params if isinstance(payload.strategy_params, dict) else {}
                if not self._strategy_signal_filter(
                    str(payload.strategy_id).strip(),
                    row,
                    snapshot,
                    strategy_params,
                ):
                    continue
            strategy_signal_context = self._resolve_strategy_signal_context(payload=payload, snapshot=snapshot)

            event_dates = self._normalize_event_dates(snapshot.get("event_dates"))
            day_entry_events = [
                event_name
                for event_name in payload.entry_events
                if event_dates.get(event_name) == as_of_date
            ]
            day_exit_events = [
                event_name
                for event_name in payload.exit_events
                if event_dates.get(event_name) == as_of_date
            ]
            raw_risk_events = snapshot.get("risk_events")
            day_risk_events: list[str] = []
            if isinstance(raw_risk_events, list):
                for raw_event in raw_risk_events:
                    event_text = str(raw_event).strip()
                    if not event_text:
                        continue
                    if event_text not in DELAY_INVALIDATION_RISK_EVENTS:
                        continue
                    if event_dates.get(event_text) == as_of_date:
                        day_risk_events.append(event_text)
            if day_exit_events:
                exit_signal_events_by_index[idx] = day_exit_events
            if str(payload.strategy_id).strip() == "ths_force_rhythm_v1":
                rhythm_ind = snapshot.get("force_rhythm_signal")
                if isinstance(rhythm_ind, dict):
                    from .force_rhythm_strategy import evaluate_force_rhythm_exit

                    strategy_params = payload.strategy_params if isinstance(payload.strategy_params, dict) else {}
                    rhythm_exit = evaluate_force_rhythm_exit(rhythm_ind, strategy_params)
                    if bool(rhythm_exit.get("exit_signal")):
                        exit_label = str(rhythm_exit.get("exit_reason") or "散户卖出").strip()
                        merged = list(exit_signal_events_by_index.get(idx, []))
                        if exit_label and exit_label not in merged:
                            merged.append(exit_label)
                        exit_signal_events_by_index[idx] = merged
            if day_risk_events:
                risk_events_by_index[idx] = day_risk_events
            if not day_entry_events:
                if score_only_strategy:
                    strategy_signal_name = ""
                    if strategy_signal_context is not None:
                        strategy_signal_name = str(strategy_signal_context.get("signal_name") or "").strip()
                    day_entry_events = [strategy_signal_name or self._resolve_event_independent_entry_label(payload)]
                else:
                    continue

            event_count = self._normalize_event_count(snapshot)
            sequence_ok = bool(snapshot.get("sequence_ok"))
            entry_quality_score = float(snapshot.get("entry_quality_score", 0.0) or 0.0)
            candle_quality_score = float(snapshot.get("candle_quality_score", 0.0) or 0.0)
            cost_center_shift_score = float(
                snapshot.get("cost_center_shift_score", entry_quality_score) or entry_quality_score
            )
            weekly_context_score = float(snapshot.get("weekly_context_score", 50.0) or 50.0)
            weekly_context_multiplier = float(snapshot.get("weekly_context_multiplier", 1.0) or 1.0)
            health_score = float(snapshot.get("health_score", entry_quality_score) or entry_quality_score)
            event_score = float(
                snapshot.get(
                    "event_score",
                    snapshot.get("event_strength_score", entry_quality_score),
                )
                or 0.0
            )
            risk_score = float(snapshot.get("risk_score", 0.0) or 0.0)
            confirmation_status = self._normalize_confirmation_status(snapshot.get("confirmation_status", "unconfirmed"))
            event_grade = self._normalize_event_grade(snapshot.get("event_grade", "C"))
            entry_phase = str(snapshot.get("phase", "闃舵鏈槑"))
            structure_hhh = str(snapshot.get("structure_hhh", "-"))
            trend_score = float(snapshot.get("trend_score", 50.0) or 50.0)
            volatility_score = float(snapshot.get("volatility_score", 50.0) or 50.0)
            phase_context_score = float(snapshot.get("phase_context_score", 0.0) or 0.0)
            event_recency_score = float(snapshot.get("event_recency_score", 0.0) or 0.0)

            if strategy_signal_context is not None:
                sequence_ok = bool(strategy_signal_context.get("sequence_ok", sequence_ok))
                entry_quality_score = float(
                    strategy_signal_context.get("entry_quality_score", entry_quality_score) or 0.0
                )
                candle_quality_score = float(
                    strategy_signal_context.get("candle_quality_score", candle_quality_score) or entry_quality_score
                )
                cost_center_shift_score = float(
                    strategy_signal_context.get("cost_center_shift_score", cost_center_shift_score) or entry_quality_score
                )
                weekly_context_score = float(
                    strategy_signal_context.get("weekly_context_score", weekly_context_score) or entry_quality_score
                )
                weekly_context_multiplier = float(
                    strategy_signal_context.get("weekly_context_multiplier", weekly_context_multiplier) or 1.0
                )
                health_score = float(strategy_signal_context.get("health_score", health_score) or entry_quality_score)
                event_score = float(strategy_signal_context.get("event_score", event_score) or entry_quality_score)
                risk_score = float(strategy_signal_context.get("risk_score", risk_score) or 0.0)
                confirmation_status = self._normalize_confirmation_status(
                    strategy_signal_context.get("confirmation_status", confirmation_status)
                )
                event_grade = self._normalize_event_grade(strategy_signal_context.get("event_grade", event_grade))
                entry_phase = str(strategy_signal_context.get("phase", entry_phase) or entry_phase)
                structure_hhh = str(strategy_signal_context.get("structure_hhh", structure_hhh) or structure_hhh)
                trend_score = float(strategy_signal_context.get("trend_score", trend_score) or entry_quality_score)
                volatility_score = float(
                    strategy_signal_context.get("volatility_score", volatility_score) or entry_quality_score
                )
                phase_context_score = float(
                    strategy_signal_context.get("phase_context_score", phase_context_score) or 0.0
                )
                event_recency_score = float(
                    strategy_signal_context.get("event_recency_score", event_recency_score) or 0.0
                )
            if not score_only_strategy:
                if event_count < payload.min_event_count:
                    continue
                if payload.require_sequence and not sequence_ok:
                    continue
            if entry_quality_score < payload.min_score:
                continue
            if not self._passes_semantic_score_gates(
                payload=payload,
                health_score=health_score,
                event_score=event_score,
                event_grade=event_grade,
                confirmation_status=confirmation_status,
            ):
                continue

            if score_only_strategy:
                final_rank_score = max(0.0, min(100.0, float(entry_quality_score)))
            else:
                final_rank_score = self._compute_final_rank_score(
                    payload=payload,
                    health_score=health_score,
                    event_score=event_score,
                )
            entry_meta_by_index[idx] = {
                "entry_signal": " / ".join(day_entry_events),
                "entry_phase": entry_phase,
                "entry_quality_score": entry_quality_score,
                "candle_quality_score": max(0.0, min(100.0, candle_quality_score)),
                "cost_center_shift_score": max(0.0, min(100.0, cost_center_shift_score)),
                "weekly_context_score": max(0.0, min(100.0, weekly_context_score)),
                "weekly_context_multiplier": max(0.85, min(1.15, weekly_context_multiplier)),
                "entry_phase_score": PHASE_PRIORITY_SCORE.get(entry_phase, 0.0),
                "entry_events_weight": float(sum(ENTRY_EVENT_WEIGHTS.get(evt, 1.0) for evt in day_entry_events)),
                "entry_structure_score": self._structure_score(structure_hhh),
                "entry_trend_score": float(trend_score),
                "entry_volatility_score": float(volatility_score),
                "health_score": max(0.0, min(100.0, health_score)),
                "event_score": max(0.0, min(100.0, event_score)),
                "risk_score": max(0.0, min(100.0, risk_score)),
                "confirmation_status": confirmation_status,
                "event_grade": event_grade,
                "phase_context_score": max(0.0, min(100.0, float(phase_context_score))),
                "event_recency_score": max(0.0, min(100.0, float(event_recency_score))),
                "final_rank_score": final_rank_score,
            }
            entry_date_for_plan = self._resolve_planned_entry_date(candles, idx, payload)
            if entry_date_for_plan is None:
                continue
            plan_candidate = self._build_plan_candidate_from_meta(
                symbol=symbol,
                signal_date=as_of_date,
                entry_date=entry_date_for_plan,
                reference_price=float(candles[idx].close),
                meta=entry_meta_by_index[idx],
                payload=payload,
            )
            if plan_candidate is not None:
                plan_candidates.append(plan_candidate)

        out: list[CandidateTrade] = []
        if len(in_range_indexes) < 2:
            return out, t1_no_sellable_skips, delay_skip_reasons, plan_candidates
        cursor = 0
        while cursor < len(in_range_indexes) - 1:
            signal_index = in_range_indexes[cursor]
            meta = entry_meta_by_index.get(signal_index)
            if not meta:
                cursor += 1
                continue

            entry_index = self._resolve_entry_index(signal_index, payload)
            if entry_index >= len(candles):
                delay_skip_reasons[DELAY_SKIP_REASON_NO_ENTRY_DAY] += 1
                break
            if payload.delay_invalidation_enabled:
                delay_reason = self._resolve_delay_invalidation_reason_legacy(
                    signal_index=int(signal_index),
                    entry_index=int(entry_index),
                    risk_events_by_index=risk_events_by_index,
                    exit_signal_events_by_index=exit_signal_events_by_index,
                )
                if delay_reason:
                    delay_skip_reasons[delay_reason] = delay_skip_reasons.get(delay_reason, 0) + 1
                    cursor += 1
                    continue
            entry_bar = candles[entry_index]
            entry_price = self._resolve_entry_price_from_candle(entry_bar, payload)
            if not math.isfinite(entry_price) or entry_price <= 0:
                cursor += 1
                continue

            exit_resolved = self._resolve_exit(
                candles,
                entry_index,
                exit_signal_events_by_index,
                payload,
                max_bar_index=max_exit_bar_index,
                entry_price_override=entry_price,
            )
            exit_legs = self._resolve_exit_legs_from_candles(
                candles,
                entry_index,
                entry_price,
                exit_signal_events_by_index,
                payload,
                max_bar_index=max_exit_bar_index,
            )
            if exit_resolved is None or not exit_legs:
                t1_no_sellable_skips += 1
                cursor += 1
                continue

            exit_index, exit_price, exit_reason = exit_resolved
            out.append(
                CandidateTrade(
                    symbol=symbol,
                    signal_date=candles[signal_index].time,
                    entry_date=entry_bar.time,
                    exit_date=candles[exit_index].time,
                    entry_signal=str(meta["entry_signal"]),
                    entry_phase=str(meta["entry_phase"]),
                    entry_quality_score=float(meta["entry_quality_score"]),
                    candle_quality_score=float(meta["candle_quality_score"]),
                    cost_center_shift_score=float(meta["cost_center_shift_score"]),
                    weekly_context_score=float(meta["weekly_context_score"]),
                    weekly_context_multiplier=float(meta["weekly_context_multiplier"]),
                    entry_phase_score=float(meta["entry_phase_score"]),
                    entry_events_weight=float(meta["entry_events_weight"]),
                    entry_structure_score=int(meta["entry_structure_score"]),
                    entry_trend_score=float(meta["entry_trend_score"]),
                    entry_volatility_score=float(meta["entry_volatility_score"]),
                    health_score=float(meta["health_score"]),
                    event_score=float(meta["event_score"]),
                    risk_score=float(meta["risk_score"]),
                    confirmation_status=self._normalize_confirmation_status(meta["confirmation_status"]),
                    event_grade=str(meta["event_grade"]),
                    phase_context_score=float(meta["phase_context_score"]),
                    event_recency_score=float(meta["event_recency_score"]),
                    delay_entry_days=max(1, int(payload.entry_delay_days)),
                    delay_window_days=max(0, int(entry_index) - int(signal_index) - 1),
                    final_rank_score=float(meta["final_rank_score"]),
                    entry_price=entry_price,
                    exit_price=float(exit_price),
                    holding_days=max(0, exit_index - entry_index + 1),
                    exit_reason=exit_reason,
                    entry_index=int(entry_index),
                    exit_legs=list(exit_legs),
                )
            )

            if allow_reentry_after_skipped:
                cursor += 1
            else:
                while cursor < len(in_range_indexes) and in_range_indexes[cursor] <= exit_index:
                    cursor += 1

        return out, t1_no_sellable_skips, delay_skip_reasons, plan_candidates

    @staticmethod
    def _matrix_intent_sort_key(
        row: MatrixEntryIntent,
        *,
        priority_mode: str,
    ) -> tuple[Any, ...]:
        if priority_mode == "phase_first":
            return (
                row.entry_date,
                -row.entry_phase_score,
                -row.final_rank_score,
                -row.entry_quality_score,
                -row.entry_events_weight,
                -row.entry_structure_score,
                row.symbol,
                row.signal_date,
            )
        if priority_mode == "momentum":
            return (
                row.entry_date,
                -row.entry_trend_score,
                -row.final_rank_score,
                -row.entry_quality_score,
                -row.entry_events_weight,
                -row.entry_structure_score,
                row.symbol,
                row.signal_date,
            )
        return (
            row.entry_date,
            -row.final_rank_score,
            -row.entry_quality_score,
            -row.entry_phase_score,
            -row.entry_events_weight,
            -row.entry_structure_score,
            row.symbol,
            row.signal_date,
        )

    def _build_matrix_entry_intents(
        self,
        *,
        payload: BacktestRunRequest,
        symbols: list[str],
        start_date: str,
        end_date: str,
        matrix_bundle: MatrixBundle,
        matrix_signals: BacktestSignalMatrix,
        allowed_symbols_by_date: dict[str, set[str]] | None = None,
        control_callback: Callable[[], None] | None = None,
    ) -> tuple[list[MatrixEntryIntent], dict[str, int]]:
        dates = list(matrix_bundle.dates)
        if not dates:
            return [], self._build_delay_skip_counter()

        total_shape = (len(dates), len(matrix_bundle.symbols))
        if (
            matrix_bundle.open.shape != total_shape
            or matrix_bundle.valid_mask.shape != total_shape
            or matrix_signals.buy_signal.shape != total_shape
            or matrix_signals.score.shape != total_shape
        ):
            raise ValueError("matrix bundle / signals shape mismatch")

        in_range_mask = np.fromiter(
            (start_date <= day <= end_date for day in dates),
            dtype=bool,
            count=len(dates),
        )
        if int(np.count_nonzero(in_range_mask)) < 2:
            return [], self._build_delay_skip_counter()

        symbol_to_col = matrix_bundle.symbol_to_index()
        in_range_valid_buy = matrix_signals.buy_signal & matrix_bundle.valid_mask & in_range_mask[:, np.newaxis]
        buy_any_by_col = np.any(in_range_valid_buy, axis=0)
        out: list[MatrixEntryIntent] = []
        delay_skip_reasons = self._build_delay_skip_counter()

        for raw_symbol in symbols:
            if control_callback is not None:
                control_callback()
            symbol = str(raw_symbol).strip().lower()
            col = symbol_to_col.get(symbol)
            if col is None:
                continue
            if not bool(buy_any_by_col[col]):
                continue

            open_col = matrix_bundle.open[:, col]
            valid_col = matrix_bundle.valid_mask[:, col]
            score_col = matrix_signals.score[:, col]
            sell_col = matrix_signals.sell_signal[:, col]
            buy_indexes = np.flatnonzero(in_range_valid_buy[:, col])
            if buy_indexes.size <= 0:
                continue
            if allowed_symbols_by_date is not None:
                filtered_indexes = [
                    int(idx)
                    for idx in buy_indexes.tolist()
                    if symbol in allowed_symbols_by_date.get(dates[int(idx)], set())
                ]
                if not filtered_indexes:
                    continue
                buy_indexes = np.asarray(filtered_indexes, dtype=np.int64)

            for signal_index in buy_indexes.tolist():
                if control_callback is not None:
                    control_callback()
                entry_index = self._resolve_entry_index(signal_index, payload)
                if entry_index >= len(dates):
                    delay_skip_reasons[DELAY_SKIP_REASON_NO_ENTRY_DAY] += 1
                    continue
                if not bool(valid_col[entry_index]):
                    continue
                if payload.delay_invalidation_enabled:
                    if payload.matrix_event_semantic_version == MATRIX_SEMANTIC_ALIGNED:
                        delay_reason = self._resolve_delay_invalidation_reason_matrix_aligned(
                            symbol=symbol,
                            signal_index=int(signal_index),
                            entry_index=int(entry_index),
                            dates=dates,
                            payload=payload,
                        )
                    else:
                        delay_reason = self._resolve_delay_invalidation_reason_matrix(
                            signal_index=int(signal_index),
                            entry_index=int(entry_index),
                            valid_col=valid_col,
                            sell_col=sell_col,
                        )
                    if delay_reason:
                        delay_skip_reasons[delay_reason] = delay_skip_reasons.get(delay_reason, 0) + 1
                        continue

                entry_price = (
                    float(close_col[entry_index])
                    if self._uses_same_day_close_entry(payload)
                    else float(open_col[entry_index])
                )
                if (not math.isfinite(entry_price)) or entry_price <= 0:
                    continue

                entry_tags: list[str] = []
                if bool(matrix_signals.s5[signal_index, col]):
                    entry_tags.append("SOS")
                if bool(matrix_signals.s6[signal_index, col]):
                    entry_tags.append("LPS")
                if not entry_tags:
                    entry_tags.append(payload.entry_events[0] if payload.entry_events else "ENTRY")
                entry_phase = "鍚哥D" if bool(matrix_signals.s7[signal_index, col]) else "闃舵鏈槑"
                raw_quality = float(score_col[signal_index]) if math.isfinite(float(score_col[signal_index])) else 0.0
                entry_quality_score = max(0.0, min(100.0, raw_quality))
                candle_quality_score = entry_quality_score
                cost_center_shift_score = entry_quality_score
                weekly_context_score = entry_quality_score
                weekly_context_multiplier = 1.0
                entry_signal_text = " / ".join(entry_tags)
                entry_phase_score = float(PHASE_PRIORITY_SCORE.get(entry_phase, 0.0))
                entry_events_weight = float(len(entry_tags))
                entry_structure_score = int(bool(matrix_signals.in_pool[signal_index, col]))
                entry_trend_score = entry_quality_score
                entry_volatility_score = entry_quality_score
                health_score = entry_quality_score
                event_score = entry_quality_score
                risk_score = 0.0
                confirmation_status = "unconfirmed"
                event_grade = "C"
                phase_context_score = 0.0
                event_recency_score = 0.0

                if payload.matrix_event_semantic_version == MATRIX_SEMANTIC_ALIGNED:
                    semantic_meta = self._build_matrix_semantic_meta(
                        symbol=symbol,
                        signal_date=dates[signal_index],
                        payload=payload,
                    )
                    if semantic_meta is None:
                        continue
                    entry_signal_text = str(semantic_meta["entry_signal"])
                    entry_phase = str(semantic_meta["entry_phase"])
                    entry_quality_score = float(semantic_meta["entry_quality_score"])
                    candle_quality_score = float(semantic_meta.get("candle_quality_score", 0.0) or 0.0)
                    cost_center_shift_score = float(semantic_meta.get("cost_center_shift_score", 0.0) or 0.0)
                    weekly_context_score = float(semantic_meta.get("weekly_context_score", 50.0) or 50.0)
                    weekly_context_multiplier = float(semantic_meta.get("weekly_context_multiplier", 1.0) or 1.0)
                    entry_phase_score = float(semantic_meta["entry_phase_score"])
                    entry_events_weight = float(semantic_meta["entry_events_weight"])
                    entry_structure_score = int(semantic_meta["entry_structure_score"])
                    entry_trend_score = float(semantic_meta["entry_trend_score"])
                    entry_volatility_score = float(semantic_meta["entry_volatility_score"])
                    health_score = float(semantic_meta["health_score"])
                    event_score = float(semantic_meta["event_score"])
                    risk_score = float(semantic_meta.get("risk_score", 0.0) or 0.0)
                    confirmation_status = self._normalize_confirmation_status(
                        semantic_meta.get("confirmation_status", "unconfirmed")
                    )
                    event_grade = self._normalize_event_grade(semantic_meta["event_grade"])
                    phase_context_score = float(semantic_meta.get("phase_context_score", 0.0) or 0.0)
                    event_recency_score = float(semantic_meta.get("event_recency_score", 0.0) or 0.0)
                elif not self._passes_semantic_score_gates(
                    payload=payload,
                    health_score=health_score,
                    event_score=event_score,
                    event_grade=event_grade,
                    confirmation_status=confirmation_status,
                ):
                    continue

                final_rank_score = self._compute_final_rank_score(
                    payload=payload,
                    health_score=health_score,
                    event_score=event_score,
                )

                out.append(
                    MatrixEntryIntent(
                        symbol=symbol,
                        signal_index=int(signal_index),
                        entry_index=entry_index,
                        signal_date=dates[signal_index],
                        entry_date=dates[entry_index],
                        entry_signal=entry_signal_text,
                        entry_phase=entry_phase,
                        entry_quality_score=entry_quality_score,
                        candle_quality_score=max(0.0, min(100.0, candle_quality_score)),
                        cost_center_shift_score=max(0.0, min(100.0, cost_center_shift_score)),
                        weekly_context_score=max(0.0, min(100.0, weekly_context_score)),
                        weekly_context_multiplier=max(0.85, min(1.15, weekly_context_multiplier)),
                        entry_phase_score=entry_phase_score,
                        entry_events_weight=entry_events_weight,
                        entry_structure_score=entry_structure_score,
                        entry_trend_score=entry_trend_score,
                        entry_volatility_score=entry_volatility_score,
                        health_score=health_score,
                        event_score=event_score,
                        risk_score=risk_score,
                        confirmation_status=confirmation_status,
                        event_grade=event_grade,
                        phase_context_score=phase_context_score,
                        event_recency_score=event_recency_score,
                        delay_entry_days=max(1, int(payload.entry_delay_days)),
                        delay_window_days=max(0, int(entry_index) - int(signal_index) - 1),
                        final_rank_score=final_rank_score,
                        entry_price=entry_price,
                    )
                )
        return out, delay_skip_reasons

    def _build_trades_from_exit_legs(
        self,
        *,
        legs: list[ExitLeg],
        symbol: str,
        signal_date: str,
        entry_date: str,
        entry_index: int,
        entry_price: float,
        entry_exec: float,
        fee_rate: float,
        trade_meta: dict[str, Any],
    ) -> tuple[list[BacktestTrade], list[dict[str, float | str]], str, str, bool, int]:
        remaining_shares = int(trade_meta["total_shares"])
        cash_events: list[dict[str, float | str]] = []
        executed_rows: list[BacktestTrade] = []
        final_exit_date = entry_date
        final_exit_reason = "open"
        slot_still_open = False

        for leg in legs:
            exit_price = float(leg.exit_price)
            if not math.isfinite(exit_price) or exit_price <= 0:
                return executed_rows, cash_events, final_exit_date, final_exit_reason, slot_still_open, remaining_shares
            exit_exec = exit_price * (1 - fee_rate)
            if (not math.isfinite(exit_exec)) or exit_exec <= 0:
                return executed_rows, cash_events, final_exit_date, final_exit_reason, slot_still_open, remaining_shares

            exit_date = leg.exit_date or entry_date
            holding_days = max(0, int(leg.exit_index) - int(entry_index) + 1)
            final_exit_date = exit_date
            final_exit_reason = leg.exit_reason
            if self._is_backtest_open_holding(leg.exit_reason):
                slot_still_open = True
                if remaining_shares > 0:
                    holding_days = max(0, int(leg.exit_index) - int(entry_index) + 1)
                    executed_rows.append(
                        BacktestTrade(
                            symbol=symbol,
                            name=self._resolve_symbol_name(symbol),
                            signal_date=signal_date,
                            entry_date=entry_date,
                            exit_date=exit_date,
                            entry_signal=str(trade_meta["entry_signal"]),
                            entry_phase=str(trade_meta["entry_phase"]),
                            entry_quality_score=round(float(trade_meta["entry_quality_score"]), 2),
                            candle_quality_score=round(float(trade_meta["candle_quality_score"]), 2),
                            cost_center_shift_score=round(float(trade_meta["cost_center_shift_score"]), 2),
                            weekly_context_score=round(float(trade_meta["weekly_context_score"]), 2),
                            weekly_context_multiplier=round(float(trade_meta["weekly_context_multiplier"]), 4),
                            health_score=round(float(trade_meta["health_score"]), 2),
                            event_score=round(float(trade_meta["event_score"]), 2),
                            risk_score=round(float(trade_meta["risk_score"]), 2),
                            confirmation_status=self._normalize_confirmation_status(str(trade_meta["confirmation_status"])),
                            event_grade=self._normalize_event_grade(trade_meta["event_grade"]),  # type: ignore[arg-type]
                            phase_context_score=round(float(trade_meta["phase_context_score"]), 2),
                            event_recency_score=round(float(trade_meta["event_recency_score"]), 2),
                            exit_reason=leg.exit_reason,
                            delay_entry_days=max(1, int(trade_meta["delay_entry_days"])),
                            delay_window_days=max(0, int(trade_meta["delay_window_days"])),
                            quantity=remaining_shares,
                            entry_price=round(entry_price, 4),
                            exit_price=round(exit_price, 4),
                            holding_days=holding_days,
                            pnl_amount=0.0,
                            pnl_ratio=0.0,
                        )
                    )
                break

            if self._is_intraday_reduce_only_exit(leg.exit_reason):
                reduce_ratio = self._parse_intraday_reduce_ratio(leg.exit_reason)
                sell_shares = self._compute_reduce_sell_shares(remaining_shares, reduce_ratio)
            else:
                sell_shares = remaining_shares
            if sell_shares <= 0:
                continue

            leg_invested = sell_shares * entry_exec
            exit_amount = sell_shares * exit_exec
            pnl_amount = exit_amount - leg_invested
            pnl_ratio = pnl_amount / leg_invested if leg_invested > 0 else 0.0
            remaining_shares -= sell_shares
            cash_events.append(
                {
                    "exit_date": exit_date,
                    "execution_phase": leg.execution_phase,
                    "exit_amount": float(exit_amount),
                    "pnl_amount": float(pnl_amount),
                }
            )
            executed_rows.append(
                BacktestTrade(
                    symbol=symbol,
                    name=self._resolve_symbol_name(symbol),
                    signal_date=signal_date,
                    entry_date=entry_date,
                    exit_date=exit_date,
                    entry_signal=str(trade_meta["entry_signal"]),
                    entry_phase=str(trade_meta["entry_phase"]),
                    entry_quality_score=round(float(trade_meta["entry_quality_score"]), 2),
                    candle_quality_score=round(float(trade_meta["candle_quality_score"]), 2),
                    cost_center_shift_score=round(float(trade_meta["cost_center_shift_score"]), 2),
                    weekly_context_score=round(float(trade_meta["weekly_context_score"]), 2),
                    weekly_context_multiplier=round(float(trade_meta["weekly_context_multiplier"]), 4),
                    health_score=round(float(trade_meta["health_score"]), 2),
                    event_score=round(float(trade_meta["event_score"]), 2),
                    risk_score=round(float(trade_meta["risk_score"]), 2),
                    confirmation_status=self._normalize_confirmation_status(str(trade_meta["confirmation_status"])),
                    event_grade=self._normalize_event_grade(trade_meta["event_grade"]),  # type: ignore[arg-type]
                    phase_context_score=round(float(trade_meta["phase_context_score"]), 2),
                    event_recency_score=round(float(trade_meta["event_recency_score"]), 2),
                    exit_reason=leg.exit_reason,
                    delay_entry_days=max(1, int(trade_meta["delay_entry_days"])),
                    delay_window_days=max(0, int(trade_meta["delay_window_days"])),
                    quantity=sell_shares,
                    entry_price=round(entry_price, 4),
                    exit_price=round(exit_price, 4),
                    holding_days=holding_days,
                    pnl_amount=round(pnl_amount, 4),
                    pnl_ratio=round(pnl_ratio, 6),
                )
            )
            if remaining_shares <= 0:
                break

        return executed_rows, cash_events, final_exit_date, final_exit_reason, slot_still_open, remaining_shares

    @staticmethod
    def _cash_available(exit_date: str, execution_phase: str, entry_date: str, entry_phase: str = "open") -> bool:
        # Daily bars cannot establish which intraday trade happened first.
        # Only an opening execution can finance another opening entry that day.
        return bool(exit_date) and (exit_date < entry_date or (exit_date == entry_date and (execution_phase == "open" or (entry_phase == "close" and execution_phase == "intraday"))))

    def _execute_candidates_portfolio(
        self,
        *,
        candidates: list[CandidateTrade],
        payload: BacktestRunRequest,
        fee_rate: float,
        control_callback: Callable[[], None] | None = None,
    ) -> tuple[list[BacktestTrade], int, dict[str, int]]:
        cash = float(payload.initial_capital)
        equity = float(payload.initial_capital)
        max_concurrent_positions = 0
        active_positions: list[dict[str, float | str | list[dict[str, float | str]]]] = []
        executed: list[BacktestTrade] = []
        skip_reasons: dict[str, int] = {
            "max_positions": 0,
            "insufficient_cash": 0,
            "invalid_price": 0,
            "duplicate_symbol": 0,
            "intraday_reduce_block": 0,
        }
        active_symbols: set[str] = set()

        def release_until(current_entry_date: str) -> None:
            nonlocal cash, equity, active_positions, active_symbols
            if not active_positions:
                return
            remaining_positions: list[dict[str, float | str | list[dict[str, float | str]]]] = []
            for item in active_positions:
                cash_events = item.get("cash_events")
                if isinstance(cash_events, list):
                    pending_events: list[dict[str, float | str]] = []
                    for event in cash_events:
                        if not isinstance(event, dict):
                            continue
                        event_date = str(event.get("exit_date", ""))
                        if event_date and BacktestEngine._cash_available(event_date, str(event.get("execution_phase", "intraday")), current_entry_date, "close" if payload.strategy_id in SAME_DAY_CLOSE_ENTRY_STRATEGY_IDS else "open"):
                            cash += float(event.get("exit_amount", 0.0))
                            equity += float(event.get("pnl_amount", 0.0))
                        else:
                            pending_events.append(event)
                    item["cash_events"] = pending_events

                if self._is_backtest_open_holding(str(item.get("exit_reason", ""))):
                    remaining_positions.append(item)
                    continue

                exit_date = str(item.get("exit_date", ""))
                pending_events = item.get("cash_events")
                has_pending = isinstance(pending_events, list) and len(pending_events) > 0
                if has_pending:
                    remaining_positions.append(item)
                    continue
                if BacktestEngine._cash_available(exit_date, str(item.get("execution_phase", "intraday")), current_entry_date, "close" if payload.strategy_id in SAME_DAY_CLOSE_ENTRY_STRATEGY_IDS else "open"):
                    cash += float(item.get("exit_amount", 0.0))
                    equity += float(item.get("pnl_amount", 0.0))
                else:
                    remaining_positions.append(item)
            active_positions = remaining_positions
            active_symbols = self._active_position_symbols(active_positions)

        for row in candidates:
            if control_callback is not None:
                control_callback()
            symbol_key = str(row.symbol).strip().lower()
            if self._entry_blocked_by_pending_intraday_reduce(active_positions, symbol_key, row.entry_date):
                skip_reasons["intraday_reduce_block"] += 1
                continue
            release_until(row.entry_date)

            if symbol_key in active_symbols:
                skip_reasons["duplicate_symbol"] += 1
                continue
            if self._count_active_position_slots(active_positions) >= payload.max_positions:
                skip_reasons["max_positions"] += 1
                continue

            entry_exec = float(row.entry_price) * (1 + fee_rate)
            if not math.isfinite(entry_exec) or entry_exec <= 0:
                skip_reasons["invalid_price"] += 1
                continue

            allocation = min(cash, max(0.0, equity * payload.position_pct))
            shares = int(math.floor(allocation / entry_exec / 100.0)) * 100
            if shares <= 0:
                skip_reasons["insufficient_cash"] += 1
                continue

            invested = shares * entry_exec
            if invested <= 0 or invested > cash + 1e-9:
                skip_reasons["insufficient_cash"] += 1
                continue

            cash -= invested
            trade_meta = {
                "total_shares": shares,
                "entry_signal": row.entry_signal,
                "entry_phase": row.entry_phase,
                "entry_quality_score": row.entry_quality_score,
                "candle_quality_score": row.candle_quality_score,
                "cost_center_shift_score": row.cost_center_shift_score,
                "weekly_context_score": row.weekly_context_score,
                "weekly_context_multiplier": row.weekly_context_multiplier,
                "health_score": row.health_score,
                "event_score": row.event_score,
                "risk_score": row.risk_score,
                "confirmation_status": row.confirmation_status,
                "event_grade": row.event_grade,
                "phase_context_score": row.phase_context_score,
                "event_recency_score": row.event_recency_score,
                "delay_entry_days": row.delay_entry_days,
                "delay_window_days": row.delay_window_days,
            }
            if row.exit_legs:
                leg_trades, cash_events, final_exit_date, final_exit_reason, slot_still_open, remaining_shares = (
                    self._build_trades_from_exit_legs(
                        legs=row.exit_legs,
                        symbol=row.symbol,
                        signal_date=row.signal_date,
                        entry_date=row.entry_date,
                        entry_index=int(row.entry_index),
                        entry_price=float(row.entry_price),
                        entry_exec=entry_exec,
                        fee_rate=fee_rate,
                        trade_meta=trade_meta,
                    )
                )
                if remaining_shares <= 0 and not slot_still_open and not leg_trades:
                    cash += invested
                    skip_reasons["invalid_price"] += 1
                    continue
                executed.extend(leg_trades)
                active_positions.append(
                    {
                        "symbol": row.symbol,
                        "exit_date": final_exit_date,
                        "exit_amount": 0.0,
                        "pnl_amount": 0.0,
                        "exit_reason": final_exit_reason,
                        "cash_events": cash_events,
                        "execution_phase": row.exit_legs[-1].execution_phase,
                    }
                )
            else:
                exit_exec = float(row.exit_price) * (1 - fee_rate)
                if not math.isfinite(exit_exec) or exit_exec <= 0:
                    cash += invested
                    skip_reasons["invalid_price"] += 1
                    continue
                exit_amount = shares * exit_exec
                is_open_holding = self._is_backtest_open_holding(row.exit_reason)
                pnl_amount = 0.0 if is_open_holding else (exit_amount - invested)
                pnl_ratio = 0.0 if is_open_holding else (pnl_amount / invested if invested > 0 else 0.0)
                active_positions.append(
                    {
                        "symbol": row.symbol,
                        "exit_date": row.exit_date,
                        "exit_amount": float(exit_amount),
                        "pnl_amount": float(pnl_amount),
                        "exit_reason": row.exit_reason,
                        "execution_phase": row.exit_execution_phase,
                    }
                )
                executed.append(
                    BacktestTrade(
                        symbol=row.symbol,
                        name=self._resolve_symbol_name(row.symbol),
                        signal_date=row.signal_date,
                        entry_date=row.entry_date,
                        exit_date=row.exit_date,
                        entry_signal=row.entry_signal,
                        entry_phase=row.entry_phase,
                        entry_quality_score=round(row.entry_quality_score, 2),
                        candle_quality_score=round(row.candle_quality_score, 2),
                        cost_center_shift_score=round(row.cost_center_shift_score, 2),
                        weekly_context_score=round(row.weekly_context_score, 2),
                        weekly_context_multiplier=round(row.weekly_context_multiplier, 4),
                        health_score=round(row.health_score, 2),
                        event_score=round(row.event_score, 2),
                        risk_score=round(row.risk_score, 2),
                        confirmation_status=self._normalize_confirmation_status(row.confirmation_status),
                        event_grade=self._normalize_event_grade(row.event_grade),  # type: ignore[arg-type]
                        phase_context_score=round(row.phase_context_score, 2),
                        event_recency_score=round(row.event_recency_score, 2),
                        exit_reason=row.exit_reason,
                        delay_entry_days=max(1, int(row.delay_entry_days)),
                        delay_window_days=max(0, int(row.delay_window_days)),
                        quantity=shares,
                        entry_price=round(row.entry_price, 4),
                        exit_price=round(row.exit_price, 4),
                        holding_days=row.holding_days,
                        pnl_amount=round(pnl_amount, 4),
                        pnl_ratio=round(pnl_ratio, 6),
                    )
                )
            if symbol_key:
                active_symbols.add(symbol_key)
            max_concurrent_positions = max(
                max_concurrent_positions,
                self._count_active_position_slots(active_positions),
            )

        if active_positions:
            for item in sorted(active_positions, key=lambda row: str(row.get("exit_date", ""))):
                if self._is_backtest_open_holding(str(item.get("exit_reason", ""))):
                    continue
                cash_events = item.get("cash_events")
                if isinstance(cash_events, list):
                    for event in cash_events:
                        if isinstance(event, dict):
                            cash += float(event.get("exit_amount", 0.0))
                            equity += float(event.get("pnl_amount", 0.0))
                cash += float(item.get("exit_amount", 0.0))
                equity += float(item.get("pnl_amount", 0.0))

        return executed, max_concurrent_positions, skip_reasons

    def _execute_matrix_position_intents(
        self,
        *,
        payload: BacktestRunRequest,
        intents: list[MatrixEntryIntent],
        matrix_bundle: MatrixBundle,
        matrix_signals: BacktestSignalMatrix,
        control_callback: Callable[[], None] | None = None,
        end_date: str | None = None,
    ) -> tuple[list[BacktestTrade], int, dict[str, int], int]:
        intent_fee_rate = max(0.0, float(payload.fee_bps)) / 10000.0
        dates = list(matrix_bundle.dates)
        symbol_to_col = matrix_bundle.symbol_to_index()
        max_exit_bar_index = len(dates) - 1
        if end_date:
            for i in range(len(dates) - 1, -1, -1):
                if dates[i] <= end_date:
                    max_exit_bar_index = i
                    break

        cash = float(payload.initial_capital)
        equity = float(payload.initial_capital)
        max_concurrent_positions = 0
        active_positions: list[dict[str, float | str]] = []
        active_symbols: set[str] = set()
        executed: list[BacktestTrade] = []
        t1_no_sellable_skips = 0
        skip_reasons: dict[str, int] = {
            "max_positions": 0,
            "insufficient_cash": 0,
            "invalid_price": 0,
            "duplicate_symbol": 0,
            "intraday_reduce_block": 0,
        }

        def release_until(current_entry_date: str) -> None:
            nonlocal cash, equity, active_positions, active_symbols
            if not active_positions:
                return
            remaining_positions: list[dict[str, float | str | list[dict[str, float | str]]]] = []
            for item in active_positions:
                cash_events = item.get("cash_events")
                if isinstance(cash_events, list):
                    pending_events: list[dict[str, float | str]] = []
                    for event in cash_events:
                        if not isinstance(event, dict):
                            continue
                        event_date = str(event.get("exit_date", ""))
                        if event_date and BacktestEngine._cash_available(event_date, str(event.get("execution_phase", "intraday")), current_entry_date, "close" if payload.strategy_id in SAME_DAY_CLOSE_ENTRY_STRATEGY_IDS else "open"):
                            cash += float(event.get("exit_amount", 0.0))
                            equity += float(event.get("pnl_amount", 0.0))
                        else:
                            pending_events.append(event)
                    item["cash_events"] = pending_events

                if self._is_backtest_open_holding(str(item.get("exit_reason", ""))):
                    remaining_positions.append(item)
                    continue

                exit_date = str(item.get("exit_date", ""))
                pending_events = item.get("cash_events")
                has_pending = isinstance(pending_events, list) and len(pending_events) > 0
                if has_pending:
                    remaining_positions.append(item)
                    continue
                if BacktestEngine._cash_available(exit_date, str(item.get("execution_phase", "intraday")), current_entry_date, "close" if payload.strategy_id in SAME_DAY_CLOSE_ENTRY_STRATEGY_IDS else "open"):
                    cash += float(item.get("exit_amount", 0.0))
                    equity += float(item.get("pnl_amount", 0.0))
                else:
                    remaining_positions.append(item)
            active_positions = remaining_positions  # type: ignore[assignment]
            active_symbols = self._active_position_symbols(active_positions)

        for row in intents:
            if control_callback is not None:
                control_callback()
            symbol_key = str(row.symbol).strip().lower()
            if self._entry_blocked_by_pending_intraday_reduce(active_positions, symbol_key, row.entry_date):
                skip_reasons["intraday_reduce_block"] += 1
                continue
            release_until(row.entry_date)

            if symbol_key in active_symbols:
                skip_reasons["duplicate_symbol"] += 1
                continue
            if self._count_active_position_slots(active_positions) >= payload.max_positions:
                skip_reasons["max_positions"] += 1
                continue

            entry_exec = float(row.entry_price) * (1 + intent_fee_rate)
            if (not math.isfinite(entry_exec)) or entry_exec <= 0:
                skip_reasons["invalid_price"] += 1
                continue

            allocation = min(cash, max(0.0, equity * payload.position_pct))
            shares = int(math.floor(allocation / entry_exec / 100.0)) * 100
            if shares <= 0:
                skip_reasons["insufficient_cash"] += 1
                continue
            invested = shares * entry_exec
            if invested <= 0 or invested > cash + 1e-9:
                skip_reasons["insufficient_cash"] += 1
                continue

            col = symbol_to_col.get(row.symbol)
            if col is None:
                skip_reasons["invalid_price"] += 1
                continue

            exit_legs = BacktestEngine._resolve_exit_matrix_legs(
                entry_index=row.entry_index,
                entry_price=float(row.entry_price),
                open_col=matrix_bundle.open[:, col],
                high_col=matrix_bundle.high[:, col],
                low_col=matrix_bundle.low[:, col],
                close_col=matrix_bundle.close[:, col],
                valid_col=matrix_bundle.valid_mask[:, col],
                sell_col=matrix_signals.sell_signal[:, col],
                sell_label_col=matrix_signals.sell_signal_label[:, col] if matrix_signals.sell_signal_label is not None else None,
                payload=payload,
                max_bar_index=max_exit_bar_index,
                dates=dates,
            )
            if not exit_legs:
                t1_no_sellable_skips += 1
                continue

            cash -= invested
            trade_meta = {
                "total_shares": shares,
                "entry_signal": row.entry_signal,
                "entry_phase": row.entry_phase,
                "entry_quality_score": row.entry_quality_score,
                "candle_quality_score": row.candle_quality_score,
                "cost_center_shift_score": row.cost_center_shift_score,
                "weekly_context_score": row.weekly_context_score,
                "weekly_context_multiplier": row.weekly_context_multiplier,
                "health_score": row.health_score,
                "event_score": row.event_score,
                "risk_score": row.risk_score,
                "confirmation_status": row.confirmation_status,
                "event_grade": row.event_grade,
                "phase_context_score": row.phase_context_score,
                "event_recency_score": row.event_recency_score,
                "delay_entry_days": row.delay_entry_days,
                "delay_window_days": row.delay_window_days,
            }
            leg_trades, cash_events, final_exit_date, final_exit_reason, slot_still_open, remaining_shares = (
                self._build_trades_from_exit_legs(
                    legs=exit_legs,
                    symbol=row.symbol,
                    signal_date=row.signal_date,
                    entry_date=row.entry_date,
                    entry_index=int(row.entry_index),
                    entry_price=float(row.entry_price),
                    entry_exec=entry_exec,
                    fee_rate=intent_fee_rate,
                    trade_meta=trade_meta,
                )
            )
            if remaining_shares <= 0 and not slot_still_open and not leg_trades:
                cash += invested
                skip_reasons["invalid_price"] += 1
                continue

            executed.extend(leg_trades)
            active_positions.append(
                {
                    "symbol": row.symbol,
                    "exit_date": final_exit_date,
                    "exit_amount": 0.0,
                    "pnl_amount": 0.0,
                    "exit_reason": final_exit_reason,
                    "cash_events": cash_events,
                    "execution_phase": exit_legs[-1].execution_phase,
                }
            )
            if symbol_key:
                active_symbols.add(symbol_key)
            max_concurrent_positions = max(
                max_concurrent_positions,
                self._count_active_position_slots(active_positions),
            )

        if active_positions:
            for item in sorted(active_positions, key=lambda row: str(row.get("exit_date", ""))):
                if self._is_backtest_open_holding(str(item.get("exit_reason", ""))):
                    continue
                cash_events = item.get("cash_events")
                if isinstance(cash_events, list):
                    for event in cash_events:
                        if isinstance(event, dict):
                            cash += float(event.get("exit_amount", 0.0))
                            equity += float(event.get("pnl_amount", 0.0))
                cash += float(item.get("exit_amount", 0.0))
                equity += float(item.get("pnl_amount", 0.0))

        return executed, max_concurrent_positions, skip_reasons, t1_no_sellable_skips

    def run(
        self,
        *,
        payload: BacktestRunRequest,
        symbols: list[str],
        allowed_symbols_by_date: dict[str, set[str]] | None = None,
        matrix_bundle: MatrixBundle | None = None,
        matrix_signals: BacktestSignalMatrix | None = None,
        build_equity_curve: bool = True,
        control_callback: Callable[[], None] | None = None,
    ) -> BacktestResponse:
        if control_callback is not None:
            control_callback()
        start_dt = self._parse_date(payload.date_from)
        end_dt = self._parse_date(payload.date_to)
        if start_dt is None or end_dt is None:
            raise ValueError("date_from/date_to 蹇呴』鏄?YYYY-MM-DD")
        if start_dt > end_dt:
            start_dt, end_dt = end_dt, start_dt
        start_date = start_dt.strftime("%Y-%m-%d")
        end_date = end_dt.strftime("%Y-%m-%d")
        allow_reentry_after_skipped = payload.pool_roll_mode == "position"
        use_matrix_position_intents = (
            allow_reentry_after_skipped
            and matrix_bundle is not None
            and matrix_signals is not None
        )

        notes: list[str] = []
        intents: list[MatrixEntryIntent] = []
        plan_candidates: list[CandidateTrade] = []
        delay_skip_reasons = self._build_delay_skip_counter()
        candidate_stage_start = time.perf_counter()
        if matrix_bundle is not None and matrix_signals is not None:
            plan_candidates = self._build_plan_candidates_from_matrix(
                payload=payload,
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
                matrix_bundle=matrix_bundle,
                matrix_signals=matrix_signals,
                allowed_symbols_by_date=allowed_symbols_by_date,
                control_callback=control_callback,
            )
            if use_matrix_position_intents:
                candidates = []
                total_t1_skips = 0
                intents, delay_skips_intents = self._build_matrix_entry_intents(
                    payload=payload,
                    symbols=symbols,
                    start_date=start_date,
                    end_date=end_date,
                    matrix_bundle=matrix_bundle,
                    matrix_signals=matrix_signals,
                    allowed_symbols_by_date=allowed_symbols_by_date,
                    control_callback=control_callback,
                )
                for key, value in delay_skips_intents.items():
                    if value > 0:
                        delay_skip_reasons[key] = delay_skip_reasons.get(key, 0) + int(value)
            else:
                candidates, total_t1_skips, delay_skips_matrix = self._build_candidates_from_matrix(
                    payload=payload,
                    symbols=symbols,
                    start_date=start_date,
                    end_date=end_date,
                    matrix_bundle=matrix_bundle,
                    matrix_signals=matrix_signals,
                    allowed_symbols_by_date=allowed_symbols_by_date,
                    allow_reentry_after_skipped=allow_reentry_after_skipped,
                    control_callback=control_callback,
                )
                for key, value in delay_skips_matrix.items():
                    if value > 0:
                        delay_skip_reasons[key] = delay_skip_reasons.get(key, 0) + int(value)
            notes.append("矩阵信号引擎：使用 (T,N) 信号切片路径，跳过逐股逐日 snapshot 重算。")
        else:
            candidates = []
            total_t1_skips = 0
            for symbol in symbols:
                if control_callback is not None:
                    control_callback()
                rows, t1_skips, delay_skips_legacy, plan_rows = self._build_candidates_for_symbol(
                    symbol,
                    payload,
                    start_date,
                    end_date,
                    allowed_symbols_by_date=allowed_symbols_by_date,
                    allow_reentry_after_skipped=allow_reentry_after_skipped,
                    control_callback=control_callback,
                )
                candidates.extend(rows)
                plan_candidates.extend(plan_rows)
                total_t1_skips += t1_skips
                for key, value in delay_skips_legacy.items():
                    if value > 0:
                        delay_skip_reasons[key] = delay_skip_reasons.get(key, 0) + int(value)
        if matrix_bundle is not None and matrix_signals is not None:
            notes.append(f"执行路径: matrix (semantic={payload.matrix_event_semantic_version})")
        else:
            notes.append("执行路径: legacy")
        if payload.entry_delay_days != 1:
            notes.append(f"延迟入场已启用：entry_delay_days={payload.entry_delay_days}（交易日）")
        if payload.delay_invalidation_enabled:
            notes.append("延迟窗口失效保护已启用。")
        delay_skipped_total = int(sum(delay_skip_reasons.values()))
        if delay_skipped_total > 0:
            detail = ", ".join(f"{key}:{value}" for key, value in delay_skip_reasons.items() if value > 0)
            notes.append(f"延迟窗口共跳过 {delay_skipped_total} 笔信号（{detail}）。")
        if total_t1_skips > 0:
            notes.append(f"T+1 约束导致 {total_t1_skips} 笔信号因样本内无可卖出日被跳过。")
        if allow_reentry_after_skipped:
            notes.append("持仓触发滚动：未成交信号不会阻断同标的后续信号。")
        if payload.health_score_min > 0 or payload.event_score_min > 0 or payload.event_grade_min != "C":
            notes.append(
                f"语义门控已启用：health_score_min={payload.health_score_min}, "
                f"event_score_min={payload.event_score_min}, event_grade_min={payload.event_grade_min}。"
            )

        if use_matrix_position_intents:
            if payload.prioritize_signals:
                intents.sort(key=lambda row: self._matrix_intent_sort_key(row, priority_mode=payload.priority_mode))
                w_health, w_event = self._resolve_rank_weights(payload)
                notes.append(
                    f"同日信号按优先级执行（mode={payload.priority_mode}, "
                    f"health={w_health:.2f}, event={w_event:.2f}）。"
                )
            else:
                intents.sort(key=lambda row: (row.entry_date, row.symbol, row.signal_date))
                if payload.priority_topk_per_day > 0:
                    notes.append("未启用优先级排序，priority_topk_per_day 不生效。")
        elif payload.prioritize_signals:
            candidates.sort(key=lambda row: self._candidate_sort_key(row, priority_mode=payload.priority_mode))
            w_health, w_event = self._resolve_rank_weights(payload)
            notes.append(
                f"同日信号按优先级执行（mode={payload.priority_mode}, "
                f"health={w_health:.2f}, event={w_event:.2f}）。"
            )
        else:
            candidates.sort(key=lambda row: (row.entry_date, row.symbol, row.exit_date))
            if payload.priority_topk_per_day > 0:
                notes.append("未启用优先级排序，priority_topk_per_day 不生效。")

        if use_matrix_position_intents and payload.prioritize_signals and payload.priority_topk_per_day > 0:
            before_count = len(intents)
            kept_intents: list[MatrixEntryIntent] = []
            day_counter: dict[str, int] = defaultdict(int)
            for row in intents:
                if day_counter[row.signal_date] >= payload.priority_topk_per_day:
                    continue
                kept_intents.append(row)
                day_counter[row.signal_date] += 1
            intents = kept_intents
            dropped = before_count - len(intents)
            if dropped > 0:
                notes.append(
                    f"同日 TopK 限流生效：每日保留前 {payload.priority_topk_per_day} 笔候选，过滤 {dropped} 笔。"
                )

        if (not use_matrix_position_intents) and payload.prioritize_signals and payload.priority_topk_per_day > 0:
            before_count = len(candidates)
            kept: list[CandidateTrade] = []
            day_counter: dict[str, int] = defaultdict(int)
            for row in candidates:
                if day_counter[row.signal_date] >= payload.priority_topk_per_day:
                    continue
                kept.append(row)
                day_counter[row.signal_date] += 1
            candidates = kept
            dropped = before_count - len(candidates)
            if dropped > 0:
                notes.append(
                    f"同日 TopK 限流生效：每日保留前 {payload.priority_topk_per_day} 笔候选，过滤 {dropped} 笔。"
                )

        candidate_count = len(intents) if use_matrix_position_intents else len(candidates)
        plan_signals = self._finalize_plan_signals(plan_candidates, payload=payload)
        candidate_stage_elapsed = time.perf_counter() - candidate_stage_start

        open_signals: list[BacktestTrade] = []
        if matrix_bundle is not None and matrix_signals is not None:
            open_signals = self._collect_last_day_open_signals_matrix(
                payload=payload,
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
                matrix_bundle=matrix_bundle,
                matrix_signals=matrix_signals,
                allowed_symbols_by_date=allowed_symbols_by_date,
            )
        else:
            open_signals = self._collect_last_day_open_signals_legacy(
                payload=payload,
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
                allowed_symbols_by_date=allowed_symbols_by_date,
            )
        if open_signals:
            notes.append(f"末日本入场信号: {len(open_signals)} 只（仅供参考，次日数据更新后可正常入场）。")
        else:
            diag = getattr(self, "_last_open_signal_diag", None)
            if diag:
                parts = [f"检查{diag['total']}只"]
                if diag["can_enter"] > 0:
                    parts.append(f"{diag['can_enter']}只可正常入场(数据充足)")
                if diag["no_signal"] > 0:
                    parts.append(f"{diag['no_signal']}只无信号")
                if diag["no_event"] > 0:
                    parts.append(f"{diag['no_event']}只无入场事件")
                if diag["low_score"] > 0:
                    parts.append(f"{diag['low_score']}只分数不足")
                if diag["gated"] > 0:
                    parts.append(f"{diag['gated']}只语义门控未通过")
                if diag["not_allowed"] > 0:
                    parts.append(f"{diag['not_allowed']}只不在候选池")
                notes.append(f"末日信号扫描: 无未入场信号（{', '.join(parts)}）")
            else:
                notes.append("末日信号扫描: 回测区间最后一日无符合条件且无法入场的买入信号。")

        fee_rate = max(0.0, float(payload.fee_bps)) / 10000.0
        if fee_rate > 0.01:
            notes.append("fee_bps 超过 100 时，cost_snapshot 仅展示截断后的 commission_rate=1%。")

        match_stage_start = time.perf_counter()
        if use_matrix_position_intents and matrix_bundle is not None and matrix_signals is not None:
            executed, max_concurrent_positions, skip_reasons, t1_skips_exec = self._execute_matrix_position_intents(
                payload=payload,
                intents=intents,
                matrix_bundle=matrix_bundle,
                matrix_signals=matrix_signals,
                control_callback=control_callback,
                end_date=end_date,
            )
            total_t1_skips += t1_skips_exec
            if t1_skips_exec > 0:
                notes.append(f"T+1 约束导致 {t1_skips_exec} 笔持仓候选因无可卖出日被跳过。")
        else:
            executed, max_concurrent_positions, skip_reasons = self._execute_candidates_portfolio(
                candidates=candidates,
                payload=payload,
                fee_rate=fee_rate,
                control_callback=control_callback,
            )

        for key, value in delay_skip_reasons.items():
            if value > 0:
                skip_reasons[key] = skip_reasons.get(key, 0) + int(value)

        # Apply position/cash constraints to open_signals, but treat next-day planned
        # sells as freeing slots (same rule as operation plans).
        next_trade_date = self._resolve_next_trade_date_from_candles(
            end_date=end_date,
            symbols=symbols,
            get_candles=self._get_candles,
        )
        available_slots, blocking_symbols = self._resolve_last_day_open_signal_slots(
            trades=executed,
            end_date=end_date,
            next_trade_date=next_trade_date,
            max_positions=payload.max_positions,
        )
        if open_signals:
            held_trades_end = self._resolve_last_day_held_trades(executed, as_of_date=end_date)
            held_symbols_end = set(self._group_held_trades_by_symbol(held_trades_end).keys())
            pending_sells_end = [
                trade
                for trade in held_trades_end
                if next_trade_date
                and trade.exit_date == next_trade_date
                and self._is_next_day_plan_sell(trade)
            ]
            reduce_blocked_end = self._symbols_with_pending_intraday_reduce(pending_sells_end)
            open_signals = self._filter_plan_buy_signals(
                open_signals,
                held_symbols=held_symbols_end,
                reduce_blocked_symbols=reduce_blocked_end,
                max_slots=available_slots,
            )
            open_signals = [
                signal
                for signal in open_signals
                if str(signal.symbol).strip().lower() not in blocking_symbols
            ]
            open_signals.sort(key=lambda t: -t.entry_quality_score)
        if open_signals:
            notes.append(f"末日可入场信号: {len(open_signals)} 只（经过持仓约束筛选）。")
        elif not any("末日" in n for n in notes):
            if available_slots <= 0 and blocking_symbols:
                notes.append(
                    f"末日信号扫描: 末日本地持仓已满（{len(blocking_symbols)}/{payload.max_positions}），"
                    "且无次日计划卖出释放仓位。"
                )
            else:
                notes.append("末日信号扫描: 无可入场信号。")

        execution_match_elapsed = time.perf_counter() - match_stage_start
        skipped_count = int(sum(skip_reasons.values()))
        fill_rate = (len(executed) / candidate_count) if candidate_count > 0 else 0.0
        if skipped_count > 0:
            detail = ", ".join(f"{key}:{value}" for key, value in skip_reasons.items() if value > 0)
            notes.append(f"组合约束跳过 {skipped_count} 笔信号（{detail}）。")

        open_holding_count = sum(1 for row in executed if self._is_backtest_open_holding(row.exit_reason))
        if open_holding_count > 0:
            executed = self._apply_end_date_mark_to_market(
                executed,
                end_date=end_date,
                fee_rate=fee_rate,
            )
            notes.append(
                f"末日本持仓盯市: {open_holding_count} 笔未卖出持仓已按 {end_date} 收盘价计入收益。"
            )

        trade_count = len(executed)
        win_count = sum(1 for row in executed if row.pnl_amount > 0)
        loss_count = sum(1 for row in executed if row.pnl_amount < 0)
        gross_profit = sum(row.pnl_amount for row in executed if row.pnl_amount > 0)
        gross_loss = sum(row.pnl_amount for row in executed if row.pnl_amount < 0)
        avg_pnl_ratio = (
            sum(row.pnl_ratio for row in executed) / trade_count
            if trade_count > 0
            else 0.0
        )
        win_rate = (win_count / trade_count) if trade_count > 0 else 0.0
        total_pnl = sum(row.pnl_amount for row in executed)
        final_equity = float(payload.initial_capital) + total_pnl
        total_return = (final_equity / payload.initial_capital - 1) if payload.initial_capital > 0 else 0.0

        if gross_loss < 0:
            profit_factor = gross_profit / abs(gross_loss)
        elif gross_profit > 0:
            profit_factor = math.inf
        else:
            profit_factor = 0.0

        if not build_equity_curve:
            notes.append(
                f"并发持仓峰值: {max_concurrent_positions}/{payload.max_positions}；"
                "轻量预演模式：跳过资金曲线与回撤计算。"
            )
            notes.append(
                f"执行细分耗时[候选={candidate_stage_elapsed:.2f}s, 撮合={execution_match_elapsed:.2f}s, 曲线=0.00s]"
            )
            monthly_agg: dict[str, dict[str, float]] = defaultdict(lambda: {"pnl": 0.0, "count": 0.0})
            for row in executed:
                month = row.exit_date[:7]
                monthly_agg[month]["pnl"] += row.pnl_amount
                monthly_agg[month]["count"] += 1.0
            monthly_returns = [
                MonthlyReturnPoint(
                    month=month,
                    return_ratio=round(values["pnl"] / payload.initial_capital, 6) if payload.initial_capital > 0 else 0.0,
                    pnl_amount=round(values["pnl"], 4),
                    trade_count=int(values["count"]),
                )
                for month, values in sorted(monthly_agg.items())
            ]
            top_trades = sorted(executed, key=lambda row: row.pnl_amount, reverse=True)[:10]
            bottom_trades = sorted(executed, key=lambda row: row.pnl_amount)[:10]
            stats = ReviewStats(
                win_rate=round(win_rate, 6),
                total_return=round(total_return, 6),
                max_drawdown=0.0,
                avg_pnl_ratio=round(avg_pnl_ratio, 6),
                trade_count=trade_count,
                win_count=win_count,
                loss_count=loss_count,
                profit_factor=round(profit_factor, 6) if math.isfinite(profit_factor) else 999.0,
            )
            cost_snapshot = SimTradingConfig(
                initial_capital=float(payload.initial_capital),
                commission_rate=min(0.01, fee_rate),
                min_commission=0.0,
                stamp_tax_rate=0.0,
                transfer_fee_rate=0.0,
                slippage_rate=0.0,
            )
            return BacktestResponse(
                stats=stats,
                trades=executed,
                equity_curve=[],
                drawdown_curve=[],
                monthly_returns=monthly_returns,
                top_trades=top_trades,
                bottom_trades=bottom_trades,
                cost_snapshot=cost_snapshot,
                range=ReviewRange(date_from=start_date, date_to=end_date, date_axis="sell"),
                notes=notes,
                candidate_count=candidate_count,
                skipped_count=skipped_count,
                fill_rate=round(fill_rate, 6),
                max_concurrent_positions=max_concurrent_positions,
                plan_signals=plan_signals,
                open_signals=open_signals,
            )

        curve_stage_start = time.perf_counter()
        entries_by_date: dict[str, list[tuple[int, BacktestTrade]]] = defaultdict(list)
        exits_by_date: dict[str, list[tuple[int, BacktestTrade]]] = defaultdict(list)
        for idx, trade in enumerate(executed):
            entries_by_date[trade.entry_date].append((idx, trade))
            exits_by_date[trade.exit_date].append((idx, trade))

        executed_symbols = {trade.symbol for trade in executed}
        input_symbols = {str(symbol).strip().lower() for symbol in symbols if str(symbol).strip()}
        calendar_dates_set: set[str] = set()
        close_map_by_symbol: dict[str, dict[str, float]] = {}
        if matrix_bundle is not None and matrix_bundle.dates:
            matrix_dates = list(matrix_bundle.dates)
            in_range_indexes = [
                idx for idx, day in enumerate(matrix_dates) if start_date <= day <= end_date
            ]
            for idx in in_range_indexes:
                calendar_dates_set.add(matrix_dates[idx])

            symbol_to_col = matrix_bundle.symbol_to_index()
            for symbol in executed_symbols:
                col = symbol_to_col.get(symbol)
                if col is None:
                    continue
                close_col = matrix_bundle.close[:, col]
                valid_col = matrix_bundle.valid_mask[:, col]
                day_close: dict[str, float] = {}
                for idx in in_range_indexes:
                    if not bool(valid_col[idx]):
                        continue
                    close_price = float(close_col[idx])
                    if math.isfinite(close_price) and close_price > 0:
                        day_close[matrix_dates[idx]] = close_price
                close_map_by_symbol[symbol] = day_close
        else:
            for symbol in input_symbols:
                day_close: dict[str, float] = {}
                for bar in self._get_candles(symbol):
                    if bar.time < start_date or bar.time > end_date:
                        continue
                    calendar_dates_set.add(bar.time)
                    if symbol not in executed_symbols:
                        continue
                    close_price = float(bar.close)
                    if math.isfinite(close_price) and close_price > 0:
                        day_close[bar.time] = close_price
                if symbol in executed_symbols:
                    close_map_by_symbol[symbol] = day_close

        for symbol in executed_symbols:
            if symbol in close_map_by_symbol:
                continue
            day_close: dict[str, float] = {}
            for bar in self._get_candles(symbol):
                if bar.time < start_date or bar.time > end_date:
                    continue
                calendar_dates_set.add(bar.time)
                close_price = float(bar.close)
                if math.isfinite(close_price) and close_price > 0:
                    day_close[bar.time] = close_price
            close_map_by_symbol[symbol] = day_close

        if not calendar_dates_set:
            calendar_dates_set.update(day for day in entries_by_date if start_date <= day <= end_date)
            calendar_dates_set.update(day for day in exits_by_date if start_date <= day <= end_date)

        if not calendar_dates_set:
            cursor_dt = start_dt
            while cursor_dt <= end_dt:
                if cursor_dt.weekday() < 5:
                    calendar_dates_set.add(cursor_dt.strftime("%Y-%m-%d"))
                cursor_dt += timedelta(days=1)

        trading_dates = sorted(calendar_dates_set) if calendar_dates_set else [start_date]
        notes.append("资金曲线按交易日盯市：周末及节假日不生成净值点。")
        running_realized_pnl = 0.0
        cash_mark = float(payload.initial_capital)
        open_positions: dict[int, dict[str, float | str]] = {}
        last_close_by_symbol: dict[str, float] = {}
        days_with_positions = 0

        equity_curve: list[EquityPoint] = []
        for day in trading_dates:
            if control_callback is not None:
                control_callback()
            for idx, trade in entries_by_date.get(day, []):
                entry_exec = float(trade.entry_price) * (1 + fee_rate)
                invested = float(trade.quantity) * entry_exec
                cash_mark -= invested
                open_positions[idx] = {
                    "symbol": trade.symbol,
                    "quantity": float(trade.quantity),
                    "entry_price": float(trade.entry_price),
                }

            for idx, trade in exits_by_date.get(day, []):
                if self._is_backtest_open_holding(trade.exit_reason):
                    continue
                exit_exec = float(trade.exit_price) * (1 - fee_rate)
                exit_amount = float(trade.quantity) * exit_exec
                cash_mark += exit_amount
                running_realized_pnl += float(trade.pnl_amount)
                open_positions.pop(idx, None)

            market_value = 0.0
            for position in open_positions.values():
                symbol = str(position.get("symbol", ""))
                quantity = float(position.get("quantity", 0.0))
                mark = close_map_by_symbol.get(symbol, {}).get(day)
                if mark is not None and math.isfinite(mark) and mark > 0:
                    last_close_by_symbol[symbol] = mark
                else:
                    mark = last_close_by_symbol.get(symbol)
                if mark is None or not math.isfinite(mark) or mark <= 0:
                    mark = float(position.get("entry_price", 0.0))
                market_value += quantity * mark
            if open_positions:
                days_with_positions += 1

            equity_curve.append(
                EquityPoint(
                    date=day,
                    equity=round(cash_mark + market_value, 4),
                    realized_pnl=round(running_realized_pnl, 4),
                )
            )

        if not equity_curve:
            equity_curve.append(
                EquityPoint(
                    date=start_date,
                    equity=round(float(payload.initial_capital), 4),
                    realized_pnl=0.0,
                )
            )
        notes.append(
            f"并发持仓峰值: {max_concurrent_positions}/{payload.max_positions}；"
            f"持仓覆盖交易日: {days_with_positions}/{len(trading_dates)}。"
        )

        curve_elapsed = time.perf_counter() - curve_stage_start
        notes.append(
            f"执行细分耗时[候选={candidate_stage_elapsed:.2f}s, 撮合={execution_match_elapsed:.2f}s, 曲线={curve_elapsed:.2f}s]"
        )

        drawdown_curve: list[DrawdownPoint] = []
        peak = -float("inf")
        max_drawdown_raw = 0.0
        for row in equity_curve:
            peak = max(peak, row.equity)
            drawdown = (row.equity - peak) / peak if peak > 0 else 0.0
            max_drawdown_raw = min(max_drawdown_raw, drawdown)
            drawdown_curve.append(DrawdownPoint(date=row.date, drawdown=round(drawdown, 6)))

        monthly_agg: dict[str, dict[str, float]] = defaultdict(lambda: {"pnl": 0.0, "count": 0.0})
        for row in executed:
            month = row.exit_date[:7]
            monthly_agg[month]["pnl"] += row.pnl_amount
            monthly_agg[month]["count"] += 1.0
        monthly_returns = [
            MonthlyReturnPoint(
                month=month,
                return_ratio=round(values["pnl"] / payload.initial_capital, 6) if payload.initial_capital > 0 else 0.0,
                pnl_amount=round(values["pnl"], 4),
                trade_count=int(values["count"]),
            )
            for month, values in sorted(monthly_agg.items())
        ]

        if equity_curve:
            final_equity = float(equity_curve[-1].equity)
            total_return = (
                (final_equity / payload.initial_capital - 1) if payload.initial_capital > 0 else 0.0
            )

        top_trades = sorted(executed, key=lambda row: row.pnl_amount, reverse=True)[:10]
        bottom_trades = sorted(executed, key=lambda row: row.pnl_amount)[:10]
        stats = ReviewStats(
            win_rate=round(win_rate, 6),
            total_return=round(total_return, 6),
            max_drawdown=round(abs(max_drawdown_raw), 6),
            avg_pnl_ratio=round(avg_pnl_ratio, 6),
            trade_count=trade_count,
            win_count=win_count,
            loss_count=loss_count,
            profit_factor=round(profit_factor, 6) if math.isfinite(profit_factor) else 999.0,
        )

        cost_snapshot = SimTradingConfig(
            initial_capital=float(payload.initial_capital),
            commission_rate=min(0.01, fee_rate),
            min_commission=0.0,
            stamp_tax_rate=0.0,
            transfer_fee_rate=0.0,
            slippage_rate=0.0,
        )

        return BacktestResponse(
            stats=stats,
            trades=executed,
            equity_curve=equity_curve,
            drawdown_curve=drawdown_curve,
            monthly_returns=monthly_returns,
            top_trades=top_trades,
            bottom_trades=bottom_trades,
            cost_snapshot=cost_snapshot,
            range=ReviewRange(date_from=start_date, date_to=end_date, date_axis="sell"),
            notes=notes,
            candidate_count=candidate_count,
            skipped_count=skipped_count,
            fill_rate=round(fill_rate, 6),
            max_concurrent_positions=max_concurrent_positions,
            plan_signals=plan_signals,
            open_signals=open_signals,
        )

    # ------------------------------------------------------------------
    # Plateau 优化：候选交易复用 + 仅重放组合模拟
    # ------------------------------------------------------------------

    def run_candidates_only(
        self,
        *,
        payload: BacktestRunRequest,
        symbols: list[str],
        allowed_symbols_by_date: dict[str, set[str]] | None = None,
        apply_priority_topk: bool = True,
        control_callback: Callable[[], None] | None = None,
    ) -> list[CandidateTrade]:
        """Build and return sorted candidate trades without portfolio simulation.

        Used by plateau evaluation to cache candidates across parameter
        combinations that share the same candidate-generation key.
        """
        start_dt = self._parse_date(payload.date_from)
        end_dt = self._parse_date(payload.date_to)
        if start_dt is None or end_dt is None:
            raise ValueError("date_from/date_to 必须是 YYYY-MM-DD")
        if start_dt > end_dt:
            start_dt, end_dt = end_dt, start_dt
        start_date = start_dt.strftime("%Y-%m-%d")
        end_date = end_dt.strftime("%Y-%m-%d")

        candidates: list[CandidateTrade] = []
        for symbol in symbols:
            if control_callback is not None:
                control_callback()
            rows, _, _, _ = self._build_candidates_for_symbol(
                symbol,
                payload,
                start_date,
                end_date,
                allowed_symbols_by_date=allowed_symbols_by_date,
                allow_reentry_after_skipped=payload.pool_roll_mode == "position",
                control_callback=control_callback,
            )
            candidates.extend(rows)

        if payload.prioritize_signals:
            candidates.sort(
                key=lambda row: self._candidate_sort_key(row, priority_mode=payload.priority_mode),
            )
        else:
            candidates.sort(key=lambda row: (row.entry_date, row.symbol, row.exit_date))

        if apply_priority_topk and payload.prioritize_signals and payload.priority_topk_per_day > 0:
            kept: list[CandidateTrade] = []
            day_counter: dict[str, int] = defaultdict(int)
            for row in candidates:
                if day_counter[row.signal_date] >= payload.priority_topk_per_day:
                    continue
                kept.append(row)
                day_counter[row.signal_date] += 1
            candidates = kept

        return candidates

    def replay_portfolio(
        self,
        *,
        candidates: list[CandidateTrade],
        payload: BacktestRunRequest,
    ) -> BacktestResponse:
        """Run portfolio simulation on pre-built candidates (no signal detection).

        This is the fast path for plateau evaluation: candidates are built once
        and then replayed with different (max_positions, position_pct,
        priority_topk_per_day) combinations.
        """
        start_dt = self._parse_date(payload.date_from)
        end_dt = self._parse_date(payload.date_to)
        if start_dt is None or end_dt is None:
            raise ValueError("date_from/date_to 必须是 YYYY-MM-DD")
        if start_dt > end_dt:
            start_dt, end_dt = end_dt, start_dt
        start_date = start_dt.strftime("%Y-%m-%d")
        end_date = end_dt.strftime("%Y-%m-%d")

        # Re-apply topk filter with the current payload's priority_topk_per_day
        effective_candidates = candidates
        if payload.prioritize_signals and payload.priority_topk_per_day > 0:
            kept: list[CandidateTrade] = []
            day_counter: dict[str, int] = defaultdict(int)
            for row in candidates:
                if day_counter[row.signal_date] >= payload.priority_topk_per_day:
                    continue
                kept.append(row)
                day_counter[row.signal_date] += 1
            effective_candidates = kept

        candidate_count = len(effective_candidates)
        fee_rate = max(0.0, float(payload.fee_bps)) / 10000.0

        executed, max_concurrent_positions, skip_reasons = self._execute_candidates_portfolio(
            candidates=effective_candidates,
            payload=payload,
            fee_rate=fee_rate,
        )

        skipped_count = int(sum(skip_reasons.values()))
        fill_rate = (len(executed) / candidate_count) if candidate_count > 0 else 0.0
        open_holding_count = sum(1 for row in executed if self._is_backtest_open_holding(row.exit_reason))
        notes: list[str] = [f"plateau replay: candidates={candidate_count}, portfolio_only=True"]
        if open_holding_count > 0:
            executed = self._apply_end_date_mark_to_market(
                executed,
                end_date=end_date,
                fee_rate=fee_rate,
            )
            notes.append(
                f"末日本持仓盯市: {open_holding_count} 笔未卖出持仓已按 {end_date} 收盘价计入收益。"
            )
        trade_count = len(executed)
        win_count = sum(1 for t in executed if t.pnl_amount > 0)
        loss_count = sum(1 for t in executed if t.pnl_amount < 0)
        gross_profit = sum(t.pnl_amount for t in executed if t.pnl_amount > 0)
        gross_loss = sum(t.pnl_amount for t in executed if t.pnl_amount < 0)
        avg_pnl_ratio = (sum(t.pnl_ratio for t in executed) / trade_count) if trade_count > 0 else 0.0
        win_rate = (win_count / trade_count) if trade_count > 0 else 0.0
        total_pnl = sum(t.pnl_amount for t in executed)
        final_equity = float(payload.initial_capital) + total_pnl
        total_return = (final_equity / payload.initial_capital - 1) if payload.initial_capital > 0 else 0.0
        if gross_loss < 0:
            profit_factor = gross_profit / abs(gross_loss)
        elif gross_profit > 0:
            profit_factor = math.inf
        else:
            profit_factor = 0.0
        monthly_agg: dict[str, dict[str, float]] = defaultdict(lambda: {"pnl": 0.0, "count": 0.0})
        for t in executed:
            month = t.exit_date[:7]
            monthly_agg[month]["pnl"] += t.pnl_amount
            monthly_agg[month]["count"] += 1.0
        monthly_returns = [
            MonthlyReturnPoint(
                month=m,
                return_ratio=round(v["pnl"] / payload.initial_capital, 6) if payload.initial_capital > 0 else 0.0,
                pnl_amount=round(v["pnl"], 4),
                trade_count=int(v["count"]),
            )
            for m, v in sorted(monthly_agg.items())
        ]
        stats = ReviewStats(
            win_rate=round(win_rate, 6),
            total_return=round(total_return, 6),
            max_drawdown=0.0,
            avg_pnl_ratio=round(avg_pnl_ratio, 6),
            trade_count=trade_count,
            win_count=win_count,
            loss_count=loss_count,
            profit_factor=round(profit_factor, 6) if math.isfinite(profit_factor) else 999.0,
        )
        cost_snapshot = SimTradingConfig(
            initial_capital=float(payload.initial_capital),
            commission_rate=min(0.01, fee_rate),
            min_commission=0.0,
            stamp_tax_rate=0.0,
            transfer_fee_rate=0.0,
            slippage_rate=0.0,
        )
        return BacktestResponse(
            stats=stats,
            trades=executed,
            equity_curve=[],
            drawdown_curve=[],
            monthly_returns=monthly_returns,
            top_trades=sorted(executed, key=lambda t: t.pnl_amount, reverse=True)[:10],
            bottom_trades=sorted(executed, key=lambda t: t.pnl_amount)[:10],
            cost_snapshot=cost_snapshot,
            range=ReviewRange(date_from=start_date, date_to=end_date, date_axis="sell"),
            notes=notes,
            candidate_count=candidate_count,
            skipped_count=skipped_count,
            fill_rate=round(fill_rate, 6),
            max_concurrent_positions=max_concurrent_positions,
        )
