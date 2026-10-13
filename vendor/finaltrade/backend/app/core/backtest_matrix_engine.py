from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Callable

import numpy as np

from ..models import CandlePoint


@dataclass(slots=True)
class MatrixBundle:
    dates: list[str]
    symbols: list[str]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    valid_mask: np.ndarray

    def shape(self) -> tuple[int, int]:
        return int(self.close.shape[0]), int(self.close.shape[1])

    def symbol_to_index(self) -> dict[str, int]:
        return {symbol: idx for idx, symbol in enumerate(self.symbols)}


class BacktestMatrixEngine:
    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self._cache_dir = self._resolve_cache_dir(cache_dir)
        self._runtime_cache: dict[str, tuple[float, MatrixBundle]] = {}
        self._runtime_cache_lock = RLock()
        self._incremental_manifest_lock = RLock()
        self._build_meta_lock = RLock()
        self._build_meta_by_cache_key: dict[str, dict[str, object]] = {}

    @staticmethod
    def _resolve_cache_dir(cache_dir: str | Path | None) -> Path:
        if cache_dir is not None and str(cache_dir).strip():
            return Path(str(cache_dir).strip()).expanduser()
        env_value = str(os.getenv('TDX_TREND_BACKTEST_MATRIX_CACHE_DIR', '')).strip()
        if env_value:
            return Path(env_value).expanduser()
        return Path.home() / '.tdx-trend' / 'backtest-matrix-cache'

    @staticmethod
    def _parse_date(value: str) -> datetime:
        return datetime.strptime(value, '%Y-%m-%d')

    @staticmethod
    def _resolve_build_workers() -> int:
        raw = str(os.getenv('TDX_TREND_BACKTEST_MATRIX_BUILD_WORKERS', '')).strip()
        if raw:
            try:
                return max(1, int(raw))
            except Exception:
                pass
        cpu_count = os.cpu_count() or 4
        return max(1, min(8, cpu_count))

    @staticmethod
    def _is_runtime_cache_enabled() -> bool:
        raw = str(os.getenv('TDX_TREND_BACKTEST_MATRIX_RUNTIME_CACHE', '')).strip().lower()
        if not raw:
            return True
        if raw in {'1', 'true', 'yes', 'on', 'y'}:
            return True
        if raw in {'0', 'false', 'no', 'off', 'n'}:
            return False
        return True

    @staticmethod
    def _runtime_cache_ttl_sec() -> float:
        raw = str(os.getenv('TDX_TREND_BACKTEST_MATRIX_RUNTIME_CACHE_TTL_SEC', '')).strip()
        if not raw:
            return 15 * 60.0
        try:
            return max(0.0, float(raw))
        except Exception:
            return 15 * 60.0

    @staticmethod
    def _runtime_cache_max_items() -> int:
        raw = str(os.getenv('TDX_TREND_BACKTEST_MATRIX_RUNTIME_CACHE_MAX_ITEMS', '')).strip()
        if not raw:
            return 16
        try:
            return max(1, int(raw))
        except Exception:
            return 16

    def _load_runtime_cache(self, cache_key: str) -> MatrixBundle | None:
        if not self._is_runtime_cache_enabled():
            return None
        ttl_sec = self._runtime_cache_ttl_sec()
        with self._runtime_cache_lock:
            cached = self._runtime_cache.get(cache_key)
            if cached is None:
                return None
            created_at, bundle = cached
            if ttl_sec > 0 and (time.time() - created_at) > ttl_sec:
                self._runtime_cache.pop(cache_key, None)
                return None
            return bundle

    def _save_runtime_cache(self, cache_key: str, bundle: MatrixBundle) -> None:
        if not self._is_runtime_cache_enabled():
            return
        now_ts = time.time()
        with self._runtime_cache_lock:
            self._runtime_cache[cache_key] = (now_ts, bundle)
            max_items = self._runtime_cache_max_items()
            if len(self._runtime_cache) > max_items:
                stale_items = sorted(
                    self._runtime_cache.items(),
                    key=lambda item: float(item[1][0]),
                )
                overflow = len(self._runtime_cache) - max_items
                for key, _value in stale_items[:overflow]:
                    self._runtime_cache.pop(key, None)

    def clear_runtime_cache(self) -> None:
        with self._runtime_cache_lock:
            self._runtime_cache.clear()

    @staticmethod
    def _is_incremental_cache_enabled() -> bool:
        raw = str(os.getenv('TDX_TREND_BACKTEST_MATRIX_INCREMENTAL_CACHE', '')).strip().lower()
        if not raw:
            return True
        if raw in {'0', 'false', 'no', 'off', 'n'}:
            return False
        return True

    @staticmethod
    def _build_meta_max_items() -> int:
        raw = str(os.getenv('TDX_TREND_BACKTEST_MATRIX_BUILD_META_MAX_ITEMS', '')).strip()
        if not raw:
            return 128
        try:
            return max(8, int(raw))
        except Exception:
            return 128

    @staticmethod
    def _control_callback_poll_stride() -> int:
        raw = str(os.getenv('TDX_TREND_BACKTEST_MATRIX_CONTROL_POLL_STRIDE', '')).strip()
        if not raw:
            return 256
        try:
            return max(16, int(raw))
        except Exception:
            return 256

    def _save_build_meta(self, cache_key: str, meta: dict[str, object]) -> None:
        with self._build_meta_lock:
            self._build_meta_by_cache_key[cache_key] = dict(meta)
            max_items = self._build_meta_max_items()
            overflow = len(self._build_meta_by_cache_key) - max_items
            if overflow > 0:
                stale_keys = list(self._build_meta_by_cache_key.keys())[:overflow]
                for key in stale_keys:
                    self._build_meta_by_cache_key.pop(key, None)

    def get_build_meta(self, cache_key: str) -> dict[str, object] | None:
        with self._build_meta_lock:
            row = self._build_meta_by_cache_key.get(cache_key)
            if row is None:
                return None
            return dict(row)

    def _incremental_manifest_file(self) -> Path:
        return self._cache_dir / '_incremental_manifest.json'

    def _load_incremental_manifest(self) -> dict[str, dict[str, str]]:
        path = self._incremental_manifest_file()
        if not path.exists():
            return {}
        try:
            with path.open('r', encoding='utf-8') as fp:
                raw = json.load(fp)
        except Exception:
            return {}
        if not isinstance(raw, dict):
            return {}
        out: dict[str, dict[str, str]] = {}
        for sig, row in raw.items():
            key = str(sig).strip()
            if not key or not isinstance(row, dict):
                continue
            cache_key = str(row.get('cache_key') or '').strip()
            last_date = str(row.get('last_date') or '').strip()
            if not cache_key:
                continue
            out[key] = {'cache_key': cache_key, 'last_date': last_date}
        return out

    def _save_incremental_manifest(self, payload: dict[str, dict[str, str]]) -> None:
        path = self._incremental_manifest_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix('.tmp.json')
            with tmp_path.open('w', encoding='utf-8') as fp:
                json.dump(payload, fp, ensure_ascii=True, sort_keys=True)
            tmp_path.replace(path)
        except Exception:
            return

    def _resolve_incremental_base_cache_key(self, signature: str) -> tuple[str | None, str | None]:
        sig = str(signature).strip()
        if not sig:
            return None, None
        with self._incremental_manifest_lock:
            manifest = self._load_incremental_manifest()
            row = manifest.get(sig) or {}
            cache_key = str(row.get('cache_key') or '').strip() or None
            last_date = str(row.get('last_date') or '').strip() or None
            return cache_key, last_date

    def _update_incremental_manifest(self, signature: str, cache_key: str, last_date: str | None) -> None:
        sig = str(signature).strip()
        key = str(cache_key).strip()
        if not sig or not key:
            return
        with self._incremental_manifest_lock:
            manifest = self._load_incremental_manifest()
            manifest[sig] = {
                'cache_key': key,
                'last_date': str(last_date or '').strip(),
            }
            if len(manifest) > 512:
                # Keep a bounded manifest; retain the latest insertion keys.
                keep_keys = list(manifest.keys())[-512:]
                manifest = {name: manifest[name] for name in keep_keys}
            self._save_incremental_manifest(manifest)

    @classmethod
    def _with_lookback_start(cls, date_from: str, max_lookback_days: int) -> str:
        if max_lookback_days <= 0:
            return date_from
        start_dt = cls._parse_date(date_from)
        # 使用 3x 日历天近似覆盖交易日窗口，避免周末/节假日导致样本不足。
        lookback_dt = start_dt - timedelta(days=max_lookback_days * 3)
        return lookback_dt.strftime('%Y-%m-%d')

    @staticmethod
    def _symbols_hash(symbols: list[str]) -> str:
        normalized = [str(item).strip().lower() for item in symbols if str(item).strip()]
        raw = '\n'.join(sorted(set(normalized)))
        return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]

    def build_cache_key(
        self,
        *,
        symbols: list[str],
        date_from: str,
        date_to: str,
        data_version: str,
        window_set: tuple[int, ...],
        algo_version: str,
    ) -> str:
        payload = {
            'symbols_hash': self._symbols_hash(symbols),
            'date_range': f'{date_from}:{date_to}',
            'data_version': str(data_version).strip(),
            'window_set': list(window_set),
            'algo_version': str(algo_version).strip(),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
        return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:24]

    def build_incremental_signature(
        self,
        *,
        symbols: list[str],
        date_from: str,
        max_lookback_days: int,
        data_version: str,
        window_set: tuple[int, ...],
        algo_version: str,
    ) -> str:
        payload = {
            'symbols_hash': self._symbols_hash(symbols),
            'date_from': str(date_from).strip(),
            'lookback_start': self._with_lookback_start(date_from, max_lookback_days),
            'data_version': str(data_version).strip(),
            'window_set': list(window_set),
            'algo_version': str(algo_version).strip(),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
        return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:24]

    def _cache_file(self, cache_key: str) -> Path:
        return self._cache_dir / f'{cache_key}.npz'

    @staticmethod
    def _build_empty_bundle() -> MatrixBundle:
        empty = np.zeros((0, 0), dtype=np.float64)
        return MatrixBundle(
            dates=[],
            symbols=[],
            open=empty,
            high=empty,
            low=empty,
            close=empty,
            volume=empty,
            valid_mask=np.zeros((0, 0), dtype=bool),
        )

    @staticmethod
    def _normalize_symbols(symbols: list[str]) -> list[str]:
        normalized = [str(item).strip().lower() for item in symbols if str(item).strip()]
        return sorted(set(normalized))

    @staticmethod
    def _is_valid_ohlcv(o: float, h: float, l: float, c: float, v: float) -> bool:
        if not (np.isfinite(o) and np.isfinite(h) and np.isfinite(l) and np.isfinite(c) and np.isfinite(v)):
            return False
        if o <= 0 or h <= 0 or l <= 0 or c <= 0:
            return False
        return True

    @staticmethod
    def _slice_bundle_by_date_to(bundle: MatrixBundle, *, date_to: str) -> MatrixBundle | None:
        if not bundle.dates:
            return None
        end = 0
        for idx, day in enumerate(bundle.dates):
            if day <= date_to:
                end = idx + 1
            else:
                break
        if end <= 0:
            return None
        if end >= len(bundle.dates):
            return bundle
        return MatrixBundle(
            dates=list(bundle.dates[:end]),
            symbols=list(bundle.symbols),
            open=np.array(bundle.open[:end, :], dtype=np.float64, copy=True),
            high=np.array(bundle.high[:end, :], dtype=np.float64, copy=True),
            low=np.array(bundle.low[:end, :], dtype=np.float64, copy=True),
            close=np.array(bundle.close[:end, :], dtype=np.float64, copy=True),
            volume=np.array(bundle.volume[:end, :], dtype=np.float64, copy=True),
            valid_mask=np.array(bundle.valid_mask[:end, :], dtype=bool, copy=True),
        )

    def load_bundle_from_cache(self, cache_key: str) -> MatrixBundle | None:
        cached_runtime = self._load_runtime_cache(cache_key)
        if cached_runtime is not None:
            return cached_runtime

        path = self._cache_file(cache_key)
        if not path.exists():
            return None
        try:
            with np.load(path, allow_pickle=False) as data:
                dates = data['dates'].astype(str).tolist()
                symbols = data['symbols'].astype(str).tolist()
                # npz payload can be loaded as read-only arrays; use writable copies.
                open_ = np.array(data['open'], dtype=np.float64, copy=True)
                high = np.array(data['high'], dtype=np.float64, copy=True)
                low = np.array(data['low'], dtype=np.float64, copy=True)
                close = np.array(data['close'], dtype=np.float64, copy=True)
                volume = np.array(data['volume'], dtype=np.float64, copy=True)
                valid_mask = np.array(data['valid_mask'], dtype=bool, copy=True)
            expected_shape = (len(dates), len(symbols))
            if (
                open_.shape != expected_shape
                or high.shape != expected_shape
                or low.shape != expected_shape
                or close.shape != expected_shape
                or volume.shape != expected_shape
                or valid_mask.shape != expected_shape
            ):
                return None
            bundle = MatrixBundle(
                dates=dates,
                symbols=symbols,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                valid_mask=valid_mask,
            )
            self._save_runtime_cache(cache_key, bundle)
            return bundle
        except Exception:
            return None

    def save_bundle_to_cache(
        self,
        cache_key: str,
        bundle: MatrixBundle,
        *,
        incremental_signature: str | None = None,
    ) -> None:
        path = self._cache_file(cache_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp.npz')
        np.savez_compressed(
            tmp,
            dates=np.asarray(bundle.dates, dtype='U10'),
            symbols=np.asarray(bundle.symbols, dtype='U16'),
            open=bundle.open,
            high=bundle.high,
            low=bundle.low,
            close=bundle.close,
            volume=bundle.volume,
            valid_mask=bundle.valid_mask.astype(np.uint8),
        )
        tmp.replace(path)
        self._save_runtime_cache(cache_key, bundle)
        if incremental_signature and bundle.dates:
            self._update_incremental_manifest(
                incremental_signature,
                cache_key,
                bundle.dates[-1],
            )

    def build_bundle(
        self,
        *,
        symbols: list[str],
        get_candles: Callable[[str], list[CandlePoint]],
        date_from: str,
        date_to: str,
        max_lookback_days: int,
        cache_key: str,
        incremental_signature: str | None = None,
        use_cache: bool = True,
        control_callback: Callable[[], None] | None = None,
    ) -> tuple[MatrixBundle, bool]:
        started_at = time.perf_counter()
        if control_callback is not None:
            control_callback()
        if use_cache:
            cached = self.load_bundle_from_cache(cache_key)
            if cached is not None:
                self._save_build_meta(
                    cache_key,
                    {
                        'mode': 'cache_hit',
                        'cache_hit': True,
                        'elapsed_sec': max(0.0, time.perf_counter() - started_at),
                        'shape_t': int(cached.close.shape[0]),
                        'shape_n': int(cached.close.shape[1]),
                        'last_date': str(cached.dates[-1]) if cached.dates else '',
                    },
                )
                if incremental_signature and cached.dates:
                    self._update_incremental_manifest(
                        incremental_signature,
                        cache_key,
                        cached.dates[-1],
                    )
                return cached, True

        deduped_symbols = self._normalize_symbols(symbols)
        if not deduped_symbols:
            empty_bundle = self._build_empty_bundle()
            self._save_build_meta(
                cache_key,
                {
                    'mode': 'empty',
                    'cache_hit': False,
                    'elapsed_sec': max(0.0, time.perf_counter() - started_at),
                    'shape_t': 0,
                    'shape_n': 0,
                    'last_date': '',
                },
            )
            return (
                empty_bundle,
                False,
            )

        lookback_start = self._with_lookback_start(date_from, max_lookback_days)
        control_poll_stride = self._control_callback_poll_stride()

        def _load_symbol_rows(
            symbol: str,
            *,
            lower_bound_exclusive: str | None,
        ) -> tuple[str, list[CandlePoint], list[str]]:
            if control_callback is not None:
                control_callback()
            candles = get_candles(symbol)
            rows: list[CandlePoint] = []
            local_dates: list[str] = []
            for index, item in enumerate(candles):
                if control_callback is not None and (index % control_poll_stride == 0):
                    control_callback()
                day = str(item.time).strip()
                if not day:
                    continue
                if day < lookback_start or day > date_to:
                    continue
                if lower_bound_exclusive is not None and day <= lower_bound_exclusive:
                    continue
                rows.append(item)
                local_dates.append(day)
            return symbol, rows, local_dates

        def _collect_rows(
            *,
            lower_bound_exclusive: str | None,
        ) -> tuple[dict[str, list[CandlePoint]], set[str]]:
            filtered_rows_by_symbol: dict[str, list[CandlePoint]] = {}
            all_dates: set[str] = set()
            workers = self._resolve_build_workers()
            if workers <= 1 or len(deduped_symbols) <= 1:
                for symbol in deduped_symbols:
                    if control_callback is not None:
                        control_callback()
                    loaded_symbol, rows, local_dates = _load_symbol_rows(
                        symbol,
                        lower_bound_exclusive=lower_bound_exclusive,
                    )
                    filtered_rows_by_symbol[loaded_symbol] = rows
                    all_dates.update(local_dates)
                return filtered_rows_by_symbol, all_dates

            with ThreadPoolExecutor(max_workers=min(workers, len(deduped_symbols))) as executor:
                future_map = {
                    executor.submit(
                        _load_symbol_rows,
                        symbol,
                        lower_bound_exclusive=lower_bound_exclusive,
                    ): symbol
                    for symbol in deduped_symbols
                }
                for future in as_completed(future_map):
                    if control_callback is not None:
                        control_callback()
                    loaded_symbol, rows, local_dates = future.result()
                    filtered_rows_by_symbol[loaded_symbol] = rows
                    all_dates.update(local_dates)
            return filtered_rows_by_symbol, all_dates

        def _build_bundle_from_rows(
            filtered_rows_by_symbol: dict[str, list[CandlePoint]],
            *,
            existing_dates: list[str] | None = None,
            existing_open: np.ndarray | None = None,
            existing_high: np.ndarray | None = None,
            existing_low: np.ndarray | None = None,
            existing_close: np.ndarray | None = None,
            existing_volume: np.ndarray | None = None,
            existing_valid_mask: np.ndarray | None = None,
        ) -> MatrixBundle:
            date_set: set[str] = set(existing_dates or [])
            for rows in filtered_rows_by_symbol.values():
                for item in rows:
                    day = str(item.time).strip()
                    if day:
                        date_set.add(day)
            dates = sorted(date_set)
            date_to_idx = {day: idx for idx, day in enumerate(dates)}
            t = len(dates)
            n = len(deduped_symbols)

            open_ = np.full((t, n), np.nan, dtype=np.float64)
            high = np.full((t, n), np.nan, dtype=np.float64)
            low = np.full((t, n), np.nan, dtype=np.float64)
            close = np.full((t, n), np.nan, dtype=np.float64)
            volume = np.full((t, n), np.nan, dtype=np.float64)
            valid_mask = np.zeros((t, n), dtype=bool)

            if existing_dates and existing_open is not None:
                existing_t = min(len(existing_dates), existing_open.shape[0])
                if existing_t > 0:
                    open_[:existing_t, :] = existing_open[:existing_t, :]
                    high[:existing_t, :] = existing_high[:existing_t, :]
                    low[:existing_t, :] = existing_low[:existing_t, :]
                    close[:existing_t, :] = existing_close[:existing_t, :]
                    volume[:existing_t, :] = existing_volume[:existing_t, :]
                    valid_mask[:existing_t, :] = existing_valid_mask[:existing_t, :]

            for col, symbol in enumerate(deduped_symbols):
                if control_callback is not None and (col % 16 == 0):
                    control_callback()
                for row in filtered_rows_by_symbol.get(symbol, []):
                    idx = date_to_idx.get(str(row.time).strip())
                    if idx is None:
                        continue
                    o = float(row.open)
                    h = float(row.high)
                    l = float(row.low)
                    c = float(row.close)
                    v = float(row.volume)
                    if not self._is_valid_ohlcv(o, h, l, c, v):
                        continue
                    open_[idx, col] = o
                    high[idx, col] = h
                    low[idx, col] = l
                    close[idx, col] = c
                    volume[idx, col] = max(0.0, v)
                    valid_mask[idx, col] = True

            return MatrixBundle(
                dates=dates,
                symbols=deduped_symbols,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                valid_mask=valid_mask,
            )

        base_cache_key: str | None = None
        base_last_date: str | None = None
        base_bundle: MatrixBundle | None = None
        mode_label = 'full_build'
        appended_rows = 0

        if (
            use_cache
            and self._is_incremental_cache_enabled()
            and str(incremental_signature or '').strip()
        ):
            candidate_base_cache_key, manifest_last_date = self._resolve_incremental_base_cache_key(
                str(incremental_signature),
            )
            if candidate_base_cache_key and candidate_base_cache_key != cache_key:
                cached_base = self.load_bundle_from_cache(candidate_base_cache_key)
                if (
                    cached_base is not None
                    and cached_base.symbols == deduped_symbols
                    and cached_base.dates
                ):
                    resolved_base = self._slice_bundle_by_date_to(cached_base, date_to=date_to)
                    if resolved_base is not None and resolved_base.dates:
                        resolved_last_date = resolved_base.dates[-1]
                        manifest_last = str(manifest_last_date or '').strip()
                        if manifest_last and resolved_last_date < manifest_last:
                            resolved_last_date = manifest_last
                        if resolved_last_date >= lookback_start:
                            base_cache_key = candidate_base_cache_key
                            base_bundle = resolved_base
                            base_last_date = resolved_base.dates[-1]

        if base_bundle is not None and base_last_date is not None and base_last_date < date_to:
            if control_callback is not None:
                control_callback()
            incremental_rows_by_symbol, _ = _collect_rows(
                lower_bound_exclusive=base_last_date,
            )
            bundle = _build_bundle_from_rows(
                incremental_rows_by_symbol,
                existing_dates=base_bundle.dates,
                existing_open=base_bundle.open,
                existing_high=base_bundle.high,
                existing_low=base_bundle.low,
                existing_close=base_bundle.close,
                existing_volume=base_bundle.volume,
                existing_valid_mask=base_bundle.valid_mask,
            )
            appended_rows = max(0, len(bundle.dates) - len(base_bundle.dates))
            mode_label = 'incremental_append' if appended_rows > 0 else 'incremental_reuse'
        elif base_bundle is not None and base_last_date is not None and base_last_date >= date_to:
            bundle = base_bundle
            mode_label = 'incremental_reuse'
        else:
            if control_callback is not None:
                control_callback()
            rows_by_symbol, _ = _collect_rows(lower_bound_exclusive=None)
            bundle = _build_bundle_from_rows(rows_by_symbol)

        if use_cache:
            if control_callback is not None:
                control_callback()
            self.save_bundle_to_cache(
                cache_key,
                bundle,
                incremental_signature=incremental_signature,
            )

        self._save_build_meta(
            cache_key,
            {
                'mode': mode_label,
                'cache_hit': False,
                'base_cache_key': str(base_cache_key or ''),
                'base_last_date': str(base_last_date or ''),
                'append_rows': int(appended_rows),
                'elapsed_sec': max(0.0, time.perf_counter() - started_at),
                'shape_t': int(bundle.close.shape[0]),
                'shape_n': int(bundle.close.shape[1]),
                'last_date': str(bundle.dates[-1]) if bundle.dates else '',
            },
        )
        return bundle, False
