#!/usr/bin/env python3
"""Self-verifying WinRT OCR settlement-statement extractor for 頑主杯 master traders.

Supports 4 broker slip layouts observed in our downloaded 顽主杯 archives:
  1. `eastmoney_dark`  : 东方财富信用交易历史成交 (乌江望月 71/72/73届)
  2. `excel_export`    : 白底券商导出交割单表格 (如晗 52届, 松松 50届)
  3. `ths_fill`        : 同花顺深色/浅色历史成交 (灰常懒 73届, 山顶动人 55届)
  4. `ths_entrust`     : 同花顺深色历史委托+成交 (暴涨基金董事长 72届)

Outputs `A-trade` MasterSlipsService-compatible CSV ledgers with mathematical
`price * quantity ≈ amount` verification and full OCR caching.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageEnhance, ImageOps

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_ROOT = REPO_ROOT / "artifacts" / "wechat-jiaogedan" / "downloads" / "顽主杯全量2022_2026" / "01_专场与交割单"
ANALYSIS_DIR = REPO_ROOT / "artifacts" / "wechat-jiaogedan" / "analysis"
OCR_CACHE_DIR = ANALYSIS_DIR / "ocr_cache"
LEDGER_DIR = ANALYSIS_DIR / "ledgers"
WINRT_PS1 = REPO_ROOT / "scripts" / "winrt-ocr-batch.ps1"

DIGIT_FIX = str.maketrans(
    {
        "O": "0",
        "o": "0",
        "〇": "0",
        "I": "1",
        "l": "1",
        "|": "1",
        "卜": "1",
        "丨": "1",
        "S": "5",
        "s": "5",
        "Z": "2",
        "B": "8",
        "，": ".",
        "．": ".",
        "·": ".",
        "。": ".",
        "：": ":",
        "；": ":",
        "一": "-",
        "—": "-",
        "–": "-",
    }
)

TRADER_JOBS: dict[str, list[dict[str, Any]]] = {
    "wujiang_73": [
        {
            "article_id": "417057263",
            "trader": "乌江望月",
            "session": "第73届(2026-09~11)",
            "layout": "eastmoney_dark",
            "img_range": (26, 50),
        }
    ],
    "wujiang_72": [
        {
            "article_id": "409343827",
            "trader": "乌江望月",
            "session": "第72届(2026-07~09)",
            "layout": "eastmoney_dark",
            "img_range": (17, 36),
        }
    ],
    "wujiang_71": [
        {
            "article_id": "398851384",
            "trader": "乌江望月",
            "session": "第71届(2026-05~07)",
            "layout": "eastmoney_dark",
            "img_range": (22, 31),
        }
    ],
    "ruhan_52": [
        {
            "article_id": "240824020",
            "trader": "如晗",
            "session": "第52届(2022-11~2023-01)",
            "layout": "excel_export",
            "has_balance_col": False,
            "img_range": (17, 20),
        }
    ],
    "songsong_50": [
        {
            "article_id": "230696083",
            "trader": "松雅湖的松松",
            "session": "第50届(2022-07~09)",
            "layout": "excel_export",
            "has_balance_col": True,
            "img_range": (12, 22),
        }
    ],
    "huichanglan_73": [
        {
            "article_id": "417058442",
            "trader": "灰常懒",
            "session": "第73届千万组冠军(2026-09~11)",
            "layout": "ths_fill_dark",
            "img_range": (20, 26),
        }
    ],
    "xiwangjiejie_73": [
        {
            "article_id": "417057733",
            "trader": "希望姐姐",
            "session": "第73届千万组榜眼(2026-09~11)",
            "layout": "ths_fill_dark",
            "img_range": (20, 25),
        }
    ],
    "dongshizhang_72": [
        {
            "article_id": "409200132",
            "trader": "暴涨基金董事长",
            "session": "第72届冠军(2026-07~09)",
            "layout": "ths_fill_dark",
            "img_range": (22, 25),
        },
        {
            "article_id": "409200132",
            "trader": "暴涨基金董事长",
            "session": "第72届冠军(2026-07~09)",
            "layout": "ths_entrust_dark",
            "orders_only": True,
            "img_range": (26, 33),
        },
    ],
    "shanding_55": [
        {
            "article_id": "259566201",
            "trader": "山顶动人",
            "session": "第55届冠军(2023-07~08)",
            "layout": "ths_fill_light",
            "img_range": (21, 50),
        }
    ],
}


def find_article_dir(article_id: str) -> Path:
    matches = list(SPEC_ROOT.glob(f"*_{article_id}"))
    if not matches:
        raise FileNotFoundError(f"No article directory ending with _{article_id} in {SPEC_ROOT}")
    return matches[0]


def get_layout_preprocess(layout: str) -> tuple[float, bool, int, int]:
    """Return `(scale, invert, strip_h, overlap)`."""
    if layout in ("eastmoney_dark", "ths_fill_dark", "ths_entrust_dark"):
        return 1.5, True, 1300, 170
    if layout == "excel_export":
        return 3.0, False, 740, 90
    if layout == "ths_fill_light":
        # Narrow 355px-wide THS screenshots need shorter square-ish strips + 4x upscale so WinRT doesn't skew text angle
        return 4.0, False, 340, 85
    return 1.5, False, 1300, 160


def run_ocr_for_image(img_path: Path, cache_path: Path, layout: str, force: bool = False) -> dict[str, Any]:
    if cache_path.exists() and not force:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    scale, invert, strip_h, overlap = get_layout_preprocess(layout)
    tmp_dir = Path(tempfile.mkdtemp(prefix="ocr_strip_"))
    strip_meta: list[dict[str, Any]] = []
    strip_paths: list[str] = []

    try:
        with Image.open(img_path) as raw_im:
            im = raw_im.convert("RGB")
            orig_w, orig_h = im.size
            y0 = 0
            idx = 0
            while y0 < orig_h:
                y1 = min(orig_h, y0 + strip_h)
                crop = im.crop((0, y0, orig_w, y1))
                if invert:
                    crop = ImageOps.invert(crop)
                crop = ImageOps.grayscale(crop)
                if layout == "ths_fill_light":
                    crop = ImageOps.autocontrast(crop, cutoff=1)
                    crop = ImageEnhance.Contrast(crop).enhance(2.2)
                    crop = ImageEnhance.Sharpness(crop).enhance(1.8)
                else:
                    crop = ImageEnhance.Contrast(crop).enhance(1.6)
                eff_scale = scale
                nw, nh = int(crop.width * eff_scale), int(crop.height * eff_scale)
                if max(nw, nh) > 2450:
                    eff_scale = 2450.0 / max(crop.width, crop.height)
                    nw, nh = int(crop.width * eff_scale), int(crop.height * eff_scale)
                crop = crop.resize((nw, nh), Image.LANCZOS)
                sp = tmp_dir / f"s_{idx:03d}.png"
                crop.save(sp)
                strip_paths.append(str(sp))
                strip_meta.append({"y0": y0, "y1": y1, "scale": eff_scale, "w": nw, "h": nh})
                idx += 1
                if y1 >= orig_h:
                    break
                y0 = max(y0 + 1, y1 - overlap)

        in_j = tmp_dir / "in.json"
        out_j = tmp_dir / "out.json"
        in_j.write_text(json.dumps(strip_paths, ensure_ascii=False), encoding="utf-8")
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(WINRT_PS1),
                "-InputJson",
                str(in_j),
                "-OutputJson",
                str(out_j),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        raw_res = json.loads(out_j.read_text(encoding="utf-8"))
        if isinstance(raw_res, dict):
            raw_res = [raw_res]

        strips_out = []
        for meta, r in zip(strip_meta, raw_res):
            words = []
            for ln in r.get("lines") or []:
                for w in ln.get("words") or []:
                    words.append(
                        {
                            "t": w["t"],
                            "xf": round(w["x"] / max(1.0, meta["w"]), 4),
                            "x": round(w["x"] / meta["scale"], 1),
                            "y": round(meta["y0"] + w["y"] / meta["scale"], 1),
                            "yc": round(meta["y0"] + (w["y"] + w["h"] * 0.5) / meta["scale"], 1),
                            "w": round(w["w"] / meta["scale"], 1),
                            "h": round(w["h"] / meta["scale"], 1),
                        }
                    )
            strips_out.append({"y0": meta["y0"], "y1": meta["y1"], "ok": r.get("ok", False), "words": words})

        payload = {
            "image": img_path.name,
            "orig_w": orig_w,
            "orig_h": orig_h,
            "layout": layout,
            "strips": strips_out,
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload
    finally:
        for p in tmp_dir.glob("*"):
            try:
                p.unlink()
            except Exception:
                pass
        try:
            tmp_dir.rmdir()
        except Exception:
            pass


def clean_num_str(s: str) -> str:
    return re.sub(r"\s+", "", s.translate(DIGIT_FIX))


def parse_float(s: str) -> float | None:
    c = clean_num_str(s)
    c = re.sub(r"[^0-9.\-]", "", c)
    # Fix multiple dots (keep last dot as decimal if needed)
    if c.count(".") > 1:
        parts = c.split(".")
        c = "".join(parts[:-1]) + "." + parts[-1]
    if not c or c in ("-", ".", "-."):
        return None
    try:
        return float(c)
    except ValueError:
        return None


def parse_date_token(s: str) -> str | None:
    c = clean_num_str(s)
    digits = re.sub(r"\D", "", c)
    if len(digits) >= 8:
        m = re.search(r"(202[0-9])(0[1-9]|1[0-2])([0-3][0-9])", digits)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= d <= 31:
                return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def parse_time_token(s: str) -> str | None:
    c = clean_num_str(s)
    m = re.search(r"(\d{1,2}):(\d{2}):(\d{2})", c)
    if m:
        hh, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59:
            return f"{hh:02d}:{mm:02d}:{ss:02d}"
    digits = re.sub(r"\D", "", c)
    if len(digits) == 6:
        hh, mm, ss = int(digits[:2]), int(digits[2:4]), int(digits[4:6])
        if 0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59:
            return f"{hh:02d}:{mm:02d}:{ss:02d}"
    return None


def cluster_rows(words: list[dict[str, Any]], y_tol: float) -> list[dict[str, Any]]:
    ws = sorted(words, key=lambda w: (w["yc"], w["x"]))
    rows: list[dict[str, Any]] = []
    for w in ws:
        if rows and abs(w["yc"] - rows[-1]["yc"]) <= y_tol:
            rows[-1]["ws"].append(w)
            rows[-1]["yc"] = sum(z["yc"] for z in rows[-1]["ws"]) / len(rows[-1]["ws"])
        else:
            rows.append({"yc": w["yc"], "ws": [w]})
    for r in rows:
        r["ws"].sort(key=lambda z: z["x"])
    return rows


def join_col(ws: list[dict[str, Any]], x_lo: float, x_hi: float) -> str:
    return "".join(w["t"] for w in ws if x_lo <= w["xf"] < x_hi).strip()


def split_qty_amount(raw_text: str, price: float | None) -> tuple[int | None, float | None, bool]:
    """Disambiguate Eastern Wealth's closely placed `quantity` + `amount` digits using `price`."""
    c = clean_num_str(raw_text)
    # Remove any stray non-digit/dot characters
    c = re.sub(r"[^0-9.]", "", c)
    if not c:
        return None, None, False
    # Handle decimal part of amount (typically 2 or 3 decimal digits at the end)
    if "." in c:
        int_part, dec_part = c.rsplit(".", 1)
        int_part = int_part.replace(".", "")
        dec_part = dec_part[:3]
    else:
        int_part, dec_part = c, "000"

    if not int_part or len(int_part) < 2:
        return None, None, False

    if not price or price <= 0:
        return None, None, False

    best: tuple[float, int, float] | None = None
    for k in range(1, len(int_part)):
        q_s = int_part[:k]
        a_s = int_part[k:]
        if q_s.startswith("0") or (len(a_s) > 1 and a_s.startswith("0")):
            continue
        q = int(q_s)
        amt = float(f"{a_s}.{dec_part}")
        if amt <= 0 or q <= 0:
            continue
        est = q * price
        rel_err = abs(est - amt) / max(amt, 1.0)
        # Small bonus for lot-aligned shares (multiple of 100 or 10)
        penalty = 0.0 if (q % 100 == 0) else (0.0005 if q % 10 == 0 else 0.001)
        score = rel_err + penalty
        if best is None or score < best[0]:
            best = (score, q, amt)

    if best and best[0] <= 0.03:
        return best[1], round(best[2], 3), (abs(best[1] * price - best[2]) / max(best[2], 1.0) <= 0.02)
    return None, None, False


