"""B1 战法选股策略 —— 多时间框架指标计算

条件：
1. 月线 MACD 多头区间（DIF > 0 且 DIF > DEA 且红柱）
2. 周线 DIF > 0（零轴之上）
3. 日线 MA5 > MA20 且 收盘 > MA20
4. 缩量（当日量 < 20日均量 × ratio）
5. 涨跌幅绝对值 < chg_limit
6. 振幅 < amp_limit（10cm 板块 / 20cm 板块）
7. KDJ J 值从低位勾头向上（前一日 J < 当日 J，且 J < kdj_j_upper）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class B1Params:
    vol_ratio: float = 0.8
    chg_limit: float = 3.0
    amp_limit_10cm: float = 5.0
    amp_limit_20cm: float = 8.0
    kdj_j_upper: float = 50.0
    min_daily_bars: int = 60
    min_total_bars: int = 900  # ~40 months × 22 trading days, enough for monthly MACD


# ─── EMA / MACD ───────────────────────────────────────────────

def _ema_series(values: list[float], n: int) -> list[float]:
    if not values:
        return []
    result = [values[0]]
    k = 2.0 / (n + 1)
    for i in range(1, len(values)):
        result.append(values[i] * k + result[-1] * (1 - k))
    return result


def calc_macd(
    closes: list[float],
    *,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[float], list[float], list[float]]:
    """Return (DIF, DEA, MACD-histogram) series.  histogram = (DIF - DEA) * 2."""
    if len(closes) < slow + signal:
        return [], [], []
    ema_fast = _ema_series(closes, fast)
    ema_slow = _ema_series(closes, slow)
    dif = [f - s for f, s in zip(ema_fast, ema_slow)]
    dea = _ema_series(dif, signal)
    hist = [(d - e) * 2 for d, e in zip(dif, dea)]
    return dif, dea, hist


# ─── KDJ ──────────────────────────────────────────────────────

def calc_kdj(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    n: int = 9,
) -> list[tuple[float, float, float]]:
    """Return [(K, D, J), ...] series."""
    length = len(closes)
    if length < n:
        return []

    result: list[tuple[float, float, float]] = []
    k_val = 50.0
    d_val = 50.0

    for i in range(length):
        if i < n - 1:
            result.append((50.0, 50.0, 50.0))
            continue

        low_n = min(lows[i - n + 1 : i + 1])
        high_n = max(highs[i - n + 1 : i + 1])
        if high_n == low_n:
            rsv = 50.0
        else:
            rsv = (closes[i] - low_n) / (high_n - low_n) * 100.0

        k_val = k_val * 2 / 3 + rsv / 3
        d_val = d_val * 2 / 3 + k_val / 3
        j_val = 3 * k_val - 2 * d_val
        result.append((k_val, d_val, j_val))

    return result


# ─── Bar aggregation ──────────────────────────────────────────

def _sma(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def daily_to_weekly(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate daily bars into weekly bars (ISO week grouping)."""
    if not bars:
        return []

    weeks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for b in bars:
        d = b.get("date") or b.get("time", "")
        # Support both int (20260101) and str ("2026-01-01") formats
        if isinstance(d, int):
            year = d // 10000
            month = (d % 10000) // 100
            day = d % 100
        else:
            parts = str(d).replace("-", "").replace("/", "")
            if len(parts) < 8:
                continue
            year = int(parts[:4])
            month = int(parts[4:6])
            day = int(parts[6:8])

        try:
            from datetime import date as dt_date

            iso_week = dt_date(year, month, day).isocalendar()[:2]
        except Exception:
            continue

        if current is None or iso_week != current["_week"]:
            if current is not None:
                weeks.append(current)
            current = {
                "date": d,
                "open": b["open"],
                "high": b["high"],
                "low": b["low"],
                "close": b["close"],
                "volume": b.get("volume", 0),
                "_week": iso_week,
            }
        else:
            current["high"] = max(current["high"], b["high"])
            current["low"] = min(current["low"], b["low"])
            current["close"] = b["close"]
            current["volume"] = current.get("volume", 0) + b.get("volume", 0)
            current["date"] = d

    if current is not None:
        weeks.append(current)

    return weeks


