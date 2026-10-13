"""Build runtime snapshots for AI chat from store state."""

from __future__ import annotations

from typing import Any, Callable

from ..models import AIChatContext, BacktestReportDetail, BacktestResponse, ScreenerResult, ScreenerRunDetail
from .ai_context_summaries import (
    find_screener_focus,
    summarize_backtest_report,
    summarize_backtest_response,
    summarize_screener_run,
    summarize_signals_payload,
    summarize_strategy_descriptor,
)
from .sentiment_valuation import summarize_valuation_payload


class AIContextBuilder:
    def __init__(
        self,
        *,
        resolve_symbol_name: Callable[[str, ScreenerResult | None], str],
        get_screener_row: Callable[[str], ScreenerResult | None],
        build_row_from_candles: Callable[[str], ScreenerResult | None],
        build_ai_context_text: Callable[[str, ScreenerResult | None], str],
        compose_prompt_context: Callable[[str, ScreenerResult | None, list[str]], dict[str, object]],
        enabled_source_urls: Callable[[], list[str]],
        get_latest_ai_record: Callable[[str], dict[str, Any] | None],
        get_screener_run: Callable[[str], ScreenerRunDetail | None],
        get_backtest_report: Callable[[str], BacktestReportDetail | None],
        get_strategy_descriptor: Callable[[str], Any | None],
        get_sentiment_valuation_quote: Callable[[str], Any | None] | None = None,
    ) -> None:
        self._resolve_symbol_name = resolve_symbol_name
        self._get_screener_row = get_screener_row
        self._build_row_from_candles = build_row_from_candles
        self._build_ai_context_text = build_ai_context_text
        self._compose_prompt_context = compose_prompt_context
        self._enabled_source_urls = enabled_source_urls
        self._get_latest_ai_record = get_latest_ai_record
        self._get_screener_run = get_screener_run
        self._get_backtest_report = get_backtest_report
        self._get_strategy_descriptor = get_strategy_descriptor
        self._get_sentiment_valuation_quote = get_sentiment_valuation_quote

    def enrich(self, context: AIChatContext) -> AIChatContext:
        page = context.page
        if page == "chart" or (page == "generic" and context.symbol):
            return self._enrich_chart(context)
        if page == "screener":
            return self._enrich_screener(context)
        if page == "backtest":
            return self._enrich_backtest(context)
        if page == "strategy":
            return self._enrich_strategy(context)
        if page == "signals":
            return self._enrich_signals(context)
        if page == "review":
            return self._enrich_review(context)
        if page == "sentiment_valuation":
            return self._enrich_sentiment_valuation(context)
        if page in {"trade", "portfolio", "market_trend", "sector_capital", "abnormal_movement", "cross_validate"}:
            return self._merge_payload(context, {"page_hint": page})
        return context

    def _merge_payload(self, context: AIChatContext, extra: dict[str, Any]) -> AIChatContext:
        payload = dict(context.payload)
        payload.update(extra)
        return context.model_copy(update={"payload": payload})

    def _resolve_row(self, symbol: str) -> ScreenerResult | None:
        row = self._get_screener_row(symbol)
        if row is not None:
            return row
        return self._build_row_from_candles(symbol)

    def _enrich_chart(self, context: AIChatContext) -> AIChatContext:
        symbol = (context.symbol or "").strip().lower()
        if not symbol:
            return context
        row = self._resolve_row(symbol)
        source_urls = self._enabled_source_urls()
        prompt_ctx = self._compose_prompt_context(symbol, row, source_urls)
        baseline = prompt_ctx.get("baseline")
        snapshot: dict[str, Any] = dict(context.payload)
        snapshot.update(
            {
                "symbol": symbol,
                "name": self._resolve_symbol_name(symbol, row),
                "kline_context": self._build_ai_context_text(symbol, row),
                "inferred_sector": str(prompt_ctx.get("inferred_sector", "")),
                "web_evidence_count": len(prompt_ctx.get("web_evidence", []))
                if isinstance(prompt_ctx.get("web_evidence"), list)
                else 0,
            }
        )
        if row is not None:
            snapshot["screener_row"] = {
                "trend_class": row.trend_class,
                "stage": row.stage,
                "score": row.score,
                "ret40": row.ret40,
                "retrace20": row.retrace20,
                "turnover20": row.turnover20,
                "ai_confidence": row.ai_confidence,
                "theme_stage": row.theme_stage,
                "up_down_volume_ratio": row.up_down_volume_ratio,
            }
        if baseline is not None and hasattr(baseline, "model_dump"):
            snapshot["heuristic_baseline"] = baseline.model_dump()
        latest_ai = self._get_latest_ai_record(symbol)
        if latest_ai:
            snapshot["latest_ai_record"] = latest_ai
        return context.model_copy(update={"payload": snapshot, "symbol": symbol})

    def _enrich_screener(self, context: AIChatContext) -> AIChatContext:
        run_id = (context.refs.get("run_id") or "").strip()
        if not run_id:
            return context
        detail = self._get_screener_run(run_id)
        if detail is None:
            return self._merge_payload(context, {"run_missing": True, "run_id": run_id})
        focus = (context.symbol or context.payload.get("focus_symbol") or "")
        focus_text = str(focus).strip().lower() if focus else None
        return self._merge_payload(context, summarize_screener_run(detail, focus_symbol=focus_text))

    def _enrich_backtest(self, context: AIChatContext) -> AIChatContext:
        report_id = (context.refs.get("report_id") or "").strip()
        if report_id:
            detail = self._get_backtest_report(report_id)
            if detail is not None:
                return self._merge_payload(context, summarize_backtest_report(detail))
            return self._merge_payload(context, {"report_missing": True, "report_id": report_id})
        inline = context.payload.get("run_result")
        if isinstance(inline, dict):
            try:
                result = BacktestResponse.model_validate(inline)
                return self._merge_payload(context, summarize_backtest_response(result))
            except Exception:
                pass
        return context

    def _enrich_strategy(self, context: AIChatContext) -> AIChatContext:
        strategy_id = (context.refs.get("strategy_id") or str(context.payload.get("strategy_id") or "")).strip()
        if not strategy_id:
            compare_ids = context.payload.get("strategy_ids")
            if isinstance(compare_ids, list) and compare_ids:
                summaries = []
                for raw_id in compare_ids[:6]:
                    descriptor = self._get_strategy_descriptor(str(raw_id))
                    if descriptor is not None:
                        summaries.append(summarize_strategy_descriptor(descriptor))
                return self._merge_payload(context, {"compare_strategies": summaries})
            return context
        descriptor = self._get_strategy_descriptor(strategy_id)
        if descriptor is None:
            return self._merge_payload(context, {"strategy_missing": True, "strategy_id": strategy_id})
        params = context.payload.get("strategy_params")
        params_dict = dict(params) if isinstance(params, dict) else None
        return self._merge_payload(context, summarize_strategy_descriptor(descriptor, params_dict))

    def _enrich_signals(self, context: AIChatContext) -> AIChatContext:
        return self._merge_payload(context, summarize_signals_payload(dict(context.payload)))

    def _enrich_review(self, context: AIChatContext) -> AIChatContext:
        payload = dict(context.payload)
        payload.setdefault("review_date", context.refs.get("date") or context.as_of_date)
        return context.model_copy(update={"payload": payload})

    def _enrich_sentiment_valuation(self, context: AIChatContext) -> AIChatContext:
        payload = dict(context.payload)
        symbol = (context.symbol or str(payload.get("symbol") or "")).strip().lower()
        if symbol:
            payload["symbol"] = symbol
            payload.setdefault("name", payload.get("name") or "")
            if self._get_sentiment_valuation_quote is not None:
                try:
                    quote = self._get_sentiment_valuation_quote(symbol)
                    if quote is not None:
                        if hasattr(quote, "model_dump"):
                            payload["market_quote"] = quote.model_dump(exclude_none=True)
                        elif isinstance(quote, dict):
                            payload["market_quote"] = quote
                except Exception:
                    payload["market_quote_error"] = True
        payload["valuation_summary"] = summarize_valuation_payload(payload)
        return context.model_copy(update={"payload": payload, "symbol": symbol or context.symbol})
