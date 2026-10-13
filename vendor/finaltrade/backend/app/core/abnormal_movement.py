"""Severe abnormal fluctuation scan (10d +100% / 30d +200% deviation rules)."""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from ..tdx_loader import load_candles_for_symbol
from .market_momentum import (
    _bars_from_candles,
    _is_limit_up,
    _iter_tdx_symbols,
    _reference_trading_dates,
    detect_primary_board,
    limit_up_ratio,
    symbol_matches_board_filters,
)


ALGORITHM_VERSION = "v4.1-fast-scan"
MAX_BARS_CAP = 900

TRIGGER_THRESHOLD_10 = 100.0
TRIGGER_THRESHOLD_30 = 200.0
WARN_THRESHOLD_10 = 80.0
WARN_THRESHOLD_30 = 160.0
WINDOW_10 = 10
WINDOW_30 = 30
MIN_BARS_FLOOR = WINDOW_30 + 60

# 监管口径：单只证券涨跌幅 − 对应分类指数涨跌幅；创业板用创业板综合指数 399102。
BENCHMARK_SPECS: dict[str, tuple[str, str]] = {
    "sh_main": ("sh000002", "上证A指"),
    "sz_main": ("sz399107", "深证A指"),
    "gem": ("sz399102", "创业板综合"),
    "star": ("sh000688", "科创50"),
    "beijing": ("bj899050", "北证50"),
}

BENCHMARK_FALLBACK: dict[str, tuple[str, str]] = {
    "sh_main": ("sh000001", "上证指数"),
    "sz_main": ("sz399001", "深证成指"),
    "gem": ("sz399006", "创业板指"),
    "beijing": ("sz399101", "中证全指"),
}


def resolve_benchmark_key(symbol: str) -> str:
    board = detect_primary_board(symbol)
    market = str(symbol or "")[:2]
    if board == "gem":
        return "gem"
    if board == "star":
        return "star"
    if board == "beijing":
        return "beijing"
    if market == "sh":
        return "sh_main"
    return "sz_main"


def resolve_benchmark_symbol(symbol: str) -> tuple[str, str]:
    key = resolve_benchmark_key(symbol)
    primary = BENCHMARK_SPECS.get(key) or BENCHMARK_FALLBACK["sz_main"]
    return primary[0], primary[1]


def _resolve_bars_needed(
    *,
    date_from: str,
    date_to: str,
    trading_dates: list[str],
    snapshot_only: bool = False,
) -> int:
    if snapshot_only:
        # 截至日快照只需覆盖 30 日窗口 + 少量缓冲
        return min(MAX_BARS_CAP, max(MIN_BARS_FLOOR, WINDOW_30 + 25))
    in_range = [day for day in trading_dates if date_from <= day <= date_to]
    span = len(in_range) if in_range else 90
    return min(MAX_BARS_CAP, max(MIN_BARS_FLOOR, WINDOW_30 + span + 40))


def _fast_board_prefilter(symbol: str, board_filters: list[str]) -> bool:
    """Board filter without stock name (valid when ST board is not selected)."""
    if not board_filters:
        return True
    if "st" in board_filters:
        return True
    selected_boards = [item for item in board_filters if item != "st"]
    if not selected_boards:
        return False
    board = detect_primary_board(symbol)
    return board is not None and board in selected_boards


def _resolve_parallel_workers(requested: int) -> int:
    if requested > 0:
        return min(32, requested)
    cpu = os.cpu_count() or 4
    return min(32, max(8, cpu * 2))