def daily_to_monthly(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate daily bars into monthly bars (year-month grouping)."""
    if not bars:
        return []

    months: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for b in bars:
        d = b.get("date") or b.get("time", "")
        if isinstance(d, int):
            ym = d // 100
        else:
            parts = str(d).replace("-", "").replace("/", "")
            if len(parts) < 6:
                continue
            ym = int(parts[:6])

        if current is None or ym != current["_ym"]:
            if current is not None:
                months.append(current)
            current = {
                "date": d,
                "open": b["open"],
                "high": b["high"],
                "low": b["low"],
                "close": b["close"],
                "volume": b.get("volume", 0),
                "_ym": ym,
            }
        else:
            current["high"] = max(current["high"], b["high"])
            current["low"] = min(current["low"], b["low"])
            current["close"] = b["close"]
            current["volume"] = current.get("volume", 0) + b.get("volume", 0)
            current["date"] = d

    if current is not None:
        months.append(current)

    return months


# ─── B1 main check ────────────────────────────────────────────

def _is_20cm(code: str) -> bool:
    return code.startswith("30") or code.startswith("688")


def check_b1(
    code: str,
    bars: list[dict[str, Any]],
    params: B1Params | None = None,
) -> dict[str, Any] | None:
    """Run B1 conditions on daily bars.  Return result dict or None."""
    if params is None:
        params = B1Params()

    if len(bars) < params.min_total_bars:
        return None

    # ── 1. Monthly MACD bullish ──
    monthly = daily_to_monthly(bars)
    if len(monthly) < 35:
        return None
    m_closes = [b["close"] for b in monthly]
    m_dif, m_dea, m_hist = calc_macd(m_closes)
    if not m_hist:
        return None
    if m_dif[-1] <= 0 or m_dif[-1] <= m_dea[-1] or m_hist[-1] <= 0:
        return None

    # ── 2. Weekly DIF > 0 ──
    weekly = daily_to_weekly(bars)
    if len(weekly) < 35:
        return None
    w_closes = [b["close"] for b in weekly]
    w_dif, w_dea, w_hist = calc_macd(w_closes)
    if not w_hist:
        return None
    if w_dif[-1] <= 0:
        return None

    # ── Daily indicators ──
    daily = bars[-params.min_daily_bars:]
    if len(daily) < 30:
        return None

    closes = [b["close"] for b in daily]
    highs = [b["high"] for b in daily]
    lows = [b["low"] for b in daily]
    vols = [b.get("volume", 0) for b in daily]
    latest = daily[-1]
    prev = daily[-2]

    ma5 = _sma(closes, 5)
    ma20 = _sma(closes, 20)
    vol_ma20 = _sma(vols, 20)

    if ma5 is None or ma20 is None or vol_ma20 is None or vol_ma20 == 0:
        return None

    # ── 3. MA5 > MA20 ──
    if ma5 <= ma20:
        return None

    # ── 4. Close > MA20 ──
    if latest["close"] <= ma20:
        return None

    # ── 5. Shrinking volume ──
    if latest.get("volume", 0) >= vol_ma20 * params.vol_ratio:
        return None

    # ── 6. Price change < limit ──
    if prev["close"] == 0:
        return None
    chg_pct = (latest["close"] - prev["close"]) / prev["close"] * 100
    if abs(chg_pct) >= params.chg_limit:
        return None

    # ── 7. Amplitude < limit ──
    amplitude = (latest["high"] - latest["low"]) / prev["close"] * 100
    amp_limit = params.amp_limit_20cm if _is_20cm(code) else params.amp_limit_10cm
    if amplitude >= amp_limit:
        return None

    # ── 8. KDJ J turning up from low ──
    kdj = calc_kdj(highs, lows, closes)
    if len(kdj) < 2:
        return None
    _, _, j_today = kdj[-1]
    _, _, j_yesterday = kdj[-2]
    if not (j_today > j_yesterday and j_yesterday < params.kdj_j_upper):
        return None

    return {
        "symbol": code,
        "close": latest["close"],
        "change_pct": round(chg_pct, 2),
        "amplitude_pct": round(amplitude, 2),
        "volume_ratio": round(latest.get("volume", 0) / vol_ma20, 4),
        "kdj_j": round(j_today, 1),
        "weekly_macd": round(w_hist[-1], 4),
        "monthly_macd": round(m_hist[-1], 4),
    }


# ─── Snapshot-compatible wrapper ────────────────────────────────


def calculate_b1_signal(candles: list[Any]) -> dict[str, Any]:
    """Compute B1 multi-timeframe signal from CandlePoint list.

    Designed to be called from the snapshot pipeline (like
    ``calculate_wulong_cluster_signal``). Returns a dict that is
    stored as ``snapshot["b1_mtf_signal"]``.
    """
    if not candles or len(candles) < 250:
        return {"has_data": False, "signal": False}

    # Convert CandlePoint → plain dict bars
    bars: list[dict[str, Any]] = []
    for cp in candles:
        t = getattr(cp, "time", None) or getattr(cp, "date", "")
        bars.append({
            "date": t,
            "open": float(getattr(cp, "open", 0)),
            "high": float(getattr(cp, "high", 0)),
            "low": float(getattr(cp, "low", 0)),
            "close": float(getattr(cp, "close", 0)),
            "volume": max(0, int(getattr(cp, "volume", 0))),
            "amount": max(0.0, float(getattr(cp, "amount", 0))),
        })

    # Extract code from symbol if present
    symbol = str(getattr(candles[0], "symbol", "")) if candles else ""
    code = symbol[2:] if len(symbol) > 6 else symbol

    result = check_b1(code, bars)
    if result is None:
        return {"has_data": True, "signal": False}

    return {
        "has_data": True,
        "signal": True,
        **result,
    }


def evaluate_b1_signal(
    indicator: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate a B1 signal and produce scores for ranking/grading.

    Parallel to ``evaluate_wulong_cluster_signal``.
    """
    if not isinstance(indicator, dict) or not indicator.get("signal"):
        return {"signal": False, "signal_score": 0.0}

    kdj_j = float(indicator.get("kdj_j", 0))
    volume_ratio = float(indicator.get("volume_ratio", 0))
    monthly_macd = float(indicator.get("monthly_macd", 0))
    weekly_macd = float(indicator.get("weekly_macd", 0))

    # KDJ J score: lower J from below threshold = stronger reversal signal (0-40)
    kdj_score = max(0.0, min(40.0, (50.0 - kdj_j) / 50.0 * 40.0))

    # Volume ratio score: more contraction = stronger (0-30)
    vol_score = max(0.0, min(30.0, (1.0 - volume_ratio) / 1.0 * 30.0))

    # MACD strength score: larger histogram = stronger trend (0-30)
    macd_strength = abs(monthly_macd) + abs(weekly_macd)
    macd_score = max(0.0, min(30.0, macd_strength * 100))

    signal_score = kdj_score + vol_score + macd_score

    event_grade = "C"
    if signal_score >= 55:
        event_grade = "A"
    elif signal_score >= 40:
        event_grade = "B"

    return {
        "signal": True,
        "signal_score": round(signal_score, 2),
        "health_score": round(kdj_score, 2),
        "event_score": round(vol_score, 2),
        "trend_score": round(macd_score, 2),
        "structure_score": round(macd_strength * 100, 2),
        "phase_score": round(kdj_score + vol_score, 2),
        "volatility_score": round(max(0, 30.0 - abs(float(indicator.get("change_pct", 0))) * 5), 2),
        "event_grade": event_grade,
    }
