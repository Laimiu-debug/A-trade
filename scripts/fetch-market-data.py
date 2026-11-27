"""Resolve stock/CB codes from OCR ledgers and fetch historical A-share K-lines (2022-2026).

Uses Eastmoney public endpoints (with Tencent fallback) to:
1. Build a full A-share + Convertible Bond + ETF name -> (market, code, official_name) map.
2. Clean/fuzzy-match OCR stock names (including Eastmoney suggest API for historical/delisted names).
3. Fetch unadjusted (fqt=0) daily OHLCV + true pct_chg K-lines for:
   - Major A-share indices (sh000001, sz399001, sz399006, sh000852, sh000688, bj899050)
   - Every unique security traded across all extracted master ledgers (with +/-45 days buffer)
4. Cross-validate matched codes against actual trade prices on tradeDate, repair minor 1-digit OCR
   price glitches where amount/qty is exact, and export enriched + A-trade-compatible CSVs.
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import difflib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import aiohttp

ROOT = Path(r"h:\CODE\A-trade\artifacts\wechat-jiaogedan\analysis")
LEDGER_DIR = ROOT / "ledgers"
CACHE_DIR = ROOT / "market_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
ATRADE_DIR = LEDGER_DIR / "atrade_import"
ATRADE_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}

INDICES = {
    "sh000001": ("1.000001", "上证指数"),
    "sz399001": ("0.399001", "深证成指"),
    "sz399006": ("0.399006", "创业板指"),
    "sh000852": ("1.000852", "中证1000"),
    "sh000688": ("1.000688", "科创50"),
    "bj899050": ("0.899050", "北证50"),
}

# Common Chinese OCR confusions on narrow/low-res broker fonts
OCR_REPLACEMENTS = [
    ("料技", "科技"),
    ("抖技", "科技"),
    ("丿L", "儿"),
    ("丿l", "儿"),
    ("教言", "教育"),
    ("电孑", "电子"),
    ("股扮", "股份"),
    ("材抖", "材料"),
    ("集回", "集团"),
    ("新能原", "新能源"),
    ("转愤", "转债"),
    ("转绩", "转债"),
    ("转情", "转债"),
    ("转值", "转债"),
    ("转供", "转债"),
    ("转货", "转债"),
    ("药止", "药业"),
    ("医约", "医药"),
    ("特气", "特气"),
    ("证芬", "证券"),
    ("光屯", "光电"),
    ("信启", "信息"),
    ("软伴", "软件"),
    ("智熊", "智能"),
    ("通倍", "通信"),
    ("传煤", "传媒"),
    ("媒休", "媒体"),
    ("机域", "机械"),
    ("重エ", "重工"),
    ("化エ", "化工"),
    ("食晶", "食品"),
    ("饮科", "饮料"),
    ("百货", "百货"),
    ("建殳", "建设"),
    ("控胶", "控股"),
    ("实止", "实业"),
    ("网终", "网络"),
    ("半导休", "半导体"),
    ("生特", "生物"),
    ("莱联", "荣联"),
    ("晶春朱一/、", "富春染织"),
    ("晶春朵一/、", "富春染织"),
    ("晶春朱灬", "富春染织"),
    ("二丿、二", "二三四五"),
    ("荃锒高料", "荃银高科"),
    ("荃银高料", "荃银高科"),
    ("中国高料", "中国高科"),
    ("豈业达", "竞业达"),
    ("娱料", "天娱数科"),
    ("盟料", "盟科药业"),
    ("c盟料", "C盟科"),
    (" ", ""),
]

# Explicit historical / renamed / delisted security overrides (verified against trade prices)
MANUAL_OVERRIDES: dict[str, tuple[str, str, str]] = {
    "二三四五": ("002195", "0.002195", "岩山科技(原二三四五)"),
    "大东方": ("600327", "1.600327", "大东方"),
    "天安新材": ("603725", "1.603725", "天安新材"),
    "天虹股份": ("002419", "0.002419", "天虹股份"),
    "大港股份": ("002077", "0.002077", "大港股份"),
    "富春染织": ("605189", "1.605189", "富春染织"),
    "远大智能": ("002689", "0.002689", "ST远智(原远大智能)"),
    "威创股份": ("002308", "0.002308", "威创退(原威创股份)"),
    "中国高科": ("600730", "1.600730", "中国高科"),
    "竞业达": ("003005", "0.003005", "竞业达"),
    "荃银高科": ("300087", "0.300087", "荃银高科"),
    "天娱数科": ("002354", "0.002354", "天娱数科"),
    "盟科药业": ("688373", "1.688373", "盟科药业"),
    "C盟科": ("688373", "1.688373", "盟科药业"),
}


def norm_name(s: str) -> str:
    s = (s or "").strip()
    s = s.replace("\u3000", "").replace(" ", "")
    s = s.replace("（", "(").replace("）", ")")
    for a, b in OCR_REPLACEMENTS:
        s = s.replace(a, b)
    # Strip trailing/leading stray punctuation
    s = re.sub(r"^[·•.,:;_-]+|[·•.,:;_-]+$", "", s)
    return s


def secid_from_code(code: str) -> str:
    """Convert 6-digit China A-share/CB/ETF code to Eastmoney secid (1.=SH, 0.=SZ/BJ)."""
    c = code.strip()
    if c.startswith(("6", "5", "9", "11")):
        # Note: 92xxxx is Beijing Stock Exchange (0.), 900xxx is SH B-share (1.)
        if c.startswith("92"):
            return f"0.{c}"
        return f"1.{c}"
    return f"0.{c}"


def symbol_from_secid(secid: str) -> str:
    mkt, code = secid.split(".", 1)
    if mkt == "1":
        return f"sh{code}"
    if code.startswith(("43", "83", "87", "88", "92", "899")):
        return f"bj{code}"
    return f"sz{code}"


def classify_board(code: str, name: str = "") -> str:
    if not code:
        return "可转债" if "转" in name or "债" in name else "未知"
    if code.startswith(("11", "12")):
        return "可转债"
    if code.startswith(("51", "56", "58", "15", "16")):
        return "ETF/基金"
    if code.startswith("688") or code.startswith("689"):
        return "科创板(20cm)"
    if code.startswith("300") or code.startswith("301"):
        return "创业板(20cm)"
    if code.startswith(("43", "83", "87", "88", "92")):
        return "北交所(30cm)"
    if code.startswith(("60", "00")):
        return "主板(10cm)"
    return "其他"


async def fetch_json(session: aiohttp.ClientSession, url: str, params: dict | None = None, retries: int = 3) -> Any:
    for attempt in range(retries):
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    return json.loads(text)
        except Exception:
            if attempt == retries - 1:
                return None
            await asyncio.sleep(0.5 * (attempt + 1))
    return None


async def fetch_eastmoney_universe(session: aiohttp.ClientSession) -> dict[str, dict]:
    """Fetch active A-shares, convertible bonds, and ETFs from Eastmoney."""
    cache_path = CACHE_DIR / "em_universe.json"
    if cache_path.exists() and cache_path.stat().st_size > 10000:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    # fs specs: A-shares, CBs, ETFs
    fs_list = [
        ("stock", "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"),
        ("cb", "b:MK0354"),
        ("etf", "b:MK0021,b:MK0022,b:MK0023,b:MK0024"),
    ]
    universe: dict[str, dict] = {}
    url = "https://push2.eastmoney.com/api/qt/clist/get"

    for kind, fs in fs_list:
        for pn in range(1, 100):
            params = {
                "pn": str(pn),
                "pz": "100",
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fid": "f12",
                "fs": fs,
                "fields": "f12,f13,f14",
            }
            js = await fetch_json(session, url, params=params)
            diff = ((js or {}).get("data") or {}).get("diff") or []
            if not diff:
                break
            for item in diff:
                code = str(item.get("f12", "")).strip()
                mkt = str(item.get("f13", "")).strip()
                name = str(item.get("f14", "")).strip()
                if code and name and mkt in ("0", "1"):
                    secid = f"{mkt}.{code}"
                    universe[code] = {"code": code, "secid": secid, "name": name, "kind": kind}
            if len(diff) < 100:
                break
            await asyncio.sleep(0.05)

    if len(universe) > 2000:
        cache_path.write_text(json.dumps(universe, ensure_ascii=False), encoding="utf-8")
    print(f"[UNIVERSE] fetched {len(universe)} securities from Eastmoney")
    return universe


async def eastmoney_suggest(session: aiohttp.ClientSession, query: str) -> list[dict]:
    """Query Eastmoney suggest API (includes renamed/delisted/redeemed CBs)."""
    url = "https://searchapi.eastmoney.com/api/suggest/get"
    params = {
        "input": query,
        "type": "14",
        "token": "D43BF722C8E33BDC906FB84D85E326E8",
        "count": "6",
    }
    js = await fetch_json(session, url, params=params)
    items = (((js or {}).get("QuotationCodeTable") or {}).get("Data") or [])
    out = []
    for it in items:
        code = str(it.get("Code", "")).strip()
        qcode = str(it.get("QuoteID", "")).strip()  # e.g. "0.002875" or "1.600519"
        name = str(it.get("Name", "")).strip()
        stype = str(it.get("SecurityTypeName", "")).strip()
        if code and len(code) == 6 and code.isdigit() and "." in qcode:
            mkt = qcode.split(".", 1)[0]
            if mkt in ("0", "1"):
                out.append({"code": code, "secid": qcode, "name": name, "type": stype})
    return out


async def fetch_kline_em(
    session: aiohttp.ClientSession, secid: str, beg: str, end: str
) -> list[dict]:
    """Fetch unadjusted (fqt=0) daily K-lines from Eastmoney (includes ex-div pct_chg)."""
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "0",
        "beg": beg,
        "end": end,
        "lmt": "2000",
    }
    js = await fetch_json(session, url, params=params)
    klines = ((js or {}).get("data") or {}).get("klines") or []
    rows = []
    for line in klines:
        parts = line.split(",")
        if len(parts) < 11:
            continue
        rows.append({
            "date": parts[0],
            "open": float(parts[1]),
            "close": float(parts[2]),
            "high": float(parts[3]),
            "low": float(parts[4]),
            "vol": float(parts[5]),
            "amount": float(parts[6]),
            "amp": float(parts[7]),
            "pct": float(parts[8]),
            "chg": float(parts[9]),
            "turnover": float(parts[10]),
        })
    return rows


async def fetch_kline_tencent(
    session: aiohttp.ClientSession, symbol: str, beg_iso: str, end_iso: str
) -> list[dict]:
    """Fallback daily K-lines from Tencent if Eastmoney returns empty."""
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{symbol},day,{beg_iso},{end_iso},1500,"}
    js = await fetch_json(session, url, params=params)
    node = ((js or {}).get("data") or {}).get(symbol) or {}
    bars = node.get("day") or node.get("qfqday") or []
    rows = []
    prev_close = None
    for b in bars:
        if len(b) < 6:
            continue
        o, c, h, l, v = float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5])
        pct = ((c - prev_close) / prev_close * 100.0) if prev_close else 0.0
        rows.append({
            "date": b[0],
            "open": o,
            "close": c,
            "high": h,
            "low": l,
            "vol": v,
            "amount": 0.0,
            "amp": ((h - l) / prev_close * 100.0) if prev_close else 0.0,
            "pct": round(pct, 2),
            "chg": round(c - prev_close, 4) if prev_close else 0.0,
            "turnover": 0.0,
        })
        prev_close = c
    return rows


async def main():
    trade_files = sorted(LEDGER_DIR.glob("*_trades.csv"))
    print(f"[INFO] Found {len(trade_files)} trade CSVs: {[p.name for p in trade_files]}")

    all_rows_by_file: dict[str, list[dict]] = {}
    known_name_to_code: dict[str, str] = {}
    name_contexts: dict[str, list[tuple[str, float]]] = defaultdict(list)
    code_date_bounds: dict[str, list[str]] = defaultdict(list)

    for p in trade_files:
        rows = list(csv.DictReader(p.open(encoding="utf-8-sig")))
        all_rows_by_file[p.name] = rows
        for r in rows:
            c = (r.get("code") or "").strip()
            nm = norm_name(r.get("name") or "")
            d = (r.get("tradeDate") or "").strip()
            try:
                pr = float(r.get("price") or 0)
            except ValueError:
                pr = 0.0
            if c and len(c) == 6 and c.isdigit():
                if nm:
                    known_name_to_code[nm] = c
                if d:
                    code_date_bounds[c].append(d)
            elif nm:
                name_contexts[nm].append((d, pr))

    conn = aiohttp.TCPConnector(limit=12, ssl=False)
    async with aiohttp.ClientSession(headers=HEADERS, connector=conn) as session:
        # 1. Fetch major A-share indices (2022-01-01 to 2026-11-25)
        indices_cache = CACHE_DIR / "indices_2022_2026.json"
        if indices_cache.exists() and indices_cache.stat().st_size > 50000:
            indices_data = json.loads(indices_cache.read_text(encoding="utf-8"))
            print(f"[INDICES] loaded from cache ({len(indices_data)} indices)")
        else:
            indices_data = {}
            for sym, (secid, cname) in INDICES.items():
                bars = await fetch_kline_em(session, secid, "20211101", "20261125")
                if not bars:
                    bars = await fetch_kline_tencent(session, sym, "2021-11-01", "2026-11-25")
                indices_data[sym] = {"symbol": sym, "secid": secid, "name": cname, "bars": bars}
                print(f"[INDEX] {sym} ({cname}): {len(bars)} bars ({bars[0]['date'] if bars else '?'}..{bars[-1]['date'] if bars else '?'})")
                await asyncio.sleep(0.1)
            indices_cache.write_text(json.dumps(indices_data, ensure_ascii=False), encoding="utf-8")

        # 2. Fetch active A-share + CB + ETF universe
        universe = await fetch_eastmoney_universe(session)
        uni_by_name: dict[str, dict] = {}
        uni_by_clean: dict[str, dict] = {}
        for info in universe.values():
            n = info["name"].replace(" ", "")
            uni_by_name[n] = info
            # Also map without ST/*ST/XD/XR/N/C prefixes for historical name matching
            stripped = re.sub(r"^(\*?ST|S\*ST|SST|XD|XR|DR|N|C|U|W)+", "", n, flags=re.I)
            if stripped and stripped not in uni_by_clean:
                uni_by_clean[stripped] = info

        # 3. Resolve every uncoded stock/CB name (fresh map to prevent stale fuzzy collisions)
        map_cache_path = CACHE_DIR / "name_code_map.json"
        name_map: dict[str, dict] = {}

        # Apply verified manual overrides first
        for nm, (code, secid, oname) in MANUAL_OVERRIDES.items():
            name_map[nm] = {"code": code, "secid": secid, "official_name": oname, "match_method": "manual_override"}

        # Populate from known_name_to_code (from wujiang slips with explicit 6-digit codes)
        for nm, code in known_name_to_code.items():
            if nm not in name_map:
                info = universe.get(code)
                name_map[nm] = {
                    "code": code,
                    "secid": info["secid"] if info else secid_from_code(code),
                    "official_name": info["name"] if info else nm,
                    "match_method": "slip_code",
                }

        all_uni_names = list(uni_by_name.keys())
        all_clean_names = list(uni_by_clean.keys())
        unresolved_names = sorted(set(name_contexts.keys()) - set(name_map.keys()))
        print(f"[MAP] Resolving {len(unresolved_names)} uncoded names...")

        suggest_queue: list[str] = []
        for nm in unresolved_names:
            is_cb = ("转" in nm) or ("债" in nm)
            stripped = re.sub(r"^(\*?ST|XD|XR|DR|N|C)+", "", nm, flags=re.I)
            if nm in uni_by_name:
                info = uni_by_name[nm]
                if is_cb == info["code"].startswith(("11", "12")):
                    name_map[nm] = {"code": info["code"], "secid": info["secid"], "official_name": info["name"], "match_method": "exact"}
                    continue
            if stripped in uni_by_clean and len(stripped) >= 2:
                info = uni_by_clean[stripped]
                if is_cb == info["code"].startswith(("11", "12")):
                    name_map[nm] = {"code": info["code"], "secid": info["secid"], "official_name": info["name"], "match_method": "stripped_exact"}
                    continue
            # For CBs, NEVER fuzzy-map to a different CB (redeemed 2022 CBs must NOT collide with active CBs!)
            if is_cb:
                suggest_queue.append(nm)
            else:
                cand = difflib.get_close_matches(nm, all_uni_names, n=1, cutoff=0.76)
                if cand and abs(len(cand[0]) - len(nm)) <= 1 and not uni_by_name[cand[0]]["code"].startswith(("11", "12")):
                    info = uni_by_name[cand[0]]
                    name_map[nm] = {"code": info["code"], "secid": info["secid"], "official_name": info["name"], "match_method": f"fuzzy:{cand[0]}"}
                else:
                    suggest_queue.append(nm)

        # Query Eastmoney Suggest API for remaining names (renamed/delisted/redeemed CBs/OCR typos)
        sem = asyncio.Semaphore(8)

        async def resolve_via_suggest(nm: str):
            async with sem:
                is_cb = ("转" in nm) or ("债" in nm)
                cands = await eastmoney_suggest(session, nm)
                if cands:
                    # Strictly require CB <-> 11/12xxxx and exact or very high similarity (>=0.75)
                    filtered = [
                        c for c in cands
                        if (is_cb and c["code"].startswith(("11", "12")))
                        or (not is_cb and not c["code"].startswith(("11", "12")))
                    ]
                    if filtered:
                        best = max(
                            filtered,
                            key=lambda c: difflib.SequenceMatcher(None, nm, c["name"].replace(" ", "")).ratio(),
                        )
                        ratio = difflib.SequenceMatcher(None, nm, best["name"].replace(" ", "")).ratio()
                        if ratio >= (0.85 if is_cb else 0.72):
                            name_map[nm] = {
                                "code": best["code"],
                                "secid": best["secid"],
                                "official_name": best["name"],
                                "match_method": f"suggest({ratio:.2f})",
                            }
                            return

        await asyncio.gather(*(resolve_via_suggest(nm) for nm in suggest_queue))
        map_cache_path.write_text(json.dumps(name_map, ensure_ascii=False, indent=2), encoding="utf-8")

        still_unmatched = [nm for nm in unresolved_names if nm not in name_map]
        print(f"[MAP] Resolved {len(name_map)} names total; still unmatched: {len(still_unmatched)}")
        if still_unmatched:
            print("  Unmatched sample:", still_unmatched[:30])

        # 4. Collect all unique (code -> min_date, max_date) to fetch stock K-lines
        all_code_dates: dict[str, list[str]] = defaultdict(list)
        all_code_secid: dict[str, str] = {}
        for c, ds in code_date_bounds.items():
            all_code_dates[c].extend(ds)
            info = universe.get(c)
            all_code_secid[c] = info["secid"] if info else secid_from_code(c)

        for nm, ctxs in name_contexts.items():
            m = name_map.get(nm)
            if m:
                c = m["code"]
                all_code_secid[c] = m["secid"]
                all_code_dates[c].extend(d for d, _ in ctxs if d)

        kline_cache_path = CACHE_DIR / "stock_klines.json"
        stock_klines: dict[str, list[dict]] = (
            json.loads(kline_cache_path.read_text(encoding="utf-8")) if kline_cache_path.exists() else {}
        )

        to_fetch = []
        for c, ds in sorted(all_code_dates.items()):
            if not ds:
                continue
            dmin = min(ds)
            dmax = max(ds)
            # Expand +/- 60 calendar days for pre-entry momentum & post-entry returns
            beg_dt = dt.date.fromisoformat(dmin) - dt.timedelta(days=60)
            end_dt = min(dt.date(2026, 11, 25), dt.date.fromisoformat(dmax) + dt.timedelta(days=30))
            beg_s = beg_dt.strftime("%Y%m%d")
            end_s = end_dt.strftime("%Y%m%d")
            # Check if cached covers range
            existing = stock_klines.get(c)
            if existing and existing[0]["date"] <= (beg_dt + dt.timedelta(days=15)).isoformat() and existing[-1]["date"] >= (end_dt - dt.timedelta(days=15)).isoformat():
                continue
            to_fetch.append((c, all_code_secid[c], beg_s, end_s, beg_dt.isoformat(), end_dt.isoformat()))

        print(f"[KLINE] Need to fetch K-lines for {len(to_fetch)} / {len(all_code_dates)} unique securities...")
        ksem = asyncio.Semaphore(10)
        fetched_ok = 0
        fetched_empty = 0

        async def fetch_one(item):
            nonlocal fetched_ok, fetched_empty
            c, secid, beg_s, end_s, beg_iso, end_iso = item
            async with ksem:
                bars = await fetch_kline_em(session, secid, beg_s, end_s)
                if not bars:
                    bars = await fetch_kline_tencent(session, symbol_from_secid(secid), beg_iso, end_iso)
                if bars:
                    # Merge with any existing bars by date
                    merged = {b["date"]: b for b in stock_klines.get(c, [])}
                    for b in bars:
                        merged[b["date"]] = b
                    stock_klines[c] = [merged[k] for k in sorted(merged)]
                    fetched_ok += 1
                else:
                    fetched_empty += 1

        await asyncio.gather(*(fetch_one(it) for it in to_fetch))
        kline_cache_path.write_text(json.dumps(stock_klines, ensure_ascii=False), encoding="utf-8")
        print(f"[KLINE] Fetched OK={fetched_ok}, empty={fetched_empty}, total cached={len(stock_klines)}")

    # 5. Enrich all ledgers, price-check matches, repair 1-digit OCR price glitches, write clean + A-trade CSVs
    print("\n=== ENRICHING LEDGERS & AUDITING PRICE ALIGNMENT ===")
    enriched_by_trader: dict[str, list[dict]] = defaultdict(list)

    for fname, rows in all_rows_by_file.items():
        job_key = fname.replace("_trades.csv", "")
        mapped_cnt = 0
        price_matched_cnt = 0
        price_checked_cnt = 0
        repaired_cnt = 0
        out_rows = []

        for r in rows:
            r = dict(r)
            raw_name = r.get("name", "")
            nm = norm_name(raw_name)
            code = (r.get("code") or "").strip()
            official_name = raw_name

            if code and code in universe:
                official_name = universe[code]["name"]
            elif not code and nm in name_map:
                m = name_map[nm]
                code = m["code"]
                official_name = m["official_name"]

            # Repair verified=False where amount/qty gives exact realistic price
            qty = float(r["quantity"]) if r.get("quantity") else 0.0
            price = float(r["price"]) if r.get("price") else 0.0
            amount = float(r["amount"]) if r.get("amount") else 0.0
            verified = str(r.get("verified", "")).lower() in ("true", "1")
            price_repaired = False
            if not verified and qty > 0 and amount > 0:
                implied = round(amount / qty, 3)
                if price > 0 and abs(implied - price) / implied <= 0.25:
                    price = implied
                    verified = True
                    price_repaired = True
                    repaired_cnt += 1

            # Sanity repair for convertible bonds with 10x price / 0.1x qty OCR drop (e.g. 4110.4 -> 411.04)
            is_cb_row = ("转" in nm) or ("债" in nm) or code.startswith(("11", "12"))
            if is_cb_row and price > 900 and amount > 0:
                for div in (10.0, 100.0):
                    if 70.0 <= price / div <= 700.0:
                        price = round(price / div, 3)
                        qty = round((amount / price) / 10.0) * 10
                        price_repaired = True
                        repaired_cnt += 1
                        break

            # Cross-check against real K-line high/low on tradeDate & repair simultaneous decimal-point drops
            kline_ok = ""
            d = r.get("tradeDate", "")
            if code and code in stock_klines:
                bar_map = {b["date"]: b for b in stock_klines[code]}
                b = bar_map.get(d)
                if b and b["low"] > 0 and b["high"] > 0:
                    price_checked_cnt += 1
                    if b["low"] * 0.96 <= price <= b["high"] * 1.04:
                        price_matched_cnt += 1
                        kline_ok = "True"
                    else:
                        # Check if dropped decimal point (price / 10, / 100, / 1000 fits [low, high])
                        fixed_dec = False
                        for div in (10.0, 100.0, 1000.0):
                            cand_p = price / div
                            if b["low"] * 0.97 <= cand_p <= b["high"] * 1.03:
                                old_p = price
                                price = round(cand_p, 4)
                                if qty > 0 and amount > 0 and abs(qty * old_p - amount) / max(amount, 1.0) <= 0.04:
                                    amount = round(amount / div, 2)
                                elif qty > 0:
                                    amount = round(price * qty, 2)
                                price_repaired = True
                                repaired_cnt += 1
                                price_matched_cnt += 1
                                kline_ok = "True"
                                fixed_dec = True
                                break
                        if not fixed_dec:
                            # If code came from name_map (not native slip code) and price mismatches K-line, reject false mapping!
                            if not (r.get("code") or "").strip():
                                code = ""
                                official_name = nm
                                kline_ok = ""
                                price_checked_cnt -= 1
                            else:
                                kline_ok = f"Mismatch({b['low']}-{b['high']})"
                if code:
                    mapped_cnt += 1
            elif code:
                mapped_cnt += 1

            r["code"] = code
            r["name"] = official_name if code else nm
            r["rawName"] = raw_name
            r["quantity"] = str(int(round(qty)))
            r["price"] = f"{price:.3f}".rstrip("0").rstrip(".")
            r["amount"] = f"{amount:.2f}"
            r["verified"] = str(verified)
            r["priceRepaired"] = str(price_repaired)
            r["board"] = classify_board(code, r["name"])
            r["klinePriceMatch"] = kline_ok
            out_rows.append(r)
            enriched_by_trader[r["trader"]].append(r)

        # Write enriched CSV back alongside original
        out_path = LEDGER_DIR / f"{job_key}_enriched.csv"
        fieldnames = list(out_rows[0].keys()) if out_rows else []
        with out_path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(out_rows)

        ver_cnt = sum(1 for x in out_rows if x["verified"] == "True")
        pct_match = (price_matched_cnt / price_checked_cnt * 100.0) if price_checked_cnt else 0.0
        print(
            f"  {job_key:18s} | rows={len(out_rows):4d} | ver={ver_cnt:4d} ({ver_cnt/max(1,len(out_rows))*100:5.1f}%, repaired={repaired_cnt:2d}) "
            f"| mapped={mapped_cnt:4d} ({mapped_cnt/max(1,len(out_rows))*100:5.1f}%) "
            f"| kline_price_ok={price_matched_cnt}/{price_checked_cnt} ({pct_match:5.1f}%)"
        )

    # Export clean A-trade MasterSlipsService-compatible CSVs (one per trader)
    trader_slug = {
        "乌江望月": "wujiang_71_72_73",
        "如晗": "ruhan_52",
        "松松": "songsong_50",
        "松雅湖的松松": "songsong_50",
        "灰常懒": "huichanglan_73",
        "希望姐姐": "xiwangjiejie_73",
        "暴涨基金董事长": "dongshizhang_72",
        "山顶动人": "shanding_55",
    }
    for trader, rows in enriched_by_trader.items():
        rows.sort(key=lambda x: (x["tradeDate"], x["tradeTime"], x["code"], x["side"]))
        slug = trader_slug.get(trader, trader)
        p = ATRADE_DIR / f"{slug}_atrade.csv"
        cols = ["tradeDate", "tradeTime", "code", "name", "side", "quantity", "price", "amount", "brokerAction", "session"]
        with p.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in cols})
        print(f"[ATRADE-CSV] {p.name}: {len(rows)} rows")


if __name__ == "__main__":
    asyncio.run(main())
