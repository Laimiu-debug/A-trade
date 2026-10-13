from __future__ import annotations

import json

from typing import Literal

from fastapi import FastAPI, File, Path, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .models import (
    AIAnalysisRecord,
    AIChatRequest,
    AIChatResponse,
    AIChatSessionResponse,
    AILocalPlaybookPayload,
    AILocalPlaybookResponse,
    AIParameterProposalApplyRequest,
    AIParameterProposalApplyResponse,
    AIParameterProposalRequest,
    AIParameterProposalResponse,
    AIPlaybookResponse,
    AIProviderTestRequest,
    AIProviderTestResponse,
    AIQuickPromptsPayload,
    AIQuickPromptsResponse,
    AIRecordsResponse,
    AISessionsListResponse,
    AIUsageResponse,
    BacktestResponse,
    BacktestRunRequest,
    BacktestPlateauRunRequest,
    BacktestPlateauResponse,
    BacktestPlateauPointDetailResponse,
    BacktestPlateauTaskListResponse,
    BacktestPlateauTaskDeleteResponse,
    BacktestPlateauTaskStatusResponse,
    BacktestPoolRollMode,
    CrossValidateRequest,
    CrossValidateResponse,
    CrossValidateTaskStartResponse,
    CrossValidateTaskStatusResponse,
    CrossValidateHistoryRecord,
    CrossValidateHistoryDetail,
    CrossValidateBacktestRequest,
    CrossValidateBacktestResponse,
    BacktestTaskListResponse,
    BacktestTaskDeleteResponse,
    BacktestTaskStartResponse,
    BacktestTaskStatusResponse,
    BacktestReportBuildRequest,
    BacktestReportBuildResponse,
    BacktestReportDeleteResponse,
    BacktestReportDetail,
    BacktestReportImportResponse,
    BacktestReportListResponse,
    BacktestStrategySignalsResponse,
    BoardFilter,
    Market,
    AnnotationUpdateResponse,
    ApiErrorPayload,
    AppConfig,
    CreateOrderRequest,
    CreateOrderResponse,
    DeleteAIRecordResponse,
    IntradayPayload,
    MarketDataSyncRequest,
    MarketDataSyncResponse,
    MarketNewsResponse,
    PortfolioSnapshot,
    DailyReviewListResponse,
    DailyReviewPayload,
    DailyReviewRecord,
    ReviewResponse,
    ReviewTag,
    ReviewTagCreateRequest,
    ReviewTagStatsResponse,
    ReviewTagsPayload,
    ReviewTagType,
    SimFillsResponse,
    SimOrdersResponse,
    SimResetResponse,
    SimSettleResponse,
    SimTradingConfig,
    SystemStorageStatus,
    SignalScanMode,
    SignalEtfBacktestCreateRequest,
    SignalEtfBacktestAutoCreateRequest,
    SignalEtfBacktestAutoCreateResponse,
    SignalEtfBacktestDeleteResponse,
    SignalEtfBacktestDetail,
    SignalEtfBacktestListResponse,
    SignalEtfBacktestRecord,
    SignalEtfBacktestUpdateRequest,
    StrategyCatalogResponse,
    StrategyDescriptor,
    EventJudgmentCatalogResponse,
    EventJudgmentProfile,
    EventJudgmentProfileApplyRequest,
    EventJudgmentProfileDeleteResponse,
    EventJudgmentProfileUpsertRequest,
    StrategyUpdateRequest,
    TrendPoolStep,
    ScreenerParams,
    ScreenerRunDetail,
    ScreenerRunResponse,
    SignalsResponse,
    B1Params,
    B1ScreenerRequest,
    B1ScreenerResponse,
    MarketTrendLeadersRequest,
    MarketTrendLeadersResponse,
    LimitUpLadderRequest,
    LimitUpLadderResponse,
    AbnormalMovementRequest,
    AbnormalMovementResponse,
    SectorCapitalFlowRequest,
    SectorCapitalFlowResponse,
    SentimentValuationQuoteResponse,
    WyckoffEventStoreBackfillRequest,
    WyckoffEventStoreBackfillResponse,
    WyckoffEventStoreStatsResponse,
    StockAnalysisResponse,
    StockAnnotation,
    TradeFillTagAssignment,
    TradeFillTagUpdateRequest,
    WeeklyReviewListResponse,
    WeeklyReviewPayload,
    WeeklyReviewRecord,
)
from .sim_engine import SimEngineError
from .store import BacktestValidationError, MarketDataUnavailable, store
from .utils.persistence import StatePersistenceError
from .security import api_access_error

app = FastAPI(title="Final trade API", version="4.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    degraded: bool | None = None,
    degraded_reason: str | None = None,
) -> JSONResponse:
    payload = ApiErrorPayload(
        code=code,
        message=message,
        degraded=degraded,
        degraded_reason=degraded_reason,
        trace_id=str(__import__("time").time_ns()),
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(exclude_none=True))


@app.middleware('http')
async def protect_api(request: Request, call_next):
    error = api_access_error(request)
    if error:
        return error_response(403, 'API_ACCESS_DENIED', error)
    return await call_next(request)


@app.exception_handler(StatePersistenceError)
def handle_persistence_error(_: Request, exc: StatePersistenceError) -> JSONResponse:
    return error_response(500, 'STATE_SAVE_FAILED', str(exc))


@app.exception_handler(MarketDataUnavailable)
def handle_market_data_error(_: Request, exc: MarketDataUnavailable) -> JSONResponse:
    return error_response(404, 'MARKET_DATA_UNAVAILABLE', str(exc))


@app.exception_handler(RequestValidationError)
def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    detail = exc.errors()[0].get("msg") if exc.errors() else "请求参数不合法"
    return error_response(422, "VALIDATION_ERROR", str(detail))


@app.exception_handler(SimEngineError)
def handle_sim_error(_: Request, exc: SimEngineError) -> JSONResponse:
    status_code = 404 if exc.code == "SIM_ORDER_NOT_FOUND" else 400
    return error_response(status_code, exc.code, exc.message)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/screener/run", response_model=ScreenerRunResponse)
def run_screener(params: ScreenerParams) -> ScreenerRunResponse:
    detail = store.create_screener_run(params)
    return ScreenerRunResponse(run_id=detail.run_id)


@app.get("/api/screener/runs/{run_id}", response_model=ScreenerRunDetail)
def get_screener_run(run_id: str = Path(min_length=6)) -> ScreenerRunDetail | JSONResponse:
    run = store.get_screener_run(run_id)
    if run is None:
        return error_response(404, "RUN_NOT_FOUND", "筛选任务不存在")
    return run


