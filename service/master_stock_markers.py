"""Aggregate buy/sell markers for a single stock across all local research & master sources.

Reads read-only from:
  1. `masters.sqlite3`  -> real master-trader delivery slips (`masterSlips`)
  2. `research.sqlite3` -> completed `masterBacktest` tasks (`masterBacktests` + `nextDayOrders`)
                        -> completed local `ResearchService` backtests (`researchTrades`)
"""
from __future__ import annotations

import json
import sqlite3
import zlib
from pathlib import Path
from typing import Any

from service import tdx
from service.master_advisor import STYLE_PROFILES
from service.master_rules import ACTION_LABELS


def _normalize_symbol(raw: Any) -> str:
    market, code = tdx.symbol_parts(raw)
    return f"{market}{code}"


def _unpack_blob(blob: Any) -> Any:
    if not blob:
        return None
    try:
        return json.loads(zlib.decompress(blob))
    except Exception:
        return None


def _collect_master_slips(directory: Path, symbol: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    db_path = directory / "masters.sqlite3"
    if not db_path.is_file():
        return [], []
    try:
        db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=8)
        db.row_factory = sqlite3.Row
    except sqlite3.Error:
        return [], []
    try:
        rows = db.execute(
            """
            SELECT t.id, t.master_id, m.name AS master_name, m.style_tag,
                   t.trade_date, t.trade_time, t.side, t.symbol, t.code, t.name,
                   t.quantity, t.price, t.amount, t.fees, t.note
            FROM master_trades t
            JOIN masters m ON m.id = t.master_id
            WHERE LOWER(t.symbol) = ?
            ORDER BY t.trade_date ASC, t.trade_time ASC, t.created_at ASC
            """,
            (symbol,),
        ).fetchall()
        slips: list[dict[str, Any]] = []
        masters_seen: dict[str, dict[str, Any]] = {}
        running_qty: dict[str, float] = {}
        for r in rows:
            mid = r["master_id"]
            mname = r["master_name"] or mid
            if mid not in masters_seen:
                masters_seen[mid] = {"id": mid, "name": mname, "styleTag": r["style_tag"] or "", "count": 0}
            masters_seen[mid]["count"] += 1

            side = str(r["side"] or "").lower()
            qty = float(r["quantity"] or 0)
            prev_q = running_qty.get(mid, 0.0)
            if side == "buy":
                leg_type = "slip_add" if prev_q > 0 else "slip_buy"
                leg_label = "实盘加仓" if prev_q > 0 else "实盘买入"
                running_qty[mid] = prev_q + qty
            else:
                leg_type = "slip_sell"
                leg_label = "实盘清仓" if qty >= prev_q - 1e-6 and prev_q > 0 else "实盘卖出"
                running_qty[mid] = max(0.0, prev_q - qty)

            slips.append({
                "id": r["id"],
                "masterId": mid,
                "masterName": mname,
                "styleTag": r["style_tag"] or "",
                "date": r["trade_date"],
                "time": r["trade_time"] or "",
                "side": side,
                "legType": leg_type,
                "legLabel": leg_label,
                "symbol": symbol,
                "code": r["code"] or symbol[2:],
                "name": r["name"] or symbol[2:],
                "quantity": round(qty, 2),
                "price": round(float(r["price"] or 0), 3),
                "amount": round(float(r["amount"] or 0), 2),
                "note": r["note"] or "",
            })
        slips.reverse()
        return slips, list(masters_seen.values())
    except sqlite3.Error:
        return [], []
    finally:
        db.close()


