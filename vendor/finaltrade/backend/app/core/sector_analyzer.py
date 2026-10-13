"""
板块分析模块 — 从趋势为王移植

提供:
  - 27 个行业板块代码映射
  - 板块指数 K 线加载
  - 板块动量排名
  - 个股-板块相关性匹配
  - 带缓存的 SectorAnalysisCache
"""
from __future__ import annotations

import math
import statistics
import struct
import time
from pathlib import Path
from typing import Any

import numpy as np

DAY_RECORD = struct.Struct("<IIIIIfII")

SECTOR_CODES: dict[str, list[str]] = {
    "煤炭": ["881001", "881002", "881005"],
    "石油石化": ["881006", "881007", "881008", "881011"],
    "化工": ["881015", "881016", "881019", "881026", "881034", "881044", "881051", "881055"],
    "钢铁": ["881061", "881062", "881065", "881069"],
    "有色": ["881070", "881071", "881075", "881078", "881082", "881087"],
    "建材": ["881090", "881091", "881094", "881097", "881104"],
    "农林牧渔": ["881105", "881106", "881111", "881115", "881116", "881119", "881123", "881127"],
    "食品饮料": ["881129", "881130", "881136", "881139", "881140", "881144"],
    "纺织服饰": ["881150", "881151", "881157", "881162"],
    "轻工": ["881166", "881167", "881171", "881177", "881180"],
    "家电": ["881183", "881184", "881187", "881190", "881194", "881198"],
    "商贸": ["881199", "881200", "881204", "881205", "881206", "881207"],
    "汽车": ["881211", "881212", "881215", "881218", "881224", "881227"],
    "医药": ["881230", "881231", "881234", "881241", "881247", "881252", "881256", "881257"],
    "电力设备": ["881260", "881261", "881262", "881268", "881275", "881282", "881285"],
    "军工": ["881286", "881287", "881288", "881289", "881290", "881291"],
    "机械": ["881292", "881293", "881294", "881303", "881310", "881313"],
    "电子": ["881318", "881319", "881326", "881329", "881333", "881336"],
    "通信": ["881337", "881338", "881344", "881347"],
    "计算机": ["881351", "881352", "881355", "881359", "881364"],
    "传媒": ["881368", "881369", "881370", "881373", "881376", "881380", "881384"],
    "银行": ["881385", "881386", "881389"],
    "非银": ["881393", "881394", "881395", "881396"],
    "建筑": ["881405", "881406", "881407", "881410", "881415", "881416"],
    "房地产": ["881417", "881418", "881422"],
    "社会服务": ["881426", "881427", "881428", "881429", "881432", "881436"],
    "交运": ["881441", "881442", "881446", "881449", "881452"],
    "公用事业": ["881458", "881459", "881467", "881468"],
    "环保": ["881469", "881470", "881471", "881476"],
    "综合": ["881477", "881478"],
}

_CODE_TO_SECTOR: dict[str, str] = {}
for _name, _codes in SECTOR_CODES.items():
    for _c in _codes:
        _CODE_TO_SECTOR[_c] = _name


def _parse_sector_day(fp: Path) -> list[dict[str, Any]] | None:
    """解析单个板块指数 .day 文件"""
    try:
        raw = fp.read_bytes()
    except OSError:
        return None
    bars: list[dict[str, Any]] = []
    for offset in range(0, len(raw), DAY_RECORD.size):
        chunk = raw[offset : offset + DAY_RECORD.size]
        if len(chunk) < DAY_RECORD.size:
            continue
        day, o, h, l, c, amount, vol, _ = DAY_RECORD.unpack(chunk)
        if day <= 19900101 or c <= 0:
            continue
        bars.append({"date": day, "close": c / 100.0, "amount": float(amount), "vol": vol})
    return bars if bars else None


def load_sector_candles(tdx_data_path: str) -> dict[str, list[dict[str, Any]]]:
    """加载所有板块指数 K 线, 返回 {code: [bars]}"""
    tdx_root = Path(tdx_data_path)
    # tdx_data_path 通常指向 vipdoc, 向上一级取根目录
    if tdx_root.name in ("vipdoc", "vipdoc"):
        tdx_root = tdx_root.parent

    all_codes: set[str] = set()
    for codes in SECTOR_CODES.values():
        all_codes.update(codes)

    result: dict[str, list[dict[str, Any]]] = {}
    for code in all_codes:
        found = False
        for market in ("sh", "sz"):
            day_file = tdx_root / market / "lday" / f"{market}{code}.day"
            if not day_file.exists():
                day_file = tdx_root / "vipdoc" / market / "lday" / f"{market}{code}.day"
            if day_file.exists():
                bars = _parse_sector_day(day_file)
                if bars:
                    result[code] = bars
                    found = True
                    break
        if not found:
            # 尝试不带市场前缀
            for market in ("sh", "sz"):
                day_file = tdx_root / market / "lday" / f"{code}.day"
                if not day_file.exists():
                    day_file = tdx_root / "vipdoc" / market / "lday" / f"{code}.day"
                if day_file.exists():
                    bars = _parse_sector_day(day_file)
                    if bars:
                        result[code] = bars
                        break
    return result


