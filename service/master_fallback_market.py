"""Shared loader for A-trade built-in master presets and compact historical K-line fallback.

Allows T11 (Master Slips), Master Daily Advisor, and Master Strategy Backtester to work
seamlessly out of the box even before local Tongdaxin is configured, while ALWAYS
preferring local Tongdaxin data whenever it is configured and covers the requested symbol/date.
"""
from __future__ import annotations

import gzip
import json
from functools import lru_cache
from pathlib import Path
from statistics import mean
from typing import Any

PRESET_DIR = Path(__file__).resolve().parent / "master_presets"
BUNDLE_PATH = PRESET_DIR / "masters_bundle.json.gz"
FALLBACK_PATH = PRESET_DIR / "market_fallback.json.gz"


@lru_cache(maxsize=1)
def load_presets_bundle() -> dict[str, Any]:
    if not BUNDLE_PATH.is_file():
        return {"schema": "a-trade.master-presets.v1", "presets": []}
    with gzip.open(BUNDLE_PATH, "rt", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _raw_fallback() -> dict[str, Any]:
    if not FALLBACK_PATH.is_file():
        return {"indices": {}, "stocks": {}, "names": {}}
    with gzip.open(FALLBACK_PATH, "rt", encoding="utf-8") as f:
        return json.load(f)


def _decode_bars(compact_rows: list[list[Any]]) -> list[dict[str, Any]]:
    out = []
    for r in compact_rows:
        if len(r) < 8:
            continue
        out.append({
            "date": r[0],
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
            "amount": float(r[6]),
            "pct": float(r[7]),
        })
    return out


@lru_cache(maxsize=600)
def get_fallback_stock_bars(symbol: str) -> list[dict[str, Any]]:
    raw = _raw_fallback().get("stocks") or {}
    rows = raw.get(symbol.lower())
    return _decode_bars(rows) if rows else []


@lru_cache(maxsize=16)
def get_fallback_index_bars(symbol: str) -> list[dict[str, Any]]:
    raw = _raw_fallback().get("indices") or {}
    node = raw.get(symbol.lower())
    return _decode_bars(node.get("bars") or []) if node else []


def get_fallback_names() -> dict[str, str]:
    return dict(_raw_fallback().get("names") or {})


def get_fallback_all_stock_symbols() -> list[str]:
    return sorted((_raw_fallback().get("stocks") or {}).keys())


@lru_cache(maxsize=1)
def build_fallback_market_env() -> dict[str, dict[str, Any]]:
    """Build causal daily market regime & proxy breadth table from the 6 bundled indices + 544 stocks."""
    sh = get_fallback_index_bars("sh000001")
    sz = get_fallback_index_bars("sz399001")
    cyb = get_fallback_index_bars("sz399006")
    zz1k = get_fallback_index_bars("sh000852")
    kc50 = get_fallback_index_bars("sh000688")
    if not sh:
        return {}

    def index_map(bars: list[dict]) -> dict[str, tuple[int, dict]]:
        return {b["date"]: (i, b) for i, b in enumerate(bars)}

    m_sh, m_sz, m_cyb, m_zz, m_kc = (
        index_map(sh), index_map(sz), index_map(cyb), index_map(zz1k), index_map(kc50)
    )

    # Aggregate daily breadth proxy across bundled stock sample
    adv_by_day: dict[str, list[int]] = {}
    lu_by_day: dict[str, int] = {}
    ld_by_day: dict[str, int] = {}
    for sym in get_fallback_all_stock_symbols():
        if sym[2:].startswith(("11", "12", "51", "56", "58", "15", "16")):
            continue
        lim = 20.0 if sym.startswith(("sh68", "sz30")) else (30.0 if sym.startswith("bj") else 10.0)
        bars = get_fallback_stock_bars(sym)
        for b in bars:
            d = b["date"]
            slot = adv_by_day.setdefault(d, [0, 0])
            slot[1] += 1
            if b["pct"] > 0:
                slot[0] += 1
            if b["pct"] >= lim - 0.5 and abs(b["close"] - b["high"]) / max(b["high"], 0.01) < 0.006:
                lu_by_day[d] = lu_by_day.get(d, 0) + 1
            elif b["pct"] <= -(lim - 0.5) and abs(b["close"] - b["low"]) / max(b["low"], 0.01) < 0.006:
                ld_by_day[d] = ld_by_day.get(d, 0) + 1

    env: dict[str, dict[str, Any]] = {}
    for i, b_sh in enumerate(sh):
        d = b_sh["date"]
        sh_amt = b_sh["amount"]
        sz_amt = m_sz[d][1]["amount"] if d in m_sz else 0.0
        to_yi = round((sh_amt + sz_amt) / 1e8, 1)

        def stats(bars_list: list[dict], idx_m: dict[str, tuple[int, dict]]):
            if d not in idx_m:
                return None, None, None, None, None
            pos, bar = idx_m[d]
            ma20 = mean(x["close"] for x in bars_list[max(0, pos - 19):pos + 1])
            ret5 = ((bar["close"] / bars_list[pos - 5]["close"]) - 1.0) * 100.0 if pos >= 5 else 0.0
            ret20 = ((bar["close"] / bars_list[pos - 20]["close"]) - 1.0) * 100.0 if pos >= 20 else 0.0
            return bar["pct"], bar["close"] >= ma20, round(ret5, 2), round(ret20, 2), round(ma20, 2)

        sh_pct, sh_abv, sh_r5, sh_r20, _ = stats(sh, m_sh)
        cyb_pct, cyb_abv, _, cyb_r20, _ = stats(cyb, m_cyb)
        zz_pct, zz_abv, zz_r5, zz_r20, _ = stats(zz1k, m_zz)
        kc_pct, kc_abv, kc_r5, kc_r20, _ = stats(kc50, m_kc)

        r20 = zz_r20 if zz_r20 is not None else (sh_r20 or 0.0)
        abv = zz_abv if zz_abv is not None else bool(sh_abv)
        r5 = zz_r5 if zz_r5 is not None else (sh_r5 or 0.0)

        if abv and r20 >= 3.0:
            stage, stage_label = "advance", "主升"
        elif (not abv) and r20 <= -3.0:
            stage, stage_label = "decline", "主跌退潮"
        elif abv and r5 >= 1.5:
            stage, stage_label = "trial", "低位试错"
        else:
            stage, stage_label = "chop", "高位震荡"

        adv_cnt, tot_cnt = adv_by_day.get(d, [0, 0])
        adv_ratio = round(adv_cnt / tot_cnt * 100.0, 1) if tot_cnt >= 10 else None
        # Scale sample limit-up count to rough market-equivalent for UI readability when TDX absent
        lu_sample = lu_by_day.get(d, 0)
        ld_sample = ld_by_day.get(d, 0)

        env[d] = {
            "date": d,
            "stage": stage,
            "stageLabel": stage_label,
            "shChangePct": round(sh_pct or 0.0, 2),
            "cybChangePct": round(cyb_pct or 0.0, 2) if cyb_pct is not None else None,
            "zz1000ChangePct": round(zz_pct or 0.0, 2) if zz_pct is not None else None,
            "kc50ChangePct": round(kc_pct or 0.0, 2) if kc_pct is not None else None,
            "zz1000Ret20": zz_r20,
            "zz1000AboveMA20": zz_abv,
            "kc50Ret20": kc_r20,
            "kc50AboveMA20": kc_abv,
            "turnoverYi": to_yi,
            "advanceRatio": adv_ratio,
            "aboveMA20Ratio": 65.0 if abv else 35.0,
            "limitUpCount": lu_sample,
            "limitDownCount": ld_sample,
            "maxHeight": None,
            "premium": None,
            "promotionRate": None,
            "source": "bundled_fallback",
        }
    return env
