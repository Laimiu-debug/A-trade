"""Market momentum scan: trend leaders timeline and limit-up ladder timeline."""
from __future__ import annotations

import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..tdx_loader import load_candles_for_symbol
from .trend_leaders_cache import TrendLeadersDailyCache


def limit_up_ratio(symbol: str) -> float:
    code = str(symbol or "")[2:]
    market = str(symbol or "")[:2]
    if market == "bj":
        return 0.30
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    return 0.10


def detect_primary_board(symbol: str) -> str | None:
    normalized = str(symbol).strip().lower()
    if len(normalized) < 8:
        return None
    market = normalized[:2]
    code = normalized[2:]
    if market == "bj":
        return "beijing"
    if market == "sh":
        if code.startswith("688") or code.startswith("689"):
            return "star"
        return "main"
    if market == "sz":
        if code.startswith("300") or code.startswith("301"):
            return "gem"
        return "main"
    return None


def is_st_stock(name: str) -> bool:
    normalized_name = re.sub(r"\s+", "", str(name).upper())
    return "ST" in normalized_name


def symbol_matches_board_filters(symbol: str, name: str, board_filters: list[str]) -> bool:
    if not board_filters:
        return True
    if is_st_stock(name) and "st" not in board_filters:
        return False
    selected_boards = [item for item in board_filters if item != "st"]
    if not selected_boards:
        return is_st_stock(name)
    board = detect_primary_board(symbol)
    if board is None:
        return False
    return board in selected_boards


def _iter_tdx_symbols(tdx_root: str, markets: list[str]) -> list[tuple[str, str]]:
    symbols: list[tuple[str, str]] = []
    for market in markets:
        market_dir = os.path.join(tdx_root, market, "lday")
        if not os.path.isdir(market_dir):
            continue
        for fname in sorted(os.listdir(market_dir)):
            if not fname.endswith(".day"):
                continue
            code = fname.replace(market, "").replace(".day", "")
            if market == "sh" and not code.startswith("60") and not code.startswith("68"):
                continue
            if market == "sz" and not (code.startswith("00") or code.startswith("30")):
                continue
            symbols.append((code, f"{market}{code}"))
    return symbols


def _bars_from_candles(candles: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "date": cp.time,
            "open": float(cp.open),
            "high": float(cp.high),
            "low": float(cp.low),
            "close": float(cp.close),
            "volume": max(0, int(cp.volume)),
            "amount": max(0.0, float(cp.amount)),
        }
        for cp in candles
    ]


def _find_bar_index(bars: list[dict[str, Any]], target_date: str) -> int | None:
    for idx, bar in enumerate(bars):
        if str(bar["date"]) == target_date:
            return idx
    return None


def _is_limit_up(bar: dict[str, Any], prev_close: float, symbol: str) -> bool:
    if prev_close <= 0:
        return False
    pct = (float(bar["close"]) - prev_close) / prev_close
    threshold = limit_up_ratio(symbol) - 0.002
    return pct >= threshold


def count_consecutive_limit_up(bars: list[dict[str, Any]], end_idx: int, symbol: str) -> int:
    if end_idx <= 0 or end_idx >= len(bars):
        return 0
    count = 0
    idx = end_idx
    while idx > 0:
        prev_close = float(bars[idx - 1]["close"])
        if not _is_limit_up(bars[idx], prev_close, symbol):
            break
        count += 1
        idx -= 1
    return count


def window_return_pct(bars: list[dict[str, Any]], end_idx: int, window_days: int) -> float | None:
    start_idx = end_idx - window_days
    if start_idx < 0:
        return None
    start_close = float(bars[start_idx]["close"])
    end_close = float(bars[end_idx]["close"])
    if start_close <= 0:
        return None
    return (end_close - start_close) / start_close * 100.0


def _mean_amount(bars: list[dict[str, Any]], end_idx: int, window_days: int) -> float:
    start_idx = max(0, end_idx - window_days + 1)
    slice_bars = bars[start_idx : end_idx + 1]
    if not slice_bars:
        return 0.0
    return sum(float(item["amount"]) for item in slice_bars) / len(slice_bars)


def _collect_trading_dates(all_bars: list[list[dict[str, Any]]], date_from: str, date_to: str) -> list[str]:
    dates: set[str] = set()
    for bars in all_bars:
        for bar in bars:
            day = str(bar["date"])
            if date_from <= day <= date_to:
                dates.add(day)
    return sorted(dates)


@dataclass
class TrendLeaderRow:
    symbol: str
    name: str
    return_pct: float
    window_return_pct: float
    amount_avg: float
    board: str | None
    leader_days: int = 0
    first_leader_date: str | None = None
    last_leader_date: str | None = None
    max_return_pct: float = 0.0