@app.get("/api/screener/latest-run", response_model=ScreenerRunDetail)
def get_latest_screener_run() -> ScreenerRunDetail | JSONResponse:
    run = store.get_latest_screener_run()
    if run is None:
        return error_response(404, "RUN_NOT_FOUND", "暂无可用筛选任务，请先在选股池执行筛选")
    return run


@app.post("/api/screener/b1-run", response_model=B1ScreenerResponse)
def run_b1_screener(req: B1ScreenerRequest) -> B1ScreenerResponse:
    from dataclasses import asdict

    from .core.b1_strategy import B1Params as DCB1Params

    bp = DCB1Params(**req.b1_params.model_dump())
    result = store.create_b1_screener_run(
        markets=req.markets,
        as_of_date=req.as_of_date,
        b1_params=bp,
    )
    return B1ScreenerResponse(
        total_scanned=result["total_scanned"],
        hit_count=result["hit_count"],
        hits=result["hits"],
        b1_params=asdict(bp),
        elapsed_sec=result["elapsed_sec"],
    )


@app.post("/api/market/trend-leaders", response_model=MarketTrendLeadersResponse)
def run_market_trend_leaders(req: MarketTrendLeadersRequest) -> MarketTrendLeadersResponse:
    result = store.scan_market_trend_leaders(request=req)
    return MarketTrendLeadersResponse.model_validate(result)


@app.post("/api/market/limit-up-ladder", response_model=LimitUpLadderResponse)
def run_limit_up_ladder(req: LimitUpLadderRequest) -> LimitUpLadderResponse:
    result = store.scan_limit_up_ladder(request=req)
    return LimitUpLadderResponse.model_validate(result)


@app.post("/api/market/abnormal-movement", response_model=AbnormalMovementResponse)
def run_abnormal_movement(req: AbnormalMovementRequest) -> AbnormalMovementResponse:
    result = store.scan_abnormal_movement(request=req)
    return AbnormalMovementResponse.model_validate(result)


@app.post("/api/market/sector-capital-flow", response_model=SectorCapitalFlowResponse)
def run_sector_capital_flow(req: SectorCapitalFlowRequest) -> SectorCapitalFlowResponse:
    result = store.scan_sector_capital_flow(request=req)
    return SectorCapitalFlowResponse.model_validate(result)


@app.get("/api/market/sentiment-valuation/quote", response_model=SentimentValuationQuoteResponse)
def get_sentiment_valuation_quote(
    symbol: str = Query(min_length=4, max_length=16, description="股票代码，如 sh600519"),
) -> SentimentValuationQuoteResponse:
    return store.get_sentiment_valuation_quote(symbol)


@app.get("/api/stocks/{symbol}/candles")
def get_stock_candles(symbol: str) -> dict[str, object]:
    return store.get_candles_payload(symbol)


@app.get("/api/stocks/{symbol}/intraday", response_model=IntradayPayload)
def get_stock_intraday(
    symbol: str,
    date: str = Query(default="", description="YYYY-MM-DD"),
) -> IntradayPayload:
    return store.get_intraday_payload(symbol, date)


@app.get("/api/stocks/{symbol}/analysis", response_model=StockAnalysisResponse)
def get_stock_analysis(symbol: str) -> StockAnalysisResponse:
    return store.get_analysis(symbol)


@app.put("/api/stocks/{symbol}/annotations", response_model=AnnotationUpdateResponse)
def put_stock_annotation(symbol: str, payload: StockAnnotation) -> AnnotationUpdateResponse | JSONResponse:
    if payload.symbol != symbol:
        return error_response(400, "SYMBOL_MISMATCH", "symbol 与 URL 不一致")
    saved = store.save_annotation(payload)
    return AnnotationUpdateResponse(success=True, annotation=saved)


@app.get("/api/signals", response_model=SignalsResponse)
def get_signals(
    mode: SignalScanMode = Query(default="trend_pool"),
    run_id: str = Query(default="", min_length=0, max_length=64),
    trend_step: TrendPoolStep = Query(default="auto"),
    strategy_id: str = Query(default="wyckoff_trend_v1", min_length=1, max_length=64),
    strategy_params_json: str = Query(default="", alias="strategy_params"),
    market_filters: list[Market] | None = Query(default=None),
    board_filters: list[BoardFilter] | None = Query(default=None),
    as_of_date: str | None = Query(default=None, min_length=10, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    refresh: bool = Query(default=False),
    window_days: int = Query(default=60, ge=20, le=240),
    min_score: float = Query(default=60, ge=0, le=100),
    require_sequence: bool = Query(default=False),
    min_event_count: int = Query(default=1, ge=0, le=12),
    signal_age_min: int = Query(default=0, ge=0, le=240),
    signal_age_max: int | None = Query(default=None, ge=0, le=240),
    backtest_date_from: str | None = Query(default=None, min_length=10, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    backtest_pool_roll_mode: BacktestPoolRollMode | None = Query(default=None),
    backtest_max_symbols: int | None = Query(default=None, ge=20, le=2000),
) -> SignalsResponse | JSONResponse:
    strategy_params: dict[str, object] | None = None
    if strategy_params_json.strip():
        try:
            parsed = json.loads(strategy_params_json)
        except Exception:
            return error_response(400, "STRATEGY_PARAMS_INVALID", "strategy_params 必须为 JSON 对象字符串。")
        if not isinstance(parsed, dict):
            return error_response(400, "STRATEGY_PARAMS_INVALID", "strategy_params 必须为 JSON 对象字符串。")
        strategy_params = parsed
    try:
        return store.get_signals(
            mode=mode,
            run_id=run_id.strip() or None,
            trend_step=trend_step,
            strategy_id=strategy_id.strip(),
            strategy_params=strategy_params,
            market_filters=list(dict.fromkeys(market_filters or [])),
            board_filters=list(dict.fromkeys(board_filters or [])),
            as_of_date=as_of_date,
            refresh=refresh,
            window_days=window_days,
            min_score=min_score,
            require_sequence=require_sequence,
            min_event_count=min_event_count,
            signal_age_min=signal_age_min,
            signal_age_max=signal_age_max,
            backtest_date_from=backtest_date_from,
            backtest_pool_roll_mode=backtest_pool_roll_mode,
            backtest_max_symbols=backtest_max_symbols,
        )
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "SIGNALS_INVALID", str(exc))


@app.post("/api/signals/cross-validate", response_model=CrossValidateResponse)
def post_cross_validate(
    payload: CrossValidateRequest,
) -> CrossValidateResponse | JSONResponse:
    try:
        return store.cross_validate_strategies(request=payload)
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "CROSS_VALIDATE_INVALID", str(exc))