def _load_symbol_bars_for_abnormal_scan(
    *,
    tdx_root: str,
    resolve_name: Callable[[str], str] | None,
    market_data_source: str,
    akshare_cache_dir: str,
    board_filters: list[str],
    bars_needed: int,
    date_from: str,
    date_to: str,
    markets: list[str],
    parallel_workers: int,
    symbol_filter: set[str] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str], int]:
    filters = list(board_filters or [])
    use_fast_board = "st" not in filters
    jobs: list[str] = []
    total_scanned = 0
    for _code, full_symbol in _iter_tdx_symbols(tdx_root, markets):
        if symbol_filter is not None and full_symbol not in symbol_filter:
            continue
        total_scanned += 1
        if use_fast_board:
            if not _fast_board_prefilter(full_symbol, filters):
                continue
        else:
            name = resolve_name(full_symbol) if resolve_name else full_symbol
            if not symbol_matches_board_filters(full_symbol, name, filters):
                continue
        jobs.append(full_symbol)

    symbol_bars: dict[str, list[dict[str, Any]]] = {}
    symbol_names: dict[str, str] = {}
    if not jobs:
        return symbol_bars, symbol_names, total_scanned

    def _load_one(full_symbol: str) -> tuple[str, list[dict[str, Any]]] | None:
        try:
            candles = load_candles_for_symbol(
                tdx_root,
                full_symbol,
                window=bars_needed,
                market_data_source=market_data_source,
                akshare_cache_dir=akshare_cache_dir,
            )
            if candles is None or len(candles) < 2:
                return None
            bars = _bars_from_candles(candles)
            if not any(date_from <= str(item["date"]) <= date_to for item in bars):
                return None
            return full_symbol, bars
        except Exception:
            return None

    workers = _resolve_parallel_workers(parallel_workers)
    workers = min(workers, len(jobs))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for result in executor.map(_load_one, jobs, chunksize=128):
            if result is None:
                continue
            full_symbol, bars = result
            symbol_bars[full_symbol] = bars
    if resolve_name is not None:
        for full_symbol in symbol_bars:
            symbol_names[full_symbol] = resolve_name(full_symbol)
    return symbol_bars, symbol_names, total_scanned


def resolve_index_close_for_symbol(
    benchmark_maps: dict[str, dict[str, float]],
    symbol: str,
) -> dict[str, float]:
    """Pick index close series with fallbacks so symbols are not dropped silently."""
    preferred_keys = [
        resolve_benchmark_key(symbol),
        "sz_main",
        "sh_main",
        "gem",
        "star",
        "beijing",
    ]
    seen: set[str] = set()
    for key in preferred_keys:
        if key in seen:
            continue
        seen.add(key)
        close_map = benchmark_maps.get(key) or {}
        if close_map:
            return close_map
    merged: dict[str, float] = {}
    for close_map in benchmark_maps.values():
        merged.update(close_map)
    return merged


def _pct_change(close: float, prev_close: float) -> float | None:
    if prev_close <= 0 or close <= 0:
        return None
    return (close / prev_close - 1.0) * 100.0


def _load_index_close_by_date(
    *,
    tdx_root: str,
    benchmark_symbol: str,
    market_data_source: str,
    akshare_cache_dir: str,
    bars_needed: int,
) -> dict[str, float]:
    candles = load_candles_for_symbol(
        tdx_root,
        benchmark_symbol,
        window=bars_needed,
        market_data_source=market_data_source,
        akshare_cache_dir=akshare_cache_dir,
    )
    if candles is None or len(candles) < 2:
        return {}
    bars = _bars_from_candles(candles)
    return {str(item["date"]): float(item["close"]) for item in bars if float(item["close"]) > 0}


def _load_benchmark_close_maps(
    *,
    tdx_root: str,
    market_data_source: str,
    akshare_cache_dir: str,
    bars_needed: int,
) -> dict[str, dict[str, float]]:
    loaded: dict[str, dict[str, float]] = {}
    symbols_to_try: dict[str, list[tuple[str, str]]] = {}
    for key, (primary, _) in BENCHMARK_SPECS.items():
        fallback = BENCHMARK_FALLBACK.get(key)
        symbols_to_try[key] = [(primary, key)]
        if fallback:
            symbols_to_try[key].append(fallback)

    for key, candidates in symbols_to_try.items():
        for symbol, _ in candidates:
            close_map = _load_index_close_by_date(
                tdx_root=tdx_root,
                benchmark_symbol=symbol,
                market_data_source=market_data_source,
                akshare_cache_dir=akshare_cache_dir,
                bars_needed=bars_needed,
            )
            if close_map:
                loaded[key] = close_map
                break
        if key not in loaded:
            loaded[key] = {}
    return loaded


def _capped_stock_pct(
    bars: list[dict[str, Any]],
    bar_idx: int,
    symbol: str,
) -> float | None:
    prev_close = float(bars[bar_idx - 1]["close"])
    close = float(bars[bar_idx]["close"])
    actual = _pct_change(close, prev_close)
    if actual is None:
        return None
    limit_pct = limit_up_ratio(symbol) * 100.0
    if _is_limit_up(bars[bar_idx], prev_close, symbol) and actual > 0:
        return limit_pct
    threshold = limit_pct - 0.2
    if actual >= threshold:
        return limit_pct
    if actual <= -threshold:
        return -limit_pct
    return actual