def compute_sector_scores(
    sector_candles: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """计算板块动量排名, 返回 {sector_name: {score, m5, m20, rank}}"""
    sector_scores: dict[str, dict[str, Any]] = {}
    for name, codes in SECTOR_CODES.items():
        moms: list[dict[str, Any]] = []
        for code in codes:
            bars = sector_candles.get(code)
            if not bars or len(bars) < 21:
                continue
            closes = [b["close"] for b in bars]
            if closes[-21] <= 0:
                continue
            m5 = 0.0
            if len(closes) >= 6 and closes[-6] > 0:
                m5 = (closes[-1] - closes[-6]) / closes[-6] * 100.0
            m20 = (closes[-1] - closes[-21]) / closes[-21] * 100.0
            ma5 = _sma(closes, 5)
            ma20 = _sma(closes, 20)
            ma_ok = ma5 is not None and ma20 is not None and ma5 > ma20
            moms.append({"m5": m5, "m20": m20, "ma_ok": ma_ok})
        if not moms:
            continue
        avg_m5 = statistics.mean(m["m5"] for m in moms)
        avg_m20 = statistics.mean(m["m20"] for m in moms)
        pct_ma = sum(1 for m in moms if m["ma_ok"]) / len(moms) * 100.0
        score = avg_m5 * 0.4 + avg_m20 * 0.4 + (pct_ma - 50) * 0.2
        sector_scores[name] = {
            "score": round(score, 2),
            "m5": round(avg_m5, 2),
            "m20": round(avg_m20, 2),
        }

    ranked = sorted(sector_scores.items(), key=lambda x: -x[1]["score"])
    for i, (name, _) in enumerate(ranked):
        sector_scores[name]["rank"] = i + 1
    return sector_scores


def match_stock_sector(
    stock_closes: list[float],
    sector_candles: dict[str, list[dict[str, Any]]],
    sector_scores: dict[str, dict[str, Any]],
) -> tuple[str | None, int, float]:
    """计算个股与所有板块的相关性, 返回 (sector_name, sector_rank, sector_m5)"""
    window = 41
    if len(stock_closes) < window:
        return None, 99, 0.0

    # 计算个股收益率
    stock_arr = np.array(stock_closes[-window:], dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        stock_ret = np.diff(stock_arr) / stock_arr[:-1]
    stock_ret = np.nan_to_num(stock_ret, nan=0.0)

    best_corr = -1.0
    best_sector: str | None = None

    for name, codes in SECTOR_CODES.items():
        # 使用该板块下所有成分指数, 取平均相关性
        corrs: list[float] = []
        for code in codes:
            bars = sector_candles.get(code)
            if not bars or len(bars) < window:
                continue
            sector_closes = [b["close"] for b in bars[-window:]]
            if len(sector_closes) < window:
                continue
            sec_arr = np.array(sector_closes, dtype=np.float64)
            # 按日期对齐: 取最后 len-1 个收益率
            with np.errstate(divide="ignore", invalid="ignore"):
                sec_ret = np.diff(sec_arr) / sec_arr[:-1]
            sec_ret = np.nan_to_num(sec_ret, nan=0.0)

            n = min(len(stock_ret), len(sec_ret))
            if n < 20:
                continue
            s_r = stock_ret[-n:]
            c_r = sec_ret[-n:]
            denom = np.sqrt(np.sum((s_r - s_r.mean()) ** 2) * np.sum((c_r - c_r.mean()) ** 2))
            if denom > 0:
                corr = float(np.sum((s_r - s_r.mean()) * (c_r - c_r.mean())) / denom)
                corrs.append(corr)

        if corrs:
            avg_corr = sum(corrs) / len(corrs)
            if avg_corr > best_corr:
                best_corr = avg_corr
                best_sector = name

    if best_corr < 0.3 or best_sector is None:
        return None, 99, 0.0

    info = sector_scores.get(best_sector, {})
    rank = int(info.get("rank", 99))
    m5 = float(info.get("m5", 0.0))
    return best_sector, rank, m5


class SectorAnalysisCache:
    """带 TTL 的板块分析缓存"""

    _TTL_SECONDS = 1800  # 30 分钟

    def __init__(self) -> None:
        self._sector_candles: dict[str, list[dict[str, Any]]] | None = None
        self._sector_scores: dict[str, dict[str, Any]] | None = None
        self._loaded_at: float = 0.0

    def is_expired(self) -> bool:
        if self._sector_candles is None:
            return True
        return (time.monotonic() - self._loaded_at) > self._TTL_SECONDS

    def refresh(self, tdx_data_path: str) -> None:
        candles = load_sector_candles(tdx_data_path)
        if candles:
            self._sector_candles = candles
            self._sector_scores = compute_sector_scores(candles)
            self._loaded_at = time.monotonic()

    @property
    def sector_candles(self) -> dict[str, list[dict[str, Any]]]:
        return self._sector_candles or {}

    @property
    def sector_scores(self) -> dict[str, dict[str, Any]]:
        return self._sector_scores or {}

    def lookup(
        self,
        stock_closes: list[float],
    ) -> tuple[str | None, int, float]:
        """返回 (sector_name, sector_rank, sector_m5)"""
        if not self._sector_candles:
            return None, 99, 0.0
        return match_stock_sector(stock_closes, self._sector_candles, self.sector_scores)


def _sma(vals: list[float], n: int) -> float | None:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n
