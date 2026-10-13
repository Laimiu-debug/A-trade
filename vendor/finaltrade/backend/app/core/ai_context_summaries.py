"""Summarize store objects into compact AI runtime snapshots."""

from __future__ import annotations

from typing import Any

from ..models import BacktestReportDetail, BacktestResponse, ScreenerResult, ScreenerRunDetail


def _row_brief(row: ScreenerResult) -> dict[str, Any]:
    return {
        "symbol": row.symbol,
        "name": row.name,
        "score": row.score,
        "trend_class": row.trend_class,
        "stage": row.stage,
        "ai_confidence": row.ai_confidence,
        "theme_stage": row.theme_stage,
        "ret40": row.ret40,
        "retrace20": row.retrace20,
        "reject_reasons": list(row.reject_reasons or []),
    }


def summarize_screener_run(detail: ScreenerRunDetail, *, focus_symbol: str | None = None) -> dict[str, Any]:
    summary = detail.step_summary
    payload: dict[str, Any] = {
        "run_id": detail.run_id,
        "as_of_date": detail.as_of_date,
        "degraded": detail.degraded,
        "degraded_reason": detail.degraded_reason,
        "step_counts": {
            "input": summary.input_count,
            "step1": summary.step1_count,
            "step2": summary.step2_count,
            "step3": summary.step3_count,
            "step4": summary.step4_count,
        },
        "step4_top": [_row_brief(row) for row in detail.step_pools.step4[:12]],
    }
    if focus_symbol:
        payload["focus"] = find_screener_focus(detail, focus_symbol)
    return payload


def find_screener_focus(detail: ScreenerRunDetail, symbol: str) -> dict[str, Any]:
    normalized = symbol.strip().lower()
    pools = {
        "step4": detail.step_pools.step4,
        "step3": detail.step_pools.step3,
        "step2": detail.step_pools.step2,
        "step1": detail.step_pools.step1,
        "input": detail.step_pools.input,
    }
    for step_name, rows in pools.items():
        for row in rows:
            if row.symbol == normalized:
                return {"found_in": step_name, "row": _row_brief(row)}
    return {"found_in": None, "reject_hint": "未出现在本次 run 的各层池中"}


def summarize_backtest_response(result: BacktestResponse) -> dict[str, Any]:
    stats = result.stats
    worst = sorted(result.trades, key=lambda item: item.pnl_ratio)[:5]
    return {
        "strategy_id": result.strategy_id,
        "strategy_version": result.strategy_version,
        "strategy_params": dict(result.strategy_params or {}),
        "date_from": result.range.date_from,
        "date_to": result.range.date_to,
        "metrics": {
            "total_return": stats.total_return,
            "max_drawdown": stats.max_drawdown,
            "win_rate": stats.win_rate,
            "trade_count": stats.trade_count,
            "profit_factor": stats.profit_factor,
        },
        "worst_trades": [
            {
                "symbol": item.symbol,
                "buy_date": item.buy_date,
                "sell_date": item.sell_date,
                "pnl_ratio": item.pnl_ratio,
            }
            for item in worst
        ],
        "notes": list(result.notes or [])[:5],
        "execution_path": result.execution_path,
    }


def summarize_backtest_report(detail: BacktestReportDetail) -> dict[str, Any]:
    summary = summarize_backtest_response(detail.run_result)
    summary["report_id"] = detail.summary.report_id
    summary["report_title"] = detail.summary.title
    summary["run_request"] = detail.run_request.model_dump()
    if detail.plateau_result is not None:
        summary["plateau_point_count"] = len(detail.plateau_result.points or [])
    return summary


def summarize_strategy_descriptor(descriptor: Any, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "strategy_id": descriptor.strategy_id,
        "name": descriptor.name,
        "version": descriptor.version,
        "description": getattr(descriptor, "description", "") or "",
        "playbook": getattr(descriptor, "playbook", {}) or {},
        "current_params": dict(params or getattr(descriptor, "default_params", {}) or {}),
        "params_schema_keys": list(getattr(descriptor, "params_schema", {}).keys()),
    }


def summarize_signals_payload(payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("items")
    if not isinstance(items, list):
        items = []
    top = []
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        top.append(
            {
                "symbol": item.get("symbol"),
                "name": item.get("name"),
                "trigger_date": item.get("trigger_date"),
                "primary_event": item.get("primary_event"),
                "score": item.get("score"),
            }
        )
    return {
        "mode": payload.get("mode"),
        "as_of_date": payload.get("as_of_date"),
        "strategy_id": payload.get("strategy_id"),
        "source_count": payload.get("source_count"),
        "signal_count": len(items),
        "top_signals": top,
    }