def _window_cum_deviation(
    bars: list[dict[str, Any]],
    index_close_by_date: dict[str, float],
    bar_idx: int,
    window: int,
) -> float | None:
    """监管窗口偏离：N 个交易日收盘涨跌幅 − 指数同期涨跌幅。"""
    if bar_idx < window:
        return None
    start_idx = bar_idx - window
    start_date = str(bars[start_idx]["date"])
    end_date = str(bars[bar_idx]["date"])
    start_close = float(bars[start_idx]["close"])
    end_close = float(bars[bar_idx]["close"])
    index_start = index_close_by_date.get(start_date)
    index_end = index_close_by_date.get(end_date)
    if index_start is None or index_end is None or start_close <= 0 or index_start <= 0:
        return None
    stock_ret = (end_close / start_close - 1.0) * 100.0
    index_ret = (index_end / index_start - 1.0) * 100.0
    return stock_ret - index_ret


def _rolling_daily_sum(values: list[float], end_idx: int, window: int) -> float | None:
    if end_idx < window - 1:
        return None
    return float(sum(values[end_idx - window + 1 : end_idx + 1]))


def _max_daily_dev_room(
    deviations: list[float],
    end_idx: int,
    window: int,
    threshold: float,
) -> float | None:
    if end_idx < window - 1:
        return None
    prev_sum = float(sum(deviations[end_idx - window + 1 : end_idx]))
    return threshold - prev_sum


def _board_limit_pct(symbol: str) -> float:
    return limit_up_ratio(symbol) * 100.0


@dataclass
class AlignedSeries:
    dates: list[str]
    bar_indices: list[int]
    deviations: list[float]
    stock_pcts: list[float]
    index_pcts: list[float]


@dataclass
class AbnormalDailyLimitRow:
    date: str
    day_offset: int
    cum_dev_10: float | None
    cum_dev_30: float | None
    max_dev_10: float | None
    max_dev_30: float | None
    max_stock_pct_10: float | None
    max_stock_pct_30: float | None
    effective_max_stock_pct: float | None
    actual_deviation: float | None
    actual_stock_pct: float | None
    actual_index_pct: float | None
    reset_10: bool = False
    reset_30: bool = False


@dataclass
class AbnormalMovementEvent:
    symbol: str
    name: str
    board: str | None
    kind: str
    status: str
    entry_date: str
    trigger_date: str | None
    as_of_date: str
    cum_deviation: float
    threshold: float
    warn_threshold: float
    benchmark_symbol: str
    benchmark_name: str
    episode_day: int = 0
    daily_limits: list[AbnormalDailyLimitRow] = field(default_factory=list)


def _build_aligned_series(
    bars: list[dict[str, Any]],
    index_close_by_date: dict[str, float],
    symbol: str,
    *,
    trading_dates: list[str] | None = None,
) -> AlignedSeries | None:
    # 构建参考交易日 → 序号映射,用于判定个股两根 bar 之间是否跨多个交易日(停牌复牌)
    trading_date_rank: dict[str, int] = {}
    if trading_dates:
        for rank, day in enumerate(trading_dates):
            trading_date_rank[str(day)] = rank

    dates: list[str] = []
    bar_indices: list[int] = []
    deviations: list[float] = []
    stock_pcts: list[float] = []
    index_pcts: list[float] = []

    for bar_idx in range(1, len(bars)):
        day = str(bars[bar_idx]["date"])
        prev_day = str(bars[bar_idx - 1]["date"])
        if day not in index_close_by_date or prev_day not in index_close_by_date:
            continue
        # 停牌复牌检测:个股两根 bar 跨越多个交易日时,跳过当日偏离。
        # 否则复牌当日会把 N 日累计涨跌误算成单日偏离,严重放大异动。
        if trading_date_rank:
            prev_rank = trading_date_rank.get(prev_day)
            curr_rank = trading_date_rank.get(day)
            if prev_rank is not None and curr_rank is not None and curr_rank - prev_rank > 1:
                continue
        stock_pct = _capped_stock_pct(bars, bar_idx, symbol)
        if stock_pct is None:
            continue
        index_pct = _pct_change(
            float(index_close_by_date[prev_day]),
            float(index_close_by_date[day]),
        )
        if index_pct is None:
            continue
        dates.append(day)
        bar_indices.append(bar_idx)
        stock_pcts.append(stock_pct)
        index_pcts.append(index_pct)
        deviations.append(stock_pct - index_pct)

    if len(dates) < WINDOW_30 + 1:
        return None
    return AlignedSeries(
        dates=dates,
        bar_indices=bar_indices,
        deviations=deviations,
        stock_pcts=stock_pcts,
        index_pcts=index_pcts,
    )