@app.post("/api/signals/cross-validate/tasks", response_model=CrossValidateTaskStartResponse)
def post_cross_validate_task(
    payload: CrossValidateRequest,
) -> CrossValidateTaskStartResponse | JSONResponse:
    try:
        task_id = store.start_cross_validate_task(payload)
        return CrossValidateTaskStartResponse(task_id=task_id)
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "CROSS_VALIDATE_INVALID", str(exc))


@app.get("/api/signals/cross-validate/tasks/{task_id}", response_model=CrossValidateTaskStatusResponse)
def get_cross_validate_task(
    task_id: str = Path(...),
) -> CrossValidateTaskStatusResponse | JSONResponse:
    task = store.get_cross_validate_task(task_id)
    if task is None:
        return error_response(404, "CROSS_VALIDATE_TASK_NOT_FOUND", "交叉验证任务不存在")
    return task


@app.post("/api/signals/cross-validate/tasks/{task_id}/cancel", response_model=CrossValidateTaskStatusResponse)
def cancel_cross_validate_task(
    task_id: str = Path(...),
) -> CrossValidateTaskStatusResponse | JSONResponse:
    try:
        return store.cancel_cross_validate_task(task_id)
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))


@app.post("/api/signals/cross-validate/backtest", response_model=CrossValidateBacktestResponse)
def post_cross_validate_backtest(
    payload: CrossValidateBacktestRequest,
) -> CrossValidateBacktestResponse | JSONResponse:
    import logging
    logger = logging.getLogger(__name__)
    hold_label = "until_today" if payload.hold_until_today else str(payload.hold_days)
    logger.info("backtest request: %d stocks, hold=%s, mode=%s", len(payload.stocks), hold_label, payload.buy_mode)
    if payload.stocks:
        logger.info("backtest first 3 stocks: %s", [(s.symbol, s.trigger_date) for s in payload.stocks[:3]])
    try:
        resp = store.backtest_cross_validate(request=payload)
        logger.info("backtest result: valid=%d/%d, win_rate=%.1f%%", resp.valid_count, resp.total_count, resp.win_rate)
        return resp
    except ValueError as exc:
        return error_response(400, "BACKTEST_INVALID", str(exc))
    except Exception as exc:
        logger.exception("backtest fatal error: %s", exc)
        return error_response(500, "BACKTEST_ERROR", str(exc))


@app.post("/api/signals/cross-validate/history", response_model=CrossValidateHistoryRecord)
def save_cross_validate_history(
    payload: dict,
) -> CrossValidateHistoryRecord | JSONResponse:
    try:
        resp = CrossValidateResponse(**payload["response"])
        strategy_ids = payload.get("strategy_ids", [])
        strategy_names = payload.get("strategy_names", [])
        request_params = payload.get("request_params", {})
        label = payload.get("label", "")
        return store.save_cross_validate_result(
            response=resp,
            label=label,
            strategy_ids=strategy_ids,
            strategy_names=strategy_names,
            request_params=request_params,
        )
    except Exception as exc:
        return error_response(400, "SAVE_HISTORY_INVALID", str(exc))


@app.get("/api/signals/cross-validate/history", response_model=list[CrossValidateHistoryRecord])
def list_cross_validate_history() -> list[CrossValidateHistoryRecord]:
    return store.list_cross_validate_history()


@app.get("/api/signals/cross-validate/history/{record_id}", response_model=CrossValidateHistoryDetail)
def get_cross_validate_history(record_id: str = Path(...)) -> CrossValidateHistoryDetail | JSONResponse:
    detail = store.get_cross_validate_history(record_id)
    if detail is None:
        return error_response(404, "NOT_FOUND", f"记录 {record_id} 不存在")
    return detail


@app.delete("/api/signals/cross-validate/history/{record_id}")
def delete_cross_validate_history(record_id: str = Path(...)) -> JSONResponse:
    ok = store.delete_cross_validate_history(record_id)
    if not ok:
        return error_response(404, "NOT_FOUND", f"记录 {record_id} 不存在")
    return JSONResponse({"ok": True})


@app.post("/api/signals/etf-backtests", response_model=SignalEtfBacktestDetail)
def post_signal_etf_backtest(
    payload: SignalEtfBacktestCreateRequest,
) -> SignalEtfBacktestDetail | JSONResponse:
    try:
        return store.create_signal_etf_backtest(payload)
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "SIGNAL_ETF_INVALID", str(exc))


@app.post("/api/signals/etf-backtests/auto", response_model=SignalEtfBacktestAutoCreateResponse)
def post_signal_etf_backtest_auto(
    payload: SignalEtfBacktestAutoCreateRequest,
) -> SignalEtfBacktestAutoCreateResponse | JSONResponse:
    try:
        return store.create_signal_etf_backtests_auto(payload)
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "SIGNAL_ETF_INVALID", str(exc))


@app.get("/api/signals/etf-backtests", response_model=SignalEtfBacktestListResponse)
def get_signal_etf_backtests(
    refresh: bool = Query(default=True),
    holding_days: int | None = Query(default=None, ge=1, le=120),
) -> SignalEtfBacktestListResponse | JSONResponse:
    try:
        return store.list_signal_etf_backtests(refresh=refresh, holding_days=holding_days)
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "SIGNAL_ETF_INVALID", str(exc))


@app.get("/api/signals/etf-backtests/{record_id}", response_model=SignalEtfBacktestDetail)
def get_signal_etf_backtest(
    record_id: str = Path(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"),
    refresh: bool = Query(default=True),
    as_of_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    holding_days: int | None = Query(default=None, ge=1, le=120),
) -> SignalEtfBacktestDetail | JSONResponse:
    try:
        detail = store.get_signal_etf_backtest(
            record_id,
            refresh=refresh,
            as_of_date=as_of_date,
            holding_days=holding_days,
        )
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "SIGNAL_ETF_INVALID", str(exc))
    if detail is None:
        return error_response(404, "SIGNAL_ETF_NOT_FOUND", "待买ETF回测记录不存在")
    return detail