@dataclass
class TrendSeriesPoint:
    date: str
    return_pct: float


@dataclass
class TrendSeriesRow:
    symbol: str
    name: str
    points: list[TrendSeriesPoint] = field(default_factory=list)


@dataclass
class LimitUpTimelinePoint:
    symbol: str
    name: str
    date: str
    board_height: int


@dataclass
class LimitUpStockSummary:
    symbol: str
    name: str
    max_board_height: int
    active_days: int
    latest_board_height: int = 0


@dataclass
class TrendScanStats:
    cache_hits: int = 0
    cache_misses: int = 0
    live_days: int = 0
    frozen_days: int = 0
    computed_days: int = 0


def _compute_daily_top_symbols(
    symbol_bars: dict[str, list[dict[str, Any]]],
    eval_date: str,
    window_days: int,
    daily_top_n: int,
    min_amount_avg: float,
) -> list[str]:
    candidates: list[tuple[str, float]] = []
    for symbol, bars in symbol_bars.items():
        idx = _find_bar_index(bars, eval_date)
        if idx is None or idx < window_days:
            continue
        ret = window_return_pct(bars, idx, window_days)
        if ret is None or ret <= 0:
            continue
        if _mean_amount(bars, idx, window_days) < min_amount_avg:
            continue
        candidates.append((symbol, ret))
    candidates.sort(key=lambda item: item[1], reverse=True)
    return [symbol for symbol, _ret in candidates[: max(1, daily_top_n)]]


def _load_symbol_bars_for_scan(
    *,
    tdx_root: str,
    resolve_name: Callable[[str], str],
    market_data_source: str,
    akshare_cache_dir: str,
    board_filters: list[str],
    bars_needed: int,
    date_from: str,
    date_to: str,
    markets: list[str],
    symbol_filter: set[str] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str], int]:
    symbol_bars: dict[str, list[dict[str, Any]]] = {}
    symbol_names: dict[str, str] = {}
    total_scanned = 0
    for _code, full_symbol in _iter_tdx_symbols(tdx_root, markets):
        if symbol_filter is not None and full_symbol not in symbol_filter:
            continue
        total_scanned += 1
        name = resolve_name(full_symbol)
        if not symbol_matches_board_filters(full_symbol, name, board_filters):
            continue
        try:
            candles = load_candles_for_symbol(
                tdx_root,
                full_symbol,
                window=bars_needed,
                market_data_source=market_data_source,
                akshare_cache_dir=akshare_cache_dir,
            )
            if candles is None or len(candles) < 2:
                continue
            bars = _bars_from_candles(candles)
            if not any(date_from <= str(item["date"]) <= date_to for item in bars):
                continue
            symbol_bars[full_symbol] = bars
            symbol_names[full_symbol] = name
        except Exception:
            continue
    return symbol_bars, symbol_names, total_scanned


def _reference_trading_dates(
    *,
    tdx_root: str,
    market_data_source: str,
    akshare_cache_dir: str,
    date_from: str,
    date_to: str,
) -> list[str]:
    candles = load_candles_for_symbol(
        tdx_root,
        "sh600519",
        window=1200,
        market_data_source=market_data_source,
        akshare_cache_dir=akshare_cache_dir,
    )
    if candles is None:
        return []
    bars = _bars_from_candles(candles)
    return [str(item["date"]) for item in bars if date_from <= str(item["date"]) <= date_to]