def _window_cum_at(
    bars: list[dict[str, Any]],
    index_close_by_date: dict[str, float],
    series: AlignedSeries,
    idx: int,
    window: int,
) -> float | None:
    return _window_cum_deviation(bars, index_close_by_date, series.bar_indices[idx], window)


def _effective_cum_at(
    bars: list[dict[str, Any]],
    index_close_by_date: dict[str, float],
    series: AlignedSeries,
    idx: int,
    window: int,
) -> float | None:
    """取窗口偏离与日内偏离累加二者较大值，兼容行情软件两种展示口径。"""
    window_cum = _window_cum_at(bars, index_close_by_date, series, idx, window)
    daily_sum = _rolling_daily_sum(series.deviations, idx, window)
    values = [value for value in (window_cum, daily_sum) if value is not None]
    if not values:
        return None
    return max(values)


def _resolve_as_of_idx(series: AlignedSeries, date_to: str) -> int:
    for idx in range(len(series.dates) - 1, -1, -1):
        if series.dates[idx] <= date_to:
            return idx
    return -1


def _round_cum(value: float | None, digits: int = 1) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _meets_warn(cum: float, warn_threshold: float) -> bool:
    rounded = _round_cum(cum)
    return rounded is not None and rounded >= warn_threshold


def _meets_trigger(cum: float, trigger_threshold: float) -> bool:
    """与行情软件一致：1 位小数四舍五入后达阈即触发；两位显示为 199.97 时按 1 位也视为触发。"""
    rounded1 = _round_cum(cum, 1)
    if rounded1 is not None and rounded1 >= trigger_threshold:
        return True
    rounded2 = _round_cum(cum, 2)
    if rounded2 is not None and rounded2 >= trigger_threshold:
        return True
    return False


def _find_episode_start(
    bars: list[dict[str, Any]],
    index_close_by_date: dict[str, float],
    series: AlignedSeries,
    end_idx: int,
    window: int,
    warn_threshold: float,
) -> int:
    for idx in range(end_idx, -1, -1):
        cum = _effective_cum_at(bars, index_close_by_date, series, idx, window)
        if cum is None or not _meets_warn(cum, warn_threshold):
            continue
        prev = _effective_cum_at(bars, index_close_by_date, series, idx - 1, window) if idx > 0 else None
        if prev is None or not _meets_warn(prev, warn_threshold):
            return idx
    return end_idx


def _find_first_trigger_date(
    bars: list[dict[str, Any]],
    index_close_by_date: dict[str, float],
    series: AlignedSeries,
    start_idx: int,
    end_idx: int,
    window: int,
    trigger_threshold: float,
) -> str | None:
    for idx in range(start_idx, end_idx + 1):
        cum = _effective_cum_at(bars, index_close_by_date, series, idx, window)
        if cum is not None and _meets_trigger(cum, trigger_threshold):
            return series.dates[idx]
    return None