@app.patch("/api/signals/etf-backtests/{record_id}", response_model=SignalEtfBacktestRecord)
def patch_signal_etf_backtest(
    payload: SignalEtfBacktestUpdateRequest,
    record_id: str = Path(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"),
) -> SignalEtfBacktestRecord | JSONResponse:
    if payload.name is None and payload.notes is None:
        return error_response(400, "SIGNAL_ETF_UPDATE_EMPTY", "至少提供一个可更新字段：name/notes。")
    try:
        row = store.update_signal_etf_backtest(record_id, payload)
    except BacktestValidationError as exc:
        status_code = 404 if exc.code == "SIGNAL_ETF_NOT_FOUND" else 400
        return error_response(status_code, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "SIGNAL_ETF_INVALID", str(exc))
    return row


@app.delete("/api/signals/etf-backtests/{record_id}", response_model=SignalEtfBacktestDeleteResponse)
def delete_signal_etf_backtest(
    record_id: str = Path(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"),
) -> SignalEtfBacktestDeleteResponse | JSONResponse:
    try:
        deleted = store.delete_signal_etf_backtest(record_id)
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "SIGNAL_ETF_INVALID", str(exc))
    if not deleted:
        return error_response(404, "SIGNAL_ETF_NOT_FOUND", "待买ETF回测记录不存在")
    return SignalEtfBacktestDeleteResponse(deleted=True, record_id=record_id)


@app.get("/api/strategies", response_model=StrategyCatalogResponse)
def get_strategies() -> StrategyCatalogResponse:
    return store.list_strategies()


@app.patch("/api/strategies/{strategy_id}", response_model=StrategyDescriptor)
def patch_strategy(strategy_id: str, payload: StrategyUpdateRequest) -> StrategyDescriptor | JSONResponse:
    if payload.enabled is None and payload.is_default is None and payload.version is None:
        return error_response(400, "STRATEGY_UPDATE_EMPTY", "至少需要提供一个可更新字段：enabled/is_default/version。")
    try:
        return store.update_strategy(
            strategy_id=strategy_id.strip(),
            enabled=payload.enabled,
            is_default=payload.is_default,
            version=payload.version,
        )
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))


@app.get("/api/event-judgment/profiles", response_model=EventJudgmentCatalogResponse)
def get_event_judgment_profiles() -> EventJudgmentCatalogResponse:
    return store.list_event_judgment_profiles()


@app.post("/api/event-judgment/profiles", response_model=EventJudgmentProfile)
def post_event_judgment_profile(payload: EventJudgmentProfileUpsertRequest) -> EventJudgmentProfile | JSONResponse:
    try:
        return store.upsert_event_judgment_profile(payload)
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "EVENT_JUDGMENT_INVALID", str(exc))


@app.post("/api/event-judgment/profiles/apply", response_model=EventJudgmentCatalogResponse)
def post_event_judgment_profile_apply(
    payload: EventJudgmentProfileApplyRequest,
) -> EventJudgmentCatalogResponse | JSONResponse:
    try:
        return store.apply_event_judgment_profile(payload.profile_id)
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "EVENT_JUDGMENT_INVALID", str(exc))


@app.delete("/api/event-judgment/profiles/{profile_id}", response_model=EventJudgmentProfileDeleteResponse)
def delete_event_judgment_profile(profile_id: str) -> EventJudgmentProfileDeleteResponse | JSONResponse:
    try:
        return store.delete_event_judgment_profile(profile_id)
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "EVENT_JUDGMENT_INVALID", str(exc))


@app.post("/api/backtest/run", response_model=BacktestResponse)
def post_backtest_run(payload: BacktestRunRequest) -> BacktestResponse | JSONResponse:
    try:
        return store.run_backtest(payload)
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "BACKTEST_INVALID", str(exc))


@app.post("/api/backtest/plateau", response_model=BacktestPlateauResponse)
def post_backtest_plateau(payload: BacktestPlateauRunRequest) -> BacktestPlateauResponse | JSONResponse:
    try:
        return store.run_backtest_plateau(payload)
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "BACKTEST_INVALID", str(exc))


@app.post("/api/backtest/plateau/tasks", response_model=BacktestTaskStartResponse)
def post_backtest_plateau_task(payload: BacktestPlateauRunRequest) -> BacktestTaskStartResponse | JSONResponse:
    try:
        task_id = store.start_backtest_plateau_task(payload)
        return BacktestTaskStartResponse(task_id=task_id)
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "BACKTEST_INVALID", str(exc))


@app.get("/api/backtest/plateau/tasks", response_model=BacktestPlateauTaskListResponse)
def get_backtest_plateau_tasks(
    include_result: bool = Query(default=False),
) -> BacktestPlateauTaskListResponse | JSONResponse:
    try:
        return store.list_backtest_plateau_tasks(include_result=include_result)
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "BACKTEST_INVALID", str(exc))


@app.get("/api/backtest/plateau/tasks/{task_id}", response_model=BacktestPlateauTaskStatusResponse)
def get_backtest_plateau_task(
    task_id: str = Path(min_length=8, max_length=64),
) -> BacktestPlateauTaskStatusResponse | JSONResponse:
    task = store.get_backtest_plateau_task(task_id)
    if task is None:
        return error_response(404, "BACKTEST_PLATEAU_TASK_NOT_FOUND", "收益平原任务不存在")
    return task


@app.get("/api/backtest/plateau/tasks/{task_id}/points/{detail_key}", response_model=BacktestPlateauPointDetailResponse)
def get_backtest_plateau_point_detail(
    task_id: str = Path(min_length=8, max_length=64),
    detail_key: str = Path(min_length=4, max_length=96),
) -> BacktestPlateauPointDetailResponse | JSONResponse:
    detail = store.get_backtest_plateau_point_detail(task_id, detail_key)
    if detail is None:
        return error_response(404, "BACKTEST_PLATEAU_POINT_DETAIL_NOT_FOUND", "收益平原参数组结果不存在")
    return detail


@app.post("/api/backtest/plateau/tasks/{task_id}/pause", response_model=BacktestPlateauTaskStatusResponse)
def post_backtest_plateau_task_pause(
    task_id: str = Path(min_length=8, max_length=64),
) -> BacktestPlateauTaskStatusResponse | JSONResponse:
    try:
        return store.pause_backtest_plateau_task(task_id)
    except BacktestValidationError as exc:
        status_code = 404 if exc.code == "BACKTEST_PLATEAU_TASK_NOT_FOUND" else 400
        return error_response(status_code, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "BACKTEST_INVALID", str(exc))