def scan_trend_leaders_timeline(
    *,
    tdx_root: str,
    resolve_name: Callable[[str], str],
    market_data_source: str,
    akshare_cache_dir: str,
    date_from: str,
    date_to: str,
    window_days: int = 20,
    daily_top_n: int = 5,
    board_filters: list[str] | None = None,
    min_amount_avg: float = 5e7,
    markets: list[str] | None = None,
    cache_path: str | None = None,
) -> tuple[list[TrendLeaderRow], list[TrendSeriesRow], list[str], int, float, TrendScanStats]:
    """Collect daily top-N trend leaders; freeze historical days via cache, recompute live window only."""
    start_ts = time.monotonic()
    stats = TrendScanStats()
    filters = list(board_filters or [])
    market_list = markets or ["sh", "sz"]

    trading_dates = _reference_trading_dates(
        tdx_root=tdx_root,
        market_data_source=market_data_source,
        akshare_cache_dir=akshare_cache_dir,
        date_from=date_from,
        date_to=date_to,
    )
    if not trading_dates:
        elapsed = time.monotonic() - start_ts
        return [], [], [], 0, elapsed, stats

    bars_needed = max(window_days + 10, len(trading_dates) + window_days + 20, 320)
    live_count = min(len(trading_dates), max(1, window_days))
    live_dates = set(trading_dates[-live_count:])
    frozen_dates = [day for day in trading_dates if day not in live_dates]
    stats.live_days = len(live_dates)
    stats.frozen_days = len(frozen_dates)

    cache = TrendLeadersDailyCache(Path(cache_path)) if cache_path else None
    daily_leaders: dict[str, list[str]] = {}
    dates_to_compute: list[str] = []

    for eval_date in frozen_dates:
        cached_symbols: list[str] | None = None
        if cache is not None:
            cached_symbols = cache.get(
                eval_date=eval_date,
                window_days=window_days,
                daily_top_n=daily_top_n,
                board_filters=filters,
                min_amount_avg=min_amount_avg,
            )
        if cached_symbols:
            daily_leaders[eval_date] = cached_symbols
            stats.cache_hits += 1
        else:
            dates_to_compute.append(eval_date)
            stats.cache_misses += 1

    for eval_date in live_dates:
        dates_to_compute.append(eval_date)

    dates_to_compute = sorted(set(dates_to_compute))
    stats.computed_days = len(dates_to_compute)

    symbol_bars: dict[str, list[dict[str, Any]]] = {}
    symbol_names: dict[str, str] = {}
    total_scanned = 0

    if dates_to_compute:
        symbol_bars, symbol_names, total_scanned = _load_symbol_bars_for_scan(
            tdx_root=tdx_root,
            resolve_name=resolve_name,
            market_data_source=market_data_source,
            akshare_cache_dir=akshare_cache_dir,
            board_filters=filters,
            bars_needed=bars_needed,
            date_from=date_from,
            date_to=date_to,
            markets=market_list,
        )
        for eval_date in dates_to_compute:
            leaders = _compute_daily_top_symbols(
                symbol_bars,
                eval_date,
                window_days,
                daily_top_n,
                min_amount_avg,
            )
            daily_leaders[eval_date] = leaders
            if cache is not None:
                cache.set(
                    eval_date=eval_date,
                    window_days=window_days,
                    daily_top_n=daily_top_n,
                    board_filters=filters,
                    min_amount_avg=min_amount_avg,
                    symbols=leaders,
                )
        if cache is not None:
            cache.save()

    leader_symbols: set[str] = set()
    leader_day_count: dict[str, int] = defaultdict(int)
    leader_first: dict[str, str] = {}
    leader_last: dict[str, str] = {}

    for eval_date in trading_dates:
        for symbol in daily_leaders.get(eval_date, []):
            leader_symbols.add(symbol)
            leader_day_count[symbol] += 1
            if symbol not in leader_first:
                leader_first[symbol] = eval_date
            leader_last[symbol] = eval_date

    missing_symbols = leader_symbols - set(symbol_bars.keys())
    if missing_symbols:
        extra_bars, extra_names, extra_scanned = _load_symbol_bars_for_scan(
            tdx_root=tdx_root,
            resolve_name=resolve_name,
            market_data_source=market_data_source,
            akshare_cache_dir=akshare_cache_dir,
            board_filters=filters,
            bars_needed=bars_needed,
            date_from=date_from,
            date_to=date_to,
            markets=market_list,
            symbol_filter=missing_symbols,
        )
        symbol_bars.update(extra_bars)
        symbol_names.update(extra_names)
        total_scanned += extra_scanned

    series_rows: list[TrendSeriesRow] = []
    leader_rows: list[TrendLeaderRow] = []

    for symbol in leader_symbols:
        bars = symbol_bars.get(symbol)
        if not bars:
            continue
        start_idx = _find_bar_index(bars, date_from)
        if start_idx is None:
            for idx, bar in enumerate(bars):
                if str(bar["date"]) >= date_from:
                    start_idx = idx
                    break
        if start_idx is None:
            continue
        base_close = float(bars[start_idx]["close"])
        if base_close <= 0:
            continue

        points: list[TrendSeriesPoint] = []
        max_return = 0.0
        end_idx = _find_bar_index(bars, date_to) or (len(bars) - 1)
        day_ret = 0.0
        if end_idx > 0:
            prev_close = float(bars[end_idx - 1]["close"])
            if prev_close > 0:
                day_ret = (float(bars[end_idx]["close"]) - prev_close) / prev_close * 100.0

        for eval_date in trading_dates:
            idx = _find_bar_index(bars, eval_date)
            if idx is None:
                continue
            ret_pct = (float(bars[idx]["close"]) - base_close) / base_close * 100.0
            max_return = max(max_return, ret_pct)
            points.append(TrendSeriesPoint(date=eval_date, return_pct=round(ret_pct, 2)))

        window_ret = window_return_pct(bars, end_idx, window_days) or 0.0
        leader_rows.append(
            TrendLeaderRow(
                symbol=symbol,
                name=symbol_names.get(symbol, resolve_name(symbol)),
                return_pct=round(day_ret, 2),
                window_return_pct=round(window_ret, 2),
                amount_avg=round(_mean_amount(bars, end_idx, window_days), 2),
                board=detect_primary_board(symbol),
                leader_days=leader_day_count.get(symbol, 0),
                first_leader_date=leader_first.get(symbol),
                last_leader_date=leader_last.get(symbol),
                max_return_pct=round(max_return, 2),
            )
        )
        series_rows.append(
            TrendSeriesRow(
                symbol=symbol,
                name=symbol_names.get(symbol, resolve_name(symbol)),
                points=points,
            )
        )

    leader_rows.sort(key=lambda row: (row.leader_days, row.max_return_pct), reverse=True)
    series_rows.sort(
        key=lambda row: next(
            (item.leader_days for item in leader_rows if item.symbol == row.symbol),
            0,
        ),
        reverse=True,
    )
    elapsed = time.monotonic() - start_ts
    return leader_rows, series_rows, trading_dates, total_scanned, elapsed, stats