def _extract_research_trades_from_result(result: Any, symbol: str) -> list[dict[str, Any]]:
    """Extract normalized trade rows matching `symbol` from a ResearchService job result."""
    found: list[dict[str, Any]] = []
    if not isinstance(result, dict):
        return found

    def scan_list(items: Any, source_label: str = "") -> None:
        if not isinstance(items, list):
            return
        for row in items:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or row.get("code") or "").strip().lower()
            if sym == symbol or (len(sym) == 6 and sym == symbol[2:]):
                # Handle paired trade format (entryDate + exitDate) OR execution-leg format (date + action/side)
                if row.get("entryDate") or row.get("entry_date"):
                    ed = str(row.get("entryDate") or row.get("entry_date") or "")
                    xd = str(row.get("exitDate") or row.get("exit_date") or "")
                    ep = float(row.get("entryPrice") or row.get("entry_price") or 0)
                    xp = float(row.get("exitPrice") or row.get("exit_price") or 0)
                    qty = float(row.get("quantity") or row.get("shares") or 0)
                    pnl_ratio = row.get("return") if row.get("return") is not None else row.get("pnl_ratio")
                    if ed:
                        found.append({
                            "date": ed,
                            "side": "buy",
                            "actionLabel": "组合买入",
                            "quantity": qty,
                            "price": round(ep, 3),
                            "pnl": None,
                            "returnPct": None,
                            "reason": str(row.get("entryReason") or row.get("reason") or source_label or "研究买入"),
                        })
                    if xd and str(row.get("exitReason") or row.get("exit_reason") or "") != "open":
                        found.append({
                            "date": xd,
                            "side": "sell",
                            "actionLabel": "组合卖出",
                            "quantity": qty,
                            "price": round(xp, 3),
                            "pnl": row.get("pnl"),
                            "returnPct": round(float(pnl_ratio) * 100.0, 2) if isinstance(pnl_ratio, (int, float)) else None,
                            "reason": str(row.get("exitReason") or row.get("exit_reason") or source_label or "研究卖出"),
                        })
                elif row.get("date") and (row.get("side") or row.get("action")):
                    raw_side = str(row.get("side") or row.get("action") or "").lower()
                    side = "buy" if any(k in raw_side for k in ("buy", "买", "建", "加")) else "sell"
                    found.append({
                        "date": str(row["date"]),
                        "side": side,
                        "actionLabel": str(row.get("actionLabel") or ("组合买入" if side == "buy" else "组合卖出")),
                        "quantity": float(row.get("quantity") or row.get("shares") or 0),
                        "price": round(float(row.get("price") or row.get("execPrice") or 0), 3),
                        "pnl": row.get("pnl"),
                        "returnPct": row.get("returnPct"),
                        "reason": str(row.get("reason") or source_label or "研究执行"),
                    })

    scan_list(result.get("trades"))
    scan_list(result.get("actionLedger"))
    if isinstance(result.get("backtest"), dict):
        scan_list(result["backtest"].get("trades"))
        scan_list(result["backtest"].get("actionLedger"))
    if isinstance(result.get("portfolio"), dict):
        scan_list(result["portfolio"].get("trades"))
        scan_list(result["portfolio"].get("actionLedger"))
    return found