@app.post("/api/backtest/plateau/tasks/{task_id}/resume", response_model=BacktestPlateauTaskStatusResponse)
def post_backtest_plateau_task_resume(
    task_id: str = Path(min_length=8, max_length=64),
) -> BacktestPlateauTaskStatusResponse | JSONResponse:
    try:
        return store.resume_backtest_plateau_task(task_id)
    except BacktestValidationError as exc:
        status_code = 404 if exc.code == "BACKTEST_PLATEAU_TASK_NOT_FOUND" else 400
        return error_response(status_code, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "BACKTEST_INVALID", str(exc))


@app.post("/api/backtest/plateau/tasks/{task_id}/cancel", response_model=BacktestPlateauTaskStatusResponse)
def post_backtest_plateau_task_cancel(
    task_id: str = Path(min_length=8, max_length=64),
) -> BacktestPlateauTaskStatusResponse | JSONResponse:
    try:
        return store.cancel_backtest_plateau_task(task_id)
    except BacktestValidationError as exc:
        status_code = 404 if exc.code == "BACKTEST_PLATEAU_TASK_NOT_FOUND" else 400
        return error_response(status_code, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "BACKTEST_INVALID", str(exc))


@app.delete("/api/backtest/plateau/tasks/{task_id}", response_model=BacktestPlateauTaskDeleteResponse)
def delete_backtest_plateau_task(
    task_id: str = Path(min_length=8, max_length=64),
) -> BacktestPlateauTaskDeleteResponse | JSONResponse:
    try:
        return store.delete_backtest_plateau_task(task_id)
    except BacktestValidationError as exc:
        status_code = 404 if exc.code == "BACKTEST_PLATEAU_TASK_NOT_FOUND" else 400
        return error_response(status_code, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "BACKTEST_INVALID", str(exc))


@app.post("/api/backtest/tasks", response_model=BacktestTaskStartResponse)
def post_backtest_task(payload: BacktestRunRequest) -> BacktestTaskStartResponse | JSONResponse:
    try:
        task_id = store.start_backtest_task(payload)
        return BacktestTaskStartResponse(task_id=task_id)
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "BACKTEST_INVALID", str(exc))


@app.get("/api/backtest/tasks", response_model=BacktestTaskListResponse)
def get_backtest_tasks(
    include_result: bool = Query(default=False),
) -> BacktestTaskListResponse | JSONResponse:
    try:
        return store.list_backtest_tasks(include_result=include_result)
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "BACKTEST_INVALID", str(exc))


@app.get("/api/backtest/tasks/{task_id}", response_model=BacktestTaskStatusResponse)
def get_backtest_task(task_id: str = Path(min_length=8, max_length=64)) -> BacktestTaskStatusResponse | JSONResponse:
    task = store.get_backtest_task(task_id)
    if task is None:
        return error_response(404, "BACKTEST_TASK_NOT_FOUND", "回测任务不存在")
    return task


@app.post("/api/backtest/tasks/{task_id}/pause", response_model=BacktestTaskStatusResponse)
def post_backtest_task_pause(task_id: str = Path(min_length=8, max_length=64)) -> BacktestTaskStatusResponse | JSONResponse:
    try:
        return store.pause_backtest_task(task_id)
    except BacktestValidationError as exc:
        status_code = 404 if exc.code == "BACKTEST_TASK_NOT_FOUND" else 400
        return error_response(status_code, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "BACKTEST_INVALID", str(exc))


@app.post("/api/backtest/tasks/{task_id}/resume", response_model=BacktestTaskStatusResponse)
def post_backtest_task_resume(task_id: str = Path(min_length=8, max_length=64)) -> BacktestTaskStatusResponse | JSONResponse:
    try:
        return store.resume_backtest_task(task_id)
    except BacktestValidationError as exc:
        status_code = 404 if exc.code == "BACKTEST_TASK_NOT_FOUND" else 400
        return error_response(status_code, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "BACKTEST_INVALID", str(exc))


@app.post("/api/backtest/tasks/{task_id}/cancel", response_model=BacktestTaskStatusResponse)
def post_backtest_task_cancel(task_id: str = Path(min_length=8, max_length=64)) -> BacktestTaskStatusResponse | JSONResponse:
    try:
        return store.cancel_backtest_task(task_id)
    except BacktestValidationError as exc:
        status_code = 404 if exc.code == "BACKTEST_TASK_NOT_FOUND" else 400
        return error_response(status_code, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "BACKTEST_INVALID", str(exc))


@app.delete("/api/backtest/tasks/{task_id}", response_model=BacktestTaskDeleteResponse)
def delete_backtest_task(task_id: str = Path(min_length=8, max_length=64)) -> BacktestTaskDeleteResponse | JSONResponse:
    try:
        return store.delete_backtest_task(task_id)
    except BacktestValidationError as exc:
        status_code = 404 if exc.code == "BACKTEST_TASK_NOT_FOUND" else 400
        return error_response(status_code, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "BACKTEST_INVALID", str(exc))


@app.post("/api/backtest/reports/build", response_model=BacktestReportBuildResponse)
def post_backtest_report_build(payload: BacktestReportBuildRequest) -> BacktestReportBuildResponse | JSONResponse:
    try:
        return store.build_backtest_report_package(payload)
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "BACKTEST_REPORT_INVALID", str(exc))


@app.post("/api/backtest/reports/import", response_model=BacktestReportImportResponse)
async def post_backtest_report_import(file: UploadFile = File(...)) -> BacktestReportImportResponse | JSONResponse:
    try:
        package_bytes = await file.read()
    except Exception as exc:
        return error_response(400, "BACKTEST_REPORT_IMPORT_FAILED", f"读取导入文件失败: {exc}")
    if len(package_bytes) <= 0:
        return error_response(400, "BACKTEST_REPORT_INVALID", "导入文件为空。")
    try:
        return store.import_backtest_report_package(
            package_bytes,
            source_file_name=file.filename,
        )
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "BACKTEST_REPORT_IMPORT_FAILED", str(exc))


@app.get("/api/backtest/reports", response_model=BacktestReportListResponse)
def get_backtest_reports() -> BacktestReportListResponse | JSONResponse:
    try:
        return store.list_backtest_reports()
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "BACKTEST_REPORT_INVALID", str(exc))


@app.get("/api/backtest/reports/{report_id}", response_model=BacktestReportDetail)
def get_backtest_report(
    report_id: str = Path(min_length=4, max_length=96, pattern=r"^[A-Za-z0-9._-]+$"),
) -> BacktestReportDetail | JSONResponse:
    try:
        detail = store.get_backtest_report(report_id)
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "BACKTEST_REPORT_INVALID", str(exc))
    if detail is None:
        return error_response(404, "BACKTEST_REPORT_NOT_FOUND", "回测报告不存在")
    return detail