def _daily_row(
    *,
    symbol: str,
    bars: list[dict[str, Any]],
    index_close_by_date: dict[str, float],
    series: AlignedSeries,
    idx: int,
    day_offset: int,
    track_10: bool,
    track_30: bool,
) -> AbnormalDailyLimitRow:
    cum10 = _window_cum_at(bars, index_close_by_date, series, idx, WINDOW_10) if track_10 else None
    cum30 = _window_cum_at(bars, index_close_by_date, series, idx, WINDOW_30) if track_30 else None
    max_dev_10 = _max_daily_dev_room(series.deviations, idx, WINDOW_10, TRIGGER_THRESHOLD_10) if track_10 else None
    max_dev_30 = _max_daily_dev_room(series.deviations, idx, WINDOW_30, TRIGGER_THRESHOLD_30) if track_30 else None
    board_cap = _board_limit_pct(symbol)

    def _stock_cap(max_dev: float | None) -> float | None:
        if max_dev is None:
            return None
        return min(max_dev, board_cap)

    cap10 = _stock_cap(max_dev_10)
    cap30 = _stock_cap(max_dev_30)
    caps = [value for value in (cap10, cap30) if value is not None]
    effective = min(caps) if caps else None

    prev10 = _window_cum_at(bars, index_close_by_date, series, idx - 1, WINDOW_10) if idx > 0 and track_10 else None
    prev30 = _window_cum_at(bars, index_close_by_date, series, idx - 1, WINDOW_30) if idx > 0 and track_30 else None
    reset_10 = bool(
        track_10
        and prev10 is not None
        and prev10 >= WARN_THRESHOLD_10
        and cum10 is not None
        and cum10 < WARN_THRESHOLD_10,
    )
    reset_30 = bool(
        track_30
        and prev30 is not None
        and prev30 >= WARN_THRESHOLD_30
        and cum30 is not None
        and cum30 < WARN_THRESHOLD_30,
    )

    return AbnormalDailyLimitRow(
        date=series.dates[idx],
        day_offset=day_offset,
        cum_dev_10=cum10,
        cum_dev_30=cum30,
        max_dev_10=max_dev_10,
        max_dev_30=max_dev_30,
        max_stock_pct_10=cap10,
        max_stock_pct_30=cap30,
        effective_max_stock_pct=effective,
        actual_deviation=series.deviations[idx],
        actual_stock_pct=series.stock_pcts[idx],
        actual_index_pct=series.index_pcts[idx],
        reset_10=reset_10,
        reset_30=reset_30,
    )


def _collect_episode(
    *,
    symbol: str,
    name: str,
    board: str | None,
    kind: str,
    status: str,
    entry_date: str,
    trigger_date: str | None,
    as_of_date: str,
    cum_deviation: float,
    threshold: float,
    warn_threshold: float,
    benchmark_symbol: str,
    benchmark_name: str,
    bars: list[dict[str, Any]],
    index_close_by_date: dict[str, float],
    series: AlignedSeries,
    start_idx: int,
    end_idx: int,
    track_10: bool,
    track_30: bool,
) -> AbnormalMovementEvent:
    episode_day = max(1, end_idx - start_idx + 1)
    daily_limits = [
        _daily_row(
            symbol=symbol,
            bars=bars,
            index_close_by_date=index_close_by_date,
            series=series,
            idx=idx,
            day_offset=idx - start_idx,
            track_10=track_10,
            track_30=track_30,
        )
        for idx in range(start_idx, end_idx + 1)
    ]
    return AbnormalMovementEvent(
        symbol=symbol,
        name=name,
        board=board,
        kind=kind,
        status=status,
        entry_date=entry_date,
        trigger_date=trigger_date,
        as_of_date=as_of_date,
        cum_deviation=cum_deviation,
        threshold=threshold,
        warn_threshold=warn_threshold,
        benchmark_symbol=benchmark_symbol,
        benchmark_name=benchmark_name,
        episode_day=episode_day,
        daily_limits=daily_limits,
    )


