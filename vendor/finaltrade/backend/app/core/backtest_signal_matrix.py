from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from .backtest_matrix_engine import MatrixBundle


@dataclass(slots=True)
class BacktestSignalMatrix:
    s1: np.ndarray
    s2: np.ndarray
    s3: np.ndarray
    s4: np.ndarray
    s5: np.ndarray
    s6: np.ndarray
    s7: np.ndarray
    s8: np.ndarray
    s9: np.ndarray
    in_pool: np.ndarray
    buy_signal: np.ndarray
    sell_signal: np.ndarray
    score: np.ndarray
    sell_signal_label: np.ndarray | None = None


def _to_bool_array(frame: pd.DataFrame) -> np.ndarray:
    # Pandas/NumPy may return read-only views for some blocks; force writable copies.
    values = frame.fillna(False).to_numpy(dtype=bool, copy=True)
    return np.array(values, dtype=bool, copy=True)


def _top_n_mask_from_returns(
    returns: np.ndarray,
    *,
    top_n: int,
    control_callback: Callable[[], None] | None = None,
) -> np.ndarray:
    if returns.ndim != 2:
        return np.zeros((0, 0), dtype=bool)
    t, n = returns.shape
    if t <= 0 or n <= 0:
        return np.zeros((t, n), dtype=bool)

    keep_n = max(1, int(top_n))
    out = np.zeros((t, n), dtype=bool)
    for row_idx in range(t):
        if control_callback is not None and (row_idx % 32 == 0):
            control_callback()
        row = returns[row_idx]
        valid_idx = np.flatnonzero(np.isfinite(row))
        valid_count = int(valid_idx.size)
        if valid_count <= 0:
            continue
        k = min(keep_n, valid_count)
        if k >= valid_count:
            out[row_idx, valid_idx] = True
            continue

        valid_values = row[valid_idx]
        pivot = valid_count - k
        part = np.argpartition(valid_values, pivot)[pivot:]
        chosen = valid_idx[part]

        # 在边界存在并列时，按值降序 + 列序升序稳定收敛。
        if chosen.size > k:
            chosen_values = row[chosen]
            order = np.lexsort((chosen, -chosen_values))
            chosen = chosen[order[:k]]

        out[row_idx, chosen] = True
    return out


