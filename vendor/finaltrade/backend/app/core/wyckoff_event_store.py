from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
import time
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any


def build_wyckoff_params_hash(window_days: int, *, profile_hash: str = "") -> str:
    payload = {
        "window_days": int(window_days),
        "profile_hash": str(profile_hash or "").strip(),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


_EXTRA_SCORE_KEYS: tuple[str, ...] = (
    "health_score",
    "event_score",
    "event_grade",
    "candle_quality_score",
    "cost_center_shift_score",
    "weekly_context_score",
    "weekly_context_multiplier",
    "risk_score",
    "confirmation_status",
    "phase_context_score",
    "event_recency_score",
    "event_background_score",
    "event_position_score",
    "event_vol_price_score",
    "event_confirmation_score",
    "slope_stability",
    "volatility_stability",
    "pullback_quality",
    "event_confirmation_map",
    "event_grade_map",
)


class WyckoffEventStore:
    def __init__(
        self,
        db_path: Path,
        *,
        enabled: bool,
        read_only: bool,
    ) -> None:
        self._db_path = Path(db_path)
        self._enabled = bool(enabled)
        self._read_only = bool(read_only)
        self._lock = RLock()
        self._runtime_cache: dict[tuple[str, str, int, str, str, str, str], dict[str, Any]] = {}
        self._runtime_cache_limit = self._resolve_runtime_cache_limit()
        if self._enabled and not self._read_only:
            self._init_db()

    @staticmethod
    def _resolve_runtime_cache_limit() -> int:
        raw = str(os.getenv("TDX_TREND_WYCKOFF_RUNTIME_CACHE_LIMIT", "")).strip()
        if not raw:
            return 10_000
        try:
            value = int(raw)
        except Exception:
            return 10_000
        return max(0, value)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def read_only(self) -> bool:
        return self._read_only

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def runtime_cache_size(self) -> int:
        return len(self._runtime_cache)

    def clear_runtime_cache(self) -> None:
        with self._lock:
            self._runtime_cache.clear()

    def _prune_runtime_cache(self) -> None:
        with self._lock:
            self._prune_runtime_cache_locked()

    def _prune_runtime_cache_locked(self) -> None:
        if self._runtime_cache_limit <= 0:
            self._runtime_cache.clear()
            return
        while len(self._runtime_cache) > self._runtime_cache_limit:
            stale_key = next(iter(self._runtime_cache))
            self._runtime_cache.pop(stale_key, None)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self._db_path), timeout=30.0)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wyckoff_daily_events (
                    symbol TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    window_days INTEGER NOT NULL,
                    phase TEXT NOT NULL,
                    signal TEXT NOT NULL,
                    sequence_ok INTEGER NOT NULL,
                    event_count INTEGER NOT NULL,
                    quality_score REAL NOT NULL,
                    event_dates_json TEXT NOT NULL,
                    event_chain_json TEXT NOT NULL,
                    events_json TEXT NOT NULL,
                    risk_events_json TEXT NOT NULL,
                    trend_score REAL NOT NULL,
                    phase_score REAL NOT NULL,
                    structure_score REAL NOT NULL,
                    volatility_score REAL NOT NULL,
                    event_strength_score REAL NOT NULL,
                    structure_hhh TEXT NOT NULL,
                    trigger_date TEXT NOT NULL,
                    algo_version TEXT NOT NULL,
                    data_source TEXT NOT NULL,
                    data_version TEXT NOT NULL,
                    params_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (
                        symbol,
                        trade_date,
                        window_days,
                        algo_version,
                        data_source,
                        data_version,
                        params_hash
                    )
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_wyckoff_daily_events_trade_symbol
                ON wyckoff_daily_events (trade_date, symbol)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_wyckoff_daily_events_symbol_trade
                ON wyckoff_daily_events (symbol, trade_date)
                """
            )
            conn.commit()
            # 迁移：添加 extra_scores_json 列（存储 health_score / event_score / candle_quality_score 等）
            try:
                conn.execute("ALTER TABLE wyckoff_daily_events ADD COLUMN extra_scores_json TEXT NOT NULL DEFAULT '{}'")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # 列已存在

    @staticmethod
    def _safe_float(raw: Any, default: float = 0.0) -> float:
        try:
            value = float(raw)
        except Exception:
            return default
        if value != value:
            return default
        return value

    @staticmethod
    def _safe_int(raw: Any, default: int = 0) -> int:
        try:
            return int(raw)
        except Exception:
            return default

    @staticmethod
    def _json_dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _json_load_dict(raw: Any) -> dict[str, str]:
        try:
            payload = json.loads(str(raw or "{}"))
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        out: dict[str, str] = {}
        for key, value in payload.items():
            key_text = str(key).strip()
            value_text = str(value).strip()
            if not key_text or not value_text:
                continue
            out[key_text] = value_text
        return out

    @staticmethod
    def _json_load_list(raw: Any) -> list[Any]:
        try:
            payload = json.loads(str(raw or "[]"))
        except Exception:
            return []
        if not isinstance(payload, list):
            return []
        return payload

    @staticmethod
    def _normalize_event_count(snapshot: dict[str, Any]) -> int:
        chain = snapshot.get("event_chain")
        if isinstance(chain, list):
            valid = 0
            for row in chain:
                if isinstance(row, dict) and str(row.get("event", "")).strip():
                    valid += 1
            if valid > 0:
                return valid
        events = snapshot.get("events")
        risks = snapshot.get("risk_events")
        count = len(events) if isinstance(events, list) else 0
        count += len(risks) if isinstance(risks, list) else 0
        return count

    def _runtime_key(
        self,
        *,
        symbol: str,
        trade_date: str,
        window_days: int,
        algo_version: str,
        data_source: str,
        data_version: str,
        params_hash: str,
    ) -> tuple[str, str, int, str, str, str, str]:
        return (
            str(symbol).strip().lower(),
            str(trade_date).strip(),
            int(window_days),
            str(algo_version).strip(),
            str(data_source).strip(),
            str(data_version).strip(),
            str(params_hash).strip(),
        )

    def get_snapshot(
        self,
        *,
        symbol: str,
        trade_date: str,
        window_days: int,
        algo_version: str,
        data_source: str,
        data_version: str,
        params_hash: str,
    ) -> dict[str, Any] | None:
        if not self._enabled:
            return None
        key = self._runtime_key(
            symbol=symbol,
            trade_date=trade_date,
            window_days=window_days,
            algo_version=algo_version,
            data_source=data_source,
            data_version=data_version,
            params_hash=params_hash,
        )
        cached = self._runtime_cache.get(key)
        if cached is not None:
            return copy.deepcopy(cached)
        if not self._db_path.exists():
            return None

        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT
                        phase,
                        signal,
                        sequence_ok,
                        event_count,
                        quality_score,
                        event_dates_json,
                        event_chain_json,
                        events_json,
                        risk_events_json,
                        trend_score,
                        phase_score,
                        structure_score,
                        volatility_score,
                        event_strength_score,
                        structure_hhh,
                        trigger_date,
                        extra_scores_json
                    FROM wyckoff_daily_events
                    WHERE symbol=? AND trade_date=? AND window_days=?
                      AND algo_version=? AND data_source=? AND data_version=? AND params_hash=?
                    LIMIT 1
                    """,
                    key,
                ).fetchone()
        except Exception:
            return None

        if not row:
            return None

        snapshot: dict[str, Any] = {
            "events": [str(item).strip() for item in self._json_load_list(row[7]) if str(item).strip()],
            "risk_events": [str(item).strip() for item in self._json_load_list(row[8]) if str(item).strip()],
            "event_dates": self._json_load_dict(row[5]),
            "event_chain": [
                item
                for item in self._json_load_list(row[6])
                if isinstance(item, dict)
                and str(item.get("event", "")).strip()
                and str(item.get("date", "")).strip()
            ],
            "sequence_ok": bool(self._safe_int(row[2], 0)),
            "entry_quality_score": self._safe_float(row[4], 0.0),
            "phase": str(row[0] or "阶段未明"),
            "signal": str(row[1] or ""),
            "trigger_date": str(row[15] or trade_date),
            "phase_hint": "",
            "structure_hhh": str(row[14] or "-"),
            "event_strength_score": self._safe_float(row[13], 0.0),
            "phase_score": self._safe_float(row[10], 0.0),
            "structure_score": self._safe_float(row[11], 0.0),
            "trend_score": self._safe_float(row[9], 0.0),
            "volatility_score": self._safe_float(row[12], 0.0),
        }
        # 合并 extra_scores_json 中的扩展分数字段
        extra_raw = row[16] if len(row) > 16 else "{}"
        extra_scores = self._json_load_dict(extra_raw) if extra_raw else {}
        for extra_key in _EXTRA_SCORE_KEYS:
            if extra_key in extra_scores:
                snapshot[extra_key] = extra_scores[extra_key]
        with self._lock:
            self._runtime_cache[key] = copy.deepcopy(snapshot)
            self._prune_runtime_cache_locked()
        return snapshot

    def upsert_snapshot(
        self,
        *,
        symbol: str,
        trade_date: str,
        window_days: int,
        algo_version: str,
        data_source: str,
        data_version: str,
        params_hash: str,
        snapshot: dict[str, Any],
    ) -> bool:
        if (not self._enabled) or self._read_only:
            return False

        key = self._runtime_key(
            symbol=symbol,
            trade_date=trade_date,
            window_days=window_days,
            algo_version=algo_version,
            data_source=data_source,
            data_version=data_version,
            params_hash=params_hash,
        )
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        events = [str(item).strip() for item in snapshot.get("events", []) if str(item).strip()]
        risk_events = [str(item).strip() for item in snapshot.get("risk_events", []) if str(item).strip()]
        event_dates = snapshot.get("event_dates", {})
        if not isinstance(event_dates, dict):
            event_dates = {}
        clean_event_dates: dict[str, str] = {}
        for event_code, event_date in event_dates.items():
            code_text = str(event_code).strip()
            date_text = str(event_date).strip()
            if code_text and date_text:
                clean_event_dates[code_text] = date_text
        event_chain = snapshot.get("event_chain", [])
        if not isinstance(event_chain, list):
            event_chain = []
        clean_event_chain: list[dict[str, str]] = []
        for row in event_chain:
            if not isinstance(row, dict):
                continue
            event_text = str(row.get("event", "")).strip()
            date_text = str(row.get("date", "")).strip()
            if not event_text or not date_text:
                continue
            clean_event_chain.append(
                {
                    "event": event_text,
                    "date": date_text,
                    "category": str(row.get("category", "other")).strip() or "other",
                }
            )

        record_values = (
            key[0],
            key[1],
            key[2],
            str(snapshot.get("phase", "阶段未明") or "阶段未明"),
            str(snapshot.get("signal", "") or ""),
            1 if bool(snapshot.get("sequence_ok")) else 0,
            int(max(0, self._normalize_event_count(snapshot))),
            self._safe_float(snapshot.get("entry_quality_score"), 0.0),
            self._json_dump(clean_event_dates),
            self._json_dump(clean_event_chain),
            self._json_dump(events),
            self._json_dump(risk_events),
            self._safe_float(snapshot.get("trend_score"), 0.0),
            self._safe_float(snapshot.get("phase_score"), 0.0),
            self._safe_float(snapshot.get("structure_score"), 0.0),
            self._safe_float(snapshot.get("volatility_score"), 0.0),
            self._safe_float(snapshot.get("event_strength_score"), 0.0),
            str(snapshot.get("structure_hhh", "-") or "-"),
            str(snapshot.get("trigger_date", key[1]) or key[1]),
            key[3],
            key[4],
            key[5],
            key[6],
            self._json_dump({k: snapshot[k] for k in _EXTRA_SCORE_KEYS if k in snapshot}),
            now_text,
            now_text,
        )

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                with self._lock:
                    self._db_path.parent.mkdir(parents=True, exist_ok=True)
                    with self._connect() as conn:
                        conn.execute(
                            """
                            INSERT INTO wyckoff_daily_events (
                                symbol,
                                trade_date,
                                window_days,
                                phase,
                                signal,
                                sequence_ok,
                                event_count,
                                quality_score,
                                event_dates_json,
                                event_chain_json,
                                events_json,
                                risk_events_json,
                                trend_score,
                                phase_score,
                                structure_score,
                                volatility_score,
                                event_strength_score,
                                structure_hhh,
                                trigger_date,
                                algo_version,
                                data_source,
                                data_version,
                                params_hash,
                                extra_scores_json,
                                created_at,
                                updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(
                                symbol,
                                trade_date,
                                window_days,
                                algo_version,
                                data_source,
                                data_version,
                                params_hash
                            ) DO UPDATE SET
                                phase=excluded.phase,
                                signal=excluded.signal,
                                sequence_ok=excluded.sequence_ok,
                                event_count=excluded.event_count,
                                quality_score=excluded.quality_score,
                                event_dates_json=excluded.event_dates_json,
                                event_chain_json=excluded.event_chain_json,
                                events_json=excluded.events_json,
                                risk_events_json=excluded.risk_events_json,
                                trend_score=excluded.trend_score,
                                phase_score=excluded.phase_score,
                                structure_score=excluded.structure_score,
                                volatility_score=excluded.volatility_score,
                                event_strength_score=excluded.event_strength_score,
                                structure_hhh=excluded.structure_hhh,
                                trigger_date=excluded.trigger_date,
                                extra_scores_json=excluded.extra_scores_json,
                                updated_at=excluded.updated_at
                            """,
                            record_values,
                        )
                        conn.commit()
                break
            except sqlite3.OperationalError:
                if attempt >= max_attempts:
                    return False
                time.sleep(0.05 * attempt)
            except Exception:
                return False

        with self._lock:
            self._runtime_cache[key] = copy.deepcopy(snapshot)
            self._prune_runtime_cache_locked()
        return True

    def count_records(self) -> int:
        if not self._enabled:
            return 0
        if not self._db_path.exists():
            return 0
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT COUNT(1) FROM wyckoff_daily_events").fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        except Exception:
            return 0