def _scan_window_episodes(
    *,
    symbol: str,
    name: str,
    board: str | None,
    bars: list[dict[str, Any]],
    index_close_by_date: dict[str, float],
    series: AlignedSeries,
    date_from: str,
    date_to: str,
    include_warnings: bool,
    window: int,
    warn_threshold: float,
    trigger_threshold: float,
    kind: str,
    track_10: bool,
    track_30: bool,
    snapshot_only: bool = False,
    cooling_days: int = 3,
) -> list[AbnormalMovementEvent]:
    benchmark_symbol, benchmark_name = resolve_benchmark_symbol(symbol)
    events: list[AbnormalMovementEvent] = []
    emitted: set[tuple[str, str, str]] = set()
    as_of_idx = _resolve_as_of_idx(series, date_to)
    if as_of_idx < 0:
        return []

    def _in_zone(idx: int) -> bool:
        cum = _effective_cum_at(bars, index_close_by_date, series, idx, window)
        if cum is None:
            return False
        if _meets_trigger(cum, trigger_threshold):
            return True
        return include_warnings and _meets_warn(cum, warn_threshold)

    def _append_episode(start_idx: int, end_idx: int) -> None:
        if start_idx > end_idx:
            return
        as_of_date = series.dates[end_idx]
        if as_of_date < date_from or as_of_date > date_to:
            return
        entry = series.dates[start_idx]
        signature = (kind, entry, as_of_date)
        if signature in emitted:
            return
        cum = _effective_cum_at(bars, index_close_by_date, series, end_idx, window) or 0.0
        status = "triggered" if _meets_trigger(cum, trigger_threshold) else "warning"
        if status == "warning" and not include_warnings:
            return
        trigger_date = _find_first_trigger_date(
            bars,
            index_close_by_date,
            series,
            start_idx,
            end_idx,
            window,
            trigger_threshold,
        )
        emitted.add(signature)
        events.append(
            _collect_episode(
                symbol=symbol,
                name=name,
                board=board,
                kind=kind,
                status=status,
                entry_date=entry,
                trigger_date=trigger_date,
                as_of_date=as_of_date,
                cum_deviation=cum,
                threshold=trigger_threshold,
                warn_threshold=warn_threshold,
                benchmark_symbol=benchmark_symbol,
                benchmark_name=benchmark_name,
                bars=bars,
                index_close_by_date=index_close_by_date,
                series=series,
                start_idx=start_idx,
                end_idx=end_idx,
                track_10=track_10,
                track_30=track_30,
            )
        )

    # 1) 截至日仍在异动区的票（含“压着异动走”、扫描区间内无新穿越的）
    if _in_zone(as_of_idx):
        start_idx = _find_episode_start(
            bars,
            index_close_by_date,
            series,
            as_of_idx,
            window,
            warn_threshold,
        )
        _append_episode(start_idx, as_of_idx)

    if snapshot_only:
        return events

    # 2) 扫描区间内已结束的历史异动段（截至日仍异动的由步骤 1 覆盖）
    # 冷却期(cooling_days):异动段暂时退出 zone 后,在 cooling_days 个交易日内若再次入区,
    # 则视为同一段的延续(合并),避免边界抖动把段切碎。
    active_start: int | None = None
    cooling_remaining = 0  # >0 表示正处于冷却窗口内,等待是否重新入区
    for idx in range(len(series.dates)):
        day = series.dates[idx]
        if day > date_to:
            break
        if _in_zone(idx):
            if active_start is None:
                active_start = _find_episode_start(
                    bars,
                    index_close_by_date,
                    series,
                    idx,
                    window,
                    warn_threshold,
                )
            # 重新入区,取消冷却
            cooling_remaining = 0
            continue
        # 不在 zone 内
        if active_start is not None:
            if cooling_days > 0 and cooling_remaining < cooling_days:
                # 冷却窗口内,暂不结束段,等待是否重新入区
                cooling_remaining += 1
                continue
            # 冷却窗口已耗尽或未启用,正式结束段
            end_idx = idx - cooling_remaining  # 回退到冷却前的最后一个 in_zone 日
            if end_idx >= active_start:
                _append_episode(active_start, end_idx)
            active_start = None
            cooling_remaining = 0

    # 循环结束时若仍在段内(含冷却窗口),flush 最后一段
    if active_start is not None:
        end_idx = len(series.dates) - 1 - cooling_remaining
        if end_idx >= active_start and series.dates[end_idx] <= date_to:
            _append_episode(active_start, end_idx)

    return events


