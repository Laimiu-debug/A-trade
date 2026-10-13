"""Persistent daily cache for trend-leader rankings (frozen historical days)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def build_daily_cache_key(
    *,
    eval_date: str,
    window_days: int,
    daily_top_n: int,
    board_filters: list[str],
    min_amount_avg: float,
) -> str:
    boards = ",".join(sorted({str(item).strip().lower() for item in board_filters if str(item).strip()}))
    return f"{eval_date}|w{window_days}|n{daily_top_n}|{boards}|a{int(min_amount_avg)}"


class TrendLeadersDailyCache:
    def __init__(self, cache_path: Path) -> None:
        self._path = cache_path
        self._entries: dict[str, dict[str, Any]] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._path.exists():
            self._entries = {}
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            raw = payload.get("entries") if isinstance(payload, dict) else None
            self._entries = raw if isinstance(raw, dict) else {}
        except Exception:
            self._entries = {}

    def get(
        self,
        *,
        eval_date: str,
        window_days: int,
        daily_top_n: int,
        board_filters: list[str],
        min_amount_avg: float,
    ) -> list[str] | None:
        self._ensure_loaded()
        key = build_daily_cache_key(
            eval_date=eval_date,
            window_days=window_days,
            daily_top_n=daily_top_n,
            board_filters=board_filters,
            min_amount_avg=min_amount_avg,
        )
        row = self._entries.get(key)
        if not isinstance(row, dict):
            return None
        symbols = row.get("symbols")
        if not isinstance(symbols, list):
            return None
        normalized = [str(item).strip().lower() for item in symbols if str(item).strip()]
        return normalized if normalized else None

    def set(
        self,
        *,
        eval_date: str,
        window_days: int,
        daily_top_n: int,
        board_filters: list[str],
        min_amount_avg: float,
        symbols: list[str],
    ) -> None:
        self._ensure_loaded()
        key = build_daily_cache_key(
            eval_date=eval_date,
            window_days=window_days,
            daily_top_n=daily_top_n,
            board_filters=board_filters,
            min_amount_avg=min_amount_avg,
        )
        self._entries[key] = {
            "symbols": [str(item).strip().lower() for item in symbols if str(item).strip()],
            "computed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def save(self) -> None:
        self._ensure_loaded()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "entries": self._entries}
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
