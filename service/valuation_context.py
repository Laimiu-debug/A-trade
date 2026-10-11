"""Observed index context for the explicitly conventional valuation multiplier.

This is the original FINAL-TRADE scenario convention, not a measure of fair
value.  A missing quote remains missing; it never becomes a neutral 1.0.
"""
from __future__ import annotations

import datetime as dt
import math

from service.trading_calendar import trading_sessions


ZONE = dt.timezone(dt.timedelta(hours=8))
BASELINE_POINTS = 3000.0
FLOOR_COEFFICIENT = 0.5
MODEL_NOTE = "沿用原情绪估值模型约定：上证指数收盘点位 ÷ 3000，最低取 0.5。3000 是模型锚点，不是公允价值或交易所估值标准。"


def _now():
    return dt.datetime.now(ZONE)


def _day(value):
    if not isinstance(value, str):
        raise ValueError("上证指数日线交易日期无效")
    try:
        day = dt.date.fromisoformat(value)
        if day.isoformat() != value:
            raise ValueError()
        return day
    except ValueError:
        raise ValueError("上证指数日线交易日期无效") from None


def finance_context(owner, payload=None):
    """Read the user's configured market.bars source, with no hardcoded path."""
    if payload is None:
        payload = {}
    if not isinstance(payload, dict) or set(payload) - {"refresh"}:
        raise ValueError("包含不支持的大盘水位请求参数")
    if "refresh" in payload and type(payload["refresh"]) is not bool:
        raise ValueError("refresh 必须是布尔值")

    now = _now()
    # A daily bar for the current session may still be updating before close.
    cutoff = now.date() if now.time() >= dt.time(15) else now.date() - dt.timedelta(days=1)
    sessions = trading_sessions((cutoff - dt.timedelta(days=40)).isoformat(), cutoff.isoformat())
    expected = sessions[-1] if sessions else None
    result = {
        "symbol": "sh000001", "name": "上证指数", "indexPoints": None,
        "baselinePoints": BASELINE_POINTS, "floorCoefficient": FLOOR_COEFFICIENT,
        "rawCoefficient": None, "coefficient": None, "asOf": None,
        "expectedAsOf": expected, "lagTradingDays": None, "priceKind": "daily_close",
        "sourceName": "已配置的通达信指数日线", "sourceUrl": None,
        "provider": None, "status": "unavailable", "stale": False, "cached": False,
        "receivedAt": None, "lastValidatedAt": None, "modelNote": MODEL_NOTE,
        "warnings": [], "errors": [],
    }
    try:
        # market.bars revalidates the chosen file on every read and uses an
        # enabled MCP when the requested local index is unavailable/unindexed.
        data = owner.bars({"symbol": "sh000001", "limit": 10})
        if data.get("symbol") != "sh000001" or data.get("kind") != "index":
            raise ValueError("大盘水位来源不是上证指数日线")
        rows = data.get("rows")
        if not isinstance(rows, list) or not rows:
            raise ValueError("尚未取得上证指数已收盘日线")
        usable, seen = [], set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("上证指数日线格式无效")
            day = _day(row.get("date"))
            close = row.get("close")
            if (day > now.date() or day in seen or isinstance(close, bool)
                    or not isinstance(close, (int, float)) or not math.isfinite(close) or close <= 0):
                raise ValueError("上证指数日线点位或交易日期无效")
            seen.add(day)
            calendar = trading_sessions(day.isoformat(), day.isoformat())
            if calendar == []:
                raise ValueError("上证指数日线包含非交易日数据")
            if day <= cutoff:
                usable.append((day, float(close)))
        if not usable:
            raise ValueError("来源只有今日未收盘日线，暂不据此计算收盘水位")
        day, points = max(usable)
        as_of = day.isoformat()
        lag_sessions = trading_sessions(as_of, expected) if expected else None
        lag = len([value for value in lag_sessions if value > as_of]) if lag_sessions is not None else None
        warnings = list(data.get("warnings", []))
        source_stale = bool(data.get("stale"))
        if expected is None:
            warnings.append("当前年份交易日历尚未覆盖，无法确认该日线是否为最近交易日。")
        elif as_of < expected:
            distance = f"共 {lag} 个交易日" if lag is not None else "（间隔超出已覆盖交易日历）"
            warnings.append(f"上证指数日线停留在 {as_of}，落后最近收盘日 {expected} {distance}；请更新所选行情源。")
        if len(usable) != len(rows):
            warnings.append("今日未收盘日线已排除，水位使用最近已收盘点位。")
        provider = data.get("provider") or "tdx_local"
        return {**result, "indexPoints": points, "rawCoefficient": points / BASELINE_POINTS,
                "coefficient": max(FLOOR_COEFFICIENT, points / BASELINE_POINTS), "asOf": as_of,
                "lagTradingDays": lag, "sourceName": "通达信 MCP 上证指数日线" if provider == "tdx_mcp" else "通达信本地上证指数日线",
                "provider": provider, "status": "ready", "stale": source_stale or expected is None or as_of != expected,
                "cached": bool(data.get("cached")), "receivedAt": data.get("lastReadAt"),
                "lastValidatedAt": data.get("lastValidatedAt"), "warnings": warnings}
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as error:
        message = str(error) if isinstance(error, ValueError) else "指数日线当前不可读取，未采用本次数据。"
        result["errors"] = [{"provider": "configured_market", "message": message}]
        result["warnings"] = ["尚无可用的上证指数收盘点位，水位系数保持缺失；请在设置中选择并更新行情目录，或连接通达信 MCP。"]
        return result
