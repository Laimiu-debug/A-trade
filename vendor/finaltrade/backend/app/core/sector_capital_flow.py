"""Sector capital flow analysis from TDX industry index data."""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any

from .sector_analyzer import SECTOR_CODES, load_sector_candles


def _day_int_to_str(day: int) -> str:
    text = str(int(day))
    if len(text) != 8:
        return text
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def _normalize_bar(row: dict[str, Any]) -> dict[str, Any]:
    day = row.get("date")
    if isinstance(day, int):
        date = _day_int_to_str(day)
    else:
        date = str(day)
    return {
        "date": date,
        "close": float(row.get("close", 0.0) or 0.0),
        "amount": float(row.get("amount", 0.0) or 0.0),
        "vol": float(row.get("vol", 0.0) or 0.0),
    }


def _merge_sector_bars(code_bars: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    by_date: dict[str, dict[str, list[float]]] = {}
    for bars in code_bars:
        for raw in bars:
            row = _normalize_bar(raw)
            if row["close"] <= 0:
                continue
            bucket = by_date.setdefault(row["date"], {"close": [], "amount": [], "vol": []})
            bucket["close"].append(row["close"])
            bucket["amount"].append(row["amount"])
            bucket["vol"].append(row["vol"])
    merged: list[dict[str, Any]] = []
    for date in sorted(by_date.keys()):
        bucket = by_date[date]
        merged.append(
            {
                "date": date,
                "close": statistics.mean(bucket["close"]),
                "amount": sum(bucket["amount"]),
                "vol": sum(bucket["vol"]),
            }
        )
    return merged


def load_sector_series_by_name(tdx_data_path: str) -> dict[str, list[dict[str, Any]]]:
    raw = load_sector_candles(tdx_data_path)
    result: dict[str, list[dict[str, Any]]] = {}
    for name, codes in SECTOR_CODES.items():
        code_bars = [raw[code] for code in codes if code in raw]
        if not code_bars:
            continue
        merged = _merge_sector_bars(code_bars)
        if merged:
            result[name] = merged
    return result


def _collect_trading_dates(sector_series: dict[str, list[dict[str, Any]]], date_from: str, date_to: str) -> list[str]:
    dates: set[str] = set()
    for bars in sector_series.values():
        for bar in bars:
            day = str(bar["date"])
            if date_from <= day <= date_to:
                dates.add(day)
    return sorted(dates)


def _find_index(bars: list[dict[str, Any]], target_date: str) -> int | None:
    for idx, bar in enumerate(bars):
        if str(bar["date"]) == target_date:
            return idx
    return None


def _mean_amount(bars: list[dict[str, Any]], end_idx: int, window: int) -> float:
    start = max(0, end_idx - window + 1)
    slice_bars = bars[start : end_idx + 1]
    if not slice_bars:
        return 0.0
    return sum(float(item["amount"]) for item in slice_bars) / len(slice_bars)


@dataclass
class SectorDayMetrics:
    sector: str
    date: str
    close: float
    amount: float
    return_pct: float
    amount_delta: float
    flow_score: float
    rank_return: int = 0
    rank_flow: int = 0


@dataclass
class SectorSeriesPoint:
    date: str
    return_pct: float
    flow_score: float


@dataclass
class SectorSeriesRow:
    sector: str
    points: list[SectorSeriesPoint] = field(default_factory=list)


@dataclass
class SectorLeaderSummary:
    sector: str
    leader_days: int = 0
    max_return_pct: float = 0.0
    avg_flow_score: float = 0.0
    first_leader_date: str | None = None
    last_leader_date: str | None = None


def scan_sector_capital_flow(
    *,
    tdx_data_path: str,
    date_from: str,
    date_to: str,
    daily_top_n: int = 5,
    flow_window: int = 5,
) -> tuple[list[SectorDayMetrics], list[SectorSeriesRow], list[SectorLeaderSummary], list[str], float]:
    start_ts = time.monotonic()
    sector_series = load_sector_series_by_name(tdx_data_path)
    trading_dates = _collect_trading_dates(sector_series, date_from, date_to)
    if not trading_dates:
        return [], [], [], [], time.monotonic() - start_ts

    all_rows: list[SectorDayMetrics] = []
    leader_count: dict[str, int] = {}
    leader_first: dict[str, str] = {}
    leader_last: dict[str, str] = {}
    leader_sectors: set[str] = set()
    flow_scores_by_sector: dict[str, list[float]] = {}

    for eval_date in trading_dates:
        day_rows: list[SectorDayMetrics] = []
        for sector, bars in sector_series.items():
            idx = _find_index(bars, eval_date)
            if idx is None:
                continue
            close = float(bars[idx]["close"])
            amount = float(bars[idx]["amount"])
            prev_close = float(bars[idx - 1]["close"]) if idx > 0 else close
            prev_amount = float(bars[idx - 1]["amount"]) if idx > 0 else amount
            return_pct = ((close - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0
            amount_delta = amount - prev_amount
            mean_amt = _mean_amount(bars, idx, flow_window)
            flow_score = (amount / mean_amt - 1.0) * 100.0 if mean_amt > 0 else 0.0
            row = SectorDayMetrics(
                sector=sector,
                date=eval_date,
                close=close,
                amount=round(amount, 2),
                return_pct=round(return_pct, 2),
                amount_delta=round(amount_delta, 2),
                flow_score=round(flow_score, 2),
            )
            day_rows.append(row)

        if not day_rows:
            continue

        by_return = sorted(day_rows, key=lambda item: item.return_pct, reverse=True)
        by_flow = sorted(day_rows, key=lambda item: item.flow_score, reverse=True)
        for rank, item in enumerate(by_return, start=1):
            item.rank_return = rank
        for rank, item in enumerate(by_flow, start=1):
            item.rank_flow = rank

        top_by_flow = {item.sector for item in by_flow[: max(1, daily_top_n)]}
        for item in day_rows:
            if item.sector in top_by_flow:
                leader_sectors.add(item.sector)
                leader_count[item.sector] = leader_count.get(item.sector, 0) + 1
                leader_first.setdefault(item.sector, eval_date)
                leader_last[item.sector] = eval_date
            flow_scores_by_sector.setdefault(item.sector, []).append(item.flow_score)
            all_rows.append(item)

    series_rows: list[SectorSeriesRow] = []
    summaries: list[SectorLeaderSummary] = []

    for sector in leader_sectors:
        bars = sector_series.get(sector, [])
        start_idx = _find_index(bars, date_from)
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

        points: list[SectorSeriesPoint] = []
        max_return = 0.0
        for eval_date in trading_dates:
            idx = _find_index(bars, eval_date)
            if idx is None:
                continue
            ret_pct = (float(bars[idx]["close"]) - base_close) / base_close * 100.0
            max_return = max(max_return, ret_pct)
            flow_score = next((row.flow_score for row in all_rows if row.sector == sector and row.date == eval_date), 0.0)
            points.append(
                SectorSeriesPoint(
                    date=eval_date,
                    return_pct=round(ret_pct, 2),
                    flow_score=flow_score,
                )
            )
        series_rows.append(SectorSeriesRow(sector=sector, points=points))
        summaries.append(
            SectorLeaderSummary(
                sector=sector,
                leader_days=leader_count.get(sector, 0),
                max_return_pct=round(max_return, 2),
                avg_flow_score=round(
                    statistics.mean(flow_scores_by_sector.get(sector, [0.0])),
                    2,
                ),
                first_leader_date=leader_first.get(sector),
                last_leader_date=leader_last.get(sector),
            )
        )

    summaries.sort(key=lambda item: (item.leader_days, item.max_return_pct), reverse=True)
    series_rows.sort(
        key=lambda row: next((item.leader_days for item in summaries if item.sector == row.sector), 0),
        reverse=True,
    )
    elapsed = time.monotonic() - start_ts
    return all_rows, series_rows, summaries, trading_dates, elapsed