def _collect_research_jobs(directory: Path, symbol: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    db_path = directory / "research.sqlite3"
    if not db_path.is_file():
        return [], [], [], []
    try:
        db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=8)
        db.row_factory = sqlite3.Row
    except sqlite3.Error:
        return [], [], [], []

    master_bt_trades: list[dict[str, Any]] = []
    master_bt_runs: list[dict[str, Any]] = []
    master_next_orders: list[dict[str, Any]] = []
    research_trades: list[dict[str, Any]] = []

    try:
        rows = db.execute("SELECT id, meta, result FROM jobs WHERE result IS NOT NULL").fetchall()
        # Sort newest first in Python using meta.createdAt
        parsed = []
        for r in rows:
            try:
                meta = json.loads(r["meta"])
            except Exception:
                continue
            if meta.get("status") != "completed":
                continue
            parsed.append((str(meta.get("createdAt") or ""), r["id"], meta, r["result"]))
        parsed.sort(key=lambda x: x[0], reverse=True)

        master_task_count = 0
        other_task_count = 0
        for _created, task_id, meta, blob in parsed:
            kind = meta.get("kind")
            if kind == "masterBacktest":
                if master_task_count >= 25:
                    continue
                result = _unpack_blob(blob)
                if not isinstance(result, dict):
                    continue
                master_task_count += 1
                style_key = str(result.get("styleKey") or meta.get("config", {}).get("styleKey") or "")
                style_name = str(result.get("styleName") or STYLE_PROFILES.get(style_key, {}).get("name") or style_key)
                run_name = str(meta.get("name") or style_name)
                dr = result.get("dateRange") or {}
                matched_count = 0
                for t in result.get("trades") or []:
                    if str(t.get("symbol") or "").lower() != symbol:
                        continue
                    act_type = str(t.get("actionType") or ("open_buy" if t.get("side") == "buy" else "sell_profit"))
                    master_bt_trades.append({
                        "taskId": task_id,
                        "taskName": run_name,
                        "styleKey": style_key,
                        "styleName": style_name,
                        "date": t.get("date"),
                        "side": t.get("side"),
                        "actionType": act_type,
                        "actionLabel": t.get("actionLabel") or ACTION_LABELS.get(act_type, "买入" if t.get("side") == "buy" else "卖出"),
                        "symbol": symbol,
                        "code": t.get("code") or symbol[2:],
                        "name": t.get("name") or symbol[2:],
                        "quantity": t.get("quantity") or 0,
                        "price": t.get("price") or 0,
                        "amount": t.get("amount") or 0,
                        "pnl": t.get("pnl"),
                        "returnPct": t.get("returnPct"),
                        "holdDays": t.get("holdDays"),
                        "beforeStockWeightPct": t.get("beforeStockWeightPct"),
                        "afterStockWeightPct": t.get("afterStockWeightPct"),
                        "afterTotalPosPct": t.get("afterTotalPosPct"),
                        "reason": t.get("reason") or "",
                        "stage": t.get("stage") or "",
                    })
                    matched_count += 1

                # Also collect end-of-run next-day predicted orders for this symbol
                nxt = result.get("nextDayOrders") or {}
                sig_date = nxt.get("signalDate") or dr.get("endDate") or ""
                for o in nxt.get("orders") or []:
                    if str(o.get("symbol") or "").lower() == symbol:
                        master_next_orders.append({
                            "taskId": task_id,
                            "taskName": run_name,
                            "styleKey": style_key,
                            "styleName": style_name,
                            "signalDate": sig_date,
                            **o,
                        })

                if matched_count > 0:
                    master_bt_runs.append({
                        "taskId": task_id,
                        "taskName": run_name,
                        "styleKey": style_key,
                        "styleName": style_name,
                        "startDate": dr.get("startDate") or meta.get("config", {}).get("startDate") or "",
                        "endDate": dr.get("endDate") or meta.get("config", {}).get("endDate") or "",
                        "dataSource": result.get("dataSource") or "auto",
                        "tradeCount": matched_count,
                        "totalReturnPct": result.get("summary", {}).get("totalReturnPct"),
                    })
            elif kind in ("singleBacktest", "portfolio", "comparison", "walkForward", "frozenValidation"):
                if other_task_count >= 25:
                    continue
                # Quick check: skip jobs whose symbol list clearly doesn't include symbol (when explicit)
                syms = meta.get("symbols")
                if isinstance(syms, list) and syms and symbol not in syms:
                    continue
                result = _unpack_blob(blob)
                if not isinstance(result, dict):
                    continue
                other_task_count += 1
                extracted = _extract_research_trades_from_result(result, symbol)
                run_name = str(meta.get("name") or kind)
                for row in extracted:
                    research_trades.append({
                        "taskId": task_id,
                        "taskName": run_name,
                        "kind": kind,
                        "symbol": symbol,
                        **row,
                    })

        master_bt_trades.sort(key=lambda x: (str(x.get("date") or ""), str(x.get("taskId") or "")), reverse=True)
        research_trades.sort(key=lambda x: (str(x.get("date") or ""), str(x.get("taskId") or "")), reverse=True)
        return master_bt_trades, master_bt_runs, master_next_orders, research_trades
    except sqlite3.Error:
        return [], [], [], []
    finally:
        db.close()


def collect_stock_markers(directory: Path, raw_symbol: Any) -> dict[str, Any]:
    """Collect all local master slips, master backtest trades, and research trades for one symbol."""
    symbol = _normalize_symbol(raw_symbol)
    slips, masters = _collect_master_slips(directory, symbol)
    master_bt_trades, master_bt_runs, master_next_orders, research_trades = _collect_research_jobs(directory, symbol)
    return {
        "symbol": symbol,
        "masterSlips": slips,
        "masters": masters,
        "masterBacktestTrades": master_bt_trades,
        "masterBacktestRuns": master_bt_runs,
        "masterNextDayOrders": master_next_orders,
        "researchTrades": research_trades,
        "availableStyles": [
            {"key": k, "name": v["name"], "shortName": v["name"].split("·")[0].strip()}
            for k, v in STYLE_PROFILES.items()
        ],
    }