def analyze_symbol_abnormal_events(
    *,
    symbol: str,
    name: str,
    bars: list[dict[str, Any]],
    index_close_by_date: dict[str, float],
    date_from: str,
    date_to: str,
    include_warnings: bool = True,
    snapshot_only: bool = False,
    trading_dates: list[str] | None = None,
    cooling_days: int = 3,
) -> list[AbnormalMovementEvent]:
    series = _build_aligned_series(bars, index_close_by_date, symbol, trading_dates=trading_dates)
    if series is None:
        return []

    events: list[AbnormalMovementEvent] = []
    board = detect_primary_board(symbol)
    events.extend(
        _scan_window_episodes(
            symbol=symbol,
            name=name,
            board=board,
            bars=bars,
            index_close_by_date=index_close_by_date,
            series=series,
            date_from=date_from,
            date_to=date_to,
            include_warnings=include_warnings,
            window=WINDOW_10,
            warn_threshold=WARN_THRESHOLD_10,
            trigger_threshold=TRIGGER_THRESHOLD_10,
            kind="10d",
            track_10=True,
            track_30=False,
            snapshot_only=snapshot_only,
            cooling_days=cooling_days,
        )
    )
    events.extend(
        _scan_window_episodes(
            symbol=symbol,
            name=name,
            board=board,
            bars=bars,
            index_close_by_date=index_close_by_date,
            series=series,
            date_from=date_from,
            date_to=date_to,
            include_warnings=include_warnings,
            window=WINDOW_30,
            warn_threshold=WARN_THRESHOLD_30,
            trigger_threshold=TRIGGER_THRESHOLD_30,
            kind="30d",
            track_10=False,
            track_30=True,
            snapshot_only=snapshot_only,
            cooling_days=cooling_days,
        )
    )

    deduped: list[AbnormalMovementEvent] = []
    seen: set[tuple[str, str, str, str]] = set()
    for event in events:
        key = (event.kind, event.entry_date, event.as_of_date, event.status)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def scan_abnormal_movement(
    *,
    tdx_root: str,
    resolve_name: Callable[[str], str],
    market_data_source: str,
    akshare_cache_dir: str,
    date_from: str,
    date_to: str,
    board_filters: list[str] | None = None,
    markets: list[str] | None = None,
    include_warnings: bool = True,
    scan_mode: Literal["snapshot", "full"] = "snapshot",
    parallel_workers: int = 0,
    cooling_days: int = 3,
) -> tuple[list[AbnormalMovementEvent], list[str], int, float, dict[str, Any]]:
    start_ts = time.monotonic()
    market_list = markets or ["sh", "sz", "bj"]
    snapshot_only = scan_mode != "full"
    t_prep = time.monotonic()
    trading_dates = _reference_trading_dates(
        tdx_root=tdx_root,
        market_data_source=market_data_source,
        akshare_cache_dir=akshare_cache_dir,
        date_from=date_from,
        date_to=date_to,
    )
    bars_needed = _resolve_bars_needed(
        date_from=date_from,
        date_to=date_to,
        trading_dates=trading_dates,
        snapshot_only=snapshot_only,
    )
    benchmark_maps = _load_benchmark_close_maps(
        tdx_root=tdx_root,
        market_data_source=market_data_source,
        akshare_cache_dir=akshare_cache_dir,
        bars_needed=bars_needed,
    )
    prep_sec = time.monotonic() - t_prep
    t_load = time.monotonic()
    symbol_bars, symbol_names, total_scanned = _load_symbol_bars_for_abnormal_scan(
        tdx_root=tdx_root,
        resolve_name=resolve_name,
        market_data_source=market_data_source,
        akshare_cache_dir=akshare_cache_dir,
        board_filters=list(board_filters or []),
        bars_needed=bars_needed,
        date_from=date_from,
        date_to=date_to,
        markets=market_list,
        parallel_workers=parallel_workers,
    )
    load_sec = time.monotonic() - t_load
    t_analyze = time.monotonic()
    events: list[AbnormalMovementEvent] = []
    symbols_analyzed = 0
    symbols_skipped_no_index = 0
    for symbol, bars in symbol_bars.items():
        index_close = resolve_index_close_for_symbol(benchmark_maps, symbol)
        if not index_close:
            symbols_skipped_no_index += 1
            continue
        symbols_analyzed += 1
        name = symbol_names.get(symbol) or (resolve_name(symbol) if resolve_name else symbol[2:])
        events.extend(
            analyze_symbol_abnormal_events(
                symbol=symbol,
                name=name,
                bars=bars,
                index_close_by_date=index_close,
                date_from=date_from,
                date_to=date_to,
                include_warnings=include_warnings,
                snapshot_only=snapshot_only,
                trading_dates=trading_dates,
                cooling_days=cooling_days,
            )
        )
    trade_date_to = trading_dates[-1] if trading_dates else date_to
    events.sort(
        key=lambda row: (
            0 if row.status == "triggered" else 1,
            0 if row.kind == "10d" else 1,
            row.as_of_date,
            row.symbol,
        ),
        reverse=True,
    )
    analyze_sec = time.monotonic() - t_analyze
    elapsed = time.monotonic() - start_ts
    return events, trading_dates, total_scanned, elapsed, {
        "algorithm_version": ALGORITHM_VERSION,
        "symbols_analyzed": symbols_analyzed,
        "symbols_skipped_no_index": symbols_skipped_no_index,
        "trade_date_to": trade_date_to,
        "scan_mode": scan_mode,
        "bars_needed": bars_needed,
        "parallel_workers": _resolve_parallel_workers(parallel_workers),
        "prep_sec": round(prep_sec, 2),
        "load_sec": round(load_sec, 2),
        "analyze_sec": round(analyze_sec, 2),
    }