def normalize_side(action: str) -> str:
    a = action or ""
    if any(k in a for k in ("买", "融资开仓", "担保品划入", "买开")):
        return "buy"
    if any(k in a for k in ("卖", "卖券还款", "融券", "买券还券")):
        return "sell"
    return ""


def clean_stock_name(name: str, strip_badge: bool = False) -> str:
    n = re.sub(r"[\s·•:：,，.。;；!！?？`'\"()（）\[\]]+", "", name or "")
    # Only strip buy/sell badge artifacts on THS layouts (NEVER strip real chars like 天/大/美/实/头)
    if strip_badge:
        n = re.sub(r"^(买|卖)+(?=[\u4e00-\u9fff]{2,}|[A-Za-z*])", "", n)
    return n.strip()


def parse_eastmoney_dark(ocr_data: dict[str, Any], meta_info: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for strip in ocr_data["strips"]:
        rows = cluster_rows(strip["words"], y_tol=18.0)
        # Find date anchor rows in col 1 (xf < 0.34)
        anchors: list[tuple[int, str]] = []
        for idx, r in enumerate(rows):
            c1 = join_col(r["ws"], 0.0, 0.35)
            d = parse_date_token(c1)
            if d:
                anchors.append((idx, d))

        for ai, (row_idx, trade_date) in enumerate(anchors):
            y_top = rows[row_idx]["yc"]
            y_next = rows[anchors[ai + 1][0]]["yc"] if ai + 1 < len(anchors) else (y_top + 185.0)
            if y_next - y_top < 45:
                continue
            y_end = min(y_next - 12.0, y_top + 185.0)
            block_words = [w for w in strip["words"] if (y_top - 22.0) <= w["yc"] <= y_end]
            # Split top half vs bottom half around y_top + 38 (source px for 1256w is ~60-85px between top and bot line)
            # Detect exact time line y inside Col 1
            time_candidates = [w for w in block_words if w["xf"] < 0.35 and w["yc"] > y_top + 25.0]
            if not time_candidates:
                continue
            y_bot = sum(w["yc"] for w in time_candidates) / len(time_candidates)
            y_mid = (y_top + y_bot) / 2.0

            top_w = [w for w in block_words if w["yc"] < y_mid]
            bot_w = [w for w in block_words if w["yc"] >= y_mid]

            trade_time = parse_time_token(join_col(bot_w, 0.0, 0.35)) or ""
            name = clean_stock_name(join_col(top_w, 0.34, 0.58))
            code_raw = clean_num_str(join_col(bot_w, 0.34, 0.58))
            code_m = re.search(r"(\d{6})", code_raw)
            code = code_m.group(1) if code_m else ""

            price_raw = join_col(top_w, 0.56, 0.74)
            price = parse_float(price_raw)
            action = re.sub(r"\s+", "", join_col(top_w, 0.74, 1.01))
            side = normalize_side(action)

            qa_raw = join_col(bot_w, 0.55, 1.01)
            qty, amount, verified = split_qty_amount(qa_raw, price)

            if not name and not code:
                continue
            if not side:
                continue
            if qty is None or price is None or amount is None:
                continue
            # Filter out reverse repo / bond subscription
            if code.startswith(("204", "131", "753", "754", "780", "732")) or "配债" in name or "R-001" in name:
                continue

            records.append(
                {
                    "trader": meta_info["trader"],
                    "session": meta_info["session"],
                    "tradeDate": trade_date,
                    "tradeTime": trade_time,
                    "code": code,
                    "name": name,
                    "side": side,
                    "quantity": qty,
                    "price": round(price, 4),
                    "amount": round(amount, 2),
                    "brokerAction": action,
                    "verified": verified,
                    "sourceArticle": meta_info["article_name"],
                    "sourceImage": ocr_data["image"],
                }
            )
    return records


def parse_excel_export(ocr_data: dict[str, Any], meta_info: dict[str, Any]) -> list[dict[str, Any]]:
    has_bal = bool(meta_info.get("has_balance_col", False))
    records: list[dict[str, Any]] = []
    for strip in ocr_data["strips"]:
        rows = cluster_rows(strip["words"], y_tol=7.5)
        for r in rows:
            ws = r["ws"]
            if has_bal:
                c_date = join_col(ws, 0.00, 0.13)
                c_time = join_col(ws, 0.13, 0.25)
                c_name = join_col(ws, 0.25, 0.37)
                c_act = join_col(ws, 0.37, 0.51)
                c_qty = join_col(ws, 0.51, 0.64)
                c_prc = join_col(ws, 0.64, 0.755)
                c_amt = join_col(ws, 0.755, 0.875)
            else:
                c_date = join_col(ws, 0.00, 0.14)
                c_time = join_col(ws, 0.14, 0.28)
                c_name = join_col(ws, 0.28, 0.43)
                c_act = join_col(ws, 0.43, 0.57)
                c_qty = join_col(ws, 0.57, 0.72)
                c_prc = join_col(ws, 0.72, 0.855)
                c_amt = join_col(ws, 0.855, 1.01)

            trade_date = parse_date_token(c_date)
            if not trade_date:
                continue
            action = re.sub(r"\s+", "", c_act)
            side = normalize_side(action)
            if not side:
                continue
            name = clean_stock_name(c_name)
            if not name or any(k in name for k in ("R001", "R-001", "国金金腾", "配号", "配债")):
                continue
            trade_time = parse_time_token(c_time) or ""

            qty_f = parse_float(c_qty)
            price = parse_float(c_prc)
            amount = parse_float(c_amt)
            if qty_f is None or price is None or amount is None or price <= 0 or amount <= 0:
                continue
            qty = abs(int(round(qty_f)))
            if qty <= 0:
                continue

            # Repair missing decimal point in price/amount when OCR dropped `.` (e.g. `1309` vs `13.09`)
            ratio = (qty * price) / max(amount, 1e-6)
            if 95 <= ratio <= 105:
                price = round(price / 100.0, 4)
            elif 9.5 <= ratio <= 10.5:
                price = round(price / 10.0, 4)
            elif 950 <= ratio <= 1050:
                price = round(price / 1000.0, 4)
            elif 0.095 <= ratio <= 0.105:
                amount = round(amount / 10.0, 2)
            elif 0.0095 <= ratio <= 0.0105:
                amount = round(amount / 100.0, 2)

            rel_err = abs(qty * price - amount) / max(amount, 1.0)
            verified = rel_err <= 0.025
            if rel_err > 0.15:
                continue

            records.append(
                {
                    "trader": meta_info["trader"],
                    "session": meta_info["session"],
                    "tradeDate": trade_date,
                    "tradeTime": trade_time,
                    "code": "",
                    "name": name,
                    "side": side,
                    "quantity": qty,
                    "price": round(price, 4),
                    "amount": round(amount, 2),
                    "brokerAction": action,
                    "verified": verified,
                    "sourceArticle": meta_info["article_name"],
                    "sourceImage": ocr_data["image"],
                }
            )
    return records


def parse_ths_fill(ocr_data: dict[str, Any], meta_info: dict[str, Any]) -> list[dict[str, Any]]:
    is_light = ocr_data["layout"] == "ths_fill_light"
    y_tol = 9.0 if is_light else 18.0
    look_up = 52.0 if is_light else 115.0
    look_dn = 20.0 if is_light else 45.0
    records: list[dict[str, Any]] = []

    for strip in ocr_data["strips"]:
        rows = cluster_rows(strip["words"], y_tol=y_tol)
        # Find date-time anchor rows in Col 1 (xf < 0.42)
        anchors: list[tuple[int, str, str]] = []
        for idx, r in enumerate(rows):
            c1 = join_col(r["ws"], 0.0, 0.42)
            d = parse_date_token(c1)
            if d:
                t = parse_time_token(c1) or ""
                anchors.append((idx, d, t))

        for ai, (row_idx, trade_date, trade_time) in enumerate(anchors):
            y_dt = rows[row_idx]["yc"]
            y_prev = rows[anchors[ai - 1][0]]["yc"] if ai > 0 else (y_dt - look_up - 30.0)
            y_lo = max(y_prev + 12.0, y_dt - look_up)
            y_hi = y_dt + look_dn

            block = [w for w in strip["words"] if y_lo <= w["yc"] <= y_hi]
            name_ws = [w for w in block if w["xf"] < 0.36 and w["yc"] < y_dt - (8.0 if is_light else 18.0)]
            name = clean_stock_name("".join(w["t"] for w in sorted(name_ws, key=lambda z: z["x"])), strip_badge=True)
            if not name or any(k in name.upper() for k in ("R001", "R-001", "GC001", "GC002", "GC003", "GC004", "GC007", "逆回购", "国债")):
                continue

            # Price in Col 2, Qty in Col 3, Action + Amount in Col 4
            p_raw = join_col(sorted(block, key=lambda z: z["x"]), 0.34, 0.56)
            q_raw = join_col(sorted(block, key=lambda z: z["x"]), 0.56, 0.76)
            col4 = [w for w in block if w["xf"] >= 0.74]
            col4_top = [w for w in col4 if w["yc"] < y_dt - (4.0 if is_light else 10.0)]
            col4_bot = [w for w in col4 if w["yc"] >= y_dt - (4.0 if is_light else 10.0)]
            act_raw = "".join(w["t"] for w in sorted(col4_top, key=lambda z: z["x"]))
            if not normalize_side(act_raw):
                # Fallback: check Col 1 badge (买/卖)
                c1_all = join_col(rows[row_idx]["ws"], 0.0, 0.12)
                act_raw = act_raw or c1_all
            side = normalize_side(act_raw)
            amt_raw = "".join(w["t"] for w in sorted(col4_bot, key=lambda z: z["x"]))

            price = parse_float(p_raw)
            qty_f = parse_float(q_raw)
            amount = parse_float(amt_raw)
            if not side or price is None or qty_f is None or amount is None or price <= 0 or amount <= 0:
                continue
            qty = abs(int(round(qty_f)))
            if qty <= 0:
                continue

            # Auto-repair dropped decimal point in price/amount
            ratio = (qty * price) / max(amount, 1e-6)
            if 950 <= ratio <= 1050:
                price = round(price / 1000.0, 4)
            elif 95 <= ratio <= 105:
                price = round(price / 100.0, 4)
            elif 9.5 <= ratio <= 10.5:
                price = round(price / 10.0, 4)
            elif 0.00095 <= ratio <= 0.00105:
                amount = round(amount / 1000.0, 2)

            rel_err = abs(qty * price - amount) / max(amount, 1.0)
            if rel_err > 0.15:
                continue

            records.append(
                {
                    "trader": meta_info["trader"],
                    "session": meta_info["session"],
                    "tradeDate": trade_date,
                    "tradeTime": trade_time,
                    "code": "",
                    "name": name,
                    "side": side,
                    "quantity": qty,
                    "price": round(price, 4),
                    "amount": round(amount, 2),
                    "brokerAction": act_raw or ("买入" if side == "buy" else "卖出"),
                    "verified": rel_err <= 0.025,
                    "sourceArticle": meta_info["article_name"],
                    "sourceImage": ocr_data["image"],
                }
            )
    return records


def parse_ths_entrust(ocr_data: dict[str, Any], meta_info: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse Tonghuashun dark-mode 历史委托 (returns executed_trades, all_orders)."""
    trades: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []

    for strip in ocr_data["strips"]:
        rows = cluster_rows(strip["words"], y_tol=18.0)
        anchors: list[tuple[int, str, str]] = []
        for idx, r in enumerate(rows):
            c1 = join_col(r["ws"], 0.0, 0.38)
            d = parse_date_token(c1)
            if d:
                anchors.append((idx, d, parse_time_token(c1) or ""))

        for ai, (row_idx, trade_date, trade_time) in enumerate(anchors):
            y_dt = rows[row_idx]["yc"]
            y_prev = rows[anchors[ai - 1][0]]["yc"] if ai > 0 else (y_dt - 140.0)
            y_lo = max(y_prev + 18.0, y_dt - 115.0)
            y_hi = y_dt + 35.0
            block = [w for w in strip["words"] if y_lo <= w["yc"] <= y_hi]
            y_mid = y_dt - 25.0

            top_w = [w for w in block if w["yc"] < y_mid]
            bot_w = [w for w in block if w["yc"] >= y_mid]

            name = clean_stock_name(join_col(top_w, 0.0, 0.36), strip_badge=True)
            if not name or any(k in name for k in ("R001", "R-001")):
                continue

            ord_price = parse_float(join_col(top_w, 0.36, 0.55))
            fill_price = parse_float(join_col(bot_w, 0.36, 0.55))
            ord_qty_f = parse_float(join_col(top_w, 0.55, 0.74))
            fill_qty_f = parse_float(join_col(bot_w, 0.55, 0.74))
            act_top = re.sub(r"\s+", "", join_col(top_w, 0.74, 1.01))
            st_bot = re.sub(r"\s+", "", join_col(bot_w, 0.74, 1.01))

            side = normalize_side(act_top) or normalize_side(join_col(bot_w, 0.0, 0.10))
            if not side:
                continue

            ord_qty = int(round(abs(ord_qty_f))) if ord_qty_f is not None else 0
            fill_qty = int(round(abs(fill_qty_f))) if fill_qty_f is not None else 0
            is_cancel = ("撤" in act_top) or ("撤" in st_bot) or (fill_qty == 0)

            ord_rec = {
                "trader": meta_info["trader"],
                "session": meta_info["session"],
                "tradeDate": trade_date,
                "tradeTime": trade_time,
                "name": name,
                "side": side,
                "orderPrice": round(ord_price, 4) if ord_price else 0.0,
                "fillPrice": round(fill_price, 4) if fill_price else 0.0,
                "orderQty": ord_qty,
                "fillQty": fill_qty,
                "brokerAction": act_top,
                "orderStatus": st_bot,
                "isCancelled": is_cancel,
                "sourceImage": ocr_data["image"],
            }
            orders.append(ord_rec)

            if (not ("撤单" in act_top)) and fill_qty > 0 and fill_price and fill_price > 0:
                amt = round(fill_price * fill_qty, 2)
                trades.append(
                    {
                        "trader": meta_info["trader"],
                        "session": meta_info["session"],
                        "tradeDate": trade_date,
                        "tradeTime": trade_time,
                        "code": "",
                        "name": name,
                        "side": side,
                        "quantity": fill_qty,
                        "price": round(fill_price, 4),
                        "amount": amt,
                        "brokerAction": f"{act_top}/{st_bot}",
                        "verified": True,
                        "sourceArticle": meta_info["article_name"],
                        "sourceImage": ocr_data["image"],
                    }
                )
    return trades, orders


def dedup_trades(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        key = (
            r["tradeDate"],
            r["tradeTime"],
            r["name"],
            r["side"],
            r["quantity"],
            round(float(r["price"]), 3),
        )
        if key not in seen:
            seen.add(key)
            out.append(r)
    out.sort(key=lambda x: (x["tradeDate"], x["tradeTime"], x["name"]))
    return out


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def run_job(job_key: str, force_ocr: bool = False) -> dict[str, Any]:
    job_list = TRADER_JOBS[job_key]
    all_trades: list[dict[str, Any]] = []
    all_orders: list[dict[str, Any]] = []

    for job in job_list:
        art_dir = find_article_dir(job["article_id"])
        meta = json.loads((art_dir / "meta.json").read_text(encoding="utf-8"))
        lo, hi = job["img_range"]
        meta_info = {**job, "article_name": art_dir.name}

        for im in meta["images"]:
            m_idx = re.match(r"(\d+)", im["file"])
            if not m_idx:
                continue
            num = int(m_idx.group(1))
            if not (lo <= num <= hi):
                continue
            img_path = art_dir / im["file"]
            cache_path = OCR_CACHE_DIR / job_key / f"{img_path.stem}.json"
            ocr_data = run_ocr_for_image(img_path, cache_path, job["layout"], force=force_ocr)
            layout = job["layout"]
            if layout == "eastmoney_dark":
                all_trades.extend(parse_eastmoney_dark(ocr_data, meta_info))
            elif layout == "excel_export":
                all_trades.extend(parse_excel_export(ocr_data, meta_info))
            elif layout in ("ths_fill_dark", "ths_fill_light"):
                all_trades.extend(parse_ths_fill(ocr_data, meta_info))
            elif layout == "ths_entrust_dark":
                t_rows, o_rows = parse_ths_entrust(ocr_data, meta_info)
                if not job.get("orders_only"):
                    all_trades.extend(t_rows)
                all_orders.extend(o_rows)

    deduped = dedup_trades(all_trades)
    verified_cnt = sum(1 for r in deduped if r.get("verified"))
    dates = sorted({r["tradeDate"] for r in deduped if r.get("tradeDate")})

    trade_fields = [
        "trader",
        "session",
        "tradeDate",
        "tradeTime",
        "code",
        "name",
        "side",
        "quantity",
        "price",
        "amount",
        "brokerAction",
        "verified",
        "sourceArticle",
        "sourceImage",
    ]
    out_csv = LEDGER_DIR / f"{job_key}_trades.csv"
    write_csv(out_csv, deduped, trade_fields)

    if all_orders:
        seen_o: set[tuple[Any, ...]] = set()
        dedup_o = []
        for o in all_orders:
            k = (o["tradeDate"], o["tradeTime"], o["name"], o["side"], o["orderQty"], o["orderPrice"], o["brokerAction"])
            if k not in seen_o:
                seen_o.add(k)
                dedup_o.append(o)
        dedup_o.sort(key=lambda x: (x["tradeDate"], x["tradeTime"], x["name"]))
        write_csv(
            LEDGER_DIR / f"{job_key}_all_orders.csv",
            dedup_o,
            ["trader", "session", "tradeDate", "tradeTime", "name", "side", "orderPrice", "fillPrice", "orderQty", "fillQty", "brokerAction", "orderStatus", "isCancelled", "sourceImage"],
        )

    return {
        "job": job_key,
        "trades": len(deduped),
        "verified": verified_cnt,
        "verify_pct": round(100.0 * verified_cnt / max(1, len(deduped)), 1),
        "unique_stocks": len({r["name"] for r in deduped if r["name"]}),
        "date_range": f"{dates[0]}..{dates[-1]}" if dates else "",
        "csv": str(out_csv),
        "orders": len(all_orders),
    }


def main() -> int:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Extract verified CSV trade ledgers from 顽主杯 slip screenshots.")
    p.add_argument("--jobs", nargs="+", default=list(TRADER_JOBS.keys()), choices=list(TRADER_JOBS.keys()))
    p.add_argument("--force-ocr", action="store_true")
    args = p.parse_args()

    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for jk in args.jobs:
        st = run_job(jk, force_ocr=args.force_ocr)
        results.append(st)
        print(
            f"[DONE] {jk:16s} | trades={st['trades']:4d} | verified={st['verified']:4d} ({st['verify_pct']:5.1f}%) | "
            f"stocks={st['unique_stocks']:3d} | range={st['date_range']} | orders={st['orders']}",
            flush=True,
        )
    (LEDGER_DIR / "extraction_summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