def scan_limit_up_timeline(
    *,
    tdx_root: str,
    resolve_name: Callable[[str], str],
    market_data_source: str,
    akshare_cache_dir: str,
    date_from: str,
    date_to: str,
    recent_days: int = 5,
    historical_min_boards: int = 3,
    board_filters: list[str] | None = None,
    markets: list[str] | None = None,
) -> tuple[list[LimitUpTimelinePoint], list[LimitUpStockSummary], list[str], int, float]:
    """Scan limit-up ladder points across a date range."""
    start_ts = time.monotonic()
    filters = list(board_filters or [])
    market_list = markets or ["sh", "sz"]
    bars_needed = 320
    total_scanned = 0

    symbol_bars: dict[str, list[dict[str, Any]]] = {}
    symbol_names: dict[str, str] = {}

    for _code, full_symbol in _iter_tdx_symbols(tdx_root, market_list):
        total_scanned += 1
        name = resolve_name(full_symbol)
        if not symbol_matches_board_filters(full_symbol, name, filters):
            continue
        try:
            candles = load_candles_for_symbol(
                tdx_root,
                full_symbol,
                window=bars_needed,
                market_data_source=market_data_source,
                akshare_cache_dir=akshare_cache_dir,
            )
            if candles is None or len(candles) < 3:
                continue
            bars = _bars_from_candles(candles)
            if not any(date_from <= str(item["date"]) <= date_to for item in bars):
                continue
            symbol_bars[full_symbol] = bars
            symbol_names[full_symbol] = name
        except Exception:
            continue

    trading_dates = _collect_trading_dates(list(symbol_bars.values()), date_from, date_to)
    if not trading_dates:
        elapsed = time.monotonic() - start_ts
        return [], [], [], total_scanned, elapsed

    recent_start = trading_dates[max(0, len(trading_dates) - max(1, recent_days))]

    timeline: list[LimitUpTimelinePoint] = []
    summary_map: dict[str, LimitUpStockSummary] = {}

    for symbol, bars in symbol_bars.items():
        name = symbol_names.get(symbol, symbol)
        active_days = 0
        max_height = 0
        latest_height = 0
        for eval_date in trading_dates:
            idx = _find_bar_index(bars, eval_date)
            if idx is None:
                continue
            height = count_consecutive_limit_up(bars, idx, symbol)
            if eval_date == date_to or eval_date == trading_dates[-1]:
                latest_height = height
            if height <= 0:
                continue
            in_recent = eval_date >= recent_start
            if in_recent or height >= historical_min_boards:
                timeline.append(
                    LimitUpTimelinePoint(
                        symbol=symbol,
                        name=name,
                        date=eval_date,
                        board_height=height,
                    )
                )
                active_days += 1
                max_height = max(max_height, height)

        if active_days > 0:
            summary_map[symbol] = LimitUpStockSummary(
                symbol=symbol,
                name=name,
                max_board_height=max_height,
                active_days=active_days,
                latest_board_height=latest_height,
            )

    timeline.sort(key=lambda row: (row.date, -row.board_height, row.symbol))
    summaries = sorted(
        summary_map.values(),
        key=lambda row: (row.max_board_height, row.active_days),
        reverse=True,
    )
    elapsed = time.monotonic() - start_ts
    return timeline, summaries, trading_dates, total_scanned, elapsed