def compute_backtest_signal_matrix(
    bundle: MatrixBundle,
    *,
    top_n: int = 500,
    event_matrix_builder: Callable[[MatrixBundle], BacktestSignalMatrix] | None = None,
    control_callback: Callable[[], None] | None = None,
) -> BacktestSignalMatrix:
    t, n = bundle.shape()
    if event_matrix_builder is not None:
        if control_callback is not None:
            control_callback()
        matrix = event_matrix_builder(bundle)
        expected_shape = (t, n)
        if (
            matrix.buy_signal.shape != expected_shape
            or matrix.sell_signal.shape != expected_shape
            or matrix.score.shape != expected_shape
        ):
            raise ValueError("event-driven signal matrix shape mismatch")
        if matrix.sell_signal_label is not None and matrix.sell_signal_label.shape != expected_shape:
            raise ValueError("event-driven sell signal labels shape mismatch")
        return matrix
    if t <= 0 or n <= 0:
        empty_bool = np.zeros((t, n), dtype=bool)
        empty_score = np.zeros((t, n), dtype=np.float64)
        return BacktestSignalMatrix(
            s1=empty_bool,
            s2=empty_bool,
            s3=empty_bool,
            s4=empty_bool,
            s5=empty_bool,
            s6=empty_bool,
            s7=empty_bool,
            s8=empty_bool,
            s9=empty_bool,
            in_pool=empty_bool,
            buy_signal=empty_bool,
            sell_signal=empty_bool,
            score=empty_score,
            sell_signal_label=None,
        )

    if control_callback is not None:
        control_callback()
    close = pd.DataFrame(bundle.close, index=bundle.dates, columns=bundle.symbols)
    high = pd.DataFrame(bundle.high, index=bundle.dates, columns=bundle.symbols)
    low = pd.DataFrame(bundle.low, index=bundle.dates, columns=bundle.symbols)
    volume = pd.DataFrame(bundle.volume, index=bundle.dates, columns=bundle.symbols)

    if control_callback is not None:
        control_callback()
    ma10 = close.rolling(10, min_periods=10).mean()
    ma20 = close.rolling(20, min_periods=20).mean()
    ma60 = close.rolling(60, min_periods=60).mean()
    vol_ma10 = volume.rolling(10, min_periods=10).mean()
    vol_ma20 = volume.rolling(20, min_periods=20).mean()
    vol_ma60 = volume.rolling(60, min_periods=60).mean()

    if control_callback is not None:
        control_callback()
    range20 = high.rolling(20, min_periods=20).max() - low.rolling(20, min_periods=20).min()
    range60 = high.rolling(60, min_periods=60).max() - low.rolling(60, min_periods=60).min()
    atr_ratio = range20 / range60.replace(0.0, np.nan)
    s1 = atr_ratio < 0.7

    vol_ratio = vol_ma10 / vol_ma60.replace(0.0, np.nan)
    s2 = vol_ratio < 0.6

    ret_40d = close.pct_change(40, fill_method=None)
    s3_b = _top_n_mask_from_returns(
        ret_40d.to_numpy(dtype=np.float64, copy=False),
        top_n=top_n,
        control_callback=control_callback,
    )

    # 横盘识别：20日振幅收敛且价格靠近20日均线。
    sideways_ratio = range20 / close.replace(0.0, np.nan)
    near_ma20 = (close - ma20).abs() / ma20.replace(0.0, np.nan)
    s4 = (sideways_ratio < 0.18) & (near_ma20 < 0.06)

    high20_prev = high.rolling(20, min_periods=20).max().shift(1)
    s5 = (close > high20_prev) & (volume > vol_ma20 * 1.2)

    low10_prev = low.rolling(10, min_periods=10).min().shift(1)
    s6 = (
        (close > ma10)
        & (close.shift(1) <= ma10.shift(1))
        & (close > low10_prev)
    )

    s7 = (ma10 > ma20) & (ma20 > ma60)

    low20_prev = low.rolling(20, min_periods=20).min().shift(1)
    s8 = (close < low20_prev) | (close < ma20 * 0.97)

    s9 = (close < ma10) & (volume > vol_ma20 * 1.4) & (ret_40d < 0)

    if control_callback is not None:
        control_callback()
    s1_b = _to_bool_array(s1)
    s2_b = _to_bool_array(s2)
    s4_b = _to_bool_array(s4)
    s5_b = _to_bool_array(s5)
    s6_b = _to_bool_array(s6)
    s7_b = _to_bool_array(s7)
    s8_b = _to_bool_array(s8)
    s9_b = _to_bool_array(s9)

    pool_score = s1_b.astype(np.int16) + s2_b.astype(np.int16) + s3_b.astype(np.int16) + s4_b.astype(np.int16)
    in_pool = pool_score >= 2

    buy_signal = (s5_b | s6_b) & in_pool
    sell_signal = s8_b | s9_b

    raw_score = (
        pool_score.astype(np.float64)
        + s5_b.astype(np.float64) * 2.0
        + s6_b.astype(np.float64) * 1.5
        + s7_b.astype(np.float64) * 1.0
    )
    score = np.clip(raw_score / 8.5 * 100.0, 0.0, 100.0)

    valid_mask = np.asarray(bundle.valid_mask, dtype=bool)
    s1_b = s1_b & valid_mask
    s2_b = s2_b & valid_mask
    s3_b = s3_b & valid_mask
    s4_b = s4_b & valid_mask
    s5_b = s5_b & valid_mask
    s6_b = s6_b & valid_mask
    s7_b = s7_b & valid_mask
    s8_b = s8_b & valid_mask
    s9_b = s9_b & valid_mask
    in_pool = in_pool & valid_mask
    buy_signal = buy_signal & valid_mask
    sell_signal = sell_signal & valid_mask
    score = np.where(valid_mask, score, 0.0)

    if control_callback is not None:
        control_callback()
    return BacktestSignalMatrix(
        s1=s1_b,
        s2=s2_b,
        s3=s3_b,
        s4=s4_b,
        s5=s5_b,
        s6=s6_b,
        s7=s7_b,
        s8=s8_b,
        s9=s9_b,
        in_pool=in_pool,
        buy_signal=buy_signal,
        sell_signal=sell_signal,
        score=score,
        sell_signal_label=None,
    )