@app.delete("/api/backtest/reports/{report_id}", response_model=BacktestReportDeleteResponse)
def delete_backtest_report(
    report_id: str = Path(min_length=4, max_length=96, pattern=r"^[A-Za-z0-9._-]+$"),
) -> BacktestReportDeleteResponse | JSONResponse:
    try:
        deleted = store.delete_backtest_report(report_id)
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "BACKTEST_REPORT_DELETE_FAILED", str(exc))
    if not deleted:
        return error_response(404, "BACKTEST_REPORT_NOT_FOUND", "回测报告不存在")
    return BacktestReportDeleteResponse(deleted=True, report_id=report_id)


@app.get("/api/backtest/strategy-signals/{symbol}", response_model=BacktestStrategySignalsResponse)
def get_backtest_strategy_signals(
    symbol: str = Path(min_length=4, max_length=16),
) -> BacktestStrategySignalsResponse | JSONResponse:
    try:
        return store.get_backtest_strategy_signals(symbol.strip())
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "BACKTEST_STRATEGY_SIGNALS_INVALID", str(exc))


@app.get("/api/backtest/scan-stock-strategies/{symbol}", response_model=BacktestStrategySignalsResponse)
def scan_stock_strategies(
    symbol: str = Path(min_length=4, max_length=16),
    window_days: int = Query(default=60, ge=20, le=120),
    scan_days: int = Query(default=120, ge=20, le=250),
) -> BacktestStrategySignalsResponse | JSONResponse:
    try:
        return store.scan_stock_strategy_signals(symbol.strip(), window_days=window_days, scan_days=scan_days)
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(400, "SCAN_STRATEGY_SIGNALS_INVALID", str(exc))


@app.post("/api/sim/orders", response_model=CreateOrderResponse)
def post_order(payload: CreateOrderRequest) -> CreateOrderResponse:
    return store.create_order(payload)


@app.get("/api/sim/orders", response_model=SimOrdersResponse)
def get_orders(
    status: Literal["pending", "filled", "cancelled", "rejected"] | None = Query(default=None),
    symbol: str | None = Query(default=None, min_length=2, max_length=16),
    side: Literal["buy", "sell"] | None = Query(default=None),
    date_from: str | None = Query(default=None, min_length=10, max_length=10),
    date_to: str | None = Query(default=None, min_length=10, max_length=10),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
) -> SimOrdersResponse:
    return store.list_orders(
        status=status,
        symbol=symbol.strip().lower() if symbol else None,
        side=side,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )


@app.get("/api/sim/fills", response_model=SimFillsResponse)
def get_fills(
    symbol: str | None = Query(default=None, min_length=2, max_length=16),
    side: Literal["buy", "sell"] | None = Query(default=None),
    date_from: str | None = Query(default=None, min_length=10, max_length=10),
    date_to: str | None = Query(default=None, min_length=10, max_length=10),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
) -> SimFillsResponse:
    return store.list_fills(
        symbol=symbol.strip().lower() if symbol else None,
        side=side,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )


@app.post("/api/sim/orders/{order_id}/cancel", response_model=CreateOrderResponse)
def post_cancel_order(order_id: str = Path(min_length=8, max_length=64)) -> CreateOrderResponse:
    return store.cancel_order(order_id)


@app.post("/api/sim/settle", response_model=SimSettleResponse)
def post_sim_settle() -> SimSettleResponse:
    return store.settle_sim()


@app.post("/api/sim/reset", response_model=SimResetResponse)
def post_sim_reset() -> SimResetResponse:
    return store.reset_sim()


@app.get("/api/sim/config", response_model=SimTradingConfig)
def get_sim_config() -> SimTradingConfig:
    return store.get_sim_config()


@app.put("/api/sim/config", response_model=SimTradingConfig)
def put_sim_config(payload: SimTradingConfig) -> SimTradingConfig:
    return store.set_sim_config(payload)


@app.get("/api/sim/portfolio", response_model=PortfolioSnapshot)
def get_portfolio() -> PortfolioSnapshot:
    return store.get_portfolio()


@app.get("/api/review/stats", response_model=ReviewResponse)
def get_review_stats(
    date_from: str | None = Query(default=None, min_length=10, max_length=10),
    date_to: str | None = Query(default=None, min_length=10, max_length=10),
    date_axis: Literal["sell", "buy"] = Query(default="sell"),
) -> ReviewResponse:
    return store.get_review(date_from=date_from, date_to=date_to, date_axis=date_axis)


@app.get("/api/market/news", response_model=MarketNewsResponse)
def get_market_news(
    query: str = Query(default="", min_length=0, max_length=120),
    symbol: str | None = Query(default=None, min_length=0, max_length=16),
    source_domains: str | None = Query(default=None, min_length=0, max_length=300),
    age_hours: int = Query(default=72, ge=1, le=240, description="支持 24/48/72，其他值会回退到 72"),
    refresh: bool = Query(default=False, description="true 时跳过短缓存，强制拉取最新资讯"),
    limit: int = Query(default=20, ge=1, le=50),
) -> MarketNewsResponse:
    domains: list[str] = []
    if source_domains:
        raw_tokens = source_domains.replace(";", ",").replace("|", ",")
        domains = [token.strip() for token in raw_tokens.split(",") if token.strip()]
    return store.get_market_news(
        query=query,
        symbol=symbol,
        source_domains=domains,
        age_hours=age_hours,
        refresh=refresh,
        limit=limit,
    )


@app.get("/api/review/daily", response_model=DailyReviewListResponse)
def get_daily_reviews(
    date_from: str | None = Query(default=None, min_length=10, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str | None = Query(default=None, min_length=10, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> DailyReviewListResponse:
    return store.list_daily_reviews(date_from=date_from, date_to=date_to)


@app.get("/api/review/daily/{date}", response_model=DailyReviewRecord)
def get_daily_review(date: str = Path(pattern=r"^\d{4}-\d{2}-\d{2}$")) -> DailyReviewRecord | JSONResponse:
    row = store.get_daily_review(date)
    if row is None:
        return error_response(404, "REVIEW_DAILY_NOT_FOUND", "日复盘不存在")
    return row


@app.put("/api/review/daily/{date}", response_model=DailyReviewRecord)
def put_daily_review(
    payload: DailyReviewPayload,
    date: str = Path(pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> DailyReviewRecord:
    return store.upsert_daily_review(date, payload)


@app.delete("/api/review/daily/{date}")
def delete_daily_review(date: str = Path(pattern=r"^\d{4}-\d{2}-\d{2}$")) -> dict[str, bool]:
    return {"deleted": store.delete_daily_review(date)}


@app.get("/api/review/weekly", response_model=WeeklyReviewListResponse)
def get_weekly_reviews(year: int | None = Query(default=None, ge=2000, le=2100)) -> WeeklyReviewListResponse:
    return store.list_weekly_reviews(year=year)


@app.get("/api/review/weekly/{week_label}", response_model=WeeklyReviewRecord)
def get_weekly_review(week_label: str = Path(pattern=r"^\d{4}-W\d{2}$")) -> WeeklyReviewRecord | JSONResponse:
    row = store.get_weekly_review(week_label)
    if row is None:
        return error_response(404, "REVIEW_WEEKLY_NOT_FOUND", "周复盘不存在")
    return row


@app.put("/api/review/weekly/{week_label}", response_model=WeeklyReviewRecord)
def put_weekly_review(
    payload: WeeklyReviewPayload,
    week_label: str = Path(pattern=r"^\d{4}-W\d{2}$"),
) -> WeeklyReviewRecord | JSONResponse:
    try:
        return store.upsert_weekly_review(week_label, payload)
    except ValueError as exc:
        return error_response(400, "REVIEW_WEEKLY_INVALID", str(exc))


@app.delete("/api/review/weekly/{week_label}")
def delete_weekly_review(week_label: str = Path(pattern=r"^\d{4}-W\d{2}$")) -> dict[str, bool]:
    return {"deleted": store.delete_weekly_review(week_label)}


@app.get("/api/review/tags", response_model=ReviewTagsPayload)
def get_review_tags() -> ReviewTagsPayload:
    return store.get_review_tags()


@app.post("/api/review/tags/{tag_type}", response_model=ReviewTag)
def post_review_tag(tag_type: ReviewTagType, payload: ReviewTagCreateRequest) -> ReviewTag | JSONResponse:
    try:
        return store.create_review_tag(tag_type, payload)
    except ValueError as exc:
        return error_response(400, "REVIEW_TAG_INVALID", str(exc))


@app.delete("/api/review/tags/{tag_type}/{tag_id}")
def delete_review_tag(
    tag_type: ReviewTagType,
    tag_id: str = Path(min_length=3, max_length=64),
) -> dict[str, bool]:
    return {"deleted": store.delete_review_tag(tag_type, tag_id)}


@app.get("/api/review/fill-tags", response_model=list[TradeFillTagAssignment])
def get_fill_tag_assignments() -> list[TradeFillTagAssignment]:
    return store.list_fill_tag_assignments()


@app.get("/api/review/fill-tags/{order_id}", response_model=TradeFillTagAssignment)
def get_fill_tag_assignment(order_id: str = Path(min_length=8, max_length=64)) -> TradeFillTagAssignment | JSONResponse:
    row = store.get_fill_tag_assignment(order_id)
    if row is None:
        return error_response(404, "REVIEW_FILL_TAG_NOT_FOUND", "成交标签不存在")
    return row


@app.put("/api/review/fill-tags/{order_id}", response_model=TradeFillTagAssignment)
def put_fill_tag_assignment(
    payload: TradeFillTagUpdateRequest,
    order_id: str = Path(min_length=8, max_length=64),
) -> TradeFillTagAssignment | JSONResponse:
    try:
        return store.set_fill_tag_assignment(order_id, payload)
    except ValueError as exc:
        return error_response(400, "REVIEW_FILL_TAG_INVALID", str(exc))


@app.get("/api/review/tag-stats", response_model=ReviewTagStatsResponse)
def get_review_tag_stats(
    date_from: str | None = Query(default=None, min_length=10, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str | None = Query(default=None, min_length=10, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> ReviewTagStatsResponse:
    return store.get_review_tag_stats(date_from=date_from, date_to=date_to)


@app.get("/api/ai/records", response_model=AIRecordsResponse)
def get_ai_records() -> AIRecordsResponse:
    return AIRecordsResponse(items=store.get_ai_records())


@app.post("/api/stocks/{symbol}/ai-analyze", response_model=AIAnalysisRecord)
def post_stock_ai_analyze(symbol: str) -> AIAnalysisRecord:
    return store.analyze_stock_with_ai(symbol)


@app.get("/api/stocks/{symbol}/ai-prompt-preview")
def get_stock_ai_prompt_preview(symbol: str) -> dict[str, object]:
    return store.get_ai_prompt_preview(symbol)


@app.delete("/api/ai/records", response_model=DeleteAIRecordResponse)
def delete_ai_record(
    symbol: str = Query(min_length=2),
    fetched_at: str = Query(min_length=10),
    provider: str | None = Query(default=None),
) -> DeleteAIRecordResponse:
    deleted = store.delete_ai_record(symbol=symbol, fetched_at=fetched_at, provider=provider)
    return DeleteAIRecordResponse(deleted=deleted, remaining=len(store.get_ai_records()))


@app.post("/api/ai/providers/test", response_model=AIProviderTestResponse)
def post_ai_provider_test(payload: AIProviderTestRequest) -> AIProviderTestResponse:
    return store.test_ai_provider(
        payload.provider,
        fallback_api_key=payload.fallback_api_key,
        fallback_api_key_path=payload.fallback_api_key_path,
        timeout_sec=payload.timeout_sec,
    )


@app.get("/api/ai/playbook", response_model=AIPlaybookResponse)
def get_ai_playbook(
    scope: str = Query(default="all", min_length=2, max_length=32),
    strategy_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> AIPlaybookResponse:
    payload = store.get_ai_playbook(scope=scope, strategy_id=strategy_id)
    return AIPlaybookResponse(playbook=payload["playbook"], text=str(payload["text"]))


@app.get("/api/ai/playbook/local", response_model=AILocalPlaybookResponse)
def get_ai_local_playbook() -> AILocalPlaybookResponse:
    payload = store.get_ai_local_playbook()
    principles = payload.get("principles")
    if not isinstance(principles, list):
        principles = []
    return AILocalPlaybookResponse(
        principles=[str(item) for item in principles],
        updated_at=str(payload.get("updated_at") or ""),
    )


@app.put("/api/ai/playbook/local", response_model=AILocalPlaybookResponse)
def put_ai_local_playbook(payload: AILocalPlaybookPayload) -> AILocalPlaybookResponse:
    saved = store.set_ai_local_playbook(payload.principles)
    principles = saved.get("principles")
    if not isinstance(principles, list):
        principles = []
    return AILocalPlaybookResponse(
        principles=[str(item) for item in principles],
        updated_at=str(saved.get("updated_at") or ""),
    )


@app.get("/api/ai/quick-prompts", response_model=AIQuickPromptsResponse)
def get_ai_quick_prompts() -> AIQuickPromptsResponse:
    payload = store.get_ai_quick_prompts()
    templates = payload.get("templates")
    if not isinstance(templates, list):
        templates = []
    return AIQuickPromptsResponse(templates=templates)  # type: ignore[arg-type]


@app.put("/api/ai/quick-prompts", response_model=AIQuickPromptsResponse)
def put_ai_quick_prompts(payload: AIQuickPromptsPayload) -> AIQuickPromptsResponse:
    saved = store.set_ai_quick_prompts([item.model_dump() for item in payload.templates])
    templates = saved.get("templates")
    if not isinstance(templates, list):
        templates = []
    return AIQuickPromptsResponse(templates=templates)  # type: ignore[arg-type]


@app.post("/api/ai/chat", response_model=None)
def post_ai_chat(payload: AIChatRequest) -> AIChatResponse | StreamingResponse | JSONResponse:
    if payload.stream:
        try:
            return StreamingResponse(
                store.ai_chat_stream(payload),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        except ValueError as exc:
            return error_response(422, "AI_CHAT_FAILED", str(exc))
        except Exception as exc:
            return error_response(502, "AI_PROVIDER_ERROR", f"AI 对话失败: {type(exc).__name__}")
    try:
        return store.ai_chat(payload)
    except BacktestValidationError as exc:
        return error_response(400, exc.code, str(exc))
    except ValueError as exc:
        return error_response(422, "AI_CHAT_FAILED", str(exc))
    except Exception as exc:
        return error_response(502, "AI_PROVIDER_ERROR", f"AI 对话失败: {type(exc).__name__}")


@app.get("/api/ai/sessions", response_model=AISessionsListResponse)
def list_ai_chat_sessions(limit: int = Query(default=20, ge=1, le=100)) -> AISessionsListResponse:
    items = store.list_ai_sessions(limit=limit)
    return AISessionsListResponse(items=items)  # type: ignore[arg-type]


@app.get("/api/ai/usage", response_model=AIUsageResponse)
def get_ai_usage() -> AIUsageResponse:
    totals = store.get_ai_usage()
    return AIUsageResponse(
        prompt_tokens=int(totals.get("prompt_tokens", 0) or 0),
        completion_tokens=int(totals.get("completion_tokens", 0) or 0),
        total_tokens=int(totals.get("total_tokens", 0) or 0),
    )


@app.post("/api/ai/parameter-proposals", response_model=AIParameterProposalResponse)
def post_ai_parameter_proposal(payload: AIParameterProposalRequest) -> AIParameterProposalResponse | JSONResponse:
    try:
        record = store.create_ai_parameter_proposal(message=payload.message, context=payload.context)
        return AIParameterProposalResponse(**record)  # type: ignore[arg-type]
    except ValueError as exc:
        return error_response(422, "AI_PROPOSAL_FAILED", str(exc))
    except Exception as exc:
        return error_response(502, "AI_PROVIDER_ERROR", f"参数建议失败: {type(exc).__name__}")


@app.post("/api/ai/parameter-proposals/{proposal_id}/apply", response_model=AIParameterProposalApplyResponse)
def post_ai_parameter_proposal_apply(
    payload: AIParameterProposalApplyRequest,
    proposal_id: str = Path(min_length=8, max_length=64),
) -> AIParameterProposalApplyResponse | JSONResponse:
    try:
        result = store.apply_ai_parameter_proposal(
            proposal_id,
            change_ids=payload.change_ids,
            confirm_high_risk=payload.confirm_high_risk,
        )
        return AIParameterProposalApplyResponse(**result)  # type: ignore[arg-type]
    except ValueError as exc:
        code = str(exc)
        status = 404 if "NOT_FOUND" in code else 410 if "EXPIRED" in code else 422
        return error_response(status, code, str(exc))
    except Exception as exc:
        return error_response(502, "AI_PROPOSAL_APPLY_FAILED", f"应用参数失败: {type(exc).__name__}")


@app.get("/api/ai/sessions/{session_id}", response_model=AIChatSessionResponse)
def get_ai_chat_session(session_id: str = Path(min_length=8, max_length=128)) -> AIChatSessionResponse | JSONResponse:
    payload = store.get_ai_chat_session(session_id)
    messages = payload.get("messages")
    if not isinstance(messages, list):
        messages = []
    return AIChatSessionResponse(session_id=session_id, messages=messages)  # type: ignore[arg-type]


@app.delete("/api/ai/sessions/{session_id}")
def delete_ai_chat_session(session_id: str = Path(min_length=8, max_length=128)) -> dict[str, bool]:
    return {"deleted": store.delete_ai_chat_session(session_id)}


@app.get("/api/config", response_model=AppConfig)
def get_config() -> AppConfig:
    return store.get_public_config()


@app.put("/api/config", response_model=AppConfig)
def put_config(payload: AppConfig) -> AppConfig:
    store.set_config(payload)
    return store.get_public_config()


@app.get("/api/system/storage", response_model=SystemStorageStatus)
def get_system_storage() -> SystemStorageStatus:
    return store.get_system_storage_status()


@app.get("/api/system/wyckoff-event-store/stats", response_model=WyckoffEventStoreStatsResponse)
def get_wyckoff_event_store_stats() -> WyckoffEventStoreStatsResponse:
    return store.get_wyckoff_event_store_stats()


@app.post("/api/system/wyckoff-event-store/backfill", response_model=WyckoffEventStoreBackfillResponse)
def post_wyckoff_event_store_backfill(
    payload: WyckoffEventStoreBackfillRequest,
) -> WyckoffEventStoreBackfillResponse | JSONResponse:
    try:
        return store.backfill_wyckoff_event_store(payload)
    except ValueError as exc:
        return error_response(400, "WYCKOFF_BACKFILL_INVALID", str(exc))


@app.post("/api/system/sync-market-data", response_model=MarketDataSyncResponse)
def post_sync_market_data(payload: MarketDataSyncRequest) -> MarketDataSyncResponse:
    return store.sync_market_data(payload)

